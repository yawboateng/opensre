"""Kubernetes investigation guidance for action stage."""

from __future__ import annotations


def kubernetes_action_prompt_fragment() -> str:
    """Kubernetes investigation rules for action stage."""
    return (
        "When the request names a cluster or environment, call "
        "kubernetes_list_clusters first and pass the matching name as cluster on "
        "every subsequent call; do not rely on the default cluster. "
        "When the request names a namespace, pass it as namespace. When it does "
        "not and the cluster is unfamiliar, call kubernetes_list_namespaces before "
        "concluding nothing is wrong. "
        "An empty pod/deployment list from one namespace is not evidence that a "
        "cluster is healthy or that a workload is absent — a workload may be an "
        "Argo Rollout; use kubernetes_list_workloads for 'does X exist / is it "
        "healthy'. Say which cluster and namespace were checked."
        "A cloud project named in an alert is a monitoring scope, not a runtime "
        "location: logs, metrics and Error Reporting may live in a dedicated "
        "observability project or beside the workload — it varies, so check "
        "both. When unsure which project holds the signal, sweep with "
        "project='*' where the tool accepts it, and otherwise query the "
        "observability project directly. If you still cannot place a workload, "
        "call kubernetes_search_fleet before saying it does not exist. An "
        "unavailable tool is a missing capability, never a missing connection "
        "or credential."
    )


__all__ = ["kubernetes_action_prompt_fragment"]
