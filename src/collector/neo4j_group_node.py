"""Neo4jGroupNode: Encapsulates Entra ID group processing for Neo4j operations."""

import logging
import uuid
from typing import Optional

from collector.graph_client import GraphClient
from collector.user_cache import UserCache
from collector.neo4j_user_node import Neo4jUserNode
from shared.neo4j_client import Neo4jClient
from shared.classify import get_risk_level

logger = logging.getLogger(__name__)


class Neo4jGroupNode:
    """
    Encapsulates Entra ID group object processing for Neo4j operations.
    
    Consolidates group data extraction, member enumeration (including nested groups),
    risk assessment, and all relationship operations (CONTAINS for members, SHARED_WITH
    for permissions) into a single, type-safe interface.
    
    Maintains a complete list of all group members (including recursively enumerated
    nested group members), enabling comprehensive auditing and access review.
    
    Usage:
        # From Entra ID group object
        group_node = Neo4jGroupNode(
            group_dict, 
            graph_client, 
            user_cache, 
            processed_groups_cache,
            group_type="Group"
        )
        
        # Enumerate members and process permissions
        has_external = group_node.enumerate_members(neo4j, run_id)
        group_node.merge_as_file_permission_recipient(neo4j, site_id, drive_id, ...)
        
        # Access full member list
        all_members = group_node.members  # [{id, email, displayName, source, ...}, ...]
    
    Attributes:
        group_id (str): Group unique identifier from Graph API.
        display_name (str): Group display name.
        email (str): Group email address (or display_name fallback).
        group_type (str): "Group" or "siteGroup".
        members (list[dict]): All group members (users and nested groups), recursively enumerated.
    """
    
    def __init__(
        self,
        group: dict,
        graph_client: GraphClient,
        user_cache: UserCache,
        processed_groups_cache: dict,
        group_type: Optional[str] = None,
    ):
        """
        Initialize from Entra ID group object.
        
        Args:
            group: Graph API group dict with fields:
                   {id, displayName, email, @odata.type}
            graph_client: GraphClient instance for member enumeration.
            user_cache: UserCache for efficient user lookups during enumeration.
            processed_groups_cache: GroupMembershipCache instance to track enumerated groups.
                                   Prevents re-enumeration across multiple permissions.
            group_type: Optional override for group type ("Group" or "siteGroup").
                       If None, inferred from group dict.
            
        Raises:
            ValueError: If group_id is invalid UUID.
            
        Example:
            >>> group = {
            ...     "id": "123e4567-e89b-12d3-a456-426614174000",
            ...     "displayName": "Engineering Team",
            ...     "email": "eng@example.com",
            ... }
            >>> group_node = Neo4jGroupNode(
            ...     group, graph_client, user_cache, processed_groups_cache
            ... )
        """
        # Extract attributes from group object
        self.group_id = group.get("id", "")
        self.display_name = group.get("displayName", "Unknown Group")
        self.email = group.get("email", "")
        
        # Validate UUID
        if not self._is_valid_uuid(self.group_id):
            raise ValueError(f"Invalid group ID: {self.group_id}")
        
        # Store dependencies
        self.graph_client = graph_client
        self.user_cache = user_cache
        self.processed_groups_cache = processed_groups_cache
        
        # Determine group type
        if group_type:
            self._group_type = group_type
        else:
            self._group_type = "siteGroup" if "siteGroup" in group else "Group"
        
        # Member enumeration state
        self._members: list[dict] = []
        self._has_external_members: bool = False
        self._enumeration_complete: bool = False
        
        logger.debug(
            f"Created Neo4jGroupNode: id={self.group_id}, displayName={self.display_name}, "
            f"type={self._group_type}"
        )
    
    @staticmethod
    def _is_valid_uuid(uuid_to_test: str, version: int = 4) -> bool:
        """
        Check if a string is a valid UUID.
        
        Args:
            uuid_to_test: String to validate.
            version: UUID version to check (default 4).
            
        Returns:
            True if valid UUID, False otherwise.
        """
        try:
            uuid.UUID(uuid_to_test, version=version)
            return True
        except ValueError:
            return False
    
    # Properties
    
    @property
    def has_enumerated_members(self) -> bool:
        """True if member enumeration has been completed for this group."""
        return self._enumeration_complete
    
    @property
    def has_external_members(self) -> bool:
        """
        True if group contains any External/Guest members.
        
        Requires enumerate_members() to have been called first.
        """
        if not self._enumeration_complete:
            raise RuntimeError(
                "Cannot access has_external_members before enumerate_members() called"
            )
        return self._has_external_members
    
    @property
    def members(self) -> list[dict]:
        """
        List of all group members (users and nested groups) recursively enumerated.
        
        Each member dict contains:
        {
            "id": user_id,
            "email": email,
            "displayName": display_name,
            "userType": "Member" or "Guest",
            "identities": [...],
            "source": "Internal", "External", or "Guest"
        }
        
        Empty list if enumerate_members() has not been called yet.
        """
        return self._members.copy()
    
    @property
    def group_type(self) -> str:
        """Returns group type: 'Group' or 'siteGroup'."""
        return self._group_type
    
    # Core Neo4j operations
    
    def merge_node(self, neo4j: Neo4jClient) -> None:
        """
        Merge Group node into Neo4j database.
        
        Creates or updates group node with id, displayName, and type.
        
        Args:
            neo4j: Neo4jClient instance.
            
        Example:
            >>> group_node.merge_node(neo4j)
            # Group node created in Neo4j with properties:
            # {id: "123e...", displayName: "Engineering Team", source: "Group"}
        """
        neo4j.merge_group(self.group_id, self.display_name, self._group_type)
    
    def enumerate_members(
        self,
        neo4j: Neo4jClient,
        run_id: str,
    ) -> bool:
        """
        Recursively enumerate all group members (users and nested groups).
        
        Public entry point that delegates to _walk_group_members() for the actual
        enumeration logic. Populates self._members list with all members found,
        including those from nested groups. Each member includes id, email, displayName,
        userType, identities, and source classification.
        
        Also creates Neo4j relationships:
        - (Group)-[CONTAINS]->(User) for direct member users
        - (Group)-[CONTAINS]->(Group) for nested groups
        - User nodes with proper classification
        
        Results are cached via processed_groups_cache to prevent re-enumeration of
        the same group across multiple permission contexts.
        
        Args:
            neo4j: Neo4jClient for database operations.
            run_id: Audit run ID for tracking membership across runs.
            
        Returns:
            bool: True if group or any nested groups contain External/Guest members.
            
        Example:
            >>> has_external = group_node.enumerate_members(neo4j, "run-123")
            >>> group_node.members  # Now populated with all members
            >>> for member in group_node.members:
            ...     print(member["displayName"], member["source"])
        """
        return self._walk_group_members(neo4j, run_id)
    
    def _walk_group_members(
        self,
        neo4j: Neo4jClient,
        run_id: str,
        visited_groups: Optional[set] = None,
        depth: int = 0,
        max_depth: int = 10,
    ) -> bool:
        """
        Recursively enumerate all members of this group, handling nested groups and caching.
        
        Traverses the group membership hierarchy to find all individual users
        (including guests) at any nesting level. Uses user cache to determine
        user source based on Graph API userType field. Handles circular group
        references and enforces a maximum recursion depth.
        
        Results are cached in processed_groups_cache to prevent re-enumeration of
        the same group across multiple permission contexts.
        
        Args:
            neo4j: Neo4jClient for database operations.
            run_id: Audit run ID.
            visited_groups: Set of group IDs already processed (prevent circular refs).
            depth: Current recursion depth.
            max_depth: Maximum recursion depth to prevent infinite loops.
            
        Returns:
            bool: True if this group or any nested groups contain External/Guest members.
                
        Example:
            >>> has_external = group_node._walk_group_members(neo4j, "run-123")
            >>> print(f"Members: {len(group_node.members)}, has_guests: {has_external}")
        """
        # Initialize visited_groups on first call
        if visited_groups is None:
            visited_groups = set()
        
        # Check if we've already cached this group
        if self.processed_groups_cache.has(self.group_id):
            cache_entry = self.processed_groups_cache.get(self.group_id)
            self._has_external_members = cache_entry.get("has_guests", False)
            self._members = cache_entry.get("members", [])
            self._enumeration_complete = True
            logger.debug(f"Using cached members for group {self.group_id}")
            return self._has_external_members
        
        # Check circular reference
        if self.group_id in visited_groups:
            logger.debug(f"Group {self.group_id} already processed, skipping (circular ref)")
            return False
        
        # Check depth limit
        if depth > max_depth:
            logger.warning(
                f"Maximum recursion depth ({max_depth}) reached for group {self.group_id}"
            )
            return False
        
        visited_groups.add(self.group_id)
        
        try:
            # Fetch members from Graph API
            logger.debug(f"Retrieving members for group {self.group_id} (depth {depth})")
            members_data = self.graph_client.get_group_members(self.group_id)
            
            # Pre-populate cache with all member IDs
            member_ids = [m.get("id") for m in members_data if m.get("id")]
            if member_ids:
                self.user_cache.batch_populate(member_ids)
            
            # Process each member
            for member in members_data:
                member_id = member.get("id")
                member_display_name = member.get("displayName", "Unknown")
                
                # Get full user data from cache
                user_data = self.user_cache.get(member_id)
                
                # Check if this is a user or nested group
                if member.get("@odata.type") != "#microsoft.graph.group":
                    # This is a user
                    try:
                        member_node = Neo4jUserNode(user_data)
                        
                        # Track if external/guest
                        if member_node.is_guest:
                            self._has_external_members = True
                        
                        # Add to members list
                        self._members.append({
                            "id": member_node.user_id,
                            "email": member_node.email,
                            "displayName": member_node.display_name,
                            "userType": member_node.user_type,
                            "identities": member_node.identities,
                            "source": member_node.source,
                        })
                        
                        logger.debug(
                            f"Found {member_node.source} member: {member_display_name} "
                            f"in group {self.group_id}"
                        )
                        
                        # Create Neo4j relationship
                        member_node.merge_as_group_member(neo4j, self.group_id, run_id)
                    
                    except ValueError as e:
                        logger.warning(f"Invalid user data for group member: {e}")
                        continue
                
                else:
                    # This is a nested group
                    logger.debug(
                        f"Found nested group: {member_display_name} "
                        f"in group {self.group_id}"
                    )
                    
                    try:
                        # Create nested group node and relationship
                        nested_group_node = Neo4jGroupNode(
                            member,
                            self.graph_client,
                            self.user_cache,
                            self.processed_groups_cache,
                            group_type="Group",
                        )
                        nested_group_node.merge_as_nested_group_member(neo4j, self.group_id, run_id)
                        
                        # Recursively enumerate nested group members
                        nested_has_external = nested_group_node._walk_group_members(
                            neo4j,
                            run_id,
                            visited_groups,
                            depth + 1,
                            max_depth,
                        )
                        
                        # Track if nested group has external members
                        if nested_has_external:
                            self._has_external_members = True
                        
                        # Add nested members to our member list
                        self._members.extend(nested_group_node._members)
                    
                    except ValueError as e:
                        logger.warning(f"Invalid group data for nested group: {e}")
                        continue
            
            # Cache result
            self.processed_groups_cache.set(
                self.group_id,
                has_guests=self._has_external_members,
                members=self._members.copy(),
            )
            
            self._enumeration_complete = True
            logger.debug(
                f"Enumerated {len(self._members)} members for group {self.group_id}, "
                f"has_external={self._has_external_members}"
            )
            
            return self._has_external_members
        
        except Exception:
            logger.exception(
                f"Error enumerating members for group {self.group_id}"
            )
            return False

    
    # Context-specific merge operations
    
    def merge_as_file_permission_recipient(
        self,
        neo4j: Neo4jClient,
        site_id: str,
        drive_id: str,
        item_id: str,
        item_path: str,
        web_url: str,
        file_type: str,
        sharing_type: str,
        role: str,
        created_date_time: str,
        run_id: str,
        granted_by: str = "",
    ) -> None:
        """
        Merge as direct file permission recipient: File + Group + SHARED_WITH + relationships.
        
        Enumerates group members (if not already done) to check for external/guest users,
        then escalates risk to HIGH if external members are found.
        
        Creates or updates:
        - Group node
        - File node
        - SHARED_WITH relationship (Group → File)
        - CONTAINS relationship (Site → File)
        - FOUND relationship (ScanRun → File)
        - User nodes and relationships for all group members
        
        Args:
            neo4j: Neo4jClient instance.
            site_id: ID of containing site.
            drive_id: ID of containing drive.
            item_id: ID of shared file/folder.
            item_path: Path to file (e.g., "/Shared Documents/file.docx").
            web_url: Web URL to file.
            file_type: "File" or "Folder".
            sharing_type: Type of sharing (e.g., "UserSharing", "LinkSharing").
            role: Permission role (e.g., "View", "Edit").
            created_date_time: ISO datetime when permission was created.
            run_id: Audit run ID.
            granted_by: Email of user who granted the permission (optional).
            
        Example:
            >>> group_node = Neo4jGroupNode(group_dict, graph, cache, proc_cache)
            >>> group_node.merge_as_file_permission_recipient(
            ...     neo4j, "site-1", "drive-1", "item-1", "/file.docx",
            ...     "https://...", "File", "UserSharing", "View",
            ...     "2026-05-31T...", "run-123"
            ... )
            # Creates: Group node + File node + SHARED_WITH + member relationships
        """
        # Create group node to allow linking members to it
        self.merge_node(neo4j)

        # Enumerate members to determine risk
        has_external = self.enumerate_members(neo4j, run_id)
        
        # Determine risk level
        risk = get_risk_level(sharing_type, self._group_type, item_path)
        
        # Escalate risk if group contains external/guest members
        if has_external:
            risk = "HIGH"
        
        # Merge group node permission
        neo4j.merge_permission(
            site_id=site_id,
            drive_id=drive_id,
            item_id=item_id,
            item_path=item_path,
            web_url=web_url,
            file_type=file_type,
            user_email=self.email,
            user_id=self.group_id,
            user_display_name=self.display_name,
            user_source=self._group_type,
            sharing_type=sharing_type,
            shared_with_type=self._group_type,
            role=role,
            risk_level=risk,
            created_date_time=created_date_time,
            run_id=run_id,
            granted_by=granted_by,
        )
    
    def merge_as_link_recipient(
        self,
        neo4j: Neo4jClient,
        site_id: str,
        drive_id: str,
        item_id: str,
        item_path: str,
        web_url: str,
        file_type: str,
        sharing_type: str,
        role: str,
        created_date_time: str,
        run_id: str,
        granted_by: str = "",
    ) -> None:
        """
        Merge as sharing link recipient (group expanded from grantedToIdentitiesV2).
        
        Used when processing "specific people" sharing links and the link identity
        expands to a group. Same logic as merge_as_file_permission_recipient.
        
        Args:
            neo4j: Neo4jClient instance.
            site_id: ID of containing site.
            drive_id: ID of containing drive.
            item_id: ID of shared file/folder.
            item_path: Path to file.
            web_url: Web URL to file.
            file_type: "File" or "Folder".
            sharing_type: Type of sharing (e.g., "LinkSharing").
            role: Permission role (e.g., "View", "Edit").
            created_date_time: ISO datetime when link was created.
            run_id: Audit run ID.
            granted_by: Email of link creator (optional).
            
        Example:
            >>> # Group expanded from grantedToIdentitiesV2 of a sharing link
            >>> group_node = Neo4jGroupNode(group_dict, graph, cache, proc_cache)
            >>> group_node.merge_as_link_recipient(
            ...     neo4j, "site-1", "drive-1", "item-1", "/file.docx",
            ...     "https://...", "File", "LinkSharing", "View",
            ...     "2026-05-31T...", "run-123"
            ... )
        """
        self.merge_as_file_permission_recipient(
            neo4j,
            site_id,
            drive_id,
            item_id,
            item_path,
            web_url,
            file_type,
            sharing_type,
            role,
            created_date_time,
            run_id,
            granted_by,
        )
    
    def merge_as_nested_group_member(
        self,
        neo4j: Neo4jClient,
        parent_group_id: str,
        run_id: str,
    ) -> None:
        """
        Merge as nested group: creates Group node + CONTAINS relationship to parent.
        
        Used when a group is a member of another group (nested group membership).
        
        Args:
            neo4j: Neo4jClient instance.
            parent_group_id: ID of the parent group.
            run_id: Audit run ID.
            
        Example:
            >>> nested_group = Neo4jGroupNode(nested_dict, graph, cache, proc_cache)
            >>> nested_group.merge_as_nested_group_member(neo4j, "parent-group-id", "run-123")
            # Creates: (ParentGroup)-[CONTAINS]->(NestedGroup)
        """
        neo4j.merge_nested_group(
            parent_group_id,
            self.group_id,
            self.display_name,
            self._group_type,
            run_id,
        )
    
    # Factory methods
    @classmethod
    def from_list(
        cls,
        groups: list[dict],
        graph_client: GraphClient,
        user_cache: UserCache,
        processed_groups_cache: dict,
    ) -> list["Neo4jGroupNode"]:
        """
        Construct batch of Neo4jGroupNode instances from list of group dicts.
        
        Efficient batch construction for processing multiple groups from a single
        operation (e.g., group list from Graph API).
        
        Args:
            groups: List of Graph API group dicts.
            graph_client: GraphClient instance.
            user_cache: UserCache for member enumeration.
            processed_groups_cache: Processed groups cache.
            
        Returns:
            List of Neo4jGroupNode instances (preserves input order).
            
        Raises:
            ValueError: If any group dict has invalid ID.
            
        Example:
            >>> groups = graph.get_all_groups()
            >>> group_nodes = Neo4jGroupNode.from_list(
            ...     groups, graph_client, cache, proc_cache
            ... )
        """
        return [cls(group, graph_client, user_cache, processed_groups_cache) for group in groups]
    
    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"Neo4jGroupNode(id={self.group_id}, displayName={self.display_name}, "
            f"members={len(self._members)}, type={self._group_type})"
        )