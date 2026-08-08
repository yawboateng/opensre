"""Tests for Kubernetes investigation tools."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.agent_harness.tools.registry import RegisteredTool
from core.execution import execute_tool_calls
from core.llm.types import ToolCall

from integrations.kubernetes.client import _RESOURCE_DISPATCH
from integrations.kubernetes.tools import (
    KubernetesDescribePodTool,
    KubernetesGetEventsTool,
    KubernetesGetPodLogsTool,
    KubernetesGetResourceTool,
    KubernetesListClustersTool,
    KubernetesListConfigMapsTool,
    KubernetesListDaemonSetsTool,
    KubernetesListDeploymentsTool,
    KubernetesListIngressesTool,
    KubernetesListNamespacesTool,
    KubernetesListNodesTool,
    KubernetesListPodsTool,
    KubernetesListServicesTool,
    KubernetesListStatefulSetsTool,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state

_MINIMAL_KUBECONFIG = (
    "apiVersion: v1\n"
    "clusters: []\n"
    "contexts: []\n"
    "current-context: ''\n"
    "kind: Config\n"
    "preferences: {}\n"
    "users: []\n"
)

_K8S_SOURCE = {
    "connection_verified": True,
    "kubeconfig": _MINIMAL_KUBECONFIG,
    "context": "",
    "namespace": "default",
}


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


class TestKubernetesListPodsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListPodsTool()


class TestKubernetesGetPodLogsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesGetPodLogsTool()


class TestKubernetesListDeploymentsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListDeploymentsTool()


class TestKubernetesGetEventsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesGetEventsTool()


class TestKubernetesDescribePodContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesDescribePodTool()


class TestKubernetesListNodesContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListNodesTool()


class TestKubernetesListServicesContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListServicesTool()


class TestKubernetesListStatefulSetsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListStatefulSetsTool()


class TestKubernetesListDaemonSetsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListDaemonSetsTool()


class TestKubernetesListIngressesContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListIngressesTool()


class TestKubernetesListConfigMapsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListConfigMapsTool()


class TestKubernetesGetResourceContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesGetResourceTool()


# ---------------------------------------------------------------------------
# is_available / extract_params
# ---------------------------------------------------------------------------


def test_list_pods_is_available_requires_kubeconfig() -> None:
    tool = KubernetesListPodsTool()
    assert tool.is_available({"kubernetes": _K8S_SOURCE}) is True
    assert tool.is_available({}) is False
    assert tool.is_available({"kubernetes": {}}) is False
    assert tool.is_available({"kubernetes": {"kubeconfig": ""}}) is False


def test_list_pods_extract_params_maps_fields() -> None:
    tool = KubernetesListPodsTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["kubeconfig"] == sources["kubernetes"]["kubeconfig"]
    assert params["namespace"] == "default"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_mock_pod(name: str, phase: str = "Running") -> MagicMock:
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = "default"
    pod.metadata.labels = {"app": "test"}
    pod.metadata.creation_timestamp = None
    pod.status.phase = phase
    pod.status.conditions = []
    pod.status.container_statuses = []
    pod.spec.node_name = "node-1"
    return pod


def _make_client_with_core(mock_core: MagicMock) -> Any:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    client = KubernetesClient(cfg)
    client._core_v1 = mock_core
    client._apps_v1 = MagicMock()
    client._networking_v1 = MagicMock()
    return client


def _make_client_with_apps(mock_apps: MagicMock) -> Any:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    client = KubernetesClient(cfg)
    client._core_v1 = MagicMock()
    client._apps_v1 = mock_apps
    client._networking_v1 = MagicMock()
    return client


def _make_client_with_networking(mock_networking: MagicMock) -> Any:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    client = KubernetesClient(cfg)
    client._core_v1 = MagicMock()
    client._apps_v1 = MagicMock()
    client._networking_v1 = mock_networking
    return client


# ---------------------------------------------------------------------------
# list_pods run()
# ---------------------------------------------------------------------------


def test_list_pods_run_happy_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mock_pod_list = MagicMock()
    mock_pod_list.items = [_make_mock_pod("web-abc"), _make_mock_pod("web-xyz")]

    mock_core = MagicMock()
    mock_core.list_namespaced_pod.return_value = mock_pod_list

    tool = KubernetesListPodsTool()

    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 2
    assert result["pods"][0]["name"] == "web-abc"


def test_list_pods_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListPodsTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


def test_list_pods_run_returns_unavailable_on_api_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from kubernetes.client.exceptions import ApiException

    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_core.list_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")
    mock_client._core_v1 = mock_core
    mock_client._apps_v1 = MagicMock()
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesListPodsTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is False
    assert "403" in result["error"]


# ---------------------------------------------------------------------------
# get_pod_logs run()
# ---------------------------------------------------------------------------


def test_get_pod_logs_run_happy_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_core.read_namespaced_pod_log.return_value = "line1\nline2\nline3"
    mock_client._core_v1 = mock_core
    mock_client._apps_v1 = MagicMock()
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesGetPodLogsTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            pod_name="web-abc",
            namespace="default",
        )

    assert result["available"] is True
    assert result["total"] == 3
    assert result["lines"] == ["line1", "line2", "line3"]
    assert result["pod_name"] == "web-abc"


def test_get_pod_logs_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesGetPodLogsTool()
    result = tool.run(kubeconfig="", pod_name="web-abc")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_deployments run()
# ---------------------------------------------------------------------------


def _make_mock_deployment(name: str, desired: int = 3, ready: int = 3) -> MagicMock:
    dep = MagicMock()
    dep.metadata.name = name
    dep.metadata.namespace = "default"
    dep.metadata.labels = {}
    dep.metadata.creation_timestamp = None
    dep.spec.replicas = desired
    dep.status.ready_replicas = ready
    dep.status.available_replicas = ready
    dep.status.unavailable_replicas = desired - ready
    dep.status.updated_replicas = ready
    return dep


def test_list_deployments_run_happy_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_apps = MagicMock()
    mock_dep_list = MagicMock()
    mock_dep_list.items = [_make_mock_deployment("api", desired=3, ready=2)]
    mock_apps.list_namespaced_deployment.return_value = mock_dep_list
    mock_client._core_v1 = MagicMock()
    mock_client._apps_v1 = mock_apps
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesListDeploymentsTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["deployments"][0]["name"] == "api"
    assert result["deployments"][0]["unavailable"] == 1


# ---------------------------------------------------------------------------
# get_events run()
# ---------------------------------------------------------------------------


def _make_mock_event(name: str, reason: str = "CrashLoopBackOff") -> MagicMock:
    ev = MagicMock()
    ev.metadata.name = name
    ev.metadata.namespace = "default"
    ev.reason = reason
    ev.message = f"Back-off restarting failed container: {reason}"
    ev.type = "Warning"
    ev.count = 5
    ev.involved_object.kind = "Pod"
    ev.involved_object.name = "web-abc"
    ev.involved_object.namespace = "default"
    ev.first_timestamp = None
    ev.last_timestamp = None
    return ev


def test_get_events_run_happy_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_ev_list = MagicMock()
    mock_ev_list.items = [_make_mock_event("ev-1")]
    mock_core.list_namespaced_event.return_value = mock_ev_list
    mock_client._core_v1 = mock_core
    mock_client._apps_v1 = MagicMock()
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesGetEventsTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["events"][0]["reason"] == "CrashLoopBackOff"
    assert result["events"][0]["type"] == "Warning"


# ---------------------------------------------------------------------------
# describe_pod run()
# ---------------------------------------------------------------------------


def _make_mock_pod_detail(name: str) -> MagicMock:
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = "default"
    pod.metadata.labels = {"app": "web"}
    pod.metadata.annotations = {}
    pod.metadata.creation_timestamp = None
    pod.metadata.owner_references = []
    pod.spec.node_name = "node-1"
    pod.spec.service_account_name = "default"
    pod.spec.node_selector = {}
    pod.spec.volumes = []
    pod.spec.init_containers = []
    c = MagicMock()
    c.name = "app"
    c.image = "nginx:1.25"
    c.ports = []
    c.resources.requests = {"cpu": "100m"}
    c.resources.limits = {"memory": "256Mi"}
    c.env = []
    pod.spec.containers = [c]
    pod.status.phase = "Running"
    pod.status.host_ip = "10.0.0.1"
    pod.status.pod_ip = "192.168.1.5"
    pod.status.conditions = []
    pod.status.container_statuses = []
    pod.status.init_container_statuses = []
    return pod


def test_describe_pod_run_happy_path() -> None:
    mock_core = MagicMock()
    mock_core.read_namespaced_pod.return_value = _make_mock_pod_detail("web-abc")

    tool = KubernetesDescribePodTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, pod_name="web-abc", namespace="default")

    assert result["available"] is True
    assert result["name"] == "web-abc"
    assert result["spec"]["node_name"] == "node-1"
    assert result["status"]["phase"] == "Running"
    assert result["spec"]["containers"][0]["image"] == "nginx:1.25"


def test_describe_pod_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesDescribePodTool()
    result = tool.run(kubeconfig="", pod_name="web-abc")
    assert result["available"] is False


def test_describe_pod_run_includes_valuefrom_env_names_without_values() -> None:
    literal_env = MagicMock()
    literal_env.name = "LOG_LEVEL"
    literal_env.value = "debug"
    secret_env = MagicMock()
    secret_env.name = "DB_PASSWORD"
    secret_env.value = None

    pod = _make_mock_pod_detail("web-abc")
    pod.spec.containers[0].env = [literal_env, secret_env]

    mock_core = MagicMock()
    mock_core.read_namespaced_pod.return_value = pod

    tool = KubernetesDescribePodTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, pod_name="web-abc", namespace="default")

    env_names = result["spec"]["containers"][0]["env"]
    assert env_names == ["LOG_LEVEL", "DB_PASSWORD"]
    assert "debug" not in env_names


def test_describe_pod_run_strips_last_applied_config_annotation() -> None:
    pod = _make_mock_pod_detail("web-abc")
    pod.metadata.annotations = {
        "kubectl.kubernetes.io/last-applied-configuration": (
            '{"spec":{"containers":[{"env":[{"name":"DB_PASSWORD","value":"hunter2"}]}]}}'
        ),
        "some-other/annotation": "keep-me",
    }

    mock_core = MagicMock()
    mock_core.read_namespaced_pod.return_value = pod

    tool = KubernetesDescribePodTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, pod_name="web-abc", namespace="default")

    assert "kubectl.kubernetes.io/last-applied-configuration" not in result["annotations"]
    assert result["annotations"]["some-other/annotation"] == "keep-me"


# ---------------------------------------------------------------------------
# list_nodes run()
# ---------------------------------------------------------------------------


def _make_mock_node(name: str, ready: bool = True) -> MagicMock:
    node = MagicMock()
    node.metadata.name = name
    node.metadata.labels = {"kubernetes.io/hostname": name}
    node.metadata.creation_timestamp = None
    node.spec.taints = []
    cond = MagicMock()
    cond.type = "Ready"
    cond.status = "True" if ready else "False"
    cond.reason = "KubeletReady"
    cond.message = "kubelet is posting ready status"
    node.status.conditions = [cond]
    node.status.capacity = {"cpu": "4", "memory": "8Gi"}
    node.status.allocatable = {"cpu": "3900m", "memory": "7Gi"}
    return node


def test_list_nodes_run_happy_path() -> None:
    mock_core = MagicMock()
    mock_node_list = MagicMock()
    mock_node_list.items = [_make_mock_node("node-1"), _make_mock_node("node-2", ready=False)]
    mock_core.list_node.return_value = mock_node_list

    tool = KubernetesListNodesTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG)

    assert result["available"] is True
    assert result["total"] == 2
    assert result["nodes"][0]["name"] == "node-1"
    assert result["nodes"][0]["conditions"][0]["type"] == "Ready"
    assert result["nodes"][0]["conditions"][0]["status"] == "True"
    assert result["nodes"][1]["conditions"][0]["status"] == "False"


def test_list_nodes_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListNodesTool()
    result = tool.run(kubeconfig="")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_services run()
# ---------------------------------------------------------------------------


def _make_mock_service(name: str, svc_type: str = "ClusterIP") -> MagicMock:
    svc = MagicMock()
    svc.metadata.name = name
    svc.metadata.namespace = "default"
    svc.metadata.labels = {}
    svc.metadata.creation_timestamp = None
    svc.spec.type = svc_type
    svc.spec.cluster_ip = "10.96.0.1"
    svc.spec.external_i_ps = []
    svc.spec.selector = {"app": name}
    port = MagicMock()
    port.name = "http"
    port.port = 80
    port.target_port = 8080
    port.protocol = "TCP"
    port.node_port = None
    svc.spec.ports = [port]
    return svc


def test_list_services_run_happy_path() -> None:
    mock_core = MagicMock()
    mock_svc_list = MagicMock()
    mock_svc_list.items = [_make_mock_service("api"), _make_mock_service("frontend")]
    mock_core.list_namespaced_service.return_value = mock_svc_list

    tool = KubernetesListServicesTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 2
    assert result["services"][0]["name"] == "api"
    assert result["services"][0]["type"] == "ClusterIP"
    assert result["services"][0]["ports"][0]["port"] == 80


def test_list_services_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListServicesTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_statefulsets run()
# ---------------------------------------------------------------------------


def _make_mock_statefulset(name: str, desired: int = 3, ready: int = 3) -> MagicMock:
    sts = MagicMock()
    sts.metadata.name = name
    sts.metadata.namespace = "default"
    sts.metadata.labels = {}
    sts.metadata.creation_timestamp = None
    sts.spec.replicas = desired
    sts.status.ready_replicas = ready
    sts.status.current_replicas = ready
    sts.status.updated_replicas = ready
    return sts


def test_list_statefulsets_run_happy_path() -> None:
    mock_apps = MagicMock()
    mock_sts_list = MagicMock()
    mock_sts_list.items = [_make_mock_statefulset("postgres", desired=3, ready=2)]
    mock_apps.list_namespaced_stateful_set.return_value = mock_sts_list

    tool = KubernetesListStatefulSetsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_apps(mock_apps),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["statefulsets"][0]["name"] == "postgres"
    assert result["statefulsets"][0]["desired"] == 3
    assert result["statefulsets"][0]["ready"] == 2


def test_list_statefulsets_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListStatefulSetsTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_daemonsets run()
# ---------------------------------------------------------------------------


def _make_mock_daemonset(name: str, desired: int = 5, ready: int = 5) -> MagicMock:
    ds = MagicMock()
    ds.metadata.name = name
    ds.metadata.namespace = "default"
    ds.metadata.labels = {}
    ds.metadata.creation_timestamp = None
    ds.status.desired_number_scheduled = desired
    ds.status.current_number_scheduled = desired
    ds.status.number_ready = ready
    ds.status.updated_number_scheduled = ready
    ds.status.number_available = ready
    return ds


def test_list_daemonsets_run_happy_path() -> None:
    mock_apps = MagicMock()
    mock_ds_list = MagicMock()
    mock_ds_list.items = [_make_mock_daemonset("fluentd", desired=5, ready=4)]
    mock_apps.list_namespaced_daemon_set.return_value = mock_ds_list

    tool = KubernetesListDaemonSetsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_apps(mock_apps),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["daemonsets"][0]["name"] == "fluentd"
    assert result["daemonsets"][0]["desired"] == 5
    assert result["daemonsets"][0]["ready"] == 4


def test_list_daemonsets_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListDaemonSetsTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_ingresses run()
# ---------------------------------------------------------------------------


def _make_mock_ingress(name: str, host: str = "api.example.com") -> MagicMock:
    ing = MagicMock()
    ing.metadata.name = name
    ing.metadata.namespace = "default"
    ing.metadata.labels = {}
    ing.metadata.creation_timestamp = None
    ing.spec.ingress_class_name = "nginx"
    path = MagicMock()
    path.path = "/api"
    path.path_type = "Prefix"
    path.backend.service.name = "api-svc"
    path.backend.service.port.number = 80
    rule = MagicMock()
    rule.host = host
    rule.http.paths = [path]
    ing.spec.rules = [rule]
    ing.spec.tls = []
    ing.status.load_balancer.ingress = []
    return ing


def test_list_ingresses_run_happy_path() -> None:
    mock_networking = MagicMock()
    mock_ing_list = MagicMock()
    mock_ing_list.items = [_make_mock_ingress("api-ingress")]
    mock_networking.list_namespaced_ingress.return_value = mock_ing_list

    tool = KubernetesListIngressesTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_networking(mock_networking),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["ingresses"][0]["name"] == "api-ingress"
    assert result["ingresses"][0]["ingress_class"] == "nginx"
    assert result["ingresses"][0]["rules"][0]["host"] == "api.example.com"
    assert result["ingresses"][0]["rules"][0]["paths"][0]["service_name"] == "api-svc"


def test_list_ingresses_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListIngressesTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_configmaps run()
# ---------------------------------------------------------------------------


def _make_mock_configmap(name: str, data: dict[str, str] | None = None) -> MagicMock:
    cm = MagicMock()
    cm.metadata.name = name
    cm.metadata.namespace = "default"
    cm.metadata.labels = {}
    cm.metadata.creation_timestamp = None
    cm.data = data or {"key1": "value1", "key2": "value2"}
    return cm


def test_list_configmaps_run_happy_path() -> None:
    mock_core = MagicMock()
    mock_cm_list = MagicMock()
    mock_cm_list.items = [
        _make_mock_configmap("app-config", {"DB_HOST": "postgres:5432", "LOG_LEVEL": "info"})
    ]
    mock_core.list_namespaced_config_map.return_value = mock_cm_list

    tool = KubernetesListConfigMapsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["configmaps"][0]["name"] == "app-config"
    assert result["configmaps"][0]["data"]["DB_HOST"] == "postgres:5432"
    assert "DB_HOST" in result["configmaps"][0]["data_keys"]


def test_list_configmaps_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListConfigMapsTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# get_resource run()
# ---------------------------------------------------------------------------


def test_get_resource_run_happy_path() -> None:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_dep = MagicMock()
    mock_apps = MagicMock()
    mock_apps.read_namespaced_deployment.return_value = mock_dep
    mock_client._core_v1 = mock_core
    mock_client._apps_v1 = mock_apps
    mock_client._networking_v1 = MagicMock()
    # simulate sanitize_for_serialization
    mock_client._api_client = MagicMock()
    mock_client._api_client.sanitize_for_serialization.return_value = {
        "kind": "Deployment",
        "metadata": {"name": "api"},
    }

    tool = KubernetesGetResourceTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            resource_type="deployment",
            name="api",
            namespace="default",
        )

    assert result["available"] is True
    assert result["resource_type"] == "deployment"
    assert result["name"] == "api"
    assert result["resource"]["kind"] == "Deployment"


def test_get_resource_run_strips_last_applied_config_annotation() -> None:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_client._core_v1 = MagicMock()
    mock_apps = MagicMock()
    mock_apps.read_namespaced_deployment.return_value = MagicMock()
    mock_client._apps_v1 = mock_apps
    mock_client._networking_v1 = MagicMock()
    mock_client._api_client = MagicMock()
    mock_client._api_client.sanitize_for_serialization.return_value = {
        "kind": "Deployment",
        "metadata": {
            "name": "api",
            "annotations": {
                "kubectl.kubernetes.io/last-applied-configuration": (
                    '{"spec":{"template":{"spec":{"containers":'
                    '[{"env":[{"name":"DB_PASSWORD","value":"hunter2"}]}]}}}}'
                ),
                "some-other/annotation": "keep-me",
            },
        },
    }

    tool = KubernetesGetResourceTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            resource_type="deployment",
            name="api",
            namespace="default",
        )

    annotations = result["resource"]["metadata"]["annotations"]
    assert "kubectl.kubernetes.io/last-applied-configuration" not in annotations
    assert annotations["some-other/annotation"] == "keep-me"


def test_get_resource_run_unsupported_type() -> None:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_client._core_v1 = MagicMock()
    mock_client._apps_v1 = MagicMock()
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesGetResourceTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG, resource_type="foobar", name="x", namespace="default"
        )

    assert result["available"] is False
    assert "foobar" in result["error"]


def test_get_resource_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesGetResourceTool()
    result = tool.run(kubeconfig="", resource_type="deployment", name="api")
    assert result["available"] is False


# ---------------------------------------------------------------------------
# Multi-cluster selection
# ---------------------------------------------------------------------------


class TestKubernetesListClustersContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListClustersTool()


_MULTI_CLUSTER_SOURCES = {
    "kubernetes": {"kubeconfig": "kc-dev", "context": "ctx-dev", "namespace": "dev"},
    "_all_kubernetes_instances": [
        {
            "name": "gke-dev",
            "tags": {"env": "dev"},
            "config": {"kubeconfig": "kc-dev", "context": "ctx-dev", "namespace": "dev"},
        },
        {
            "name": "gke-prod",
            "tags": {"env": "prod"},
            "config": {"kubeconfig_path": "/p/prod", "context": "ctx-prod", "namespace": "prod"},
        },
    ],
}


def test_extract_params_includes_cluster_configs() -> None:
    tool = KubernetesListPodsTool()
    params = tool.extract_params(_MULTI_CLUSTER_SOURCES)
    # cluster_configs is trusted connection configuration: it MUST be a protected
    # injected param so the runtime re-forces it over any model-supplied value.
    assert "cluster_configs" in tool.injected_params
    assert sorted(params["cluster_configs"]) == ["gke-dev", "gke-prod"]
    assert params["cluster_configs"]["gke-prod"]["kubeconfig_path"] == "/p/prod"


def test_cluster_configs_is_protected_on_every_connection_tool() -> None:
    # Every tool that builds a client from cluster_configs must protect it.
    connection_tools = [
        KubernetesListPodsTool(),
        KubernetesGetPodLogsTool(),
        KubernetesListDeploymentsTool(),
        KubernetesGetEventsTool(),
        KubernetesDescribePodTool(),
        KubernetesListNodesTool(),
        KubernetesListServicesTool(),
        KubernetesListStatefulSetsTool(),
        KubernetesListDaemonSetsTool(),
        KubernetesListIngressesTool(),
        KubernetesListConfigMapsTool(),
        KubernetesGetResourceTool(),
    ]
    for tool in connection_tools:
        assert "cluster_configs" in tool.injected_params, tool.name


def test_model_cannot_override_cluster_configs_via_tool_input() -> None:
    """A model-supplied cluster_configs must never replace the trusted map.

    Exercises the real runtime merge (core.execution): because cluster_configs
    is a protected injected param, the extracted (store-derived) map wins, so
    the client is built from the registered cluster's connection fields, not
    the model's.
    """
    from core.execution import execute_tool_calls
    from core.llm.types import ToolCall
    from core.tool_framework.registered_tool import RegisteredTool

    mock_pod_list = MagicMock()
    mock_pod_list.items = []
    mock_core = MagicMock()
    mock_core.list_namespaced_pod.return_value = mock_pod_list

    resolved = {
        "kubernetes": {"kubeconfig": "kc-dev", "context": "ctx-dev", "namespace": "dev"},
        "_all_kubernetes_instances": [
            {"name": "gke-dev", "tags": {}, "config": {"kubeconfig": "kc-dev"}},
            {
                "name": "gke-prod",
                "tags": {},
                "config": {"kubeconfig_path": "/trusted/prod", "namespace": "prod"},
            },
        ],
    }
    malicious_input = {
        "cluster": "gke-prod",
        "cluster_configs": {"gke-prod": {"kubeconfig_path": "/attacker/controlled"}},
    }

    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ) as mock_make:
        execute_tool_calls(
            [ToolCall(id="c1", name="kubernetes_list_pods", input=malicious_input)],
            [RegisteredTool.from_base_tool(KubernetesListPodsTool())],
            resolved,
        )

    built_from = mock_make.call_args.args[0]["kubernetes"]
    assert built_from["kubeconfig_path"] == "/trusted/prod"
    assert built_from["kubeconfig_path"] != "/attacker/controlled"


def test_run_targets_named_cluster() -> None:
    mock_pod_list = MagicMock()
    mock_pod_list.items = [_make_mock_pod("prod-pod")]
    mock_core = MagicMock()
    mock_core.list_namespaced_pod.return_value = mock_pod_list

    cluster_configs = KubernetesListPodsTool().extract_params(_MULTI_CLUSTER_SOURCES)[
        "cluster_configs"
    ]

    tool = KubernetesListPodsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ) as mock_make:
        result = tool.run(
            kubeconfig="kc-dev",
            context="ctx-dev",
            namespace="dev",
            cluster="gke-prod",
            cluster_configs=cluster_configs,
        )

    # Client built from the prod instance's connection fields, not the injected default.
    assert mock_make.call_args.args[0] == {"kubernetes": cluster_configs["gke-prod"]}
    # Query scoped to the prod instance's namespace.
    assert mock_core.list_namespaced_pod.call_args.kwargs["namespace"] == "prod"
    assert result["available"] is True
    assert result["namespace"] == "prod"
    assert result["total"] == 1


def test_run_omitting_cluster_uses_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mock_pod_list = MagicMock()
    mock_pod_list.items = []
    mock_core = MagicMock()
    mock_core.list_namespaced_pod.return_value = mock_pod_list

    tool = KubernetesListPodsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ) as mock_make:
        tool.run(kubeconfig="kc-default", context="ctx-a", default_namespace="ns-a")

    # No cluster named -> the injected default connection fields are used verbatim.
    assert mock_make.call_args.args[0] == {
        "kubernetes": {
            "kubeconfig": "kc-default",
            "kubeconfig_path": "",
            "context": "ctx-a",
            "namespace": "ns-a",
        }
    }
    assert mock_core.list_namespaced_pod.call_args.kwargs["namespace"] == "ns-a"


def test_run_unknown_cluster_errors() -> None:
    tool = KubernetesListPodsTool()
    result = tool.run(
        kubeconfig="kc-default",
        cluster="ghost",
        cluster_configs={"gke-dev": {"kubeconfig": "kc-dev"}},
    )
    assert result["available"] is False
    assert "ghost" in result["error"]
    assert "gke-dev" in result["error"]
    assert result["total"] == 0


def test_list_clusters_is_available() -> None:
    tool = KubernetesListClustersTool()
    assert tool.is_available({"kubernetes": _K8S_SOURCE}) is True
    assert tool.is_available({}) is False


def test_list_clusters_run_lists_registered_instances() -> None:
    tool = KubernetesListClustersTool()
    params = tool.extract_params(_MULTI_CLUSTER_SOURCES)
    result = tool.run(**params)
    assert result["available"] is True
    assert result["total"] == 2
    assert [c["name"] for c in result["clusters"]] == ["gke-dev", "gke-prod"]
    assert result["clusters"][0]["is_default"] is True
    assert result["clusters"][1]["is_default"] is False
    assert result["clusters"][1]["tags"] == {"env": "prod"}


def test_list_clusters_run_single_default() -> None:
    tool = KubernetesListClustersTool()
    params = tool.extract_params({"kubernetes": _K8S_SOURCE})
    result = tool.run(**params)
    assert result["total"] == 1
    assert result["clusters"][0]["name"] == "default"
    assert result["clusters"][0]["is_default"] is True


# --- read-only surface -------------------------------------------------------


def test_every_fetchable_resource_is_read_only() -> None:
    """The kubernetes integration reads; it never writes.

    Documented as the reason auto-registering a GKE cluster is an exposure
    decision rather than a destructive one (``docs/gcp.mdx``), and prose is not
    enforceable. If someone adds a ``patch_``/``delete_``/``create_`` entry here,
    that argument stops being true and this fails rather than the docs quietly
    going stale.
    """
    verbs = {method for _api, method, _cluster_scoped in _RESOURCE_DISPATCH.values()}

    assert verbs, "dispatch table is empty; this test would pass vacuously"
    assert all(verb.startswith("read_") for verb in verbs), sorted(
        verb for verb in verbs if not verb.startswith("read_")
    )


def test_secrets_are_not_fetchable() -> None:
    """``kubernetes_get_resource`` takes an enum, and Secret is deliberately absent.

    Pod logs and configmaps already leak credentials by accident; reading Secrets
    would do it by design.
    """
    assert not [key for key in _RESOURCE_DISPATCH if "secret" in key.lower()]


# --- Namespace targeting tests -----------------------------------------------


def test_model_namespace_argument_survives_the_runtime_merge() -> None:
    """Namespace passed by model reaches the client, not the stored default.

    This test goes through the real execute_tool_calls path rather than calling
    run() directly because it reproduces the bug that _base_params starts
    re-emitting a key named namespace (section 0.4 of the plan). A unit-level
    run() test would miss this since it bypasses the runtime merge.
    """
    # Create a resolved context with a stored namespace different from model's
    resolved = mock_agent_state()
    resolved.integrations_context.sources["kubernetes"] = {
        **_K8S_SOURCE,
        "namespace": "stored-namespace"  # Different from what model will pass
    }

    with patch("integrations.kubernetes.client.KubernetesClient.list_pods") as mock_list:
        mock_list.return_value = {"success": True, "pods": [], "total": 0}

        # Execute with model providing a specific namespace
        result = execute_tool_calls(
            [ToolCall(input={"namespace": "model-namespace"})],
            [RegisteredTool.from_base_tool(KubernetesListPodsTool())],
            resolved
        )

        # The namespace that reached the client should be the model's, not stored
        mock_list.assert_called_once()
        call_args = mock_list.call_args
        assert call_args.kwargs["namespace"] == "model-namespace"


def test_omitted_namespace_falls_back_to_the_named_clusters_namespace() -> None:
    """With no model namespace, use the named cluster's stored namespace."""
    # Two clusters with different stored namespaces
    multi_cluster_sources = {
        "kubernetes": [
            {
                "name": "cluster-a",
                "config": {**_K8S_SOURCE, "namespace": "namespace-a"}
            },
            {
                "name": "cluster-b",
                "config": {**_K8S_SOURCE, "namespace": "namespace-b"}
            }
        ]
    }

    tool = KubernetesListPodsTool()
    params = tool.extract_params(multi_cluster_sources)

    with patch("integrations.kubernetes.client.KubernetesClient.list_pods") as mock_list:
        mock_list.return_value = {"success": True, "pods": [], "total": 0}

        # Call with cluster="cluster-b" and no namespace
        result = tool.run(**params, cluster="cluster-b", namespace="")

        # Should use cluster-b's stored namespace
        mock_list.assert_called_once()
        call_args = mock_list.call_args
        assert call_args.kwargs["namespace"] == "namespace-b"


def test_namespace_falls_back_to_default_when_nothing_is_configured() -> None:
    """With no model namespace and no stored config, fall back to 'default'."""
    source_no_namespace = {
        "connection_verified": True,
        "kubeconfig": _MINIMAL_KUBECONFIG,
        "context": "",
        # no 'namespace' key at all
    }

    tool = KubernetesListPodsTool()
    params = tool.extract_params({"kubernetes": source_no_namespace})

    with patch("integrations.kubernetes.client.KubernetesClient.list_pods") as mock_list:
        mock_list.return_value = {"success": True, "pods": [], "total": 0}

        # Call with empty/whitespace namespace
        result = tool.run(**params, namespace="   ")

        # Should fall back to "default"
        mock_list.assert_called_once()
        call_args = mock_list.call_args
        assert call_args.kwargs["namespace"] == "default"


def test_list_namespaces_degrades_when_the_credential_cannot_list_cluster_wide() -> None:
    """list_namespaces degrades gracefully on 403/401."""
    tool = KubernetesListNamespacesTool()
    params = tool.extract_params({"kubernetes": _K8S_SOURCE})

    # Test 403 forbidden case
    with patch("integrations.kubernetes.client.KubernetesClient.list_namespaces") as mock_list:
        mock_list.return_value = {
            "success": False,
            "forbidden": True,
            "error": "namespaces is forbidden"
        }

        result = tool.run(**params)

        assert result["available"] is True
        assert result["listable"] is False
        assert result["namespaces"] == []
        assert result["total"] == 0
        assert "configured_namespace" in result
        assert "note" in result
        assert "cannot enumerate namespaces cluster-wide" in result["note"]

    # Test non-403 error returns tool_unavailable
    with patch("integrations.kubernetes.client.KubernetesClient.list_namespaces") as mock_list:
        mock_list.return_value = {
            "success": False,
            "error": "connection failed"
        }

        result = tool.run(**params)

        assert result["available"] is False
        assert result["listable"] is False


@pytest.mark.parametrize("tool_class", [
    KubernetesListNodesTool,
    KubernetesListClustersTool,
    KubernetesListNamespacesTool
])
def test_cluster_scoped_tools_expose_no_namespace_parameter(tool_class: type) -> None:
    """Cluster-scoped tools do not expose namespace parameter."""
    tool = tool_class()
    properties = tool.input_schema.get("properties", {})
    assert "namespace" not in properties
