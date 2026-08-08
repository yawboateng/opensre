"""Kubernetes fleet search tool for finding workloads across all clusters."""

from __future__ import annotations

import concurrent.futures
import contextvars
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from core.tool_framework.base import BaseTool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.kubernetes.tools import (
    _CLUSTER_PROP,
    _UNAVAILABLE_MSG,
    _is_available,
    _resolve_client,
)

#: One task per cluster, not per API call: the five kind calls inside a cluster
#: share one client and one urllib3 pool. core/execution.py already runs up to
#: _TOOL_EXECUTOR_WORKERS (10) tool calls in parallel, so a 17-wide fan-out from
#: inside one of them would be up to 170 threads. 8 caps the worst case at 80 and
#: clears 17 clusters in three waves. Deliberately not _MAX_PARALLEL_VERIFIERS (16).
_MAX_PARALLEL_CLUSTERS = 8

#: Absolute wall-clock budget for the whole fan-out. The gateway turn dies at
#: 240s; _BoundedApiClient allows 5s connect / 60s read PER REQUEST, so one wedged
#: cluster running five sequential kind calls can burn 300s on its own — longer
#: than the turn. 90s leaves room for the model's follow-up call in the same turn.
_FLEET_SEARCH_DEADLINE_SECONDS = 90.0


def _search_one_cluster_workloads(
    cluster_name: str,
    cluster_conn: dict[str, Any],
    name_contains: str,
    namespace: str,
) -> dict[str, Any]:
    """Search workloads in one cluster (phase 1).

    Returns a dict with matches, success status, truncation, and unavailable kinds.
    """
    try:
        # Build client for this cluster
        cfg_dict = {"kubernetes": cluster_conn}
        from integrations.kubernetes.tools import _make_client

        client = _make_client(cfg_dict)
        if client is None:
            return {
                "success": False,
                "error": "client build failed",
                "matches": [],
                "truncated": False,
                "unavailable_kinds": [],
            }

        # Search workload owners
        workload_result = client.search_workload_owners(name_contains, namespace)
        if not workload_result.get("success"):
            return {
                "success": False,
                "error": workload_result.get("error", "unknown error"),
                "matches": [],
                "truncated": False,
                "unavailable_kinds": [],
            }

        workload_matches = workload_result.get("workloads", [])

        # Project matches to final shape and add cluster name
        projected_matches = []
        for match in workload_matches:
            projected_matches.append(
                {
                    "cluster": cluster_name,
                    "namespace": match.get("namespace"),
                    "kind": match.get("kind"),
                    "name": match.get("name"),
                    "ready": match.get("ready"),
                    "desired": match.get("desired"),
                    "phase": match.get("phase"),
                }
            )

        # Collect unavailable kinds with cluster name
        unavailable_kinds = []
        for kind_info in workload_result.get("unavailable_kinds", []):
            unavailable_kinds.append(
                {
                    "cluster": cluster_name,
                    "kind": kind_info["kind"],
                    "reason": kind_info["reason"],
                }
            )

        return {
            "success": True,
            "matches": projected_matches,
            "truncated": workload_result.get("truncated", False),
            "unavailable_kinds": unavailable_kinds,
        }

    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "matches": [],
            "truncated": False,
            "unavailable_kinds": [],
        }


def _search_one_cluster_pods(
    cluster_name: str,
    cluster_conn: dict[str, Any],
    name_contains: str,
    namespace: str,
) -> dict[str, Any]:
    """Search pods in one cluster (phase 2).

    Returns a dict with matches, success status, and truncation.
    """
    try:
        # Build client for this cluster
        cfg_dict = {"kubernetes": cluster_conn}
        from integrations.kubernetes.tools import _make_client

        client = _make_client(cfg_dict)
        if client is None:
            return {
                "success": False,
                "error": "client build failed",
                "matches": [],
                "truncated": False,
            }

        # Search pods
        pod_result = client.search_pods(name_contains, namespace)
        if not pod_result.get("success"):
            return {
                "success": False,
                "error": pod_result.get("error", "unknown error"),
                "matches": [],
                "truncated": False,
            }

        pod_matches = pod_result.get("pods", [])

        # Project matches to final shape and add cluster name
        projected_matches = []
        for match in pod_matches:
            projected_matches.append(
                {
                    "cluster": cluster_name,
                    "namespace": match.get("namespace"),
                    "kind": match.get("kind"),
                    "name": match.get("name"),
                    "ready": match.get("ready"),
                    "desired": match.get("desired"),
                    "phase": match.get("phase"),
                }
            )

        return {
            "success": True,
            "matches": projected_matches,
            "truncated": pod_result.get("truncated", False),
        }

    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "matches": [],
            "truncated": False,
        }


class KubernetesSearchFleetTool(BaseTool):
    """Find where a workload or pod runs across every registered cluster."""

    name = "kubernetes_search_fleet"
    source = "kubernetes"
    description = (
        "Find where a workload or pod runs across every registered cluster, when you "
        "do not already know the cluster. Matches a case-insensitive substring against "
        "Deployments, StatefulSets, DaemonSets, CronJobs and Argo Rollouts in all "
        "namespaces, and falls back to individual pods when no owner matches anywhere. "
        "Returns the cluster, namespace, kind and readiness of each match, plus the "
        "clusters it searched and any it could not reach — a result is only a reliable "
        "'does not exist' when clusters_failed is empty and partial is false."
    )
    use_cases = [
        "An alert names a workload but not a cluster",
        "An alert names a project that holds signals rather than the cluster the workload runs in",
        "Confirming a workload really is absent everywhere before saying so",
        "Finding which of several clusters runs a named service",
    ]
    anti_examples = [
        "You already know the cluster and namespace — use kubernetes_list_workloads, "
        "which is one call instead of a fan-out across every cluster.",
        "Listing everything in a namespace — this tool needs a name substring.",
        "Reading pod logs or restart counts — locate it here, then use "
        "kubernetes_list_pods or kubernetes_get_pod_logs on the cluster it returned.",
    ]
    surfaces = ("investigation", "chat", "action")
    requires = []
    injected_params = [
        "kubeconfig",
        "kubeconfig_path",
        "context",
        "default_namespace",
        "cluster_configs",
    ]
    input_schema = {
        "type": "object",
        "properties": {
            "name_contains": {
                "type": "string",
                "description": (
                    "Case-insensitive substring of the workload or pod name to find. "
                    "Pass the name exactly as it appears in the alert — a full pod "
                    "name with its replicaset hash works, and so does a bare service "
                    "name."
                ),
            },
            "namespace": {
                "type": "string",
                "default": "",
                "description": (
                    "Optional namespace narrower. OMIT THIS to search every namespace, "
                    "which is the point of this tool. Pass one only when the request "
                    "names a namespace."
                ),
            },
            "cluster": _CLUSTER_PROP,
            "include_pods": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Search individual pods as well as their owners. Left false, pods "
                    "are searched automatically only when no owner matches anywhere."
                ),
            },
            # kubeconfig / kubeconfig_path / context are injected and pruned from
            # the model-facing schema; declare them for parity with the other tools.
            **{
                "kubeconfig": {"type": "string", "description": "Raw kubeconfig YAML string"},
                "kubeconfig_path": {
                    "type": "string",
                    "default": "",
                    "description": "Path to kubeconfig file (alternative to kubeconfig)",
                },
                "context": {
                    "type": "string",
                    "default": "",
                    "description": "Kubeconfig context to use",
                },
            },
        },
        "required": ["name_contains"],
    }
    outputs = {
        "matches": "List of workloads and pods found across clusters",
        "clusters_searched": "List of cluster names successfully searched",
        "clusters_failed": "List of clusters that could not be searched",
        "partial": "True when some clusters failed or results were truncated",
        "pods_searched": "Whether individual pods were searched",
    }

    def run(
        self,
        name_contains: str,
        namespace: str = "",
        cluster: str = "",
        include_pods: bool = False,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Search for workloads across the fleet."""
        if not _is_available(
            {"kubernetes": {"kubeconfig": kubeconfig, "kubeconfig_path": kubeconfig_path}}
        ):
            return tool_unavailable("kubernetes", _UNAVAILABLE_MSG)

        # Deliberately NOT passed through _effective_namespace. That helper
        # falls back to conn["namespace"], which _cluster_configs defaults to
        # "default" for every auto-registered cluster. Reusing it here would
        # search one usually-empty namespace per cluster, return zero across the
        # whole fleet, and be indistinguishable from a correct negative. An
        # empty namespace argument means every namespace, full stop.
        search_namespace = namespace.strip()

        # Use _resolve_client to handle cluster selection properly
        configs = cluster_configs or {}
        default_conn = {
            "kubeconfig": kubeconfig,
            "kubeconfig_path": kubeconfig_path,
            "context": context,
            "namespace": default_namespace,
        }

        # Determine selected clusters
        if cluster:
            # Single cluster - use _resolve_client to validate
            client, conn, error = _resolve_client(cluster, configs, default_conn)
            if error:
                return tool_unavailable("kubernetes", error)
            selected_clusters = {cluster: conn}
        else:
            # All clusters
            if configs:
                selected_clusters = configs
            else:
                # Fallback to default
                selected_clusters = {"default": default_conn}

        # Phase 1: Search workload owners across fleet
        deadline = time.monotonic() + _FLEET_SEARCH_DEADLINE_SECONDS
        ctx = contextvars.copy_context()

        # Use ThreadPoolExecutor without context manager to control shutdown properly
        executor = ThreadPoolExecutor(max_workers=_MAX_PARALLEL_CLUSTERS)

        try:
            # Submit phase 1 jobs
            def _submit_workload_search(cname: str, cconn: dict[str, Any]) -> dict[str, Any]:
                return ctx.run(_search_one_cluster_workloads, cname, cconn, name_contains, search_namespace)

            phase1_futures = {
                executor.submit(_submit_workload_search, cname, cconn): cname
                for cname, cconn in selected_clusters.items()
            }

            # Wait for phase 1 with deadline
            phase1_done, phase1_pending = concurrent.futures.wait(
                phase1_futures, timeout=max(0.0, deadline - time.monotonic())
            )

            # Collect phase 1 results
            phase1_matches = []
            clusters_searched = []
            clusters_failed = []
            all_unavailable_kinds = []
            truncated_clusters = set()

            for future in phase1_done:
                cluster_name = phase1_futures[future]
                try:
                    result = future.result()
                    if result["success"]:
                        clusters_searched.append(cluster_name)
                        phase1_matches.extend(result["matches"])
                        all_unavailable_kinds.extend(result["unavailable_kinds"])
                        if result["truncated"]:
                            truncated_clusters.add(cluster_name)
                    else:
                        clusters_failed.append({"cluster": cluster_name, "reason": result["error"]})
                except Exception as exc:
                    clusters_failed.append({"cluster": cluster_name, "reason": str(exc)})

            # Handle phase 1 timeouts
            for future in phase1_pending:
                cluster_name = phase1_futures[future]
                clusters_failed.append(
                    {"cluster": cluster_name, "reason": "search deadline exceeded"}
                )

            # Phase 2: Search pods only if fleet-wide phase 1 found nothing OR include_pods is True
            phase2_matches = []
            pods_searched = False

            if include_pods or len(phase1_matches) == 0:
                pods_searched = True

                # Only search clusters that succeeded in phase 1
                def _submit_pod_search(cname: str, cconn: dict[str, Any]) -> dict[str, Any]:
                    return ctx.run(_search_one_cluster_pods, cname, cconn, name_contains, search_namespace)

                phase2_futures = {
                    executor.submit(_submit_pod_search, cname, selected_clusters[cname]): cname
                    for cname in clusters_searched
                }

                # Wait for phase 2 with remaining deadline
                phase2_done, phase2_pending = concurrent.futures.wait(
                    phase2_futures, timeout=max(0.0, deadline - time.monotonic())
                )

                for future in phase2_done:
                    cluster_name = phase2_futures[future]
                    try:
                        result = future.result()
                        if result["success"]:
                            phase2_matches.extend(result["matches"])
                            if result["truncated"]:
                                truncated_clusters.add(cluster_name)
                        else:
                            # Phase 2 failure: move cluster from searched to failed
                            clusters_searched.remove(cluster_name)
                            clusters_failed.append(
                                {"cluster": cluster_name, "reason": result["error"]}
                            )
                    except Exception as exc:
                        clusters_searched.remove(cluster_name)
                        clusters_failed.append({"cluster": cluster_name, "reason": str(exc)})

                # Handle phase 2 timeouts
                for future in phase2_pending:
                    cluster_name = phase2_futures[future]
                    clusters_searched.remove(cluster_name)
                    clusters_failed.append(
                        {"cluster": cluster_name, "reason": "search deadline exceeded"}
                    )

        finally:
            # Properly shutdown executor without context manager re-joining
            executor.shutdown(wait=False, cancel_futures=True)

        # Combine all matches
        all_matches = phase1_matches + phase2_matches

        # Sort matches by (cluster, namespace, kind, name)
        all_matches.sort(
            key=lambda match: (
                match.get("cluster", ""),
                match.get("namespace", ""),
                match.get("kind", ""),
                match.get("name", ""),
            )
        )

        # Determine truncation status (per cluster, not per match)
        truncated = len(truncated_clusters) > 0
        truncated_kinds = sorted(truncated_clusters) if truncated else []

        # Partial if any clusters failed or results were truncated
        partial = len(clusters_failed) > 0 or truncated

        return {
            "source": "kubernetes",
            "available": True,
            "query": name_contains,
            "matches": all_matches,
            "total": len(all_matches),
            "clusters_searched": sorted(clusters_searched),
            "clusters_failed": clusters_failed,
            "pods_searched": pods_searched,
            "partial": partial,
            "truncated": truncated,
            "truncated_kinds": truncated_kinds,
            "unavailable_kinds": all_unavailable_kinds,
        }


# Export the tool instance
tool = KubernetesSearchFleetTool()
