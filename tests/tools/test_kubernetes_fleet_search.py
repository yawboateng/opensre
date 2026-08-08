"""Tests for the Kubernetes fleet search tool."""

import time
from unittest.mock import MagicMock, patch

from integrations.kubernetes.tools.fleet_search import (
    KubernetesSearchFleetTool,
    _search_one_cluster_pods,
    _search_one_cluster_workloads,
)


class TestKubernetesFleetSearch:
    """Test the Kubernetes fleet search tool."""

    def setup_method(self):
        """Set up each test."""
        self.tool = KubernetesSearchFleetTool()

    def test_unavailable_when_not_configured(self):
        """Tool returns unavailable when kubernetes not configured."""
        with patch(
            "integrations.kubernetes.tools.fleet_search._is_available",
            return_value=False,
        ):
            result = self.tool.run(name_contains="test")

        assert result["source"] == "kubernetes"
        assert result["available"] is False
        assert "error" in result

    def test_single_cluster_success(self, monkeypatch):
        """Search a single cluster successfully."""
        # Mock _is_available
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._is_available",
            lambda _: True,
        )

        # Mock cluster resolution
        mock_client = MagicMock()
        mock_conn = {"cluster_name": "test-cluster"}

        def _mock_resolve_client(cluster_name, configs, default_conn):
            if cluster_name == "test-cluster":
                return mock_client, mock_conn, None
            return None, None, "unknown cluster"

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._resolve_client",
            _mock_resolve_client,
        )

        # Mock search functions
        def _mock_search_workloads(cluster_name, cluster_conn, name_contains, namespace):
            return {
                "success": True,
                "matches": [
                    {
                        "cluster": cluster_name,
                        "namespace": "default",
                        "kind": "Deployment",
                        "name": "test-deployment",
                    }
                ],
                "unavailable_kinds": [],
                "truncated": False,
            }

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._search_one_cluster_workloads",
            _mock_search_workloads,
        )

        result = self.tool.run(
            name_contains="test",
            cluster="test-cluster",
            cluster_configs={"test-cluster": mock_conn},
        )

        assert result["source"] == "kubernetes"
        assert result["available"] is True
        assert result["total"] == 1
        assert len(result["matches"]) == 1
        assert result["matches"][0]["name"] == "test-deployment"
        assert result["clusters_searched"] == ["test-cluster"]
        assert result["clusters_failed"] == []
        assert result["partial"] is False
        assert result["pods_searched"] is False

    def test_unknown_cluster_error(self, monkeypatch):
        """Tool returns error for unknown cluster."""
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._is_available",
            lambda _: True,
        )

        def _mock_resolve_client(cluster_name, configs, default_conn):
            return None, None, f"unknown cluster '{cluster_name}'"

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._resolve_client",
            _mock_resolve_client,
        )

        result = self.tool.run(
            name_contains="test",
            cluster="unknown-cluster",
            cluster_configs={},
        )

        assert result["available"] is False
        assert "unknown cluster" in result["error"]

    def test_multi_cluster_success(self, monkeypatch):
        """Search multiple clusters successfully."""
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._is_available",
            lambda _: True,
        )

        # Mock search functions to return different results per cluster
        def _mock_search_workloads(cluster_name, cluster_conn, name_contains, namespace):
            if cluster_name == "cluster-a":
                return {
                    "success": True,
                    "matches": [
                        {
                            "cluster": cluster_name,
                            "namespace": "default",
                            "kind": "Deployment",
                            "name": "app-a",
                        }
                    ],
                    "unavailable_kinds": [],
                    "truncated": False,
                }
            elif cluster_name == "cluster-b":
                return {
                    "success": True,
                    "matches": [
                        {
                            "cluster": cluster_name,
                            "namespace": "prod",
                            "kind": "StatefulSet",
                            "name": "db-b",
                        }
                    ],
                    "unavailable_kinds": [],
                    "truncated": False,
                }
            return {"success": True, "matches": [], "unavailable_kinds": [], "truncated": False}

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._search_one_cluster_workloads",
            _mock_search_workloads,
        )

        configs = {
            "cluster-a": {"cluster_name": "cluster-a"},
            "cluster-b": {"cluster_name": "cluster-b"},
        }

        result = self.tool.run(
            name_contains="app",
            cluster_configs=configs,
        )

        assert result["source"] == "kubernetes"
        assert result["available"] is True
        assert result["total"] == 2
        assert len(result["matches"]) == 2
        assert sorted(result["clusters_searched"]) == ["cluster-a", "cluster-b"]
        assert result["clusters_failed"] == []
        assert result["partial"] is False

        # Check sorting: cluster-a should come before cluster-b
        assert result["matches"][0]["cluster"] == "cluster-a"
        assert result["matches"][1]["cluster"] == "cluster-b"

    def test_partial_cluster_failure(self, monkeypatch):
        """Handle partial cluster failures correctly."""
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._is_available",
            lambda _: True,
        )

        def _mock_search_workloads(cluster_name, cluster_conn, name_contains, namespace):
            if cluster_name == "good-cluster":
                return {
                    "success": True,
                    "matches": [
                        {
                            "cluster": cluster_name,
                            "namespace": "default",
                            "kind": "Deployment",
                            "name": "working-app",
                        }
                    ],
                    "unavailable_kinds": [],
                    "truncated": False,
                }
            elif cluster_name == "bad-cluster":
                return {
                    "success": False,
                    "error": "connection refused",
                    "matches": [],
                    "unavailable_kinds": [],
                    "truncated": False,
                }
            return {"success": True, "matches": [], "unavailable_kinds": [], "truncated": False}

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._search_one_cluster_workloads",
            _mock_search_workloads,
        )

        configs = {
            "good-cluster": {"cluster_name": "good-cluster"},
            "bad-cluster": {"cluster_name": "bad-cluster"},
        }

        result = self.tool.run(
            name_contains="app",
            cluster_configs=configs,
        )

        assert result["total"] == 1
        assert result["clusters_searched"] == ["good-cluster"]
        assert len(result["clusters_failed"]) == 1
        assert result["clusters_failed"][0]["cluster"] == "bad-cluster"
        assert result["clusters_failed"][0]["reason"] == "connection refused"
        assert result["partial"] is True

    def test_two_phase_heuristic_pods_triggered(self, monkeypatch):
        """Test that pod search is triggered when no workload owners found."""
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._is_available",
            lambda _: True,
        )

        # Mock workload search to return empty
        def _mock_search_workloads(cluster_name, cluster_conn, name_contains, namespace):
            return {
                "success": True,
                "matches": [],
                "unavailable_kinds": [],
                "truncated": False,
            }

        # Mock pod search to return results
        def _mock_search_pods(cluster_name, cluster_conn, name_contains, namespace):
            return {
                "success": True,
                "matches": [
                    {
                        "cluster": cluster_name,
                        "namespace": "default",
                        "kind": "Pod",
                        "name": "orphan-pod",
                    }
                ],
                "unavailable_kinds": [],
                "truncated": False,
            }

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._search_one_cluster_workloads",
            _mock_search_workloads,
        )
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._search_one_cluster_pods",
            _mock_search_pods,
        )

        result = self.tool.run(
            name_contains="orphan",
            cluster_configs={"test": {"cluster_name": "test"}},
        )

        assert result["total"] == 1
        assert result["matches"][0]["kind"] == "Pod"
        assert result["pods_searched"] is True

    def test_include_pods_forces_pod_search(self, monkeypatch):
        """Test that include_pods=True forces pod search even with workload matches."""
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._is_available",
            lambda _: True,
        )

        # Mock workload search to return results
        def _mock_search_workloads(cluster_name, cluster_conn, name_contains, namespace):
            return {
                "success": True,
                "matches": [
                    {
                        "cluster": cluster_name,
                        "namespace": "default",
                        "kind": "Deployment",
                        "name": "test-deployment",
                    }
                ],
                "unavailable_kinds": [],
                "truncated": False,
            }

        # Mock pod search to return additional results
        def _mock_search_pods(cluster_name, cluster_conn, name_contains, namespace):
            return {
                "success": True,
                "matches": [
                    {
                        "cluster": cluster_name,
                        "namespace": "default",
                        "kind": "Pod",
                        "name": "test-pod",
                    }
                ],
                "unavailable_kinds": [],
                "truncated": False,
            }

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._search_one_cluster_workloads",
            _mock_search_workloads,
        )
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._search_one_cluster_pods",
            _mock_search_pods,
        )

        result = self.tool.run(
            name_contains="test",
            include_pods=True,
            cluster_configs={"test": {"cluster_name": "test"}},
        )

        assert result["total"] == 2
        assert result["pods_searched"] is True
        # Should have both deployment and pod
        kinds = {match["kind"] for match in result["matches"]}
        assert kinds == {"Deployment", "Pod"}

    def test_deadline_timeout(self, monkeypatch):
        """Test that deadline timeout is handled correctly."""
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._is_available",
            lambda _: True,
        )

        # Mock search to take longer than deadline
        def _mock_search_workloads(cluster_name, cluster_conn, name_contains, namespace):
            time.sleep(2)  # Longer than the mocked deadline
            return {
                "success": True,
                "matches": [],
                "unavailable_kinds": [],
                "truncated": False,
            }

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._search_one_cluster_workloads",
            _mock_search_workloads,
        )

        # Mock a very short deadline
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._FLEET_SEARCH_DEADLINE_SECONDS",
            0.1,
        )

        result = self.tool.run(
            name_contains="test",
            cluster_configs={"slow-cluster": {"cluster_name": "slow-cluster"}},
        )

        assert len(result["clusters_failed"]) == 1
        assert "deadline exceeded" in result["clusters_failed"][0]["reason"]
        assert result["partial"] is True

    def test_truncation_reporting(self, monkeypatch):
        """Test that truncation is reported correctly."""
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._is_available",
            lambda _: True,
        )

        def _mock_search_workloads(cluster_name, cluster_conn, name_contains, namespace):
            return {
                "success": True,
                "matches": [
                    {
                        "cluster": cluster_name,
                        "namespace": "default",
                        "kind": "Deployment",
                        "name": "test-deployment",
                    }
                ],
                "unavailable_kinds": [],
                "truncated": True,  # Mark as truncated
            }

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._search_one_cluster_workloads",
            _mock_search_workloads,
        )

        result = self.tool.run(
            name_contains="test",
            cluster_configs={"test-cluster": {"cluster_name": "test-cluster"}},
        )

        assert result["truncated"] is True
        assert "test-cluster" in result["truncated_kinds"]
        assert result["partial"] is True

    def test_unavailable_kinds_collection(self, monkeypatch):
        """Test that unavailable kinds are collected correctly."""
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._is_available",
            lambda _: True,
        )

        def _mock_search_workloads(cluster_name, cluster_conn, name_contains, namespace):
            return {
                "success": True,
                "matches": [],
                "unavailable_kinds": [
                    {"cluster": cluster_name, "kind": "Rollout", "reason": "CRD not installed"}
                ],
                "truncated": False,
            }

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._search_one_cluster_workloads",
            _mock_search_workloads,
        )

        result = self.tool.run(
            name_contains="test",
            cluster_configs={"test-cluster": {"cluster_name": "test-cluster"}},
        )

        assert len(result["unavailable_kinds"]) == 1
        assert result["unavailable_kinds"][0]["kind"] == "Rollout"

    def test_phase2_failure_moves_cluster_to_failed(self, monkeypatch):
        """Test that phase 2 failure moves cluster from searched to failed."""
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._is_available",
            lambda _: True,
        )

        # Mock workload search to succeed and return empty (triggering phase 2)
        def _mock_search_workloads(cluster_name, cluster_conn, name_contains, namespace):
            return {
                "success": True,
                "matches": [],
                "unavailable_kinds": [],
                "truncated": False,
            }

        # Mock pod search to fail
        def _mock_search_pods(cluster_name, cluster_conn, name_contains, namespace):
            return {
                "success": False,
                "error": "pod listing failed",
                "matches": [],
                "unavailable_kinds": [],
                "truncated": False,
            }

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._search_one_cluster_workloads",
            _mock_search_workloads,
        )
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._search_one_cluster_pods",
            _mock_search_pods,
        )

        result = self.tool.run(
            name_contains="test",
            cluster_configs={"test-cluster": {"cluster_name": "test-cluster"}},
        )

        assert result["clusters_searched"] == []
        assert len(result["clusters_failed"]) == 1
        assert result["clusters_failed"][0]["cluster"] == "test-cluster"
        assert "pod listing failed" in result["clusters_failed"][0]["reason"]
        assert result["pods_searched"] is True

    def test_search_covers_every_namespace_not_the_configured_default(self, monkeypatch):
        """Test that search covers all namespaces, not the configured default.

        This pins the D7 avoidance - the most dangerous silent failure.
        """
        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._is_available",
            lambda _: True,
        )

        # Mock search functions to verify they receive empty namespace
        search_workloads_calls = []

        def _mock_search_workloads(cluster_name, cluster_conn, name_contains, namespace):
            search_workloads_calls.append((cluster_name, namespace))
            return {
                "success": True,
                "matches": [],
                "unavailable_kinds": [],
                "truncated": False,
            }

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._search_one_cluster_workloads",
            _mock_search_workloads,
        )

        # Mock search pods since phase 1 returns empty
        def _mock_search_pods(cluster_name, cluster_conn, name_contains, namespace):
            return {
                "success": True,
                "matches": [],
                "unavailable_kinds": [],
                "truncated": False,
            }

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._search_one_cluster_pods",
            _mock_search_pods,
        )

        configs = {
            "cluster-a": {"cluster_name": "cluster-a", "namespace": "default"},
            "cluster-b": {"cluster_name": "cluster-b", "namespace": "production"},
        }

        # Call with empty namespace argument (search all namespaces)
        self.tool.run(
            name_contains="test",
            namespace="",  # Empty - should search all namespaces
            cluster_configs=configs,
        )

        # Verify that search functions were called with empty namespace
        assert len(search_workloads_calls) == 2
        for _cluster_name, namespace_arg in search_workloads_calls:
            assert namespace_arg == ""  # Should be empty, not the cluster's default


class TestSearchWorkloadsFunction:
    """Test the _search_one_cluster_workloads function."""

    def test_workload_search_success(self, monkeypatch):
        """Test successful workload search."""
        mock_client = MagicMock()

        def _mock_resolve_client(cluster_name, configs, default_conn):
            return mock_client, {"cluster_name": cluster_name}, None

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._resolve_client",
            _mock_resolve_client,
        )

        # Mock search_workload_owners
        def _mock_search_workload_owners(client, name_contains, namespace):
            return [
                {
                    "name": "test-deployment",
                    "namespace": "default",
                    "kind": "Deployment",
                    "created_at": "2023-01-01T00:00:00Z",
                }
            ]

        monkeypatch.setattr(
            "integrations.kubernetes.client.search_workload_owners",
            _mock_search_workload_owners,
        )

        result = _search_one_cluster_workloads("test-cluster", {}, "test", "")

        assert result["success"] is True
        assert len(result["matches"]) == 1
        assert result["matches"][0]["name"] == "test-deployment"
        assert result["matches"][0]["cluster"] == "test-cluster"

    def test_workload_search_api_error(self, monkeypatch):
        """Test workload search with API error."""

        def _mock_resolve_client(cluster_name, configs, default_conn):
            return None, None, "connection failed"

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._resolve_client",
            _mock_resolve_client,
        )

        result = _search_one_cluster_workloads("bad-cluster", {}, "test", "")

        assert result["success"] is False
        assert "connection failed" in result["error"]


class TestSearchPodsFunction:
    """Test the _search_one_cluster_pods function."""

    def test_pod_search_success(self, monkeypatch):
        """Test successful pod search."""
        mock_client = MagicMock()

        def _mock_resolve_client(cluster_name, configs, default_conn):
            return mock_client, {"cluster_name": cluster_name}, None

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._resolve_client",
            _mock_resolve_client,
        )

        # Mock search_pods
        def _mock_search_pods(client, name_contains, namespace):
            return [
                {
                    "name": "test-pod",
                    "namespace": "default",
                    "created_at": "2023-01-01T00:00:00Z",
                }
            ]

        monkeypatch.setattr(
            "integrations.kubernetes.client.search_pods",
            _mock_search_pods,
        )

        result = _search_one_cluster_pods("test-cluster", {}, "test", "")

        assert result["success"] is True
        assert len(result["matches"]) == 1
        assert result["matches"][0]["name"] == "test-pod"
        assert result["matches"][0]["cluster"] == "test-cluster"
        assert result["matches"][0]["kind"] == "Pod"

    def test_pod_search_api_error(self, monkeypatch):
        """Test pod search with API error."""

        def _mock_resolve_client(cluster_name, configs, default_conn):
            return None, None, "connection failed"

        monkeypatch.setattr(
            "integrations.kubernetes.tools.fleet_search._resolve_client",
            _mock_resolve_client,
        )

        result = _search_one_cluster_pods("bad-cluster", {}, "test", "")

        assert result["success"] is False
        assert "connection failed" in result["error"]
