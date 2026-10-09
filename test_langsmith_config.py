#!/usr/bin/env python3
"""Quick test script to verify LangSmithConfig implementation."""

from backend.config.settings import get_settings, LangSmithConfig

# Test 1: Verify LangSmithConfig can be instantiated with defaults
config = LangSmithConfig()
print("Test 1: LangSmithConfig default instantiation")
print(f"  tracing_enabled: {config.tracing_enabled} (expected: False)")
print(f"  endpoint: {config.endpoint} (expected: https://api.smith.langchain.com)")
print(f"  api_key: {config.api_key} (expected: None)")
print(f"  project_name: {config.project_name} (expected: Enterprise-AI-TestAutomation)")
print()

# Test 2: Verify LangSmithConfig is integrated into Settings
settings = get_settings()
print("Test 2: LangSmithConfig integrated into Settings")
print(f"  settings.langsmith.tracing_enabled: {settings.langsmith.tracing_enabled}")
print(f"  settings.langsmith.endpoint: {settings.langsmith.endpoint}")
print(f"  settings.langsmith.api_key: {'set' if settings.langsmith.api_key else 'not set'}")
print(f"  settings.langsmith.project_name: {settings.langsmith.project_name}")
print()

# Test 3: Verify field names match design spec
print("Test 3: Field name verification")
assert hasattr(config, 'tracing_enabled'), "Missing field: tracing_enabled"
assert hasattr(config, 'endpoint'), "Missing field: endpoint"
assert hasattr(config, 'api_key'), "Missing field: api_key"
assert hasattr(config, 'project_name'), "Missing field: project_name"
print("  All required fields present ✓")
print()

print("All tests passed! ✓")
