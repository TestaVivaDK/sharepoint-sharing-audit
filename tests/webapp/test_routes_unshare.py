# tests/webapp/test_routes_unshare.py
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from webapp.app import create_app


class TestUnshareEndpoint:
    @patch("webapp.routes_unshare.bulk_unshare", new_callable=AsyncMock)
    def test_unshare_calls_bulk_unshare(self, mock_bulk):
        mock_bulk.return_value = {
            "succeeded": ["d1:i1", "d2:i2"],
            "failed": [],
        }
        app = create_app()
        client = TestClient(app)
        sid = app.state.sessions.create("user@test.com", "Test User")
        client.cookies.set("session_id", sid)

        resp = client.post("/api/unshare", json={
            "file_ids": ["d1:i1", "d2:i2"],
            "graph_token": "fake-token",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["succeeded"]) == 2
        mock_bulk.assert_called_once_with("fake-token", ["d1:i1", "d2:i2"])

    def test_unshare_requires_auth(self):
        app = create_app()
        client = TestClient(app)
        resp = client.post("/api/unshare", json={
            "file_ids": ["d1:i1"],
            "graph_token": "fake-token",
        })
        assert resp.status_code == 401
