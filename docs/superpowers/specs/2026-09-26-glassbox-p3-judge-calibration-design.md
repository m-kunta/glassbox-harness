# P3a: LLM judge calibration design

**Date:** 2026-09-26  
**Status:** Proposed — revisions pending approval for implementation planning
**Scope:** the first P3 increment only: collect optional human reasoning-quality labels, run a configured LLM judge against them, persist its provenance, and enforce the calibration gate. Drift monitoring, counterfactual providers, and alerts remain a later P3 increment.

## Goal

Glassbox will assess the quality of a recorded decision's reasoning with an explicitly configured LLM judge, but will not use that assessment as a gate until it is calibrated against human labels. The implementation must preserve Glassbox's local-first defaults: no provider is selected implicitly, no provider SDK is a core runtime dependency, and remote data transfer is explicit at the command line.

The source specification's rule remains authoritative: calibrate on at least 30 human-labelled decisions, report judge/human agreement, and refuse to gate when weighted kappa is below 0.60. A calibrated reasoning-quality gate requires an average judge score of at least 3.5 on the 1--5 rubric.

## Human labels

P3 extends the existing append-only `feedback` ledger instead of creating a competing label store. A feedback submission gains an optional `reasoning_quality_score`, an integer from 1 through 5, and an optional `reasoning_quality_rubric_version`. The version is required whenever a score is present and absent otherwise. It is independent of the existing `agree`/`disagree`/`uncertain` verdict and may be omitted during ordinary feedback.

The Decision Card presents this as an optional **Reasoning quality (1--5)** field with concise rubric help. Existing feedback submission, CSRF, Host/Origin, idempotency, and PRG behaviour remain unchanged. On an exact idempotent replay, the score is part of the caller-supplied payload comparison.

Feedback stays historical. For calibration, one label is selected for each decision and rubric version: the newest feedback row with a non-null score for that version, ordered by `created_at DESC, feedback_id DESC`. Older scored rows remain available for audit and never get overwritten. A v1 human score is never paired with a v2 judge score.

The rubric is versioned as a checked-in `glassbox/eval/rubrics/reasoning_quality_v1.md` artifact:

| Score | Meaning |
|---|---|
| 1 | The recommendation is unsupported by, or conflicts with, the cited evidence. |
| 2 | Material evidence, rationale, or alternative analysis is missing or materially flawed. |
| 3 | The recommendation is basically grounded in evidence, with understandable but meaningful gaps. |
| 4 | Evidence, rationale, and alternatives are clearly connected and operationally useful. |
| 5 | Reasoning is precise, complete for the decision, evidence-grounded, and candid about relevant limits. |

The judge requests a concise evaluative rationale, not hidden chain-of-thought. A rubric edit creates a new versioned artifact and must update the persisted rubric version; results from different rubric versions are reported separately and are not comparable.

## Judge input and output

For each selected decision, the judge receives only:

- the recommendation;
- the decision rationale;
- ordered rationale citations resolved to their persisted evidence field groups, including each field's `retrieved_at` and `source_system`; and
- alternatives considered.

It never receives confidence, planner verdicts, corrected recommendations, free-text feedback, raw blob content, prompt/completion references, or outcome labels. Persisted evidence has already passed Glassbox's redaction boundary, but it may still contain sensitive operational data; sending it to a remote provider is therefore a deliberate egress action.

Rationale, recommendation, alternatives, and evidence are untrusted model- or source-generated content. The prompt serializes each within clearly delimited data blocks and instructs the judge that text inside those blocks is data to evaluate, never instructions to follow. The rubric and response-format instructions remain outside those blocks.

Each provider adapter must produce a strictly parsed response shaped as `{"score": 1..5, "rationale": "..."}`. Invalid responses are recorded as a failed judge result with a short diagnostic and do not abort the remaining cases. Temperature is fixed at zero.

## Provider configuration and egress

Both configuration values are mandatory; Glassbox provides no default:

```dotenv
GLASSBOX_JUDGE_PROVIDER=claude     # claude | openai | gemini | ollama
GLASSBOX_JUDGE_MODEL=...
```

Credentials are selected only for the configured provider:

| Provider | Credential/configuration | Destination |
|---|---|---|
| `claude` | `ANTHROPIC_API_KEY` | `api.anthropic.com` |
| `openai` | `OPENAI_API_KEY` | `api.openai.com` |
| `gemini` | `GEMINI_API_KEY` | `generativelanguage.googleapis.com` |
| `ollama` | optional `GLASSBOX_JUDGE_OLLAMA_URL` | its configured base URL |

The judge loader reads only the Glassbox project-root `.env`, not an agent repository's `.env`. It uses `dotenv_values()` to parse a local mapping rather than injecting values into `os.environ`; process environment values take precedence. It reads only `GLASSBOX_JUDGE_*` and the credential for the chosen provider. `.env` is ignored by Git. This is deliberately narrower than the normal Glassbox configuration policy: tracing and serving still read their existing process-environment settings and do not acquire implicit dotenv loading.

Provider SDKs are optional extras and imported lazily inside their provider adapters. The base package does not install Anthropic, OpenAI, Gemini, or dotenv tooling merely to trace, serve, or run deterministic evaluation. Each adapter passes the selected credential and an explicit base URL to its SDK client constructor; it does not rely on the SDK reading environment variables or honour its ambient base-URL override. The disclosed destination is derived from that constructed base URL. Ollama uses a configured loopback URL by default; it counts as no-egress only when that URL resolves to an explicit loopback host. A non-loopback Ollama URL is treated as a remote destination.

The new command is:

```text
glassbox judge --database PATH --since DURATION [--max-cases N] [--allow-self-judge] [--confirm-egress] [--require-calibrated]
```

Before it constructs an adapter or makes any network call, it validates all configuration, dependencies, database/schema state, calibration candidates, self-judge rules, and `--max-cases`. For every remote destination it prints the provider, model, and destination, then requires `--confirm-egress`. It makes no network request without that acknowledgement. It prints the same destination disclosure for loopback Ollama but does not require confirmation.

`--since` is required (for example, `7d`) and defines the recent gated population. `--max-cases` is an optional positive cap for a deliberate bounded run; it makes no claim to be a cost-control system. A future cost/spend policy remains a backlog item.

## Self-judge protection

The command reads all LLM spans associated with each selected decision's trace before judging. It normalizes model identities by lowercasing, trimming provider prefixes, and removing a trailing dated release suffix such as `-20250929`; an equal normalized value is a self-judge match. This catches aliases such as `claude-sonnet-4-5` and `claude-sonnet-4-5-20250929` without broadly treating distinct model families as equivalent. When a span explicitly carries provider identity in `attributes["gen_ai.provider.name"]`, it also reports a matching provider-and-model pair. The current canonical span schema has a `model` field but no first-class provider field, so a missing provider never weakens a verified model-match refusal. If a decision has no recorded LLM span model, preflight says that self-judge protection could not be verified for that decision.

`--allow-self-judge` is the sole explicit bypass. The report and persisted run mark such a bypass. Different models are allowed even if provider provenance was absent; the command reports that limited provenance in its preflight output rather than inventing a provider identity.

## Persistence and calibration gate

P3 adds a strict migration that extends the current schema fingerprint and keeps `schema.sql` byte-for-byte aligned with all migrations. The migration adds `feedback.reasoning_quality_score` with a nullable 1--5 check and `feedback.reasoning_quality_rubric_version` with a paired nullability check. It adds an `eval_runs.run_kind` discriminator (`deterministic` or `judge`) and judge provenance fields:

- `judge_provider`;
- `judge_model`;
- `rubric_version`;
- `judge_temperature` (always `0` for this command); and
- `self_judge_allowed`.

The schema constrains provenance as a group: deterministic runs have every judge field null; judge runs have every judge field set. Judge runs also persist `status` (`passed`, `failed`, or `uncalibrated`), `status_reason`, and the judge-failure count. Each judge `eval_results` row persists whether that particular decision had a detected self-judge match and was judged only because `--allow-self-judge` was supplied. This preserves both the run-level bypass intent and the per-case audit trail.

The repository owns all SQL and transaction boundaries. A P3 run appends one `eval_runs` row, then one `eval_results` row per judged decision using `assertion_name = "reasoning_quality"`, numeric `score`, and a concise `judge_rationale`. Judge failures are persisted as failed results without a numeric score. Existing deterministic evaluation rows are migrated as `run_kind = deterministic` and retain null judge provenance.

The command reports:

- selected, successfully judged, and failed case counts;
- mean judge score for the current recent gated set;
- paired human-label count for the calibration set;
- linear weighted kappa and a deterministic 1,000-resample bootstrap confidence interval over the paired 1--5 labels; and
- the status, reason, and whether the quality gate was eligible and passed.

The calibration set is aggregated across runs: for each decision, it selects the latest successful judge result in the same provider/model/rubric cohort and pairs it with the newest human score for that same rubric version. Re-runs therefore replace a decision's calibration observation rather than inflating the 30-label count. `--max-cases` can bound a current run but cannot bypass the accumulated 30-pair requirement.

The gated set is the successfully judged decisions selected by this invocation's `--since` window and optional `--max-cases`. Its mean, not the labelled calibration sample's mean, is checked against 3.5. Failed calls are excluded from the numerical mean but make the gate ineligible when they exceed 5% of the selected gated set; the report always exposes the failure count and rate.

Linear weighted kappa is used because adjacent reasoning scores are meaningfully different but less severe than wider disagreement. The reasoning-quality threshold is eligible only when there are at least 30 paired human labels, both label vectors contain at least two distinct values, kappa is at least 0.60, and the current run's failure rate is at most 5%, all within the same judge provider/model/rubric cohort. An undefined kappa is uncalibrated, never coerced to zero or NaN. Otherwise the run is reported as uncalibrated and does not claim a quality-gate pass or failure. Once calibrated, a current gated-set mean below 3.5 fails the gate. Provider/model/rubric changes start a separate comparability cohort and must be calibrated independently.

Completed judge commands emit structured output with `status` (`passed`, `failed`, or `uncalibrated`) and a machine-readable `reason` (`too_few_labels`, `kappa_below_threshold`, `kappa_undefined`, or `failure_rate_too_high` when uncalibrated). Human output begins an uncalibrated result with, for example, `UNCALIBRATED: no quality gate applied (reason: 22/30 labels)`. Exit `0` means a completed passed **or uncalibrated** run, exit `1` means an eligible calibrated gate failure, and exit `2` retains the project's P1 convention for operational/preflight failures, including missing `--confirm-egress`. `--require-calibrated` converts an uncalibrated completed run to exit `1`, ready for a future blocking CI workflow.

## Architecture boundaries

New judge code lives below `glassbox.eval` and may read the store; it has no SDK, collector, or web dependency. Provider SDK imports stay adapter-local. Import-linter will explicitly prevent `glassbox.sdk`, `glassbox.collector`, and `glassbox.web` from importing `glassbox.eval.judge`; the existing `eval` contract continues to prevent `eval` from importing those runtime packages. `glassbox.cli` imports the judge entry point only inside the `judge` subcommand branch, so `serve`, `export`, and deterministic `eval` neither load judge code nor optional provider SDKs. This makes judging an opt-in command path rather than a tracing or serving dependency.

## Verification

- Schema migration and `schema.sql` mirror tests cover the optional score/version pair, run-kind/provenance/status constraints, and backward compatibility for deterministic runs.
- Repository tests prove score-aware feedback idempotency, newest-scored-label selection per decision and rubric version, and latest-successful-result selection across runs.
- Web tests prove the optional score validates 1--5, renders safely, and remains optional.
- Config tests prove project-root-only scoped dotenv parsing, process-environment precedence, no `os.environ` mutation, missing provider/model/credential rejection, and no provider construction before preflight validation.
- Adapter contract tests use fakes to prove each provider's request shape, explicit credential/base-URL constructor use, honest destination disclosure, zero temperature, strict output parsing, failure isolation, and lazy optional imports; live provider calls are not part of CI.
- Judge tests prove confidence and feedback text never enter prompts; the data blocks resist instruction-shaped rationale/evidence; citations resolve as field groups including source/timestamp metadata; malformed/dangling evidence has a visible per-case failure rather than fabricated context.
- Safety tests prove remote calls require `--confirm-egress`, loopback Ollama does not, non-loopback Ollama does, missing span model provenance is reported, and normalized model/provider self-judge cases refuse unless explicitly bypassed.
- Calibration tests cover latest-result de-duplication, linear weighted kappa and its bootstrap interval, degenerate labels, the 30-label floor, the 0.60 agreement floor, the 5% failure-rate ceiling, cohort separation by provider/model/rubric, current-gated-set mean semantics, `--require-calibrated`, and the documented exit/status combinations.
- Architecture tests activate and verify the judge-specific import boundaries.

## Deferred work

P3b will design drift monitoring, counterfactual providers, and alert delivery after this calibration path has real labelled data. Bulk automated scheduling and live-provider spend controls are intentionally out of scope for P3a.
