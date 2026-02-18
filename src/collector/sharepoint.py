"""SharePoint collection: enumerate sites, walk drives, collect permissions."""

import logging

from collector.graph_client import GraphClient
from shared.neo4j_client import Neo4jClient
from collector.onedrive import _walk_drive_items

logger = logging.getLogger(__name__)


def collect_sharepoint_sites(
    graph: GraphClient,
    neo4j: Neo4jClient,
    run_id: str,
    tenant_domain: str,
) -> int:
    """Collect all sharing permissions across SharePoint sites. Returns total item count."""
    sites = graph.get_all_sites()

    # Filter out personal OneDrive sites
    sites = [s for s in sites if "-my.sharepoint.com" not in (s.get("webUrl") or "")]
    # Filter out sites without display names
    sites = [s for s in sites if s.get("displayName")]

    logger.info(f"Found {len(sites)} SharePoint sites to audit.")
    total = 0

    for i, site in enumerate(sites, 1):
        site_id = site["id"]
        site_name = site.get("displayName", site.get("webUrl", "Unknown"))
        site_url = site.get("webUrl", "")

        logger.info(f"[{i}/{len(sites)}] SharePoint: {site_name}")

        neo4j.merge_site(site_id, site_name, site_url, "SharePoint")

        try:
            drives = graph.get_site_drives(site_id)
        except Exception as e:
            logger.warning(f"Could not access drives for site {site_name}: {e}")
            continue

        for drive in drives:
            drive_id = drive["id"]

            # Determine owner (best effort)
            owner_email = ""
            owner = drive.get("owner", {})
            if owner.get("user", {}).get("email"):
                owner_email = owner["user"]["email"]
                neo4j.merge_user(owner_email, owner["user"].get("displayName", ""), "internal")
                neo4j.merge_owns(owner_email, site_id)

            count = _walk_drive_items(
                graph, neo4j, drive_id, "root", "",
                site_id, owner_email, tenant_domain, run_id,
            )
            total += count

        logger.info(f"  {site_name}: done. Running total: {total}")

    return total
