"""Shared deduplication logic for reporter and webapp."""

from shared.classify import compute_risk_score, get_risk_level, is_teams_chat_file

RISK_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
SWT_PRIORITY = {"Anonymous": 0, "External": 1, "Guest": 2, "Internal": 3, "Unknown": 4}


def deduplicate_records(
    records: list[dict],
    include_ids: bool = False,
    tag_teams: bool = True,
) -> list[dict]:
    """Group records by file, keep highest risk, consolidate sharing details, compute risk score.

    Args:
        records: Raw sharing records from Neo4j.
        include_ids: If True, include drive_id/item_id and generate a composite id field (for webapp).
        tag_teams: If True, reclassify OneDrive Teams chat files with source "Teams".

    Returns:
        Deduplicated records sorted by risk_score descending.
    """
    groups: dict[str, dict] = {}

    for r in records:
        if include_ids:
            key = f"{r.get('drive_id')}:{r.get('item_id')}"
        else:
            key = r.get("item_web_url") or f"{r.get('source', '')}:{r.get('item_path', '')}"

        if key not in groups:
            groups[key] = {
                "key": key,
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

        # Keep highest risk
        if RISK_ORDER.get(r.get("risk_level", "LOW"), 2) < RISK_ORDER.get(g["risk_level"], 2):
            g["risk_level"] = r["risk_level"]

        # Collect unique sharing info
        for field, list_key in [
            ("sharing_type", "sharing_types"),
            ("shared_with", "shared_with_list"),
            ("shared_with_type", "shared_with_types"),
            ("role", "roles"),
        ]:
            val = r.get(field, "")
            if val and val not in g[list_key]:
                g[list_key].append(val)

    result = []
    for g in groups.values():
        worst_swt = (
            min(g["shared_with_types"], key=lambda t: SWT_PRIORITY.get(t, 5))
            if g["shared_with_types"] else "Unknown"
        )
        worst_role = (
            "Write" if "Write" in g["roles"] or "Owner" in g["roles"]
            else ("Read" if "Read" in g["roles"] else "Unknown")
        )

        source = g["source"]
        if tag_teams and is_teams_chat_file(g["item_path"]) and source == "OneDrive":
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

        row = {
            "risk_score": risk_score,
            "risk_level": risk_level,
            "source": source,
            "item_type": g["item_type"],
            "item_path": g["item_path"],
            "item_web_url": g["item_web_url"],
            "sharing_type": ", ".join(g["sharing_types"]),
            "shared_with": ", ".join(g["shared_with_list"]),
            "shared_with_type": ", ".join(g["shared_with_types"]),
            "role": ", ".join(g["roles"]),
        }
        if include_ids:
            row["id"] = f"{g['drive_id']}:{g['item_id']}"
            row["drive_id"] = g["drive_id"]
            row["item_id"] = g["item_id"]

        result.append(row)

    result.sort(key=lambda r: -r["risk_score"])
    return result
