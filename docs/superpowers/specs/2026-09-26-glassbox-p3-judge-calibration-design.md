# P3a: LLM judge calibration design

**Date:** 2026-09-26  
**Status:** Approved for implementation planning  
**Scope:** the first P3 increment only: collect optional human reasoning-quality labels, run a configured LLM judge against them, persist its provenance, and enforce the calibration gate. Drift monitoring, counterfactual providers, and alerts remain a later P3 increment.

## Goal

Glassbox will assess the quality of a recorded decision's reasoning with an explicitly configured LLM judge, but will not use that assessment as a gate until it is calibrated against human labels. The implementation must preserve Glassbox's local-first defaults: no provider is selected implicitly, no provider SDK is a core runtime dependency, and remote data transfer is explicit at the command line.

The source specification's rule remains authoritative: calibrate on at least 30 human-labelled decisions, report judge/human agreement, and refuse to gate when weighted kappa is below 0.60. A calibrated reasoning-quality gate requires an average judge score of at least 3.5 on the 1--5 rubric.

## Human labels

P3 extends the existing append-only `feedback` ledger instead of creating a competing label store. A feedback submission gains an optional `reasoning_quality_score`, an integer from 1 through 5. It is independent of the existing `agree`/`disagree`/`uncertain` verdict and may be omitted during ordinary feedback.

The Decision Card presents this as an optional **Reasoning quality (1--5)** field with concise rubric help. Existing feedback submission, CSRF, Host/Origin, idempotency, and PRG behaviour remain unchanged. On an exact idempotent replay, the score is part of the caller-supplied payload comparison.

Feedback stays historical. For calibration, one label is selected for each decision: the newest feedback row with a non-null score, ordered by `created_at DESC, feedback_id DESC`. Older scored rows remain available for audit and never get overwritten.

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
- ordered rationale citations resolved to their persisted evidence field groups; and
- alternatives considered.

It never receives confidence, planner verdicts, corrected recommendations, free-text feedback, raw blob content, prompt/completion references, or outcome labels. Persisted evidence has already passed Glassbox's redaction boundary, but it may still contain sensitive operational data; sending it to a remote provider is therefore a deliberate egress action.

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

Provider SDKs are optional extras and imported lazily inside their provider adapters. The base package does not install Anthropic, OpenAI, Gemini, or dotenv tooling merely to trace, serve, or run deterministic evaluation. Ollama uses a configured loopback URL by default; it counts as no-egress only when that URL resolves to an explicit loopback host. A non-loopback Ollama URL is treated as a remote destination.

The new command is:

```text
glassbox judge --database PATH [--max-cases N] [--allow-self-judge] [--confirm-egress]
```

Before it constructs an adapter or makes any network call, it validates all configuration, dependencies, database/schema state, calibration candidates, self-judge rules, and `--max-cases`. For every remote destination it prints the provider, model, and destination, then requires `--confirm-egress`. It makes no network request without that acknowledgement. It prints the same destination disclosure for loopback Ollama but does not require confirmation.

`--max-cases` is an optional positive cap for a deliberate bounded run; it makes no claim to be a cost-control system. A future cost/spend policy remains a backlog item.

## Self-judge protection

The command reads all LLM spans associated with each selected decision's trace before judging. It refuses a case when the configured judge model exactly matches a recorded span model. When a span explicitly carries provider identity in `attributes["gen_ai.provider.name"]`, it also treats a matching provider-and-model pair as self-judging. The current canonical span schema has a `model` field but no first-class provider field, so a missing provider never weakens the model-match refusal.

`--allow-self-judge` is the sole explicit bypass. The report and persisted run mark such a bypass. Different models are allowed even if provider provenance was absent; the command reports that limited provenance in its preflight output rather than inventing a provider identity.

## Persistence and calibration gate

P3 adds a strict migration that extends the current schema fingerprint and keeps `schema.sql` byte-for-byte aligned with all migrations. The migration adds `feedback.reasoning_quality_score` with a nullable 1--5 check. It also extends `eval_runs` with nullable judge provenance fields for deterministic runs and required judge provenance for P3 judge runs:

- `judge_provider`;
- `judge_model`;
- `rubric_version`;
- `judge_temperature` (always `0` for this command); and
- `self_judge_allowed`.

The repository owns all SQL and transaction boundaries. A P3 run appends one `eval_runs` row, then one `eval_results` row per judged decision using `assertion_name = "reasoning_quality"`, numeric `score`, and a concise `judge_rationale`. Judge failures are persisted as failed results without a numeric score. Existing deterministic evaluation rows remain valid and may leave the new provenance fields null.

The command reports:

- selected and successfully judged case counts;
- mean judge score;
- number of human-scored decisions;
- linear weighted kappa over the paired 1--5 labels; and
- whether the quality gate is eligible and passed.

Linear weighted kappa is used because adjacent reasoning scores are meaningfully different but less severe than wider disagreement. The reasoning-quality threshold is enforced only when there are at least 30 paired human labels **and** kappa is at least 0.60 for the same judge provider, model, and rubric version. Otherwise the run is reported as uncalibrated and does not claim a quality-gate pass or failure. Once calibrated, a mean score below 3.5 fails the gate. Provider/model/rubric changes start a separate comparability cohort and must be calibrated independently.

## Architecture boundaries

New judge code lives below `glassbox.eval` and may read the store; it has no SDK, collector, or web dependency. Provider SDK imports stay adapter-local. Import-linter will explicitly prevent `glassbox.sdk`, `glassbox.collector`, and `glassbox.web` from importing `glassbox.eval.judge`; the existing `eval` contract continues to prevent `eval` from importing those runtime packages. This makes judging an opt-in command path rather than a tracing or serving dependency.

## Verification

- Schema migration and `schema.sql` mirror tests cover the optional score and judge metadata, including strict constraints and backward compatibility for deterministic runs.
- Repository tests prove score-aware feedback idempotency and newest-scored-label selection per decision.
- Web tests prove the optional score validates 1--5, renders safely, and remains optional.
- Config tests prove project-root-only scoped dotenv parsing, process-environment precedence, no `os.environ` mutation, missing provider/model/credential rejection, and no provider construction before preflight validation.
- Adapter contract tests use fakes to prove each provider's request shape, zero temperature, strict output parsing, failure isolation, and lazy optional imports; live provider calls are not part of CI.
- Judge tests prove confidence and feedback text never enter prompts; citations resolve as field groups; malformed/dangling evidence has a visible per-case failure rather than fabricated context.
- Safety tests prove remote calls require `--confirm-egress`, loopback Ollama does not, non-loopback Ollama does, and model/provider self-judge cases refuse unless explicitly bypassed.
- Calibration tests cover linear weighted kappa, the 30-label floor, the 0.60 agreement floor, cohort separation by provider/model/rubric, and the 3.5 mean-score gate.
- Architecture tests activate and verify the judge-specific import boundaries.

## Deferred work

P3b will design drift monitoring, counterfactual providers, and alert delivery after this calibration path has real labelled data. Bulk automated scheduling and live-provider spend controls are intentionally out of scope for P3a.
