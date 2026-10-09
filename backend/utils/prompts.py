"""
Loads a prompt template from backend/prompts/ and substitutes variables.

Uses string.Template ($var), not str.format(). Prompt files routinely need
to show a literal JSON example to the model - with .format() every { and }
in that example would need doubling ({{ }}), which makes the prompt file
painful to hand-edit. $var has no such conflict with JSON syntax.
"""

from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def load_prompt(filename: str, **variables: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        # Map old names to new standard agent md files
        if filename == "scenario_prompt.txt":
            path = PROMPTS_DIR / "agents" / "scenario_generator" / "roles_and_responsibilities.md"
        elif filename == "test_case_prompt.txt":
            path = PROMPTS_DIR / "agents" / "test_case_designer" / "roles_and_responsibilities.md"
        elif filename == "playwright_prompt.txt":
            path = PROMPTS_DIR / "agents" / "functional_script_generator" / "roles_and_responsibilities.md"
        elif filename == "evaluation_prompt.txt":
            path = PROMPTS_DIR / "agents" / "qa_test_executor" / "roles_and_responsibilities.md"
        elif filename == "api_spec_analyzer_prompt.txt":
            path = PROMPTS_DIR / "agents" / "api_spec_analyzer" / "roles_and_responsibilities.md"
        elif filename == "execution_analysis_prompt.txt":
            path = PROMPTS_DIR / "agents" / "execution_analysis" / "roles_and_responsibilities.md"
        elif filename == "defect_agent_prompt.txt":
            path = PROMPTS_DIR / "agents" / "defect_agent" / "roles_and_responsibilities.md"

    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")

    return Template(path.read_text(encoding="utf-8")).substitute(**variables)