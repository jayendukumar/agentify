import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc")
    monkeypatch.delenv("LLM_MODEL", raising=False)

    settings = Settings()

    assert settings.openrouter_api_key == "sk-or-v1-abc"
    assert settings.llm_model == "qwen/qwen3.7-flash"
    assert settings.llm_base_url == "https://openrouter.ai/api/v1"
    assert settings.llm_max_retries >= 1


def test_settings_missing_api_key_fails_fast(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
