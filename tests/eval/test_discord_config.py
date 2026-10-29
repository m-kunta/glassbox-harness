import subprocess
import sys
from pathlib import Path
from types import MappingProxyType

import pytest

from glassbox.eval.discord_config import load_discord_config

WEBHOOK = "https://discord.com/api/webhooks/123/token"
ENV = {"GLASSBOX_DISCORD_WEBHOOK_URL": WEBHOOK, "GLASSBOX_DISCORD_ALLOW_EGRESS": "1"}


def test_base_install_environment_config_does_not_import_dotenv(tmp_path):
    code = '''
import sys
sys.modules["dotenv"] = None
from pathlib import Path
from glassbox.eval.discord_config import load_discord_config
config = load_discord_config(Path(sys.argv[1]), {
    "GLASSBOX_DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/token",
    "GLASSBOX_DISCORD_ALLOW_EGRESS": "1",
})
assert config.status == "ready"
from glassbox.eval.discord import deliver_drift_alert
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_scoped_dotenv_supports_quotes_export_and_comments(tmp_path):
    (tmp_path / ".env").write_text(
        f'export GLASSBOX_DISCORD_WEBHOOK_URL="{WEBHOOK}" # local secret\n'
        "GLASSBOX_DISCORD_ALLOW_EGRESS='1'\n"
        "GLASSBOX_DISCORD_REPORT_URL=http://localhost/drift # local report\n"
    )
    config = load_discord_config(tmp_path, {})
    assert (config.status, config.webhook_url, config.report_url) == (
        "ready", WEBHOOK, "http://localhost/drift")


@pytest.mark.parametrize(
    "environ,reason",
    [
        ({}, "missing_url"),
        ({"GLASSBOX_DISCORD_ALLOW_EGRESS": "1"}, "missing_url"),
        ({"GLASSBOX_DISCORD_WEBHOOK_URL": WEBHOOK}, "egress_not_authorized"),
        (ENV | {"GLASSBOX_DISCORD_ALLOW_EGRESS": "true"}, "egress_not_authorized"),
    ],
)
def test_requires_both_explicit_gates(tmp_path: Path, environ: dict, reason: str) -> None:
    config = load_discord_config(tmp_path, environ)
    assert (config.status, config.reason, config.webhook_url) == ("disabled", reason, None)


def test_dotenv_url_alone_does_not_authorize_egress(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"GLASSBOX_DISCORD_WEBHOOK_URL={WEBHOOK}\n")
    assert load_discord_config(tmp_path, {}).status == "disabled"


def test_scoped_dotenv_and_nonblank_process_precedence_without_mutation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "GLASSBOX_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/456/file-token\n"
        "GLASSBOX_DISCORD_ALLOW_EGRESS=1\n"
        "GLASSBOX_DISCORD_REPORT_URL=http://localhost:8000/drift\n"
        "UNRELATED=value\n"
    )
    environ = MappingProxyType(
        {"GLASSBOX_DISCORD_WEBHOOK_URL": WEBHOOK, "GLASSBOX_DISCORD_ALLOW_EGRESS": "   "}
    )
    config = load_discord_config(tmp_path, environ)
    assert (config.status, config.webhook_url, config.reason) == ("ready", WEBHOOK, None)
    assert config.report_url == "http://localhost:8000/drift"
    assert dict(environ) == {
        "GLASSBOX_DISCORD_WEBHOOK_URL": WEBHOOK,
        "GLASSBOX_DISCORD_ALLOW_EGRESS": "   ",
    }


def test_dotenv_interpolation_is_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    (tmp_path / ".env").write_text(
        "GLASSBOX_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123/${DISCORD_TOKEN}\n"
        "GLASSBOX_DISCORD_ALLOW_EGRESS=1\n"
    )
    assert load_discord_config(tmp_path, {}).status == "invalid"


@pytest.mark.parametrize(
    "url",
    [
        "http://discord.com/api/webhooks/123/token",
        "https://discordapp.com/api/webhooks/123/token",
        "https://discord.com.evil.test/api/webhooks/123/token",
        "https://user@discord.com/api/webhooks/123/token",
        "https://discord.com:443/api/webhooks/123/token",
        WEBHOOK + "?wait=true",
        WEBHOOK + "#secret",
        WEBHOOK + "?",
        WEBHOOK + "#",
        "https://discord.com/other/123/token",
        "https://discord.com/api/webhooks/",
        WEBHOOK + "/../../other",
        WEBHOOK + "%2fother",
        WEBHOOK + "\n",
        " https://discord.com/api/webhooks/123/token",
        "https://[invalid",
    ],
)
def test_rejects_unsafe_webhook(tmp_path: Path, url: str) -> None:
    config = load_discord_config(tmp_path, ENV | {"GLASSBOX_DISCORD_WEBHOOK_URL": url})
    assert (config.status, config.reason, config.webhook_url) == ("invalid", "invalid_url", None)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000/drift?agent=a",
        "https://127.0.0.1/drift",
        "http://[::1]:8000/drift",
    ],
)
def test_accepts_only_exact_loopback_report_urls(tmp_path: Path, url: str) -> None:
    config = load_discord_config(tmp_path, ENV | {"GLASSBOX_DISCORD_REPORT_URL": url})
    assert config.status == "ready"
    assert config.report_url == url


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://example.com/drift",
        "https://localhost.evil.test/drift",
        "http://127.0.0.1.evil.test",
        "http://127.1/drift",
        "file://localhost/drift",
        "http://user:secret@localhost/drift",
        "http://localhost:bad/drift",
        "http://localhost:0/drift",
        "http://[invalid",
        "http://localhost/drift\n",
        "http://localhost\\@evil.test/drift",
        " http://localhost/drift",
    ],
)
def test_unsafe_or_absent_report_url_is_omitted(tmp_path: Path, url: str | None) -> None:
    environ = ENV if url is None else ENV | {"GLASSBOX_DISCORD_REPORT_URL": url}
    config = load_discord_config(tmp_path, environ)
    assert config.status == "ready"
    assert config.report_url is None
