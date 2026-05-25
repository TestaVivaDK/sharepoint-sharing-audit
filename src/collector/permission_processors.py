"""Permission processors for different permission types."""

import logging
import uuid

from collector.graph_client import GraphClient
from shared.neo4j_client import Neo4jClient
from collector.user_cache import UserCache
from shared.classify import (
    get_risk_level,
    determine_user_source,
)

logger = logging.getLogger(__name__)

# Cache to track processed groups (prevent duplicate processing)
processed_groups = {}


def is_valid_uuid(uuid_to_test: str, version: int = 4) -> bool:
    """Check if a string is a valid UUID."""
    try:
        uuid.UUID(uuid_to_test, version=version)
    except ValueError:
        return False
    return True


def process_user_permission(
    permission: dict,
    graph: GraphClient,
    user_cache: UserCache,
    neo4j: Neo4jClient,
    item_metadata: dict,
    run_id: str,
) -> None:
    """Process a direct user permission and merge into Neo4j.
    
    Args:
        permission: Permission dict from Graph API (grantedToV2.user or grantedTo.user).
        graph: GraphClient instance.
        user_cache: UserCache for efficient user lookups.
        neo4j: Neo4jClient for database operations.
        item_metadata: Dict with {site_id, drive_id, item_id, item_path, web_url, file_type, 
                                   sharing_type, role, run_id, granted_by, tenant_domain}.
        run_id: Audit run ID.
    """
    # Extract user from permission
    user_dict = permission.get("grantedToV2", {}).get("user") or permission.get("grantedTo", {}).get("user")
    if not user_dict:
        return
    
    user_id = user_dict.get("id", "")
    email = user_dict.get("email", "")
    display_name = user_dict.get("displayName", "Unknown User")
    
    if not user_id or not is_valid_uuid(user_id):
        logger.warning(f"Invalid user ID in permission: {user_id} for {item_metadata['item_path']}")
        return
    
    # Fetch full user data via cache to get userType
    user_data = user_cache.get(user_id)
    if not user_data:
        # Lazy-load if cache is empty
        user_data = {
            "id": user_id,
            "email": email,
            "displayName": display_name,
            "userType": "Member",  # Default if not available
        }
    
    # Determine source using userType
    source = determine_user_source(user_data, item_metadata.get("tenant_domain", ""))
    
    # Risk assessment
    risk = get_risk_level(
        item_metadata["sharing_type"],
        source,
        item_metadata["item_path"]
    )
    
    # Merge user node
    neo4j.merge_user(user_id, email or display_name, display_name, source)
    
    # Merge permission
    neo4j.merge_permission(
        site_id=item_metadata["site_id"],
        drive_id=item_metadata["drive_id"],
        item_id=item_metadata["item_id"],
        item_path=item_metadata["item_path"],
        web_url=item_metadata["web_url"],
        file_type=item_metadata["file_type"],
        user_email=email or display_name,
        user_id=user_id,
        user_display_name=display_name,
        user_source=source,
        sharing_type=item_metadata["sharing_type"],
        shared_with_type=source,
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
) -> None:
    """Process a direct group permission and merge into Neo4j.
    
    Enumerates group members and determines if any are guests/external.
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
    # Defer import to avoid circular dependency
    from collector.onedrive import _walk_group_members
    
    # Extract group from permission
    group_dict = permission.get("grantedToV2", {}).get("group") or permission.get("grantedToV2", {}).get("siteGroup")
    if not group_dict:
        return
    
    group_id = group_dict.get("id", "")
    group_name = group_dict.get("displayName", "Unknown Group")
    group_type = "siteGroup" if "siteGroup" in permission.get("grantedToV2", {}) else "Group"
    
    if not group_id or not is_valid_uuid(group_id):
        logger.warning(f"Invalid group ID in permission: {group_id} for {item_metadata['item_path']}")
        return
    
    # Merge group node
    neo4j.merge_group(group_id, group_name, group_type)
    
    # Enumerate group members to check for guests/external
    group_has_external = False
    if group_id not in processed_groups:
        group_has_external = _walk_group_members(graph, user_cache, neo4j, group_id, run_id)
    else:
        group_has_external = processed_groups[group_id].get("has_guests", False)
    
    # Determine risk level
    risk = get_risk_level(
        item_metadata["sharing_type"],
        group_type,
        item_metadata["item_path"]
    )
    
    # Increase risk if group contains external/guest users
    if group_has_external:
        risk = "HIGH"
    
    # Merge permission
    neo4j.merge_permission(
        site_id=item_metadata["site_id"],
        drive_id=item_metadata["drive_id"],
        item_id=item_metadata["item_id"],
        item_path=item_metadata["item_path"],
        web_url=item_metadata["web_url"],
        file_type=item_metadata["file_type"],
        user_email=group_dict.get("email", group_name),
        user_id=group_id,
        user_display_name=group_name,
        user_source=group_type,
        sharing_type=item_metadata["sharing_type"],
        shared_with_type=group_type,
        role=item_metadata["role"],
        risk_level=risk,
        created_date_time=permission.get("createdDateTime", ""),
        run_id=run_id,
        granted_by=item_metadata.get("granted_by", ""),
    )


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
    # Defer import to avoid circular dependency
    from collector.onedrive import _walk_group_members
    
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
        return
    
    # Handle specific people links (scope=="users")
    identities = permission.get("grantedToIdentitiesV2", [])
    for identity in identities:
        # Process user in identity
        if "user" in identity:
            user_dict = identity.get("user", {})
            user_id = user_dict.get("id", "")
            email = user_dict.get("email", "")
            display_name = user_dict.get("displayName", "Unknown User")
            
            if not user_id or not is_valid_uuid(user_id):
                logger.warning(f"Invalid user ID in link identity: {user_id} for {item_metadata['item_path']}")
                continue
            
            # Fetch full user data via cache to get userType
            user_data = user_cache.get(user_id)
            if not user_data:
                user_data = {
                    "id": user_id,
                    "email": email,
                    "displayName": display_name,
                    "userType": "Member",
                }
            
            source = determine_user_source(user_data, item_metadata.get("tenant_domain", ""))
            risk = get_risk_level(item_metadata["sharing_type"], source, item_metadata["item_path"])
            
            # Merge user node
            neo4j.merge_user(user_id, email or display_name, display_name, source)
            
            # Merge permission
            neo4j.merge_permission(
                site_id=item_metadata["site_id"],
                drive_id=item_metadata["drive_id"],
                item_id=item_metadata["item_id"],
                item_path=item_metadata["item_path"],
                web_url=item_metadata["web_url"],
                file_type=item_metadata["file_type"],
                user_email=email or display_name,
                user_id=user_id,
                user_display_name=display_name,
                user_source=source,
                sharing_type=item_metadata["sharing_type"],
                shared_with_type=source,
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
            group_name = group_dict.get("displayName", "Unknown Group")
            
            if not group_id or not is_valid_uuid(group_id):
                logger.warning(f"Invalid group ID in link identity: {group_id} for {item_metadata['item_path']}")
                continue
            
            # Merge group node
            neo4j.merge_group(group_id, group_name, "Group")
            
            # Enumerate group members
            group_has_external = False
            if group_id not in processed_groups:
                group_has_external = _walk_group_members(graph, user_cache, neo4j, group_id, run_id)
            else:
                group_has_external = processed_groups[group_id].get("has_guests", False)
            
            # Determine risk
            risk = get_risk_level(item_metadata["sharing_type"], "Group", item_metadata["item_path"])
            if group_has_external:
                risk = "HIGH"
            
            # Merge permission
            neo4j.merge_permission(
                site_id=item_metadata["site_id"],
                drive_id=item_metadata["drive_id"],
                item_id=item_metadata["item_id"],
                item_path=item_metadata["item_path"],
                web_url=item_metadata["web_url"],
                file_type=item_metadata["file_type"],
                user_email=group_dict.get("email", group_name),
                user_id=group_id,
                user_display_name=group_name,
                user_source="Group",
                sharing_type=item_metadata["sharing_type"],
                shared_with_type="Group",
                role=item_metadata["role"],
                risk_level=risk,
                created_date_time=permission.get("createdDateTime", ""),
                run_id=run_id,
                granted_by=item_metadata.get("granted_by", ""),
            )
