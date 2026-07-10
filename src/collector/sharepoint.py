"""SharePoint collection: enumerate sites, walk drives, collect permissions."""

import logging

import httpx

from collector.graph_client import GraphClient
from shared.neo4j_client import Neo4jClient
from collector.onedrive import _walk_drive_items
from collector.user_cache import UserCache
from collector.neo4j_user_node import Neo4jUserNode
from collector.delta import delta_scan_drive, attempt_delta_scan, seed_delta_link_safe

logger = logging.getLogger(__name__)


def collect_sharepoint_sites(
    graph: GraphClient,
    user_cache: UserCache,
    neo4j: Neo4jClient,
    run_id: str,
    tenant_domain: str,
    is_full: bool = True,
    ignore_sharepoint_groups: bool = False
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

            if owner.get("user", {}).get("email") and owner.get("user", {}).get("id"):
                owner_email = owner["user"]["email"]
                # Create user node and establish ownership
                owner_user = Neo4jUserNode(owner["user"], source="Internal")  # Drive owner is always internal
                owner_user.merge_as_site_owner(neo4j, site_id)
                
            elif owner.get("group", {}).get("id"):
                group_id = owner.get("group", {}).get("id")
                group_owners = graph.get_group_owners(group_id)
                
                if len(group_owners) > 0:
                    owner_user = Neo4jUserNode(group_owners[0], source="Internal")  # Drive owner is always internal
                    owner_user.merge_as_site_owner(neo4j, site_id)

            if is_full:
                count = _walk_drive_items(
                    graph,
                    user_cache,
                    neo4j,
                    drive_id,
                    "root",
                    "",
                    site_id,
                    owner_email,
                    tenant_domain,
                    run_id,
                    ignore_sharepoint_groups,
                )
                seed_delta_link_safe(graph, neo4j, drive_id, f"site {site_name}")
            else:
                delta_link = neo4j.get_delta_link(drive_id)
                if delta_link:
                    count, needs_fallback = attempt_delta_scan(
                        graph,
                        user_cache,
                        neo4j,
                        drive_id,
                        delta_link,
                        site_id,
                        owner_email,
                        tenant_domain,
                        run_id,
                    )
                    if needs_fallback:
                        logger.warning(
                            f"Delta link expired for drive {drive_id}, "
                            "falling back to full walk"
                        )
                        count = _walk_drive_items(
                            graph,
                            user_cache,
                            neo4j,
                            drive_id,
                            "root",
                            "",
                            site_id,
                            owner_email,
                            tenant_domain,
                            run_id,
                            ignore_sharepoint_groups
                        )
                else:
                    logger.info(f"  No delta link for drive {drive_id} — full walk")
                    count = _walk_drive_items(
                        graph,
                        user_cache,
                        neo4j,
                        drive_id,
                        "root",
                        "",
                        site_id,
                        owner_email,
                        tenant_domain,
                        run_id,
                        ignore_sharepoint_groups
                    )
                
                seed_delta_link_safe(graph, neo4j, drive_id, f"site {site_name}")

            total += count

        logger.info(f"  {site_name}: done. Running total: {total}")

    return total
