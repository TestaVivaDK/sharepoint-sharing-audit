"""GroupMembershipCache: Type-safe caching for enumerated group members."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class GroupMembershipCache:
    """
    Type-safe cache for storing enumerated group members across permission processing.
    
    Prevents re-enumeration of the same group when processing multiple permissions
    that reference the same group. Maintains consistency across OneDrive, SharePoint,
    and link processing flows.
    
    Cache Structure:
        {
            group_id: {
                "has_guests": bool,  # True if group contains any External/Guest members
                "members": [         # Complete member list (users + nested members)
                    {
                        "id": str,
                        "email": str,
                        "displayName": str,
                        "userType": str,
                        "identities": list,
                        "source": str,  # "Internal", "External", or "Guest"
                    },
                    ...
                ]
            }
        }
    
    Lifecycle:
    - Created once per collector run (full or delta scan)
    - Populated during permission processing as groups are enumerated
    - Cleared between runs to prevent stale data
    - Shared across all permission processor functions (process_user_permission,
      process_group_permission, process_link_permission)
    
    Example:
        >>> cache = GroupMembershipCache()
        >>> cache.set("group-123", has_guests=True, members=[...])
        >>> if cache.has("group-123"):
        ...     data = cache.get("group-123")
        ...     print(f"Cached: has_guests={data['has_guests']}")
    """
    
    def __init__(self):
        """Initialize empty cache."""
        self._cache: dict = {}
        self._hits: int = 0
        self._misses: int = 0
        logger.debug("GroupMembershipCache initialized")
    
    def get(self, group_id: str) -> Optional[dict]:
        """
        Retrieve cached group membership data.
        
        Args:
            group_id: Group ID to look up.
            
        Returns:
            Dict with keys:
                - "has_guests": bool - True if group contains External/Guest members
                - "members": list - All group members (recursively enumerated)
            None if group is not cached.
            
        Example:
            >>> data = cache.get("group-123")
            >>> if data:
            ...     print(f"Members: {len(data['members'])}")
        """
        if group_id in self._cache:
            self._hits += 1
            logger.debug(f"Cache hit for group {group_id}")
            # Return a copy to prevent external modifications from affecting cache
            cached = self._cache[group_id]
            return {
                "has_guests": cached["has_guests"],
                "members": cached["members"].copy(),  # Copy the members list
            }
        
        self._misses += 1
        logger.debug(f"Cache miss for group {group_id}")
        return None
    
    def set(self, group_id: str, has_guests: bool, members: list) -> None:
        """
        Store group membership data in cache.
        
        Args:
            group_id: Group ID to cache.
            has_guests: True if group contains any External/Guest members.
            members: List of all group members (including nested members from groups).
                    Each member should have: id, email, displayName, userType, identities, source.
            
        Example:
            >>> cache.set("group-123", has_guests=True, members=[...])
            >>> # Group data now cached, subsequent requests won't re-enumerate
        """
        self._cache[group_id] = {
            "has_guests": has_guests,
            "members": members.copy(),  # Store copy to prevent external mutation
        }
        logger.debug(f"Cached group {group_id} with {len(members)} members, has_guests={has_guests}")
    
    def has(self, group_id: str) -> bool:
        """
        Check if group is in cache.
        
        Args:
            group_id: Group ID to check.
            
        Returns:
            True if group is cached, False otherwise.
            
        Example:
            >>> if cache.has("group-123"):
            ...     data = cache.get("group-123")
        """
        return group_id in self._cache
    
    def clear(self) -> None:
        """
        Clear all cached data.
        
        Use between collector runs to prevent stale group membership data.
        
        Example:
            >>> cache.clear()
            >>> # Cache now empty, all groups will be re-enumerated on next access
        """
        count = len(self._cache)
        self._cache.clear()
        logger.debug(f"Cleared cache: {count} entries removed")
    
    def stats(self) -> dict:
        """
        Return cache statistics for debugging.
        
        Returns:
            Dict with:
                - "cached_groups": int - Number of groups in cache
                - "hits": int - Cache hits since initialization
                - "misses": int - Cache misses since initialization
                - "hit_rate": float - Hit rate as percentage (0-100)
                
        Example:
            >>> stats = cache.stats()
            >>> print(f"Hit rate: {stats['hit_rate']:.1f}%")
        """
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        return {
            "cached_groups": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
        }
    
    def __repr__(self) -> str:
        """String representation for debugging."""
        stats = self.stats()
        return (
            f"GroupMembershipCache("
            f"cached={stats['cached_groups']}, "
            f"hits={stats['hits']}, "
            f"misses={stats['misses']}, "
            f"hit_rate={stats['hit_rate']:.1f}%)"
        )
