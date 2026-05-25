"""Delta scan: process only changed items for a drive."""

import logging

from collector.graph_client import GraphClient
from shared.neo4j_client import Neo4jClient
from collector.user_cache import UserCache
from collector.permission_processors import (
    process_user_permission,
    process_group_permission,
    process_link_permission,
)
from shared.classify import (
    get_sharing_type,
    get_risk_level,
    get_permission_role,
    get_granted_by,
    determine_user_source,
)

logger = logging.getLogger(__name__)


def _item_path_from_delta(item: dict) -> str:
    """Extract file path from a delta response item.

    Delta items have parentReference.path like '/drive/root:/Folder/Sub'
    and a name field. Combine them to get the relative path.
    """
    parent_ref = item.get("parentReference", {})
    parent_path = parent_ref.get("path", "")
    # Strip the /drive/root: prefix
    if ":/" in parent_path:
        parent_path = parent_path.split(":/", 1)[1]
    elif parent_path.endswith(":"):
        parent_path = ""
    else:
        parent_path = ""
    name = item.get("name", "")
    if parent_path:
        return f"/{parent_path}/{name}"
    return f"/{name}"


def delta_scan_drive(
    graph: GraphClient,
    user_cache: UserCache,
    neo4j: Neo4jClient,
    drive_id: str,
    delta_link: str,
    site_id: str,
    owner_email: str,
    tenant_domain: str,
    run_id: str,
) -> int:
    """Process delta changes for a single drive. Returns count of shared items found."""
    items, new_delta_link = graph.get_drive_delta(delta_link)
    logger.info(f"  Delta returned {len(items)} changed items")

    count = 0
    for item in items:
        item_id = item["id"]

        # Handle deleted items
        if item.get("deleted"):
            neo4j.remove_file_permissions(drive_id, item_id, run_id)
            continue

        item_path = _item_path_from_delta(item)
        item_type = "Folder" if item.get("folder") else "File"
        web_url = item.get("webUrl", "")

        # Content-only change: update file metadata and relationships
        if not item.get("@microsoft.graph.sharedChanged"):
            neo4j.merge_file(drive_id, item_id, item_path, web_url, item_type)
            neo4j.merge_contains(site_id, drive_id, item_id)
            neo4j.mark_file_found(drive_id, item_id, run_id)
            continue

        # Permission change: re-fetch and re-merge
        try:
            permissions = graph.get_item_permissions(drive_id, item_id)
        except Exception as e:
            logger.warning(f"Could not get permissions for {item_path}: {e}")
            permissions = []

        for perm in permissions:
            sharing_type = get_sharing_type(perm)
            role = get_permission_role(perm)
            granted_by = get_granted_by(perm) or owner_email
            
            # Build item metadata dict for permission processors
            item_metadata = {
                "site_id": site_id,
                "drive_id": drive_id,
                "item_id": item_id,
                "item_path": item_path,
                "web_url": web_url,
                "file_type": item_type,
                "sharing_type": sharing_type,
                "role": role,
                "granted_by": granted_by,
                "tenant_domain": tenant_domain,
            }
            
            # Dispatch to appropriate processor based on permission type
            if "link" in perm:
                process_link_permission(perm, graph, user_cache, neo4j, item_metadata, run_id)
                count += 1
            
            elif perm.get("grantedToV2", {}).get("group") or perm.get("grantedToV2", {}).get("siteGroup"):
                process_group_permission(perm, graph, user_cache, neo4j, item_metadata, run_id)
                count += 1
            
            elif perm.get("grantedToV2", {}).get("user") or perm.get("grantedTo", {}).get("user"):
                # Skip owner's own "owner" permission
                user_dict = perm.get("grantedToV2", {}).get("user") or perm.get("grantedTo", {}).get("user")
                user_email = user_dict.get("email", "")
                if not (role == "Owner" and user_email == owner_email):
                    process_user_permission(perm, graph, user_cache, neo4j, item_metadata, run_id)
                    count += 1

    # Save the new delta link for next scan
    if new_delta_link:
        neo4j.save_delta_link(drive_id, new_delta_link)
    else:
        logger.warning(f"No delta link returned for drive {drive_id}")
    return count
