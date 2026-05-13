"""OneDrive collection: enumerate users, walk drives, collect permissions."""

import logging

import httpx
import uuid

from collector.graph_client import GraphClient
from shared.neo4j_client import Neo4jClient
from shared.classify import (
    get_sharing_type,
    get_shared_with_info,
    get_risk_level,
    get_permission_role,
    get_granted_by,
)
from collector.delta import delta_scan_drive
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Cache to avoid processing a group multiple times
processed_groups = {}

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

    # Process only folders and shared items to improve performance and avoid hitting Microsoft Graph service-specific throttling limits
    items_to_process = []
    for item in children:
        if "shared" in item: 
            items_to_process.append(item)
            
        elif "folder" in item:
            # Recurse into folder
            item_path = (f"{parent_path}/{item['name']}" if parent_path else f"/{item['name']}")
            if item.get("folder") and item["folder"].get("childCount", 0) > 0:
                count += _walk_drive_items(
                    graph,
                    neo4j,
                    drive_id,
                    item["id"],
                    item_path,
                    site_id,
                    owner_email,
                    tenant_domain,
                    run_id,
                )
    
    # Batch process items to benefit from Microsoft Graph API JSON batching capability and improve performance
    chunked_items = chunks(items_to_process, 20)
    for chunk in chunked_items:
        count += _batch_process_items_permissions(
            graph,
            neo4j,
            chunk,
            drive_id,
            parent_id,
            parent_path,
            site_id,
            owner_email,
            tenant_domain,
            run_id,
            )
        
    return count


def _batch_process_items_permissions(
    graph: GraphClient,
    neo4j: Neo4jClient,
    chunk: List,
    drive_id: str,
    parent_id: str,
    parent_path: str,
    site_id: str,
    owner_email: str,
    tenant_domain: str,
    run_id: str
) -> int:
    count = 0
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
            
            if sharing_type == "Link-SpecificPeople" or (sharing_type == "Link-Organization" and "grantedToIdentitiesV2" in perm):
                _walk_link_permission(
                    graph=graph,
                    neo4j=neo4j,
                    site_id=site_id,
                    drive_id=drive_id,
                    tenant_domain=tenant_domain,
                    item_id=item["id"],
                    item_path=item_path,
                    web_url=web_url,
                    file_type=item_type,
                    sharing_type=sharing_type,
                    role=role,
                    permission=perm,
                    run_id=run_id,
                    granted_by=granted_by,
                )
                
            else:
                shared_info = get_shared_with_info(perm, tenant_domain)
                risk = get_risk_level(
                    sharing_type, shared_info["shared_with_type"], item_path
                )

                # Skip owner's own "owner" permission
                if role == "Owner" and shared_info["shared_with"] == owner_email:
                    continue

                # Determine the "shared with" email for the User node
                shared_email = shared_info["shared_with"]
                if shared_info["shared_with_type"] == "Anonymous":
                    shared_email = "anonymous"
                elif sharing_type == "Link-Organization":
                    shared_email = "organization"

                # Detect invalid user or group ID which might be related with detected Entra ID objects.
                if "id" not in shared_info:
                    logger.error(f"NO ID DETECTED: {item_path} {perm}")
                    continue
                elif len(shared_info["id"]) == 0 or not is_valid_uuid(shared_info["id"]):
                    logger.warning(f"INVALID UUID DETECTED: {item_path} {perm}")
                    if "@" in shared_email:
                        found_id = graph.get_user_id(shared_email)
                        if len(shared_info["id"]) > 0 and is_valid_uuid(shared_info["id"]):
                            shared_info["id"] = found_id 
                            logger.info(f"FOUND ID {found_id} for user {shared_email}")
                        else:
                            continue

                if shared_info["shared_with_type"] == "Group" and shared_info["shared_with"] not in ["SharePoint Administrator", "Global Administrator"]:
                    # Create group node first without linking it to the file node
                    group_id = shared_info["id"]
                    neo4j.merge_group(group_id, shared_info["shared_with"], shared_info["shared_with_type"])
                    
                    # Walk group members and nested groups, return True if Guest or External users found
                    if group_id in processed_groups:
                        group_has_guests = processed_groups[group_id]["has_guests"]
                    else:
                        logger.debug(f"DRIVE ITEM - walk_group_members as group {group_id} not found in {processed_groups}")
                        group_has_guests = _walk_group_members(graph, neo4j, group_id, run_id)
                    
                    # Increase risk level if Guest or External users found in group or nested groups
                    if group_has_guests:
                        risk = "HIGH"
                    
                neo4j.merge_permission(
                    site_id=site_id,
                    drive_id=drive_id,
                    item_id=item["id"],
                    item_path=item_path,
                    web_url=web_url,
                    file_type=item_type,
                    user_email=shared_email,
                    user_id=shared_info["id"] if "id" in shared_info else "-",
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


        graph.throttle()

    return count


def collect_onedrive_user(
    graph: GraphClient,
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

    neo4j.merge_user(user_id, upn, display_name, "internal")
    neo4j.merge_site(site_id, display_name, drive.get("webUrl", ""), "OneDrive")
    neo4j.merge_owns(upn, site_id)

    if is_full:
        count = _walk_drive_items(
            graph,
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


def _walk_group_members(
        graph: GraphClient,
        neo4j: Neo4jClient,
        group_id: str,
        run_id: str,
        visited_groups: Optional[set] = None,
        depth: int = 0,
        max_depth: int = 10,
    ) -> bool:
        """
        Recursively retrieve all members of a group, expanding nested groups.
        
        Traverses the group membership hierarchy to find all individual users
        (including guests) at any nesting level. Handles circular group references
        and enforces a maximum recursion depth.
        
        Args:
            group_id: The group ID to retrieve members for.
            visited_groups: Set of group IDs already processed (for circular refs).
            depth: Current recursion depth.
            max_depth: Maximum recursion depth to prevent infinite loops.
            
        Returns:
            Bool: Returns if group or nested groups contains Guest.
                
        Example:
            >>> api = SharePointGraphAPI(client)
            >>> all_members = api.get_group_members_recursive("group-id")
            >>> guests = [m for m in all_members if m.get('userType') == 'Guest']
        """
        if visited_groups is None:
            visited_groups = set()
        
        if group_id in visited_groups:
            logger.debug(f"Group {group_id} already processed, skipping (circular ref)")
            return {"has_guests": False}
        
        if depth > max_depth:
            logger.warning(
                f"Maximum recursion depth ({max_depth}) reached for group {group_id}"
            )
            return {"has_guests": False}
        
        visited_groups.add(group_id)
        has_guests = False
        
        try:
            logger.debug(f"Retrieving members for group {group_id} (depth {depth})")
            members = graph.get_group_members(group_id)
            
            for member in members:
                member_id = member.get("id")
                member_type = member.get("userType")
                member_email = member.get("userPrincipalName")
                member_display_name = member.get("displayName")
                
                if member_type == "Guest":
                    has_guests = True
                    logger.debug(
                        f"Found guest: {member.get('displayName')} "
                        f"(depth {depth})"
                    )
                    neo4j.merge_group_member(group_id, member_email, member_id, member_display_name, "External", run_id)


                elif member_type == "Member":
                    logger.debug(
                        f"Found member: {member.get('displayName')} "
                        f"(depth {depth})"
                    )
                    neo4j.merge_group_member(group_id, member_email, member_id, member_display_name, "Internal", run_id)

                elif member.get("@odata.type") == "#microsoft.graph.group":
                    logger.debug(
                        f"Expanding nested group: {member.get('displayName')} "
                        f"at depth {depth}"
                    )
                    neo4j.merge_nested_group(group_id, member_id, member_display_name, "Group", run_id)
                    
                    nested_group_result = _walk_group_members(
                        graph,
                        neo4j,
                        member_id,
                        run_id,
                        visited_groups,
                        depth + 1,
                        max_depth,
                    )
                    has_guests = has_guests or nested_group_result["has_guests"]
            
            result = {
                "has_guests": has_guests,
            }
            
            # Add processed group to result to avoid processing it multiple times.
            processed_groups[group_id] = result
            return result
     
        except Exception as e:
            logger.error(
                f"Error recursively retrieving members for group {group_id}: {e}"
            )
            return False

        
def _walk_link_permission(
    graph: GraphClient,
    neo4j: Neo4jClient,
    tenant_domain: str,
    site_id: str,
    drive_id: str,
    item_id: str,
    item_path: str,
    web_url: str,
    file_type: str,
    sharing_type: str,
    permission: dict,
    role: str,
    run_id: str,
    granted_by: str = ""
) -> List[Dict[str, Any]]:
    """Extract individual users targeted by sharing link, write to Neo4j."""
    #logger.info(f"SHARED WITH LINK: {permission}")
    link = permission.get("link")
    if link:
        identities_v2 = permission.get("grantedToIdentitiesV2", [])
        if identities_v2:
            for identity in identities_v2:
                if "user" in identity:
                    user = identity.get("user", {})
                    id = user.get("id", "")
                    email = user.get("email", "")
                    display = user.get("displayName", "")
                    
                    is_guest = "#EXT#" in email
                    is_external = tenant_domain and not email.endswith(f"@{tenant_domain}")
    
                    if is_guest:
                        shared_with_type = "Guest"
                    elif is_external:
                        shared_with_type = "External"
                    else:
                        shared_with_type = "Internal"
                    
                    risk = get_risk_level(
                        sharing_type, shared_with_type, item_path
                    )
                    
                    # Detect invalid user ID which might be related with detected Entra ID objects.
                    if len(id) == 0 or not is_valid_uuid(id):
                        logger.warning(f"INVALID UUID DETECTED: {item_path} {permission}")
                        return
                    
                    neo4j.merge_permission(
                        site_id=site_id,
                        drive_id=drive_id,
                        item_id=item_id,
                        item_path=item_path,
                        web_url=web_url,
                        file_type=file_type,
                        user_email=email,
                        user_id=id,
                        user_display_name=display,
                        user_source=shared_with_type,
                        sharing_type=sharing_type,
                        shared_with_type=shared_with_type,
                        role=role,
                        risk_level=risk,
                        created_date_time=permission.get("createdDateTime", ""),
                        run_id=run_id,
                        granted_by=granted_by,
                    )
                       
                elif "group" in identity:
                    group = identity.get("group", {})
                    id = group.get("id", "")
                    email = group.get("email", "")
                    display = group.get("displayName", "Unknown Group")
                    shared_with_type = "Group"
                    
                    # Detect invalid group ID which might be related with detected Entra ID objects.
                    if len(id) == 0 or not is_valid_uuid(id):
                        logger.warning(f"INVALID UUID DETECTED: {item_path} {permission}")
                        return
                    
                    # Create group node first without linking it
                    neo4j.merge_group(id, display, shared_with_type)
                    
                    # Walk group members and nested groups, return True if Guest or External users found
                    if id in processed_groups:
                        logger.info(f"LINK - group {id} already processed")
                        group_has_guests = processed_groups[id]["has_guests"]
                    else:
                        logger.info(f"LINK - walk_group_members as group {id} not found in {processed_groups}")
                        group_has_guests = _walk_group_members(graph, neo4j, id, run_id)
                    
                    # Set risk level    
                    risk = get_risk_level(
                        sharing_type, shared_with_type, item_path
                    )
                    # Increase risk level if Guest or External users found in group or nested groups
                    if group_has_guests:
                        risk = "HIGH"
                    
                    neo4j.merge_permission(
                        site_id=site_id,
                        drive_id=drive_id,
                        item_id=item_id,
                        item_path=item_path,
                        web_url=web_url,
                        file_type=file_type,
                        user_email=email,
                        user_id=id,
                        user_display_name=display,
                        user_source=shared_with_type,
                        sharing_type=sharing_type,
                        shared_with_type=shared_with_type,
                        role=role,
                        risk_level=risk,
                        created_date_time=permission.get("createdDateTime", ""),
                        run_id=run_id,
                        granted_by=granted_by,
                    )


def is_valid_uuid(uuid_to_test, version=4):
    try:
        # check for validity of Uuid
        uuid.UUID(uuid_to_test, version=version)
    except ValueError:
        return False
    return True


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]