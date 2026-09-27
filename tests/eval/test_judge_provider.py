from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from glassbox.eval.judge_config import JudgeConfig
from glassbox.eval.judge_provider import (
    JudgeParseResult,
    create_judge_provider,
    parse_judge_response,
)

_RAW_JUDGE_JSON = json.dumps({"score": 4, "rationale": "Well grounded in the cited evidence."})


def _claude_config() -> JudgeConfig:
    return JudgeConfig(
        provider="claude",
        model="claude-3-5-sonnet",
        credential="claude-secret",
        base_url="https://api.anthropic.com",
        is_remote=True,
    )


def _openai_config() -> JudgeConfig:
    return JudgeConfig(
        provider="openai",
        model="gpt-4o",
        credential="openai-secret",
        base_url="https://api.openai.com/v1",
        is_remote=True,
    )


def _gemini_config() -> JudgeConfig:
    return JudgeConfig(
        provider="gemini",
        model="gemini-1.5-pro",
        credential="gemini-secret",
        base_url="https://generativelanguage.googleapis.com",
        is_remote=True,
    )


def _ollama_config(base_url: str = "http://localhost:11434") -> JudgeConfig:
    return JudgeConfig(
        provider="ollama",
        model="llama3",
        credential=None,
        base_url=base_url,
        is_remote=False,
    )


def _assert_optional_sdks_untouched(*, skip: str | None = None) -> None:
    for module_name in ("anthropic", "openai", "google.genai"):
        if module_name == skip:
            continue
        assert module_name not in sys.modules, f"{module_name} was imported eagerly"


def test_create_judge_provider_for_claude_builds_client_with_credential_and_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeMessages:
        def create(self, **kwargs: Any) -> Any:
            captured["create_kwargs"] = kwargs
            return types.SimpleNamespace(content=[types.SimpleNamespace(text=_RAW_JUDGE_JSON)])

    class _FakeAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            captured["init_kwargs"] = kwargs
            self.messages = _FakeMessages()

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = _FakeAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    provider = create_judge_provider(_claude_config())
    raw = provider.judge("system prompt", "user prompt")

    assert captured["init_kwargs"] == {
        "api_key": "claude-secret",
        "base_url": "https://api.anthropic.com",
    }
    assert raw == _RAW_JUDGE_JSON
    _assert_optional_sdks_untouched(skip="anthropic")


def test_create_judge_provider_for_openai_builds_client_with_credential_and_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeCompletions:
        def create(self, **kwargs: Any) -> Any:
            captured["create_kwargs"] = kwargs
            message = types.SimpleNamespace(content=_RAW_JUDGE_JSON)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    class _FakeChat:
        def __init__(self) -> None:
            self.completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured["init_kwargs"] = kwargs
            self.chat = _FakeChat()

    fake_module = types.ModuleType("openai")
    fake_module.OpenAI = _FakeOpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    provider = create_judge_provider(_openai_config())
    raw = provider.judge("system prompt", "user prompt")

    assert captured["init_kwargs"] == {
        "api_key": "openai-secret",
        "base_url": "https://api.openai.com/v1",
    }
    assert raw == _RAW_JUDGE_JSON
    _assert_optional_sdks_untouched(skip="openai")


def test_create_judge_provider_for_gemini_builds_client_with_credential_and_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeModels:
        def generate_content(self, **kwargs: Any) -> Any:
            captured["generate_kwargs"] = kwargs
            return types.SimpleNamespace(text=_RAW_JUDGE_JSON)

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["init_kwargs"] = kwargs
            self.models = _FakeModels()

    fake_genai_module = types.ModuleType("google.genai")
    fake_genai_module.Client = _FakeClient  # type: ignore[attr-defined]
    fake_google_module = types.ModuleType("google")
    fake_google_module.genai = fake_genai_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", fake_google_module)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai_module)

    provider = create_judge_provider(_gemini_config())
    raw = provider.judge("system prompt", "user prompt")

    assert captured["init_kwargs"] == {
        "api_key": "gemini-secret",
        "http_options": {"base_url": "https://generativelanguage.googleapis.com"},
    }
    assert raw == _RAW_JUDGE_JSON
    _assert_optional_sdks_untouched(skip="google.genai")


def test_create_judge_provider_for_ollama_uses_urllib_and_needs_no_optional_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeResponse:
        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"response": _RAW_JUDGE_JSON}).encode("utf-8")

    def _fake_urlopen(request: Any, timeout: float | None = None) -> _FakeResponse:
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["data"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    provider = create_judge_provider(_ollama_config())
    raw = provider.judge("system prompt", "user prompt")

    assert raw == _RAW_JUDGE_JSON
    assert captured["method"] == "POST"
    assert captured["url"].startswith("http://localhost:11434")
    assert captured["data"]["model"] == "llama3"
    _assert_optional_sdks_untouched()


def test_selecting_ollama_never_imports_any_optional_provider_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeResponse:
        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"response": _RAW_JUDGE_JSON}).encode("utf-8")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

    create_judge_provider(_ollama_config())

    _assert_optional_sdks_untouched()


def test_create_judge_provider_rejects_unsupported_provider() -> None:
    bad_config = JudgeConfig(
        provider="bedrock",
        model="m",
        credential="k",
        base_url="https://example.com",
        is_remote=True,
    )

    with pytest.raises(ValueError, match="bedrock"):
        create_judge_provider(bad_config)


def test_parse_judge_response_accepts_strict_json_with_valid_score_and_rationale() -> None:
    result = parse_judge_response(_RAW_JUDGE_JSON)

    assert result == JudgeParseResult(
        score=4, rationale="Well grounded in the cited evidence.", error=None
    )


def test_parse_judge_response_rejects_invalid_json() -> None:
    result = parse_judge_response("not json at all")

    assert result.score is None
    assert result.rationale is None
    assert result.error is not None


def test_parse_judge_response_rejects_a_non_object_top_level_value() -> None:
    result = parse_judge_response(json.dumps([1, 2, 3]))

    assert result.score is None
    assert result.error is not None


@pytest.mark.parametrize("bad_score", [3.5, "4", None, True, False])
def test_parse_judge_response_rejects_non_integer_scores(bad_score: Any) -> None:
    raw = json.dumps({"score": bad_score, "rationale": "some reason"})

    result = parse_judge_response(raw)

    assert result.score is None
    assert result.error is not None


@pytest.mark.parametrize("bad_score", [0, 6, -1, 100])
def test_parse_judge_response_rejects_scores_outside_one_to_five(bad_score: int) -> None:
    raw = json.dumps({"score": bad_score, "rationale": "some reason"})

    result = parse_judge_response(raw)

    assert result.score is None
    assert result.error is not None


@pytest.mark.parametrize("bad_rationale", ["", "   ", None, 42])
def test_parse_judge_response_rejects_missing_or_empty_rationale(bad_rationale: Any) -> None:
    raw = json.dumps({"score": 3, "rationale": bad_rationale})

    result = parse_judge_response(raw)

    assert result.rationale is None
    assert result.error is not None


def test_parse_judge_response_rejects_non_finite_json_constants() -> None:
    raw = '{"score": NaN, "rationale": "some reason"}'

    result = parse_judge_response(raw)

    assert result.score is None
    assert result.error is not None


def test_parse_judge_response_rejects_missing_score_field() -> None:
    raw = json.dumps({"rationale": "some reason"})

    result = parse_judge_response(raw)

    assert result.score is None
    assert result.error is not None
