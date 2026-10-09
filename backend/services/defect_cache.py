"""
DefectCache - In-memory cache for defect information with TTL expiration.

This service provides caching for Jira defect information to reduce API calls
and improve performance. It supports TTL-based expiration and thread-safe
concurrent access.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("backend.services.defect_cache")


@dataclass
class DefectInfo:
    """Defect information for display in defect trace tables."""
    defect_id: str  # Jira issue key
    summary: str
    status: str
    status_category: str  # "done", "in_progress", "to_do"
    created_at: datetime
    updated_at: datetime
    url: str


@dataclass
class CacheEntry:
    """Cache entry with TTL tracking."""
    data: list[DefectInfo]
    cached_at: datetime
    ttl_seconds: int
    
    def is_expired(self) -> bool:
        """Check if entry has exceeded TTL."""
        now = datetime.now(timezone.utc)
        expiry_time = self.cached_at + timedelta(seconds=self.ttl_seconds)
        return now >= expiry_time


class DefectCache:
    """
    In-memory cache for defect information with TTL expiration.
    
    Thread-safe cache implementation with lazy expiration checking.
    Entries are removed when accessed and found to be expired.
    """
    
    def __init__(self, default_ttl_seconds: int = 300):
        """
        Initialize cache with 5-minute default TTL.
        
        Args:
            default_ttl_seconds: Default time-to-live in seconds (default: 300 = 5 minutes)
        """
        self._cache: dict[str, CacheEntry] = {}
        self._default_ttl = default_ttl_seconds
        self._lock = threading.Lock()
        logger.info(f"DefectCache initialized with {default_ttl_seconds}s TTL")
    
    def get(self, test_case_jira_id: str) -> list[DefectInfo] | None:
        """
        Get cached defects if not expired.
        
        Args:
            test_case_jira_id: Jira issue key of test case
            
        Returns:
            List of DefectInfo if cached and not expired, None otherwise
        """
        with self._lock:
            entry = self._cache.get(test_case_jira_id)
            
            if entry is None:
                logger.debug(f"Cache miss for {test_case_jira_id}")
                return None
            
            if entry.is_expired():
                logger.debug(f"Cache expired for {test_case_jira_id}")
                del self._cache[test_case_jira_id]
                return None
            
            logger.debug(f"Cache hit for {test_case_jira_id} ({len(entry.data)} defects)")
            return entry.data
    
    def set(
        self,
        test_case_jira_id: str,
        defects: list[DefectInfo],
        ttl_seconds: int | None = None
    ) -> None:
        """
        Cache defects with TTL.
        
        Args:
            test_case_jira_id: Jira issue key of test case
            defects: List of defect information to cache
            ttl_seconds: Custom TTL in seconds (uses default if None)
        """
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        
        with self._lock:
            entry = CacheEntry(
                data=defects,
                cached_at=datetime.now(timezone.utc),
                ttl_seconds=ttl
            )
            self._cache[test_case_jira_id] = entry
            logger.debug(f"Cached {len(defects)} defects for {test_case_jira_id} (TTL: {ttl}s)")
    
    def invalidate(self, test_case_jira_id: str) -> None:
        """
        Remove entry from cache.
        
        Args:
            test_case_jira_id: Jira issue key of test case
        """
        with self._lock:
            if test_case_jira_id in self._cache:
                del self._cache[test_case_jira_id]
                logger.debug(f"Invalidated cache for {test_case_jira_id}")
    
    def clear(self) -> None:
        """Clear entire cache."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info(f"Cleared cache ({count} entries removed)")
