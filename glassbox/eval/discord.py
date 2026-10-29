"""Bounded, opt-in Discord delivery for already persisted drift runs."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from http.client import HTTPException
from time import monotonic
from typing import Callable, Literal
from urllib.error import URLError

from glassbox.eval.discord_config import DiscordConfig, _loopback_report_url
from glassbox.eval.drift import DriftReport, DriftSignalReport
from glassbox.store import Repository
from glassbox.store.repository import DiscordDriftSignal, DiscordDriftSnapshot

MAX_METADATA_CHARACTERS = 128
MAX_PAYLOAD_BYTES = 8192
MAX_REPORT_URL_BYTES = 512


def _bounded_text(value: str | None, limit: int) -> str:
    text = value or "unknown"
    return text if len(text) <= limit else "[omitted: oversized value]"


@dataclass(frozen=True)
class DiscordDeliveryOutcome:
    status: Literal["disabled", "invalid", "skipped", "delivered", "failed"]
    reason: str | None
    delivery_id: str | None


def build_discord_payload(
    report: DriftReport | DiscordDriftSnapshot, *, report_url: str | None,
) -> dict[str, object]:
    signals: tuple[DriftSignalReport | DiscordDriftSignal, ...] = report.signals
    embed: dict[str, object] = {
        "title": "Glassbox drift detected",
        "timestamp": report.as_of.isoformat(),
        "description": (
            f"Agent: {_bounded_text(report.agent_name, MAX_METADATA_CHARACTERS)}\n"
            f"Baseline: {_bounded_text(report.baseline_id, 26)}\n"
            f"Policy: {_bounded_text(report.policy_version, MAX_METADATA_CHARACTERS)}\n"
            f"Policy hash: {report.policy_hash[:12]}"
        ),
        "fields": [
            {
                "name": signal.name,
                "value": _bounded_text(
                    f"{signal.algorithm}: {signal.value}; warning: {signal.warning_threshold}; "
                    f"alert: {signal.alert_threshold}; baseline samples: {signal.baseline_count}; "
                    f"recent samples: {signal.recent_count}", 256
                ),
            }
            for signal in sorted(signals, key=lambda signal: signal.name)[:4]
            if signal.status == "drift_detected" and signal.name in {
                "confidence", "decision_type", "trace_latency_ms", "trace_cost_usd"
            }
        ],
    }
    validated_url = _loopback_report_url(report_url)
    if validated_url is not None and len(validated_url.encode("utf-8")) <= MAX_REPORT_URL_BYTES:
        embed["url"] = validated_url
    payload: dict[str, object] = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    # Reserve space for the fixed-size persisted run ID added by delivery.
    if len(json.dumps(payload).encode("utf-8")) > MAX_PAYLOAD_BYTES - 128:
        embed["fields"] = []
    return payload


def _post_json(
    url: str,
    payload: dict[str, object],
    *,
    timeout_seconds: float,
) -> tuple[int, str | None]:
    """Enforce a wall-clock budget, including DNS/TLS/headers, then reap the worker."""
    _validate_timing(timeout_seconds)
    deadline = monotonic() + timeout_seconds
    if timeout_seconds == 0:
        raise OSError("Discord request deadline exceeded")
    request = json.dumps({"url": url, "payload": payload, "timeout_seconds": timeout_seconds})
    child = subprocess.Popen(
        [sys.executable, "-m", "glassbox.eval._discord_transport"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        output, _ = child.communicate(
            request.encode("utf-8"), timeout=max(0, deadline - monotonic())
        )
        if child.returncode != 0:
            raise OSError("Discord transport failed")
        try:
            status, retry_after = json.loads(output)
            if not isinstance(status, int) or not (
                retry_after is None or isinstance(retry_after, str)
            ):
                raise ValueError
            return status, retry_after
        except (ValueError, TypeError) as error:
            raise OSError("Discord transport failed") from error
    except subprocess.TimeoutExpired:
        raise OSError("Discord request deadline exceeded") from None
    finally:
        if child.poll() is None:
            child.kill()
        # Reap on timeout, cancellation, and success; no background request survives.
        child.communicate()


def _retry_delay(retry_after: str | None, cap_seconds: float) -> float:
    _validate_timing(cap_seconds)
    fallback = min(0.25, cap_seconds)
    try:
        delay = float(retry_after) if retry_after is not None else float("nan")
    except ValueError:
        return fallback
    return delay if math.isfinite(delay) and 0 <= delay <= min(2.0, cap_seconds) else fallback


def _validate_timing(value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError("Discord timing must be finite and nonnegative")


def deliver_drift_alert(
    repository: Repository,
    report: DriftReport | DiscordDriftSnapshot,
    drift_run_id: str,
    config: DiscordConfig,
    *,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    timeout_seconds: float = 2.0,
    retry_delay_cap_seconds: float = 2.0,
    post: Callable[..., tuple[int, str | None]] | None = None,
) -> DiscordDeliveryOutcome:
    _validate_timing(timeout_seconds)
    _validate_timing(retry_delay_cap_seconds)
    if report.status != "drift_detected":
        return DiscordDeliveryOutcome("skipped", "not_drift_detected", None)
    if config.status != "ready":
        return DiscordDeliveryOutcome(config.status, config.reason, None)
    assert config.webhook_url is not None
    destination_hash = hashlib.sha256(config.webhook_url.encode("utf-8")).hexdigest()
    claim = repository.claim_discord_delivery(drift_run_id, destination_hash, now=clock())
    delivery_id = claim.delivery.delivery_id if claim.delivery is not None else None
    if claim.status != "claimed":
        return DiscordDeliveryOutcome("skipped", claim.status, delivery_id)
    assert delivery_id is not None
    payload = build_discord_payload(report, report_url=config.report_url)
    payload["content"] = f"Drift run: {drift_run_id}"

    error_category: str | None = None
    for attempt in range(2):
        retry_after = None
        try:
            status, retry_after = (post or _post_json)(
                config.webhook_url, payload, timeout_seconds=timeout_seconds
            )
            if 200 <= status < 300:
                error_category = None
                break
            if status == 429:
                error_category = "http_429"
            elif 500 <= status < 600:
                error_category = "http_5xx"
            else:
                error_category = "http_4xx"
        except (URLError, OSError, HTTPException):
            error_category = "transport_error"
        if error_category == "http_4xx" or attempt == 1:
            break
        sleep(_retry_delay(retry_after, retry_delay_cap_seconds))

    final_status: Literal["delivered", "failed"] = (
        "delivered" if error_category is None else "failed"
    )
    repository.finalize_discord_delivery(
        delivery_id, status=final_status, error_category=error_category, finalized_at=clock()
    )
    return DiscordDeliveryOutcome(final_status, error_category, delivery_id)


def recover_drift_alerts(
    repository: Repository, report: DriftReport, config: DiscordConfig, *,
    clock: Callable[[], datetime], sleep: Callable[[float], None],
) -> None:
    if config.status != "ready" or report.agent_name is None or report.persisted_run_id is None:
        return
    assert config.webhook_url is not None
    destination_hash = hashlib.sha256(config.webhook_url.encode("utf-8")).hexdigest()
    reports = repository.recoverable_discord_reports(
        report.agent_name, destination_hash, exclude_run_id=report.persisted_run_id, now=clock(),
    )
    for previous in reports:
        try:
            deliver_drift_alert(
                repository, previous, previous.persisted_run_id, config, clock=clock, sleep=sleep,
            )
        except Exception:
            # One damaged/stale attempt must not prevent delivery of other reports.
            continue
