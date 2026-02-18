# tests/webapp/test_routes_files.py
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from webapp.app import create_app


def make_authed_client():
    """Create a test client with a valid session."""
    app = create_app()
    client = TestClient(app)
    sid = app.state.sessions.create("user@test.com", "Test User")
    client.cookies.set("session_id", sid)
    return client, app


class TestFilesEndpoint:
    @patch("webapp.routes_files.get_neo4j")
    def test_returns_files_for_authenticated_user(self, mock_get_neo4j):
        mock_neo4j = MagicMock()
        mock_get_neo4j.return_value = mock_neo4j
        mock_neo4j.execute.side_effect = [
            # get_last_scan_time
            [{"runId": "run-1", "timestamp": "2026-02-18T12:00:00Z"}],
            # get_user_files
            [
                {
                    "drive_id": "d1", "item_id": "i1",
                    "risk_level": "HIGH", "source": "OneDrive",
                    "item_path": "/doc.xlsx", "item_web_url": "https://x.com/doc",
                    "item_type": "File", "sharing_type": "Link-Anyone",
                    "shared_with": "anonymous", "shared_with_type": "Anonymous",
                    "role": "Read",
                }
            ],
        ]
        client, app = make_authed_client()
        resp = client.get("/api/files")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 1
        assert data["files"][0]["item_path"] == "/doc.xlsx"

    def test_returns_401_without_session(self):
        app = create_app()
        client = TestClient(app)
        resp = client.get("/api/files")
        assert resp.status_code == 401


class TestStatsEndpoint:
    @patch("webapp.routes_files.get_neo4j")
    def test_returns_stats(self, mock_get_neo4j):
        mock_neo4j = MagicMock()
        mock_get_neo4j.return_value = mock_neo4j
        mock_neo4j.execute.side_effect = [
            # get_last_scan_time
            [{"runId": "run-1", "timestamp": "2026-02-18T12:00:00Z"}],
            # get_user_files (called by get_user_stats)
            [
                {"drive_id": "d1", "item_id": "i1", "risk_level": "HIGH",
                 "source": "OneDrive", "item_path": "/doc.xlsx",
                 "item_web_url": "", "item_type": "File",
                 "sharing_type": "Link-Anyone", "shared_with": "anonymous",
                 "shared_with_type": "Anonymous", "role": "Read"},
            ],
        ]
        client, app = make_authed_client()
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["high"] == 1
