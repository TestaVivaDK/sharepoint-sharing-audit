"""Reporter entry point: python -m reporter"""

import logging
import os
import re
from datetime import datetime, timezone

from shared.config import ReporterConfig
from shared.neo4j_client import Neo4jClient
from shared.classify import is_teams_chat_file
from reporter.queries import get_latest_completed_run, get_sharing_data
from reporter.csv_export import generate_csv
from reporter.pdf_export import generate_pdf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


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
    logger.info(f"Total sharing records: {len(all_records)}")

    if not all_records:
        logger.info("No shared items found.")
        neo4j.close()
        return

    # Split Teams chat files
    regular = [r for r in all_records if not is_teams_chat_file(r.get("item_path", ""))]
    teams = [r for r in all_records if is_teams_chat_file(r.get("item_path", ""))]

    # Combined reports
    csv_path = os.path.join(config.output_dir, f"SharingAudit_{timestamp}.csv")
    generate_csv(regular, csv_path)
    logger.info(f"Combined CSV: {csv_path} ({len(regular)} records)")

    pdf_path = os.path.join(config.output_dir, f"SharingAudit_{timestamp}.pdf")
    result = generate_pdf(regular, pdf_path)
    logger.info(f"Combined PDF: {result}")

    if teams:
        teams_csv = os.path.join(config.output_dir, f"SharingAudit_{timestamp}_TeamsChatFiles.csv")
        generate_csv(teams, teams_csv)
        logger.info(f"Teams CSV: {teams_csv} ({len(teams)} records)")

        teams_pdf = os.path.join(config.output_dir, f"SharingAudit_{timestamp}_TeamsChatFiles.pdf")
        result = generate_pdf(teams, teams_pdf, title="Teams Chat Files — Sharing Audit")
        logger.info(f"Teams PDF: {result}")

    # Per-owner reports
    owners = {}
    for r in all_records:
        owner = r.get("owner_email") or "(unknown)"
        owners.setdefault(owner, []).append(r)

    for owner, records in owners.items():
        safe_owner = re.sub(r'[\\/:*?"<>|]', '_', owner)
        owner_regular = [r for r in records if not is_teams_chat_file(r.get("item_path", ""))]
        owner_teams = [r for r in records if is_teams_chat_file(r.get("item_path", ""))]
        display = records[0].get("owner_display_name", owner)

        if owner_regular:
            path = os.path.join(config.output_dir, f"SharingAudit_{safe_owner}_{timestamp}.csv")
            generate_csv(owner_regular, path)
            pdf_p = os.path.join(config.output_dir, f"SharingAudit_{safe_owner}_{timestamp}.pdf")
            generate_pdf(owner_regular, pdf_p, user_label=f"{display} ({owner})")
            logger.info(f"  {owner}: {len(owner_regular)} items + PDF")

        if owner_teams:
            path = os.path.join(config.output_dir, f"SharingAudit_{safe_owner}_TeamsChatFiles_{timestamp}.csv")
            generate_csv(owner_teams, path)
            pdf_p = os.path.join(config.output_dir, f"SharingAudit_{safe_owner}_TeamsChatFiles_{timestamp}.pdf")
            generate_pdf(owner_teams, pdf_p, title="Teams Chat Files", user_label=f"{display} ({owner})")
            logger.info(f"  {owner}: {len(owner_teams)} Teams chat files + PDF")

    logger.info("Report generation complete.")
    neo4j.close()


if __name__ == "__main__":
    main()
