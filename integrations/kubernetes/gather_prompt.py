"""Kubernetes investigation guidance for gather stage."""

from __future__ import annotations


def kubernetes_gather_prompt_fragment() -> str:
    """Kubernetes investigation rules for gather stage."""
    return (
        "When the request names a cluster or environment, call "
        "kubernetes_list_clusters first and pass the matching name as cluster on "
        "every subsequent call; do not rely on the default cluster. "
        "When the request names a namespace, pass it as namespace. When it does "
        "not and the cluster is unfamiliar, call kubernetes_list_namespaces before "
        "concluding nothing is wrong. "
        "An empty pod/deployment list from one namespace is not evidence that a "
        "cluster is healthy — it is evidence that namespace is empty. Say which "
        "cluster and namespace were checked."
    )


__all__ = ["kubernetes_gather_prompt_fragment"]
