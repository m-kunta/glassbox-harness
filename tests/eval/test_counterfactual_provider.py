from __future__ import annotations

import subprocess
import sys
import threading
import time
import types
from typing import Any
from urllib.error import URLError

import pytest

from glassbox.eval.counterfactual_config import CounterfactualConfig
from glassbox.eval.counterfactual_provider import create_counterfactual_provider
from glassbox.eval.judge_config import JudgeConfig

CONFIG = CounterfactualConfig("openai", "m", "k", "https://api.openai.com/v1", True)


def test_ollama_timeout_contains_incremental_response_and_prevents_overlapping_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from glassbox.eval.counterfactual_provider import CounterfactualProviderTimeout

    release_stream = threading.Event()
    stream_closed = threading.Event()
    request_started = threading.Event()
    requests: list[object] = []
    chunks: list[bytes] = []
    workers: list[threading.Thread] = []
    thread_factory = threading.Thread

    def create_thread(*args, **kwargs):
        worker = thread_factory(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(threading, "Thread", create_thread)

    class IncrementalResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            stream_closed.set()

        def read(self):
            # Every chunk arrives within the socket timeout. The complete body
            # still takes much longer than the caller's overall deadline.
            for chunk in b'{"response":"incremental answer"}':
                release_stream.wait(0.01)
                chunks.append(bytes([chunk]))
            return b"".join(chunks)

    def urlopen(request, *, timeout):
        requests.append(request)
        request_started.set()
        return IncrementalResponse()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    config = CounterfactualConfig("ollama", "model", None, "http://localhost:11434", False)
    provider = create_counterfactual_provider(config)
    started_at = time.monotonic()
    try:
        with pytest.raises(CounterfactualProviderTimeout):
            provider.complete("system", "user", timeout_seconds=0.04)
        assert time.monotonic() - started_at < 0.2
        assert request_started.is_set()
        assert 0 < len(chunks) < len(b'{"response":"incremental answer"}')
        assert not stream_closed.is_set()

        # A new provider instance must share containment, and must not launch
        # another call while the timed-out request is still completing.
        with pytest.raises(CounterfactualProviderTimeout):
            create_counterfactual_provider(config).complete(
                "system", "user", timeout_seconds=0.02,
            )

        class SwitchedRemoteTransport:
            def judge(self, *args, **kwargs):
                pytest.fail("a remote provider must not overlap the stranded Ollama request")

        with pytest.raises(CounterfactualProviderTimeout):
            create_counterfactual_provider(
                CONFIG, transport_factory=lambda _: SwitchedRemoteTransport(),
            ).complete("system", "user", timeout_seconds=0.02)
        assert len(requests) == 1
        assert len(workers) == 1
    finally:
        release_stream.set()
        assert stream_closed.wait(1.0)
        for worker in workers:
            worker.join(timeout=1.0)
            assert not worker.is_alive()


@pytest.mark.parametrize("original_provider", ["claude", "openai", "gemini", "ollama"])
@pytest.mark.parametrize("next_provider", ["claude", "openai", "gemini", "ollama"])
def test_finite_timeout_contains_remote_completion_across_provider_switches_and_recovers(
    monkeypatch: pytest.MonkeyPatch, original_provider: str, next_provider: str,
) -> None:
    from glassbox.eval.counterfactual_provider import CounterfactualProviderTimeout

    release_stream = threading.Event()
    completed = threading.Event()
    chunks: list[int] = []
    workers: list[threading.Thread] = []
    replacement_calls: list[int] = []
    thread_factory = threading.Thread

    def create_thread(*args, **kwargs):
        worker = thread_factory(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(threading, "Thread", create_thread)

    class IncrementalRemoteTransport:
        def judge(self, system_prompt, user_prompt, *, timeout_seconds=None):
            assert timeout_seconds == 0.04
            try:
                for index in range(30):
                    release_stream.wait(0.01)
                    chunks.append(index)
                return "late remote result"
            finally:
                completed.set()

    class ReplacementTransport:
        def judge(self, system_prompt, user_prompt, *, timeout_seconds=None):
            replacement_calls.append(threading.get_ident())
            return "replacement result"

    original_config = CounterfactualConfig(
        original_provider, "original", "credential", "https://original.example", True,
    )
    next_config = CounterfactualConfig(
        next_provider, "replacement", "credential", "https://replacement.example", True,
    )
    original = create_counterfactual_provider(
        original_config, transport_factory=lambda _: IncrementalRemoteTransport(),
    )
    replacement = create_counterfactual_provider(
        next_config, transport_factory=lambda _: ReplacementTransport(),
    )
    started_at = time.monotonic()
    try:
        with pytest.raises(CounterfactualProviderTimeout):
            original.complete("system", "user", timeout_seconds=0.04)
        assert time.monotonic() - started_at < 0.2
        assert 0 < len(chunks) < 30
        assert not completed.is_set()
        with pytest.raises(CounterfactualProviderTimeout):
            replacement.complete("system", "user", timeout_seconds=0.02)
        assert replacement_calls == []
        assert len(workers) == 1

        # Unbounded offline calls still run synchronously, without admission.
        assert replacement.complete("system", "user") == "replacement result"
        assert replacement_calls == [threading.get_ident()]
        assert len(workers) == 1
    finally:
        release_stream.set()
        assert completed.wait(1.0)
        for worker in workers:
            worker.join(timeout=1.0)
            assert not worker.is_alive()

    # Finishing the stranded operation restores admission for a new provider.
    assert replacement.complete("system", "user", timeout_seconds=1.0) == "replacement result"
    assert len(workers) == 2
    assert len(replacement_calls) == 2


def test_counterfactual_provider_forwards_request_timeout() -> None:
    class RecordingJudgeTransport:
        timeout_seconds: float | None = None

        def judge(self, system_prompt: str, user_prompt: str, *, timeout_seconds=None) -> str:
            self.timeout_seconds = timeout_seconds
            return "answer"

    transport = RecordingJudgeTransport()
    provider = create_counterfactual_provider(CONFIG, transport_factory=lambda _: transport)
    assert provider.complete("system", "user", timeout_seconds=10.0) == "answer"
    assert transport.timeout_seconds == 10.0


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), -float("inf")])
def test_counterfactual_provider_rejects_invalid_timeout(timeout: float) -> None:
    class NeverTransport:
        def judge(self, *args, **kwargs):
            pytest.fail("invalid timeout must fail before a request")

    provider = create_counterfactual_provider(CONFIG, transport_factory=lambda _: NeverTransport())
    with pytest.raises(ValueError):
        provider.complete("system", "user", timeout_seconds=timeout)


@pytest.mark.parametrize("provider_name", ["claude", "openai", "gemini", "ollama"])
@pytest.mark.parametrize("failure", [None, "timeout", "other"])
@pytest.mark.parametrize("timeout", [None, 10.0])
def test_lazy_transports_forward_timeout_and_normalize_only_deadline_errors(
    monkeypatch: pytest.MonkeyPatch, provider_name: str, failure: str | None,
    timeout: float | None,
) -> None:
    from glassbox.eval.counterfactual_provider import CounterfactualProviderTimeout

    captured: dict[str, Any] = {}

    class SDKTimeout(Exception):
        pass

    error = SDKTimeout("deadline") if failure == "timeout" else RuntimeError("provider failure")
    if provider_name == "gemini":
        import httpx
        error = httpx.ReadTimeout("deadline") if failure == "timeout" else error
    elif provider_name == "ollama":
        error = URLError(TimeoutError("deadline")) if failure == "timeout" else error

    def request(**kwargs):
        captured["request"] = kwargs
        if failure:
            raise error
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(text="answer")], text="answer",
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="answer"))],
        )

    class Client:
        def __init__(self, **kwargs):
            captured["constructor"] = kwargs
            self.messages = types.SimpleNamespace(create=request)
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=request))
            self.models = types.SimpleNamespace(generate_content=request)

        def with_options(self, **kwargs):
            captured["options"] = kwargs
            return self

    if provider_name in {"claude", "openai"}:
        module = types.ModuleType("anthropic" if provider_name == "claude" else "openai")
        module.APITimeoutError = SDKTimeout
        setattr(module, "Anthropic" if provider_name == "claude" else "OpenAI", Client)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    elif provider_name == "gemini":
        google = types.ModuleType("google")
        google.genai = types.SimpleNamespace(Client=Client)
        monkeypatch.setitem(sys.modules, "google", google)
    else:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return b'{"response":"answer"}'

        def urlopen(request, *, timeout):
            captured["request"] = {"timeout": timeout}
            if failure:
                raise error
            return Response()

        monkeypatch.setattr("urllib.request.urlopen", urlopen)

    config = CounterfactualConfig(provider_name, "model", "credential", CONFIG.base_url, True)
    provider = create_counterfactual_provider(config)
    if failure == "timeout" and timeout is not None:
        with pytest.raises(CounterfactualProviderTimeout):
            provider.complete("system", "user", timeout_seconds=timeout)
    elif failure:
        with pytest.raises(type(error)) as caught:
            provider.complete("system", "user", timeout_seconds=timeout)
        assert caught.value is error
    else:
        assert provider.complete("system", "user", timeout_seconds=timeout) == "answer"
    if provider_name == "gemini":
        if timeout is None:
            assert "http_options" not in captured["request"]["config"]
        else:
            assert captured["request"]["config"]["http_options"] == {
                "timeout": 10000, "retry_options": {"attempts": 1},
            }
        assert captured["request"]["config"]["temperature"] == 0
    elif provider_name == "ollama":
        assert captured["request"]["timeout"] == (120 if timeout is None else timeout)
    if provider_name in {"openai", "claude"}:
        if timeout is None:
            assert "options" not in captured
            assert "timeout" not in captured["request"]
        else:
            assert captured["options"] == {"max_retries": 0}
            assert captured["request"]["timeout"] == timeout
        assert captured["request"]["temperature"] == 0
    if provider_name != "ollama":
        assert captured["constructor"]["api_key"] == "credential"


def test_adapter_passes_explicit_config_and_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []

    class FakeTransport:
        def judge(self, system_prompt: str, user_prompt: str) -> str:
            captured.extend([system_prompt, user_prompt])
            return '{"recommendation":null,"rationale":"reason"}'

    def factory(config: JudgeConfig) -> FakeTransport:
        captured.append(config)
        return FakeTransport()

    monkeypatch.setattr("glassbox.eval.counterfactual_provider.create_judge_provider", factory)
    config = CounterfactualConfig("openai", "m", "k", "https://api.openai.com/v1", True)
    provider = create_counterfactual_provider(config)
    assert provider.complete("system", "user") == '{"recommendation":null,"rationale":"reason"}'
    assert captured == [JudgeConfig("openai", "m", "k", config.base_url, True), "system", "user"]


def test_imports_and_local_factory_do_not_load_optional_sdks() -> None:
    result = subprocess.run([sys.executable, "-c", """
import sys
from glassbox.eval.counterfactual_config import CounterfactualConfig
from glassbox.eval.counterfactual_provider import create_counterfactual_provider
create_counterfactual_provider(CounterfactualConfig('ollama', 'm', None,
    'http://127.0.0.1:11434', False))
assert not {'anthropic', 'openai', 'google.genai'}.intersection(sys.modules)
"""], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
