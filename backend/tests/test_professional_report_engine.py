"""
Professional Report Engine Tests

Tests for the new Professional Report Engine including:
- ExecutionAnalyzer functionality
- ProfessionalReportGenerator functionality
- Feature flag behavior
- Backward compatibility
- LLM failure fallback
"""

from pathlib import Path
from uuid import uuid4, UUID
from datetime import datetime, timezone
import pytest

from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.models.professional_report import (
    Validation,
    ValidationList,
    BusinessRuleValidation,
    ExecutiveSummary,
    EvidenceItem,
    ProfessionalReportContext,
    ValidationStatus
)
from backend.services.execution_analyzer import ExecutionAnalyzer
from backend.services.professional_report_generator import ProfessionalReportGenerator
from backend.config.report_config import ReportConfig


@pytest.fixture
def sample_execution_result():
    """Create a sample ExecutionResult for testing."""
    return ExecutionResult(
        id=uuid4(),
        test_case_id=uuid4(),
        status=ExecutionStatus.COMPLETED,
        duration_seconds=45.5,
        executed_at=datetime.now(timezone.utc),
        error_message=None,
        screenshot_path=None,
        video_path=None,
        trace_path=None,
        timeline=[
            {
                "event": "Navigate to booking page",
                "time": 2.5,
                "type": "success",
                "details": ""
            },
            {
                "event": "Enter hotel search criteria",
                "time": 5.3,
                "type": "success",
                "details": ""
            },
            {
                "event": "Click search button",
                "time": 8.1,
                "type": "success",
                "details": "screenshot-1.png"
            },
            {
                "event": "Select hotel from results",
                "time": 15.2,
                "type": "success",
                "details": "screenshot-2.png"
            },
            {
                "event": "Enter customer details",
                "time": 22.4,
                "type": "success",
                "details": "screenshot-3.png"
            },
            {
                "event": "Complete booking",
                "time": 35.7,
                "type": "success",
                "details": "screenshot-4.png"
            }
        ],
        screenshots=["screenshot-1.png", "screenshot-2.png", "screenshot-3.png", "screenshot-4.png"]
    )


@pytest.fixture
def sample_test_case_steps():
    """Sample test case steps."""
    return [
        "Navigate to booking page",
        "Enter hotel search criteria",
        "Click search button",
        "Select hotel from results",
        "Enter customer details",
        "Complete booking"
    ]


class TestExecutionAnalyzer:
    """Tests for ExecutionAnalyzer component."""
    
    def test_analyzer_initialization(self):
        """Test that ExecutionAnalyzer initializes correctly."""
        analyzer = ExecutionAnalyzer()
        assert analyzer is not None
        assert analyzer.llm_service is not None
    
    def test_rule_based_validation_generation(
        self,
        sample_execution_result,
        sample_test_case_steps
    ):
        """Test rule-based validation generation (LLM fallback)."""
        analyzer = ExecutionAnalyzer()
        
        # Use rule-based generation by passing None for LLM service
        analyzer.llm_service = None
        
        context = analyzer.analyze_execution(
            execution_result=sample_execution_result,
            test_case_title="Hotel Booking Workflow",
            test_case_description="Test the complete hotel booking process",
            expected_result="Booking completed successfully with order number",
            test_case_steps=sample_test_case_steps,
            website_url="https://example-hotel.com"
        )
        
        # Verify context structure
        assert isinstance(context, ProfessionalReportContext)
        assert context.test_case_title == "Hotel Booking Workflow"
        assert context.total_validations == len(sample_test_case_steps)
        assert len(context.validations) == len(sample_test_case_steps)
        
        # Verify each validation has required fields
        for validation in context.validations:
            assert validation.validation_title
            assert validation.business_goal
            assert validation.observations
            assert validation.confidence >= 0
            assert validation.confidence <= 100
            assert validation.result in [ValidationStatus.PASS, ValidationStatus.FAIL, ValidationStatus.WARNING, ValidationStatus.INFO]
            assert validation.action_taken
            assert validation.evidence
    
    def test_evidence_index_generation(
        self,
        sample_execution_result,
        sample_test_case_steps
    ):
        """Test evidence index generation."""
        analyzer = ExecutionAnalyzer()
        analyzer.llm_service = None
        
        context = analyzer.analyze_execution(
            execution_result=sample_execution_result,
            test_case_title="Hotel Booking Workflow",
            test_case_description="Test the complete hotel booking process",
            expected_result="Booking completed successfully",
            test_case_steps=sample_test_case_steps,
            website_url="https://example-hotel.com"
        )
        
        # Verify evidence index
        assert len(context.evidence) == len(sample_execution_result.screenshots)
        
        for evidence in context.evidence:
            assert evidence.number > 0
            assert evidence.description
            assert isinstance(evidence.related_validations, list)
    
    def test_executive_summary_generation(
        self,
        sample_execution_result,
        sample_test_case_steps
    ):
        """Test executive summary generation."""
        analyzer = ExecutionAnalyzer()
        analyzer.llm_service = None
        
        context = analyzer.analyze_execution(
            execution_result=sample_execution_result,
            test_case_title="Hotel Booking Workflow",
            test_case_description="Test the complete hotel booking process",
            expected_result="Booking completed successfully",
            test_case_steps=sample_test_case_steps,
            website_url="https://example-hotel.com"
        )
        
        # Verify executive summary
        assert context.executive_summary.narrative
        assert isinstance(context.executive_summary.achievements, list)
        assert isinstance(context.executive_summary.critical_issues, list)
        assert context.executive_summary.business_rule_compliance
    
    def test_failed_execution_handling(
        self,
        sample_test_case_steps
    ):
        """Test handling of failed executions."""
        failed_result = ExecutionResult(
            id=uuid4(),
            test_case_id=uuid4(),
            status=ExecutionStatus.FAILED,
            duration_seconds=15.2,
            executed_at=datetime.now(timezone.utc),
            error_message="Element not found: #submit-button",
            screenshot_path=None,
            video_path=None,
            trace_path=None,
            timeline=[
                {
                    "event": "Navigate to booking page",
                    "time": 2.5,
                    "type": "success",
                    "details": ""
                },
                {
                    "event": "Element not found: #submit-button",
                    "time": 15.2,
                    "type": "error",
                    "details": "Element not found"
                }
            ],
            screenshots=["error-screenshot.png"]
        )
        
        analyzer = ExecutionAnalyzer()
        analyzer.llm_service = None
        
        context = analyzer.analyze_execution(
            execution_result=failed_result,
            test_case_title="Hotel Booking Workflow",
            test_case_description="Test the complete hotel booking process",
            expected_result="Booking completed successfully",
            test_case_steps=sample_test_case_steps,
            website_url="https://example-hotel.com"
        )
        
        # Verify failed execution is handled correctly
        assert context.execution_status == ValidationStatus.FAIL
        assert len(context.executive_summary.critical_issues) > 0


class TestProfessionalReportGenerator:
    """Tests for ProfessionalReportGenerator component."""
    
    def test_generator_initialization(self):
        """Test that ProfessionalReportGenerator initializes correctly."""
        generator = ProfessionalReportGenerator()
        assert generator is not None
        assert generator.template_dir == Path("backend/templates")
    
    def test_html_generation(self, tmp_path):
        """Test HTML report generation."""
        generator = ProfessionalReportGenerator()
        
        # Create sample context
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
        
        output_path = tmp_path / "test_report.html"
        result = generator.generate_html(context, output_path)
        
        # Verify HTML file was created
        assert result.exists()
        assert result.stat().st_size > 0
        
        # Verify HTML content
        content = result.read_text(encoding='utf-8')
        assert "Test Case" in content
        assert "Executive Summary" in content
        assert "Page Loaded" in content
    
    def test_pdf_generation_with_pymupdf_fallback(self, tmp_path):
        """Test PDF generation with PyMuPDF fallback."""
        generator = ProfessionalReportGenerator()
        
        # Create minimal context
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
            validations=[],
            business_rules=[],
            evidence=[],
            total_validations=0,
            passed_count=0,
            failed_count=0,
            warning_count=0,
            info_count=0
        )
        
        output_path = tmp_path / "test_report.pdf"
        result = generator.generate_pdf(context, output_path)
        
        # Verify PDF file was created
        assert result.exists()
        assert result.stat().st_size > 0


class TestReportConfig:
    """Tests for ReportConfig feature flags."""
    
    def test_default_config(self):
        """Test default configuration (legacy mode)."""
        assert ReportConfig.REPORT_ENGINE == ReportEngine.LEGACY
        assert not ReportConfig.is_professional_enabled()
        assert ReportConfig.should_fallback_on_error()
    
    def test_professional_mode_switch(self):
        """Test switching to professional mode."""
        ReportConfig.set_professional_mode()
        assert ReportConfig.REPORT_ENGINE == ReportEngine.PROFESSIONAL
        assert ReportConfig.is_professional_enabled()
        
        # Reset to default
        ReportConfig.set_legacy_mode()
        assert ReportConfig.REPORT_ENGINE == ReportEngine.LEGACY
        assert not ReportConfig.is_professional_enabled()
    
    def test_fallback_configuration(self):
        """Test fallback configuration."""
        assert ReportConfig.should_fallback_on_error()


class TestBackwardCompatibility:
    """Tests for backward compatibility with existing report engine."""
    
    def test_legacy_report_service_unchanged(self):
        """Test that legacy compile_reports method still exists and works."""
        from backend.services.report_service import ReportService
        
        # Verify the method exists
        assert hasattr(ReportService, 'compile_reports')
        
        # Verify the new method exists
        assert hasattr(ReportService, 'compile_reports_with_engine_selection')
    
    def test_feature_flag_respects_default(self):
        """Test that default configuration uses legacy engine."""
        assert not ReportConfig.is_professional_enabled()
        
        # This ensures existing behavior is preserved by default


class TestLLMFallback:
    """Integration tests for LLM failure fallback."""
    
    def test_llm_failure_falls_back_to_rule_based(
        self,
        sample_execution_result,
        sample_test_case_steps
    ):
        """Test that LLM failure falls back to rule-based generation."""
        analyzer = ExecutionAnalyzer()
        
        # Simulate LLM failure by setting llm_service to None
        analyzer.llm_service = None
        
        # This should not raise an exception
        context = analyzer.analyze_execution(
            execution_result=sample_execution_result,
            test_case_title="Hotel Booking Workflow",
            test_case_description="Test the complete hotel booking process",
            expected_result="Booking completed successfully",
            test_case_steps=sample_test_case_steps,
            website_url="https://example-hotel.com"
        )
        
        # Verify fallback generated valid context
        assert context is not None
        assert len(context.validations) == len(sample_test_case_steps)
