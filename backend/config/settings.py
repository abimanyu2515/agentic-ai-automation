"""
Configuration loader.

Precedence: .env overrides default_config.yaml.
YAML holds the *shape* of config (agents enabled, workflow mode, thresholds).
.env holds secrets and anything that changes per machine/environment.

Usage:
    from backend.config.settings import get_settings
    settings = get_settings()
    settings.llm.model
    settings.agents.scenario
"""

from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).parent
DEFAULT_CONFIG_PATH = CONFIG_DIR / "default_config.yaml"

load_dotenv()


class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    temperature: float = 1.0
    api_key: str | None = None
    api_base: str | None = None  # required for ollama (e.g. http://localhost:11434); leave unset for cloud providers


LLM_ROLES = ("generation", "analysis", "chat", "retrieval")


class LLMRoleConfig(BaseModel):
    """One LLM role (spec 0003). Every call made by a role goes through that
    role's shared pool in llm.py, which reads these limits on each acquire.
    Built from GENERATION_LLM_* / ANALYSIS_LLM_* env vars, falling back to llm.*."""
    provider: str
    models: list[str] = Field(min_length=1)
    api_key: str | None = None
    api_base: str | None = None
    max_concurrent: int = Field(default=2, ge=1)
    min_interval_seconds: float = Field(default=0, ge=0)
    cooldown_seconds: float = Field(default=60, ge=0)
    max_wait_seconds: float = Field(default=180, gt=0)


class WorkflowConfig(BaseModel):
    mode: str = "sequential"
    human_review_enabled: bool = True
    evaluation_threshold: float = 0.75


class AgentsConfig(BaseModel):
    requirement: bool = True
    scenario: bool = True
    testcase: bool = True
    evaluation: bool = True
    human_approval: bool = True
    playwright: bool = False
    execution: bool = False
    report: bool = False


class BrowserConfig(BaseModel):
    type: str = "chromium"
    headless: bool = True


class RAGConfig(BaseModel):
    enabled: bool = False
    vector_db_provider: str = "chroma"
    vector_db_path: str = "./data/vector_store"
    ragflow_api_base: str = "http://localhost:9380"
    ragflow_api_key: str | None = None  # from .env only; never returned by a route
    # Per project knowledge base (spec 0006).
    storage_root: str = "data/projects"
    upload_max_mb: int = Field(default=20, ge=1)
    upload_types: list[str] = ["pdf", "docx", "txt", "md", "csv", "xlsx"]
    parse_poll_seconds: float = Field(default=5, gt=0)
    parse_timeout_seconds: float = Field(default=600, gt=0)
    # Waits before each retry of a failed RAGFlow call (AC-4).
    index_retry_delays: list[float] = [10, 60, 300]
    # Minutes between replays of the pending RAGFlow delete table (spec 0007, AC-7).
    delete_resweep_minutes: float = Field(default=30, gt=0)
    # Graded retrieval loop in the workflow (spec 0006, AC-17 to AC-22).
    workflow_retrieval_enabled: bool = False
    top_k: int = Field(default=8, ge=1)
    max_retrieval_attempts: int = Field(default=2, ge=1)
    min_relevant_chunks: int = Field(default=2, ge=1)
    # Retrieval role pool defaults; RETRIEVAL_LLM_MAX_CONCURRENT and friends override them.
    retrieval_llm_max_concurrent: int = Field(default=2, ge=1)
    retrieval_llm_cooldown_seconds: float = Field(default=30, ge=0)
    retrieval_llm_max_wait_seconds: float = Field(default=20, gt=0)


class GenerationConfig(BaseModel):
    scenario_count: int = 3


class EvaluationConfig(BaseModel):
    # Word-overlap (Jaccard) on title+expected_result combined, not title
    # alone - title-only similarity can't distinguish "correct password"
    # from "incorrect password" (near-identical text, opposite meaning).
    # Calibrated against real examples, not guessed - see evaluation_agent.py.
    duplicate_similarity_threshold: float = 0.75
    # Below this relevance score, a test case is rejected outright rather
    # than sent for human review - e.g. a "Forgot Password" test case
    # generated for a "Profile Picture Upload" requirement isn't a
    # borderline call, it's just wrong.
    relevance_rejection_threshold: float = 0.3


class PlaywrightConfig(BaseModel):
    base_url: str = ""
    browser: str = "chromium"
    headless: bool = True
    timeout: int = 60
    retries: int = 0
    workspace_url: str = "http://localhost:3000"


class LangSmithConfig(BaseModel):
    """LangSmith tracing configuration.
    
    Loaded from environment variables:
    - LANGSMITH_TRACING: Enable/disable tracing (default: false)
    - LANGSMITH_ENDPOINT: API endpoint (default: https://api.smith.langchain.com)
    - LANGSMITH_API_KEY: API authentication key (required if tracing enabled)
    - LANGSMITH_PROJECT: Project name for traces (default: Enterprise-AI-TestAutomation)
    """
    tracing_enabled: bool = False
    endpoint: str = "https://api.smith.langchain.com"
    api_key: str | None = None
    project_name: str = "Enterprise-AI-TestAutomation"


class RepositoryConfig(BaseModel):
    provider: str = "json"
    database_url: str = "postgresql+psycopg2://postgres:scoot@localhost:5432/agentic_ai"
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout: int = 30


class ScriptRepairConfig(BaseModel):
    """Script repair from the RCA (spec 0011, AC-18), under `object_repository.healing.script_repair`."""
    enabled: bool = True
    min_rca_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    max_attempts: int = Field(default=1, ge=0)


class HealingConfig(BaseModel):
    """Self healing locators (spec 0011), under `object_repository.healing`."""
    enabled: bool = True
    auto_apply_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    suggest_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    min_margin: float = Field(default=0.15, ge=0.0, le=1.0)
    llm_enabled: bool = True
    max_attempts_per_run: int = Field(default=1, ge=0)
    max_candidates_for_llm: int = Field(default=5, ge=1)
    script_repair: ScriptRepairConfig = ScriptRepairConfig()


class ObjectRepositoryConfig(BaseModel):
    max_prompt_elements: int = 150
    locator_timeout_ms: int = 3000
    scan_status_ttl_seconds: int = 3600
    harvest_enabled: bool = True
    harvest_max_pages: int = 20
    harvest_page_budget_ms: int = 5000
    healing: HealingConfig = HealingConfig()


# axe-core tags the accessibility scan may request (spec 0011). A tag outside
# this set fails when the config loads, not partway through a run.
KNOWN_AXE_WCAG_TAGS = frozenset({
    "wcag2a", "wcag2aa", "wcag2aaa", "wcag21a", "wcag21aa", "wcag22aa",
    "best-practice", "section508", "EN-301-549",
})


class AccessibilityConfig(BaseModel):
    """Accessibility scans (spec 0011). A11Y_* env vars override some values."""
    enabled: bool = True
    wcag_tags: list[str] = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
    max_pages: int = Field(default=10, ge=1)
    page_budget_ms: int = Field(default=30000, ge=1000)
    max_llm_findings: int = Field(default=20, ge=0)
    llm_timeout_seconds: float = Field(default=60, gt=0)
    max_element_screenshots: int = Field(default=10, ge=0)
    max_nodes_per_rule: int = Field(default=50, ge=1)
    max_pages_per_finding: int = Field(default=20, ge=1)
    webhook_payload_max_bytes: int = Field(default=1_000_000, ge=10_000)
    scan_stale_after_seconds: int = Field(default=900, ge=60)

    @model_validator(mode="after")
    def _check_tags(self) -> "AccessibilityConfig":
        if not self.wcag_tags:
            raise ValueError("accessibility.wcag_tags must name at least one axe tag")
        unknown = [tag for tag in self.wcag_tags if tag not in KNOWN_AXE_WCAG_TAGS]
        if unknown:
            raise ValueError(f"accessibility.wcag_tags has unknown axe tags: {unknown}")
        return self


class ChatConfig(BaseModel):
    """Chat assistant (spec 0005). Defaults live under `chat:` in
    default_config.yaml; CHAT_* env vars override them."""
    enabled: bool = True
    history_messages: int = Field(default=20, ge=1)
    history_chars: int = Field(default=24000, ge=1000)
    max_tool_steps: int = Field(default=6, ge=1)
    tool_result_chars: int = Field(default=4000, ge=500)
    guide_chars: int = Field(default=8000, ge=500)
    max_streams: int = Field(default=10, ge=1)
    rate_limit_per_minute: int = Field(default=20, ge=1)
    retention_days: int = Field(default=90, ge=1)
    # Chat role pool defaults; CHAT_LLM_MAX_CONCURRENT and friends override them.
    llm_max_concurrent: int = Field(default=4, ge=1)
    llm_cooldown_seconds: float = Field(default=60, ge=0)
    llm_max_wait_seconds: float = Field(default=30, gt=0)


class TokenUsageConfig(BaseModel):
    """Token Utilization feature. Values saved from the UI (token_usage_settings.json)
    take precedence over these defaults."""
    enabled: bool = True
    monthly_budget_usd: float = Field(default=0.0, ge=0)  # 0 = no budget
    alert_threshold_pct: float = Field(default=80.0, ge=0, le=100)
    # {"<model>": {"input_per_1m": 0.15, "output_per_1m": 0.6}} in USD per 1M tokens
    pricing_overrides: dict[str, dict[str, float]] = {}


class JiraConfig(BaseModel):
    base_url: str = "https://your-domain.atlassian.net"
    email: str = ""
    api_token: str = ""
    project_key: str = ""
    default_issue_type: str = "Story"
    verify_ssl: bool = True
    resolved_statuses: list[str] = ["Done", "Closed", "Ready for Testing", "Resolved"]


class WebhookConfig(BaseModel):
    """Shared secrets for the machine callers (spec 0009). Read only from
    .env, never from YAML. Empty means the routes they gate return 503."""
    workspace_secret: str = ""
    jira_secret: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    llm: LLMConfig = LLMConfig()
    repository: RepositoryConfig = RepositoryConfig()
    workflow: WorkflowConfig = WorkflowConfig()
    agents: AgentsConfig = AgentsConfig()
    browser: BrowserConfig = BrowserConfig()
    rag: RAGConfig = RAGConfig()
    generation: GenerationConfig = GenerationConfig()
    evaluation: EvaluationConfig = EvaluationConfig()
    playwright: PlaywrightConfig = PlaywrightConfig()
    langsmith: LangSmithConfig = LangSmithConfig()
    jira: JiraConfig = JiraConfig()
    object_repository: ObjectRepositoryConfig = ObjectRepositoryConfig()
    accessibility: AccessibilityConfig = AccessibilityConfig()
    chat: ChatConfig = ChatConfig()
    token_usage: TokenUsageConfig = TokenUsageConfig()
    webhooks: WebhookConfig = WebhookConfig()
    # Read only from .env (never override.yaml); filled from llm.* when unset.
    llm_roles: dict[str, LLMRoleConfig] = {}

    @model_validator(mode="after")
    def _fill_llm_roles(self) -> "Settings":
        if not self.llm_roles:
            self.llm_roles = _build_llm_roles(self.llm, self.chat, self.rag)
        return self

    @classmethod
    def from_yaml(cls, path: Path = DEFAULT_CONFIG_PATH) -> "Settings":
        raw = yaml.safe_load(path.read_text()) if path.exists() else {}

        # Merge local overrides from override.yaml if exists
        override_path = CONFIG_DIR / "override.yaml"
        if override_path.exists():
            try:
                overrides = yaml.safe_load(override_path.read_text())
                if isinstance(overrides, dict):
                    for key, val in overrides.items():
                        if isinstance(val, dict) and key in raw and isinstance(raw[key], dict):
                            raw[key].update(val)
                        else:
                            raw[key] = val
            except Exception:
                pass

        # .env values win if set; LLM_API_KEY has no YAML equivalent on purpose.
        raw.setdefault("llm", {})["api_key"] = _env("LLM_API_KEY")
        raw.setdefault("llm", {})["api_base"] = _env("LLM_API_BASE")
        _override(raw, "llm", "provider", "LLM_PROVIDER")
        _override(raw, "llm", "model", "LLM_MODEL")
        _override(raw, "workflow", "evaluation_threshold", "EVALUATION_THRESHOLD", cast=float)
        _override(raw, "workflow", "human_review_enabled", "HUMAN_REVIEW_ENABLED", cast=_bool)

        # LangSmith configuration from .env
        raw.setdefault("langsmith", {})
        _override(raw, "langsmith", "tracing_enabled", "LANGSMITH_TRACING", cast=_bool)
        _override(raw, "langsmith", "endpoint", "LANGSMITH_ENDPOINT")
        raw.setdefault("langsmith", {})["api_key"] = _env("LANGSMITH_API_KEY")
        _override(raw, "langsmith", "project_name", "LANGSMITH_PROJECT")

        # RAGFlow settings from .env
        raw.setdefault("rag", {})
        _override(raw, "rag", "enabled", "RAG_ENABLED", cast=_bool)
        _override(raw, "rag", "vector_db_provider", "RAG_VECTOR_DB_PROVIDER")
        _override(raw, "rag", "ragflow_api_base", "RAGFLOW_API_BASE")
        raw["rag"]["ragflow_api_key"] = _env("RAGFLOW_API_KEY")
        _override(raw, "rag", "storage_root", "RAG_STORAGE_ROOT")
        _override(raw, "rag", "upload_max_mb", "RAG_UPLOAD_MAX_MB", cast=int)
        _override(raw, "rag", "upload_types", "RAG_UPLOAD_TYPES", cast=_csv_lower)
        _override(raw, "rag", "parse_poll_seconds", "RAG_PARSE_POLL_SECONDS", cast=float)
        _override(raw, "rag", "parse_timeout_seconds", "RAG_PARSE_TIMEOUT_SECONDS", cast=float)
        _override(raw, "rag", "index_retry_delays", "RAG_INDEX_RETRY_DELAYS", cast=_csv_floats)
        _override(raw, "rag", "delete_resweep_minutes", "RAG_DELETE_RESWEEP_MINUTES", cast=float)
        _override(raw, "rag", "workflow_retrieval_enabled", "RAG_WORKFLOW_RETRIEVAL_ENABLED", cast=_bool)
        _override(raw, "rag", "top_k", "RAG_TOP_K", cast=int)
        _override(raw, "rag", "max_retrieval_attempts", "RAG_MAX_RETRIEVAL_ATTEMPTS", cast=int)
        _override(raw, "rag", "min_relevant_chunks", "RAG_MIN_RELEVANT_CHUNKS", cast=int)

        # Repository settings overrides from .env
        raw.setdefault("repository", {})
        _override(raw, "repository", "provider", "REPOSITORY_PROVIDER")
        _override(raw, "repository", "database_url", "DATABASE_URL")
        _override(raw, "repository", "pool_size", "DB_POOL_SIZE", cast=int)
        _override(raw, "repository", "max_overflow", "DB_MAX_OVERFLOW", cast=int)
        _override(raw, "repository", "pool_timeout", "DB_POOL_TIMEOUT", cast=int)

        # Accessibility scan settings from .env (spec 0011)
        raw.setdefault("accessibility", {})
        _override(raw, "accessibility", "enabled", "A11Y_ENABLED", cast=_bool)
        _override(raw, "accessibility", "max_pages", "A11Y_MAX_PAGES", cast=int)
        _override(raw, "accessibility", "page_budget_ms", "A11Y_PAGE_BUDGET_MS", cast=int)
        _override(raw, "accessibility", "max_llm_findings", "A11Y_MAX_LLM_FINDINGS", cast=int)

        # Chat assistant settings from .env (spec 0005)
        raw.setdefault("chat", {})
        _override(raw, "chat", "enabled", "CHAT_ENABLED", cast=_bool)
        for field, cast in (
            ("history_messages", int), ("history_chars", int), ("max_tool_steps", int),
            ("tool_result_chars", int), ("guide_chars", int), ("max_streams", int),
            ("rate_limit_per_minute", int), ("retention_days", int),
        ):
            _override(raw, "chat", field, f"CHAT_{field.upper()}", cast=cast)

        # JIRA settings overrides from .env
        raw.setdefault("jira", {})
        _override(raw, "jira", "base_url", "JIRA_BASE_URL")
        _override(raw, "jira", "email", "JIRA_EMAIL")
        _override(raw, "jira", "api_token", "JIRA_API_TOKEN")
        _override(raw, "jira", "project_key", "JIRA_PROJECT_KEY")
        _override(raw, "jira", "default_issue_type", "JIRA_DEFAULT_ISSUE_TYPE")
        _override(raw, "jira", "verify_ssl", "JIRA_VERIFY_SSL", cast=_bool)
        
        env_resolved = _env("JIRA_RESOLVED_STATUSES")
        if env_resolved:
            if env_resolved.strip().startswith("["):
                import json
                try:
                    raw["jira"]["resolved_statuses"] = json.loads(env_resolved)
                except Exception:
                    raw["jira"]["resolved_statuses"] = [s.strip() for s in env_resolved.split(",")]
            else:
                raw["jira"]["resolved_statuses"] = [s.strip() for s in env_resolved.split(",")]

        # Webhook secrets (spec 0009): .env only, so a YAML value is dropped.
        raw["webhooks"] = {
            "workspace_secret": (_env("WORKSPACE_WEBHOOK_SECRET") or "").strip(),
            "jira_secret": (_env("JIRA_WEBHOOK_SECRET") or "").strip(),
        }

        return cls(**raw)



def _env(key: str) -> str | None:
    import os
    return os.getenv(key)


def _override(raw: dict, section: str, field: str, env_key: str, cast=str) -> None:
    value = _env(env_key)
    if value is not None:
        raw.setdefault(section, {})[field] = cast(value)


def _csv_floats(value: str) -> list[float]:
    return [float(item) for item in value.split(",") if item.strip()]


def _csv_lower(value: str) -> list[str]:
    return [item.strip().lower().lstrip(".") for item in value.split(",") if item.strip()]


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_set(key: str) -> str | None:
    """Env value with blank treated as unset (spec 0003, AC-9)."""
    value = _env(key)
    if value is None or not value.strip():
        return None
    return value.strip()


def _parse_models(value: str | None) -> list[str]:
    """Comma separated list: trim, drop blanks, drop duplicates keeping the first."""
    models: list[str] = []
    for item in (value or "").split(","):
        item = item.strip()
        if item and item not in models:
            models.append(item)
    return models


def _build_llm_roles(
    llm: LLMConfig, chat: ChatConfig | None = None, rag: RAGConfig | None = None
) -> dict[str, LLMRoleConfig]:
    roles: dict[str, LLMRoleConfig] = {}
    for role in ("generation", "analysis"):
        prefix = f"{role.upper()}_LLM_"
        provider = _env_set(prefix + "PROVIDER") or llm.provider
        if role == "generation":
            models = _parse_models(_env_set(prefix + "MODELS"))
        else:
            single = _env_set(prefix + "MODEL")
            models = [single] if single else []
        # An api_base belongs to one provider's endpoint (e.g. NVIDIA's), so a
        # role only inherits LLM_API_BASE when it uses the same provider.
        # Otherwise a Gemini role would be sent to the NVIDIA host.
        same_provider = provider == llm.provider
        fields = {
            "provider": provider,
            "models": models or [llm.model],
            "api_key": _env_set(prefix + "API_KEY") or llm.api_key,
            "api_base": _env_set(prefix + "API_BASE") or (llm.api_base if same_provider else None),
        }
        limits = ["MAX_CONCURRENT", "COOLDOWN_SECONDS", "MAX_WAIT_SECONDS"]
        if role == "generation":
            limits.append("MIN_INTERVAL_SECONDS")
        for name in limits:
            value = _env_set(prefix + name)
            if value is not None:
                fields[name.lower()] = value
        roles[role] = LLMRoleConfig(**fields)
    chat = chat or ChatConfig()
    rag = rag or RAGConfig()
    # Spec 0005, AC-24 (chat) and spec 0006, AC-22 (retrieval).
    roles["chat"] = _build_analysis_fallback_role(
        "CHAT_LLM_", roles["analysis"],
        chat.llm_max_concurrent, chat.llm_cooldown_seconds, chat.llm_max_wait_seconds,
    )
    roles["retrieval"] = _build_analysis_fallback_role(
        "RETRIEVAL_LLM_", roles["analysis"],
        rag.retrieval_llm_max_concurrent, rag.retrieval_llm_cooldown_seconds, rag.retrieval_llm_max_wait_seconds,
    )
    return roles


def _build_analysis_fallback_role(
    prefix: str, analysis: LLMRoleConfig, max_concurrent: int, cooldown_seconds: float, max_wait_seconds: float
) -> LLMRoleConfig:
    """A role whose every unset <PREFIX>* value falls back to the resolved
    analysis role (which already fell back to LLM_*). Pool limits have their
    own defaults so a busy role fails fast instead of queueing behind analysis."""
    provider = _env_set(prefix + "PROVIDER") or analysis.provider
    model = _env_set(prefix + "MODEL")
    same_provider = provider == analysis.provider
    fields = {
        "provider": provider,
        "models": [model] if model else [analysis.models[0]],
        "api_key": _env_set(prefix + "API_KEY") or analysis.api_key,
        "api_base": _env_set(prefix + "API_BASE") or (analysis.api_base if same_provider else None),
        "max_concurrent": max_concurrent,
        "cooldown_seconds": cooldown_seconds,
        "max_wait_seconds": max_wait_seconds,
    }
    for name in ("MAX_CONCURRENT", "COOLDOWN_SECONDS", "MAX_WAIT_SECONDS"):
        value = _env_set(prefix + name)
        if value is not None:
            fields[name.lower()] = value
    return LLMRoleConfig(**fields)


@lru_cache
def get_settings() -> Settings:
    return Settings.from_yaml()