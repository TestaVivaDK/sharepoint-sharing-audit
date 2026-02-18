"""Microsoft Entra ID token validation and session management."""

import time
import uuid
from typing import Optional

import httpx
from fastapi import Request, HTTPException
from jose import jwt


class SessionStore:
    """In-memory session store. Maps session IDs to user info."""

    def __init__(self, ttl_seconds: int = 8 * 3600):
        self._sessions: dict[str, dict] = {}
        self._ttl = ttl_seconds

    def create(self, email: str, name: str) -> str:
        sid = str(uuid.uuid4())
        self._sessions[sid] = {"email": email, "name": name, "created_at": time.time()}
        return sid

    def get(self, sid: str) -> Optional[dict]:
        session = self._sessions.get(sid)
        if session and time.time() - session.get("created_at", 0) > self._ttl:
            del self._sessions[sid]
            return None
        return session

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


_jwks_cache: dict = {}  # {tenant_id: {"data": ..., "fetched_at": float}}
_JWKS_TTL = 86400  # 24 hours


async def get_entra_jwks(tenant_id: str) -> dict:
    """Fetch Microsoft Entra JWKS (cached with 24h TTL)."""
    cached = _jwks_cache.get(tenant_id)
    if cached and time.time() - cached["fetched_at"] < _JWKS_TTL:
        return cached["data"]
    url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        _jwks_cache[tenant_id] = {"data": data, "fetched_at": time.time()}
        return data


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


def require_session(request: Request) -> dict:
    """FastAPI dependency: get current session or raise 401."""
    sid = request.cookies.get("session_id")
    if not sid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session = request.app.state.sessions.get(sid)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    return session
