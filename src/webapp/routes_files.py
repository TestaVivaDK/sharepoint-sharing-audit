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
