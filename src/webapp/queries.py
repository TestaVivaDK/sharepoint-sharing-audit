"""Neo4j queries for the web app — user-specific data."""

from shared.neo4j_client import Neo4jClient
from shared.deduplicate import deduplicate_records


def get_last_scan_time(
    client: Neo4jClient,
) -> tuple[str | None, str | None, str | None]:
    """Get the latest scan run. Prefers completed, falls back to running."""
    result = client.execute("""
        MATCH (r:ScanRun) WHERE r.status IN ['completed', 'running']
        RETURN r.runId AS runId, r.timestamp AS timestamp, r.status AS status
        ORDER BY
            CASE r.status WHEN 'completed' THEN 0 ELSE 1 END,
            r.timestamp DESC
        LIMIT 1
    """)
    if not result:
        return None, None, None
    return result[0]["runId"], result[0]["timestamp"], result[0]["status"]


def get_user_files(client: Neo4jClient, email: str, run_id: str) -> list[dict]:
    """Get shared files where the current user granted the sharing permission."""
    result = client.execute(
        """
        MATCH (f:File)-[s:SHARED_WITH {lastSeenRunId: $runId, grantedBy: $email}]->(shared_user:User)
        MATCH (site:Site)-[:CONTAINS]->(f)
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
    """,
        {"email": email, "runId": run_id},
    )
    return result


def deduplicate_user_files(records: list[dict]) -> list[dict]:
    """Group records by file, consolidate sharing details, compute risk score."""
    return deduplicate_records(records, include_ids=True)


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
