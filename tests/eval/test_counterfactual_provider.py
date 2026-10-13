from __future__ import annotations

import subprocess
import sys

import pytest

from glassbox.eval.counterfactual_config import CounterfactualConfig
from glassbox.eval.counterfactual_provider import create_counterfactual_provider
from glassbox.eval.judge_config import JudgeConfig


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
