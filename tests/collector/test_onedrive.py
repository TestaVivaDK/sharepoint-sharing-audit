"""Tests for OneDrive collection logic."""

from unittest.mock import MagicMock, call
from collector.onedrive import collect_onedrive_user


def make_mock_graph():
    mock = MagicMock()
    mock.get_user_drive.return_value = {
        "id": "drive-1", "webUrl": "https://x.com/drive"
    }
    mock.get_drive_children.return_value = [
        {"id": "item-1", "name": "doc.xlsx", "webUrl": "https://x.com/doc",
         "file": {"mimeType": "x"}, "folder": None},
    ]
    mock.get_item_permissions.return_value = [
        {"id": "p1", "link": {"scope": "organization"}, "roles": ["read"],
         "inheritedFrom": {}},
    ]
    mock.delay_ms = 0
    mock.throttle = MagicMock()
    return mock


class TestCollectOneDriveUser:
    def test_collects_permissions(self):
        graph = make_mock_graph()
        neo4j = MagicMock()
        user = {"id": "u1", "displayName": "Alice", "userPrincipalName": "a@test.dk"}

        count = collect_onedrive_user(graph, neo4j, user, "run-1", "test.dk")

        assert count == 1
        neo4j.merge_file.assert_called_once()
        neo4j.merge_shared_with.assert_called_once()

    def test_skips_user_without_drive(self):
        graph = MagicMock()
        graph.get_user_drive.return_value = None
        neo4j = MagicMock()
        user = {"id": "u1", "displayName": "Alice", "userPrincipalName": "a@test.dk"}

        count = collect_onedrive_user(graph, neo4j, user, "run-1", "test.dk")

        assert count == 0
        neo4j.merge_file.assert_not_called()
