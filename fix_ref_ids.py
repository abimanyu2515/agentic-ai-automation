"""
Script to fix missing scenario_ref_id and test_case_ref_id in API responses
"""

# Read the main.py file
with open('backend/main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix ScenarioResponse constructions - add scenario_ref_id after requirement_id
content = content.replace(
    '''ScenarioResponse(
                id=s.id,
                requirement_id=s.requirement_id,
                scenario_name=s.scenario_name,''',
    '''ScenarioResponse(
                id=s.id,
                requirement_id=s.requirement_id,
                scenario_ref_id=s.scenario_ref_id,
                scenario_name=s.scenario_name,'''
)

# Fix TestCaseResponse constructions - add test_case_ref_id after scenario_id
content = content.replace(
    '''TestCaseResponse(
                id=tc.id,
                scenario_id=tc.scenario_id,
                title=tc.title,''',
    '''TestCaseResponse(
                id=tc.id,
                scenario_id=tc.scenario_id,
                test_case_ref_id=tc.test_case_ref_id,
                title=tc.title,'''
)

# Fix single-object returns (updated, duplicated)
content = content.replace(
    '''ScenarioResponse(
        id=updated.id,
        requirement_id=updated.requirement_id,
        scenario_name=updated.scenario_name,''',
    '''ScenarioResponse(
        id=updated.id,
        requirement_id=updated.requirement_id,
        scenario_ref_id=updated.scenario_ref_id,
        scenario_name=updated.scenario_name,'''
)

content = content.replace(
    '''ScenarioResponse(
        id=duplicated.id,
        requirement_id=duplicated.requirement_id,
        scenario_name=duplicated.scenario_name,''',
    '''ScenarioResponse(
        id=duplicated.id,
        requirement_id=duplicated.requirement_id,
        scenario_ref_id=duplicated.scenario_ref_id,
        scenario_name=duplicated.scenario_name,'''
)

content = content.replace(
    '''TestCaseResponse(
        id=updated.id,
        scenario_id=updated.scenario_id,
        title=updated.title,''',
    '''TestCaseResponse(
        id=updated.id,
        scenario_id=updated.scenario_id,
        test_case_ref_id=updated.test_case_ref_id,
        title=updated.title,'''
)

content = content.replace(
    '''TestCaseResponse(
        id=updated_tc.id,
        scenario_id=updated_tc.scenario_id,
        title=updated_tc.title,''',
    '''TestCaseResponse(
        id=updated_tc.id,
        scenario_id=updated_tc.scenario_id,
        test_case_ref_id=updated_tc.test_case_ref_id,
        title=updated_tc.title,'''
)

content = content.replace(
    '''TestCaseResponse(
        id=tc.id,
        scenario_id=tc.scenario_id,
        title=tc.title,''',
    '''TestCaseResponse(
        id=tc.id,
        scenario_id=tc.scenario_id,
        test_case_ref_id=tc.test_case_ref_id,
        title=tc.title,'''
)

# Write the fixed content back
with open('backend/main.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✅ Fixed all ScenarioResponse and TestCaseResponse constructions in main.py")
print("✅ Added scenario_ref_id to all ScenarioResponse instances")
print("✅ Added test_case_ref_id to all TestCaseResponse instances")
