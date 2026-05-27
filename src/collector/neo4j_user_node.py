"""Neo4jUserNode: Encapsulates Entra ID user processing for Neo4j operations."""

import logging
import uuid
from typing import Optional

from collector.user_cache import UserCache
from shared.neo4j_client import Neo4jClient
from shared.classify import determine_user_source

logger = logging.getLogger(__name__)


class Neo4jUserNode:
    """
    Encapsulates Entra ID user object processing for Neo4j operations.
    
    Consolidates user data extraction, validation, classification, and all
    relationship operations (OWNS, CONTAINS, SHARED_WITH) into a single,
    type-safe interface. Reduces cognitive complexity and ensures consistent
    processing across all user handling contexts.
    
    Usage:
        # From Entra ID user object
        user_node = Neo4jUserNode(user_dict)
        user_node.merge_as_site_owner(neo4j, site_id)
        
        # From cache
        user_node = Neo4jUserNode.from_user_cache(cache, user_id)
        user_node.merge_as_file_permission_recipient(neo4j, site_id, drive_id, ...)
        
        # Batch processing
        user_nodes = Neo4jUserNode.from_list(users)
        for node in user_nodes:
            node.merge_node(neo4j)
    
    Attributes:
        user_id (str): User unique identifier from Graph API.
        email (str): Primary email address (or UPN fallback).
        display_name (str): User display name.
        user_type (str): Graph API userType ("Member" or "Guest").
        identities (list): Graph API identities array for B2B detection.
        source (str): Classification - "Internal", "External", "Guest", or "Unknown".
    """
    
    def __init__(
        self,
        user: dict,
        source: Optional[str] = None,
        tenant_domain: str = "",
    ):
        """
        Initialize from Entra ID user object.
        
        Args:
            user: Graph API user dict with fields:
                  {id, email, userPrincipalName, displayName, userType, identities}
            source: Optional override for classification. If None, auto-determines 
                    via determine_user_source(). Use to override for special cases.
            tenant_domain: Deprecated (kept for backward compatibility, not used).
            
        Raises:
            ValueError: If user_id is invalid UUID or email is missing.
            
        Example:
            >>> user = {
            ...     "id": "123e4567-e89b-12d3-a456-426614174000",
            ...     "email": "user@example.com",
            ...     "displayName": "John Doe",
            ...     "userType": "Member",
            ...     "identities": []
            ... }
            >>> user_node = Neo4jUserNode(user)
            >>> user_node.source
            "Internal"
        """
        # Extract attributes from user object
        self.user_id = user.get("id", "")
        self.email = user.get("email", "") or user.get("userPrincipalName", "")
        self.display_name = user.get("displayName", "Unknown User")
        self.user_type = user.get("userType", "Member")
        self.identities = user.get("identities", [])
        
        # Validate required fields
        if not self._is_valid_uuid(self.user_id):
            raise ValueError(f"Invalid user ID: {self.user_id}")
        if not self.email:
            raise ValueError(f"User {self.user_id} has no email or UPN")
        
        # Determine source classification (auto or override)
        self.source = source or determine_user_source(user, tenant_domain)
        
        logger.info(
            f"Created Neo4jUserNode: id={self.user_id}, email={self.email}, "
            f"userType={self.user_type}, source={self.source}"
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
    
    # Properties for convenience checks
    
    @property
    def is_guest(self) -> bool:
        """True if user is Guest or External (any non-internal user)."""
        return self.source in ("Guest", "External")
    
    @property
    def is_external_b2b(self) -> bool:
        """True if user is External (B2B/Entra ID guest with ExternalAzureAD issuer)."""
        return self.source == "External"
    
    @property
    def is_internal(self) -> bool:
        """True if user is Internal (member of home tenant)."""
        return self.source == "Internal"
    
    # Core Neo4j operations
    
    def merge_node(self, neo4j: Neo4jClient) -> None:
        """
        Merge User node into Neo4j database.
        
        Creates or updates user node with id, email, displayName, and source.
        This is a foundational operation called by all relationship methods.
        
        Args:
            neo4j: Neo4jClient instance.
            
        Example:
            >>> user_node.merge_node(neo4j)
            # User node created in Neo4j with properties:
            # {id: "123e...", email: "user@example.com", displayName: "John Doe", source: "Internal"}
        """
        logger.info("MERGE NODE")
        neo4j.merge_user(self.user_id, self.email, self.display_name, self.source)
    
    # Context-specific operations
    
    def merge_as_site_owner(
        self,
        neo4j: Neo4jClient,
        site_id: str,
    ) -> None:
        """
        Merge as site owner: creates User node + OWNS relationship.
        
        Used when user is the owner of a SharePoint site or OneDrive.
        
        Args:
            neo4j: Neo4jClient instance.
            site_id: ID of the site being owned.
            
        Example:
            >>> user_node = Neo4jUserNode(owner_user)
            >>> user_node.merge_as_site_owner(neo4j, "site-123")
            # Creates: User node + (User)-[:OWNS]->(Site)
        """
        logger.info("MERGE SITE OWNER")
        self.merge_node(neo4j)
        neo4j.merge_owns(self.email, site_id)
    
    def merge_as_group_member(
        self,
        neo4j: Neo4jClient,
        group_id: str,
        run_id: str,
    ) -> None:
        """
        Merge as direct group member: creates User node + CONTAINS relationship.
        
        Used when user is a direct member of a group. Updates the lastSeenRunId
        in the relationship to track membership across audit runs.
        
        Args:
            neo4j: Neo4jClient instance.
            group_id: ID of the group.
            run_id: Audit run ID (for tracking membership changes).
            
        Example:
            >>> user_node = Neo4jUserNode(member_user)
            >>> user_node.merge_as_group_member(neo4j, "group-456", "run-789")
            # Creates: User node + (Group)-[:CONTAINS]->(User)
        """
        logger.info("MERGE GROUP MEMBER")
        self.merge_node(neo4j)
        neo4j.merge_group_member(
            group_id,
            self.email,
            self.user_id,
            self.display_name,
            self.source,
            run_id,
        )
    
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
        risk_level: str,
        created_date_time: str,
        run_id: str,
        granted_by: str = "",
    ) -> None:
        """
        Merge as direct file permission recipient: File + User + SHARED_WITH + relationships.
        
        Used when user is directly granted access to a file (not through a group).
        Creates file node, user node, SHARED_WITH relationship, and relationship to
        containing site and scan run.
        
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
            risk_level: Risk classification ("LOW", "MEDIUM", "HIGH").
            created_date_time: ISO datetime when permission was created.
            run_id: Audit run ID.
            granted_by: Email of user who granted the permission (optional).
            
        Example:
            >>> user_node = Neo4jUserNode(recipient_user)
            >>> user_node.merge_as_file_permission_recipient(
            ...     neo4j, "site-1", "drive-1", "item-1", "/file.docx",
            ...     "https://...", "File", "UserSharing", "Edit", "HIGH",
            ...     "2026-05-26T...", "run-123"
            ... )
            # Creates: File node + SHARED_WITH + CONTAINS + FOUND relationships
        """
        logger.info("MERGE PERMISSION")
        neo4j.merge_permission(
            site_id=site_id,
            drive_id=drive_id,
            item_id=item_id,
            item_path=item_path,
            web_url=web_url,
            file_type=file_type,
            user_email=self.email,
            user_id=self.user_id,
            user_display_name=self.display_name,
            user_source=self.source,
            sharing_type=sharing_type,
            shared_with_type=self.source,
            role=role,
            risk_level=risk_level,
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
        risk_level: str,
        created_date_time: str,
        run_id: str,
        granted_by: str = "",
    ) -> None:
        """
        Merge as sharing link recipient (specific people link expansion).
        
        Used when processing "specific people" sharing links and expanding the
        grantedToIdentitiesV2 array to find individual user recipients.
        
        This method is identical to merge_as_file_permission_recipient() since
        both create the same SHARED_WITH relationship. Provided separately for
        semantic clarity and future differentiation if needed.
        
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
            risk_level: Risk classification.
            created_date_time: ISO datetime when link was created.
            run_id: Audit run ID.
            granted_by: Email of link creator (optional).
            
        Example:
            >>> # User expanded from grantedToIdentitiesV2 of a sharing link
            >>> recipient = Neo4jUserNode(identity_user)
            >>> recipient.merge_as_link_recipient(
            ...     neo4j, "site-1", "drive-1", "item-1", "/file.docx",
            ...     "https://...", "File", "LinkSharing", "View", "HIGH",
            ...     "2026-05-26T...", "run-123"
            ... )
        """
        logger.info("MERGE AS LINK RECIPIENT")
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
            risk_level,
            created_date_time,
            run_id,
            granted_by,
        )
    
    # Factory methods for convenient construction
    
    @classmethod
    def from_user_cache(
        cls,
        user_cache: UserCache,
        user_id: str,
        tenant_domain: str = "",
    ) -> "Neo4jUserNode":
        """
        Construct Neo4jUserNode from UserCache lazy-load.
        
        Useful when you have a user_id and need to fetch full user data via cache.
        The cache will lazy-load from Graph API if not present.
        
        Args:
            user_cache: UserCache instance with Graph API access.
            user_id: User ID to fetch.
            tenant_domain: Optional tenant domain (deprecated, not used in determine_user_source).
            
        Returns:
            Neo4jUserNode instance populated from cache.
            
        Raises:
            ValueError: If user not found in cache or has invalid ID/email.
            
        Example:
            >>> cache = UserCache(graph_client)
            >>> user_node = Neo4jUserNode.from_user_cache(cache, "user-id-123")
            >>> user_node.merge_as_file_permission_recipient(neo4j, ...)
        """
        user_data = user_cache.get(user_id)
        if not user_data:
            raise ValueError(f"User {user_id} not found in cache")
        return cls(user_data, tenant_domain=tenant_domain)
    
    @classmethod
    def from_list(
        cls,
        users: list[dict],
        tenant_domain: str = "",
    ) -> list["Neo4jUserNode"]:
        """
        Construct batch of Neo4jUserNode instances from list of user dicts.
        
        Efficient batch construction for processing multiple users from a single
        operation (e.g., group members list from Graph API).
        
        Args:
            users: List of Graph API user dicts.
            tenant_domain: Optional tenant domain (deprecated).
            
        Returns:
            List of Neo4jUserNode instances (preserves input order).
            
        Raises:
            ValueError: If any user dict has invalid ID or missing email.
            
        Example:
            >>> members = graph.get_group_members(group_id)
            >>> member_nodes = Neo4jUserNode.from_list(members)
            >>> for node in member_nodes:
            ...     node.merge_as_group_member(neo4j, group_id, run_id)
        """
        return [cls(user, tenant_domain=tenant_domain) for user in users]
    
    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"Neo4jUserNode(id={self.user_id}, email={self.email}, "
            f"source={self.source})"
        )
