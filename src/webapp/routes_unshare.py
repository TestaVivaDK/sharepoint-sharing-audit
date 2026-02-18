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
