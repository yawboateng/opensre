"""GKE cluster auto-registration.

Turns a cluster discovered through the GCP API into a named Kubernetes instance
the ``kubernetes_*`` tools can address, closing the gap ``gcp_list_gke_clusters``
reports as ``unregistered_clusters``.
"""

from __future__ import annotations

from integrations.gcp.gke.autoregister import (
    register_now,
    requested_projects,
    start_gke_autoregistration,
)
from integrations.gcp.gke.discovery import DiscoveredCluster, discover_clusters
from integrations.gcp.gke.kubeconfig import AUTH_PLUGIN, build_kubeconfig, plugin_installed
from integrations.gcp.gke.registration import (
    ClusterRegistration,
    Outcome,
    RegistrationReport,
    register_gke_clusters,
)

__all__ = [
    "AUTH_PLUGIN",
    "ClusterRegistration",
    "DiscoveredCluster",
    "Outcome",
    "RegistrationReport",
    "build_kubeconfig",
    "discover_clusters",
    "plugin_installed",
    "register_gke_clusters",
    "register_now",
    "requested_projects",
    "start_gke_autoregistration",
]
