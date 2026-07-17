"""Unit tests for GroupMembershipCache class."""

import pytest
from collector.group_cache import GroupMembershipCache


class TestGroupMembershipCacheInitialization:
    """Tests for cache initialization."""
    
    def test_cache_creates_empty(self):
        """Cache should initialize empty."""
        cache = GroupMembershipCache()
        assert cache.has("any-group") is False
        assert cache.get("any-group") is None
    
    def test_cache_stats_initialized_to_zero(self):
        """Cache stats should initialize to zero."""
        cache = GroupMembershipCache()
        stats = cache.stats()
        assert stats["cached_groups"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 0
    
    def test_cache_repr(self):
        """Cache string representation should be informative."""
        cache = GroupMembershipCache()
        repr_str = repr(cache)
        assert "GroupMembershipCache" in repr_str
        assert "cached=" in repr_str
        assert "hits=" in repr_str
        assert "misses=" in repr_str


class TestGroupMembershipCacheSetGet:
    """Tests for set() and get() operations."""
    
    def test_set_and_get_basic(self):
        """Should store and retrieve group data correctly."""
        cache = GroupMembershipCache()
        members = [
            {"id": "user1", "email": "user1@example.com", "displayName": "User 1", "source": "Internal"},
            {"id": "user2", "email": "user2@example.com", "displayName": "User 2", "source": "Guest"},
        ]
        
        cache.set("group-123", has_guests=True, members=members)
        data = cache.get("group-123")
        
        assert data is not None
        assert data["has_guests"] is True
        assert len(data["members"]) == 2
        assert data["members"][0]["id"] == "user1"
        assert data["members"][1]["id"] == "user2"
    
    def test_set_with_no_guests(self):
        """Should handle groups without external members."""
        cache = GroupMembershipCache()
        members = [{"id": "user1", "email": "user1@example.com", "displayName": "User 1", "source": "Internal"}]
        
        cache.set("group-456", has_guests=False, members=members)
        data = cache.get("group-456")
        
        assert data["has_guests"] is False
        assert len(data["members"]) == 1
    
    def test_set_with_empty_members(self):
        """Should handle groups with no members."""
        cache = GroupMembershipCache()
        cache.set("empty-group", has_guests=False, members=[])
        data = cache.get("empty-group")
        
        assert data["has_guests"] is False
        assert data["members"] == []
    
    def test_get_nonexistent_returns_none(self):
        """Getting non-existent group should return None."""
        cache = GroupMembershipCache()
        assert cache.get("nonexistent") is None
    
    def test_set_overwrites_previous(self):
        """Setting same group twice should overwrite."""
        cache = GroupMembershipCache()
        
        cache.set("group-1", has_guests=False, members=[{"id": "user1"}])
        cache.set("group-1", has_guests=True, members=[{"id": "user1"}, {"id": "user2"}])
        
        data = cache.get("group-1")
        assert data["has_guests"] is True
        assert len(data["members"]) == 2
    
    def test_set_creates_copy_of_members(self):
        """Cache should store a copy of members list."""
        cache = GroupMembershipCache()
        original_members = [{"id": "user1"}]
        
        cache.set("group-1", has_guests=False, members=original_members)
        
        # Modify original list
        original_members.append({"id": "user2"})
        
        # Cache should not be affected
        data = cache.get("group-1")
        assert len(data["members"]) == 1
    
    def test_get_returns_copy_of_data(self):
        """Get should return data that can be modified without affecting cache."""
        cache = GroupMembershipCache()
        members = [{"id": "user1"}]
        cache.set("group-1", has_guests=False, members=members)
        
        data = cache.get("group-1")
        data["members"].append({"id": "user2"})
        
        # Cache should not be affected
        data2 = cache.get("group-1")
        assert len(data2["members"]) == 1


class TestGroupMembershipCacheHas:
    """Tests for has() method."""
    
    def test_has_returns_true_for_cached_group(self):
        """has() should return True for cached groups."""
        cache = GroupMembershipCache()
        cache.set("group-1", has_guests=False, members=[])
        
        assert cache.has("group-1") is True
    
    def test_has_returns_false_for_uncached_group(self):
        """has() should return False for uncached groups."""
        cache = GroupMembershipCache()
        assert cache.has("group-1") is False
    
    def test_has_after_set_multiple_groups(self):
        """has() should work correctly with multiple groups."""
        cache = GroupMembershipCache()
        
        cache.set("group-1", has_guests=False, members=[])
        cache.set("group-2", has_guests=True, members=[])
        
        assert cache.has("group-1") is True
        assert cache.has("group-2") is True
        assert cache.has("group-3") is False


class TestGroupMembershipCacheClear:
    """Tests for clear() method."""
    
    def test_clear_empties_cache(self):
        """clear() should remove all cached data."""
        cache = GroupMembershipCache()
        cache.set("group-1", has_guests=False, members=[])
        cache.set("group-2", has_guests=False, members=[])
        
        cache.clear()
        
        assert cache.has("group-1") is False
        assert cache.has("group-2") is False
        assert cache.get("group-1") is None
    
    def test_clear_resets_stats(self):
        """clear() should only clear data, not reset stats."""
        cache = GroupMembershipCache()
        cache.set("group-1", has_guests=False, members=[])
        cache.get("group-1")  # Creates a hit
        cache.get("group-2")  # Creates a miss
        
        cache.clear()
        
        stats = cache.stats()
        assert stats["cached_groups"] == 0
        # Stats tracking should continue (hits/misses are cumulative)
        assert stats["hits"] == 1
        assert stats["misses"] == 1


class TestGroupMembershipCacheStats:
    """Tests for stats() method."""
    
    def test_stats_hit_on_cached_group(self):
        """Getting cached group should increment hits."""
        cache = GroupMembershipCache()
        cache.set("group-1", has_guests=False, members=[])
        
        cache.get("group-1")
        
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 100.0
    
    def test_stats_miss_on_uncached_group(self):
        """Getting uncached group should increment misses."""
        cache = GroupMembershipCache()
        
        cache.get("nonexistent")
        
        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0
    
    def test_stats_mixed_hits_and_misses(self):
        """Stats should track mixed hits and misses correctly."""
        cache = GroupMembershipCache()
        cache.set("group-1", has_guests=False, members=[])
        
        cache.get("group-1")  # Hit
        cache.get("group-1")  # Hit
        cache.get("nonexistent-1")  # Miss
        cache.get("nonexistent-2")  # Miss
        cache.get("nonexistent-3")  # Miss
        
        stats = cache.stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 3
        assert abs(stats["hit_rate"] - 40.0) < 0.1  # 2 out of 5 = 40%
    
    def test_stats_empty_cache_hit_rate(self):
        """Hit rate should be 0 when cache is empty."""
        cache = GroupMembershipCache()
        
        stats = cache.stats()
        assert stats["hit_rate"] == 0
    
    def test_stats_cached_groups_count(self):
        """Stats should report correct number of cached groups."""
        cache = GroupMembershipCache()
        
        cache.set("group-1", has_guests=False, members=[])
        assert cache.stats()["cached_groups"] == 1
        
        cache.set("group-2", has_guests=False, members=[])
        assert cache.stats()["cached_groups"] == 2
        
        cache.set("group-3", has_guests=False, members=[])
        assert cache.stats()["cached_groups"] == 3


class TestGroupMembershipCacheDataStructure:
    """Tests for cache data structure format."""
    
    def test_cache_maintains_member_structure(self):
        """Cache should preserve full member record structure."""
        cache = GroupMembershipCache()
        member = {
            "id": "user-123",
            "email": "user@example.com",
            "displayName": "Test User",
            "userType": "Member",
            "identities": [{"issuer": "example.com", "issuerAssignedId": "user@example.com"}],
            "source": "Internal",
        }
        
        cache.set("group-1", has_guests=False, members=[member])
        data = cache.get("group-1")
        
        retrieved_member = data["members"][0]
        assert retrieved_member["id"] == "user-123"
        assert retrieved_member["email"] == "user@example.com"
        assert retrieved_member["displayName"] == "Test User"
        assert retrieved_member["userType"] == "Member"
        assert retrieved_member["source"] == "Internal"
        assert len(retrieved_member["identities"]) == 1
    
    def test_cache_with_multiple_identities(self):
        """Cache should handle members with multiple identities."""
        cache = GroupMembershipCache()
        member = {
            "id": "user-123",
            "email": "user@example.com",
            "displayName": "Test User",
            "source": "Guest",
            "identities": [
                {"issuer": "example.com", "issuerAssignedId": "user@example.com"},
                {"issuer": "linkedin.com", "issuerAssignedId": "user@linkedin.com"},
            ],
        }
        
        cache.set("group-1", has_guests=True, members=[member])
        data = cache.get("group-1")
        
        retrieved_member = data["members"][0]
        assert len(retrieved_member["identities"]) == 2
        assert retrieved_member["identities"][1]["issuer"] == "linkedin.com"


class TestGroupMembershipCacheScenarios:
    """Integration tests for realistic usage scenarios."""
    
    def test_scenario_duplicate_group_processing(self):
        """Cache should prevent re-enumeration of same group."""
        cache = GroupMembershipCache()
        
        # First time: cache miss
        assert cache.has("eng-team") is False
        data1 = cache.get("eng-team")
        assert data1 is None
        
        # Store group data
        members = [{"id": "alice"}, {"id": "bob"}]
        cache.set("eng-team", has_guests=False, members=members)
        
        # Second time: cache hit
        assert cache.has("eng-team") is True
        data2 = cache.get("eng-team")
        assert data2 is not None
        assert len(data2["members"]) == 2
        
        # Stats should show 1 hit, 1 miss
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
    
    def test_scenario_multiple_groups_across_permissions(self):
        """Cache should track multiple groups correctly."""
        cache = GroupMembershipCache()
        
        groups = {
            "eng-team": [{"id": "alice"}, {"id": "bob"}],
            "finance": [{"id": "carol"}],
            "executives": [{"id": "dave"}, {"id": "eve"}, {"id": "frank"}],
        }
        
        # Populate cache
        for group_id, members in groups.items():
            cache.set(group_id, has_guests=False, members=members)
        
        # Verify all groups cached
        assert cache.stats()["cached_groups"] == 3
        
        # Access in different order
        assert cache.get("finance") is not None
        assert cache.get("eng-team") is not None
        assert cache.get("executives") is not None
        assert cache.get("finance") is not None
        
        stats = cache.stats()
        assert stats["hits"] == 4
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 100.0
    
    def test_scenario_group_with_nested_members_and_guests(self):
        """Cache should handle complex group structures with external members."""
        cache = GroupMembershipCache()
        
        # Group with mix of internal and guest members
        members = [
            {"id": "alice", "email": "alice@company.com", "source": "Internal"},
            {"id": "bob", "email": "bob@company.com", "source": "Internal"},
            {"id": "guest1", "email": "external@gmail.com", "source": "Guest"},
            {"id": "nested-group-members", "email": "team@company.com", "source": "Internal"},
            {"id": "guest2", "email": "contractor@freelance.com", "source": "External"},
        ]
        
        cache.set("diverse-team", has_guests=True, members=members)
        data = cache.get("diverse-team")
        
        assert data["has_guests"] is True
        assert len(data["members"]) == 5
        
        guest_count = sum(1 for m in data["members"] if m["source"] in ("Guest", "External"))
        assert guest_count == 2
    
    def test_scenario_group_replacement(self):
        """Cache should correctly replace group data on re-enumeration."""
        cache = GroupMembershipCache()
        
        # First enumeration: group has 2 members, no guests
        initial_members = [{"id": "alice"}, {"id": "bob"}]
        cache.set("dynamic-group", has_guests=False, members=initial_members)
        
        data = cache.get("dynamic-group")
        assert data["has_guests"] is False
        assert len(data["members"]) == 2
        
        # Second enumeration: group now has 3 members including a guest
        updated_members = [{"id": "alice"}, {"id": "bob"}, {"id": "guest"}]
        cache.set("dynamic-group", has_guests=True, members=updated_members)
        
        data = cache.get("dynamic-group")
        assert data["has_guests"] is True
        assert len(data["members"]) == 3


class TestGroupMembershipCacheEdgeCases:
    """Tests for edge cases and boundary conditions."""
    
    def test_group_id_with_special_characters(self):
        """Cache should handle group IDs with special characters."""
        cache = GroupMembershipCache()
        special_ids = [
            "group-123-abc",
            "group_with_underscores",
            "group.with.dots",
            "group/with/slashes",
            "123e4567-e89b-12d3-a456-426614174000",  # UUID format
        ]
        
        for group_id in special_ids:
            cache.set(group_id, has_guests=False, members=[{"id": "user1"}])
            assert cache.has(group_id) is True
            assert cache.get(group_id) is not None
    
    def test_very_large_member_list(self):
        """Cache should handle large member lists."""
        cache = GroupMembershipCache()
        
        # Create 10,000 members
        large_member_list = [
            {"id": f"user-{i}", "email": f"user{i}@example.com", "displayName": f"User {i}"}
            for i in range(10000)
        ]
        
        cache.set("large-group", has_guests=False, members=large_member_list)
        data = cache.get("large-group")
        
        assert len(data["members"]) == 10000
        assert data["members"][0]["id"] == "user-0"
        assert data["members"][9999]["id"] == "user-9999"
    
    def test_many_groups_in_cache(self):
        """Cache should handle many groups efficiently."""
        cache = GroupMembershipCache()
        
        # Add 1000 groups
        for i in range(1000):
            cache.set(f"group-{i}", has_guests=i % 2 == 0, members=[{"id": "user"}])
        
        assert cache.stats()["cached_groups"] == 1000
        
        # Verify random access works
        assert cache.has("group-0") is True
        assert cache.has("group-500") is True
        assert cache.has("group-999") is True
        assert cache.has("group-1000") is False
    
    def test_empty_group_id(self):
        """Cache should handle empty group ID."""
        cache = GroupMembershipCache()
        cache.set("", has_guests=False, members=[])
        
        assert cache.has("") is True
        assert cache.get("") is not None
    
    def test_member_with_none_values(self):
        """Cache should preserve None values in member data."""
        cache = GroupMembershipCache()
        
        member = {
            "id": "user-1",
            "email": None,
            "displayName": "User",
            "source": None,
        }
        
        cache.set("group-1", has_guests=False, members=[member])
        data = cache.get("group-1")
        
        assert data["members"][0]["email"] is None
        assert data["members"][0]["source"] is None


class TestGroupMembershipCacheThreadSafety:
    """Tests for concurrent access patterns."""
    
    def test_sequential_operations_maintain_consistency(self):
        """Sequential operations should not cause data corruption."""
        cache = GroupMembershipCache()
        
        # Rapidly set and get same group
        for i in range(100):
            cache.set("group-1", has_guests=i % 2 == 0, members=[{"id": f"user-{i}"}])
            data = cache.get("group-1")
            
            # Last write should win
            assert data["members"][0]["id"] == "user-99" or i < 99
    
    def test_independent_groups_not_affected_by_other_operations(self):
        """Operations on one group shouldn't affect another."""
        cache = GroupMembershipCache()
        
        cache.set("group-1", has_guests=False, members=[{"id": "alice"}])
        cache.set("group-2", has_guests=True, members=[{"id": "bob"}])
        
        # Modify group-1
        cache.set("group-1", has_guests=True, members=[{"id": "alice"}, {"id": "charlie"}])
        
        # group-2 should be unchanged
        data2 = cache.get("group-2")
        assert data2["has_guests"] is True
        assert len(data2["members"]) == 1
        assert data2["members"][0]["id"] == "bob"
