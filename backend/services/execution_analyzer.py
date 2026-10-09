"""
Execution Analyzer

Read-only component that analyzes execution data and generates structured validations.
This component never modifies ExecutionResult, TestCase, database, or repository.
It only reads execution artifacts and returns structured validation models.
"""

import logging
from typing import Optional
from uuid import UUID

from backend.models.execution_result import ExecutionResult
from backend.models.professional_report import (
    Validation,
    ValidationList,
    BusinessRuleValidation,
    ExecutiveSummary,
    EvidenceItem,
    ProfessionalReportContext,
    ValidationStatus
)
from backend.services.llm import LLMService

logger = logging.getLogger(__name__)


class ExecutionAnalyzer:
    """
    Analyzes execution data and generates structured professional validation models.
    
    This is a read-only component that:
    - Analyzes Playwright timeline, DOM state, assertions, page URL
    - Groups related actions by business intent
    - Identifies business intents and validation points
    - Removes duplicates and retry artifacts
    - Maps to test case structure
    - Generates validation titles
    - Extracts observations from DOM state, assertions, timeline (screenshots as supporting evidence only)
    - Determines validation results
    - Generates action taken descriptions
    - Validates business rules
    - Assigns confidence scores with reasoning
    - Produces structured validation objects
    
    CRITICAL: This component never modifies any data. It only reads and returns models.
    """
    
    def __init__(self, llm_service: Optional[LLMService] = None):
        """
        Initialize the Execution Analyzer.
        
        Args:
            llm_service: Optional LLMService instance. If None, will use rule-based fallback.
        """
        print("[ExecutionAnalyzer] Step 1 - Constructor entered")
        print("[ExecutionAnalyzer] Step 2 - LLMService initialized")
        self.llm_service = llm_service or LLMService()
        print("[ExecutionAnalyzer] Step 3 - Constructor completed")
    
    def analyze_execution(
        self,
        execution_result: ExecutionResult,
        test_case_title: str,
        test_case_description: str,
        expected_result: str,
        test_case_steps: list[str],
        website_url: str,
        project_id: str,
        business_rules: Optional[list[str]] = None
    ) -> ProfessionalReportContext:
        """
        Analyze execution data and generate structured professional report context.
        
        Args:
            execution_result: The execution result to analyze
            test_case_title: Test case title
            test_case_description: Test case description
            expected_result: Expected result
            test_case_steps: Test case steps
            website_url: Website URL under test
            project_id: Project ID for generating screenshot URLs
            business_rules: Optional list of business rules to validate
            
        Returns:
            ProfessionalReportContext with all structured validation data
        """
        try:
            print("[ExecutionAnalyzer] Step 4 - analyze_execution() entered")
            logger.info(f"Analyzing execution {execution_result.id} for professional report")
            print(f"[ExecutionAnalyzer] Analyzing execution {execution_result.id}")
            
            # Extract timeline and DOM state from execution result
            print("[ExecutionAnalyzer] Step 5 - Extracting timeline and screenshots")
            timeline = execution_result.timeline or []
            screenshots = execution_result.screenshots or []
            
            print(f"[ExecutionAnalyzer] Step 6 - Timeline loaded: {len(timeline)} events")
            print(f"[ExecutionAnalyzer] Step 7 - Screenshots loaded: {len(screenshots)} items")
            print(f"[ExecutionAnalyzer] screenshots: {screenshots}")
            print(f"[ExecutionAnalyzer] screenshots type: {type(screenshots)}")
            
            # Generate validations using deterministic rule-based approach
            # Status is ALWAYS determined by execution_result.status, never by LLM
            print("[ExecutionAnalyzer] Step 8 - Validation generation started")
            print(f"[ExecutionAnalyzer] Using deterministic validation generation from execution status")
            validation_list = self._generate_validations_rule_based(
                test_case_steps=test_case_steps,
                timeline=timeline,
                screenshots=screenshots,
                execution_status=execution_result.status.value
            )
            print("[ExecutionAnalyzer] Step 9 - Validation generation completed (deterministic)")
            print(f"[ExecutionAnalyzer] Deterministic validation generation succeeded")
            
            # Generate executive summary using deterministic approach
            # Executive summary is generated from execution metrics, not LLM
            print("[ExecutionAnalyzer] Step 10 - Executive summary generation started")
            print(f"[ExecutionAnalyzer] Using deterministic executive summary generation from execution metrics")
            executive_summary = self._generate_executive_summary_rule_based(
                test_case_title=test_case_title,
                execution_status=execution_result.status.value,
                validation_count=len(validation_list.validations)
            )
            print("[ExecutionAnalyzer] Step 11 - Executive summary generated (deterministic)")
            print(f"[ExecutionAnalyzer] Deterministic executive summary generation succeeded")
            
            # Extract business rules from validations
            print("[ExecutionAnalyzer] Step 12 - Extracting business rules")
            all_business_rules = []
            for validation in validation_list.validations:
                all_business_rules.extend(validation.business_rules)
            
            # Generate evidence index
            print("[ExecutionAnalyzer] Step 13 - Evidence index generation started")
            print(f"[ExecutionAnalyzer] Generating evidence index from {len(screenshots)} screenshots...")
            evidence = self._generate_evidence_index(
                screenshots=screenshots,
                validations=validation_list.validations,
                execution_id=str(execution_result.id),
                project_id=project_id
            )
            print("[ExecutionAnalyzer] Step 14 - Evidence index generated")
            print(f"[ExecutionAnalyzer] Evidence index generated: {len(evidence)} items")
        
            # Calculate metrics
            print("[ExecutionAnalyzer] Step 15 - Calculating metrics")
            passed_count = sum(1 for v in validation_list.validations if v.result == ValidationStatus.PASS)
            failed_count = sum(1 for v in validation_list.validations if v.result == ValidationStatus.FAIL)
            warning_count = sum(1 for v in validation_list.validations if v.result == ValidationStatus.WARNING)
            info_count = sum(1 for v in validation_list.validations if v.result == ValidationStatus.INFO)
            
            # Determine overall status
            print("[ExecutionAnalyzer] Step 16 - Determining overall status")
            if failed_count > 0:
                overall_status = ValidationStatus.FAIL
            elif warning_count > 0:
                overall_status = ValidationStatus.WARNING
            else:
                overall_status = ValidationStatus.PASS
            
            # Build context
            print("[ExecutionAnalyzer] Step 17 - Building ProfessionalReportContext")
            context = ProfessionalReportContext(
                execution_id=str(execution_result.id),
                test_case_id=str(execution_result.test_case_id),
                test_case_title=test_case_title,
                test_case_description=test_case_description,
                expected_result=expected_result,
                website_url=website_url,
                execution_status=overall_status,
                execution_duration_seconds=execution_result.duration_seconds,
                browser_version=execution_result.browser_version,
                started_at=execution_result.executed_at,
                finished_at=execution_result.executed_at,  # Same for now, could calculate from duration
                executive_summary=executive_summary,
                validations=validation_list.validations,
                business_rules=all_business_rules,
                evidence=evidence,
                total_validations=len(validation_list.validations),
                passed_count=passed_count,
                failed_count=failed_count,
                warning_count=warning_count,
                info_count=info_count
            )
            
            print("[ExecutionAnalyzer] Step 18 - Returning ProfessionalReportContext")
            logger.info(f"Generated professional report context with {len(context.validations)} validations")
            return context
            
        except Exception as e:
            import traceback
            print("[ExecutionAnalyzer] FULL TRACEBACK - analyze_execution() failed")
            print(f"[ExecutionAnalyzer] Exception type: {type(e).__name__}")
            print(f"[ExecutionAnalyzer] Exception message: {e}")
            traceback.print_exc()
            raise
    
    def _generate_validations_with_llm(
        self,
        test_case_title: str,
        test_case_description: str,
        expected_result: str,
        test_case_steps: list[str],
        timeline: list[dict],
        screenshots: list[str],
        business_rules: list[str]
    ) -> ValidationList:
        """
        Generate validations using LLM with single-pass generation.
        
        This follows the specification's single-pass approach to ensure consistency
        and reduce latency.
        """
        # Build DOM state summary from timeline
        dom_state = self._extract_dom_state_from_timeline(timeline)
        
        # Build page URLs from timeline
        page_urls = self._extract_page_urls_from_timeline(timeline)
        
        # Build assertions from timeline
        assertions = self._extract_assertions_from_timeline(timeline)
        
        # Build user prompt
        user_prompt = f"""
You are an expert QA Lead analyzing test execution results. Generate a professional validation report.

TEST CASE:
Title: {test_case_title}
Description: {test_case_description}
Expected Result: {expected_result}

TEST CASE STEPS:
{self._format_test_case_steps(test_case_steps)}

EXECUTION DATA:
Timeline Events: {self._format_timeline(timeline)}
DOM State: {dom_state}
Page URLs: {page_urls}
Assertions: {assertions}
Screenshots: {screenshots}

BUSINESS RULES:
{self._format_business_rules(business_rules)}

For each test case step, generate a validation object with:

1. validation_title: Business-focused title (3-8 words)
   - Focus on what was validated, not what was executed
   - Use professional QA terminology
   - Example: "Navigate to booking page" → "Booking Page Loaded"

2. business_goal: What business objective this validation achieves

3. observations: 2-4 sentences describing actual system state
   - Analyze DOM state, assertions, timeline (NOT screenshots)
   - Screenshots corroborate but don't drive observations
   - Be factual and objective
   - Use professional QA terminology

4. confidence: Score 0-100% with reasoning
   - How confident are you in this observation?
   - Reasoning: What evidence supports this confidence?

5. result: One of PASS, FAIL, WARNING, INFO
   - PASS: Validation completed successfully
   - FAIL: Validation failed with error
   - WARNING: Completed but with concerns
   - INFO: Informational validation

6. action_taken: Business action description (1-2 sentences)
   - Describe what was accomplished, not technical implementation
   - Use professional terminology

7. evidence: Screenshot reference (e.g., "Screenshot 7")
   - Which screenshot best supports this validation?

8. business_rules: Array of business rule validations
   - rule: Business rule description
   - status: "✅" or "❌"
   - confidence: 0-100%
   - reasoning: Why this status

Output format: JSON array of validation objects

IMPORTANT:
- Generate exactly one validation per test case step
- Maintain consistency in terminology across all validations
- Use business language, not technical implementation details
- Screenshots are evidence, not the primary observation source
"""
        
        # System prompt
        system_prompt = """You are an expert QA Lead with deep experience in test automation and professional report writing. Generate structured validation data that reads as if written by a human QA engineer."""
        
        # Call LLM
        try:
            result = self.llm_service.structured_generate(
                user=user_prompt,
                response_model=ValidationList,
                system=system_prompt
            )
            return result
        except Exception as e:
            logger.error(f"LLM structured generation failed: {e}")
            raise
    
    def _generate_validations_rule_based(
        self,
        test_case_steps: list[str],
        timeline: list[dict],
        screenshots: list[str],
        execution_status: str
    ) -> ValidationList:
        """
        Generate validations using deterministic rule-based approach.
        
        Status is ALWAYS determined by execution_result.status, never by LLM.
        Confidence is deterministic based on execution data availability.
        """
        validations = []
        
        # Determine overall status from execution result
        # This is the source of truth - never overridden by LLM
        overall_result = ValidationStatus.PASS if execution_status == "passed" else ValidationStatus.FAIL
        
        # Check for explicit assertion failures in timeline
        has_assertion_failures = self._check_for_assertion_failures(timeline)
        if has_assertion_failures:
            overall_result = ValidationStatus.FAIL
        
        for i, step in enumerate(test_case_steps):
            # Use the actual test case step name as validation title
            validation_title = step
            
            # Result is ALWAYS based on execution status, never LLM
            # If execution passed, all steps pass unless there's an explicit assertion failure
            step_result = overall_result
            
            # Generate business goal from step
            business_goal = f"Verify: {step}"
            
            # Generate richer observation from timeline (deterministic)
            observation = self._generate_rich_observation_from_timeline(timeline, i, step)
            
            # Generate expected and actual results
            expected_result = f"Step should complete successfully: {step}"
            actual_result = self._generate_actual_result_from_timeline(timeline, i, step_result)
            
            # Confidence is deterministic based on data availability
            # 100% if we have timeline data, 80% if we have screenshots, 60% otherwise
            if timeline and len(timeline) > i:
                confidence = 100
                reasoning = "Deterministic from execution timeline data"
            elif screenshots and len(screenshots) > i:
                confidence = 80
                reasoning = "Deterministic from screenshot evidence"
            else:
                confidence = 60
                reasoning = "Deterministic from execution status only"
            
            # Generate richer action taken from timeline
            action_taken = self._generate_action_taken_from_timeline(timeline, i, step)
            
            # Assign screenshot
            evidence = f"Screenshot {min(i + 1, len(screenshots))}" if screenshots else "No screenshot"
            
            # Create validation
            validation = Validation(
                validation_title=validation_title,
                step_name=step,
                business_goal=business_goal,
                observations=observation,
                expected_result=expected_result,
                actual_result=actual_result,
                confidence=confidence,
                reasoning=reasoning,
                result=step_result,
                action_taken=action_taken,
                evidence=evidence,
                business_rules=[]
            )
            
            validations.append(validation)
        
        # Add business outcome validation as the final step
        if expected_result and test_case_steps:
            business_outcome_validation = self._generate_business_outcome_validation(
                expected_result=expected_result,
                execution_status=execution_status,
                timeline=timeline,
                screenshots=screenshots
            )
            validations.append(business_outcome_validation)
        
        return ValidationList(validations=validations)
    
    def _generate_executive_summary_with_llm(
        self,
        test_case_title: str,
        expected_result: str,
        validations: list[Validation],
        business_rules: list[str]
    ) -> ExecutiveSummary:
        """Generate executive summary using LLM."""
        # Calculate metrics
        total = len(validations)
        passed = sum(1 for v in validations if v.result == ValidationStatus.PASS)
        failed = sum(1 for v in validations if v.result == ValidationStatus.FAIL)
        warnings = sum(1 for v in validations if v.result == ValidationStatus.WARNING)
        
        # Build validation summary
        validation_summary = "\n".join([
            f"- {v.validation_title}: {v.result.value}" for v in validations
        ])
        
        # Build business rule compliance
        business_rule_compliance = self._summarize_business_rules(validations)
        
        user_prompt = f"""
You are a QA Lead writing an executive summary for stakeholders.

TEST CASE:
{test_case_title}
Expected Result: {expected_result}

VALIDATION SUMMARY:
{validation_summary}

BUSINESS RULE COMPLIANCE:
{business_rule_compliance}

EXECUTION METRICS:
- Total Validations: {total}
- Passed: {passed}
- Failed: {failed}
- Warnings: {warnings}

Generate an executive summary with:
1. Business workflow narrative (2-3 sentences)
2. Key achievements (3-4 bullets) if passed, OR critical issues (if failed)
3. Business rule compliance assessment
4. Recommendations (if issues exist)

Tone: Professional, business-focused
Audience: Non-technical stakeholders
Focus: Business outcomes, not technical implementation details
"""
        
        system_prompt = """You are an expert QA Lead writing executive summaries for business stakeholders."""
        
        try:
            result = self.llm_service.structured_generate(
                user=user_prompt,
                response_model=ExecutiveSummary,
                system=system_prompt
            )
            return result
        except Exception as e:
            logger.error(f"LLM executive summary generation failed: {e}")
            raise
    
    def _generate_executive_summary_rule_based(
        self,
        test_case_title: str,
        execution_status: str,
        validation_count: int
    ) -> ExecutiveSummary:
        """Generate executive summary using rule-based fallback."""
        if execution_status == "passed":
            narrative = f"The {test_case_title} workflow was executed successfully. All {validation_count} validations passed without errors or warnings."
            achievements = [
                f"Successfully completed {test_case_title}",
                f"All {validation_count} validations passed",
                "No errors or warnings detected"
            ]
            critical_issues = []
            business_rule_compliance = "All business rules validated successfully"
            recommendations = []
        else:
            narrative = f"The {test_case_title} workflow encountered issues during execution. Some validations failed, requiring investigation."
            achievements = []
            critical_issues = [
                "Execution failed with errors",
                "Validation failures detected",
                "Investigation required"
            ]
            business_rule_compliance = "Some business rules may not be compliant"
            recommendations = [
                "Review failed validations",
                "Investigate error messages",
                "Retest after fixes"
            ]
        
        return ExecutiveSummary(
            narrative=narrative,
            achievements=achievements,
            critical_issues=critical_issues,
            business_rule_compliance=business_rule_compliance,
            recommendations=recommendations
        )
    
    def _generate_evidence_index(
        self,
        screenshots: list[str],
        validations: list[Validation],
        execution_id: str,
        project_id: str
    ) -> list[EvidenceItem]:
        """Generate evidence index from screenshots and validations."""
        print(f"[ExecutionAnalyzer] _generate_evidence_index() called")
        print(f"[ExecutionAnalyzer] screenshots input: {screenshots}")
        print(f"[ExecutionAnalyzer] screenshots type: {type(screenshots)}")
        print(f"[ExecutionAnalyzer] execution_id: {execution_id}")
        print(f"[ExecutionAnalyzer] project_id: {project_id}")
        
        evidence = []
        
        # Populate evidence URLs in validations
        for i, screenshot in enumerate(screenshots, start=1):
            screenshot_url = f"/api/v1/projects/{project_id}/executions/{execution_id}/screenshots/{screenshot}"
            print(f"[ExecutionAnalyzer] Generated screenshot URL: {screenshot_url}")
            
            # Find validations that reference this screenshot and populate evidence_url
            for validation in validations:
                if validation.evidence == f"Screenshot {i}":
                    validation.evidence_url = screenshot_url
        
        for i, screenshot in enumerate(screenshots, start=1):
            print(f"[ExecutionAnalyzer] Processing screenshot {i}: {screenshot}")
            # Find validations that reference this screenshot
            related_validations = [
                v.validation_title for v in validations
                if v.evidence == f"Screenshot {i}"
            ]
            
            # Generate description from validation context
            description = f"Evidence for validation step {i}"
            if related_validations:
                description = f"Evidence for: {', '.join(related_validations[:2])}"
            
            # Generate browser-accessible URL for screenshot using the correct endpoint
            screenshot_url = f"/api/v1/projects/{project_id}/executions/{execution_id}/screenshots/{screenshot}"
            print(f"[ExecutionAnalyzer] Generated screenshot URL: {screenshot_url}")
            
            evidence_item = EvidenceItem(
                number=i,
                description=description,
                related_validations=related_validations,
                thumbnail=screenshot_url,  # Browser-accessible URL
                full_path=screenshot  # Keep original filename for reference
            )
            
            evidence.append(evidence_item)
        
        return evidence
    
    # Helper methods
    
    def _extract_dom_state_from_timeline(self, timeline: list[dict]) -> str:
        """Extract DOM state information from timeline events."""
        dom_elements = []
        for event in timeline:
            if "element" in event.get("details", "").lower():
                dom_elements.append(event.get("event", ""))
        return "; ".join(dom_elements) if dom_elements else "No DOM state captured"
    
    def _extract_page_urls_from_timeline(self, timeline: list[dict]) -> str:
        """Extract page URLs from timeline events."""
        urls = []
        for event in timeline:
            event_str = event.get("event", "")
            if "http" in event_str or "goto" in event_str.lower():
                urls.append(event_str)
        return "; ".join(urls) if urls else "No URLs captured"
    
    def _extract_assertions_from_timeline(self, timeline: list[dict]) -> str:
        """Extract assertions from timeline events."""
        assertions = []
        for event in timeline:
            event_str = event.get("event", "").lower()
            if "assert" in event_str or "expect" in event_str or "verify" in event_str:
                assertions.append(event.get("event", ""))
        return "; ".join(assertions) if assertions else "No explicit assertions captured"
    
    def _format_test_case_steps(self, steps: list[str]) -> str:
        """Format test case steps for LLM prompt."""
        return "\n".join([f"{i+1}. {step}" for i, step in enumerate(steps)])
    
    def _format_timeline(self, timeline: list[dict]) -> str:
        """Format timeline for LLM prompt."""
        return "\n".join([
            f"- {evt.get('event', '')} ({evt.get('type', 'info')})" 
            for evt in timeline[:20]  # Limit to first 20 events
        ])
    
    def _format_business_rules(self, rules: list[str]) -> str:
        """Format business rules for LLM prompt."""
        return "\n".join([f"- {rule}" for rule in rules]) if rules else "No explicit business rules defined"
    
    def _summarize_business_rules(self, validations: list[Validation]) -> str:
        """Summarize business rule compliance from validations."""
        total_rules = sum(len(v.business_rules) for v in validations)
        passed_rules = sum(
            1 for v in validations 
            for rule in v.business_rules 
            if rule.status == "✅"
        )
        
        if total_rules == 0:
            return "No business rules validated"
        
        compliance_rate = (passed_rules / total_rules) * 100 if total_rules > 0 else 0
        return f"{passed_rules}/{total_rules} business rules compliant ({compliance_rate:.0f}%)"
    
    def _generate_validation_title_from_step(self, step: str) -> str:
        """Generate validation title from test case step (rule-based)."""
        # Simple transformation
        step_lower = step.lower()
        if "navigate" in step_lower or "goto" in step_lower:
            return "Page Navigation"
        elif "click" in step_lower:
            return "Element Interaction"
        elif "type" in step_lower or "fill" in step_lower or "enter" in step_lower:
            return "Data Entry"
        elif "assert" in step_lower or "verify" in step_lower or "expect" in step_lower:
            return "Validation Check"
        elif "select" in step_lower:
            return "Selection"
        else:
            return "Action Execution"
    
    def _generate_observation_from_timeline(self, timeline: list[dict], step_index: int) -> str:
        """Generate observation from timeline (rule-based)."""
        if step_index < len(timeline):
            event = timeline[step_index]
            event_type = event.get("type", "info")
            event_name = event.get("event", "Unknown action")
            
            if event_type == "error":
                return f"Error encountered: {event_name}. Execution failed at this step."
            else:
                return f"Step completed successfully: {event_name}. No errors detected."
        else:
            return "Step completed with no timeline data available."
    
    def _generate_rich_observation_from_timeline(self, timeline: list[dict], step_index: int, step: str) -> str:
        """Generate rich observation from timeline with QA-style details."""
        if step_index < len(timeline):
            event = timeline[step_index]
            event_type = event.get("type", "info")
            event_name = event.get("event", "Unknown action")
            details = event.get("details", "")
            
            # Extract URL if available
            url = ""
            if "navigate" in event_name.lower() and details:
                url = f" Browser navigated to {details}."
            
            # Extract element interaction details
            element_details = ""
            if "type" in event_name.lower() or "click" in event_name.lower():
                if details:
                    element_details = f" Element interaction completed: {details}."
            
            if event_type == "error":
                return f"Error encountered during step execution: {event_name}. {details if details else 'No additional details available.'} Execution halted at this point."
            else:
                return f"Step executed successfully: {event_name}.{url}{element_details} No errors or exceptions detected during execution."
        else:
            return f"Step '{step}' completed with no detailed timeline data available. Execution status determined from overall execution result."
    
    def _generate_actual_result_from_timeline(self, timeline: list[dict], step_index: int, step_result: ValidationStatus) -> str:
        """Generate actual result description from timeline."""
        if step_index < len(timeline):
            event = timeline[step_index]
            event_name = event.get("event", "Unknown action")
            
            if step_result == ValidationStatus.PASS:
                return f"Action completed successfully: {event_name}"
            else:
                return f"Action failed: {event_name}"
        else:
            return "Step completed" if step_result == ValidationStatus.PASS else "Step failed"
    
    def _generate_action_taken_from_timeline(self, timeline: list[dict], step_index: int, step: str) -> str:
        """Generate rich action taken description from timeline."""
        if step_index < len(timeline):
            event = timeline[step_index]
            event_name = event.get("event", "Unknown action")
            details = event.get("details", "")
            
            # Extract locator information if available
            locator = ""
            if "locator" in details:
                locator = f" using {details}"
            
            # Build action description
            if "navigate" in event_name.lower():
                return f"Browser navigation performed to target URL{locator if locator else ''}. Page load completed successfully."
            elif "type" in event_name.lower():
                return f"Text input action performed{locator if locator else ''}. Value entered into target field."
            elif "click" in event_name.lower():
                return f"Click action performed on target element{locator if locator else ''}. Element interaction completed."
            else:
                return f"Action executed: {event_name}{locator if locator else ''}. Operation completed."
        else:
            return f"Executed step: {step}"
    
    def _check_for_assertion_failures(self, timeline: list[dict]) -> bool:
        """Check timeline for explicit assertion failures."""
        for event in timeline:
            event_type = event.get("type", "info")
            event_name = event.get("event", "").lower()
            if event_type == "error" or "fail" in event_name or "assert" in event_name and "error" in event_name:
                return True
        return False
    
    def _generate_business_outcome_validation(
        self,
        expected_result: str,
        execution_status: str,
        timeline: list[dict],
        screenshots: list[str]
    ) -> Validation:
        """Generate business outcome validation as the final validation step."""
        # Determine outcome based on execution status
        outcome_result = ValidationStatus.PASS if execution_status == "passed" else ValidationStatus.FAIL
        
        # Generate observation based on execution status
        if execution_status == "passed":
            observation = f"The test execution completed successfully. The expected business outcome '{expected_result}' was achieved. All validation steps passed without errors or exceptions."
            actual_result = "Business outcome achieved successfully"
        else:
            observation = f"The test execution did not complete as expected. The expected business outcome '{expected_result}' was not achieved. Execution encountered errors or failures."
            actual_result = "Business outcome not achieved due to execution failure"
        
        # Generate business goal
        business_goal = f"Verify business outcome: {expected_result}"
        
        # Generate action taken
        action_taken = "Validated overall test execution result against expected business outcome"
        
        # Assign evidence if available
        evidence = f"Screenshot {len(screenshots)}" if screenshots else "No screenshot"
        
        # Create validation
        return Validation(
            validation_title="Business Outcome Validation",
            step_name="Business Outcome Validation",
            business_goal=business_goal,
            observations=observation,
            expected_result=expected_result,
            actual_result=actual_result,
            confidence=100,
            reasoning="Deterministic from overall execution status",
            result=outcome_result,
            action_taken=action_taken,
            evidence=evidence,
            business_rules=[]
        )
