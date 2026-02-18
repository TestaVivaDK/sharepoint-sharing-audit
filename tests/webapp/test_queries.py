# tests/webapp/test_queries.py
from unittest.mock import MagicMock
from webapp.queries import get_user_files, get_user_stats, get_last_scan_time


class TestGetUserFiles:
    def test_queries_by_owner_email(self):
        mock_neo4j = MagicMock()
        mock_neo4j.execute.return_value = [
            {
                "risk_level": "HIGH", "source": "OneDrive", "item_path": "/doc.xlsx",
                "item_web_url": "https://x.com/doc", "item_type": "File",
                "drive_id": "d1", "item_id": "i1",
                "sharing_type": "Link-Anyone", "shared_with": "anonymous",
                "shared_with_type": "Anonymous", "role": "Read",
            }
        ]
        result = get_user_files(mock_neo4j, "user@test.com", "run-1")
        assert len(result) == 1
        assert result[0]["item_path"] == "/doc.xlsx"
        # Verify the query includes the user email parameter
        call_args = mock_neo4j.execute.call_args
        assert call_args[1].get("email") == "user@test.com" or call_args[0][1].get("email") == "user@test.com"


class TestGetUserStats:
    def test_returns_counts(self):
        mock_neo4j = MagicMock()
        mock_neo4j.execute.return_value = [
            {"drive_id": "d1", "item_id": "i1", "risk_level": "HIGH", "source": "OneDrive",
             "item_path": "/doc1.xlsx", "item_web_url": "", "item_type": "File",
             "sharing_type": "Link-Anyone", "shared_with": "anonymous",
             "shared_with_type": "Anonymous", "role": "Read"},
            {"drive_id": "d1", "item_id": "i2", "risk_level": "HIGH", "source": "OneDrive",
             "item_path": "/doc2.xlsx", "item_web_url": "", "item_type": "File",
             "sharing_type": "Link-Anyone", "shared_with": "anonymous",
             "shared_with_type": "Anonymous", "role": "Read"},
            {"drive_id": "d1", "item_id": "i3", "risk_level": "MEDIUM", "source": "OneDrive",
             "item_path": "/doc3.txt", "item_web_url": "", "item_type": "File",
             "sharing_type": "Link-Organization", "shared_with": "org",
             "shared_with_type": "Internal", "role": "Read"},
            {"drive_id": "d1", "item_id": "i4", "risk_level": "LOW", "source": "OneDrive",
             "item_path": "/doc4.txt", "item_web_url": "", "item_type": "File",
             "sharing_type": "User", "shared_with": "bob@test.com",
             "shared_with_type": "Internal", "role": "Read"},
            {"drive_id": "d1", "item_id": "i5", "risk_level": "LOW", "source": "OneDrive",
             "item_path": "/doc5.txt", "item_web_url": "", "item_type": "File",
             "sharing_type": "User", "shared_with": "alice@test.com",
             "shared_with_type": "Internal", "role": "Read"},
            {"drive_id": "d1", "item_id": "i6", "risk_level": "LOW", "source": "OneDrive",
             "item_path": "/doc6.txt", "item_web_url": "", "item_type": "File",
             "sharing_type": "User", "shared_with": "charlie@test.com",
             "shared_with_type": "Internal", "role": "Read"},
        ]
        stats = get_user_stats(mock_neo4j, "user@test.com", "run-1")
        assert stats["total"] == 6
        assert stats["high"] == 2
        assert stats["medium"] == 1
        assert stats["low"] == 3


class TestGetLastScanTime:
    def test_returns_timestamp(self):
        mock_neo4j = MagicMock()
        mock_neo4j.execute.return_value = [
            {"runId": "run-1", "timestamp": "2026-02-18T12:00:00Z"}
        ]
        run_id, ts = get_last_scan_time(mock_neo4j)
        assert run_id == "run-1"
        assert ts == "2026-02-18T12:00:00Z"

    def test_no_runs_returns_none(self):
        mock_neo4j = MagicMock()
        mock_neo4j.execute.return_value = []
        result = get_last_scan_time(mock_neo4j)
        assert result == (None, None)
