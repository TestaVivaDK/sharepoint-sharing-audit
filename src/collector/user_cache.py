"""User cache for storing Microsoft Graph user data during collector execution."""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class UserCache:
    """Thread-safe cache for user data from Microsoft Graph API.
    
    Caches user objects by user ID to avoid redundant API calls during
    a single collector execution. Supports lazy-loading from Graph API.
    
    Attributes:
        _cache: Dict mapping user_id -> {id, email, displayName, userType, ...}
        _graph_client: Reference to GraphClient for lazy-loading
    """
    
    def __init__(self, graph_client=None):
        """Initialize user cache.
        
        Args:
            graph_client: GraphClient instance for lazy-loading. If None, cache is read-only.
        """
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._graph_client = graph_client
    
    def get(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user data from cache, optionally lazy-loading from Graph.
        
        Args:
            user_id: User ID to retrieve.
            
        Returns:
            User dict {id, email, displayName, userType, ...} or None if not found.
        """
        if not user_id:
            return None
        
        # Check cache first
        if user_id in self._cache:
            return self._cache[user_id]
        
        # Lazy-load from Graph if available
        if self._graph_client:
            try:
                users = self._graph_client.batch_get_users([user_id])
                if user_id in users:
                    user_data = users[user_id]
                    self._cache[user_id] = user_data
                    return user_data
            except Exception as e:
                logger.warning(f"Failed to lazy-load user {user_id}: {e}")
        
        return None
    
    def set(self, user_id: str, user_data: Dict[str, Any]) -> None:
        """Store user data in cache.
        
        Args:
            user_id: User ID as key.
            user_data: User object to cache {id, email, displayName, userType, ...}.
        """
        if user_id and user_data:
            self._cache[user_id] = user_data
    
    def batch_populate(self, user_ids: list[str]) -> None:
        """Pre-populate cache with batch of user IDs from Graph API.
        
        Args:
            user_ids: List of user IDs to fetch (will be batched in groups of 20).
        """
        if not self._graph_client or not user_ids:
            return
        
        # Filter out already-cached users
        uncached_ids = [uid for uid in user_ids if uid not in self._cache]
        if not uncached_ids:
            return
        
        # Batch fetch in groups of 20 (Graph API batch limit)
        for i in range(0, len(uncached_ids), 20):
            batch = uncached_ids[i:i+20]
            try:
                users = self._graph_client.batch_get_users(batch)
                self._cache.update(users)
                logger.debug(f"Cached {len(users)} users ({len(uncached_ids) - len(users)} not found)")
            except Exception as e:
                logger.warning(f"Failed to batch-populate cache for {len(batch)} users: {e}")
    
    def clear(self) -> None:
        """Clear all cached data."""
        self._cache.clear()
    
    def stats(self) -> Dict[str, int]:
        """Get cache statistics.
        
        Returns:
            Dict with cache_size and cached_user_count.
        """
        return {
            "cache_size": len(self._cache),
            "cached_user_count": len(self._cache),
        }
