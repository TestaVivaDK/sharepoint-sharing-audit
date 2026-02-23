"""Tests for Neo4j delta state operations."""

from unittest.mock import MagicMock
from shared.neo4j_client import Neo4jClient


class TestDeltaState:
    def test_save_delta_link(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock()
        client.save_delta_link("drive-1", "https://graph.microsoft.com/delta?token=abc")
        client.execute.assert_called_once()
        query = client.execute.call_args[0][0]
        params = client.execute.call_args[0][1]
        assert "MERGE" in query
        assert "DeltaState" in query
        assert params["driveId"] == "drive-1"
        assert params["deltaLink"] == "https://graph.microsoft.com/delta?token=abc"

    def test_get_delta_link_found(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock(
            return_value=[{"deltaLink": "https://graph.microsoft.com/delta?token=abc"}]
        )
        result = client.get_delta_link("drive-1")
        assert result == "https://graph.microsoft.com/delta?token=abc"

    def test_get_delta_link_not_found(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock(return_value=[])
        result = client.get_delta_link("drive-1")
        assert result is None

    def test_remove_file_permissions(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock()
        client.remove_file_permissions("drive-1", "item-1", "run-1")
        client.execute.assert_called_once()
        query = client.execute.call_args[0][0]
        assert "SHARED_WITH" in query
        assert "DELETE" in query

    def test_get_last_full_scan_time_found(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock(
            return_value=[{"timestamp": "2026-02-20T12:00:00+00:00"}]
        )
        result = client.get_last_full_scan_time()
        assert result == "2026-02-20T12:00:00+00:00"

    def test_get_last_full_scan_time_not_found(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock(return_value=[])
        result = client.get_last_full_scan_time()
        assert result is None

    def test_has_delta_links(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock(return_value=[{"count": 5}])
        assert client.has_delta_links() is True

    def test_has_no_delta_links(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock(return_value=[{"count": 0}])
        assert client.has_delta_links() is False
