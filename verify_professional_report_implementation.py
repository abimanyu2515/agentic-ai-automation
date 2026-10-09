"""
Professional Report Engine Verification Script

This script verifies the implementation of the Professional Report Engine
without relying on pytest discovery issues.
"""

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

def test_imports():
    """Test that all new components can be imported."""
    print("Testing imports...")
    
    try:
        from backend.models.professional_report import (
            Validation, ValidationList, BusinessRuleValidation,
            ExecutiveSummary, EvidenceItem, ProfessionalReportContext, ValidationStatus
        )
        print("✓ Professional report models imported successfully")
    except Exception as e:
        print(f"✗ Failed to import professional report models: {e}")
        return False
    
    try:
        from backend.services.execution_analyzer import ExecutionAnalyzer
        print("✓ ExecutionAnalyzer imported successfully")
    except Exception as e:
        print(f"✗ Failed to import ExecutionAnalyzer: {e}")
        return False
    
    try:
        from backend.services.professional_report_generator import ProfessionalReportGenerator
        print("✓ ProfessionalReportGenerator imported successfully")
    except Exception as e:
        print(f"✗ Failed to import ProfessionalReportGenerator: {e}")
        return False
    
    try:
        from backend.config.report_config import ReportConfig, ReportEngine
        print("✓ ReportConfig imported successfully")
    except Exception as e:
        print(f"✗ Failed to import ReportConfig: {e}")
        return False
    
    return True


def test_feature_flags():
    """Test feature flag configuration."""
    print("\nTesting feature flags...")
    
    try:
        from backend.config.report_config import ReportConfig, ReportEngine
        
        # Test default configuration
        assert ReportConfig.REPORT_ENGINE == ReportEngine.LEGACY
        print("✓ Default configuration is LEGACY mode")
        
        assert not ReportConfig.is_professional_enabled()
        print("✓ Professional mode is disabled by default")
        
        assert ReportConfig.should_fallback_on_error()
        print("✓ Fallback to legacy is enabled by default")
        
        # Test switching to professional mode
        ReportConfig.set_professional_mode()
        assert ReportConfig.REPORT_ENGINE == ReportEngine.PROFESSIONAL
        assert ReportConfig.is_professional_enabled()
        print("✓ Successfully switched to PROFESSIONAL mode")
        
        # Reset to legacy
        ReportConfig.set_legacy_mode()
        assert ReportConfig.REPORT_ENGINE == ReportEngine.LEGACY
        assert not ReportConfig.is_professional_enabled()
        print("✓ Successfully reset to LEGACY mode")
        
        return True
    except Exception as e:
        print(f"✗ Feature flag test failed: {e}")
        return False


def test_report_service_integration():
    """Test ReportService integration."""
    print("\nTesting ReportService integration...")
    
    try:
        from backend.services.report_service import ReportService
        
        # Verify both methods exist
        assert hasattr(ReportService, 'compile_reports')
        print("✓ Legacy compile_reports method exists")
        
        assert hasattr(ReportService, 'compile_reports_with_engine_selection')
        print("✓ New compile_reports_with_engine_selection method exists")
        
        return True
    except Exception as e:
        print(f"✗ ReportService integration test failed: {e}")
        return False


def test_templates_exist():
    """Test that Jinja2 templates exist."""
    print("\nTesting template files...")
    
    template_files = [
        "backend/templates/base.html",
        "backend/templates/professional_report.html",
        "backend/templates/components/header.html",
        "backend/templates/components/executive_summary.html",
        "backend/templates/components/validation_summary.html",
        "backend/templates/components/business_rule_validation.html",
        "backend/templates/components/validation_card.html",
        "backend/templates/components/evidence_index.html",
        "backend/templates/components/footer.html"
    ]
    
    all_exist = True
    for template_file in template_files:
        path = Path(template_file)
        if path.exists():
            print(f"✓ {template_file} exists")
        else:
            print(f"✗ {template_file} missing")
            all_exist = False
    
    return all_exist


def test_execution_analyzer_basic():
    """Test basic ExecutionAnalyzer functionality."""
    print("\nTesting ExecutionAnalyzer basic functionality...")
    
    try:
        from backend.services.execution_analyzer import ExecutionAnalyzer
        from backend.models.execution_result import ExecutionResult, ExecutionStatus
        from backend.models.professional_report import ValidationStatus
        from uuid import uuid4
        from datetime import datetime, timezone
        
        # Create a simple execution result
        execution_result = ExecutionResult(
            id=uuid4(),
            test_case_id=uuid4(),
            status=ExecutionStatus.PASSED,
            duration_seconds=30.5,
            executed_at=datetime.now(timezone.utc),
            error_message=None,
            screenshot_path=None,
            video_path=None,
            trace_path=None,
            timeline=[
                {"event": "Navigate to page", "time": 2.5, "type": "success", "details": ""},
                {"event": "Click button", "time": 5.3, "type": "success", "details": "screenshot-1.png"}
            ],
            screenshots=["screenshot-1.png"]
        )
        
        # Test rule-based generation (no LLM)
        analyzer = ExecutionAnalyzer()
        analyzer.llm_service = None
        
        context = analyzer.analyze_execution(
            execution_result=execution_result,
            test_case_title="Test Case",
            test_case_description="Test description",
            expected_result="Expected result",
            test_case_steps=["Step 1", "Step 2"],
            website_url="https://example.com"
        )
        
        # Verify context
        assert context.test_case_title == "Test Case"
        assert context.total_validations == 2
        assert len(context.validations) == 2
        assert context.executive_summary.narrative
        print("✓ ExecutionAnalyzer generates valid context with rule-based fallback")
        
        return True
    except Exception as e:
        print(f"✗ ExecutionAnalyzer test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_professional_report_generator_basic():
    """Test basic ProfessionalReportGenerator functionality."""
    print("\nTesting ProfessionalReportGenerator basic functionality...")
    
    try:
        from backend.services.professional_report_generator import ProfessionalReportGenerator
        from backend.models.professional_report import (
            ProfessionalReportContext, ValidationStatus, ExecutiveSummary, Validation
        )
        from uuid import uuid4
        from datetime import datetime, timezone
        import tempfile
        
        # Create a simple context
        context = ProfessionalReportContext(
            execution_id=str(uuid4()),
            test_case_id=str(uuid4()),
            test_case_title="Test Case",
            test_case_description="Test description",
            expected_result="Expected result",
            website_url="https://example.com",
            execution_status=ValidationStatus.PASS,
            execution_duration_seconds=30.5,
            browser_version="Chromium 120.0",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            executive_summary=ExecutiveSummary(
                narrative="Test completed successfully",
                achievements=["All validations passed"],
                critical_issues=[],
                business_rule_compliance="All rules compliant",
                recommendations=[]
            ),
            validations=[
                Validation(
                    validation_title="Page Loaded",
                    business_goal="Load the page",
                    observations="Page loaded successfully",
                    confidence=95,
                    reasoning="DOM state confirms page load",
                    result=ValidationStatus.PASS,
                    action_taken="Navigated to page",
                    evidence="Screenshot 1",
                    business_rules=[]
                )
            ],
            business_rules=[],
            evidence=[],
            total_validations=1,
            passed_count=1,
            failed_count=0,
            warning_count=0,
            info_count=0
        )
        
        # Test HTML generation
        generator = ProfessionalReportGenerator()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_report.html"
            result = generator.generate_html(context, output_path)
            
            assert result.exists()
            assert result.stat().st_size > 0
            content = result.read_text(encoding='utf-8')
            assert "Test Case" in content
            assert "Executive Summary" in content
            print("✓ ProfessionalReportGenerator generates valid HTML")
        
        return True
    except Exception as e:
        print(f"✗ ProfessionalReportGenerator test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all verification tests."""
    print("=" * 70)
    print("Professional Report Engine Verification")
    print("=" * 70)
    
    tests = [
        test_imports,
        test_feature_flags,
        test_report_service_integration,
        test_templates_exist,
        test_execution_analyzer_basic,
        test_professional_report_generator_basic
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"✗ Test crashed: {e}")
            results.append(False)
    
    print("\n" + "=" * 70)
    print("Verification Summary")
    print("=" * 70)
    
    passed = sum(results)
    total = len(results)
    
    print(f"Tests passed: {passed}/{total}")
    
    if passed == total:
        print("\n✓ All verification tests passed!")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
