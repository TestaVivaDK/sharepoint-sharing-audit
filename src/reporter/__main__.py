"""Reporter entry point: python -m reporter"""

import logging
import os
from datetime import datetime, timezone

from shared.config import ReporterConfig
from shared.neo4j_client import Neo4jClient
from shared.classify import is_teams_chat_file
from shared.deduplicate import deduplicate_records, RISK_ORDER
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
