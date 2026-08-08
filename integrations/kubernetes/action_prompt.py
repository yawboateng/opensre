"""Kubernetes investigation guidance for action stage."""

from __future__ import annotations


def kubernetes_action_prompt_fragment() -> str:
    """Kubernetes investigation rules for action stage."""
    return (
        "When the request names a cluster or environment, call "
        "kubernetes_list_clusters first and pass the matching name as cluster on "
        "every subsequent call; do not rely on the default cluster. "
        "An empty pod/deployment list from one namespace is not evidence that a "
        "cluster is healthy — it is evidence that namespace is empty. Say which "
        "cluster and namespace were checked."
    )


__all__ = ["kubernetes_action_prompt_fragment"]
