"""Collector entry point: python -m collector"""

import logging
import os
from datetime import datetime, timezone, timedelta

from shared.config import CollectorConfig
from shared.neo4j_client import Neo4jClient
from collector.graph_client import GraphClient
from collector.onedrive import collect_onedrive_user
from collector.sharepoint import collect_sharepoint_sites

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _should_full_scan(config: CollectorConfig, neo4j: Neo4jClient) -> bool:
    """Determine if a full scan is needed."""
    if config.force_full_scan:
        logger.info("FORCE_FULL_SCAN is set — running full scan.")
        return True

    if not neo4j.has_delta_links():
        logger.info("No delta links stored — running full scan.")
        return True

    last_full = neo4j.get_last_full_scan_time()
    if not last_full:
        logger.info("No prior full scan found — running full scan.")
        return True

    last_full_dt = datetime.fromisoformat(last_full)
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.full_scan_interval_days)
    if last_full_dt < cutoff:
        logger.info(
            f"Last full scan was {last_full} (>{config.full_scan_interval_days}d ago) "
            "— running full scan."
        )
        return True

    logger.info(f"Last full scan was {last_full} — running delta scan.")
    return False


def main():
    config = CollectorConfig()

    logger.info("Connecting to Neo4j...")
    neo4j = Neo4jClient(config.neo4j.uri, config.neo4j.user, config.neo4j.password)
    neo4j.init_schema()

    logger.info("Connecting to Microsoft Graph (app-only)...")
    graph = GraphClient(
        config.graph_api.tenant_id,
        config.graph_api.client_id,
        config.graph_api.client_secret,
        delay_ms=config.delay_ms,
    )

    tenant_domain = graph.get_tenant_domain()
    logger.info(f"Tenant domain: {tenant_domain}")

    is_full = _should_full_scan(config, neo4j)
    scan_type = "full" if is_full else "delta"
    run_id = neo4j.create_scan_run(scan_type)
    logger.info(f"Scan run: {run_id} (type={scan_type})")

    total = 0

    try:
        # OneDrive audit
        logger.info("=== Starting OneDrive Audit ===")
        users = graph.get_users()
        logger.info(f"Found {len(users)} users.")

        users_filter = os.environ.get("USERS_TO_AUDIT", "")
        if users_filter:
            filter_upns = [u.strip() for u in users_filter.split(",")]
            users = [u for u in users if u.get("userPrincipalName") in filter_upns]
            logger.info(f"Filtered to {len(users)} users: {filter_upns}")

        for i, user in enumerate(users, 1):
            upn = user.get("userPrincipalName", "?")
            logger.info(
                f"[{i}/{len(users)}] OneDrive: {user.get('displayName', '?')} ({upn})"
            )
            count = collect_onedrive_user(
                graph, neo4j, user, run_id, tenant_domain, is_full
            )
            total += count

        # SharePoint audit
        if os.environ.get("SKIP_SHAREPOINT", "").lower() not in ("1", "true", "yes"):
            logger.info("=== Starting SharePoint Audit ===")
            sp_count = collect_sharepoint_sites(
                graph, neo4j, run_id, tenant_domain, is_full
            )
            total += sp_count
        else:
            logger.info("Skipping SharePoint audit (SKIP_SHAREPOINT is set)")

        neo4j.complete_scan_run(run_id)
        logger.info(f"Collection complete. Total shared items: {total}")
    except Exception:
        logger.exception("Collection failed — marking scan run as failed")
        neo4j.execute(
            "MATCH (r:ScanRun {runId: $runId}) SET r.status = 'failed'",
            {"runId": run_id},
        )
        raise
    finally:
        neo4j.close()


if __name__ == "__main__":
    main()
