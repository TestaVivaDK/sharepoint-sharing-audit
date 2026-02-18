# tests/webapp/test_auth.py
import time
from webapp.auth import SessionStore, validate_id_token_claims


class TestSessionStore:
    def test_create_and_get_session(self):
        store = SessionStore()
        sid = store.create("user@example.com", "User Name")
        session = store.get(sid)
        assert session is not None
        assert session["email"] == "user@example.com"
        assert session["name"] == "User Name"

    def test_get_nonexistent_returns_none(self):
        store = SessionStore()
        assert store.get("nonexistent") is None

    def test_delete_session(self):
        store = SessionStore()
        sid = store.create("user@example.com", "User Name")
        store.delete(sid)
        assert store.get(sid) is None


class TestValidateIdTokenClaims:
    def test_valid_claims(self):
        claims = {
            "aud": "test-client-id",
            "iss": "https://login.microsoftonline.com/test-tenant-id/v2.0",
            "exp": time.time() + 3600,
            "preferred_username": "user@example.com",
            "name": "Test User",
        }
        result = validate_id_token_claims(claims, "test-client-id", "test-tenant-id")
        assert result == {"email": "user@example.com", "name": "Test User"}

    def test_wrong_audience_raises(self):
        claims = {
            "aud": "wrong-client-id",
            "iss": "https://login.microsoftonline.com/test-tenant-id/v2.0",
            "exp": time.time() + 3600,
            "preferred_username": "user@example.com",
            "name": "Test User",
        }
        try:
            validate_id_token_claims(claims, "test-client-id", "test-tenant-id")
            assert False, "Should have raised"
        except ValueError as e:
            assert "audience" in str(e).lower()

    def test_expired_token_raises(self):
        claims = {
            "aud": "test-client-id",
            "iss": "https://login.microsoftonline.com/test-tenant-id/v2.0",
            "exp": time.time() - 100,
            "preferred_username": "user@example.com",
            "name": "Test User",
        }
        try:
            validate_id_token_claims(claims, "test-client-id", "test-tenant-id")
            assert False, "Should have raised"
        except ValueError as e:
            assert "expired" in str(e).lower()
