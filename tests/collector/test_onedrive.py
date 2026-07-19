"""Tests for OneDrive collection logic."""

from unittest.mock import MagicMock
from collector.onedrive import collect_onedrive_user


def make_mock_graph():
    mock = MagicMock()
    mock.get_user_drive.return_value = {
        "id": "drive-1",
        "webUrl": "https://x.com/drive",
    }
    mock.get_drive_children.return_value = [
        {
            "id": "item-1",
            "name": "doc.xlsx",
            "webUrl": "https://x.com/doc",
            "file": {"mimeType": "x"},
            "folder": None,
            "shared": {"owner": {"displayName": "Owner"}},  # Item must have "shared" attribute to be processed
        },
    ]
    mock.batch_get_item_permissions.return_value = {
        "item-1": {
            "data": {
                "id": "item-1",
                "name": "doc.xlsx",
                "webUrl": "https://x.com/doc",
                "file": {"mimeType": "x"},
            },
            "permissions": [
                {
                    "id": "p1",
                    "link": {"scope": "organization"},
                    "roles": ["read"],
                    "inheritedFrom": {},
                },
            ],
        }
    }
    mock.delay_ms = 0
    mock.throttle = MagicMock()
    return mock


def make_mock_user_cache():
    cache = MagicMock()
    cache.get.return_value = {
        "id": "user-1",
        "email": "user@test.dk",
        "displayName": "Test User",
        "userType": "Member",
        "identities": [],
    }
    return cache


class TestCollectOneDriveUser:
    def test_collects_permissions(self):
        graph = make_mock_graph()
        user_cache = make_mock_user_cache()
        neo4j = MagicMock()
        user = {"id": "123e4567-e89b-12d3-a456-426614174000", "displayName": "Alice", "userPrincipalName": "a@test.dk"}

        count = collect_onedrive_user(graph, user_cache, neo4j, user, "run-1", "test.dk")

        assert count == 1
        neo4j.merge_permission.assert_called_once()

    def test_skips_user_without_drive(self):
        graph = MagicMock()
        graph.get_user_drive.return_value = None
        user_cache = make_mock_user_cache()
        neo4j = MagicMock()
        user = {"id": "123e4567-e89b-12d3-a456-426614174000", "displayName": "Alice", "userPrincipalName": "a@test.dk"}

        count = collect_onedrive_user(graph, user_cache, neo4j, user, "run-1", "test.dk")

        assert count == 0
        neo4j.merge_file.assert_not_called()
