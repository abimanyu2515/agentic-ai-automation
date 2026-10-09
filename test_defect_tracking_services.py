"""
Integration test for defect tracking services.

This test verifies that DefectCache, DefectFetcher, and DefectTrackerService
work correctly together in mock mode.
"""

import sys
from datetime import datetime, timezone
from backend.services.jira_service import JiraService
from backend.services.defect_cache import DefectCache, DefectInfo
from backend.services.defect_fetcher import DefectFetcher
from backend.services.defect_tracker_service import DefectTrackerService


def test_defect_cache():
    """Test DefectCache basic operations."""
    print("\n=== Testing DefectCache ===")
    
    cache = DefectCache(default_ttl_seconds=300)
    
    # Create sample defect
    defect = DefectInfo(
        defect_id="TEST-123",
        summary="Sample defect",
        status="Open",
        status_category="to_do",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        url="https://jira.example.com/browse/TEST-123"
    )
    
    # Test set and get
    cache.set("TC-001", [defect])
    cached = cache.get("TC-001")
    
    assert cached is not None, "Cache should return stored data"
    assert len(cached) == 1, "Cache should have 1 defect"
    assert cached[0].defect_id == "TEST-123", "Defect ID should match"
    
    print("✓ Cache set/get works")
    
    # Test cache miss
    missing = cache.get("TC-999")
    assert missing is None, "Cache miss should return None"
    print("✓ Cache miss works")
    
    # Test invalidate
    cache.invalidate("TC-001")
    after_invalidate = cache.get("TC-001")
    assert after_invalidate is None, "Invalidated entry should return None"
    print("✓ Cache invalidation works")
    
    # Test clear
    cache.set("TC-001", [defect])
    cache.set("TC-002", [defect])
    cache.clear()
    assert cache.get("TC-001") is None, "Cleared cache should be empty"
    assert cache.get("TC-002") is None, "Cleared cache should be empty"
    print("✓ Cache clear works")


def test_defect_fetcher():
    """Test DefectFetcher with mock JiraService."""
    print("\n=== Testing DefectFetcher ===")
    
    jira_service = JiraService()  # Will run in mock mode
    fetcher = DefectFetcher(jira_service, batch_size=50)
    
    # Test single fetch (mock mode returns empty list)
    defects = fetcher.fetch_defects("TC-001")
    assert isinstance(defects, list), "Should return a list"
    print(f"✓ Single fetch works (returned {len(defects)} defects)")
    
    # Test batch fetch
    batch_results = fetcher.fetch_defects_batch(["TC-001", "TC-002", "TC-003"])
    assert isinstance(batch_results, dict), "Should return a dict"
    assert len(batch_results) == 3, "Should have results for all 3 test cases"
    print(f"✓ Batch fetch works (processed {len(batch_results)} test cases)")


def test_defect_tracker_service():
    """Test DefectTrackerService integration."""
    print("\n=== Testing DefectTrackerService ===")
    
    # Initialize services
    jira_service = JiraService()
    cache = DefectCache(default_ttl_seconds=300)
    fetcher = DefectFetcher(jira_service, batch_size=50)
    tracker = DefectTrackerService(jira_service, cache, fetcher)
    
    # Test get_defects_for_test_case
    defects, is_stale = tracker.get_defects_for_test_case("TC-001")
    assert isinstance(defects, list), "Should return a list"
    assert isinstance(is_stale, bool), "Should return stale flag"
    print(f"✓ Single get works (returned {len(defects)} defects, stale={is_stale})")
    
    # Test cache hit (second call should use cache)
    defects2, is_stale2 = tracker.get_defects_for_test_case("TC-001")
    assert is_stale2 is False, "Cached data should not be stale"
    print("✓ Cache hit works")
    
    # Test force refresh
    defects3, is_stale3 = tracker.get_defects_for_test_case("TC-001", force_refresh=True)
    assert isinstance(defects3, list), "Force refresh should return list"
    print("✓ Force refresh works")
    
    # Test batch get
    batch_results = tracker.get_defects_batch(["TC-001", "TC-002", "TC-003"])
    assert isinstance(batch_results, dict), "Should return a dict"
    assert len(batch_results) == 3, "Should have results for all 3 test cases"
    print(f"✓ Batch get works (processed {len(batch_results)} test cases)")
    
    # Verify batch results structure
    for tc_id, (defects, is_stale) in batch_results.items():
        assert isinstance(defects, list), f"Defects for {tc_id} should be a list"
        assert isinstance(is_stale, bool), f"Stale flag for {tc_id} should be a bool"
    print("✓ Batch results structure correct")
    
    # Test cache invalidation
    tracker.invalidate_cache("TC-001")
    print("✓ Cache invalidation works")
    
    # Test cache clear
    tracker.clear_cache()
    print("✓ Cache clear works")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Defect Tracking Services Integration Test")
    print("=" * 60)
    
    try:
        test_defect_cache()
        test_defect_fetcher()
        test_defect_tracker_service()
        
        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)
        return 0
    except Exception as e:
        print("\n" + "=" * 60)
        print(f"✗ Test failed: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
