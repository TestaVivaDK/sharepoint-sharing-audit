"""Unshare API route — bulk permission removal via Graph API."""

import logging
import re

from fastapi import APIRouter, HTTPException, Depends
from jose import jwt
from pydantic import BaseModel, field_validator
from webapp.auth import require_session
from webapp.graph_unshare import bulk_unshare

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["unshare"])

MAX_FILE_IDS = 100
_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_\-!]+:[A-Za-z0-9_\-!]+$")


class UnshareRequest(BaseModel):
    file_ids: list[str]
    graph_token: str

    @field_validator("file_ids")
    @classmethod
    def validate_file_ids(cls, v: list[str]) -> list[str]:
        if len(v) > MAX_FILE_IDS:
            raise ValueError(f"Maximum {MAX_FILE_IDS} files per request")
        for fid in v:
            if not _FILE_ID_RE.match(fid):
                raise ValueError(f"Invalid file_id format: {fid}")
        return v


def _validate_graph_token_owner(graph_token: str, session_email: str):
    """Verify the Graph token belongs to the authenticated session user."""
    try:
        claims = jwt.get_unverified_claims(graph_token)
        token_upn = claims.get("upn", "").lower()
        if not token_upn:
            token_upn = claims.get("preferred_username", "").lower()
        if token_upn != session_email.lower():
            raise HTTPException(
                status_code=403,
                detail="Graph token does not belong to authenticated user",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Could not decode graph token claims: {e}")
        raise HTTPException(status_code=400, detail="Invalid graph token")


@router.post("/unshare")
async def unshare(body: UnshareRequest, session: dict = Depends(require_session)):
    if not body.file_ids:
        raise HTTPException(status_code=400, detail="No files specified")

    _validate_graph_token_owner(body.graph_token, session["email"])

    logger.info(f"Unshare request from {session['email']}: {len(body.file_ids)} files")
    result = await bulk_unshare(body.graph_token, body.file_ids)
    logger.info(f"Unshare result: {len(result['succeeded'])} succeeded, {len(result['failed'])} failed")

    return result
