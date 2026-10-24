# Discord Drift Alerts Design

**Date:** 2026-10-24  
**Status:** Approved for implementation planning  
**Scope:** automatic, opt-in Discord incoming-webhook delivery for persisted
`drift_detected` reports only.

## Goal

Notify the operator in one Discord channel when a completed `glassbox drift`
invocation persists a report whose aggregate status is `drift_detected`.
Delivery is an operational side effect of an already-persisted audit record; it
must never change the drift calculation, persistence, or command result.

## Non-goals

- Discord bots, gateway connections, slash commands, or reading Discord data.
- Delivery for `watch`, `healthy`, or insufficient-data reports.
- Scheduling, queued background delivery, CI enforcement, or alert escalation.
- Sending decision data, evidence, rationale, recommendations, feedback, blob
  contents, webhook URLs, response bodies, or exception text to Discord.
- Changing trace collection, the SDK, the collector, web serving, drift policy
  calculation, or baseline materialization.

## Configuration and egress

`DiscordConfig` is loaded per `glassbox drift` invocation from the Glassbox
project-root `.env` and the real process environment. It reads only
`GLASSBOX_DISCORD_*` names into a local configuration object; it must not
mutate `os.environ`. A real process-environment value overrides the `.env`
value.

Delivery is enabled only when both values are present:

- `GLASSBOX_DISCORD_WEBHOOK_URL`: an HTTPS Discord incoming-webhook URL.
- `GLASSBOX_DISCORD_ALLOW_EGRESS=1`: an explicit acknowledgement that the
  compact operational alert may leave the machine.

The URL is a credential. Glassbox never persists, prints, displays, or includes
it in an error. It accepts only `https://discord.com/api/webhooks/...`; any
other host, scheme, path, fragment, credentials component, or malformed value
is an invalid configuration. The sender uses a dedicated no-proxy HTTP
transport, so environment proxy variables cannot redirect a presumed Discord
request elsewhere. This follows the local-shadow transport hardening: a URL
classification is not sufficient when an ambient proxy can change the network
destination.

If either opt-in gate is absent, configuration is invalid, or delivery fails,
the already-persisted drift result remains authoritative and the command
retains its existing exit semantics. Console output is concise and does not
reveal sensitive configuration or remote response detail.

## Delivery flow

After `glassbox drift` has calculated and persisted its immutable run:

1. If the aggregate report status is not `drift_detected`, do nothing.
2. Load and validate `DiscordConfig`; if it is not delivery-ready, record no
   webhook attempt and report the local configuration state without failing
   monitoring.
3. Compute a SHA-256 destination hash of the exact validated webhook URL.
4. Look up persisted delivery history for `(drift_run_id, destination_hash)`.
   If that pair has a successful delivery, skip it. A prior failed attempt is
   eligible for a new bounded attempt on a later `glassbox drift` invocation.
5. Persist a new append-only delivery attempt before sending. Make a JSON POST
   with a short request deadline using the no-proxy transport.
6. Treat any `2xx` response as delivered. For a transport timeout/error, HTTP
   `429`, or `5xx`, make exactly one retry after a capped delay (honouring a
   valid retry indication only within that cap). Other `4xx` responses fail
   once. Finalize the attempt as delivered or failed.

The delivery path is synchronous but strictly bounded: at most two network
requests and one capped wait. It runs only after the report is durable. A
failure cannot roll back, alter, or reclassify the drift run.

## Durable audit record

Add a dedicated append-only `discord_deliveries` table and typed repository
methods. Each row contains an opaque delivery ID, `drift_run_id`, destination
hash, attempt number, status (`delivered` or `failed`), creation/finalization
timestamps, and a normalized non-sensitive error category for failures. A
unique successful-delivery constraint protects `(drift_run_id,
destination_hash)` from duplicate posts, while preserving earlier failed
attempts. The raw URL, request payload, response body, and exception text are
never stored.

The report remains usable even if no delivery table exists in a historical
database until the regular forward migration has run; no read-only endpoint
depends on delivery state.

## Message contract

The JSON webhook payload is a compact operational summary containing only:

- a fixed Glassbox drift-alert title;
- affected agent name;
- drift-run ID, policy version, and policy hash prefix;
- the signals with `drift_detected` status, their metric values, and thresholds;
- report creation time; and
- an optional configured local report URL, validated as loopback before use.

It contains no decision-level or model content. Message construction has fixed
size limits and truncates only already-safe operational strings. The payload is
derived from the persisted drift report, not recomputed from raw telemetry.

## Boundaries

The delivery adapter belongs under `glassbox.eval`, alongside drift
orchestration. It may import store models/repository and standard-library HTTP
facilities. `sdk`, `collector`, and `web` must not import it. Provider or
network dependencies remain optional-free: the implementation uses the Python
standard library and a dependency-injected transport for tests.

The application does not create a Discord bot or background service. Future
scheduling can invoke the same command, and a future bot adapter can reuse the
persisted delivery contract without changing drift calculation.

## Verification

Tests must prove:

- no notification occurs for any status other than `drift_detected`;
- each required gate and URL component is validated without any network call;
- a real local receiver sees only the allowed compact payload;
- proxy environment variables cannot redirect the request;
- `2xx` success, one transient retry, non-retryable failure, and capped retry
  handling behave as specified;
- successful reruns do not repost; failures remain auditable and can retry on a
  later command;
- a delivery failure cannot change a persisted report or the command's drift
  exit status;
- migrations and typed repository constraints preserve append-only audit
  history; and
- import-linter prevents `sdk`, `collector`, and `web` from importing the new
  adapter.

## Deferred work

Discord bot capability, multiple destinations, rate-limit queues, scheduling,
CI gating, escalation policies, and dashboard notifications require separate
designs and approvals.
