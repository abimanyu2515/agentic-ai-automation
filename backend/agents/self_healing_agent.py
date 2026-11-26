"""
SelfHealingAgent (spec 0011)

Purpose:
    Heals object repository elements whose every stored locator failed in the
    run (stage 1), repairs the script from the log analyser's RCA when the test
    still fails for an automation reason (stage 2), and checks each change
    against a single rerun of the failed test case.

Workflow Position:
    ExecutionAgent
        ↓
    ExecutionAnalysisAgent (the RCA)
        ↓ (route_after_execution_analysis: a heal target, a repair target, or a change to verify)
    SelfHealingAgent
        ↓ (route_after_self_healing)
    ExecutionAgent (rerun of the changed test cases)  or  BugReportingAgent

Phases, checked in this order on every visit:
    1. Script verify (after a stage 2 rerun): keep a script fix whose rerun
       passed, restore the old script otherwise. Heals that waited for stage 2
       follow the same outcome.
    2. Locator verify (after a stage 1 rerun): keep a heal whose rerun passed;
       a heal whose key resolved while the test still failed for an automation
       reason waits for stage 2; put back the others as suggestions. When a heal
       waits, stage 2 runs in the same visit.
    3. Stage 1, locator heal: find targets in `run_marker_lines`, score
       harvested candidates, apply confident matches, save unsure ones as
       suggestions, then rerun.
    4. Stage 2, script repair (AC-18 to AC-21): regenerate the script from the
       RCA, apply it when the gates pass, then rerun.
    When every result passed there is nothing to do (AC-1).

Owned outputs:
    - state.heal_results, state.heal_attempts, state.heal_rerun_test_case_ids
    - state.script_heal_results, state.script_repair_attempts
    - state.markers_applied_result_ids
    - the failed result's screenshot_path, video_path, trace_path (evidence copies)
    - the state's copy of a repaired test case's playwright_script

All rules live in LocatorHealingService and ScriptRepairService. This agent never raises (AC-15).
"""

from uuid import UUID

from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.services.object_repository_healing import (
    LocatorHealingService,
    find_heal_targets,
    latest_results,
    pending_verification,
)
from backend.services.script_repair_service import (
    ScriptRepairService,
    is_repair_candidate,
    pending_script_verification,
)


class SelfHealingAgent(BaseAgent):
    name = "Self Healing Agent"

    def __init__(
        self,
        project_id=None,
        repo=None,
        healing_service: LocatorHealingService | None = None,
        script_repair_service: ScriptRepairService | None = None,
    ):
        self.project_id = project_id
        self.repo = repo
        self._healing_service = healing_service
        self._script_repair_service = script_repair_service

    @property
    def healing_service(self) -> LocatorHealingService:
        if self._healing_service is None:
            self._healing_service = LocatorHealingService(project_repo=self.repo)
        return self._healing_service

    @property
    def script_repair_service(self) -> ScriptRepairService:
        if self._script_repair_service is None:
            self._script_repair_service = ScriptRepairService(project_repo=self.repo)
        return self._script_repair_service

    def run(self, state: WorkflowState) -> WorkflowState:
        try:
            return self._run(state)
        except Exception as exc:
            state.heal_rerun_test_case_ids = []
            state.add_log(f"{self.name}: skipped after an error ({type(exc).__name__})")
            return state

    def _run(self, state: WorkflowState) -> WorkflowState:
        if state.project_id is None and self.project_id is not None:
            state.project_id = self.project_id
        if not state.healing_enabled:
            state.add_log(f"{self.name}: healing is off for this run")
            return state
        state.heal_rerun_test_case_ids = []

        if pending_script_verification(state):
            return self._verify_scripts(state)

        if pending_verification(state):
            if not self._verify_locators(state):
                return state
            # A heal waits for stage 2: repair now, in the same visit.
            return self._repair_scripts(state)

        settings = self.healing_service.settings
        if state.heal_attempts < settings.max_attempts_per_run and self._new_heal_targets(state):
            return self._heal_locators(state)

        return self._repair_scripts(state)

    # ----------------------------------------------------------- phases

    def _verify_scripts(self, state: WorkflowState) -> WorkflowState:
        state.add_log(f"{self.name} started script verify phase")
        passed, restored = self.script_repair_service.verify(state)
        self.healing_service.finish_pending(state, passed, passed=True)
        reverted = self.healing_service.finish_pending(state, restored, passed=False)
        for test_case_id in passed:
            state.add_log(f"{self.name}: script fix for test case {test_case_id} kept, the rerun passed")
        for test_case_id in restored:
            state.add_log(f"{self.name}: script fix for test case {test_case_id} restored as a suggestion, the rerun failed")
        for key in reverted:
            state.add_log(f"{self.name}: heal of {key} put back as a suggestion, the test never passed with it")
        state.add_log(f"{self.name} completed script verify phase")
        return state

    def _verify_locators(self, state: WorkflowState) -> bool:
        """Locator verify after a stage 1 rerun. True when a heal now waits for stage 2."""
        state.add_log(f"{self.name} started verify phase")
        reverted = self.healing_service.verify(state, defer=lambda heal: self._can_repair(state, heal))
        waiting = False
        for heal in state.heal_results:
            if heal.get("verified") == "passed":
                state.add_log(f"{self.name}: heal of {heal['key']} kept, the rerun passed")
            elif heal.get("verified") == "pending_script_repair":
                waiting = True
                state.add_log(
                    f"{self.name}: heal of {heal['key']} resolved, but the test still failed; repairing the script"
                )
        for key in reverted:
            state.add_log(f"{self.name}: heal of {key} put back as a suggestion, the rerun failed")
        state.add_log(f"{self.name} completed verify phase")
        return waiting

    def _heal_locators(self, state: WorkflowState) -> WorkflowState:
        state.add_log(f"{self.name} started heal phase (attempt {state.heal_attempts + 1})")
        results = self.healing_service.heal(state, attempt=state.heal_attempts + 1)
        state.heal_results.extend(results)
        applied = [heal for heal in results if heal["status"] == "applied"]
        for heal in results:
            state.add_log(
                f"{self.name}: {heal['status']} heal for {heal['key']} "
                f"({heal['method']}, confidence {heal['confidence']:.2f})"
            )
        if applied:
            rerun = list(dict.fromkeys(UUID(str(t)) for heal in applied for t in heal["test_case_ids"]))
            state.heal_rerun_test_case_ids = rerun
            state.heal_attempts += 1
            state.add_log(f"{self.name}: rerunning {len(rerun)} test case(s) once to check the heal")
        state.add_log(f"{self.name} completed heal phase: {len(applied)} applied, {len(results) - len(applied)} suggested")
        return state

    def _repair_scripts(self, state: WorkflowState) -> WorkflowState:
        targets = self.script_repair_service.find_targets(state)
        if not targets:
            self._settle_waiting(state, applied_ids=set())
            return state
        state.add_log(f"{self.name} started script repair phase for {len(targets)} test case(s)")
        entries = self.script_repair_service.repair(state)
        state.script_heal_results.extend(entries)
        applied = [entry for entry in entries if entry["status"] == "applied"]
        for entry in entries:
            state.add_log(
                f"{self.name}: {entry['status']} script fix for test case {entry['test_case_id']} "
                f"(RCA: {entry['rca_category']})"
            )
        applied_ids = {entry["test_case_id"] for entry in applied}
        self._settle_waiting(state, applied_ids)
        if applied:
            state.heal_rerun_test_case_ids = [UUID(t) for t in dict.fromkeys(applied_ids)]
            state.script_repair_attempts += 1
            state.add_log(f"{self.name}: rerunning {len(applied_ids)} test case(s) once to check the script fix")
        state.add_log(
            f"{self.name} completed script repair phase: {len(applied)} applied, {len(entries) - len(applied)} suggested"
        )
        return state

    # ----------------------------------------------------------- helpers

    def _new_heal_targets(self, state: WorkflowState) -> bool:
        healed_before = {heal["key"] for heal in state.heal_results}
        return any(key not in healed_before for key in find_heal_targets(state))

    def _can_repair(self, state: WorkflowState, heal: dict) -> bool:
        """AC-10: stage 2 will run for every test case this heal was rerun for."""
        latest = {str(k): v for k, v in latest_results(state.execution_results).items()}
        cases = {str(tc.id): tc for tc in state.generated_test_cases}
        settings = self.healing_service.settings
        ids = [str(t) for t in heal.get("test_case_ids", [])]
        return bool(ids) and all(
            latest.get(t) is not None and is_repair_candidate(state, latest[t], cases.get(t), settings)
            for t in ids
        )

    def _settle_waiting(self, state: WorkflowState, applied_ids: set[str]) -> None:
        """Heals that waited for stage 2 but got no applied script fix never passed: put them back."""
        waiting = {
            str(t)
            for heal in state.heal_results if heal.get("verified") == "pending_script_repair"
            for t in heal.get("test_case_ids", [])
        }
        unrepaired = sorted(waiting - applied_ids)
        if not unrepaired:
            return
        for key in self.healing_service.finish_pending(state, unrepaired, passed=False):
            state.add_log(f"{self.name}: heal of {key} put back as a suggestion, the script could not be repaired")
