"""Permission processors for different permission types."""

import logging
import uuid

from collector.graph_client import GraphClient
from shared.neo4j_client import Neo4jClient
from collector.user_cache import UserCache
from collector.neo4j_user_node import Neo4jUserNode
from collector.neo4j_group_node import Neo4jGroupNode
from collector.group_cache import GroupMembershipCache
from shared.classify import (
    get_risk_level,
    determine_user_source,
)
from typing import Optional

logger = logging.getLogger(__name__)

# Cache to track processed groups (prevent duplicate processing)
processed_groups = GroupMembershipCache()


def is_valid_uuid(uuid_to_test: str, version: int = 4) -> bool:
    """Check if a string is a valid UUID."""
    try:
        uuid.UUID(uuid_to_test, version=version)
    except ValueError:
        return False
    return True


def process_user_permission(
    permission: dict,
    user_cache: UserCache,
    neo4j: Neo4jClient,
    item_metadata: dict,
    run_id: str,
) -> None:
    """Process a direct user permission and merge into Neo4j.
    
    Args:
        permission: Permission dict from Graph API (grantedToV2.user or grantedTo.user).
        user_cache: UserCache for efficient user lookups.
        neo4j: Neo4jClient for database operations.
        item_metadata: Dict with {site_id, drive_id, item_id, item_path, web_url, file_type, 
                                   sharing_type, role, run_id, granted_by, tenant_domain}.
        run_id: Audit run ID.
    """
    # Extract user from permission
    logger.info("PROCESS USER PERMISSION")
    user_dict = permission.get("grantedToV2", {}).get("user") or permission.get("grantedTo", {}).get("user")
    if not user_dict:
        return
    
    user_id = user_dict.get("id", "")
    # email = user_dict.get("email", "")
    # display_name = user_dict.get("displayName", "Unknown User")
    
    if not user_id or not is_valid_uuid(user_id):
        logger.warning(f"Invalid user ID in permission: {user_dict} for {item_metadata['item_path']}")
        return
    
    # Fetch full user data via cache to get userType and identities
    user_data = user_cache.get(user_id)
    # if not user_data:
    #     # Lazy-load if cache is empty
    #     user_data = {
    #         "id": user_id,
    #         "email": email,
    #         "displayName": display_name,
    #         "userType": "Member",
    #         "identities": [],
    #     }
    
    # Create Neo4jUserNode for processing
    try:
        user_node = Neo4jUserNode(user_data, tenant_domain=item_metadata.get("tenant_domain", ""))
    except ValueError as e:
        logger.warning(f"Invalid user for permission: {e}")
        return
    
    # Risk assessment
    risk = get_risk_level(
        item_metadata["sharing_type"],
        user_node.source,
        item_metadata["item_path"]
    )
    
    # Merge as file permission recipient
    user_node.merge_as_file_permission_recipient(
        neo4j,
        site_id=item_metadata["site_id"],
        drive_id=item_metadata["drive_id"],
        item_id=item_metadata["item_id"],
        item_path=item_metadata["item_path"],
        web_url=item_metadata["web_url"],
        file_type=item_metadata["file_type"],
        sharing_type=item_metadata["sharing_type"],
        role=item_metadata["role"],
        risk_level=risk,
        created_date_time=permission.get("createdDateTime", ""),
        run_id=run_id,
        granted_by=item_metadata.get("granted_by", ""),
    )


def process_group_permission(
    permission: dict,
    graph: GraphClient,
    user_cache: UserCache,
    neo4j: Neo4jClient,
    item_metadata: dict,
    run_id: str,
    ignore_sharepoint_groups: bool=False
) -> None:
    """Process a direct group permission and merge into Neo4j.
    
    Enumerates group members using Neo4jGroupNode and determines if any are guests/external.
    Updates risk level accordingly.
    
    Args:
        permission: Permission dict from Graph API (grantedToV2.group or grantedToV2.siteGroup).
        graph: GraphClient instance.
        user_cache: UserCache for efficient user lookups.
        neo4j: Neo4jClient for database operations.
        item_metadata: Dict with {site_id, drive_id, item_id, item_path, web_url, file_type,
                                   sharing_type, role, run_id, granted_by}.
        run_id: Audit run ID.
    """
    # Extract group from permission
    granted = permission.get("grantedToV2", {})
    group_dict = granted.get("group") or granted.get("siteGroup")
    group_id = group_dict.get("id", "")
    group_name = group_dict.get("displayName", "")

    if(ignore_sharepoint_groups) and not permission.get("grantedToV2", {}).get("group"):
        return

    if (not group_dict) or group_name  in ["SharePoint Administrator", "Global Administrator"]: #and not (group_dict.get("displayName", "").lower() in ["sharepoint"] if ignore_sharepoint_groups else False):
        return
    
    if not group_id or not is_valid_uuid(group_id):
        logger.debug(f"Invalid group ID in permission: {group_id} for {item_metadata['item_path']}")
        return
    
    try:
        # Create Neo4jGroupNode from permission
        granted = permission.get("grantedToV2", {})
        group_dict = granted.get("group") or granted.get("siteGroup")
        group_type = "Group" if "group" in granted else "siteGroup"
        
        if not group_dict:
            raise ValueError("No group found in permission")
        
        group_node = Neo4jGroupNode(group_dict, graph, user_cache, processed_groups, group_type)

        # Merge as file permission recipient (handles enumeration, risk escalation, Neo4j ops)
        group_node.merge_as_file_permission_recipient(
            neo4j,
            site_id=item_metadata["site_id"],
            drive_id=item_metadata["drive_id"],
            item_id=item_metadata["item_id"],
            item_path=item_metadata["item_path"],
            web_url=item_metadata["web_url"],
            file_type=item_metadata["file_type"],
            sharing_type=item_metadata["sharing_type"],
            role=item_metadata["role"],
            created_date_time=permission.get("createdDateTime", ""),
            run_id=run_id,
            granted_by=item_metadata.get("granted_by", ""),
        )
    except ValueError as e:
        logger.warning(f"Invalid group in permission: {e}")
        return


def process_link_permission(
    permission: dict,
    graph: GraphClient,
    user_cache: UserCache,
    neo4j: Neo4jClient,
    item_metadata: dict,
    run_id: str,
) -> None:
    """Process a sharing link permission.
    
    For anonymous/organization links: merges permission as-is.
    For specific people links: expands grantedToIdentitiesV2 and processes each user/group.
    
    Args:
        permission: Permission dict from Graph API (with "link" field).
        graph: GraphClient instance.
        user_cache: UserCache for efficient user lookups.
        neo4j: Neo4jClient for database operations.
        item_metadata: Dict with {site_id, drive_id, item_id, item_path, web_url, file_type,
                                   sharing_type, role, run_id, granted_by, tenant_domain}.
        run_id: Audit run ID.
    """
    logger.info("PROCESS LINK PERMISSION")
    link = permission.get("link", {})
    scope = link.get("scope", "")
    
    # Handle anonymous and organization links
    if scope == "anonymous":
        risk = get_risk_level(item_metadata["sharing_type"], "Anonymous", item_metadata["item_path"])
        neo4j.merge_permission(
            site_id=item_metadata["site_id"],
            drive_id=item_metadata["drive_id"],
            item_id=item_metadata["item_id"],
            item_path=item_metadata["item_path"],
            web_url=item_metadata["web_url"],
            file_type=item_metadata["file_type"],
            user_email="anyone",
            user_id="-",
            user_display_name="Anyone with the link",
            user_source="Anonymous",
            sharing_type=item_metadata["sharing_type"],
            shared_with_type="Anonymous",
            role=item_metadata["role"],
            risk_level=risk,
            created_date_time=permission.get("createdDateTime", ""),
            run_id=run_id,
            granted_by=item_metadata.get("granted_by", ""),
        )
        return
    
    if scope == "organization":
        risk = get_risk_level(item_metadata["sharing_type"], "Internal", item_metadata["item_path"])
        neo4j.merge_permission(
            site_id=item_metadata["site_id"],
            drive_id=item_metadata["drive_id"],
            item_id=item_metadata["item_id"],
            item_path=item_metadata["item_path"],
            web_url=item_metadata["web_url"],
            file_type=item_metadata["file_type"],
            user_email="organization",
            user_id="-",
            user_display_name="All organization members",
            user_source="Internal",
            sharing_type=item_metadata["sharing_type"],
            shared_with_type="Internal",
            role=item_metadata["role"],
            risk_level=risk,
            created_date_time=permission.get("createdDateTime", ""),
            run_id=run_id,
            granted_by=item_metadata.get("granted_by", ""),
        )
    
    # Handle specific people links (scope=="users")
    identities = permission.get("grantedToIdentitiesV2", [])
    for identity in identities:
        # Process user in identity
        if "user" in identity:
            user_dict = identity.get("user", {})
            user_id = user_dict.get("id", "")
            # email = user_dict.get("email", "")
            # display_name = user_dict.get("displayName", "Unknown User")
            
            if not user_id or not is_valid_uuid(user_id):
                logger.warning(f"Invalid user ID in link identity: {user_id} for {item_metadata['item_path']}")
                continue
            
            # Fetch full user data via cache to get userType and identities
            user_data = user_cache.get(user_id)
            # if not user_data:
            #     user_data = {
            #         "id": user_id,
            #         "email": email,
            #         "displayName": display_name,
            #         "userType": "Member",
            #         "identities": [],
            #     }
            
            # Create Neo4jUserNode for processing
            try:
                user_node = Neo4jUserNode(user_data, tenant_domain=item_metadata.get("tenant_domain", ""))
            except ValueError as e:
                logger.warning(f"Invalid user in link identity: {e}")
                continue
            
            # Risk assessment
            risk = get_risk_level(item_metadata["sharing_type"], user_node.source, item_metadata["item_path"])
            
            # Merge as link recipient
            user_node.merge_as_link_recipient(
                neo4j,
                site_id=item_metadata["site_id"],
                drive_id=item_metadata["drive_id"],
                item_id=item_metadata["item_id"],
                item_path=item_metadata["item_path"],
                web_url=item_metadata["web_url"],
                file_type=item_metadata["file_type"],
                sharing_type=item_metadata["sharing_type"],
                role=item_metadata["role"],
                risk_level=risk,
                created_date_time=permission.get("createdDateTime", ""),
                run_id=run_id,
                granted_by=item_metadata.get("granted_by", ""),
            )
        
        # Process group in identity
        elif "group" in identity:
            group_dict = identity.get("group", {})
            group_id = group_dict.get("id", "")
            
            if not group_id or not is_valid_uuid(group_id):
                logger.warning(f"Invalid group ID in link identity: {group_id} for {item_metadata['item_path']}")
                continue
            
            
            try:
                # Create Neo4jGroupNode from link identity
                group_node = Neo4jGroupNode(
                    group_dict,
                    graph,
                    user_cache,
                    processed_groups,
                    group_type="Group",
                )

                # Merge as link recipient (handles enumeration, risk escalation, Neo4j ops)
                group_node.merge_as_link_recipient(
                    neo4j,
                    site_id=item_metadata["site_id"],
                    drive_id=item_metadata["drive_id"],
                    item_id=item_metadata["item_id"],
                    item_path=item_metadata["item_path"],
                    web_url=item_metadata["web_url"],
                    file_type=item_metadata["file_type"],
                    sharing_type=item_metadata["sharing_type"],
                    role=item_metadata["role"],
                    created_date_time=permission.get("createdDateTime", ""),
                    run_id=run_id,
                    granted_by=item_metadata.get("granted_by", ""),
                )
            except ValueError as e:
                logger.warning(f"Invalid group in link identity: {e}")
                continue