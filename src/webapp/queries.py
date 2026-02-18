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
