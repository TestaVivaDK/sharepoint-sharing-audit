"""Neo4j read queries for report generation."""

from shared.neo4j_client import Neo4jClient


def get_latest_completed_run(client: Neo4jClient) -> str | None:
    """Get the runId of the most recent completed ScanRun."""
    result = client.execute("""
        MATCH (r:ScanRun {status: 'completed'})
        RETURN r.runId AS runId
        ORDER BY r.timestamp DESC
        LIMIT 1
    """)
    return result[0]["runId"] if result else None


def get_sharing_data(client: Neo4jClient, run_id: str) -> list[dict]:
    """Get all sharing records for a given scan run, enriched with owner/site info."""
    result = client.execute("""
        MATCH (f:File)-[s:SHARED_WITH {lastSeenRunId: $runId}]->(u:User)
        MATCH (site:Site)-[:CONTAINS]->(f)
        OPTIONAL MATCH (owner:User)-[:OWNS]->(site)
        RETURN
            s.riskLevel AS risk_level,
            site.source AS source,
            f.path AS item_path,
            f.webUrl AS item_web_url,
            f.type AS item_type,
            s.sharingType AS sharing_type,
            u.email AS shared_with,
            u.displayName AS shared_with_name,
            s.sharedWithType AS shared_with_type,
            s.role AS role,
            s.createdDateTime AS created_date_time,
            s.grantedBy AS granted_by,
            owner.email AS owner_email,
            owner.displayName AS owner_display_name,
            site.name AS site_name
        ORDER BY
            CASE s.riskLevel WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
            owner.email, f.path
    """, {"runId": run_id})
    return result
