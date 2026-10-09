"""
DefectTrackerService - Orchestrator for defect tracking with caching.

This service coordinates between DefectCache and DefectFetcher to provide
efficient defect information retrieval with cache-first lookup and
graceful fallback on Jira connection errors.
"""

import logging
from typing import TYPE_CHECKING

from backend.services.defect_cache import DefectInfo, DefectCache
from backend.services.defect_fetcher import DefectFetcher

if TYPE_CHECKING:
    from backend.services.jira_service import JiraService

logger = logging.getLogger("backend.services.defect_tracker_service")


class JiraConnectionError(Exception):
    """Raised when Jira connection fails."""
    pass


class DefectTrackerService:
    """
    Orchestrator for defect tracking with cache-first lookup.
    
    Integrates DefectCache and DefectFetcher to provide efficient
    defect information retrieval with automatic caching and error handling.
    """
    
    def __init__(
        self,
        jira_service: 'JiraService',
        defect_cache: DefectCache,
        defect_fetcher: DefectFetcher
    ):
        """
        Initialize with Jira service, cache, and fetcher.
        
        Args:
            jira_service: JiraService instance for API communication
            defect_cache: DefectCache instance for caching
            defect_fetcher: DefectFetcher instance for fetching from Jira
        """
        self.jira_service = jira_service
        self.defect_cache = defect_cache
        self.defect_fetcher = defect_fetcher
        logger.info("DefectTrackerService initialized")
    
    def get_defects_for_test_case(
        self,
        test_case_jira_id: str,
        force_refresh: bool = False
    ) -> tuple[list[DefectInfo], bool]:
        """
        Get defects for test case with cache-first lookup.
        
        Args:
            test_case_jira_id: Jira issue key of test case
            force_refresh: Skip cache and fetch fresh data
            
        Returns:
            Tuple of (list of defects sorted by creation date, is_stale flag)
            is_stale is True when using cached fallback due to Jira errors
        """
        # Check cache first (unless force_refresh)
        if not force_refresh:
            cached = self.defect_cache.get(test_case_jira_id)
            if cached is not None:
                logger.info(f"Using cached defects for {test_case_jira_id}")
                return cached, False
        
        # Cache miss or force refresh - fetch from Jira
        try:
            defects = self.defect_fetcher.fetch_defects(test_case_jira_id)
            
            # Update cache
            self.defect_cache.set(test_case_jira_id, defects)
            
            logger.info(f"Fetched and cached {len(defects)} defects for {test_case_jira_id}")
            return defects, False
            
        except Exception as e:
            logger.error(f"Failed to fetch defects for {test_case_jira_id}: {e}")
            
            # Try to use cached data as fallback
            cached = self.defect_cache.get(test_case_jira_id)
            if cached is not None:
                logger.warning(f"Using stale cache for {test_case_jira_id} due to Jira error")
                return cached, True
            
            # No cache available - return empty list
            logger.error(f"No cached data available for {test_case_jira_id}")
            return [], True
    
    def get_defects_batch(
        self,
        test_case_jira_ids: list[str],
        force_refresh: bool = False
    ) -> dict[str, tuple[list[DefectInfo], bool]]:
        """
        Get defects for multiple test cases with batching.
        
        Args:
            test_case_jira_ids: List of Jira issue keys for test cases
            force_refresh: Skip cache and fetch fresh data
            
        Returns:
            Dictionary mapping test case ID to (list of defects, is_stale flag)
        """
        if not test_case_jira_ids:
            return {}
        
        logger.info(f"Getting defects for {len(test_case_jira_ids)} test cases")
        
        results: dict[str, tuple[list[DefectInfo], bool]] = {}
        
        # Check cache for each test case (unless force_refresh)
        to_fetch = []
        if not force_refresh:
            for tc_id in test_case_jira_ids:
                cached = self.defect_cache.get(tc_id)
                if cached is not None:
                    results[tc_id] = (cached, False)
                else:
                    to_fetch.append(tc_id)
        else:
            to_fetch = test_case_jira_ids
        
        # Fetch missing items from Jira
        if to_fetch:
            logger.info(f"Fetching {len(to_fetch)} test cases from Jira")
            
            try:
                batch_results = self.defect_fetcher.fetch_defects_batch(to_fetch)
                
                # Cache and add to results
                for tc_id, defects in batch_results.items():
                    self.defect_cache.set(tc_id, defects)
                    results[tc_id] = (defects, False)
                
                logger.info(f"Batch fetch complete: {len(batch_results)} test cases")
                
            except Exception as e:
                logger.error(f"Batch fetch failed: {e}")
                
                # Try cached fallback for failed items
                for tc_id in to_fetch:
                    if tc_id not in results:
                        cached = self.defect_cache.get(tc_id)
                        if cached is not None:
                            logger.warning(f"Using stale cache for {tc_id} due to Jira error")
                            results[tc_id] = (cached, True)
                        else:
                            logger.error(f"No cached data available for {tc_id}")
                            results[tc_id] = ([], True)
        
        return results
    
    def invalidate_cache(self, test_case_jira_id: str) -> None:
        """
        Invalidate cache for a specific test case.
        
        Useful when defects are created/updated and cache should be refreshed.
        
        Args:
            test_case_jira_id: Jira issue key of test case
        """
        self.defect_cache.invalidate(test_case_jira_id)
        logger.info(f"Invalidated cache for {test_case_jira_id}")
    
    def clear_cache(self) -> None:
        """Clear entire defect cache."""
        self.defect_cache.clear()
        logger.info("Cleared entire defect cache")
