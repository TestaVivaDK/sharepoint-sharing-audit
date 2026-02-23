"""Delta scan: process only changed items for a drive."""

import logging

from collector.graph_client import GraphClient
from shared.neo4j_client import Neo4jClient
from shared.classify import (
    get_sharing_type,
    get_shared_with_info,
    get_risk_level,
    get_permission_role,
    get_granted_by,
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

        # Content-only change: just update file metadata
        if not item.get("@microsoft.graph.sharedChanged"):
            neo4j.merge_file(drive_id, item_id, item_path, web_url, item_type)
            continue

        # Permission change: re-fetch and re-merge
        try:
            permissions = graph.get_item_permissions(drive_id, item_id)
        except Exception as e:
            logger.warning(f"Could not get permissions for {item_path}: {e}")
            permissions = []

        for perm in permissions:
            sharing_type = get_sharing_type(perm)
            shared_info = get_shared_with_info(perm, tenant_domain)
            role = get_permission_role(perm)
            granted_by = get_granted_by(perm) or owner_email
            risk = get_risk_level(
                sharing_type, shared_info["shared_with_type"], item_path
            )

            if role == "Owner" and shared_info["shared_with"] == owner_email:
                continue

            shared_email = shared_info["shared_with"]
            if shared_info["shared_with_type"] == "Anonymous":
                shared_email = "anonymous"
            elif sharing_type == "Link-Organization":
                shared_email = "organization"

            neo4j.merge_permission(
                site_id=site_id,
                drive_id=drive_id,
                item_id=item_id,
                item_path=item_path,
                web_url=web_url,
                file_type=item_type,
                user_email=shared_email,
                user_display_name=shared_info["shared_with"],
                user_source=shared_info["shared_with_type"],
                sharing_type=sharing_type,
                shared_with_type=shared_info["shared_with_type"],
                role=role,
                risk_level=risk,
                created_date_time=perm.get("createdDateTime", ""),
                run_id=run_id,
                granted_by=granted_by,
            )
            count += 1

    # Save the new delta link for next scan
    neo4j.save_delta_link(drive_id, new_delta_link)
    return count
