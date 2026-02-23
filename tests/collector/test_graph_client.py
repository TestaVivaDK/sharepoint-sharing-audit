"""Tests for Graph API client with mocked HTTP responses."""

from unittest.mock import MagicMock
from collector.graph_client import GraphClient


class TestGraphClient:
    def test_get_users(self):
        """Test user enumeration filters licensed enabled users."""
        mock_users = [
            {
                "id": "1",
                "displayName": "Alice",
                "userPrincipalName": "a@test.dk",
                "accountEnabled": True,
                "assignedLicenses": [{"skuId": "x"}],
            },
            {
                "id": "2",
                "displayName": "Disabled",
                "userPrincipalName": "d@test.dk",
                "accountEnabled": False,
                "assignedLicenses": [{"skuId": "x"}],
            },
            {
                "id": "3",
                "displayName": "Unlicensed",
                "userPrincipalName": "u@test.dk",
                "accountEnabled": True,
                "assignedLicenses": [],
            },
        ]
        client = GraphClient.__new__(GraphClient)
        client._client = MagicMock()
        client._make_request = MagicMock(return_value={"value": mock_users})
        client.delay_ms = 0

        users = client.get_users()
        assert len(users) == 1
        assert users[0]["userPrincipalName"] == "a@test.dk"

    def test_get_drive_items_returns_children(self):
        """Test drive item listing."""
        mock_children = {
            "value": [
                {
                    "id": "item-1",
                    "name": "doc.xlsx",
                    "webUrl": "https://x.com/doc",
                    "file": {"mimeType": "application/xlsx"},
                },
                {
                    "id": "item-2",
                    "name": "Folder",
                    "webUrl": "https://x.com/folder",
                    "folder": {"childCount": 0},
                },
            ]
        }
        client = GraphClient.__new__(GraphClient)
        client._make_request = MagicMock(return_value=mock_children)
        client.delay_ms = 0

        items = client.get_drive_children("drive-1", "root")
        assert len(items) == 2
        assert items[0]["name"] == "doc.xlsx"

    def test_get_item_permissions_filters_inherited(self):
        """Test that inherited permissions are filtered out."""
        mock_perms = {
            "value": [
                {"id": "p1", "link": {"scope": "anonymous"}, "inheritedFrom": {}},
                {
                    "id": "p2",
                    "link": {"scope": "organization"},
                    "inheritedFrom": {"driveId": "d1", "path": "/root"},
                },
            ]
        }
        client = GraphClient.__new__(GraphClient)
        client._make_request = MagicMock(return_value=mock_perms)

        perms = client.get_item_permissions("drive-1", "item-1")
        assert len(perms) == 1
        assert perms[0]["id"] == "p1"


class TestDeltaMethods:
    def test_seed_delta_link(self):
        """seed_delta_link calls delta?token=latest and returns deltaLink."""
        client = GraphClient.__new__(GraphClient)
        client._make_request = MagicMock(
            return_value={
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/drives/d1/root/delta?token=xyz",
                "value": [],
            }
        )
        client.delay_ms = 0

        link = client.seed_delta_link("d1")

        assert link == "https://graph.microsoft.com/v1.0/drives/d1/root/delta?token=xyz"
        url = client._make_request.call_args[0][0]
        assert "delta" in url
        params = client._make_request.call_args[0][1]
        assert params["token"] == "latest"

    def test_get_drive_delta_single_page(self):
        """get_drive_delta returns items and new delta link."""
        client = GraphClient.__new__(GraphClient)
        client.delay_ms = 0
        client._make_request = MagicMock(
            return_value={
                "value": [
                    {
                        "id": "item-1",
                        "name": "changed.docx",
                        "@microsoft.graph.sharedChanged": True,
                    },
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/delta?token=new",
            }
        )

        items, new_link = client.get_drive_delta(
            "https://graph.microsoft.com/delta?token=old"
        )

        assert len(items) == 1
        assert items[0]["id"] == "item-1"
        assert new_link == "https://graph.microsoft.com/delta?token=new"

    def test_get_drive_delta_paginates(self):
        """get_drive_delta follows nextLink then returns deltaLink."""
        client = GraphClient.__new__(GraphClient)
        client.delay_ms = 0
        client._make_request = MagicMock(
            side_effect=[
                {
                    "value": [{"id": "item-1", "name": "a.txt"}],
                    "@odata.nextLink": "https://graph.microsoft.com/delta?page=2",
                },
                {
                    "value": [{"id": "item-2", "name": "b.txt"}],
                    "@odata.deltaLink": "https://graph.microsoft.com/delta?token=final",
                },
            ]
        )

        items, new_link = client.get_drive_delta(
            "https://graph.microsoft.com/delta?token=old"
        )

        assert len(items) == 2
        assert new_link == "https://graph.microsoft.com/delta?token=final"
        assert client._make_request.call_count == 2
