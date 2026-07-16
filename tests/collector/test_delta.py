"""Tests for delta scan logic."""

from unittest.mock import MagicMock, patch
import pytest
import httpx
from collector.delta import delta_scan_drive, attempt_delta_scan, seed_delta_link_safe


class TestDeltaScanDrive:
    def test_processes_shared_changed_item(self, mock_user_cache):
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
            mock_user_cache,
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
        neo4j.remove_file_permissions.assert_called_once_with("drive-1", "item-1")
        graph.get_item_permissions.assert_called_once_with("drive-1", "item-1")

    def test_processes_deleted_item(self, mock_user_cache):
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
            mock_user_cache,
            neo4j,
            "drive-1",
            "https://graph.microsoft.com/delta?token=old",
            "site-1",
            "owner@test.dk",
            "test.dk",
            "run-1",
        )

        assert count == 0
        neo4j.remove_file_permissions_and_delete.assert_called_once_with(
            "drive-1", "item-1", "run-1"
        )
        graph.get_item_permissions.assert_not_called()

    def test_skips_content_only_changes(self, mock_user_cache):
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
            mock_user_cache,
            neo4j,
            "drive-1",
            "https://graph.microsoft.com/delta?token=old",
            "site-1",
            "owner@test.dk",
            "test.dk",
            "run-1",
        )

        assert count == 0
        graph.get_item_permissions.assert_called()
        neo4j.remove_file_permissions_and_delete.assert_not_called()
        neo4j.merge_file.assert_called_once()

    def test_returns_new_delta_link(self, mock_user_cache):
        """The function saves the new delta link."""
        graph = MagicMock()
        graph.get_drive_delta.return_value = (
            [],
            "https://graph.microsoft.com/delta?token=new",
        )
        neo4j = MagicMock()

        count = delta_scan_drive(
            graph,
            mock_user_cache,
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

    def test_dispatches_link_permission(self, mock_user_cache):
        """Link permissions are dispatched to process_link_permission."""
        graph = MagicMock()
        graph.get_drive_delta.return_value = (
            [
                {
                    "id": "item-1",
                    "name": "doc.xlsx",
                    "webUrl": "https://x.com/doc",
                    "parentReference": {"path": "/drive/root:/Folder"},
                    "@microsoft.graph.sharedChanged": True,
                },
            ],
            "https://graph.microsoft.com/delta?token=new",
        )
        graph.get_item_permissions.return_value = [
            {
                "id": "p1",
                "link": {"scope": "anonymous"},
                "roles": ["read"],
            },
        ]
        neo4j = MagicMock()

        with patch("collector.delta.process_link_permission") as mock_process_link:
            count = delta_scan_drive(
                graph,
                mock_user_cache,
                neo4j,
                "drive-1",
                "https://graph.microsoft.com/delta?token=old",
                "site-1",
                "owner@test.dk",
                "test.dk",
                "run-1",
            )

            assert count == 1
            neo4j.remove_file_permissions.assert_called_once_with("drive-1", "item-1")
            mock_process_link.assert_called_once()
            # Verify call parameters: (perm, graph, user_cache, neo4j, item_metadata, run_id)
            call_args = mock_process_link.call_args
            assert call_args[0][1] == graph  # graph parameter
            assert call_args[0][2] == mock_user_cache  # user_cache parameter
            assert call_args[0][3] == neo4j  # neo4j parameter
            item_metadata = call_args[0][4]
            assert item_metadata["tenant_domain"] == "test.dk"
            assert item_metadata["item_path"] == "/Folder/doc.xlsx"

    def test_dispatches_group_permission_with_ignore_flag(self, mock_user_cache):
        """Group permissions are dispatched with ignore_sharepoint_groups flag."""
        graph = MagicMock()
        graph.get_drive_delta.return_value = (
            [
                {
                    "id": "item-1",
                    "name": "doc.xlsx",
                    "webUrl": "https://x.com/doc",
                    "parentReference": {"path": "/drive/root:/Folder"},
                    "@microsoft.graph.sharedChanged": True,
                },
            ],
            "https://graph.microsoft.com/delta?token=new",
        )
        graph.get_item_permissions.return_value = [
            {
                "id": "p1",
                "grantedToV2": {"group": {"id": "group-1", "displayName": "Test Group"}},
                "roles": ["read"],
            },
        ]
        neo4j = MagicMock()

        with patch("collector.delta.process_group_permission") as mock_process_group:
            count = delta_scan_drive(
                graph,
                mock_user_cache,
                neo4j,
                "drive-1",
                "https://graph.microsoft.com/delta?token=old",
                "site-1",
                "owner@test.dk",
                "test.dk",
                "run-1",
                ignore_sharepoint_groups=True,
            )

            assert count == 1
            neo4j.remove_file_permissions.assert_called_once_with("drive-1", "item-1")
            mock_process_group.assert_called_once()
            # Verify ignore_sharepoint_groups is passed correctly
            call_args = mock_process_group.call_args
            assert call_args[0][6] is True  # ignore_sharepoint_groups parameter

    def test_dispatches_user_permission(self, mock_user_cache):
        """User permissions are dispatched to process_user_permission."""
        graph = MagicMock()
        graph.get_drive_delta.return_value = (
            [
                {
                    "id": "item-1",
                    "name": "doc.xlsx",
                    "webUrl": "https://x.com/doc",
                    "parentReference": {"path": "/drive/root:/Folder"},
                    "@microsoft.graph.sharedChanged": True,
                },
            ],
            "https://graph.microsoft.com/delta?token=new",
        )
        graph.get_item_permissions.return_value = [
            {
                "id": "p1",
                "grantedToV2": {
                    "user": {"id": "user-1", "email": "user@test.dk"}
                },
                "roles": ["read"],
            },
        ]
        neo4j = MagicMock()

        with patch("collector.delta.process_user_permission") as mock_process_user:
            count = delta_scan_drive(
                graph,
                mock_user_cache,
                neo4j,
                "drive-1",
                "https://graph.microsoft.com/delta?token=old",
                "site-1",
                "owner@test.dk",
                "test.dk",
                "run-1",
            )

            assert count == 1
            neo4j.remove_file_permissions.assert_called_once_with("drive-1", "item-1")
            mock_process_user.assert_called_once()
            # Verify user_cache is passed correctly
            call_args = mock_process_user.call_args
            assert call_args[0][1] == mock_user_cache  # user_cache parameter

    def test_skips_owner_permission(self, mock_user_cache):
        """Owner's own permission is skipped."""
        graph = MagicMock()
        graph.get_drive_delta.return_value = (
            [
                {
                    "id": "item-1",
                    "name": "doc.xlsx",
                    "webUrl": "https://x.com/doc",
                    "parentReference": {"path": "/drive/root:/Folder"},
                    "@microsoft.graph.sharedChanged": True,
                },
            ],
            "https://graph.microsoft.com/delta?token=new",
        )
        graph.get_item_permissions.return_value = [
            {
                "id": "p1",
                "grantedToV2": {
                    "user": {"id": "owner-id", "email": "owner@test.dk"}
                },
                "roles": ["owner"],
            },
        ]
        neo4j = MagicMock()

        with patch("collector.delta.process_user_permission") as mock_process_user:
            count = delta_scan_drive(
                graph,
                mock_user_cache,
                neo4j,
                "drive-1",
                "https://graph.microsoft.com/delta?token=old",
                "site-1",
                "owner@test.dk",
                "test.dk",
                "run-1",
            )

            assert count == 0
            neo4j.remove_file_permissions.assert_called_once_with("drive-1", "item-1")
            mock_process_user.assert_not_called()


class TestAttemptDeltaScan:
    """Tests for attempt_delta_scan helper function."""

    def test_returns_count_and_false_on_success(self, mock_user_cache):
        """On successful delta scan, returns (count, False)."""
        graph = MagicMock()
        neo4j = MagicMock()
        
        with patch("collector.delta.delta_scan_drive") as mock_delta_scan:
            mock_delta_scan.return_value = 5
            
            count, needs_fallback = attempt_delta_scan(
                graph,
                mock_user_cache,
                neo4j,
                "drive-1",
                "https://graph.microsoft.com/delta?token=old",
                "site-1",
                "owner@test.dk",
                "test.dk",
                "run-1",
            )
            
            assert count == 5
            assert needs_fallback is False

    def test_returns_none_and_true_on_410(self, mock_user_cache):
        """On 410 error, returns (None, True)."""
        graph = MagicMock()
        neo4j = MagicMock()
        
        with patch("collector.delta.delta_scan_drive") as mock_delta_scan:
            response = MagicMock()
            response.status_code = 410
            mock_delta_scan.side_effect = httpx.HTTPStatusError(
                "Gone", request=MagicMock(), response=response
            )
            
            count, needs_fallback = attempt_delta_scan(
                graph,
                mock_user_cache,
                neo4j,
                "drive-1",
                "https://graph.microsoft.com/delta?token=old",
                "site-1",
                "owner@test.dk",
                "test.dk",
                "run-1",
            )
            
            assert count is None
            assert needs_fallback is True

    def test_returns_none_and_true_on_404(self, mock_user_cache):
        """On 404 error, returns (None, True)."""
        graph = MagicMock()
        neo4j = MagicMock()
        
        with patch("collector.delta.delta_scan_drive") as mock_delta_scan:
            response = MagicMock()
            response.status_code = 404
            mock_delta_scan.side_effect = httpx.HTTPStatusError(
                "Not Found", request=MagicMock(), response=response
            )
            
            count, needs_fallback = attempt_delta_scan(
                graph,
                mock_user_cache,
                neo4j,
                "drive-1",
                "https://graph.microsoft.com/delta?token=old",
                "site-1",
                "owner@test.dk",
                "test.dk",
                "run-1",
            )
            
            assert count is None
            assert needs_fallback is True

    def test_reraises_other_http_errors(self, mock_user_cache):
        """Other HTTP errors are re-raised."""
        graph = MagicMock()
        neo4j = MagicMock()
        
        with patch("collector.delta.delta_scan_drive") as mock_delta_scan:
            response = MagicMock()
            response.status_code = 500
            mock_delta_scan.side_effect = httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=response
            )
            
            with pytest.raises(httpx.HTTPStatusError):
                attempt_delta_scan(
                    graph,
                    mock_user_cache,
                    neo4j,
                    "drive-1",
                    "https://graph.microsoft.com/delta?token=old",
                    "site-1",
                    "owner@test.dk",
                    "test.dk",
                    "run-1",
                )

    def test_passes_all_parameters_to_delta_scan(self, mock_user_cache):
        """All parameters are correctly passed to delta_scan_drive."""
        graph = MagicMock()
        neo4j = MagicMock()
        
        with patch("collector.delta.delta_scan_drive") as mock_delta_scan:
            mock_delta_scan.return_value = 3
            
            attempt_delta_scan(
                graph,
                mock_user_cache,
                neo4j,
                "drive-1",
                "https://graph.microsoft.com/delta?token=old",
                "site-1",
                "owner@test.dk",
                "test.dk",
                "run-1",
                ignore_sharepoint_groups=True,
            )
            
            mock_delta_scan.assert_called_once_with(
                graph,
                mock_user_cache,
                neo4j,
                "drive-1",
                "https://graph.microsoft.com/delta?token=old",
                "site-1",
                "owner@test.dk",
                "test.dk",
                "run-1",
                True,
                True,
            )


class TestSeedDeltaLinkSafe:
    """Tests for seed_delta_link_safe helper function."""

    def test_seeds_delta_link_on_success(self):
        """On success, seeds and saves delta link."""
        graph = MagicMock()
        neo4j = MagicMock()
        graph.seed_delta_link.return_value = "https://graph.microsoft.com/delta?token=new"
        
        seed_delta_link_safe(graph, neo4j, "drive-1", "test user")
        
        graph.seed_delta_link.assert_called_once_with("drive-1")
        neo4j.save_delta_link.assert_called_once_with(
            "drive-1", "https://graph.microsoft.com/delta?token=new"
        )

    def test_logs_warning_on_seed_failure(self, caplog):
        """On seed failure, logs warning and doesn't raise."""
        graph = MagicMock()
        neo4j = MagicMock()
        graph.seed_delta_link.side_effect = Exception("Network error")
        
        seed_delta_link_safe(graph, neo4j, "drive-1", "test user")
        
        assert "Could not seed delta link for drive drive-1" in caplog.text
        assert "for test user" in caplog.text
        neo4j.save_delta_link.assert_not_called()

    def test_logs_warning_on_save_failure(self, caplog):
        """On save failure, logs warning and doesn't raise."""
        graph = MagicMock()
        neo4j = MagicMock()
        graph.seed_delta_link.return_value = "https://graph.microsoft.com/delta?token=new"
        neo4j.save_delta_link.side_effect = Exception("Database error")
        
        seed_delta_link_safe(graph, neo4j, "drive-1", "test user")
        
        assert "Could not seed delta link for drive drive-1" in caplog.text
        assert "for test user" in caplog.text

    def test_works_without_context_label(self):
        """Works correctly when context_label is not provided."""
        graph = MagicMock()
        neo4j = MagicMock()
        graph.seed_delta_link.return_value = "https://graph.microsoft.com/delta?token=new"
        
        seed_delta_link_safe(graph, neo4j, "drive-1")
        
        graph.seed_delta_link.assert_called_once_with("drive-1")
        neo4j.save_delta_link.assert_called_once()

