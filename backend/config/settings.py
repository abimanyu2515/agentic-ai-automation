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
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).parent
DEFAULT_CONFIG_PATH = CONFIG_DIR / "default_config.yaml"

load_dotenv()


class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    api_key: str | None = None
    api_base: str | None = None  # required for ollama (e.g. http://localhost:11434); leave unset for cloud providers


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
    ragflow_api_key: str | None = None
    ragflow_dataset_id: str | None = None


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


class JiraConfig(BaseModel):
    base_url: str = "https://your-domain.atlassian.net"
    email: str = ""
    api_token: str = ""
    project_key: str = ""
    default_issue_type: str = "Story"
    verify_ssl: bool = True
    resolved_statuses: list[str] = ["Done", "Closed", "Ready for Testing", "Resolved"]


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
        _override(raw, "rag", "ragflow_dataset_id", "RAGFLOW_DATASET_ID")

        # Repository settings overrides from .env
        raw.setdefault("repository", {})
        _override(raw, "repository", "provider", "REPOSITORY_PROVIDER")
        _override(raw, "repository", "database_url", "DATABASE_URL")
        _override(raw, "repository", "pool_size", "DB_POOL_SIZE", cast=int)
        _override(raw, "repository", "max_overflow", "DB_MAX_OVERFLOW", cast=int)
        _override(raw, "repository", "pool_timeout", "DB_POOL_TIMEOUT", cast=int)

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

        return cls(**raw)



def _env(key: str) -> str | None:
    import os
    return os.getenv(key)


def _override(raw: dict, section: str, field: str, env_key: str, cast=str) -> None:
    value = _env(env_key)
    if value is not None:
        raw.setdefault(section, {})[field] = cast(value)


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache
def get_settings() -> Settings:
    return Settings.from_yaml()