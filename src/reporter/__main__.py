"""Reporter entry point: python -m reporter"""

import logging
import os
from datetime import datetime, timezone

from shared.config import ReporterConfig
from shared.neo4j_client import Neo4jClient
from shared.classify import is_teams_chat_file, compute_risk_score, get_risk_level
from reporter.queries import get_latest_completed_run, get_sharing_data
from reporter.csv_export import generate_csv
from reporter.pdf_export import generate_pdf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RISK_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def deduplicate_records(records: list[dict]) -> list[dict]:
    """Group records by file, keep highest risk, consolidate sharing details, compute risk score."""
    groups: dict[str, dict] = {}

    for r in records:
        key = r.get("item_web_url") or f"{r.get('source', '')}:{r.get('item_path', '')}"
        if key not in groups:
            groups[key] = {
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
        st = r.get("sharing_type", "")
        if st and st not in g["sharing_types"]:
            g["sharing_types"].append(st)
        sw = r.get("shared_with", "")
        if sw and sw not in g["shared_with_list"]:
            g["shared_with_list"].append(sw)
        swt = r.get("shared_with_type", "")
        if swt and swt not in g["shared_with_types"]:
            g["shared_with_types"].append(swt)
        role = r.get("role", "")
        if role and role not in g["roles"]:
            g["roles"].append(role)

    result = []
    for g in groups.values():
        # Recalculate risk level with expanded sensitive keywords
        # Use the highest-risk shared_with_type for scoring
        swt_priority = {"Anonymous": 0, "External": 1, "Guest": 2, "Internal": 3, "Unknown": 4}
        worst_swt = min(g["shared_with_types"], key=lambda t: swt_priority.get(t, 5)) if g["shared_with_types"] else "Unknown"
        worst_role = "Write" if "Write" in g["roles"] or "Owner" in g["roles"] else ("Read" if "Read" in g["roles"] else "Unknown")

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
            "risk_level": risk_level,
            "risk_score": risk_score,
            "source": g["source"],
            "item_path": g["item_path"],
            "item_web_url": g["item_web_url"],
            "item_type": g["item_type"],
            "sharing_type": ", ".join(g["sharing_types"]),
            "shared_with": ", ".join(g["shared_with_list"]),
            "shared_with_type": ", ".join(g["shared_with_types"]),
        })
    return result


def main():
    config = ReporterConfig()
    os.makedirs(config.output_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")

    logger.info("Connecting to Neo4j...")
    neo4j = Neo4jClient(config.neo4j.uri, config.neo4j.user, config.neo4j.password)

    run_id = get_latest_completed_run(neo4j)
    if not run_id:
        logger.error("No completed scan run found. Run the collector first.")
        neo4j.close()
        return

    logger.info(f"Generating reports for scan run: {run_id}")
    all_records = get_sharing_data(neo4j, run_id)
    logger.info(f"Total sharing records (raw): {len(all_records)}")

    if not all_records:
        logger.info("No shared items found.")
        neo4j.close()
        return

    # Tag Teams chat files with source "Teams" for display
    for r in all_records:
        if is_teams_chat_file(r.get("item_path", "")) and r.get("source") == "OneDrive":
            r["source"] = "Teams"

    # Deduplicate: one row per file, consolidate sharing details, compute risk score
    deduped = deduplicate_records(all_records)
    deduped.sort(key=lambda r: (-r["risk_score"], RISK_ORDER.get(r["risk_level"], 2)))
    logger.info(f"Unique files after deduplication: {len(deduped)}")

    # Combined CSV
    csv_path = os.path.join(config.output_dir, f"SharingAudit_{timestamp}.csv")
    generate_csv(deduped, csv_path)
    logger.info(f"Combined CSV: {csv_path} ({len(deduped)} records)")

    # Combined PDF — everything in one file
    pdf_path = os.path.join(config.output_dir, f"SharingAudit_{timestamp}.pdf")
    result = generate_pdf(deduped, pdf_path, webapp_url=config.webapp_url)
    logger.info(f"Combined PDF: {result}")

    logger.info("Report generation complete.")
    neo4j.close()


if __name__ == "__main__":
    main()
