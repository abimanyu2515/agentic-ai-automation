"""
Migration script to backfill scenario_ref_id and test_case_ref_id for existing records.

This script:
1. Finds all scenarios without scenario_ref_id
2. Groups them by requirement_id
3. Generates US01, US02, US03... for each requirement's scenarios
4. Finds all test cases without test_case_ref_id
5. Generates US##-TC01, US##-TC02... for each scenario's test cases
6. Updates all records in the database

Run with: python -m backend.scripts.backfill_ref_ids
"""

import logging
from sqlalchemy import select, func
from backend.database.db import SessionLocal
from backend.database.db_models import ScenarioDB, TestCaseDB, RequirementDB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def backfill_scenario_ref_ids():
    """Backfill scenario_ref_id for all scenarios without one."""
    session = SessionLocal()
    try:
        # Get all requirements with scenarios
        stmt = select(RequirementDB).where(RequirementDB.is_deleted == False)
        requirements = session.scalars(stmt).all()
        
        total_scenarios_updated = 0
        
        for req in requirements:
            # Get all non-deleted scenarios for this requirement without a ref_id
            scenarios_stmt = select(ScenarioDB).where(
                ScenarioDB.requirement_id == req.id,
                ScenarioDB.is_deleted == False,
                ScenarioDB.scenario_ref_id.is_(None)
            ).order_by(ScenarioDB.created_at)  # Order by creation time for consistency
            
            scenarios = session.scalars(scenarios_stmt).all()
            
            if not scenarios:
                continue
            
            # Get the current max counter for this requirement
            max_stmt = select(func.max(ScenarioDB.scenario_ref_id)).where(
                ScenarioDB.requirement_id == req.id,
                ScenarioDB.is_deleted == False,
                ScenarioDB.scenario_ref_id.isnot(None)
            )
            max_ref = session.scalar(max_stmt)
            
            # Extract counter from max_ref (e.g., "US03" -> 3)
            start_counter = 1
            if max_ref:
                try:
                    start_counter = int(max_ref.replace('US', '')) + 1
                except ValueError:
                    start_counter = 1
            
            # Assign ref_ids
            for idx, scenario in enumerate(scenarios, start=start_counter):
                ref_id = f"US{idx:02d}"
                scenario.scenario_ref_id = ref_id
                logger.info(f"Assigned {ref_id} to scenario '{scenario.scenario_name}' (ID: {scenario.id})")
                total_scenarios_updated += 1
        
        session.commit()
        logger.info(f"✅ Successfully backfilled {total_scenarios_updated} scenario ref IDs")
        return total_scenarios_updated
        
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Error backfilling scenario ref IDs: {e}")
        raise
    finally:
        session.close()


def backfill_test_case_ref_ids():
    """Backfill test_case_ref_id for all test cases without one."""
    session = SessionLocal()
    try:
        # Get all scenarios
        stmt = select(ScenarioDB).where(ScenarioDB.is_deleted == False)
        scenarios = session.scalars(stmt).all()
        
        total_test_cases_updated = 0
        
        for scenario in scenarios:
            # Get scenario ref_id (should exist after scenario backfill)
            scenario_ref = scenario.scenario_ref_id or "US00"
            
            # Get all non-deleted test cases for this scenario without a ref_id
            test_cases_stmt = select(TestCaseDB).where(
                TestCaseDB.scenario_id == scenario.id,
                TestCaseDB.is_deleted == False,
                TestCaseDB.test_case_ref_id.is_(None)
            ).order_by(TestCaseDB.created_at)  # Order by creation time for consistency
            
            test_cases = session.scalars(test_cases_stmt).all()
            
            if not test_cases:
                continue
            
            # Get the current max counter for this scenario
            max_stmt = select(func.max(TestCaseDB.test_case_ref_id)).where(
                TestCaseDB.scenario_id == scenario.id,
                TestCaseDB.is_deleted == False,
                TestCaseDB.test_case_ref_id.isnot(None)
            )
            max_ref = session.scalar(max_stmt)
            
            # Extract counter from max_ref (e.g., "US01-TC03" -> 3)
            start_counter = 1
            if max_ref:
                try:
                    # Extract the TC number from "US##-TC##"
                    tc_part = max_ref.split('-TC')[-1]
                    start_counter = int(tc_part) + 1
                except (ValueError, IndexError):
                    start_counter = 1
            
            # Assign ref_ids
            for idx, test_case in enumerate(test_cases, start=start_counter):
                ref_id = f"{scenario_ref}-TC{idx:02d}"
                test_case.test_case_ref_id = ref_id
                logger.info(f"Assigned {ref_id} to test case '{test_case.title}' (ID: {test_case.id})")
                total_test_cases_updated += 1
        
        session.commit()
        logger.info(f"✅ Successfully backfilled {total_test_cases_updated} test case ref IDs")
        return total_test_cases_updated
        
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Error backfilling test case ref IDs: {e}")
        raise
    finally:
        session.close()


def main():
    """Run the backfill migration."""
    logger.info("=" * 80)
    logger.info("Starting ref_id backfill migration...")
    logger.info("=" * 80)
    
    try:
        # Step 1: Backfill scenario ref IDs
        logger.info("\n[Step 1/2] Backfilling scenario_ref_id...")
        scenarios_count = backfill_scenario_ref_ids()
        
        # Step 2: Backfill test case ref IDs
        logger.info("\n[Step 2/2] Backfilling test_case_ref_id...")
        test_cases_count = backfill_test_case_ref_ids()
        
        logger.info("\n" + "=" * 80)
        logger.info("✅ Migration completed successfully!")
        logger.info(f"   - Scenarios updated: {scenarios_count}")
        logger.info(f"   - Test cases updated: {test_cases_count}")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"\n❌ Migration failed: {e}")
        raise


if __name__ == "__main__":
    main()
