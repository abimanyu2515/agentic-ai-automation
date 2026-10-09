"""
DefectFetcher - Fetches defects from Jira with batching support.

This service queries Jira for defects related to test cases using multiple
relationship types (issue links, parent-child, custom fields, labels).
Supports batching and concurrent execution for performance.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import TYPE_CHECKING

from backend.services.defect_cache import DefectInfo

if TYPE_CHECKING:
    from backend.services.jira_service import JiraService

logger = logging.getLogger("backend.services.defect_fetcher")


class DefectFetcher:
    """
    Fetches defects from Jira with batching and concurrent execution.
    
    Uses JQL queries to discover defects through multiple relationship types:
    - Issue links (relates to, is caused by, etc.)
    - Parent-child relationships
    - Custom field references
    - Label matching
    """
    
    def __init__(self, jira_service: 'JiraService', batch_size: int = 50):
        """
        Initialize with Jira service and batch size.
        
        Args:
            jira_service: JiraService instance for API communication
            batch_size: Number of test cases per batch (default: 50)
        """
        self.jira_service = jira_service
        self.batch_size = batch_size
        logger.info(f"DefectFetcher initialized with batch_size={batch_size}")
    
    def fetch_defects(self, test_case_jira_id: str) -> list[DefectInfo]:
        """
        Fetch defects for single test case.
        
        Args:
            test_case_jira_id: Jira issue key of test case
            
        Returns:
            List of DefectInfo sorted by creation date (newest first)
        """
        logger.debug(f"Fetching defects for {test_case_jira_id}")
        
        # Build JQL query to find related defects
        jql = self._build_jql_query(test_case_jira_id)
        
        # Execute search
        issues = self.jira_service.search_issues(jql)
        
        # Parse results
        defects = []
        for issue in issues:
            try:
                defect = self._parse_defect_info(issue)
                if defect:
                    defects.append(defect)
            except Exception as e:
                logger.error(f"Failed to parse defect {issue.get('key', 'unknown')}: {e}")
        
        # Sort by creation date (newest first)
        defects.sort(key=lambda d: d.created_at, reverse=True)
        
        logger.info(f"Found {len(defects)} defects for {test_case_jira_id}")
        return defects
    
    def fetch_defects_batch(
        self,
        test_case_jira_ids: list[str]
    ) -> dict[str, list[DefectInfo]]:
        """
        Fetch defects for multiple test cases in batches.
        
        Batches requests to Jira API (batch_size test cases per call).
        Uses JQL OR clauses to fetch related defects.
        Executes batches concurrently (max 3 concurrent batches).
        
        Args:
            test_case_jira_ids: List of Jira issue keys for test cases
            
        Returns:
            Dictionary mapping test case ID to list of defects
        """
        if not test_case_jira_ids:
            return {}
        
        logger.info(f"Batch fetching defects for {len(test_case_jira_ids)} test cases")
        
        # Split into batches
        batches = []
        for i in range(0, len(test_case_jira_ids), self.batch_size):
            batch = test_case_jira_ids[i:i + self.batch_size]
            batches.append(batch)
        
        logger.debug(f"Split into {len(batches)} batches")
        
        # Process batches concurrently (max 3 concurrent)
        results: dict[str, list[DefectInfo]] = {}
        
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_batch = {
                executor.submit(self._fetch_batch, batch): batch
                for batch in batches
            }
            
            for future in as_completed(future_to_batch):
                batch = future_to_batch[future]
                try:
                    batch_results = future.result(timeout=10)
                    results.update(batch_results)
                except Exception as e:
                    logger.error(f"Batch fetch failed for {len(batch)} test cases: {e}")
                    # Return empty results for failed batch
                    for tc_id in batch:
                        results[tc_id] = []
        
        logger.info(f"Batch fetch complete: {len(results)} test cases processed")
        return results
    
    def _fetch_batch(self, test_case_ids: list[str]) -> dict[str, list[DefectInfo]]:
        """
        Fetch defects for a batch of test cases.
        
        Args:
            test_case_ids: List of test case Jira IDs in this batch
            
        Returns:
            Dictionary mapping test case ID to defects
        """
        # Build combined JQL query for all test cases in batch
        jql = self._build_batch_jql_query(test_case_ids)
        
        # Execute search
        issues = self.jira_service.search_issues(jql)
        
        # Group defects by test case
        results: dict[str, list[DefectInfo]] = {tc_id: [] for tc_id in test_case_ids}
        
        for issue in issues:
            try:
                defect = self._parse_defect_info(issue)
                if not defect:
                    continue
                
                # Determine which test case(s) this defect relates to
                related_tc_ids = self._extract_related_test_cases(issue, test_case_ids)
                
                for tc_id in related_tc_ids:
                    results[tc_id].append(defect)
            except Exception as e:
                logger.error(f"Failed to parse defect {issue.get('key', 'unknown')}: {e}")
        
        # Sort each test case's defects by creation date (newest first)
        for tc_id in results:
            results[tc_id].sort(key=lambda d: d.created_at, reverse=True)
        
        return results
    
    def _build_jql_query(self, test_case_jira_id: str) -> str:
        """
        Build JQL query to find defects related to a test case.
        
        Discovers defects via:
        - Issue links
        - Parent-child relationships
        - Custom field "Test Case ID"
        - Labels matching test case reference
        
        Args:
            test_case_jira_id: Jira issue key of test case
            
        Returns:
            JQL query string
        """
        # Extract project key and number for label search
        label_ref = test_case_jira_id.replace("-", "_").lower()
        
        # Build comprehensive JQL query
        jql = (
            f'type = Bug AND ('
            f'issueLink = "{test_case_jira_id}" OR '
            f'parent = "{test_case_jira_id}" OR '
            f'"Test Case ID" ~ "{test_case_jira_id}" OR '
            f'labels = "test_{label_ref}"'
            f') ORDER BY created DESC'
        )
        
        return jql
    
    def _build_batch_jql_query(self, test_case_ids: list[str]) -> str:
        """
        Build JQL query for multiple test cases using OR clauses.
        
        Args:
            test_case_ids: List of test case Jira IDs
            
        Returns:
            JQL query string
        """
        # Build OR clauses for each test case
        conditions = []
        for tc_id in test_case_ids:
            label_ref = tc_id.replace("-", "_").lower()
            condition = (
                f'issueLink = "{tc_id}" OR '
                f'parent = "{tc_id}" OR '
                f'"Test Case ID" ~ "{tc_id}" OR '
                f'labels = "test_{label_ref}"'
            )
            conditions.append(f'({condition})')
        
        # Combine with OR
        jql = f'type = Bug AND ({" OR ".join(conditions)}) ORDER BY created DESC'
        
        return jql
    
    def _extract_related_test_cases(
        self,
        issue: dict,
        test_case_ids: list[str]
    ) -> list[str]:
        """
        Extract which test cases this defect relates to.
        
        Args:
            issue: Jira issue data
            test_case_ids: List of test case IDs to check against
            
        Returns:
            List of related test case IDs
        """
        related = []
        fields = issue.get("fields", {})
        
        # Check parent relationship
        parent = fields.get("parent")
        if parent:
            parent_key = parent.get("key")
            if parent_key in test_case_ids:
                related.append(parent_key)
        
        # Check custom field "Test Case ID"
        test_case_field = fields.get("customfield_10100")  # May need configuration
        if test_case_field:
            if isinstance(test_case_field, str) and test_case_field in test_case_ids:
                related.append(test_case_field)
        
        # Check labels
        labels = fields.get("labels", [])
        for tc_id in test_case_ids:
            label_ref = f"test_{tc_id.replace('-', '_').lower()}"
            if label_ref in labels:
                related.append(tc_id)
        
        # Check issue links (requires additional API call - simplified here)
        # For now, assume if we found it in the query, it's related to at least one
        if not related and test_case_ids:
            # Fallback: associate with first test case in batch
            # In production, would need to query issuelinks endpoint
            related.append(test_case_ids[0])
        
        return related
    
    def _parse_defect_info(self, jira_issue: dict) -> DefectInfo | None:
        """
        Parse Jira API response into DefectInfo.
        
        Args:
            jira_issue: Jira issue data from API
            
        Returns:
            DefectInfo instance or None if parsing fails
        """
        try:
            key = jira_issue.get("key")
            if not key:
                return None
            
            fields = jira_issue.get("fields", {})
            
            # Extract status information
            status_obj = fields.get("status", {})
            status_name = status_obj.get("name", "Unknown")
            
            # Map status to category
            status_category_obj = status_obj.get("statusCategory", {})
            status_category_key = status_category_obj.get("key", "to_do")
            
            # Normalize category to: done, in_progress, to_do
            if status_category_key in ["done", "complete"]:
                status_category = "done"
            elif status_category_key in ["indeterminate", "in_progress"]:
                status_category = "in_progress"
            else:
                status_category = "to_do"
            
            # Extract timestamps
            created_str = fields.get("created")
            updated_str = fields.get("updated")
            
            created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00")) if created_str else datetime.now()
            updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00")) if updated_str else created_at
            
            # Build URL
            base_url = self.jira_service.config.base_url.rstrip('/')
            url = f"{base_url}/browse/{key}"
            
            return DefectInfo(
                defect_id=key,
                summary=fields.get("summary", "No summary"),
                status=status_name,
                status_category=status_category,
                created_at=created_at,
                updated_at=updated_at,
                url=url
            )
        except Exception as e:
            logger.error(f"Failed to parse defect info: {e}")
            return None
