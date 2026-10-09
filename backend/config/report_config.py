"""
Report Configuration

Feature flags and configuration for report generation engines.
"""

import os
from enum import Enum
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class ReportEngine(str, Enum):
    """Report engine types."""
    LEGACY = "legacy"
    PROFESSIONAL = "professional"


class ReportConfig:
    """Configuration for report generation."""
    
    # Feature flag for report engine selection
    # Default: legacy (maintains backward compatibility)
    _report_engine_env = os.getenv("REPORT_ENGINE", "legacy")
    REPORT_ENGINE: ReportEngine = ReportEngine(_report_engine_env.lower())
    
    # Enable professional report engine
    ENABLE_PROFESSIONAL_REPORTS: bool = REPORT_ENGINE == ReportEngine.PROFESSIONAL
    
    # Fallback to legacy if professional fails
    FALLBACK_TO_LEGACY_ON_ERROR: bool = os.getenv(
        "FALLBACK_TO_LEGACY_ON_ERROR", 
        "true"
    ).lower() == "true"
    
    # Template directory for professional reports
    PROFESSIONAL_TEMPLATE_DIR: str = os.getenv(
        "PROFESSIONAL_TEMPLATE_DIR",
        "backend/templates"
    )
    
    # Output directory for professional reports
    PROFESSIONAL_REPORT_DIR: str = os.getenv(
        "PROFESSIONAL_REPORT_DIR",
        "data/professional_reports"
    )
    
    @classmethod
    def is_professional_enabled(cls) -> bool:
        """Check if professional report engine is enabled."""
        print(f"[ReportConfig] is_professional_enabled() called, returning: {cls.ENABLE_PROFESSIONAL_REPORTS}")
        return cls.ENABLE_PROFESSIONAL_REPORTS
    
    @classmethod
    def should_fallback_on_error(cls) -> bool:
        """Check if fallback to legacy is enabled on error."""
        return cls.FALLBACK_TO_LEGACY_ON_ERROR
    
    @classmethod
    def set_professional_mode(cls):
        """Enable professional report engine."""
        cls.REPORT_ENGINE = ReportEngine.PROFESSIONAL
        cls.ENABLE_PROFESSIONAL_REPORTS = True
        print(f"[ReportConfig] set_professional_mode() called, REPORT_ENGINE now: {cls.REPORT_ENGINE}")
    
    @classmethod
    def set_legacy_mode(cls):
        """Enable legacy report engine."""
        cls.REPORT_ENGINE = ReportEngine.LEGACY
        cls.ENABLE_PROFESSIONAL_REPORTS = False
        print(f"[ReportConfig] set_legacy_mode() called, REPORT_ENGINE now: {cls.REPORT_ENGINE}")
    
    @classmethod
    def print_config(cls):
        """Print current configuration for debugging."""
        print(f"[ReportConfig] Current Configuration:")
        print(f"[ReportConfig]   REPORT_ENGINE env var: '{cls._report_engine_env}'")
        print(f"[ReportConfig]   REPORT_ENGINE enum: {cls.REPORT_ENGINE}")
        print(f"[ReportConfig]   ENABLE_PROFESSIONAL_REPORTS: {cls.ENABLE_PROFESSIONAL_REPORTS}")
        print(f"[ReportConfig]   FALLBACK_TO_LEGACY_ON_ERROR: {cls.FALLBACK_TO_LEGACY_ON_ERROR}")
        print(f"[ReportConfig]   PROFESSIONAL_TEMPLATE_DIR: {cls.PROFESSIONAL_TEMPLATE_DIR}")
        print(f"[ReportConfig]   PROFESSIONAL_REPORT_DIR: {cls.PROFESSIONAL_REPORT_DIR}")
