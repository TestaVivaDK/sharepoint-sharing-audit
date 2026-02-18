"""OneDrive collection: enumerate users, walk drives, collect permissions."""

import logging

from collector.graph_client import GraphClient
from shared.neo4j_client import Neo4jClient
from shared.classify import (
    get_sharing_type, get_shared_with_info, get_risk_level,
    get_permission_role,
)

logger = logging.getLogger(__name__)


def _walk_drive_items(
    graph: GraphClient,
    neo4j: Neo4jClient,
    drive_id: str,
    parent_id: str,
    parent_path: str,
    site_id: str,
    owner_email: str,
    tenant_domain: str,
    run_id: str,
) -> int:
    """Recursively walk drive items, collect permissions, write to Neo4j. Returns count."""
    count = 0
    try:
        children = graph.get_drive_children(drive_id, parent_id)
    except Exception as e:
        logger.warning(f"Could not list children of {parent_path}: {e}")
        return 0

    for item in children:
        item_path = f"{parent_path}/{item['name']}" if parent_path else f"/{item['name']}"
        item_type = "Folder" if item.get("folder") else "File"
        web_url = item.get("webUrl", "")

        try:
            permissions = graph.get_item_permissions(drive_id, item["id"])
        except Exception as e:
            logger.warning(f"Could not get permissions for {item_path}: {e}")
            permissions = []

        for perm in permissions:
            sharing_type = get_sharing_type(perm)
            shared_info = get_shared_with_info(perm, tenant_domain)
            role = get_permission_role(perm)
            risk = get_risk_level(sharing_type, shared_info["shared_with_type"], item_path)

            # Skip owner's own "owner" permission
            if role == "Owner" and shared_info["shared_with"] == owner_email:
                continue

            # Determine the "shared with" email for the User node
            shared_email = shared_info["shared_with"]
            if shared_info["shared_with_type"] == "Anonymous":
                shared_email = "anonymous"
            elif sharing_type == "Link-Organization":
                shared_email = "organization"

            neo4j.merge_file(drive_id, item["id"], item_path, web_url, item_type)
            neo4j.merge_user(shared_email, shared_info["shared_with"], shared_info["shared_with_type"])
            neo4j.merge_shared_with(
                drive_id=drive_id, item_id=item["id"],
                user_email=shared_email,
                sharing_type=sharing_type,
                shared_with_type=shared_info["shared_with_type"],
                role=role, risk_level=risk,
                created_date_time=perm.get("createdDateTime", ""),
                run_id=run_id,
            )
            neo4j.merge_contains(site_id, drive_id, item["id"])
            neo4j.mark_file_found(drive_id, item["id"], run_id)
            count += 1

        # Recurse into folders
        if item.get("folder") and item["folder"].get("childCount", 0) > 0:
            count += _walk_drive_items(
                graph, neo4j, drive_id, item["id"], item_path,
                site_id, owner_email, tenant_domain, run_id,
            )

        graph.throttle()

    return count


def collect_onedrive_user(
    graph: GraphClient,
    neo4j: Neo4jClient,
    user: dict,
    run_id: str,
    tenant_domain: str,
) -> int:
    """Collect all sharing permissions for one user's OneDrive. Returns item count."""
    upn = user["userPrincipalName"]
    display_name = user["displayName"]
    user_id = user["id"]

    drive = graph.get_user_drive(user_id)
    if not drive:
        logger.warning(f"No OneDrive for {upn} — skipping.")
        return 0

    drive_id = drive["id"]
    site_id = f"onedrive-{user_id}"

    neo4j.merge_user(upn, display_name, "internal")
    neo4j.merge_site(site_id, display_name, drive.get("webUrl", ""), "OneDrive")
    neo4j.merge_owns(upn, site_id)

    count = _walk_drive_items(
        graph, neo4j, drive_id, "root", "",
        site_id, upn, tenant_domain, run_id,
    )

    logger.info(f"OneDrive {display_name} ({upn}): {count} shared items")
    return count
