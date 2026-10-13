from __future__ import annotations

import os
from pathlib import Path

import pytest

from glassbox.eval.counterfactual_config import (
    CounterfactualConfigError,
    load_counterfactual_config,
)


def test_root_dotenv_and_process_precedence_without_environment_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "glassbox"
    root.mkdir()
    (root / ".env").write_text(
        "GLASSBOX_COUNTERFACTUAL_PROVIDER=claude\n"
        "GLASSBOX_COUNTERFACTUAL_MODEL=file-model\nANTHROPIC_API_KEY=file-key\n"
    )
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / ".env").write_text("GLASSBOX_COUNTERFACTUAL_PROVIDER=wrong\n")
    monkeypatch.chdir(agent)
    before = dict(os.environ)
    environ = {"GLASSBOX_COUNTERFACTUAL_MODEL": "process-model", "ANTHROPIC_API_KEY": "key"}
    config = load_counterfactual_config(root, environ)
    assert config.provider == "claude"
    assert config.model == "process-model"
    assert config.credential == "key"
    assert os.environ == before
    assert environ == {
        "GLASSBOX_COUNTERFACTUAL_MODEL": "process-model", "ANTHROPIC_API_KEY": "key",
    }


@pytest.mark.parametrize("provider,key,url", [
    ("claude", "ANTHROPIC_API_KEY", "https://api.anthropic.com"),
    ("openai", "OPENAI_API_KEY", "https://api.openai.com/v1"),
    ("gemini", "GEMINI_API_KEY", "https://generativelanguage.googleapis.com"),
])
def test_reads_only_selected_credentials_and_fixed_destination(
    tmp_path: Path, provider: str, key: str, url: str,
) -> None:
    class ScopedEnvironment(dict[str, str]):
        def get(self, name: str, default: str | None = None) -> str | None:
            assert name in {
                "GLASSBOX_COUNTERFACTUAL_PROVIDER", "GLASSBOX_COUNTERFACTUAL_MODEL", key,
            }
            return super().get(name, default)

    config = load_counterfactual_config(tmp_path, ScopedEnvironment({
        "GLASSBOX_COUNTERFACTUAL_PROVIDER": provider,
        "GLASSBOX_COUNTERFACTUAL_MODEL": "m", key: "secret",
        "OPENAI_BASE_URL": "https://other.invalid",
    }))
    assert config.base_url == url
    assert config.is_remote


@pytest.mark.parametrize("values", [
    {}, {"GLASSBOX_COUNTERFACTUAL_PROVIDER": "openai"},
    {"GLASSBOX_COUNTERFACTUAL_PROVIDER": "openai", "GLASSBOX_COUNTERFACTUAL_MODEL": "m"},
    {"GLASSBOX_COUNTERFACTUAL_PROVIDER": "unknown", "GLASSBOX_COUNTERFACTUAL_MODEL": "m"},
    {"GLASSBOX_JUDGE_PROVIDER": "openai", "GLASSBOX_JUDGE_MODEL": "m", "OPENAI_API_KEY": "k"},
    {"GLASSBOX_COUNTERFACTUAL_PROVIDER": "  ", "GLASSBOX_COUNTERFACTUAL_MODEL": "m"},
])
def test_incomplete_config_rejected(tmp_path: Path, values: dict[str, str]) -> None:
    with pytest.raises(CounterfactualConfigError):
        load_counterfactual_config(tmp_path, values)


@pytest.mark.parametrize("url,remote", [
    ("http://127.0.0.1:11434", False), ("http://[::1]:11434", False),
    ("http://localhost:11434", False), ("http://192.168.1.2:11434", True),
    ("https://ollama.example", True), ("http://localhost.example", True),
])
def test_ollama_destination_classification(tmp_path: Path, url: str, remote: bool) -> None:
    config = load_counterfactual_config(tmp_path, {
        "GLASSBOX_COUNTERFACTUAL_PROVIDER": "ollama", "GLASSBOX_COUNTERFACTUAL_MODEL": "m",
        "GLASSBOX_COUNTERFACTUAL_OLLAMA_URL": url,
    })
    assert config.base_url == url
    assert config.is_remote is remote
    assert config.credential is None


def test_ollama_defaults_to_explicit_loopback(tmp_path: Path) -> None:
    config = load_counterfactual_config(tmp_path, {
        "GLASSBOX_COUNTERFACTUAL_PROVIDER": "ollama", "GLASSBOX_COUNTERFACTUAL_MODEL": "m",
    })
    assert config.base_url == "http://127.0.0.1:11434"
    assert config.is_remote is False


@pytest.mark.parametrize("url", ["no-url", "file://localhost/x", "http://u:p@localhost", 
                                  "http://localhost:bad", "http://localhost#fragment"])
def test_invalid_ollama_url_rejected(tmp_path: Path, url: str) -> None:
    with pytest.raises(CounterfactualConfigError):
        load_counterfactual_config(tmp_path, {
            "GLASSBOX_COUNTERFACTUAL_PROVIDER": "ollama", "GLASSBOX_COUNTERFACTUAL_MODEL": "m",
            "GLASSBOX_COUNTERFACTUAL_OLLAMA_URL": url,
        })
