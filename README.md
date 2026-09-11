# Glassbox

Glassbox is a local-first Python tracing harness for supply-chain agent decisions.
It provides a non-invasive tracing SDK, bounded fail-open collection, and local
SQLite trace inspection.

## Trace an existing agent

Decorate an agent entry point with `@glassbox.trace`, then configure the SDK
with a local collector. The included helper demonstrates the lifecycle used by
the replenishment agent:

```python
from pathlib import Path

from examples.wrap_existing_agent import configure_tracing, run_with_tracing

collector = configure_tracing(Path("glassbox.sqlite3"))
result = run_with_tracing(agent.run, collector, enriched_exceptions)
```

When tracing is disabled with `GLASSBOX_ENABLED=0`, SDK operations are no-ops
and the wrapped agent result is unchanged. Inspect a saved trace without
modifying its database:

```shell
glassbox --database glassbox.sqlite3 trace <trace-id>
```

Run the P0 overhead acceptance benchmark (30 baseline and 30 instrumented
runs by default) with:

```shell
python benchmarks/p0_overhead.py
```

The benchmark output and the P0 approval status are recorded in
[`docs/p0-ergonomics-report.md`](docs/p0-ergonomics-report.md).

## Capture redacted model content

Content capture is opt-in. Configure a content-addressed blob store, then pass
LLM prompt/completion content to a span or explicitly capture a trace input.
Glassbox applies configured redaction hooks before hashing and storing content;
events retain only the resulting blob references.

```python
from glassbox.store.blobs import BlobStore

glassbox.init(agent="triage", version="1.0", collector=collector, blob_store=BlobStore("blobs"))

with glassbox.span("llm.complete", kind="llm", prompt=prompt, completion=response):
    call_model()
glassbox.capture_input(request_payload)
```

## Run the replenishment evaluation suite

The independently versioned replenishment agent owns its adapter and golden
cases. From that repository, install its evaluation-only dependencies and run:

```shell
python -m pip install -r requirements-eval.txt
glassbox eval --suite goldens/replenishment_triage/manifest.yaml
```

## Run the local planner server

The P2.1 server is a loopback-only security foundation. Set
`GLASSBOX_LOCAL_ACCESS_TOKEN` in the process environment to a secret of at
least 32 characters, then start it:

```shell
export GLASSBOX_LOCAL_ACCESS_TOKEN='replace-with-a-secret-from-your-existing-secret-tool'
glassbox serve --port 8787
```

Open `http://127.0.0.1:8787/login` and enter the same token. Glassbox does not
load `.env` files or bind the server to a network interface. Set
`GLASSBOX_DATABASE` when the database is not `glassbox.sqlite3` in the current
directory. The server verifies that an existing database has the current schema
before it binds. After login, `/` lists decisions, `/decision/<id>` shows a
Decision Card, and `/trace/<id>` shows trace timing data. Each Decision Card
includes an authenticated append-only feedback ledger. Feedback
records planner assessment (`agree`, `disagree`, or `uncertain`) for later P3
calibration; it does not modify a decision or create an operational override.

Set `GLASSBOX_OPERATOR_NAME` to record an accountability label on operational
accept, modify, and reject actions; it defaults to `local-planner`. Overrides
are append-only: each new action supersedes the current action for that
decision, while a modified action records its corrected recommendation JSON.

## Export one Decision Card

Write a self-contained, read-only HTML snapshot without starting the server:

```shell
glassbox --database glassbox.sqlite3 export --decision <decision-id> --output decision-card.html
```

Use `--overwrite` to replace an existing artifact. Optionally add
`--live-base-url http://127.0.0.1:8787` to include a link back to the live
card. Exports contain inline CSS and no JavaScript, session state, or feedback
form; feedback and operational actions remain available only in the live app.

## Development

Use Python 3.11 or newer, then install the development extras and run the checks:

```shell
python -m pip install -e '.[dev]'
pytest --import-mode=importlib -q
ruff check .
mypy glassbox
lint-imports
```
