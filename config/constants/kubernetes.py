"""Kubernetes environment variable names."""

from __future__ import annotations

KUBECONFIG_PATH_ENV = "KUBECONFIG"
KUBECONFIG_CONTENT_ENV = "KUBECONFIG_CONTENT"
KUBECONFIG_CONTEXT_ENV = "KUBECONFIG_CONTEXT"
KUBECONFIG_NAMESPACE_ENV = "KUBECONFIG_NAMESPACE"

#: Last-resort fallback namespace after the model argument and the cluster's
#: stored config.
DEFAULT_KUBERNETES_NAMESPACE = "default"

#: JSON array registering several named clusters at once. Authoritative: the
#: local store overrides the environment record for a whole service, so
#: anything that writes clusters to the store must stand down when this is set
#: rather than replace the operator's declared set.
KUBERNETES_INSTANCES_ENV = "KUBERNETES_INSTANCES"

#: Argo Rollouts CRD coordinates. A Rollout replaces the Deployment as the
#: owning workload object, so a cluster running Argo has real workloads that
#: are invisible to every apps/v1 lister.
ARGO_ROLLOUTS_GROUP = "argoproj.io"
ARGO_ROLLOUTS_VERSION = "v1alpha1"
ARGO_ROLLOUTS_PLURAL = "rollouts"

__all__ = [
    "ARGO_ROLLOUTS_GROUP",
    "ARGO_ROLLOUTS_PLURAL",
    "ARGO_ROLLOUTS_VERSION",
    "DEFAULT_KUBERNETES_NAMESPACE",
    "KUBECONFIG_CONTENT_ENV",
    "KUBECONFIG_CONTEXT_ENV",
    "KUBECONFIG_NAMESPACE_ENV",
    "KUBECONFIG_PATH_ENV",
    "KUBERNETES_INSTANCES_ENV",
]
