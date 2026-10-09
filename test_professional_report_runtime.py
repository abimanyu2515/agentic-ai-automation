"""
Test Professional Report Engine Runtime

This script tests the professional report engine with the REPORT_ENGINE
environment variable set to "professional" to verify the actual runtime
execution path.
"""

import os
import sys
from pathlib import Path

# Set the environment variable before importing backend modules
os.environ["REPORT_ENGINE"] = "professional"

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

print("=" * 70)
print("Testing Professional Report Engine Runtime")
print("=" * 70)
print(f"Environment variable REPORT_ENGINE set to: {os.environ.get('REPORT_ENGINE')}")
print()

# Import after setting environment variable
from backend.config.report_config import ReportConfig

print("Current ReportConfig state:")
ReportConfig.print_config()
print()

# Test the configuration
print("Testing configuration:")
print(f"  is_professional_enabled(): {ReportConfig.is_professional_enabled()}")
print(f"  should_fallback_on_error(): {ReportConfig.should_fallback_on_error()}")
print()

# Test switching modes
print("Testing mode switching:")
ReportConfig.set_legacy_mode()
print(f"  After set_legacy_mode(): {ReportConfig.is_professional_enabled()}")
ReportConfig.set_professional_mode()
print(f"  After set_professional_mode(): {ReportConfig.is_professional_enabled()}")
print()

print("=" * 70)
print("Configuration test complete")
print("=" * 70)
