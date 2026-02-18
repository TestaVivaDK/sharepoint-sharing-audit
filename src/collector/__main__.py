"""Collector entry point: python -m collector"""

import logging
import os

from shared.config import CollectorConfig
from shared.neo4j_client import Neo4jClient
from collector.graph_client import GraphClient
from collector.onedrive import collect_onedrive_user
from collector.sharepoint import collect_sharepoint_sites

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


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

    run_id = neo4j.create_scan_run()
    logger.info(f"Scan run: {run_id}")

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
            logger.info(f"[{i}/{len(users)}] OneDrive: {user.get('displayName', '?')} ({upn})")
            count = collect_onedrive_user(graph, neo4j, user, run_id, tenant_domain)
            total += count

        # SharePoint audit
        if os.environ.get("SKIP_SHAREPOINT", "").lower() not in ("1", "true", "yes"):
            logger.info("=== Starting SharePoint Audit ===")
            sp_count = collect_sharepoint_sites(graph, neo4j, run_id, tenant_domain)
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
