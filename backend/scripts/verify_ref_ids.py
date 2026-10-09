"""
Verification script to check that all scenarios and test cases have ref_ids.

Run with: python -m backend.scripts.verify_ref_ids
"""

import logging
from sqlalchemy import select
from backend.database.db import SessionLocal
from backend.database.db_models import ScenarioDB, TestCaseDB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def verify_scenario_ref_ids():
    """Verify all scenarios have scenario_ref_id."""
    session = SessionLocal()
    try:
        # Count scenarios without ref_id
        stmt = select(ScenarioDB).where(
            ScenarioDB.is_deleted == False,
            ScenarioDB.scenario_ref_id.is_(None)
        )
        missing = session.scalars(stmt).all()
        
        # Count scenarios with ref_id
        stmt_with = select(ScenarioDB).where(
            ScenarioDB.is_deleted == False,
            ScenarioDB.scenario_ref_id.isnot(None)
        )
        with_ref = session.scalars(stmt_with).all()
        
        logger.info(f"Scenarios WITH ref_id: {len(with_ref)}")
        logger.info(f"Scenarios WITHOUT ref_id: {len(missing)}")
        
        if missing:
            logger.warning("❌ Some scenarios are missing ref_ids:")
            for sc in missing[:5]:  # Show first 5
                logger.warning(f"  - Scenario '{sc.scenario_name}' (ID: {sc.id})")
            return False
        else:
            logger.info("✅ All scenarios have ref_ids")
            return True
            
    finally:
        session.close()


def verify_test_case_ref_ids():
    """Verify all test cases have test_case_ref_id."""
    session = SessionLocal()
    try:
        # Count test cases without ref_id
        stmt = select(TestCaseDB).where(
            TestCaseDB.is_deleted == False,
            TestCaseDB.test_case_ref_id.is_(None)
        )
        missing = session.scalars(stmt).all()
        
        # Count test cases with ref_id
        stmt_with = select(TestCaseDB).where(
            TestCaseDB.is_deleted == False,
            TestCaseDB.test_case_ref_id.isnot(None)
        )
        with_ref = session.scalars(stmt_with).all()
        
        logger.info(f"Test Cases WITH ref_id: {len(with_ref)}")
        logger.info(f"Test Cases WITHOUT ref_id: {len(missing)}")
        
        if missing:
            logger.warning("❌ Some test cases are missing ref_ids:")
            for tc in missing[:5]:  # Show first 5
                logger.warning(f"  - Test Case '{tc.title}' (ID: {tc.id})")
            return False
        else:
            logger.info("✅ All test cases have ref_ids")
            return True
            
    finally:
        session.close()


def show_sample_refs():
    """Show sample ref_ids from the database."""
    session = SessionLocal()
    try:
        # Get 5 sample scenarios
        stmt = select(ScenarioDB).where(
            ScenarioDB.is_deleted == False,
            ScenarioDB.scenario_ref_id.isnot(None)
        ).limit(5)
        scenarios = session.scalars(stmt).all()
        
        logger.info("\n📋 Sample Scenario Ref IDs:")
        for sc in scenarios:
            logger.info(f"  {sc.scenario_ref_id} - {sc.scenario_name}")
        
        # Get 5 sample test cases
        stmt = select(TestCaseDB).where(
            TestCaseDB.is_deleted == False,
            TestCaseDB.test_case_ref_id.isnot(None)
        ).limit(5)
        test_cases = session.scalars(stmt).all()
        
        logger.info("\n📋 Sample Test Case Ref IDs:")
        for tc in test_cases:
            logger.info(f"  {tc.test_case_ref_id} - {tc.title}")
            
    finally:
        session.close()


def main():
    """Run the verification."""
    logger.info("=" * 80)
    logger.info("Verifying ref_id population...")
    logger.info("=" * 80)
    
    try:
        scenarios_ok = verify_scenario_ref_ids()
        logger.info("")
        test_cases_ok = verify_test_case_ref_ids()
        logger.info("")
        
        if scenarios_ok and test_cases_ok:
            logger.info("=" * 80)
            logger.info("✅ VERIFICATION PASSED - All records have ref_ids")
            logger.info("=" * 80)
            show_sample_refs()
        else:
            logger.error("=" * 80)
            logger.error("❌ VERIFICATION FAILED - Some records missing ref_ids")
            logger.error("=" * 80)
            logger.error("Run the backfill script: python -m backend.scripts.backfill_ref_ids")
        
    except Exception as e:
        logger.error(f"\n❌ Verification failed with error: {e}")
        raise


if __name__ == "__main__":
    main()
