"""Pure, policy-scoped deferred outcome parsing, labels, and reporting."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol, TypeAlias

from glassbox.store import Database, Repository
from glassbox.store.repository import OutcomeSubmission

JSONScalar: TypeAlias = str | int | float | bool | None
_POLICY_KEYS = {"policy_version", "maturity_days", "rules"}
_RULE_KEYS = {
    "agent_name",
    "decision_type",
    "outcome_type",
    "recommendation_pointer",
    "positive_recommendation_values",
    "outcome_pointer",
    "positive_outcome_value",
}
_OUTCOME_KEYS = {
    "source_id",
    "decision_id",
    "outcome_type",
    "observed_at",
    "horizon_days",
    "value",
}
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)")
_ULID = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}")


class ReconciliationError(ValueError):
    """A safe, stable reason code suitable for a rejection artifact."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ReconciliationRule:
    agent_name: str
    decision_type: str
    outcome_type: str
    recommendation_pointer: str
    positive_recommendation_values: tuple[JSONScalar, ...]
    outcome_pointer: str
    positive_outcome_value: JSONScalar


@dataclass(frozen=True)
class ReconciliationPolicy:
    policy_version: str
    maturity_days: int
    rules: tuple[ReconciliationRule, ...]
    policy_hash: str


@dataclass(frozen=True)
class OutcomeInput:
    line_number: int
    source_id: str
    decision_id: str
    outcome_type: str
    observed_at: datetime
    horizon_days: int
    value: object


@dataclass(frozen=True)
class RejectedOutcome:
    line_number: int
    source_id: str | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"line_number": self.line_number, "source_id": self.source_id, "reason": self.reason}


@dataclass(frozen=True)
class ImportSummary:
    accepted: int
    replayed: int
    rejected: int

    def to_dict(self) -> dict[str, object]:
        return {"accepted": self.accepted, "replayed": self.replayed, "rejected": self.rejected}


class DecisionInput(Protocol):
    """Read-only structural view of a decision; storage need not import eval."""

    @property
    def agent_name(self) -> str: ...

    @property
    def decision_type(self) -> str: ...

    @property
    def recommendation(self) -> object: ...


class OutcomeValueInput(Protocol):
    @property
    def outcome_type(self) -> str: ...

    @property
    def value(self) -> object: ...


class OutcomeSource(Protocol):
    @property
    def outcome_id(self) -> str: ...

    @property
    def outcome_type(self) -> str: ...

    @property
    def observed_at(self) -> datetime: ...

    @property
    def label(self) -> str | None: ...

    @property
    def reconciliation_policy_hash(self) -> str | None: ...


class DecisionSource(DecisionInput, Protocol):
    @property
    def decision_id(self) -> str: ...

    @property
    def decided_at(self) -> datetime: ...

    @property
    def outcomes(self) -> Sequence[OutcomeSource]: ...

    @property
    def has_effective_override(self) -> bool: ...


@dataclass(frozen=True)
class ReconciliationOutcomeSource:
    outcome_id: str
    outcome_type: str
    observed_at: datetime
    label: str | None
    reconciliation_policy_hash: str | None
    reconciliation_policy_version: str | None = None


@dataclass(frozen=True)
class ReconciliationDecisionSource:
    decision_id: str
    agent_name: str
    decision_type: str
    recommendation: object
    decided_at: datetime
    outcomes: tuple[ReconciliationOutcomeSource, ...] = ()
    has_effective_override: bool = False


@dataclass(frozen=True)
class ReconciliationMetrics:
    mature: int
    labelled: int
    coverage: float | None
    tp: int
    fp: int
    tn: int
    fn: int
    precision: float | None
    recall: float | None
    overrides: int
    override_rate: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ReconciliationReport:
    policy_version: str
    policy_hash: str
    maturity_days: int
    as_of: datetime
    totals: ReconciliationMetrics
    by_decision_type: tuple[tuple[str, ReconciliationMetrics], ...]
    by_agent_name: tuple[tuple[str, tuple[tuple[str, ReconciliationMetrics], ...]], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "maturity_days": self.maturity_days,
            "as_of": self.as_of.isoformat(),
            "totals": self.totals.to_dict(),
            "by_decision_type": {
                name: metrics.to_dict() for name, metrics in self.by_decision_type
            },
            "by_agent_name": {
                agent: {name: metrics.to_dict() for name, metrics in groups}
                for agent, groups in self.by_agent_name
            },
        }


def default_policy_path() -> Path:
    return Path(__file__).parent / "policies" / "reconciliation_v1.toml"


def _string(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReconciliationError(reason)
    return value


def _scalar(value: object, reason: str) -> JSONScalar:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ReconciliationError(reason)


def _policy_scalar(value: object) -> JSONScalar:
    if isinstance(value, str):
        return _string(value, "invalid_policy")
    return _scalar(value, "invalid_policy")


def _pointer_tokens(pointer: object, reason: str) -> tuple[str, ...]:
    if not isinstance(pointer, str):
        raise ReconciliationError(reason)
    if pointer == "":
        return ()
    if not pointer.startswith("/") or re.search(r"~(?:[^01]|$)", pointer):
        raise ReconciliationError(reason)
    return tuple(token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/"))


def load_policy(path: Path) -> ReconciliationPolicy:
    """Validate the complete policy and hash its canonical parsed TOML."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ReconciliationError("invalid_policy") from exc
    return _policy_from_data(data)


def _policy_from_data(data: Mapping[str, object]) -> ReconciliationPolicy:
    if set(data) != _POLICY_KEYS:
        raise ReconciliationError("invalid_policy")
    version = _string(data["policy_version"], "invalid_policy")
    maturity = data["maturity_days"]
    if type(maturity) is not int or maturity <= 0:
        raise ReconciliationError("invalid_policy")
    try:
        timedelta(days=maturity)
    except OverflowError as exc:
        raise ReconciliationError("invalid_policy") from exc
    raw_rules = data["rules"]
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ReconciliationError("invalid_policy")
    rules: list[ReconciliationRule] = []
    matches: set[tuple[str, str, str]] = set()
    for raw in raw_rules:
        if not isinstance(raw, dict) or set(raw) != _RULE_KEYS:
            raise ReconciliationError("invalid_policy")
        agent = _string(raw["agent_name"], "invalid_policy")
        decision_type = _string(raw["decision_type"], "invalid_policy")
        outcome_type = _string(raw["outcome_type"], "invalid_policy")
        key = (agent, decision_type, outcome_type)
        if key in matches:
            raise ReconciliationError("invalid_policy")
        matches.add(key)
        positives = raw["positive_recommendation_values"]
        if not isinstance(positives, list) or not positives:
            raise ReconciliationError("invalid_policy")
        recommendation_pointer = raw["recommendation_pointer"]
        outcome_pointer = raw["outcome_pointer"]
        _pointer_tokens(recommendation_pointer, "invalid_policy")
        _pointer_tokens(outcome_pointer, "invalid_policy")
        rules.append(
            ReconciliationRule(
                agent_name=agent,
                decision_type=decision_type,
                outcome_type=outcome_type,
                recommendation_pointer=recommendation_pointer,
                positive_recommendation_values=tuple(_policy_scalar(v) for v in positives),
                outcome_pointer=outcome_pointer,
                positive_outcome_value=_policy_scalar(raw["positive_outcome_value"]),
            )
        )
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return ReconciliationPolicy(
        version, maturity, tuple(rules), hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )


def _validate_policy(policy: ReconciliationPolicy) -> None:
    data: dict[str, object] = {
        "policy_version": policy.policy_version,
        "maturity_days": policy.maturity_days,
        "rules": [
            {**asdict(rule), "positive_recommendation_values": list(
                rule.positive_recommendation_values
            )}
            for rule in policy.rules
        ],
    }
    if _policy_from_data(data) != policy:
        raise ReconciliationError("invalid_policy")


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ReconciliationError("invalid_timestamp")


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise ReconciliationError("invalid_timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as exc:
        raise ReconciliationError("invalid_timestamp") from exc


def _json_safe(value: object) -> bool:
    if isinstance(value, list):
        return all(_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_safe(item) for key, item in value.items())
    try:
        _scalar(value, "invalid_shape")
    except ReconciliationError:
        return False
    return True


def parse_jsonl(lines: Iterable[str]) -> tuple[OutcomeInput | RejectedOutcome, ...]:
    """Parse nonblank lines independently without leaking payloads into reasons."""
    results: list[OutcomeInput | RejectedOutcome] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except (ValueError, RecursionError):
            results.append(RejectedOutcome(line_number, None, "malformed_json"))
            continue
        source_id = data.get("source_id") if isinstance(data, dict) else None
        if not isinstance(source_id, str) or not source_id.strip():
            source_id = None
        try:
            if not isinstance(data, dict) or set(data) != _OUTCOME_KEYS:
                raise ReconciliationError("invalid_shape")
            source = _string(data["source_id"], "invalid_shape")
            decision_id = _string(data["decision_id"], "invalid_shape")
            if not _ULID.fullmatch(decision_id):
                raise ReconciliationError("invalid_shape")
            outcome_type = _string(data["outcome_type"], "invalid_shape")
            horizon = data["horizon_days"]
            if (
                type(horizon) is not int
                or not 0 <= horizon <= 2**63 - 1
                or not _json_safe(data["value"])
            ):
                raise ReconciliationError("invalid_shape")
            results.append(
                OutcomeInput(
                    line_number,
                    source,
                    decision_id,
                    outcome_type,
                    _timestamp(data["observed_at"]),
                    horizon,
                    data["value"],
                )
            )
        except ReconciliationError as exc:
            results.append(RejectedOutcome(line_number, source_id, exc.reason))
        except RecursionError:
            results.append(RejectedOutcome(line_number, source_id, "invalid_shape"))
    return tuple(results)


def _pointer(value: object, pointer: str) -> JSONScalar:
    for token in _pointer_tokens(pointer, "invalid_pointer"):
        if not isinstance(value, Mapping) or token not in value:
            raise ReconciliationError("invalid_pointer")
        value = value[token]
    return _scalar(value, "invalid_pointer")


def _equal(left: JSONScalar, right: JSONScalar) -> bool:
    """JSON has one number type, but its booleans are distinct from numbers."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _matching_rules(
    policy: ReconciliationPolicy, decision: DecisionInput
) -> tuple[ReconciliationRule, ...]:
    return tuple(
        rule
        for rule in policy.rules
        if rule.agent_name == decision.agent_name and rule.decision_type == decision.decision_type
    )


def derive_label(
    policy: ReconciliationPolicy, decision: DecisionInput, outcome: OutcomeValueInput
) -> Literal["tp", "fp", "tn", "fn"]:
    rules = tuple(
        rule
        for rule in _matching_rules(policy, decision)
        if rule.outcome_type == outcome.outcome_type
    )
    if not rules:
        raise ReconciliationError("no_matching_rule")
    if len(rules) != 1:
        raise ReconciliationError("ambiguous_rule")
    rule = rules[0]
    recommendation = _pointer(decision.recommendation, rule.recommendation_pointer)
    predicted = any(
        _equal(recommendation, positive) for positive in rule.positive_recommendation_values
    )
    realized = _equal(_pointer(outcome.value, rule.outcome_pointer), rule.positive_outcome_value)
    if predicted:
        return "tp" if realized else "fp"
    return "fn" if realized else "tn"


def _outcome_id(as_of: datetime) -> str:
    value = (int(as_of.timestamp() * 1000) << 80) | secrets.randbits(80)
    if value < 0 or value >= 1 << 128:
        raise ReconciliationError("invalid_timestamp")
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    encoded = ""
    for _ in range(26):
        value, remainder = divmod(value, 32)
        encoded = alphabet[remainder] + encoded
    return encoded


def _record_outcome(
    repository: Repository, parsed: OutcomeInput, policy: ReconciliationPolicy, as_of: datetime
) -> bool | RejectedOutcome:
    detail = repository.decision_detail(parsed.decision_id)
    if detail is None:
        return RejectedOutcome(parsed.line_number, parsed.source_id, "unknown_decision")
    try:
        label = derive_label(policy, detail.stored_decision.event, parsed)
    except ReconciliationError as exc:
        return RejectedOutcome(parsed.line_number, parsed.source_id, exc.reason)
    submission = OutcomeSubmission(
        outcome_id=_outcome_id(as_of),
        source_id=parsed.source_id,
        decision_id=parsed.decision_id,
        outcome_type=parsed.outcome_type,
        observed_at=parsed.observed_at,
        horizon_days=parsed.horizon_days,
        value=parsed.value,
        label=label,
        reconciliation_policy_version=policy.policy_version,
        reconciliation_policy_hash=policy.policy_hash,
    )
    try:
        record = repository.record_outcome(submission)
    except ValueError as exc:
        if str(exc) != "outcome source_id was reused with a different payload":
            raise
        return RejectedOutcome(parsed.line_number, parsed.source_id, "source_id_conflict")
    return record.outcome_id == submission.outcome_id


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve() or (
        left.exists() and right.exists() and left.samefile(right)
    )


def import_outcomes(
    database_path: Path,
    input_path: Path,
    reject_path: Path,
    policy: ReconciliationPolicy,
    *,
    clock: Callable[[], datetime],
) -> ImportSummary:
    """Commit acceptable lines independently and atomically replace safe rejects."""
    _validate_policy(policy)
    temporary = reject_path.with_name(f".{reject_path.name}.tmp")
    if (
        _same_path(input_path, reject_path)
        or _same_path(database_path, reject_path)
        or _same_path(database_path, temporary)
    ):
        raise ReconciliationError("invalid_paths")
    as_of = clock()
    _require_utc(as_of)
    # Validate the clock's ULID range before any database mutation.
    _outcome_id(as_of)
    if reject_path.is_dir():
        raise IsADirectoryError("reject destination is a directory")
    accepted = replayed = rejected = 0
    with input_path.open(encoding="utf-8") as source:
        # Exclusive creation also refuses existing files or symlinks at the
        # temporary name, protecting the input and other callers' artifacts.
        with temporary.open("x", encoding="utf-8") as rejects:
            try:
                rejects.flush()
                os.fsync(rejects.fileno())
                database = Database.open(database_path)
                try:
                    repository = Repository(database)
                    for number, line in enumerate(source, 1):
                        if not line.strip():
                            continue
                        parsed = replace(parse_jsonl((line,))[0], line_number=number)
                        result = parsed if isinstance(parsed, RejectedOutcome) else (
                            _record_outcome(repository, parsed, policy, as_of)
                        )
                        if isinstance(result, RejectedOutcome):
                            rejected += 1
                            rejects.write(json.dumps({
                                "line": result.line_number,
                                "source_id": result.source_id,
                                "reason": result.reason,
                            }, sort_keys=True, separators=(",", ":")) + "\n")
                        elif result:
                            accepted += 1
                        else:
                            replayed += 1
                finally:
                    database.close()
                rejects.flush()
                os.fsync(rejects.fileno())
                temporary.replace(reject_path)
            finally:
                temporary.unlink(missing_ok=True)
    return ImportSummary(accepted, replayed, rejected)


def run_reconciliation_report(
    database_path: Path,
    policy: ReconciliationPolicy,
    *,
    clock: Callable[[], datetime],
) -> ReconciliationReport:
    """Calculate one policy-scoped report without writing database rows."""
    _validate_policy(policy)
    as_of = clock()
    _require_utc(as_of)
    database = Database.open_read_only(database_path)
    try:
        source = Repository(database).reconciliation_source(policy.policy_hash)
        return calculate_report(policy, source, as_of=as_of)
    finally:
        database.close()


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _metrics(rows: Sequence[tuple[str | None, bool]]) -> ReconciliationMetrics:
    labels = [label for label, _ in rows]
    tp, fp, tn, fn = (labels.count(label) for label in ("tp", "fp", "tn", "fn"))
    labelled = tp + fp + tn + fn
    overrides = sum(override for _, override in rows)
    return ReconciliationMetrics(
        len(rows),
        labelled,
        _ratio(labelled, len(rows)),
        tp,
        fp,
        tn,
        fn,
        _ratio(tp, tp + fp),
        _ratio(tp, tp + fn),
        overrides,
        _ratio(overrides, len(rows)),
    )


def calculate_report(
    policy: ReconciliationPolicy, source: Iterable[DecisionSource], *, as_of: datetime
) -> ReconciliationReport:
    """Count mature decisions with at most one same-policy label per decision."""
    _require_utc(as_of)
    try:
        cutoff = as_of - timedelta(days=policy.maturity_days)
    except OverflowError as exc:
        raise ReconciliationError("invalid_policy") from exc
    totals: list[tuple[str | None, bool]] = []
    by_type: dict[str, list[tuple[str | None, bool]]] = {}
    by_agent: dict[str, dict[str, list[tuple[str | None, bool]]]] = {}
    for decision in source:
        _require_utc(decision.decided_at)
        if decision.decided_at > cutoff:
            continue
        rules = _matching_rules(policy, decision)
        if len(rules) > 1:
            raise ReconciliationError("ambiguous_rule")
        compatible: list[OutcomeSource] = []
        if rules:
            for outcome in decision.outcomes:
                if (
                    outcome.outcome_type == rules[0].outcome_type
                    and outcome.reconciliation_policy_hash == policy.policy_hash
                ):
                    _require_utc(outcome.observed_at)
                    compatible.append(outcome)
        latest = max(compatible, key=lambda item: (item.observed_at, item.outcome_id), default=None)
        label = latest.label if latest else None
        if label is not None and label not in {"tp", "fp", "tn", "fn"}:
            raise ReconciliationError("invalid_label")
        row = (label, decision.has_effective_override)
        totals.append(row)
        by_type.setdefault(decision.decision_type, []).append(row)
        by_agent.setdefault(decision.agent_name, {}).setdefault(decision.decision_type, []).append(
            row
        )
    return ReconciliationReport(
        policy.policy_version,
        policy.policy_hash,
        policy.maturity_days,
        as_of,
        _metrics(totals),
        tuple((name, _metrics(rows)) for name, rows in sorted(by_type.items())),
        tuple(
            (agent, tuple((name, _metrics(rows)) for name, rows in sorted(groups.items())))
            for agent, groups in sorted(by_agent.items())
        ),
    )
