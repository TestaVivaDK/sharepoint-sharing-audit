"""Tests for delta scan logic."""

from unittest.mock import MagicMock, call
from collector.delta import delta_scan_drive


class TestDeltaScanDrive:
    def test_processes_shared_changed_item(self):
        """Items with sharedChanged get permissions re-fetched."""
        graph = MagicMock()
        graph.get_drive_delta.return_value = (
            [
                {
                    "id": "item-1",
                    "name": "doc.xlsx",
                    "webUrl": "https://x.com/doc",
                    "parentReference": {"path": "/drive/root:/Folder"},
                    "file": {"mimeType": "x"},
                    "@microsoft.graph.sharedChanged": True,
                },
            ],
            "https://graph.microsoft.com/delta?token=new",
        )
        graph.get_item_permissions.return_value = [
            {
                "id": "p1",
                "link": {"scope": "organization"},
                "roles": ["read"],
                "inheritedFrom": {},
            },
        ]
        neo4j = MagicMock()

        count = delta_scan_drive(
            graph,
            neo4j,
            "drive-1",
            "https://graph.microsoft.com/delta?token=old",
            "site-1",
            "owner@test.dk",
            "test.dk",
            "run-1",
        )

        assert count == 1
        graph.get_item_permissions.assert_called_once_with("drive-1", "item-1")
        neo4j.merge_permission.assert_called_once()

    def test_processes_deleted_item(self):
        """Items with deleted facet get permissions removed."""
        graph = MagicMock()
        graph.get_drive_delta.return_value = (
            [
                {"id": "item-1", "deleted": {"state": "deleted"}},
            ],
            "https://graph.microsoft.com/delta?token=new",
        )
        neo4j = MagicMock()

        count = delta_scan_drive(
            graph,
            neo4j,
            "drive-1",
            "https://graph.microsoft.com/delta?token=old",
            "site-1",
            "owner@test.dk",
            "test.dk",
            "run-1",
        )

        assert count == 0
        neo4j.remove_file_permissions.assert_called_once_with(
            "drive-1", "item-1", "run-1"
        )
        graph.get_item_permissions.assert_not_called()

    def test_skips_content_only_changes(self):
        """Items without sharedChanged or deleted skip permission fetch."""
        graph = MagicMock()
        graph.get_drive_delta.return_value = (
            [
                {
                    "id": "item-1",
                    "name": "renamed.docx",
                    "webUrl": "https://x.com/doc",
                    "parentReference": {"path": "/drive/root:/Folder"},
                    "file": {"mimeType": "x"},
                },
            ],
            "https://graph.microsoft.com/delta?token=new",
        )
        neo4j = MagicMock()

        count = delta_scan_drive(
            graph,
            neo4j,
            "drive-1",
            "https://graph.microsoft.com/delta?token=old",
            "site-1",
            "owner@test.dk",
            "test.dk",
            "run-1",
        )

        assert count == 0
        graph.get_item_permissions.assert_not_called()
        neo4j.merge_permission.assert_not_called()
        neo4j.merge_file.assert_called_once()

    def test_returns_new_delta_link(self):
        """The function saves the new delta link."""
        graph = MagicMock()
        graph.get_drive_delta.return_value = (
            [],
            "https://graph.microsoft.com/delta?token=new",
        )
        neo4j = MagicMock()

        count = delta_scan_drive(
            graph,
            neo4j,
            "drive-1",
            "https://graph.microsoft.com/delta?token=old",
            "site-1",
            "owner@test.dk",
            "test.dk",
            "run-1",
        )

        assert count == 0
        neo4j.save_delta_link.assert_called_once_with(
            "drive-1", "https://graph.microsoft.com/delta?token=new"
        )
