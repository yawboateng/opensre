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


def _search_one_cluster(
    cluster_name: str,
    cluster_conn: dict[str, Any],
    name_contains: str,
    namespace: str,
    include_pods: bool,
) -> dict[str, Any]:
    """Search a single cluster for workloads and optionally pods.

    Returns a dict with matches, clusters_searched, clusters_failed, and pods_searched.
    """
    try:
        # Build client for this cluster
        cfg_dict = {"kubernetes": cluster_conn}
        from integrations.kubernetes.tools import _make_client

        client = _make_client(cfg_dict)
        if client is None:
            return {
                "matches": [],
                "clusters_searched": [],
                "clusters_failed": [{"cluster": cluster_name, "reason": "client build failed"}],
                "pods_searched": False,
                "unavailable_kinds": [],
            }

        # Search workload owners first (phase 1)
        workload_result = client.search_workload_owners(name_contains, namespace)
        if not workload_result.get("success"):
            return {
                "matches": [],
                "clusters_searched": [],
                "clusters_failed": [
                    {
                        "cluster": cluster_name,
                        "reason": workload_result.get("error", "unknown error"),
                    }
                ],
                "pods_searched": False,
                "unavailable_kinds": [],
            }

        workload_matches = workload_result.get("workloads", [])
        unavailable_kinds = []

        # Add cluster name to matches and unavailable_kinds
        for match in workload_matches:
            match["cluster"] = cluster_name

        for kind_info in workload_result.get("unavailable_kinds", []):
            unavailable_kinds.append(
                {
                    "cluster": cluster_name,
                    "kind": kind_info["kind"],
                    "reason": kind_info["reason"],
                }
            )

        # Phase 2: Search pods if no workload matches and include_pods is True OR explicit
        pods_searched = False
        pod_matches = []

        if include_pods or len(workload_matches) == 0:
            pods_searched = True
            pod_result = client.search_pods(name_contains, namespace)
            if pod_result.get("success"):
                pod_matches = pod_result.get("pods", [])
                # Add cluster name to pod matches
                for match in pod_matches:
                    match["cluster"] = cluster_name

        # Combine matches
        all_matches = workload_matches + pod_matches

        return {
            "matches": all_matches,
            "clusters_searched": [cluster_name],
            "clusters_failed": [],
            "pods_searched": pods_searched,
            "unavailable_kinds": unavailable_kinds,
        }

    except Exception as exc:
        return {
            "matches": [],
            "clusters_searched": [],
            "clusters_failed": [{"cluster": cluster_name, "reason": str(exc)}],
            "pods_searched": False,
            "unavailable_kinds": [],
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
        default_namespace: str = "",  # noqa: ARG002 — see below
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

        # Resolve cluster selection
        configs = cluster_configs or {}
        default_conn = {
            "kubeconfig": kubeconfig,
            "kubeconfig_path": kubeconfig_path,
            "context": context,
            "namespace": default_namespace,
        }

        if cluster:
            # Single cluster specified
            selected_clusters = {cluster: configs.get(cluster)}
            if selected_clusters[cluster] is None:
                available = sorted(configs)
                return tool_unavailable(
                    "kubernetes", f"unknown cluster '{cluster}'; available clusters: {available}"
                )
        else:
            # All clusters
            if configs:
                selected_clusters = configs
            else:
                # Fallback to default
                selected_clusters = {"default": default_conn}

        # Validate all selected clusters exist
        for cluster_name, cluster_conn in selected_clusters.items():
            if cluster_conn is None:
                available = sorted(configs)
                return tool_unavailable(
                    "kubernetes",
                    f"unknown cluster '{cluster_name}'; available clusters: {available}",
                )

        # Fan out across selected clusters with deadline
        deadline = time.monotonic() + _FLEET_SEARCH_DEADLINE_SECONDS
        ctx = contextvars.copy_context()

        all_matches: list[dict[str, Any]] = []
        clusters_searched: list[str] = []
        clusters_failed: list[dict[str, str]] = []
        all_unavailable_kinds: list[dict[str, str]] = []
        pods_searched = False

        with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_CLUSTERS) as pool:
            def _submit_cluster_search(
                cname: str, cconn: dict[str, Any]
            ) -> dict[str, Any]:
                return ctx.run(
                    _search_one_cluster,
                    cname,
                    cconn,
                    name_contains,
                    search_namespace,
                    include_pods,
                )

            futures = {
                pool.submit(_submit_cluster_search, cluster_name, cluster_conn): cluster_name
                for cluster_name, cluster_conn in selected_clusters.items()
                if cluster_conn is not None  # Should never be None after validation above
            }

            done, pending = concurrent.futures.wait(
                futures, timeout=max(0.0, deadline - time.monotonic())
            )

            # Process completed futures
            for future in done:
                try:
                    result = future.result()
                    all_matches.extend(result["matches"])
                    clusters_searched.extend(result["clusters_searched"])
                    clusters_failed.extend(result["clusters_failed"])
                    all_unavailable_kinds.extend(result["unavailable_kinds"])
                    if result["pods_searched"]:
                        pods_searched = True
                except Exception as exc:
                    cluster_name = futures[future]
                    clusters_failed.append({"cluster": cluster_name, "reason": str(exc)})

            # Handle timed-out futures
            for future in pending:
                cluster_name = futures[future]
                clusters_failed.append(
                    {"cluster": cluster_name, "reason": "search deadline exceeded"}
                )

            # Deliberately leak threads: shutdown(wait=False, cancel_futures=True)
            # cannot interrupt a thread already inside a socket read; those threads
            # finish when their 60s read timeout fires. They touch nothing shared
            # and write to no collected structure after the deadline.
            pool.shutdown(wait=False, cancel_futures=True)

        # Sort matches by (cluster, namespace, kind, name)
        all_matches.sort(
            key=lambda match: (
                match.get("cluster", ""),
                match.get("namespace", ""),
                match.get("kind", ""),
                match.get("name", ""),
            )
        )

        # Deduplicate truncated kinds across clusters
        truncated_kinds = sorted(
            {
                kind
                for match in all_matches
                if match.get("truncated_kinds")
                for kind in match.get("truncated_kinds", [])
            }
        )

        # Determine if any results were truncated
        truncated = len(truncated_kinds) > 0

        # Partial if any clusters failed or truncated
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
