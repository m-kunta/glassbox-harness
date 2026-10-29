"""Bounded, opt-in Discord delivery for already persisted drift runs."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from http.client import HTTPException
from typing import Callable, Literal
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)

from glassbox.eval.discord_config import DiscordConfig, _loopback_report_url
from glassbox.eval.drift import DriftReport
from glassbox.store import Repository


@dataclass(frozen=True)
class DiscordDeliveryOutcome:
    status: Literal["disabled", "invalid", "skipped", "delivered", "failed"]
    reason: str | None
    delivery_id: str | None


def build_discord_payload(report: DriftReport, *, report_url: str | None) -> dict[str, object]:
    embed: dict[str, object] = {
        "title": "Glassbox drift detected",
        "timestamp": report.as_of.isoformat(),
        "description": (
            f"Agent: {report.agent_name}\nBaseline: {report.baseline_id}\n"
            f"Policy: {report.policy_version}\nPolicy hash: {report.policy_hash}"
        ),
        "fields": [
            {
                "name": signal.name,
                "value": (
                    f"{signal.algorithm}: {signal.value}; warning: {signal.warning_threshold}; "
                    f"alert: {signal.alert_threshold}; baseline samples: {signal.baseline_count}; "
                    f"recent samples: {signal.recent_count}"
                ),
            }
            for signal in report.signals
            if signal.status == "drift_detected"
        ],
    }
    validated_url = _loopback_report_url(report_url)
    if validated_url is not None:
        embed["url"] = validated_url
    return {"embeds": [embed], "allowed_mentions": {"parse": []}}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def _post_json(
    url: str,
    payload: dict[str, object],
    *,
    timeout_seconds: float,
    opener: OpenerDirector | None = None,
) -> tuple[int, str | None]:
    """Send once without proxies or redirects; discard all response content."""
    transport = opener if opener is not None else build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with transport.open(request, timeout=timeout_seconds) as response:
            return response.status, response.headers.get("Retry-After")
    except HTTPError as error:
        try:
            return error.code, error.headers.get("Retry-After")
        finally:
            error.close()


def _retry_delay(retry_after: str | None, cap_seconds: float) -> float:
    try:
        delay = float(retry_after) if retry_after is not None else float("nan")
    except ValueError:
        return 0.25
    return delay if math.isfinite(delay) and 0 <= delay <= min(2.0, cap_seconds) else 0.25


def deliver_drift_alert(
    repository: Repository,
    report: DriftReport,
    drift_run_id: str,
    config: DiscordConfig,
    *,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    timeout_seconds: float = 2.0,
    retry_delay_cap_seconds: float = 2.0,
    opener: OpenerDirector | None = None,
) -> DiscordDeliveryOutcome:
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
            status, retry_after = _post_json(
                config.webhook_url, payload, timeout_seconds=timeout_seconds, opener=opener
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
