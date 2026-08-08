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
    )


__all__ = ["kubernetes_action_prompt_fragment"]
