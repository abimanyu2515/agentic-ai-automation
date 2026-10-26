"""
Tests for backend/services/llm.py
"""

import pytest
from unittest.mock import Mock, patch

from backend.services.llm import LLMService, LLMServiceError, DEFAULT_LLM_TIMEOUT
from backend.config.settings import LLMConfig


class MockCompletionResponse:
    """Mock litellm completion response."""
    def __init__(self, content):
        self.choices = [Mock(message=Mock(content=content))]


def test_llm_service_requires_valid_config_for_cloud_provider():
    """Cloud providers require API key."""
    config = LLMConfig(provider="openai", model="gpt-4o", api_key=None)
    service = LLMService(config=config)
    with pytest.raises(LLMServiceError, match="missing API key"):
        service._require_valid_config()


def test_llm_service_requires_api_base_for_ollama():
    """Ollama requires api_base."""
    config = LLMConfig(provider="ollama", model="llama3", api_base=None)
    service = LLMService(config=config)
    with pytest.raises(LLMServiceError, match="missing api_base"):
        service._require_valid_config()


def test_llm_service_generate_uses_config_timeout():
    """generate() uses config.timeout when no timeout passed."""
    config = LLMConfig(provider="openai", model="gpt-4o", api_key="test-key", timeout=45)
    service = LLMService(config=config)

    with patch("backend.services.llm.completion") as mock_completion:
        mock_completion.return_value = MockCompletionResponse("test response")
        service.generate(system="sys", user="user")

        # Check timeout was passed to completion
        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs["timeout"] == 45


def test_llm_service_generate_overrides_timeout():
    """generate() allows per-call timeout override."""
    config = LLMConfig(provider="openai", model="gpt-4o", api_key="test-key", timeout=60)
    service = LLMService(config=config)

    with patch("backend.services.llm.completion") as mock_completion:
        mock_completion.return_value = MockCompletionResponse("test response")
        service.generate(system="sys", user="user", timeout=120)

        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs["timeout"] == 120


def test_llm_service_structured_generate_uses_config_timeout():
    """structured_generate() uses config.timeout when no timeout passed."""
    from pydantic import BaseModel

    class ResponseModel(BaseModel):
        value: str

    config = LLMConfig(provider="openai", model="gpt-4o", api_key="test-key", timeout=45)
    service = LLMService(config=config)

    with patch("backend.services.llm.completion") as mock_completion:
        mock_completion.return_value = MockCompletionResponse('{"value": "test"}')
        service.structured_generate(user="user", response_model=ResponseModel)

        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs["timeout"] == 45


def test_llm_service_structured_generate_overrides_timeout():
    """structured_generate() allows per-call timeout override."""
    from pydantic import BaseModel

    class ResponseModel(BaseModel):
        value: str

    config = LLMConfig(provider="openai", model="gpt-4o", api_key="test-key", timeout=60)
    service = LLMService(config=config)

    with patch("backend.services.llm.completion") as mock_completion:
        mock_completion.return_value = MockCompletionResponse('{"value": "test"}')
        service.structured_generate(user="user", response_model=ResponseModel, timeout=120)

        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs["timeout"] == 120


def test_llm_service_generate_wraps_exception():
    """generate() wraps exceptions in LLMServiceError."""
    config = LLMConfig(provider="openai", model="gpt-4o", api_key="test-key")
    service = LLMService(config=config)

    with patch("backend.services.llm.completion", side_effect=Exception("network error")):
        with pytest.raises(LLMServiceError, match="LLM call failed"):
            service.generate(system="sys", user="user")


def test_llm_service_structured_generate_retries_on_failure():
    """structured_generate() retries on validation failure."""
    from pydantic import BaseModel

    class ResponseModel(BaseModel):
        value: str

    config = LLMConfig(provider="openai", model="gpt-4o", api_key="test-key")
    service = LLMService(config=config)

    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MockCompletionResponse("not json")
        return MockCompletionResponse('{"value": "success"}')

    with patch("backend.services.llm.completion", side_effect=side_effect):
        result = service.structured_generate(user="user", response_model=ResponseModel, max_retries=3)
        assert result.value == "success"
        assert call_count == 2


def test_llm_service_structured_generate_fails_after_max_retries():
    """structured_generate() fails after max_retries exhausted."""
    from pydantic import BaseModel

    class ResponseModel(BaseModel):
        value: str

    config = LLMConfig(provider="openai", model="gpt-4o", api_key="test-key")
    service = LLMService(config=config)

    with patch("backend.services.llm.completion", return_value=MockCompletionResponse("not json")):
        with pytest.raises(LLMServiceError, match="did not match ResponseModel after 3 attempts"):
            service.structured_generate(user="user", response_model=ResponseModel, max_retries=3)


def test_default_llm_timeout_constant():
    assert DEFAULT_LLM_TIMEOUT == 60


def test_model_string_uses_provider_prefix():
    """_model_string() adds provider prefix for non-OpenAI providers."""
    config = LLMConfig(provider="ollama", model="llama3")
    service = LLMService(config=config)
    assert service._model_string() == "ollama/llama3"

    config = LLMConfig(provider="groq", model="mixtral")
    service = LLMService(config=config)
    assert service._model_string() == "groq/mixtral"

    config = LLMConfig(provider="openai", model="gpt-4o")
    service = LLMService(config=config)
    assert service._model_string() == "gpt-4o"


def test_json_mode_kwargs_for_ollama():
    """_json_mode_kwargs() returns correct format for Ollama."""
    config = LLMConfig(provider="ollama", model="llama3")
    service = LLMService(config=config)
    assert service._json_mode_kwargs() == {"format": "json"}


def test_json_mode_kwargs_default():
    """_json_mode_kwargs() returns OpenAI-style for unknown providers."""
    config = LLMConfig(provider="unknown", model="model")
    service = LLMService(config=config)
    assert service._json_mode_kwargs() == {"response_format": {"type": "json_object"}}