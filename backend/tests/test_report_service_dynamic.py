from uuid import uuid4
import pytest

from backend.models.test_case import TestCase
from backend.services.report_service import map_timeline_event_to_step


def test_map_timeline_event_to_step_dynamic():
    # Construct a test case with 3 steps and an expected result
    test_case = TestCase(
        scenario_id=uuid4(),
        title="Dynamic Test",
        steps=["Initialize the portal", "Fill out forms", "Verify dashboard summary"],
        expected_result="Dashboard loaded with correct metrics",
        confidence=1.0
    )

    project_id = uuid4()
    execution_id = uuid4()

    # 1. Test step 1 mapping (01-initial-state.png)
    event_1 = {
        "event": "Screenshot event",
        "time": 2.5,
        "type": "screenshot",
        "details": "01-initial-state.png"
    }
    step_1 = map_timeline_event_to_step(event_1, index=0, project_id=project_id, execution_id=execution_id, test_case=test_case)
    assert step_1["title"] == "Step 1: Initialize the portal"
    assert step_1["action"] == "Execute step 1: Initialize the portal"
    assert step_1["result"] == "Step 1 verification passed."

    # 2. Test step 3 mapping (last step, 03-insurant-data.png)
    event_3 = {
        "event": "Screenshot event",
        "time": 20.18,
        "type": "screenshot",
        "details": "03-final-screenshot.png"
    }
    step_3 = map_timeline_event_to_step(event_3, index=2, project_id=project_id, execution_id=execution_id, test_case=test_case)
    assert step_3["title"] == "Step 3: Verify dashboard summary"
    assert step_3["action"] == "Execute step 3: Verify dashboard summary"
    # Last step maps expected result dynamically
    assert step_3["result"] == "Verified expected result: Dashboard loaded with correct metrics"
    assert step_3["time"] == "20.18s"

    # 3. Test fallback when step index is out of bounds
    event_out = {
        "event": "Screenshot event",
        "time": 30.0,
        "type": "screenshot",
        "details": "10-nonexistent.png"
    }
    step_out = map_timeline_event_to_step(event_out, index=3, project_id=project_id, execution_id=execution_id, test_case=test_case)
    # Falls back to standard "Visual State Capture" template
    assert step_out["title"] == "Visual State Capture"
    assert "Capture screenshot" in step_out["action"]
