"""
Foundation smoke test.

Not testing edge cases here - that's what proper unit tests are for later.
This just proves config + models actually wire together before you build
a single agent on top of them. Run with: pytest backend/tests/test_foundation.py -v -s
"""

import pytest

from backend.config.settings import get_settings
from backend.models.common import Priority
from backend.models.requirement import Requirement, RequirementSource
from backend.models.scenario import Scenario
from backend.models.state import WorkflowState


def test_config_loads():
    settings = get_settings()
    assert settings.llm.provider
    assert settings.agents.scenario is True
    print(f"[config] provider={settings.llm.provider} model={settings.llm.model} "
          f"mode={settings.workflow.mode} threshold={settings.workflow.evaluation_threshold}")


@pytest.fixture
def requirement() -> Requirement:
    return Requirement(
        title="Login flow",
        description="User can log in with valid credentials",
        source=RequirementSource.MANUAL,
    )


@pytest.fixture
def scenarios(requirement: Requirement) -> list[Scenario]:
    return [
        Scenario(
            requirement_id=requirement.id,
            scenario_name="Valid Login",
            description="User logs in with correct credentials",
            priority=Priority.HIGH,
            confidence=0.92,
        ),
        Scenario(
            requirement_id=requirement.id,
            scenario_name="Invalid Password",
            description="User logs in with wrong password",
            confidence=0.81,
        ),
    ]


def test_requirement_is_immutable(requirement: Requirement):
    with pytest.raises(Exception):
        requirement.title = "changed"
    print(f"[requirement] created and immutability confirmed: {requirement.id}")


def test_scenario_links_to_requirement(requirement: Requirement, scenarios: list[Scenario]):
    assert all(s.requirement_id == requirement.id for s in scenarios)
    print(f"[scenario] created {len(scenarios)} scenarios linked to requirement {requirement.id}")


def test_state_flows(requirement: Requirement, scenarios: list[Scenario]):
    state = WorkflowState(requirement=requirement)
    state.add_log("Requirement received")

    state.generated_scenarios.extend(scenarios)
    state.add_log(f"Generated {len(scenarios)} scenarios")

    state.select_scenario(scenarios[0].id)
    state.add_log(f"Selected scenario: {scenarios[0].scenario_name}")

    assert len(state.selected_scenarios()) == 1
    assert state.selected_scenarios()[0].scenario_name == "Valid Login"

    # rejecting an unknown id should fail loudly, not silently
    with pytest.raises(ValueError):
        state.select_scenario(requirement.id)  # wrong kind of id, on purpose

    print("[state] scenario selection and logging confirmed")
    for line in state.logs:
        print("   ", line)