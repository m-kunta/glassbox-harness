# macOS Drift Scheduler Design

**Date:** 2026-10-31  
**Status:** Approved for implementation planning  
**Scope:** opt-in, per-user macOS `launchd` scheduling for the existing
`glassbox drift` command.

## Goal

Let a local Glassbox operator install, inspect, and remove one daily drift
monitoring job without creating a daemon, changing drift calculation, or
requiring GitHub-hosted infrastructure.

## Non-goals

- Linux or Windows scheduler support.
- GitHub Actions, CI enforcement, cron deployment, or a hosted database.
- A background Python process, queue, scheduler loop, or web-server-owned job.
- New drift algorithms, policies, baseline materialization, or alert rules.
- Automatic installation, automatic retries, overlapping executions, or an
  alert escalation policy.

## Commands and schedule

Add a `glassbox schedule` command group with three macOS-only subcommands:

```text
glassbox schedule install-drift \
  --database /absolute/path/glassbox.sqlite3 \
  --agent replenishment-triage-ai \
  --policy /absolute/path/drift_v2.toml

glassbox schedule status-drift
glassbox schedule remove-drift
```

`install-drift` writes the per-user LaunchAgent
`~/Library/LaunchAgents/com.glassbox.drift-monitor.plist`, with label
`com.glassbox.drift-monitor`. It runs once each day at **09:00 in the
operator's local timezone** using `StartCalendarInterval` and invokes the
installed `glassbox` executable with the supplied absolute database, agent,
and policy paths. It sets `ProcessType=Background`, does not set
`RunAtLoad`, and does not set `KeepAlive`; a missed machine-offline run waits
until the next scheduled calendar time.

The scheduled child runs the ordinary equivalent of:

```text
<resolved-glassbox-executable> drift --database <database> --agent <agent> --policy <policy>
```

It may use the existing project-root `.env` as the ordinary CLI does. The
plist contains no token, webhook URL, credential, `.env` content, or other
secret. Existing post-persistence Discord delivery remains the only alert path:
it happens only if the already configured drift command persists
`drift_detected`.

## Installation and ownership

`install-drift` accepts only existing regular database and policy files and
requires absolute paths. It validates the policy with the same parser used by
the drift command before changing any scheduler state. It resolves the actual
`glassbox` executable from `sys.executable` / the installed entry point rather
than relying on `PATH` at launch time.

The generated plist carries a Glassbox ownership marker in its comment and the
fixed job label. Installation proceeds as follows:

1. Render a complete plist in memory.
2. If the target path exists and is not a valid Glassbox-owned plist, refuse
   without touching it.
3. If it is an owned plist, refuse unless `--replace` is supplied.
4. When replacing, unload the existing known-owned job first; if unloading
   fails, leave the existing plist unchanged and return an operational error.
5. Write the new plist to a same-directory temporary file with mode `0600`,
   fsync it, then atomically replace the managed path and run `launchctl
   bootstrap gui/<uid> <plist-path>`.
6. If bootstrap fails, remove only the newly written managed plist and report a
   generic install failure. Never remove a foreign file.

No scheduling state is stored in SQLite. The marked plist is the source of
truth and survives terminal sessions and user logout.

## Status, logs, and removal

The job writes stdout and stderr to:

```text
~/Library/Logs/Glassbox/drift-monitor.stdout.log
~/Library/Logs/Glassbox/drift-monitor.stderr.log
```

Before each invocation, a small wrapper rotates each log at 1 MiB, retaining
one `.1` prior file. The wrapper redirects only process output; it does not
echo arguments, environment values, or file contents. Glassbox's normal error
discipline still governs command output.

`status-drift` does not execute drift. It reads and validates the managed plist
and queries `launchctl print gui/<uid>/com.glassbox.drift-monitor`. It reports
one of `not_installed`, `installed_not_loaded`, `loaded`, or `unavailable`,
along with the configured daily time and log file locations. It never displays
plist arguments containing a credential, `.env` values, webhook URLs, or raw
`launchctl` exception text.

`remove-drift` requires the exact valid Glassbox ownership marker. It unloads
the job if loaded, then removes the plist. It leaves the database, drift runs,
baselines, delivery history, `.env`, policies, and logs intact. A foreign or
malformed plist is refused and remains untouched.

## Error handling and exits

All three subcommands use the established CLI convention: exit `0` after the
requested operation completes, and exit `2` for invalid input, unsupported
platform, malformed/foreign scheduler state, `launchctl` failure, or filesystem
failure. They do not expose raw paths from exceptions, tokens, webhook URLs, or
command output.

Scheduling does not alter `glassbox drift` exit semantics. A daily drift report
that calculates successfully still exits normally even when its optional
Discord delivery is disabled or fails. Launchd records the command's process
status in its own service state; Glassbox does not infer or publish a separate
quality gate from scheduler failures.

## Architecture boundaries

Create a scheduler-focused module under `glassbox/ops/`:

- `glassbox.ops.scheduler` owns platform checks, ownership validation, plist
  rendering, atomic file operations, log-wrapper rendering, and a
  dependency-injected `launchctl` runner.
- `glassbox.cli` parses `schedule` subcommands and prints only safe structured
  status/error summaries.

The scheduler module may use only the standard library and the existing drift
policy parser for preflight validation. It must not import `sdk`, `collector`,
`web`, any Discord delivery module, or a provider SDK. Add import-linter
contracts preventing `sdk`, `collector`, `web`, and `glassbox.eval` from
importing `glassbox.ops.scheduler`; `cli` is the sole command boundary.

## Verification

Tests use temporary directories and an injected fake `launchctl` runner; they
never bootstrap a real user job. They prove:

- the generated plist has the fixed label, daily 09:00 local interval, absolute
  executable/database/agent/policy arguments, background behavior, and no
  `RunAtLoad` or `KeepAlive`;
- both preflight files and the policy parser are validated before any write or
  runner call;
- install, owned replacement, bootstrap rollback, status, and removal have the
  exact safe state transitions;
- foreign/malformed plists cannot be replaced, unloaded, or deleted;
- file modes, atomic replacement, wrapper log rotation, and safe log paths are
  correct;
- status/error output and generated plist content contain no secret fixture;
- unsupported platforms return the established operational exit code without
  writing or calling `launchctl`; and
- import-linter enforces scheduler isolation.

Operator documentation covers macOS scope, explicit install/replace/status/
remove commands, daily timing, log locations/rotation, missed-run behavior,
and the fact that GitHub Actions and non-macOS support remain deferred.

## Deferred work

Linux systemd timers, Windows Task Scheduler, custom cadence/timezones,
calendar editing, automatic retry/escalation, service-health alerts, dashboard
scheduler status, GitHub Actions, and CI gating require separate approved
designs.
