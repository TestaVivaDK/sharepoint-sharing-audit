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
