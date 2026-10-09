"""
The only module allowed to import litellm. Agents call LLMService, never
litellm directly - that's what keeps ScenarioAgent's code identical
whether it's talking to OpenAI, Ollama, or Groq.

structured_generate() forwards **kwargs straight to litellm.completion(),
which is deliberate: it means tests can pass mock_response=... through
the real public method and exercise this exact code path, with no
monkeypatching and no real API key. See test_scenario_agent.py.
"""

import logging
from typing import TypeVar

from litellm import completion
from pydantic import BaseModel

from backend.config.settings import LLMConfig, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Providers whose litellm model string needs a prefix beyond the bare model
# name. OpenAI needs none ("gpt-4o-mini" as-is); others route through a
# provider/ prefix. Extend this as you actually add providers - don't
# pre-populate ones you're not using yet.
_PROVIDER_PREFIXES: dict[str, str] = {
    "openai": "",
    "ollama": "ollama/",
    "groq": "groq/",
    "nvidia": "openai/",
}

# How each provider is told "respond with JSON". These are NOT
# interchangeable: OpenAI (and OpenAI-compatible providers like Groq) use
# response_format={"type": "json_object"}; Ollama's own JSON mode is a
# different parameter (format="json") per litellm's Ollama docs. Passing
# the wrong one for a provider doesn't error - it just gets ignored, and
# you silently lose JSON enforcement while still passing tests that don't
# actually hit that provider. Add a case here before using a new provider,
# don't assume the OpenAI-style kwarg works everywhere.
_JSON_MODE_KWARGS: dict[str, dict] = {
    "ollama": {"format": "json"},
    "nvidia": {"response_format": {"type": "json_object"}},
}
_DEFAULT_JSON_MODE_KWARGS = {"response_format": {"type": "json_object"}}

DEFAULT_STRUCTURED_SYSTEM_PROMPT = (
    "You respond with a single valid JSON object and nothing else - "
    "no markdown fences, no commentary before or after it."
)


class LLMServiceError(Exception):
    """Raised when a completion call fails or returns something that
    doesn't validate against the expected schema. Agents catch this, not
    litellm's own exception types - callers shouldn't need to know which
    provider SDK is underneath."""


class LLMService:
    def __init__(self, config: LLMConfig | None = None):
        self.config = config or get_settings().llm
        
        # Enable LangSmith tracing for LiteLLM if configured
        self._init_langsmith_tracing()
    
    def _init_langsmith_tracing(self) -> None:
        """Initialize LangSmith tracing for LiteLLM if enabled.
        
        This integrates LiteLLM with LangSmith using the official integration.
        When enabled, all LLM calls will be traced to LangSmith automatically.
        
        Error handling: Any tracing setup failure is logged but never breaks LLM functionality.
        """
        try:
            settings = get_settings()
            if not settings.langsmith.tracing_enabled:
                return
            
            import litellm
            
            # Enable LangSmith callback (official LiteLLM integration)
            if "langsmith" not in (litellm.callbacks or []):
                litellm.callbacks = (litellm.callbacks or []) + ["langsmith"]
                logger.info("LiteLLM LangSmith tracing enabled")
                
        except Exception as e:
            # Tracing failure should never break LLM functionality
            logger.warning(f"Failed to enable LiteLLM LangSmith tracing: {e}")


    def _model_string(self) -> str:
        prefix = _PROVIDER_PREFIXES.get(self.config.provider, "")
        return f"{prefix}{self.config.model}"

    def _json_mode_kwargs(self) -> dict:
        return _JSON_MODE_KWARGS.get(self.config.provider, _DEFAULT_JSON_MODE_KWARGS)
    
    def _completion_kwargs(self) -> dict:
        kwargs = {
            "model": self._model_string(),
            "temperature": self.config.temperature,
        }

        if self.config.api_base:
            kwargs["api_base"] = self.config.api_base

        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key

        return kwargs

    def generate(self, *, system: str, user: str, **kwargs) -> str:
        """Plain text completion. Returns the raw response content."""
        try:
            response = completion(
                model=self._model_string(),
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                api_base=self.config.api_base,
                api_key=self.config.api_key,
                **kwargs,
            )
        except Exception as exc:
            raise LLMServiceError(f"LLM call failed ({self.config.provider}/{self.config.model}): {exc}") from exc

        return response.choices[0].message.content

    def structured_generate(
        self,
        *,
        user: str,
        response_model: type[T],
        system: str = DEFAULT_STRUCTURED_SYSTEM_PROMPT,
        max_retries: int = 3,
        **kwargs,
    ) -> T:
        """Completion constrained to JSON, validated into response_model.

        response_model must describe a JSON *object* at the top level -
        OpenAI's json_object mode rejects a bare top-level array. Wrap
        list output in a small model with one field, e.g. {"items": [...]}.
        """
        import json
        import time
        
        last_exception = None
        last_content = ""
        
        for attempt in range(max_retries):
            try:
                try:
                    schema_json = json.dumps(response_model.model_json_schema(), indent=2)
                    user_prompt = f"{user}\n\nYou MUST respond with a JSON object that adheres strictly to this JSON Schema:\n{schema_json}\n\nDo not include any extra keys, markdown tags, or explanations outside the JSON object."
                    
                    response = completion(
                        model=self._model_string(),
                        messages=[{"role": "system", "content": system}, {"role": "user", "content": user_prompt}],
                        api_base=self.config.api_base,
                        api_key=self.config.api_key,
                        **self._json_mode_kwargs(),
                        **kwargs,
                    )
                except Exception as exc:
                    raise LLMServiceError(f"LLM call failed ({self.config.provider}/{self.config.model}): {exc}") from exc

                content = response.choices[0].message.content.strip()
                last_content = content
                # Strip markdown fences if present
                if content.startswith("```"):
                    lines = content.splitlines()
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    content = "\n".join(lines).strip()
                # Strip any non-JSON prefix before the first '{' or '['
                brace_pos = content.find("{")
                bracket_pos = content.find("[")
                first_pos = brace_pos if brace_pos >= 0 else bracket_pos
                if first_pos is not None and first_pos >= 0:
                    content = content[first_pos:]
                if content and content[-1] not in ("}", "]"):
                    # Trim trailing non-JSON characters
                    last_brace = content.rfind("}")
                    last_bracket = content.rfind("]")
                    last_pos = last_brace if last_brace >= last_bracket else last_bracket
                    if last_pos >= 0:
                        content = content[:last_pos + 1]
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    pass  # let model_validate_json handle the error
                else:
                    if "test_cases" in parsed and isinstance(parsed["test_cases"], list):
                        for tc in parsed["test_cases"]:
                            if isinstance(tc.get("steps"), str):
                                tc["steps"] = [tc["steps"]]
                            elif "steps" not in tc:
                                tc["steps"] = ["Execute test case"]
                            if "expected_result" not in tc:
                                tc["expected_result"] = "Test completed as expected"
                            if "preconditions" not in tc:
                                tc["preconditions"] = []
                            if "test_data" not in tc:
                                tc["test_data"] = {}
                    if "scenarios" in parsed and isinstance(parsed["scenarios"], list):
                        priority_map = {"critical": "high", "crit": "high", "critical high": "high", "highest": "high",
                                        "lowest": "low", "lowest priority": "low"}
                        for sc in parsed["scenarios"]:
                            p = sc.get("priority")
                            if isinstance(p, str) and p.lower().strip() in priority_map:
                                sc["priority"] = priority_map[p.lower().strip()]
                            if "tags" not in sc:
                                sc["tags"] = []
                    content = json.dumps(parsed)

                return response_model.model_validate_json(content)
            except Exception as exc:
                last_exception = exc
                logger.warning(
                    f"structured_generate attempt {attempt + 1} failed: {exc}. Retrying..."
                )
                if attempt < max_retries - 1:
                    time.sleep(1)
        
        # If we reach here, all attempts failed
        logger.warning(
            "structured_generate raw output (first 2000 chars): %s", last_content[:2000]
        )
        raise LLMServiceError(
            f"LLM response did not match {response_model.__name__} after {max_retries} attempts: {last_exception}\nRaw content: {last_content}"
        )