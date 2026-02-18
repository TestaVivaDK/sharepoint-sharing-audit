"""File listing and stats API routes."""

from fastapi import APIRouter, Request, Query, Depends
from webapp.auth import require_session
from webapp.queries import get_user_files, get_user_stats, get_last_scan_time, deduplicate_user_files

router = APIRouter(prefix="/api", tags=["files"])


@router.get("/files")
def list_files(
    request: Request,
    session: dict = Depends(require_session),
    risk_level: str | None = Query(None, description="Comma-separated: HIGH,MEDIUM,LOW"),
    source: str | None = Query(None, description="Comma-separated: OneDrive,SharePoint,Teams"),
    search: str | None = Query(None, description="Search in file path"),
):
    neo4j = request.app.state.neo4j
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
def stats(
    request: Request,
    session: dict = Depends(require_session),
):
    neo4j = request.app.state.neo4j
    run_id, last_scan = get_last_scan_time(neo4j)
    if not run_id:
        return {"total": 0, "high": 0, "medium": 0, "low": 0, "last_scan": None}

    counts = get_user_stats(neo4j, session["email"], run_id)
    counts["last_scan"] = last_scan
    return counts
