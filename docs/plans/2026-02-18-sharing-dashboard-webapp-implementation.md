# Sharing Dashboard Web App — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a self-service web app where tenant users log in with Microsoft Entra, view their shared files from Neo4j, and bulk-unshare via Graph API.

**Architecture:** Monorepo with FastAPI backend (`src/webapp/`) serving a React SPA (`frontend/`). Dual-token auth: MSAL.js for user login + delegated Graph API calls, app-only credentials for Neo4j queries. Single Docker container via multi-stage build.

**Tech Stack:** Python 3.11, FastAPI, uvicorn, python-jose, httpx, Neo4j driver | React 18, TypeScript, Vite, MUI DataGrid Pro, TanStack Query, MSAL React

**Design doc:** `docs/plans/2026-02-18-sharing-dashboard-webapp-design.md`

---

## Task 1: Backend Scaffold — FastAPI App + Config + Dependencies

**Files:**
- Create: `src/webapp/__init__.py`
- Create: `src/webapp/__main__.py`
- Create: `src/webapp/app.py`
- Modify: `src/shared/config.py`
- Modify: `pyproject.toml`
- Test: `tests/webapp/test_app.py`

**Step 1: Add webapp dependencies to pyproject.toml**

Add to the `dependencies` list in `pyproject.toml`:

```toml
"fastapi>=0.115,<1.0",
"uvicorn[standard]>=0.34,<1.0",
"python-jose[cryptography]>=3.3,<4.0",
"httpx>=0.28,<1.0",
```

**Step 2: Add WebappConfig to shared/config.py**

Add after `ReporterConfig`:

```python
@dataclass(frozen=True)
class WebappConfig:
    graph_api: GraphApiConfig = field(default_factory=GraphApiConfig)
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    tenant_domain: str = field(default_factory=lambda: os.environ.get("TENANT_DOMAIN", ""))
    session_secret: str = field(default_factory=lambda: os.environ.get("SESSION_SECRET", "dev-secret-change-me"))
```

**Step 3: Write the failing test**

```python
# tests/webapp/__init__.py (empty)

# tests/webapp/test_app.py
from fastapi.testclient import TestClient
from webapp.app import create_app


def test_health_endpoint():
    app = create_app()
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

**Step 4: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/webapp/test_app.py::test_health_endpoint -v`
Expected: FAIL (cannot import webapp.app)

**Step 5: Create the FastAPI app**

```python
# src/webapp/__init__.py (empty)

# src/webapp/app.py
"""FastAPI application factory."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Sharing Audit Dashboard", docs_url="/api/docs")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app
```

```python
# src/webapp/__main__.py
"""Webapp entry point: python -m webapp"""

import uvicorn
from webapp.app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run("webapp.__main__:app", host="0.0.0.0", port=8000, reload=True)
```

**Step 6: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/webapp/test_app.py::test_health_endpoint -v`
Expected: PASS

**Step 7: Install dependencies and verify app starts**

Run: `pip install -e . && PYTHONPATH=src python -c "from webapp.app import create_app; print('OK')"`

**Step 8: Commit**

```bash
git add src/webapp/ tests/webapp/ pyproject.toml src/shared/config.py
git commit -m "feat(webapp): FastAPI app scaffold with health endpoint and config"
```

---

## Task 2: Auth Module — JWT Validation + Session Management

**Files:**
- Create: `src/webapp/auth.py`
- Test: `tests/webapp/test_auth.py`

**Step 1: Write the failing tests**

```python
# tests/webapp/test_auth.py
import time
from unittest.mock import patch, MagicMock
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
```

**Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/webapp/test_auth.py -v`
Expected: FAIL (cannot import webapp.auth)

**Step 3: Implement auth module**

```python
# src/webapp/auth.py
"""Microsoft Entra ID token validation and session management."""

import time
import uuid
from typing import Optional

import httpx
from jose import jwt


class SessionStore:
    """In-memory session store. Maps session IDs to user info."""

    def __init__(self):
        self._sessions: dict[str, dict] = {}

    def create(self, email: str, name: str) -> str:
        sid = str(uuid.uuid4())
        self._sessions[sid] = {"email": email, "name": name}
        return sid

    def get(self, sid: str) -> Optional[dict]:
        return self._sessions.get(sid)

    def delete(self, sid: str):
        self._sessions.pop(sid, None)


def validate_id_token_claims(claims: dict, client_id: str, tenant_id: str) -> dict:
    """Validate decoded ID token claims. Returns user info dict or raises ValueError."""
    if claims.get("aud") != client_id:
        raise ValueError(f"Invalid audience: {claims.get('aud')}")

    expected_issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
    if claims.get("iss") != expected_issuer:
        raise ValueError(f"Invalid issuer: {claims.get('iss')}")

    if claims.get("exp", 0) < time.time():
        raise ValueError("Token expired")

    email = claims.get("preferred_username", "")
    name = claims.get("name", "")
    if not email:
        raise ValueError("No preferred_username in token")

    return {"email": email, "name": name}


_jwks_cache: dict = {}


async def get_entra_jwks(tenant_id: str) -> dict:
    """Fetch Microsoft Entra JWKS (cached)."""
    if tenant_id in _jwks_cache:
        return _jwks_cache[tenant_id]
    url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        _jwks_cache[tenant_id] = resp.json()
        return _jwks_cache[tenant_id]


async def decode_id_token(token: str, client_id: str, tenant_id: str) -> dict:
    """Decode and validate a Microsoft Entra ID token. Returns user info."""
    jwks = await get_entra_jwks(tenant_id)
    header = jwt.get_unverified_header(token)
    key = next((k for k in jwks["keys"] if k["kid"] == header["kid"]), None)
    if not key:
        raise ValueError("Token signing key not found in JWKS")

    claims = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
    )
    return validate_id_token_claims(claims, client_id, tenant_id)
```

**Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/webapp/test_auth.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/webapp/auth.py tests/webapp/test_auth.py
git commit -m "feat(webapp): auth module with JWT validation and session store"
```

---

## Task 3: Auth Routes — Login, Logout, Me

**Files:**
- Create: `src/webapp/routes_auth.py`
- Modify: `src/webapp/app.py`
- Test: `tests/webapp/test_routes_auth.py`

**Step 1: Write the failing tests**

```python
# tests/webapp/test_routes_auth.py
from unittest.mock import patch, AsyncMock
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
```

**Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/webapp/test_routes_auth.py -v`
Expected: FAIL

**Step 3: Implement auth routes and wire into app**

```python
# src/webapp/routes_auth.py
"""Auth API routes: login, logout, me."""

import logging

from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel

from webapp.auth import decode_id_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    id_token: str


def _get_session(request: Request) -> dict:
    """Get current session or raise 401."""
    sid = request.cookies.get("session_id")
    if not sid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session = request.app.state.sessions.get(sid)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    return session


@router.get("/me")
def me(request: Request):
    session = _get_session(request)
    return {"email": session["email"], "name": session["name"]}


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    config = request.app.state.config
    try:
        user_info = await decode_id_token(
            body.id_token, config.graph_api.client_id, config.graph_api.tenant_id
        )
    except Exception as e:
        logger.warning(f"Login failed: {e}")
        raise HTTPException(status_code=401, detail=str(e))

    sid = request.app.state.sessions.create(user_info["email"], user_info["name"])
    response.set_cookie(
        key="session_id",
        value=sid,
        httponly=True,
        samesite="lax",
        max_age=8 * 3600,
    )
    return {"email": user_info["email"], "name": user_info["name"]}


@router.post("/logout")
def logout(request: Request, response: Response):
    sid = request.cookies.get("session_id")
    if sid:
        request.app.state.sessions.delete(sid)
    response.delete_cookie("session_id")
    return {"status": "ok"}
```

Update `src/webapp/app.py`:

```python
"""FastAPI application factory."""

from fastapi import FastAPI
from shared.config import WebappConfig
from webapp.auth import SessionStore
from webapp.routes_auth import router as auth_router


def create_app() -> FastAPI:
    app = FastAPI(title="Sharing Audit Dashboard", docs_url="/api/docs")

    config = WebappConfig()
    app.state.config = config
    app.state.sessions = SessionStore()

    app.include_router(auth_router)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app
```

**Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/webapp/test_routes_auth.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/webapp/routes_auth.py src/webapp/app.py tests/webapp/test_routes_auth.py
git commit -m "feat(webapp): auth routes — login, logout, me with session cookies"
```

---

## Task 4: Neo4j Queries for Web App — User-Specific Files + Stats

**Files:**
- Create: `src/webapp/queries.py`
- Test: `tests/webapp/test_queries.py`

**Step 1: Write the failing tests**

```python
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
            {"risk_level": "HIGH"}, {"risk_level": "HIGH"},
            {"risk_level": "MEDIUM"},
            {"risk_level": "LOW"}, {"risk_level": "LOW"}, {"risk_level": "LOW"},
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
```

**Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/webapp/test_queries.py -v`
Expected: FAIL

**Step 3: Implement webapp queries**

```python
# src/webapp/queries.py
"""Neo4j queries for the web app — user-specific data."""

from shared.neo4j_client import Neo4jClient
from shared.classify import compute_risk_score, get_risk_level, is_teams_chat_file


def get_last_scan_time(client: Neo4jClient) -> tuple[str | None, str | None]:
    """Get the latest completed scan run ID and timestamp."""
    result = client.execute("""
        MATCH (r:ScanRun {status: 'completed'})
        RETURN r.runId AS runId, r.timestamp AS timestamp
        ORDER BY r.timestamp DESC
        LIMIT 1
    """)
    if not result:
        return None, None
    return result[0]["runId"], result[0]["timestamp"]


def get_user_files(client: Neo4jClient, email: str, run_id: str) -> list[dict]:
    """Get shared files owned by a specific user, with sharing details."""
    result = client.execute("""
        MATCH (owner:User {email: $email})-[:OWNS]->(site:Site)-[:CONTAINS]->(f:File)
        MATCH (f)-[s:SHARED_WITH {lastSeenRunId: $runId}]->(shared_user:User)
        RETURN
            f.driveId AS drive_id,
            f.itemId AS item_id,
            s.riskLevel AS risk_level,
            site.source AS source,
            f.path AS item_path,
            f.webUrl AS item_web_url,
            f.type AS item_type,
            s.sharingType AS sharing_type,
            shared_user.email AS shared_with,
            s.sharedWithType AS shared_with_type,
            s.role AS role
        ORDER BY
            CASE s.riskLevel WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
            f.path
    """, {"email": email, "runId": run_id})
    return result


def deduplicate_user_files(records: list[dict]) -> list[dict]:
    """Group records by file, consolidate sharing details, compute risk score.
    Same logic as reporter/__main__.py but returns drive_id/item_id for unshare."""
    RISK_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    groups: dict[str, dict] = {}

    for r in records:
        key = f"{r.get('drive_id')}:{r.get('item_id')}"
        if key not in groups:
            groups[key] = {
                "drive_id": r.get("drive_id", ""),
                "item_id": r.get("item_id", ""),
                "risk_level": r.get("risk_level", "LOW"),
                "source": r.get("source", ""),
                "item_path": r.get("item_path", ""),
                "item_web_url": r.get("item_web_url", ""),
                "item_type": r.get("item_type", "File"),
                "sharing_types": [],
                "shared_with_list": [],
                "shared_with_types": [],
                "roles": [],
            }
        g = groups[key]
        if RISK_ORDER.get(r.get("risk_level", "LOW"), 2) < RISK_ORDER.get(g["risk_level"], 2):
            g["risk_level"] = r["risk_level"]
        for field, key_name in [("sharing_type", "sharing_types"), ("shared_with", "shared_with_list"),
                                ("shared_with_type", "shared_with_types"), ("role", "roles")]:
            val = r.get(field, "")
            if val and val not in g[key_name]:
                g[key_name].append(val)

    result = []
    for g in groups.values():
        swt_priority = {"Anonymous": 0, "External": 1, "Guest": 2, "Internal": 3, "Unknown": 4}
        worst_swt = min(g["shared_with_types"], key=lambda t: swt_priority.get(t, 5)) if g["shared_with_types"] else "Unknown"
        worst_role = "Write" if "Write" in g["roles"] or "Owner" in g["roles"] else ("Read" if "Read" in g["roles"] else "Unknown")

        # Tag Teams source
        source = g["source"]
        if is_teams_chat_file(g["item_path"]) and source == "OneDrive":
            source = "Teams"

        risk_level = get_risk_level(
            sharing_type=g["sharing_types"][0] if g["sharing_types"] else "",
            shared_with_type=worst_swt,
            item_path=g["item_path"],
        )
        risk_score = compute_risk_score(
            shared_with_type=worst_swt,
            sharing_type=g["sharing_types"][0] if g["sharing_types"] else "",
            item_path=g["item_path"],
            role=worst_role,
            item_type=g["item_type"],
            recipient_count=len(g["shared_with_list"]),
        )

        result.append({
            "id": f"{g['drive_id']}:{g['item_id']}",
            "drive_id": g["drive_id"],
            "item_id": g["item_id"],
            "risk_score": risk_score,
            "risk_level": risk_level,
            "source": source,
            "item_type": g["item_type"],
            "item_path": g["item_path"],
            "item_web_url": g["item_web_url"],
            "sharing_type": ", ".join(g["sharing_types"]),
            "shared_with": ", ".join(g["shared_with_list"]),
            "shared_with_type": ", ".join(g["shared_with_types"]),
        })

    result.sort(key=lambda r: -r["risk_score"])
    return result


def get_user_stats(client: Neo4jClient, email: str, run_id: str) -> dict:
    """Get summary counts for a user's shared files."""
    records = get_user_files(client, email, run_id)
    deduped = deduplicate_user_files(records)
    return {
        "total": len(deduped),
        "high": sum(1 for r in deduped if r["risk_level"] == "HIGH"),
        "medium": sum(1 for r in deduped if r["risk_level"] == "MEDIUM"),
        "low": sum(1 for r in deduped if r["risk_level"] == "LOW"),
    }
```

**Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/webapp/test_queries.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/webapp/queries.py tests/webapp/test_queries.py
git commit -m "feat(webapp): Neo4j queries for user-specific files and stats"
```

---

## Task 5: Files & Stats Routes

**Files:**
- Create: `src/webapp/routes_files.py`
- Modify: `src/webapp/app.py`
- Test: `tests/webapp/test_routes_files.py`

**Step 1: Write the failing tests**

```python
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
        mock_neo4j.execute.return_value = [
            {
                "drive_id": "d1", "item_id": "i1",
                "risk_level": "HIGH", "source": "OneDrive",
                "item_path": "/doc.xlsx", "item_web_url": "https://x.com/doc",
                "item_type": "File", "sharing_type": "Link-Anyone",
                "shared_with": "anonymous", "shared_with_type": "Anonymous",
                "role": "Read",
            }
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
```

**Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/webapp/test_routes_files.py -v`
Expected: FAIL

**Step 3: Implement files routes**

```python
# src/webapp/routes_files.py
"""File listing and stats API routes."""

from fastapi import APIRouter, Request, HTTPException, Query
from shared.neo4j_client import Neo4jClient
from webapp.queries import get_user_files, get_user_stats, get_last_scan_time, deduplicate_user_files

router = APIRouter(prefix="/api", tags=["files"])

_neo4j: Neo4jClient | None = None


def get_neo4j() -> Neo4jClient:
    """Get the Neo4j client singleton. Set by app startup."""
    if _neo4j is None:
        raise RuntimeError("Neo4j not initialized")
    return _neo4j


def _get_session(request: Request) -> dict:
    sid = request.cookies.get("session_id")
    if not sid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session = request.app.state.sessions.get(sid)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    return session


@router.get("/files")
def list_files(
    request: Request,
    risk_level: str | None = Query(None, description="Comma-separated: HIGH,MEDIUM,LOW"),
    source: str | None = Query(None, description="Comma-separated: OneDrive,SharePoint,Teams"),
    search: str | None = Query(None, description="Search in file path"),
):
    session = _get_session(request)
    neo4j = get_neo4j()
    run_id, last_scan = get_last_scan_time(neo4j)
    if not run_id:
        return {"files": [], "last_scan": None}

    raw = get_user_files(neo4j, session["email"], run_id)
    files = deduplicate_user_files(raw)

    # Apply filters
    if risk_level:
        levels = {r.strip().upper() for r in risk_level.split(",")}
        files = [f for f in files if f["risk_level"] in levels]
    if source:
        sources = {s.strip() for s in source.split(",")}
        files = [f for f in files if f["source"] in sources]
    if search:
        q = search.lower()
        files = [f for f in files if q in f["item_path"].lower()]

    return {"files": files, "last_scan": last_scan}


@router.get("/stats")
def stats(request: Request):
    session = _get_session(request)
    neo4j = get_neo4j()
    run_id, last_scan = get_last_scan_time(neo4j)
    if not run_id:
        return {"total": 0, "high": 0, "medium": 0, "low": 0, "last_scan": None}

    counts = get_user_stats(neo4j, session["email"], run_id)
    counts["last_scan"] = last_scan
    return counts
```

Update `src/webapp/app.py` to include file routes and Neo4j init:

```python
"""FastAPI application factory."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from shared.config import WebappConfig
from shared.neo4j_client import Neo4jClient
from webapp.auth import SessionStore
from webapp.routes_auth import router as auth_router
from webapp import routes_files


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = app.state.config
    neo4j = Neo4jClient(config.neo4j.uri, config.neo4j.user, config.neo4j.password)
    routes_files._neo4j = neo4j
    yield
    neo4j.close()
    routes_files._neo4j = None


def create_app() -> FastAPI:
    config = WebappConfig()

    app = FastAPI(title="Sharing Audit Dashboard", docs_url="/api/docs", lifespan=lifespan)
    app.state.config = config
    app.state.sessions = SessionStore()

    app.include_router(auth_router)
    app.include_router(routes_files.router)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app
```

**Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/webapp/test_routes_files.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/webapp/routes_files.py src/webapp/app.py tests/webapp/test_routes_files.py
git commit -m "feat(webapp): files and stats API routes with filtering"
```

---

## Task 6: Graph API Unshare Module — Delegated Permission Deletion

**Files:**
- Create: `src/webapp/graph_unshare.py`
- Test: `tests/webapp/test_graph_unshare.py`

**Step 1: Write the failing tests**

```python
# tests/webapp/test_graph_unshare.py
import httpx
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from webapp.graph_unshare import remove_all_permissions


class TestRemoveAllPermissions:
    @pytest.mark.asyncio
    async def test_deletes_non_inherited_permissions(self):
        """Should fetch permissions, filter inherited, DELETE each remaining one."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)

        # GET permissions response
        perms_response = MagicMock()
        perms_response.status_code = 200
        perms_response.json.return_value = {
            "value": [
                {"id": "perm-1", "roles": ["read"]},
                {"id": "perm-2", "roles": ["write"], "inheritedFrom": {"driveId": "d0"}},
                {"id": "perm-3", "roles": ["read"], "link": {"scope": "anonymous"}},
            ]
        }

        # DELETE responses
        delete_response = MagicMock()
        delete_response.status_code = 204

        mock_client.get.return_value = perms_response
        mock_client.delete.return_value = delete_response

        result = await remove_all_permissions(mock_client, "d1", "i1")
        assert result["succeeded"] == ["perm-1", "perm-3"]
        assert result["failed"] == []
        # perm-2 is inherited, should NOT be deleted
        assert mock_client.delete.call_count == 2
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/webapp/test_graph_unshare.py -v`
Expected: FAIL

**Step 3: Implement graph unshare module**

```python
# src/webapp/graph_unshare.py
"""Delegated Graph API calls for removing sharing permissions."""

import logging
import httpx

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


async def remove_all_permissions(
    client: httpx.AsyncClient,
    drive_id: str,
    item_id: str,
) -> dict:
    """Remove all non-inherited permissions from a drive item.
    Returns {succeeded: [perm_ids], failed: [{id, error}]}."""
    url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/permissions"
    resp = await client.get(url)
    resp.raise_for_status()
    permissions = resp.json().get("value", [])

    # Filter out inherited permissions
    direct = [
        p for p in permissions
        if not (p.get("inheritedFrom", {}).get("driveId") or p.get("inheritedFrom", {}).get("path"))
    ]

    # Also skip "owner" role — can't remove the owner
    removable = [
        p for p in direct
        if "owner" not in p.get("roles", [])
    ]

    succeeded = []
    failed = []

    for perm in removable:
        perm_id = perm["id"]
        try:
            del_resp = await client.delete(f"{url}/{perm_id}")
            if del_resp.status_code in (204, 200):
                succeeded.append(perm_id)
            else:
                failed.append({"id": perm_id, "error": f"HTTP {del_resp.status_code}"})
        except Exception as e:
            failed.append({"id": perm_id, "error": str(e)})

    return {"succeeded": succeeded, "failed": failed}


async def bulk_unshare(
    graph_token: str,
    file_ids: list[str],
) -> dict:
    """Remove all sharing from multiple files. file_ids are 'driveId:itemId' strings.
    Returns {succeeded: [file_ids], failed: [{id, error}]}."""
    succeeded = []
    failed = []

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {graph_token}"},
        timeout=30.0,
    ) as client:
        for file_id in file_ids:
            try:
                drive_id, item_id = file_id.split(":", 1)
                result = await remove_all_permissions(client, drive_id, item_id)
                if result["failed"]:
                    failed.append({"id": file_id, "error": f"{len(result['failed'])} permissions failed"})
                else:
                    succeeded.append(file_id)
                    logger.info(f"Unshared {file_id}: {len(result['succeeded'])} permissions removed")
            except Exception as e:
                failed.append({"id": file_id, "error": str(e)})
                logger.warning(f"Unshare failed for {file_id}: {e}")

    return {"succeeded": succeeded, "failed": failed}
```

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/webapp/test_graph_unshare.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/webapp/graph_unshare.py tests/webapp/test_graph_unshare.py
git commit -m "feat(webapp): Graph API unshare module — bulk permission deletion"
```

---

## Task 7: Unshare Route

**Files:**
- Create: `src/webapp/routes_unshare.py`
- Modify: `src/webapp/app.py`
- Test: `tests/webapp/test_routes_unshare.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/webapp/test_routes_unshare.py -v`
Expected: FAIL

**Step 3: Implement unshare route**

```python
# src/webapp/routes_unshare.py
"""Unshare API route — bulk permission removal via Graph API."""

import logging
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from webapp.graph_unshare import bulk_unshare

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["unshare"])


class UnshareRequest(BaseModel):
    file_ids: list[str]
    graph_token: str


@router.post("/unshare")
async def unshare(body: UnshareRequest, request: Request):
    sid = request.cookies.get("session_id")
    if not sid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session = request.app.state.sessions.get(sid)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    if not body.file_ids:
        raise HTTPException(status_code=400, detail="No files specified")

    logger.info(f"Unshare request from {session['email']}: {len(body.file_ids)} files")
    result = await bulk_unshare(body.graph_token, body.file_ids)
    logger.info(f"Unshare result: {len(result['succeeded'])} succeeded, {len(result['failed'])} failed")

    return result
```

Update `src/webapp/app.py` to include unshare router:

Add import: `from webapp.routes_unshare import router as unshare_router`
Add line: `app.include_router(unshare_router)`

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/webapp/test_routes_unshare.py -v`
Expected: PASS

**Step 5: Run all webapp tests**

Run: `PYTHONPATH=src pytest tests/webapp/ -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/webapp/routes_unshare.py src/webapp/app.py tests/webapp/test_routes_unshare.py
git commit -m "feat(webapp): unshare route — bulk permission removal endpoint"
```

---

## Task 8: Frontend Scaffold — Vite + React + TypeScript + Dependencies

**Files:**
- Create: `frontend/` directory with Vite React TypeScript project
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/package.json`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/index.html`

**Step 1: Scaffold Vite project**

```bash
cd /home/mlu/Documents/project/sharepoint-dashboard
npm create vite@latest frontend -- --template react-ts
```

**Step 2: Install dependencies**

```bash
cd frontend
npm install @azure/msal-browser @azure/msal-react
npm install @mui/material @mui/x-data-grid-pro @emotion/react @emotion/styled
npm install @tanstack/react-query
npm install --save-dev @types/react @types/react-dom
```

**Step 3: Configure Vite proxy for development**

Update `frontend/vite.config.ts`:

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
})
```

**Step 4: Create minimal App component**

```tsx
// frontend/src/App.tsx
function App() {
  return (
    <div>
      <h1>Sharing Audit Dashboard</h1>
      <p>Loading...</p>
    </div>
  )
}

export default App
```

**Step 5: Verify it builds**

```bash
cd frontend && npm run build
```
Expected: Build succeeds, output in `frontend/dist/`

**Step 6: Add frontend to .gitignore**

Add to `.gitignore`:
```
frontend/node_modules/
frontend/dist/
```

**Step 7: Commit**

```bash
git add frontend/ .gitignore
git commit -m "feat(frontend): Vite React TypeScript scaffold with MUI and MSAL deps"
```

---

## Task 9: MSAL Auth Provider + Login Gate

**Files:**
- Create: `frontend/src/auth/msalConfig.ts`
- Create: `frontend/src/auth/AuthProvider.tsx`
- Create: `frontend/src/auth/LoginPage.tsx`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/App.tsx`

**Step 1: Create MSAL config**

```typescript
// frontend/src/auth/msalConfig.ts
import { Configuration, LogLevel } from '@azure/msal-browser'

export const msalConfig: Configuration = {
  auth: {
    clientId: import.meta.env.VITE_CLIENT_ID || '',
    authority: `https://login.microsoftonline.com/${import.meta.env.VITE_TENANT_ID || 'common'}`,
    redirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: 'sessionStorage',
  },
}

export const loginScopes = ['User.Read']
export const graphScopes = ['Files.ReadWrite.All']
```

**Step 2: Create auth provider wrapper**

```tsx
// frontend/src/auth/AuthProvider.tsx
import { MsalProvider } from '@azure/msal-react'
import { PublicClientApplication } from '@azure/msal-browser'
import { msalConfig } from './msalConfig'
import { ReactNode } from 'react'

const msalInstance = new PublicClientApplication(msalConfig)

export function AuthProvider({ children }: { children: ReactNode }) {
  return <MsalProvider instance={msalInstance}>{children}</MsalProvider>
}

export { msalInstance }
```

**Step 3: Create login page**

```tsx
// frontend/src/auth/LoginPage.tsx
import { useMsal } from '@azure/msal-react'
import { loginScopes } from './msalConfig'
import { Button, Box, Typography } from '@mui/material'

export function LoginPage() {
  const { instance } = useMsal()

  const handleLogin = () => {
    instance.loginRedirect({ scopes: loginScopes })
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', mt: 10 }}>
      <Typography variant="h4" gutterBottom>Sharing Audit Dashboard</Typography>
      <Typography variant="body1" color="text.secondary" gutterBottom>
        Log in with your organization account to view and manage your shared files.
      </Typography>
      <Button variant="contained" size="large" onClick={handleLogin} sx={{ mt: 3 }}>
        Sign in with Microsoft
      </Button>
    </Box>
  )
}
```

**Step 4: Update main.tsx**

```tsx
// frontend/src/main.tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider, createTheme, CssBaseline } from '@mui/material'
import { AuthProvider } from './auth/AuthProvider'
import App from './App'

const queryClient = new QueryClient()
const theme = createTheme()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AuthProvider>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>
  </React.StrictMode>,
)
```

**Step 5: Update App.tsx with auth gate**

```tsx
// frontend/src/App.tsx
import { useIsAuthenticated, useMsal } from '@azure/msal-react'
import { useEffect, useState } from 'react'
import { LoginPage } from './auth/LoginPage'
import { loginScopes } from './auth/msalConfig'

function App() {
  const isAuthenticated = useIsAuthenticated()
  const { instance, accounts } = useMsal()
  const [sessionReady, setSessionReady] = useState(false)

  useEffect(() => {
    if (!isAuthenticated || accounts.length === 0) return

    // Get ID token and send to backend to create session
    instance.acquireTokenSilent({
      scopes: loginScopes,
      account: accounts[0],
    }).then(async (response) => {
      await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id_token: response.idToken }),
      })
      setSessionReady(true)
    }).catch(console.error)
  }, [isAuthenticated, accounts, instance])

  if (!isAuthenticated) return <LoginPage />
  if (!sessionReady) return <div>Establishing session...</div>

  return <div>Dashboard placeholder — Task 11 will build this</div>
}

export default App
```

**Step 6: Create frontend .env for dev**

```bash
# frontend/.env.local
VITE_CLIENT_ID=6886a8ec-78a8-4a53-8a1c-a409dfb1df0b
VITE_TENANT_ID=d4ebe44b-4256-4f0d-b490-fd95cd0e9a92
```

Add `frontend/.env.local` to `.gitignore`.

**Step 7: Create frontend .env.example**

```bash
# frontend/.env.example
VITE_CLIENT_ID=your-client-id
VITE_TENANT_ID=your-tenant-id
```

**Step 8: Verify it builds**

Run: `cd frontend && npm run build`
Expected: Build succeeds

**Step 9: Commit**

```bash
git add frontend/src/auth/ frontend/src/main.tsx frontend/src/App.tsx frontend/.env.example .gitignore
git commit -m "feat(frontend): MSAL auth provider with login page and session handshake"
```

---

## Task 10: TanStack Query API Hooks

**Files:**
- Create: `frontend/src/api/hooks.ts`
- Create: `frontend/src/api/types.ts`

**Step 1: Define TypeScript types**

```typescript
// frontend/src/api/types.ts
export interface SharedFile {
  id: string
  drive_id: string
  item_id: string
  risk_score: number
  risk_level: 'HIGH' | 'MEDIUM' | 'LOW'
  source: string
  item_type: string
  item_path: string
  item_web_url: string
  sharing_type: string
  shared_with: string
  shared_with_type: string
}

export interface FilesResponse {
  files: SharedFile[]
  last_scan: string | null
}

export interface StatsResponse {
  total: number
  high: number
  medium: number
  low: number
  last_scan: string | null
}

export interface UnshareResponse {
  succeeded: string[]
  failed: { id: string; error: string }[]
}
```

**Step 2: Create API hooks**

```typescript
// frontend/src/api/hooks.ts
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useMsal } from '@azure/msal-react'
import { graphScopes } from '../auth/msalConfig'
import type { FilesResponse, StatsResponse, UnshareResponse } from './types'

async function apiFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(url, { credentials: 'include', ...options })
  if (!resp.ok) throw new Error(`API error: ${resp.status}`)
  return resp.json()
}

export function useFiles(filters?: { risk_level?: string; source?: string; search?: string }) {
  const params = new URLSearchParams()
  if (filters?.risk_level) params.set('risk_level', filters.risk_level)
  if (filters?.source) params.set('source', filters.source)
  if (filters?.search) params.set('search', filters.search)
  const qs = params.toString()

  return useQuery({
    queryKey: ['files', filters],
    queryFn: () => apiFetch<FilesResponse>(`/api/files${qs ? `?${qs}` : ''}`),
  })
}

export function useStats() {
  return useQuery({
    queryKey: ['stats'],
    queryFn: () => apiFetch<StatsResponse>('/api/stats'),
  })
}

export function useUnshare() {
  const queryClient = useQueryClient()
  const { instance, accounts } = useMsal()

  return useMutation({
    mutationFn: async (fileIds: string[]) => {
      // Acquire Graph API token for the delegated unshare call
      const tokenResponse = await instance.acquireTokenSilent({
        scopes: graphScopes,
        account: accounts[0],
      })

      return apiFetch<UnshareResponse>('/api/unshare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          file_ids: fileIds,
          graph_token: tokenResponse.accessToken,
        }),
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['files'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}
```

**Step 3: Verify it builds**

Run: `cd frontend && npm run build`
Expected: Build succeeds

**Step 4: Commit**

```bash
git add frontend/src/api/
git commit -m "feat(frontend): TanStack Query hooks for files, stats, and unshare"
```

---

## Task 11: Dashboard UI — DataGrid, Summary Cards, Toolbar, Unshare Flow

**Files:**
- Create: `frontend/src/components/Dashboard.tsx`
- Create: `frontend/src/components/SummaryCards.tsx`
- Create: `frontend/src/components/FileDataGrid.tsx`
- Create: `frontend/src/components/UnshareButton.tsx`
- Create: `frontend/src/components/AppHeader.tsx`
- Modify: `frontend/src/App.tsx`

**Step 1: Create AppHeader component**

```tsx
// frontend/src/components/AppHeader.tsx
import { AppBar, Toolbar, Typography, Button, Box } from '@mui/material'
import { useMsal } from '@azure/msal-react'

export function AppHeader() {
  const { instance, accounts } = useMsal()
  const name = accounts[0]?.name || accounts[0]?.username || ''

  return (
    <AppBar position="static" sx={{ bgcolor: '#1a3c6e' }}>
      <Toolbar>
        <Typography variant="h6" sx={{ flexGrow: 1 }}>Sharing Audit Dashboard</Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <Typography variant="body2">{name}</Typography>
          <Button color="inherit" size="small" onClick={() => {
            fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
            instance.logoutRedirect()
          }}>
            Logout
          </Button>
        </Box>
      </Toolbar>
    </AppBar>
  )
}
```

**Step 2: Create SummaryCards component**

```tsx
// frontend/src/components/SummaryCards.tsx
import { Box, Paper, Typography } from '@mui/material'
import { useStats } from '../api/hooks'

export function SummaryCards() {
  const { data } = useStats()
  if (!data) return null

  const cards = [
    { label: 'HIGH', count: data.high, color: '#dc3545' },
    { label: 'MEDIUM', count: data.medium, color: '#f0ad4e' },
    { label: 'LOW', count: data.low, color: '#5cb85c' },
  ]

  return (
    <Box sx={{ display: 'flex', gap: 2, my: 2, flexWrap: 'wrap', alignItems: 'center' }}>
      {cards.map(c => (
        <Paper key={c.label} sx={{ px: 3, py: 1.5, bgcolor: c.color, color: c.label === 'MEDIUM' ? '#333' : '#fff' }}>
          <Typography variant="subtitle2">{c.label}: {c.count}</Typography>
        </Paper>
      ))}
      <Typography variant="body2" color="text.secondary" sx={{ ml: 2 }}>
        {data.total} shared items | Last scan: {data.last_scan ? new Date(data.last_scan).toLocaleString() : 'never'}
      </Typography>
    </Box>
  )
}
```

**Step 3: Create FileDataGrid component**

```tsx
// frontend/src/components/FileDataGrid.tsx
import { DataGridPro, GridColDef, GridRowSelectionModel } from '@mui/x-data-grid-pro'
import { Chip, Link } from '@mui/material'
import type { SharedFile } from '../api/types'

const riskColor = { HIGH: 'error', MEDIUM: 'warning', LOW: 'success' } as const

const columns: GridColDef<SharedFile>[] = [
  {
    field: 'risk_score', headerName: 'Score', width: 70, type: 'number',
    renderCell: (p) => <strong>{p.value}</strong>,
  },
  {
    field: 'risk_level', headerName: 'Risk', width: 90,
    renderCell: (p) => (
      <Chip label={p.value} size="small" color={riskColor[p.value as keyof typeof riskColor] || 'default'} />
    ),
  },
  { field: 'source', headerName: 'Source', width: 100 },
  { field: 'item_type', headerName: 'Type', width: 70 },
  { field: 'item_path', headerName: 'File / Folder Path', flex: 1, minWidth: 250 },
  {
    field: 'item_web_url', headerName: 'Link', width: 70,
    renderCell: (p) => p.value ? <Link href={p.value} target="_blank" rel="noopener">Open</Link> : '-',
  },
  { field: 'sharing_type', headerName: 'Sharing Type', width: 150 },
  { field: 'shared_with', headerName: 'Shared With', flex: 1, minWidth: 200 },
  { field: 'shared_with_type', headerName: 'Audience', width: 100 },
]

interface Props {
  files: SharedFile[]
  loading: boolean
  selectedIds: GridRowSelectionModel
  onSelectionChange: (ids: GridRowSelectionModel) => void
}

export function FileDataGrid({ files, loading, selectedIds, onSelectionChange }: Props) {
  return (
    <DataGridPro
      rows={files}
      columns={columns}
      loading={loading}
      checkboxSelection
      rowSelectionModel={selectedIds}
      onRowSelectionModelChange={onSelectionChange}
      disableRowSelectionOnClick
      initialState={{
        sorting: { sortModel: [{ field: 'risk_score', sort: 'desc' }] },
      }}
      getRowClassName={(params) => `risk-${params.row.risk_level.toLowerCase()}`}
      sx={{
        '& .risk-high': { bgcolor: '#fce4e4' },
        '& .risk-medium': { bgcolor: '#fff8e6' },
        '& .risk-low': { bgcolor: '#eaf6ea' },
        height: 'calc(100vh - 250px)',
      }}
      pagination
      pageSizeOptions={[25, 50, 100]}
    />
  )
}
```

**Step 4: Create UnshareButton component**

```tsx
// frontend/src/components/UnshareButton.tsx
import { useState } from 'react'
import { Button, Dialog, DialogTitle, DialogContent, DialogActions, Typography, Alert, Snackbar } from '@mui/material'
import { useUnshare } from '../api/hooks'

interface Props {
  selectedIds: string[]
  onComplete: () => void
}

export function UnshareButton({ selectedIds, onComplete }: Props) {
  const [open, setOpen] = useState(false)
  const [toast, setToast] = useState<{ message: string; severity: 'success' | 'error' } | null>(null)
  const unshare = useUnshare()

  const handleConfirm = async () => {
    setOpen(false)
    try {
      const result = await unshare.mutateAsync(selectedIds)
      const msg = result.failed.length
        ? `${result.succeeded.length} succeeded, ${result.failed.length} failed`
        : `${result.succeeded.length} files unshared successfully`
      setToast({ message: msg, severity: result.failed.length ? 'error' : 'success' })
      onComplete()
    } catch (e) {
      setToast({ message: `Unshare failed: ${e}`, severity: 'error' })
    }
  }

  return (
    <>
      <Button
        variant="contained"
        color="error"
        disabled={selectedIds.length === 0 || unshare.isPending}
        onClick={() => setOpen(true)}
      >
        {unshare.isPending ? 'Removing...' : `Remove Sharing (${selectedIds.length})`}
      </Button>

      <Dialog open={open} onClose={() => setOpen(false)}>
        <DialogTitle>Remove All Sharing</DialogTitle>
        <DialogContent>
          <Typography>
            Remove all sharing from <strong>{selectedIds.length}</strong> file{selectedIds.length > 1 ? 's' : ''}?
            This cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button onClick={handleConfirm} color="error" variant="contained">Remove Sharing</Button>
        </DialogActions>
      </Dialog>

      <Snackbar open={!!toast} autoHideDuration={6000} onClose={() => setToast(null)}>
        {toast ? <Alert severity={toast.severity} onClose={() => setToast(null)}>{toast.message}</Alert> : undefined}
      </Snackbar>
    </>
  )
}
```

**Step 5: Create Dashboard component**

```tsx
// frontend/src/components/Dashboard.tsx
import { useState } from 'react'
import { Box, TextField, FormControl, InputLabel, Select, MenuItem, Container } from '@mui/material'
import { GridRowSelectionModel } from '@mui/x-data-grid-pro'
import { useFiles } from '../api/hooks'
import { AppHeader } from './AppHeader'
import { SummaryCards } from './SummaryCards'
import { FileDataGrid } from './FileDataGrid'
import { UnshareButton } from './UnshareButton'

export function Dashboard() {
  const [search, setSearch] = useState('')
  const [riskFilter, setRiskFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')
  const [selectedIds, setSelectedIds] = useState<GridRowSelectionModel>([])

  const { data, isLoading } = useFiles({
    search: search || undefined,
    risk_level: riskFilter || undefined,
    source: sourceFilter || undefined,
  })

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <AppHeader />
      <Container maxWidth={false} sx={{ flex: 1, py: 2 }}>
        <SummaryCards />

        <Box sx={{ display: 'flex', gap: 2, mb: 2, alignItems: 'center', flexWrap: 'wrap' }}>
          <TextField
            size="small" placeholder="Search file paths..."
            value={search} onChange={e => setSearch(e.target.value)}
            sx={{ minWidth: 250 }}
          />
          <FormControl size="small" sx={{ minWidth: 130 }}>
            <InputLabel>Risk Level</InputLabel>
            <Select value={riskFilter} label="Risk Level" onChange={e => setRiskFilter(e.target.value)}>
              <MenuItem value="">All</MenuItem>
              <MenuItem value="HIGH">HIGH</MenuItem>
              <MenuItem value="MEDIUM">MEDIUM</MenuItem>
              <MenuItem value="LOW">LOW</MenuItem>
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 130 }}>
            <InputLabel>Source</InputLabel>
            <Select value={sourceFilter} label="Source" onChange={e => setSourceFilter(e.target.value)}>
              <MenuItem value="">All</MenuItem>
              <MenuItem value="OneDrive">OneDrive</MenuItem>
              <MenuItem value="SharePoint">SharePoint</MenuItem>
              <MenuItem value="Teams">Teams</MenuItem>
            </Select>
          </FormControl>
          <Box sx={{ flexGrow: 1 }} />
          <UnshareButton
            selectedIds={selectedIds as string[]}
            onComplete={() => setSelectedIds([])}
          />
        </Box>

        <FileDataGrid
          files={data?.files || []}
          loading={isLoading}
          selectedIds={selectedIds}
          onSelectionChange={setSelectedIds}
        />
      </Container>
    </Box>
  )
}
```

**Step 6: Update App.tsx to use Dashboard**

```tsx
// frontend/src/App.tsx
import { useIsAuthenticated, useMsal } from '@azure/msal-react'
import { useEffect, useState } from 'react'
import { LoginPage } from './auth/LoginPage'
import { Dashboard } from './components/Dashboard'
import { loginScopes } from './auth/msalConfig'
import { Box, CircularProgress, Typography } from '@mui/material'

function App() {
  const isAuthenticated = useIsAuthenticated()
  const { instance, accounts } = useMsal()
  const [sessionReady, setSessionReady] = useState(false)

  useEffect(() => {
    if (!isAuthenticated || accounts.length === 0) return

    instance.acquireTokenSilent({
      scopes: loginScopes,
      account: accounts[0],
    }).then(async (response) => {
      await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id_token: response.idToken }),
      })
      setSessionReady(true)
    }).catch(console.error)
  }, [isAuthenticated, accounts, instance])

  if (!isAuthenticated) return <LoginPage />

  if (!sessionReady) {
    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', mt: 10, gap: 2 }}>
        <CircularProgress />
        <Typography>Establishing session...</Typography>
      </Box>
    )
  }

  return <Dashboard />
}

export default App
```

**Step 7: Verify it builds**

Run: `cd frontend && npm run build`
Expected: Build succeeds

**Step 8: Commit**

```bash
git add frontend/src/components/ frontend/src/App.tsx
git commit -m "feat(frontend): Dashboard UI with DataGrid, summary cards, filters, and unshare"
```

---

## Task 12: Docker + Docker Compose + Static File Serving

**Files:**
- Create: `docker/Dockerfile.webapp`
- Modify: `docker-compose.yml`
- Modify: `src/webapp/app.py`
- Modify: `.env.example`

**Step 1: Add static file serving to FastAPI app**

Update `src/webapp/app.py` — at the end of `create_app()`, mount the static files if the directory exists:

```python
import os
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# ... inside create_app(), after all routers:

    # Serve React SPA static files in production
    static_dir = Path(__file__).parent.parent.parent / "frontend" / "dist"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

        @app.get("/{path:path}")
        def spa_fallback(path: str):
            file_path = static_dir / path
            if file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))
            return FileResponse(str(static_dir / "index.html"))
```

**Step 2: Create Dockerfile.webapp**

```dockerfile
# docker/Dockerfile.webapp
# Stage 1: Build React frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_CLIENT_ID
ARG VITE_TENANT_ID
RUN npm run build

# Stage 2: Python backend
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY src/ ./src/
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
ENV PYTHONPATH=/app/src
EXPOSE 8000
CMD ["python", "-m", "webapp"]
```

**Step 3: Add webapp service to docker-compose.yml**

Add after the `reporter` service:

```yaml
  webapp:
    build:
      context: .
      dockerfile: docker/Dockerfile.webapp
      args:
        VITE_CLIENT_ID: ${CLIENT_ID}
        VITE_TENANT_ID: ${TENANT_ID}
    environment:
      TENANT_ID: ${TENANT_ID}
      CLIENT_ID: ${CLIENT_ID}
      CLIENT_SECRET: ${CLIENT_SECRET}
      NEO4J_URI: bolt://neo4j:7687
      NEO4J_USER: neo4j
      NEO4J_PASSWORD: ${NEO4J_PASSWORD:-changeme}
      TENANT_DOMAIN: ${TENANT_DOMAIN:-testaviva.dk}
      SESSION_SECRET: ${SESSION_SECRET:-change-me-in-production}
    ports:
      - "8000:8000"
    depends_on:
      - neo4j
```

**Step 4: Update .env.example**

Add:
```
# Webapp settings
SESSION_SECRET=change-me-in-production
```

**Step 5: Verify Docker build**

Run: `docker compose build webapp`
Expected: Build succeeds

**Step 6: Run all backend tests**

Run: `PYTHONPATH=src pytest tests/ -v`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add docker/Dockerfile.webapp docker-compose.yml src/webapp/app.py .env.example
git commit -m "feat(webapp): Docker multi-stage build and docker-compose integration"
```

---

## Post-Implementation Checklist

After all 12 tasks:

1. **Run full test suite**: `PYTHONPATH=src pytest tests/ -v`
2. **Run pyflakes**: `PYTHONPATH=src python -m pyflakes src/ tests/`
3. **Build frontend**: `cd frontend && npm run build`
4. **Docker build**: `docker compose build webapp`
5. **Manual smoke test**: Start Neo4j + webapp, log in via Entra, view files, unshare

### Azure AD Setup Required (manual)

Before the app works, update the Azure AD app registration:
1. Add **delegated permission**: `Files.ReadWrite.All`
2. Add **SPA platform** with redirect URI: `http://localhost:8000` (and `http://localhost:5173` for dev)
3. Grant admin consent
