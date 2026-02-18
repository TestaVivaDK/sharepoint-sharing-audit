# tests/webapp/test_routes_auth.py
from fastapi.testclient import TestClient
from webapp.app import create_app


def make_client():
    app = create_app()
    return TestClient(app)


class TestAuthMe:
    def test_me_without_session_returns_401(self):
        client = make_client()
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_with_valid_session(self):
        client = make_client()
        # Manually create a session via the app's session store
        app = client.app
        sid = app.state.sessions.create("user@test.com", "Test User")
        resp = client.get("/api/auth/me", cookies={"session_id": sid})
        assert resp.status_code == 200
        assert resp.json()["email"] == "user@test.com"


class TestAuthLogout:
    def test_logout_clears_session(self):
        client = make_client()
        app = client.app
        sid = app.state.sessions.create("user@test.com", "Test User")
        resp = client.post("/api/auth/logout", cookies={"session_id": sid})
        assert resp.status_code == 200
        # Session should be gone
        assert app.state.sessions.get(sid) is None
