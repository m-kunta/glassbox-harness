from __future__ import annotations

import os
from pathlib import Path

import pytest

from glassbox.eval.judge_config import JudgeConfig, JudgeConfigError, load_judge_config


def _write_env(root: Path, contents: str) -> None:
    (root / ".env").write_text(contents)


def test_load_judge_config_reads_only_the_project_root_env(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    agent_root = tmp_path / "agent"
    agent_root.mkdir()

    _write_env(
        project_root,
        "GLASSBOX_JUDGE_PROVIDER=claude\n"
        "GLASSBOX_JUDGE_MODEL=claude-3-5-sonnet\n"
        "ANTHROPIC_API_KEY=project-key\n",
    )
    _write_env(
        agent_root,
        "GLASSBOX_JUDGE_PROVIDER=openai\n"
        "GLASSBOX_JUDGE_MODEL=gpt-4o\n"
        "OPENAI_API_KEY=agent-key\n",
    )

    config = load_judge_config(project_root, {})

    assert config == JudgeConfig(
        provider="claude",
        model="claude-3-5-sonnet",
        credential="project-key",
        base_url="https://api.anthropic.com",
        is_remote=True,
    )


def test_environ_overrides_dotenv_values(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "GLASSBOX_JUDGE_PROVIDER=claude\n"
        "GLASSBOX_JUDGE_MODEL=claude-3-5-sonnet\n"
        "ANTHROPIC_API_KEY=dotenv-key\n",
    )

    config = load_judge_config(tmp_path, {"ANTHROPIC_API_KEY": "environ-key"})

    assert config.credential == "environ-key"


def test_empty_process_value_does_not_override_dotenv_value(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "GLASSBOX_JUDGE_PROVIDER=claude\n"
        "GLASSBOX_JUDGE_MODEL=claude-3-5-sonnet\n"
        "ANTHROPIC_API_KEY=dotenv-key\n",
    )

    config = load_judge_config(tmp_path, {"ANTHROPIC_API_KEY": ""})

    assert config.credential == "dotenv-key"


def test_load_judge_config_does_not_mutate_os_environ(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "GLASSBOX_JUDGE_PROVIDER=claude\n"
        "GLASSBOX_JUDGE_MODEL=claude-3-5-sonnet\n"
        "ANTHROPIC_API_KEY=dotenv-key\n",
    )
    before = dict(os.environ)

    load_judge_config(tmp_path, dict(os.environ))

    assert dict(os.environ) == before
    assert "GLASSBOX_JUDGE_PROVIDER" not in os.environ
    assert "ANTHROPIC_API_KEY" not in os.environ or os.environ.get("ANTHROPIC_API_KEY") != (
        "dotenv-key"
    )


def test_load_judge_config_does_not_mutate_the_passed_in_environ_mapping(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "GLASSBOX_JUDGE_PROVIDER=claude\nGLASSBOX_JUDGE_MODEL=m\nANTHROPIC_API_KEY=k\n",
    )
    environ = {"UNRELATED": "value"}

    load_judge_config(tmp_path, environ)

    assert environ == {"UNRELATED": "value"}


def test_missing_provider_raises(tmp_path: Path) -> None:
    _write_env(tmp_path, "GLASSBOX_JUDGE_MODEL=m\n")

    with pytest.raises(JudgeConfigError, match="GLASSBOX_JUDGE_PROVIDER"):
        load_judge_config(tmp_path, {})


def test_missing_model_raises(tmp_path: Path) -> None:
    _write_env(tmp_path, "GLASSBOX_JUDGE_PROVIDER=claude\nANTHROPIC_API_KEY=k\n")

    with pytest.raises(JudgeConfigError, match="GLASSBOX_JUDGE_MODEL"):
        load_judge_config(tmp_path, {})


def test_missing_credential_raises(tmp_path: Path) -> None:
    _write_env(tmp_path, "GLASSBOX_JUDGE_PROVIDER=claude\nGLASSBOX_JUDGE_MODEL=m\n")

    with pytest.raises(JudgeConfigError, match="ANTHROPIC_API_KEY"):
        load_judge_config(tmp_path, {})


def test_unsupported_provider_raises(tmp_path: Path) -> None:
    _write_env(tmp_path, "GLASSBOX_JUDGE_PROVIDER=bedrock\nGLASSBOX_JUDGE_MODEL=m\n")

    with pytest.raises(JudgeConfigError, match="bedrock"):
        load_judge_config(tmp_path, {})


def test_claude_config_uses_fixed_https_base_url(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "GLASSBOX_JUDGE_PROVIDER=claude\nGLASSBOX_JUDGE_MODEL=claude-3-5-sonnet\n"
        "ANTHROPIC_API_KEY=k\n",
    )

    config = load_judge_config(tmp_path, {})

    assert config.base_url == "https://api.anthropic.com"
    assert config.is_remote is True


def test_openai_config_uses_fixed_https_base_url(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "GLASSBOX_JUDGE_PROVIDER=openai\nGLASSBOX_JUDGE_MODEL=gpt-4o\nOPENAI_API_KEY=k\n",
    )

    config = load_judge_config(tmp_path, {})

    assert config.base_url == "https://api.openai.com/v1"
    assert config.is_remote is True


def test_gemini_config_uses_fixed_https_base_url(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "GLASSBOX_JUDGE_PROVIDER=gemini\nGLASSBOX_JUDGE_MODEL=gemini-1.5-pro\n"
        "GEMINI_API_KEY=k\n",
    )

    config = load_judge_config(tmp_path, {})

    assert config.base_url == "https://generativelanguage.googleapis.com"
    assert config.is_remote is True


def test_ollama_missing_url_raises(tmp_path: Path) -> None:
    _write_env(tmp_path, "GLASSBOX_JUDGE_PROVIDER=ollama\nGLASSBOX_JUDGE_MODEL=llama3\n")

    with pytest.raises(JudgeConfigError, match="GLASSBOX_JUDGE_OLLAMA_URL"):
        load_judge_config(tmp_path, {})


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://[::1]:11434",
    ],
)
def test_ollama_loopback_url_is_not_remote(tmp_path: Path, url: str) -> None:
    _write_env(
        tmp_path,
        f"GLASSBOX_JUDGE_PROVIDER=ollama\nGLASSBOX_JUDGE_MODEL=llama3\n"
        f"GLASSBOX_JUDGE_OLLAMA_URL={url}\n",
    )

    config = load_judge_config(tmp_path, {})

    assert config.base_url == url
    assert config.is_remote is False
    assert config.credential is None


@pytest.mark.parametrize(
    "url",
    [
        "http://ollama.internal.example.com:11434",
        "http://203.0.113.5:11434",
    ],
)
def test_ollama_non_loopback_url_is_remote(tmp_path: Path, url: str) -> None:
    _write_env(
        tmp_path,
        f"GLASSBOX_JUDGE_PROVIDER=ollama\nGLASSBOX_JUDGE_MODEL=llama3\n"
        f"GLASSBOX_JUDGE_OLLAMA_URL={url}\n",
    )

    config = load_judge_config(tmp_path, {})

    assert config.base_url == url
    assert config.is_remote is True


def test_ollama_provider_does_not_require_a_credential_env_var(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "GLASSBOX_JUDGE_PROVIDER=ollama\nGLASSBOX_JUDGE_MODEL=llama3\n"
        "GLASSBOX_JUDGE_OLLAMA_URL=http://localhost:11434\n",
    )

    config = load_judge_config(tmp_path, {})

    assert config.credential is None


def test_selected_provider_credential_is_scoped_to_that_provider_only(tmp_path: Path) -> None:
    """Setting every provider's credential must not leak the wrong one in."""
    _write_env(
        tmp_path,
        "GLASSBOX_JUDGE_PROVIDER=claude\nGLASSBOX_JUDGE_MODEL=claude-3-5-sonnet\n",
    )
    environ = {
        "ANTHROPIC_API_KEY": "claude-key",
        "OPENAI_API_KEY": "openai-key",
        "GEMINI_API_KEY": "gemini-key",
    }

    config = load_judge_config(tmp_path, environ)

    assert config.credential == "claude-key"


def test_missing_project_root_env_file_is_treated_as_empty(tmp_path: Path) -> None:
    with pytest.raises(JudgeConfigError, match="GLASSBOX_JUDGE_PROVIDER"):
        load_judge_config(tmp_path, {})
