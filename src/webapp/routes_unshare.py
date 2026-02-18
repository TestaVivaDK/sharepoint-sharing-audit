"""Unshare API route — bulk permission removal via Graph API."""

import logging
import re

from fastapi import APIRouter, HTTPException, Depends
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
            if ":" not in fid or len(fid) > 500:
                raise ValueError(f"Invalid file_id format: {fid}")
        return v


@router.post("/unshare")
async def unshare(body: UnshareRequest, session: dict = Depends(require_session)):
    if not body.file_ids:
        raise HTTPException(status_code=400, detail="No files specified")

    logger.info(f"Unshare request from {session['email']}: {len(body.file_ids)} files")
    result = await bulk_unshare(body.graph_token, body.file_ids)
    logger.info(f"Unshare result: {len(result['succeeded'])} succeeded, {len(result['failed'])} failed")

    return result
