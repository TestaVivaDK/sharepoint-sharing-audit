"""Permission processors for different permission types."""

import logging
import uuid

from collector.graph_client import GraphClient
from shared.neo4j_client import Neo4jClient
from collector.user_cache import UserCache
from collector.neo4j_user_node import Neo4jUserNode
from shared.classify import (
    get_risk_level,
    determine_user_source,
)
from typing import Optional

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
    
    # Extract group from permission
    logger.info("PROCESS GROUP PERMISSION")

    granted = permission.get("grantedToV2", {})
    group_dict = granted.get("group") or granted.get("siteGroup")
    group_id = group_dict.get("id", "")
    group_name = group_dict.get("displayName", "")
    group_type = "siteGroup" if "siteGroup" in permission.get("grantedToV2", {}) else "Group"

    # if(ignore_sharepoint_groups):
    #     group_dict = perm.get("grantedToV2", {}).get("group")
    # else:
    #     group_dict = perm.get("grantedToV2", {}).get("group") or perm.get("grantedToV2", {}).get("siteGroup")
    
    if (not group_dict) or group_name  in ["SharePoint Administrator", "Global Administrator"]: #and not (group_dict.get("displayName", "").lower() in ["sharepoint"] if ignore_sharepoint_groups else False):
        return
 

    #roup_name = group_dict.get("displayName", "Unknown Group")
    
    
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


def _walk_group_members(
        graph: GraphClient,
        user_cache: UserCache,
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
        (including guests) at any nesting level. Uses user cache to determine
        user source based on Graph API userType field. Handles circular group
        references and enforces a maximum recursion depth.
        
        Args:
            graph: GraphClient instance.
            user_cache: UserCache for efficient user lookups.
            neo4j: Neo4jClient for database operations.
            group_id: The group ID to retrieve members for.
            run_id: Audit run ID.
            visited_groups: Set of group IDs already processed (for circular refs).
            depth: Current recursion depth.
            max_depth: Maximum recursion depth to prevent infinite loops.
            
        Returns:
            bool: True if group or nested groups contain any Guest/External users.
                
        Example:
            >>> cache = UserCache(graph_client)
            >>> has_external = _walk_group_members(graph, cache, neo4j, "group-id", "run-123")
        """
        if visited_groups is None:
            visited_groups = set()
        
        if group_id in visited_groups:
            logger.debug(f"Group {group_id} already processed, skipping (circular ref)")
            return False
        
        if depth > max_depth:
            logger.warning(
                f"Maximum recursion depth ({max_depth}) reached for group {group_id}"
            )
            return False
        
        visited_groups.add(group_id)
        has_external = False
        
        try:
            logger.debug(f"Retrieving members for group {group_id} (depth {depth})")
            members = graph.get_group_members(group_id)
            
            # Pre-populate cache with member IDs to minimize API calls
            member_ids = [m.get("id") for m in members if m.get("id")]
            if member_ids:
                user_cache.batch_populate(member_ids)
            
            for member in members:
                member_id = member.get("id")
                member_display_name = member.get("displayName", "Unknown")
                
                # Fetch full user data from cache to ensure we have userType and identities
                user_data = user_cache.get(member_id)
                # if not user_data:
                #     user_data = member  # Use directly fetched member data
                
                # Check if this is a user (not a nested group)
                if member.get("@odata.type") != "#microsoft.graph.group":
                    # This is a user (Member or Guest)
                    try:
                        member_node = Neo4jUserNode(user_data)
                        
                        if member_node.is_guest:
                            has_external = True
                        
                        logger.debug(
                            f"Found {member_node.source} member: {member_display_name} "
                            f"(depth {depth})"
                        )
                        
                        member_node.merge_as_group_member(neo4j, group_id, run_id)
                    except ValueError as e:
                        logger.warning(f"Invalid user data for group member: {e}")
                        continue
                else:
                    # This is a nested group
                    logger.debug(
                        f"Expanding nested group: {member_display_name} "
                        f"at depth {depth}"
                    )
                    try:
                        neo4j.merge_nested_group(group_id, member_id, member_display_name, "Group", run_id)

                    except ValueError as e:
                        logger.warning(f"Invalid group data for nested group: {e}")
                        continue
                    
                    # Recursively process nested group
                    nested_has_external = _walk_group_members(
                        graph,
                        user_cache,
                        neo4j,
                        member_id,
                        run_id,
                        visited_groups,
                        depth + 1,
                        max_depth,
                    )
                    has_external = has_external or nested_has_external
            
            # Cache result to avoid processing same group multiple times
            processed_groups[group_id] = {"has_guests": has_external}
            return has_external
     
        except Exception:
            logger.exception(
                f"Error recursively retrieving members for group {group_id}"
            )
            return False
