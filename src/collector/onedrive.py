"""OneDrive collection: enumerate users, walk drives, collect permissions."""

import logging

import httpx

from collector.graph_client import GraphClient
from shared.neo4j_client import Neo4jClient
from collector.neo4j_user_node import Neo4jUserNode
from shared.classify import (
    get_sharing_type,
    get_risk_level,
    get_permission_role,
    get_granted_by,
    determine_user_source,
)
from collector.permission_processors import (
    process_user_permission,
    process_group_permission,
    process_link_permission,
    processed_groups,
)
from collector.delta import delta_scan_drive
from collector.user_cache import UserCache
from typing import Any, Dict, List, Optional, NoReturn

logger = logging.getLogger(__name__)

def _walk_drive_items(
    graph: GraphClient,
    user_cache: UserCache,
    neo4j: Neo4jClient,
    drive_id: str,
    parent_id: str,
    parent_path: str,
    site_id: str,
    owner_email: str,
    tenant_domain: str,
    run_id: str,
    ignore_sharepoint_groups: bool = False
) -> int:
    """Recursively walk drive items, collect permissions, write to Neo4j. Returns count."""
    count = 0
    try:
        children = graph.get_drive_children(drive_id, parent_id)
    except Exception as e:
        logger.warning(f"Could not list children of {parent_path}: {e}")
        return 0

    # Process only folders and shared items to improve performance and avoid hitting Microsoft Graph service-specific throttling limits
    items_to_process = []
    for item in children:
        if "shared" in item: 
            items_to_process.append(item)
            count += 1

        # A folder need to be walked but can also be shared
        if "folder" in item:
            # Recurse into folder
            item_path = (f"{parent_path}/{item['name']}" if parent_path else f"/{item['name']}")
            if item.get("folder") and item["folder"].get("childCount", 0) > 0:
                count += _walk_drive_items(
                    graph,
                    user_cache,
                    neo4j,
                    drive_id,
                    item["id"],
                    item_path,
                    site_id,
                    owner_email,
                    tenant_domain,
                    run_id,
                    ignore_sharepoint_groups,
                )
        
    
    # Batch process items to benefit from Microsoft Graph API JSON batching capability and improve performance
    chunked_items = chunks(items_to_process, 20)
    for chunk in chunked_items:
        _batch_process_items_permissions(
            graph,
            user_cache,
            neo4j,
            chunk,
            drive_id,
            parent_id,
            parent_path,
            site_id,
            owner_email,
            tenant_domain,
            run_id,
            ignore_sharepoint_groups,
            )
        
    return count


def _batch_process_items_permissions(
    graph: GraphClient,
    user_cache: UserCache,
    neo4j: Neo4jClient,
    chunk: List,
    drive_id: str,
    parent_id: str,
    parent_path: str,
    site_id: str,
    owner_email: str,
    tenant_domain: str,
    run_id: str,
    ignore_sharepoint_groups: bool = False,
) -> None:
    """Process permissions for a batch of drive items.
    
    Dispatches each permission to the appropriate processor:
    - Link-based permissions -> process_link_permission()
    - Group permissions -> process_group_permission()
    - User permissions -> process_user_permission()
    
    Args:
        graph: GraphClient instance.
        user_cache: UserCache for efficient user lookups.
        neo4j: Neo4jClient for database operations.
        chunk: List of drive items to process.
        drive_id, parent_id, parent_path, site_id: Drive/location context.
        owner_email: Email of drive/site owner.
        tenant_domain: Tenant domain for user classification.
        run_id: Audit run ID.
        ignore_sharepoint_groups: Whether to skip SharePoint groups.
    """
    result = graph.batch_get_item_permissions(drive_id, chunk)
    
    for batch_item in result.values():
        item = batch_item["data"]
        item_path = (
            f"{parent_path}/{item['name']}" if parent_path else f"/{item['name']}"
        )
        item_type = "Folder" if item.get("folder") else "File"
        web_url = item.get("webUrl", "")

        try:
            permissions = batch_item["permissions"]
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
                "item_id": item["id"],
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
            
            elif perm.get("grantedToV2", {}).get("group") or perm.get("grantedToV2", {}).get("siteGroup"):
                # Skip SharePoint default groups if requested
                logger.info(perm)
                process_group_permission(perm, graph, user_cache, neo4j, item_metadata, run_id, ignore_sharepoint_groups)
            
            elif perm.get("grantedToV2", {}).get("user") or perm.get("grantedTo", {}).get("user"):
                # Skip owner's own "owner" permission
                user_dict = perm.get("grantedToV2", {}).get("user") or perm.get("grantedTo", {}).get("user")
                user_email = user_dict.get("email", "")
                if not (role == "Owner" and user_email == owner_email):
                    process_user_permission(perm, user_cache, neo4j, item_metadata, run_id)
            
            else:
                logger.warning(f"Unable to classify permission for {item_path}: {perm}")



def collect_onedrive_user(
    graph: GraphClient,
    user_cache: UserCache,
    neo4j: Neo4jClient,
    user: dict,
    run_id: str,
    tenant_domain: str,
    is_full: bool = True,
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

    # Create user node and establish ownership
    user_node = Neo4jUserNode(user, source="Internal")  # Drive owner is always internal
    neo4j.merge_site(site_id, display_name, drive.get("webUrl", ""), "OneDrive")
    user_node.merge_as_site_owner(neo4j, site_id)

    if is_full:
        count = _walk_drive_items(
            graph,
            user_cache,
            neo4j,
            drive_id,
            "root",
            "",
            site_id,
            upn,
            tenant_domain,
            run_id,
        )
        # Seed delta link for next scan
        try:
            link = graph.seed_delta_link(drive_id)
            neo4j.save_delta_link(drive_id, link)
        except Exception as e:
            logger.warning(f"Could not seed delta link for {upn}: {e}")
    else:
        delta_link = neo4j.get_delta_link(drive_id)
        if delta_link:
            try:
                count = delta_scan_drive(
                    graph,
                    user_cache,
                    neo4j,
                    drive_id,
                    delta_link,
                    site_id,
                    upn,
                    tenant_domain,
                    run_id,
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (410, 404):
                    logger.warning(
                        f"Delta link expired for {upn}, falling back to full walk"
                    )
                    count = _walk_drive_items(
                        graph,
                        user_cache,
                        neo4j,
                        drive_id,
                        "root",
                        "",
                        site_id,
                        upn,
                        tenant_domain,
                        run_id,
                    )
                    try:
                        link = graph.seed_delta_link(drive_id)
                        neo4j.save_delta_link(drive_id, link)
                    except Exception as ex:
                        logger.warning(f"Could not seed delta link for {upn}: {ex}")
                else:
                    raise
        else:
            logger.info(f"  No delta link for {upn} — falling back to full walk")
            count = _walk_drive_items(
                graph,
                user_cache,
                neo4j,
                drive_id,
                "root",
                "",
                site_id,
                upn,
                tenant_domain,
                run_id,
            )
            try:
                link = graph.seed_delta_link(drive_id)
                neo4j.save_delta_link(drive_id, link)
            except Exception as e:
                logger.warning(f"Could not seed delta link for {upn}: {e}")

    logger.info(f"OneDrive {display_name} ({upn}): {count} shared items")
    return count


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]