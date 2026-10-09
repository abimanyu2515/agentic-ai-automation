"""
Professional Report Models

Structured data models for the new Professional Report Engine.
These models represent the output of the ExecutionAnalyzer and input to the ProfessionalReportGenerator.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ValidationStatus(str, Enum):
    """Validation result status."""
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"
    INFO = "INFO"


class BusinessRuleValidation(BaseModel):
    """Business rule validation with confidence scoring."""
    rule: str = Field(description="Business rule description")
    status: str = Field(description="✅ or ❌")
    confidence: int = Field(description="0-100 confidence score", ge=0, le=100)
    reasoning: str = Field(description="Why this status was assigned")


class Validation(BaseModel):
    """Structured validation object representing a single test validation."""
    validation_title: str = Field(description="Business-focused validation title")
    step_name: str = Field(description="Original test case step name")
    business_goal: str = Field(description="Business objective achieved")
    observations: str = Field(description="2-4 sentences describing actual state from DOM/assertions/timeline")
    expected_result: str = Field(description="Expected result for this validation step")
    actual_result: str = Field(description="Actual result observed during execution")
    confidence: int = Field(description="0-100% confidence in observation", ge=0, le=100)
    reasoning: str = Field(description="Evidence supporting confidence score")
    result: ValidationStatus = Field(description="PASS, FAIL, WARNING, or INFO")
    action_taken: str = Field(description="Business action description")
    evidence: str = Field(description="Screenshot reference (e.g., 'Screenshot 7')")
    evidence_url: Optional[str] = Field(default=None, description="Browser-accessible screenshot URL")
    business_rules: list[BusinessRuleValidation] = Field(default_factory=list)


class ValidationList(BaseModel):
    """List of validations for a test execution."""
    validations: list[Validation]


class ExecutiveSummary(BaseModel):
    """Executive summary for stakeholders."""
    narrative: str = Field(description="2-3 sentence business workflow summary")
    achievements: list[str] = Field(description="3-4 key achievements (if passed)")
    critical_issues: list[str] = Field(description="3-4 critical issues (if failed)")
    business_rule_compliance: str = Field(description="Compliance assessment")
    recommendations: list[str] = Field(description="1-2 actionable recommendations (if issues)")


class EvidenceItem(BaseModel):
    """Evidence item for the evidence index."""
    number: int = Field(description="Screenshot number")
    description: str = Field(description="Screenshot description")
    related_validations: list[str] = Field(description="Related validation titles")
    thumbnail: Optional[str] = Field(default=None, description="Thumbnail path")
    full_path: Optional[str] = Field(default=None, description="Full screenshot path")


class ProfessionalReportContext(BaseModel):
    """Complete context for professional report generation."""
    execution_id: str
    test_case_id: str
    test_case_title: str
    test_case_description: str
    expected_result: str
    website_url: str
    execution_status: ValidationStatus
    execution_duration_seconds: float
    browser_version: Optional[str]
    started_at: datetime
    finished_at: datetime
    
    # Report content
    executive_summary: ExecutiveSummary
    validations: list[Validation]
    business_rules: list[BusinessRuleValidation]
    evidence: list[EvidenceItem]
    
    # Metrics
    total_validations: int
    passed_count: int
    failed_count: int
    warning_count: int
    info_count: int
    
    # Metadata
    generated_at: datetime = Field(default_factory=datetime.utcnow)
