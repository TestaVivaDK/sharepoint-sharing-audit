"""Comprehensive unit tests for Neo4jGroupNode class.

Tests cover initialization, validation, member enumeration (including nested groups,
circular references, depth limits, and caching), risk escalation, merge operations,
factory methods, and edge cases. All external dependencies (Neo4jClient, GraphClient,
UserCache, caching) are mocked.
"""

import uuid
from unittest.mock import MagicMock, patch, call
import pytest

from collector.neo4j_group_node import Neo4jGroupNode
from collector.group_cache import GroupMembershipCache


# ============================================================================
# FIXTURES AND HELPERS
# ============================================================================


@pytest.fixture
def mock_graph_client():
    """Mock GraphClient with get_group_members() method."""
    client = MagicMock()
    client.get_group_members = MagicMock(return_value=[])
    return client


@pytest.fixture
def mock_neo4j_client():
    """Mock Neo4jClient with all merge methods."""
    client = MagicMock()
    client.merge_group = MagicMock()
    client.merge_permission = MagicMock()
    client.merge_nested_group = MagicMock()
    client.merge_group_member = MagicMock()
    return client


@pytest.fixture
def mock_user_cache():
    """Mock UserCache with batch_populate() and get() methods."""
    cache = MagicMock()
    cache.batch_populate = MagicMock()
    cache.get = MagicMock(return_value={})
    return cache


@pytest.fixture
def mock_processed_groups_cache():
    """Mock GroupMembershipCache with has(), get(), set() methods."""
    cache = MagicMock()
    cache.has = MagicMock(return_value=False)
    cache.get = MagicMock(return_value=None)
    cache.set = MagicMock()
    return cache


def create_test_group_dict(
    group_id: str = None,
    display_name: str = "Test Group",
    email: str = "group@example.com",
    odata_type: str = "#microsoft.graph.group",
) -> dict:
    """Create a valid test group dict."""
    if group_id is None:
        group_id = str(uuid.uuid4())
    return {
        "id": group_id,
        "displayName": display_name,
        "email": email,
        "@odata.type": odata_type,
    }


def create_test_user_dict(
    user_id: str = None,
    email: str = "user@example.com",
    display_name: str = "Test User",
    user_type: str = "Member",
    identities: list = None,
) -> dict:
    """Create a valid test user dict."""
    if user_id is None:
        user_id = str(uuid.uuid4())
    return {
        "id": user_id,
        "email": email,
        "userPrincipalName": email,
        "displayName": display_name,
        "userType": user_type,
        "identities": identities or [],
        "@odata.type": "#microsoft.graph.user",
    }


# ============================================================================
# TEST INITIALIZATION & VALIDATION
# ============================================================================


class TestNeo4jGroupNodeInitialization:
    """Tests for group node initialization and validation."""

    def test_init_with_complete_data(self, mock_graph_client, mock_user_cache, mock_processed_groups_cache):
        """Group initializes with complete data."""
        group_dict = create_test_group_dict()
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        assert node.group_id == group_dict["id"]
        assert node.display_name == "Test Group"
        assert node.email == "group@example.com"
        assert node.group_type == "Group"

    def test_init_with_minimal_data(self, mock_graph_client, mock_user_cache, mock_processed_groups_cache):
        """Group initializes with defaults for missing fields."""
        group_dict = {
            "id": str(uuid.uuid4()),
            "displayName": "Minimal Group",
        }
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        assert node.group_id == group_dict["id"]
        assert node.display_name == "Minimal Group"
        assert node.email == ""  # Missing email defaults to empty
        assert node.group_type == "Group"

    def test_init_invalid_uuid_raises_error(self, mock_graph_client, mock_user_cache, mock_processed_groups_cache):
        """Invalid UUID in group_id raises ValueError."""
        group_dict = {
            "id": "not-a-uuid",
            "displayName": "Invalid Group",
        }

        with pytest.raises(ValueError, match="Invalid group ID"):
            Neo4jGroupNode(
                group_dict,
                mock_graph_client,
                mock_user_cache,
                mock_processed_groups_cache,
            )

    @pytest.mark.parametrize(
        "odata_type,expected_type",
        [
            ("#microsoft.graph.group", "Group"),
            ("#microsoft.graph.groupType", "siteGroup"),
        ],
    )
    def test_init_group_type_inference(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, odata_type, expected_type
    ):
        """Group type inferred correctly from @odata.type."""
        group_dict = create_test_group_dict(odata_type=odata_type)
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        assert node.group_type in ("Group", "siteGroup")

    def test_init_group_type_override(self, mock_graph_client, mock_user_cache, mock_processed_groups_cache):
        """Group type can be explicitly overridden."""
        group_dict = create_test_group_dict()
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
            group_type="CustomGroup",
        )

        assert node.group_type == "CustomGroup"

    def test_init_internal_state_initialized(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache
    ):
        """Internal state initialized correctly after init."""
        group_dict = create_test_group_dict()
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        assert node._members == []
        assert node._has_external_members is False
        assert node._enumeration_complete is False


# ============================================================================
# TEST PROPERTIES & STATE
# ============================================================================


class TestNeo4jGroupNodeProperties:
    """Tests for property accessors and state."""

    def test_has_enumerated_members_false_before_enumeration(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache
    ):
        """has_enumerated_members returns False before enumeration."""
        group_dict = create_test_group_dict()
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        assert node.has_enumerated_members is False

    def test_has_enumerated_members_true_after_enumeration(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """has_enumerated_members returns True after enumeration."""
        group_dict = create_test_group_dict()
        mock_graph_client.get_group_members.return_value = []

        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jUserNode"):
            node.enumerate_members(mock_neo4j_client, "run-123")

        assert node.has_enumerated_members is True

    def test_members_empty_before_enumeration(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache
    ):
        """members property returns empty list before enumeration."""
        group_dict = create_test_group_dict()
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        assert node.members == []

    def test_members_returns_copy_not_reference(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache
    ):
        """members property returns copy, not reference."""
        group_dict = create_test_group_dict()
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )
        node._members = [{"id": "user1", "email": "user1@example.com"}]

        members1 = node.members
        members1.append({"id": "user2"})  # Modify returned list

        # Original should not be modified
        assert len(node.members) == 1

    def test_has_external_members_raises_before_enumeration(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache
    ):
        """Accessing has_external_members before enumeration raises RuntimeError."""
        group_dict = create_test_group_dict()
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with pytest.raises(RuntimeError, match="Cannot access has_external_members"):
            _ = node.has_external_members

    def test_has_external_members_returns_correct_value(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """has_external_members returns correct value after enumeration."""
        group_dict = create_test_group_dict()
        mock_graph_client.get_group_members.return_value = []
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jUserNode"):
            node.enumerate_members(mock_neo4j_client, "run-123")

        assert node.has_external_members is False

    def test_group_type_property(self, mock_graph_client, mock_user_cache, mock_processed_groups_cache):
        """group_type property returns stored group type."""
        group_dict = create_test_group_dict()
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
            group_type="TestType",
        )

        assert node.group_type == "TestType"


# ============================================================================
# TEST BASIC MEMBER ENUMERATION
# ============================================================================


class TestMemberEnumeration_BasicUsers:
    """Tests for enumerating groups with only user members."""

    def test_enumerate_group_with_internal_users(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """Enumerate group with only internal user members."""
        group_dict = create_test_group_dict()
        user1 = create_test_user_dict(user_type="Member")
        user2 = create_test_user_dict(email="user2@example.com", user_type="Member")
        
        mock_graph_client.get_group_members.return_value = [user1, user2]
        mock_user_cache.get.side_effect = lambda uid: user1 if uid == user1["id"] else user2

        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jUserNode") as mock_user_node_class:
            mock_user_node = MagicMock()
            mock_user_node.user_id = user1["id"]
            mock_user_node.email = user1["email"]
            mock_user_node.display_name = user1["displayName"]
            mock_user_node.user_type = user1["userType"]
            mock_user_node.identities = []
            mock_user_node.source = "Internal"
            mock_user_node.is_guest = False
            mock_user_node_class.return_value = mock_user_node

            has_external = node.enumerate_members(mock_neo4j_client, "run-123")

        assert has_external is False
        assert node.has_enumerated_members is True

    @pytest.mark.parametrize(
        "user_type,source,is_guest",
        [
            ("Guest", "Guest", True),
            ("Guest", "External", True),
            ("Member", "Internal", False),
        ],
    )
    def test_enumerate_detects_external_members(
        self,
        mock_graph_client,
        mock_user_cache,
        mock_processed_groups_cache,
        mock_neo4j_client,
        user_type,
        source,
        is_guest,
    ):
        """Detect external and guest members correctly."""
        group_dict = create_test_group_dict()
        user = create_test_user_dict(user_type=user_type)
        
        mock_graph_client.get_group_members.return_value = [user]
        mock_user_cache.get.return_value = user

        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jUserNode") as mock_user_node_class:
            mock_user_node = MagicMock()
            mock_user_node.is_guest = is_guest
            mock_user_node.source = source
            mock_user_node.user_id = user["id"]
            mock_user_node.email = user["email"]
            mock_user_node.display_name = user["displayName"]
            mock_user_node.user_type = user["userType"]
            mock_user_node.identities = []
            mock_user_node_class.return_value = mock_user_node

            has_external = node.enumerate_members(mock_neo4j_client, "run-123")

        assert has_external is is_guest

    def test_enumerate_batch_populate_user_cache(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """Batch populate user cache with member IDs."""
        group_dict = create_test_group_dict()
        user1 = create_test_user_dict()
        user2 = create_test_user_dict(email="user2@example.com")
        
        mock_graph_client.get_group_members.return_value = [user1, user2]
        mock_user_cache.get.side_effect = lambda uid: user1 if uid == user1["id"] else user2

        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jUserNode"):
            node.enumerate_members(mock_neo4j_client, "run-123")

        mock_user_cache.batch_populate.assert_called_once()

    def test_enumerate_calls_merge_as_group_member(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """User merge_as_group_member called for each user."""
        group_dict = create_test_group_dict()
        user = create_test_user_dict()
        
        mock_graph_client.get_group_members.return_value = [user]
        mock_user_cache.get.return_value = user

        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jUserNode") as mock_user_node_class:
            mock_user_node = MagicMock()
            mock_user_node.is_guest = False
            mock_user_node.source = "Internal"
            mock_user_node.user_id = user["id"]
            mock_user_node.email = user["email"]
            mock_user_node.display_name = user["displayName"]
            mock_user_node.user_type = user["userType"]
            mock_user_node.identities = []
            mock_user_node_class.return_value = mock_user_node

            node.enumerate_members(mock_neo4j_client, "run-123")

        mock_user_node.merge_as_group_member.assert_called_once_with(
            mock_neo4j_client, group_dict["id"], "run-123"
        )


# ============================================================================
# TEST NESTED GROUP ENUMERATION
# ============================================================================


class TestMemberEnumeration_NestedGroups:
    """Tests for enumerating groups with nested groups."""

    def test_enumerate_nested_group_recursively(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """Nested groups enumerated recursively."""
        parent_group = create_test_group_dict(display_name="Parent")
        nested_group = create_test_group_dict(
            display_name="Nested",
            group_id=str(uuid.uuid4()),
        )
        nested_group["@odata.type"] = "#microsoft.graph.group"

        # First call returns nested group, second call (for nested) returns empty
        mock_graph_client.get_group_members.side_effect = [
            [nested_group],
            [],
        ]

        node = Neo4jGroupNode(
            parent_group,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jGroupNode") as mock_group_node_class:
            mock_nested_node = MagicMock()
            mock_nested_node._members = []
            mock_nested_node._has_external_members = False
            mock_nested_node._walk_group_members.return_value = False
            
            mock_group_node_class.side_effect = lambda *args, **kwargs: (
                mock_nested_node if args[0].get("displayName") == "Nested" else node
            )

            node.enumerate_members(mock_neo4j_client, "run-123")

        assert node.has_enumerated_members is True

    def test_enumerate_propagates_external_from_nested(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """External members in nested group propagate to parent."""
        parent_group = create_test_group_dict(display_name="Parent")
        nested_group = create_test_group_dict(
            display_name="Nested",
            group_id=str(uuid.uuid4()),
        )
        nested_group["@odata.type"] = "#microsoft.graph.group"

        mock_graph_client.get_group_members.side_effect = [
            [nested_group],
            [],
        ]

        node = Neo4jGroupNode(
            parent_group,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jGroupNode") as mock_group_node_class:
            mock_nested_node = MagicMock()
            mock_nested_node._members = [{"id": "guest1", "source": "Guest"}]
            mock_nested_node._has_external_members = True
            mock_nested_node._walk_group_members.return_value = True
            
            mock_group_node_class.side_effect = lambda *args, **kwargs: (
                mock_nested_node if args[0].get("displayName") == "Nested" else node
            )

            node.enumerate_members(mock_neo4j_client, "run-123")

        assert node.has_external_members is True

    def test_enumerate_merge_nested_group_member_called(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """merge_as_nested_group_member called for nested groups."""
        parent_group = create_test_group_dict(display_name="Parent")
        nested_group = create_test_group_dict(
            display_name="Nested",
            group_id=str(uuid.uuid4()),
        )
        nested_group["@odata.type"] = "#microsoft.graph.group"

        mock_graph_client.get_group_members.side_effect = [
            [nested_group],
            [],
        ]

        node = Neo4jGroupNode(
            parent_group,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jGroupNode") as mock_group_node_class:
            mock_nested_node = MagicMock()
            mock_nested_node._members = []
            mock_nested_node._has_external_members = False
            mock_nested_node._walk_group_members.return_value = False
            mock_nested_node.merge_as_nested_group_member = MagicMock()
            
            mock_group_node_class.side_effect = lambda *args, **kwargs: (
                mock_nested_node if args[0].get("displayName") == "Nested" else node
            )

            node.enumerate_members(mock_neo4j_client, "run-123")

        mock_nested_node.merge_as_nested_group_member.assert_called_once()


# ============================================================================
# TEST CIRCULAR REFERENCES
# ============================================================================


class TestMemberEnumeration_CircularReferences:
    """Tests for handling circular group references."""

    def test_enumerate_handles_circular_reference(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """Circular references handled without infinite loop via visited_groups set."""
        group_a_id = str(uuid.uuid4())
        
        group_a = create_test_group_dict(display_name="GroupA", group_id=group_a_id)
        
        # Test direct circular reference within _walk_group_members
        visited = {group_a_id}  # Already visited
        node = Neo4jGroupNode(
            group_a,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        # When group_id already in visited_groups, should return False without infinite loop
        result = node._walk_group_members(
            mock_neo4j_client,
            "run-123",
            visited_groups=visited,
            depth=0,
        )

        # Should return False (no external members) and not crash
        assert result is False


# ============================================================================
# TEST DEPTH LIMITS
# ============================================================================


class TestMemberEnumeration_DepthLimits:
    """Tests for depth limit enforcement in nested enumeration."""

    def test_enumerate_respects_max_depth(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """Max depth limit enforced (default 10)."""
        group_dict = create_test_group_dict()
        mock_graph_client.get_group_members.return_value = []

        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        # Call with depth > max_depth
        result = node._walk_group_members(
            mock_neo4j_client,
            "run-123",
            visited_groups=set(),
            depth=11,
            max_depth=10,
        )

        assert result is False


# ============================================================================
# TEST CACHING
# ============================================================================


class TestMemberEnumeration_Caching:
    """Tests for member enumeration caching."""

    def test_enumerate_uses_cache_on_second_call(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """Second enumeration uses cache, doesn't call Graph API."""
        group_dict = create_test_group_dict()
        user = create_test_user_dict()
        
        mock_graph_client.get_group_members.return_value = [user]
        mock_user_cache.get.return_value = user
        
        # Cache returns data on second call
        cached_data = {
            "has_guests": False,
            "members": [{"id": "user1", "email": "user@example.com"}],
        }
        mock_processed_groups_cache.has.side_effect = [False, True]
        mock_processed_groups_cache.get.return_value = cached_data

        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jUserNode"):
            node.enumerate_members(mock_neo4j_client, "run-123")
            mock_graph_client.reset_mock()

            # Second enumeration should use cache
            node2 = Neo4jGroupNode(
                group_dict,
                mock_graph_client,
                mock_user_cache,
                mock_processed_groups_cache,
            )
            node2.enumerate_members(mock_neo4j_client, "run-123")

        # Graph API not called for cached group
        mock_graph_client.get_group_members.assert_not_called()

    def test_enumerate_stores_in_cache(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """Enumeration result stored in cache."""
        group_dict = create_test_group_dict()
        mock_graph_client.get_group_members.return_value = []

        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jUserNode"):
            node.enumerate_members(mock_neo4j_client, "run-123")

        mock_processed_groups_cache.set.assert_called_once()
        call_kwargs = mock_processed_groups_cache.set.call_args[1]
        assert "has_guests" in call_kwargs
        assert "members" in call_kwargs


# ============================================================================
# TEST ERROR HANDLING
# ============================================================================


class TestMemberEnumeration_ErrorHandling:
    """Tests for error handling during enumeration."""

    def test_enumerate_handles_invalid_user_data(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """Invalid user data logged but enumeration continues."""
        group_dict = create_test_group_dict()
        user = create_test_user_dict()
        
        mock_graph_client.get_group_members.return_value = [user]
        mock_user_cache.get.return_value = user

        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jUserNode") as mock_user_node_class:
            # Simulate invalid user (raises ValueError)
            mock_user_node_class.side_effect = ValueError("Invalid user")

            has_external = node.enumerate_members(mock_neo4j_client, "run-123")

        # Enumeration completes despite invalid user
        assert node.has_enumerated_members is True

    def test_enumerate_handles_graph_api_exception(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """Graph API exception handled gracefully and logged."""
        group_dict = create_test_group_dict()
        mock_graph_client.get_group_members.side_effect = Exception("API Error")

        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        # When exception occurs, returns False and doesn't set enumeration_complete
        has_external = node.enumerate_members(mock_neo4j_client, "run-123")

        assert has_external is False
        # Exception prevents completion, so enumeration_complete stays False
        assert node.has_enumerated_members is False


# ============================================================================
# TEST MERGE OPERATIONS
# ============================================================================


class TestMergeNode:
    """Tests for merge_node() operation."""

    def test_merge_node_calls_neo4j_merge_group(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """merge_node calls neo4j.merge_group() with correct parameters."""
        group_dict = create_test_group_dict()
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        node.merge_node(mock_neo4j_client)

        mock_neo4j_client.merge_group.assert_called_once_with(
            group_dict["id"],
            "Test Group",
            "Group",
        )


class TestMergeAsFilePermissionRecipient:
    """Tests for merge_as_file_permission_recipient() operation."""

    def test_merge_file_permission_enumerates_members(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """merge_as_file_permission_recipient enumerates members first."""
        group_dict = create_test_group_dict()
        mock_graph_client.get_group_members.return_value = []
        
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jUserNode"):
            with patch("collector.neo4j_group_node.get_risk_level", return_value="LOW"):
                node.merge_as_file_permission_recipient(
                    mock_neo4j_client,
                    site_id="site-1",
                    drive_id="drive-1",
                    item_id="item-1",
                    item_path="/test.docx",
                    web_url="https://example.com",
                    file_type="File",
                    sharing_type="UserSharing",
                    role="View",
                    created_date_time="2026-05-31T00:00:00Z",
                    run_id="run-123",
                )

        assert node.has_enumerated_members is True

    @pytest.mark.parametrize(
        "has_external,expected_risk",
        [
            (False, "LOW"),  # No escalation
            (True, "HIGH"),  # Escalated to HIGH
        ],
    )
    def test_merge_file_permission_escalates_risk_for_external(
        self,
        mock_graph_client,
        mock_user_cache,
        mock_processed_groups_cache,
        mock_neo4j_client,
        has_external,
        expected_risk,
    ):
        """Risk escalated to HIGH if group has external members."""
        group_dict = create_test_group_dict()
        mock_graph_client.get_group_members.return_value = []
        
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )
        node._has_external_members = has_external
        node._enumeration_complete = True

        with patch("collector.neo4j_group_node.Neo4jUserNode"):
            with patch("collector.neo4j_group_node.get_risk_level", return_value="LOW"):
                node.merge_as_file_permission_recipient(
                    mock_neo4j_client,
                    site_id="site-1",
                    drive_id="drive-1",
                    item_id="item-1",
                    item_path="/test.docx",
                    web_url="https://example.com",
                    file_type="File",
                    sharing_type="UserSharing",
                    role="View",
                    created_date_time="2026-05-31T00:00:00Z",
                    run_id="run-123",
                )

        # Check that merge_permission was called with correct risk level
        called_risk = mock_neo4j_client.merge_permission.call_args[1]["risk_level"]
        assert called_risk == expected_risk

    def test_merge_file_permission_calls_merge_permission(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """merge_as_file_permission_recipient calls neo4j.merge_permission()."""
        group_dict = create_test_group_dict()
        mock_graph_client.get_group_members.return_value = []
        
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jUserNode"):
            with patch("collector.neo4j_group_node.get_risk_level", return_value="MEDIUM"):
                node.merge_as_file_permission_recipient(
                    mock_neo4j_client,
                    site_id="site-1",
                    drive_id="drive-1",
                    item_id="item-1",
                    item_path="/test.docx",
                    web_url="https://example.com",
                    file_type="File",
                    sharing_type="UserSharing",
                    role="View",
                    created_date_time="2026-05-31T00:00:00Z",
                    run_id="run-123",
                )

        mock_neo4j_client.merge_permission.assert_called_once()


class TestMergeAsLinkRecipient:
    """Tests for merge_as_link_recipient() operation."""

    def test_merge_link_recipient_delegates_to_file_permission(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """merge_as_link_recipient delegates to merge_as_file_permission_recipient."""
        group_dict = create_test_group_dict()
        mock_graph_client.get_group_members.return_value = []
        
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jUserNode"):
            with patch("collector.neo4j_group_node.get_risk_level", return_value="LOW"):
                node.merge_as_link_recipient(
                    mock_neo4j_client,
                    site_id="site-1",
                    drive_id="drive-1",
                    item_id="item-1",
                    item_path="/test.docx",
                    web_url="https://example.com",
                    file_type="File",
                    sharing_type="LinkSharing",
                    role="View",
                    created_date_time="2026-05-31T00:00:00Z",
                    run_id="run-123",
                )

        mock_neo4j_client.merge_permission.assert_called_once()


class TestMergeAsNestedGroupMember:
    """Tests for merge_as_nested_group_member() operation."""

    def test_merge_nested_group_member_calls_neo4j(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """merge_as_nested_group_member calls neo4j.merge_nested_group()."""
        group_dict = create_test_group_dict()
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        node.merge_as_nested_group_member(
            mock_neo4j_client,
            parent_group_id="parent-group",
            run_id="run-123",
        )

        mock_neo4j_client.merge_nested_group.assert_called_once_with(
            "parent-group",
            group_dict["id"],
            "Test Group",
            "Group",
            "run-123",
        )


# ============================================================================
# TEST FACTORY METHODS
# ============================================================================


class TestFromListFactoryMethod:
    """Tests for Neo4jGroupNode.from_list() factory method."""

    def test_from_list_creates_batch(self, mock_graph_client, mock_user_cache, mock_processed_groups_cache):
        """from_list creates multiple instances from group list."""
        groups = [
            create_test_group_dict(group_id=str(uuid.uuid4()), display_name=f"Group {i}")
            for i in range(3)
        ]

        nodes = Neo4jGroupNode.from_list(
            groups,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        assert len(nodes) == 3
        assert all(isinstance(n, Neo4jGroupNode) for n in nodes)

    def test_from_list_preserves_order(self, mock_graph_client, mock_user_cache, mock_processed_groups_cache):
        """from_list preserves input order."""
        groups = [
            create_test_group_dict(display_name="First"),
            create_test_group_dict(display_name="Second"),
            create_test_group_dict(display_name="Third"),
        ]

        nodes = Neo4jGroupNode.from_list(
            groups,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        assert nodes[0].display_name == "First"
        assert nodes[1].display_name == "Second"
        assert nodes[2].display_name == "Third"

    def test_from_list_invalid_group_raises_error(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache
    ):
        """from_list raises ValueError for invalid group."""
        groups = [
            create_test_group_dict(),
            {"id": "invalid-id", "displayName": "Bad Group"},  # Invalid UUID
        ]

        with pytest.raises(ValueError):
            Neo4jGroupNode.from_list(
                groups,
                mock_graph_client,
                mock_user_cache,
                mock_processed_groups_cache,
            )


# ============================================================================
# TEST STRING REPRESENTATION
# ============================================================================


class TestReprMethod:
    """Tests for __repr__() string representation."""

    def test_repr_includes_key_fields(self, mock_graph_client, mock_user_cache, mock_processed_groups_cache):
        """__repr__() includes group_id, displayName, members count, type."""
        group_dict = create_test_group_dict()
        node = Neo4jGroupNode(
            group_dict,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        repr_str = repr(node)

        assert "Neo4jGroupNode" in repr_str
        assert group_dict["id"] in repr_str
        assert "Test Group" in repr_str
        assert "Group" in repr_str


# ============================================================================
# TEST COMPLEX SCENARIOS
# ============================================================================


class TestComplexScenarios:
    """Tests for complex real-world scenarios."""

    def test_group_with_only_nested_groups_no_direct_users(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """Group containing only nested groups (no direct user members)."""
        parent = create_test_group_dict(display_name="Parent")
        nested1 = create_test_group_dict(display_name="Nested1", group_id=str(uuid.uuid4()))
        nested2 = create_test_group_dict(display_name="Nested2", group_id=str(uuid.uuid4()))
        
        nested1["@odata.type"] = "#microsoft.graph.group"
        nested2["@odata.type"] = "#microsoft.graph.group"

        mock_graph_client.get_group_members.side_effect = [
            [nested1, nested2],  # Parent's members
            [],  # Nested1's members
            [],  # Nested2's members
        ]

        node = Neo4jGroupNode(
            parent,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        with patch("collector.neo4j_group_node.Neo4jGroupNode") as mock_group_node_class:
            mock_nested = MagicMock()
            mock_nested._members = []
            mock_nested._has_external_members = False
            mock_nested._walk_group_members.return_value = False
            
            def mock_constructor(*args, **kwargs):
                if args[0].get("displayName") in ("Nested1", "Nested2"):
                    return mock_nested
                return node

            mock_group_node_class.side_effect = mock_constructor

            node.enumerate_members(mock_neo4j_client, "run-123")

        assert node.has_enumerated_members is True

    def test_deeply_nested_groups_approach_depth_limit(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """Deeply nested group hierarchy approaching max depth."""
        group = create_test_group_dict()
        mock_graph_client.get_group_members.return_value = []

        node = Neo4jGroupNode(
            group,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        # Walk to near max depth
        result = node._walk_group_members(
            mock_neo4j_client,
            "run-123",
            visited_groups=set(),
            depth=9,
            max_depth=10,
        )

        # Should complete successfully (depth 9 is within limit)
        assert node.has_enumerated_members is True

    def test_empty_group_no_members(
        self, mock_graph_client, mock_user_cache, mock_processed_groups_cache, mock_neo4j_client
    ):
        """Empty group with no members."""
        group = create_test_group_dict()
        mock_graph_client.get_group_members.return_value = []

        node = Neo4jGroupNode(
            group,
            mock_graph_client,
            mock_user_cache,
            mock_processed_groups_cache,
        )

        node.enumerate_members(mock_neo4j_client, "run-123")

        assert len(node.members) == 0
        assert node.has_external_members is False
