# Glassbox P2.1 Security Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the local-only, token-gated web-server foundation that safely precedes the P2 decision screens and feedback workflow.

**Architecture:** Add a small `glassbox.web` package. `server.py` owns configuration validation, the FastAPI application factory, CLI startup, and the session guard; `auth.py` owns constant-time token comparison plus an in-memory, thread-safe session store. The only rendered screen in P2.1 is login; an authenticated root placeholder proves the guard without building P2.2’s planner queue.

**Tech Stack:** Python 3.11+, FastAPI, plain Uvicorn, Jinja2, python-multipart, httpx (development tests), pytest, Ruff, mypy, import-linter.

## Global Constraints

- Require Python `>=3.11`; keep Glassbox local-first with no external service or network egress.
- Read `GLASSBOX_LOCAL_ACCESS_TOKEN` only through `os.environ`; do not parse `.env`, accept an env-file option, or add `python-dotenv`.
- Reject a missing or fewer-than-32-character access token before a listener is opened; never log, persist, render, or place the configured token in a URL.
- Permit only exact loopback hosts `127.0.0.1` and `::1`; default to `127.0.0.1`; reject wildcard, unspecified, hostname, and other network addresses before calling Uvicorn.
- Keep the server single-process and local; use plain `uvicorn`, never `uvicorn[standard]`, reload mode, or worker mode.
- Sessions are opaque, server-side, in-memory, and expire after 30 minutes of inactivity; process restart invalidates them.
- Set only a path-scoped `HttpOnly`, `SameSite=Strict` session cookie. Deliberately omit `Secure`, because P2.1 serves loopback HTTP.
- `/login` and `/health` are public. All other P2.1 routes require a session and redirect unauthenticated callers to `/login`; `/health` exposes only `{"status": "ok"}`.
- `glassbox.web` may later import `store` and `explain`, but must never import `sdk`; activate and test the import-linter contract now.
- Do not build the decision queue, decision card, trace view, feedback storage, static export, HTMX, Host/Origin checks, CSRF POST enforcement, or persistent sessions. Those remain P2.2–P2.4 work.

---

## File structure

| Path | Responsibility |
| --- | --- |
| `pyproject.toml` | Runtime and test-only dependency declarations; active `web-dependencies` boundary. |
| `glassbox/web/__init__.py` | Keeps the web package importable without exporting SDK symbols. |
| `glassbox/web/auth.py` | Constant-time token comparison, opaque session records, clock-injectable in-memory session store. |
| `glassbox/web/server.py` | Validated startup configuration, FastAPI app factory, route guard, login and health routes, Uvicorn launcher. |
| `glassbox/web/templates/login.html` | Escaped local-token login form with generic authentication failure rendering. |
| `tests/web/test_auth.py` | Unit tests for token and session behavior without HTTP. |
| `tests/web/test_server.py` | Configuration, no-bind-before-validation, CLI, cookie, login, guard, health, and idle-expiry tests. |
| `tests/test_architecture.py` | Package metadata and import-linter contract regression checks. |
| `tests/test_cli.py` | Extends CLI coverage only if the existing file is preferred for the `serve` parser path. |
| `README.md` | Documents safe environment-only startup and its loopback URL. |
| `TODO.md` | Marks only P2.1 planning and loopback verification checks that have execution evidence. |

## Task 1: Package scaffold, dependencies, and validated server startup

**Files:**
- Create: `glassbox/web/__init__.py`
- Create: `glassbox/web/server.py`
- Create: `tests/web/test_server.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_architecture.py`
- Modify: `glassbox/cli.py`

**Interfaces:**
- Produces `ServerConfig(host: str, port: int, access_token: str, session_idle_timeout: timedelta)`.
- Produces `load_server_config(*, host: str, port: int, environ: Mapping[str, str] | None = None, session_idle_timeout: timedelta = timedelta(minutes=30)) -> ServerConfig`.
- Produces `create_app(config: ServerConfig, *, clock: Callable[[], datetime] = utc_now) -> FastAPI` and `run_server(*, host: str, port: int, environ: Mapping[str, str] | None = None) -> None`.
- Consumes only the process environment and does not open SQLite or import `glassbox.sdk`.

- [ ] **Step 1: Verify the target machine can bind both required loopback interfaces before adding web code.**

  Run:

  ```shell
  python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()); s.close()'
  python -c 'import socket; s = socket.socket(socket.AF_INET6); s.bind(("::1", 0)); print(s.getsockname()); s.close()'
  ```

  Expected: both commands print a loopback address and ephemeral port, with no listener left open.

- [ ] **Step 2: Write failing metadata, boundary, configuration, and no-bind tests.**

  Add the web dependency expectations to `tests/test_architecture.py` and create `tests/web/test_server.py`:

  ```python
  from datetime import timedelta

  import pytest

  from glassbox.web.server import load_server_config, run_server


  TOKEN = "t" * 32


  @pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
  def test_load_server_config_accepts_exact_loopback_hosts(host: str) -> None:
      config = load_server_config(host=host, port=8787, environ={"GLASSBOX_LOCAL_ACCESS_TOKEN": TOKEN})
      assert config.host == host


  @pytest.mark.parametrize("host", ["0.0.0.0", "::", "localhost", "192.168.1.8"])
  def test_invalid_host_fails_before_uvicorn_is_called(
      host: str, monkeypatch: pytest.MonkeyPatch
  ) -> None:
      called = False

      def unexpected_run(*args: object, **kwargs: object) -> None:
          nonlocal called
          called = True

      monkeypatch.setattr("glassbox.web.server.uvicorn.run", unexpected_run)
      with pytest.raises(ValueError, match="loopback"):
          run_server(host=host, port=8787, environ={"GLASSBOX_LOCAL_ACCESS_TOKEN": TOKEN})
      assert called is False


  @pytest.mark.parametrize("token", [None, "", "x" * 31])
  def test_missing_or_short_token_fails_before_startup(token: str | None) -> None:
      environ = {} if token is None else {"GLASSBOX_LOCAL_ACCESS_TOKEN": token}
      with pytest.raises(ValueError, match="GLASSBOX_LOCAL_ACCESS_TOKEN"):
          load_server_config(host="127.0.0.1", port=8787, environ=environ)


  def test_non_positive_session_timeout_is_rejected() -> None:
      with pytest.raises(ValueError, match="session_idle_timeout"):
          load_server_config(
              host="127.0.0.1",
              port=8787,
              environ={"GLASSBOX_LOCAL_ACCESS_TOKEN": TOKEN},
              session_idle_timeout=timedelta(),
          )


  def test_serve_command_dispatches_validated_arguments(
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      from glassbox import cli

      received: dict[str, object] = {}

      def fake_run_server(*, host: str, port: int) -> None:
          received.update(host=host, port=port)

      monkeypatch.setattr(cli, "run_server", fake_run_server)
      assert cli.main(["serve", "--host", "::1", "--port", "8788"]) == 0
      assert received == {"host": "::1", "port": 8788}
  ```

  Make `test_package_metadata_declares_required_quality_tools()` require the four runtime strings `fastapi`, `uvicorn`, `jinja2`, and `python-multipart`, add `httpx` to the `dev` expected set, include a `web-dependencies` entry in `expected`, and update the `contracts_options` count from `5` to `6`.

- [ ] **Step 3: Run the focused tests and confirm they fail because the web package and dependencies are absent.**

  Run: `pytest --import-mode=importlib tests/web/test_server.py tests/test_architecture.py -q`

  Expected: collection fails with `ModuleNotFoundError: No module named 'glassbox.web'` or metadata assertions fail.

- [ ] **Step 4: Add only the declared dependencies and activate the web boundary.**

  Change the relevant `pyproject.toml` sections to:

  ```toml
  dependencies = [
    "pydantic>=2.0",
    "PyYAML>=6.0",
    "jsonschema>=4.0",
    "fastapi>=0.110",
    "uvicorn>=0.27",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
  ]

  [project.optional-dependencies]
  dev = [
    "httpx>=0.27",
    "import-linter>=2.0",
    "mypy>=1.0",
    "pytest>=8.0",
    "ruff>=0.6",
    "types-jsonschema>=4.0",
    "types-PyYAML>=6.0",
  ]

  [[tool.importlinter.contracts]]
  name = "web-dependencies"
  type = "forbidden"
  source_modules = ["glassbox.web"]
  forbidden_modules = ["glassbox.sdk"]
  ```

  Do not use a `[standard]` extra for FastAPI or Uvicorn, and do not add `python-dotenv`.

- [ ] **Step 5: Implement the package shell and configuration validation.**

  Create `glassbox/web/__init__.py` with only a module docstring. Create `glassbox/web/server.py` with these public definitions; defer route registration to Task 3:

  ```python
  from __future__ import annotations

  import os
  from collections.abc import Callable, Mapping
  from dataclasses import dataclass
  from datetime import UTC, datetime, timedelta

  import uvicorn
  from fastapi import FastAPI

  _LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
  _MIN_TOKEN_LENGTH = 32


  def utc_now() -> datetime:
      return datetime.now(UTC)


  @dataclass(frozen=True)
  class ServerConfig:
      host: str
      port: int
      access_token: str
      session_idle_timeout: timedelta = timedelta(minutes=30)


  def load_server_config(
      *,
      host: str = "127.0.0.1",
      port: int = 8787,
      environ: Mapping[str, str] | None = None,
      session_idle_timeout: timedelta = timedelta(minutes=30),
  ) -> ServerConfig:
      source = os.environ if environ is None else environ
      token = source.get("GLASSBOX_LOCAL_ACCESS_TOKEN", "")
      if host not in _LOOPBACK_HOSTS:
          raise ValueError("host must be exactly 127.0.0.1 or ::1 (loopback only)")
      if not 1 <= port <= 65_535:
          raise ValueError("port must be between 1 and 65535")
      if len(token) < _MIN_TOKEN_LENGTH:
          raise ValueError("GLASSBOX_LOCAL_ACCESS_TOKEN must contain at least 32 characters")
      if session_idle_timeout <= timedelta():
          raise ValueError("session_idle_timeout must be positive")
      return ServerConfig(host, port, token, session_idle_timeout)


  def create_app(config: ServerConfig, *, clock: Callable[[], datetime] = utc_now) -> FastAPI:
      del config, clock
      return FastAPI()


  def run_server(
      *, host: str = "127.0.0.1", port: int = 8787, environ: Mapping[str, str] | None = None
  ) -> None:
      config = load_server_config(host=host, port=port, environ=environ)
      uvicorn.run(create_app(config), host=config.host, port=config.port)
  ```

  Extend `glassbox/cli.py` with a `serve` parser carrying `--host` (default
  `127.0.0.1`) and `--port` (integer, default `8787`). Import `run_server`
  from `glassbox.web.server` at module scope so the test can replace it, then
  dispatch before the existing trace-database branch:

  ```python
  serve_command = commands.add_parser("serve", help="run the local Glassbox web server")
  serve_command.add_argument("--host", default="127.0.0.1")
  serve_command.add_argument("--port", type=int, default=8787)
  # ... after parse_args(...)
  if arguments.command == "serve":
      try:
          run_server(host=arguments.host, port=arguments.port)
      except ValueError as exc:
          print(f"glassbox: unable to start server: {exc}", file=sys.stderr)
          return 2
      return 0
  ```

- [ ] **Step 6: Install the edited package and run configuration, CLI, architecture, type, and import-boundary checks.**

  Run:

  ```shell
  python -m pip install -e '.[dev]'
  pytest --import-mode=importlib tests/web/test_server.py tests/test_architecture.py tests/test_cli.py -q
  ruff check glassbox tests
  mypy glassbox
  lint-imports
  ```

  Expected: all checks pass, and `lint-imports` reports six kept contracts.

- [ ] **Step 7: Commit the independently testable foundation.**

  ```shell
  git add pyproject.toml glassbox/cli.py glassbox/web/__init__.py glassbox/web/server.py tests/web/test_server.py tests/test_architecture.py tests/test_cli.py
  git commit -m "feat: add local web server foundation"
  ```

## Task 2: Server-side authentication and session primitives

**Files:**
- Create: `glassbox/web/auth.py`
- Create: `tests/web/test_auth.py`
- Modify: `glassbox/web/server.py`

**Interfaces:**
- Produces `SESSION_COOKIE_NAME = "glassbox_session"`.
- Produces `Session(session_id: str, csrf_token: str, last_activity: datetime)`.
- Produces `SessionStore(idle_timeout: timedelta, *, clock: Callable[[], datetime])` with `create() -> Session`, `get(session_id: str) -> Session | None`, and `touch(session_id: str) -> Session | None`.
- Produces `token_matches(submitted: str, configured: str) -> bool` using `hmac.compare_digest`.
- `server.create_app()` consumes these interfaces but still does not touch `store` or `sdk`.

- [ ] **Step 1: Write failing unit tests with a deterministic clock.**

  Create `tests/web/test_auth.py`:

  ```python
  from datetime import UTC, datetime, timedelta

  from glassbox.web.auth import SessionStore, token_matches


  class Clock:
      def __init__(self) -> None:
          self.value = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

      def __call__(self) -> datetime:
          return self.value


  def test_token_matches_only_the_configured_value() -> None:
      assert token_matches("a" * 32, "a" * 32) is True
      assert token_matches("b" * 32, "a" * 32) is False


  def test_session_is_opaque_and_has_a_csrf_token() -> None:
      session = SessionStore(timedelta(minutes=30), clock=Clock()).create()
      assert session.session_id != session.csrf_token
      assert len(session.session_id) >= 32
      assert len(session.csrf_token) >= 32


  def test_expired_session_is_removed_and_not_returned() -> None:
      clock = Clock()
      store = SessionStore(timedelta(minutes=30), clock=clock)
      session = store.create()
      clock.value += timedelta(minutes=30, seconds=1)
      assert store.get(session.session_id) is None
      assert store.get(session.session_id) is None


  def test_touch_refreshes_activity_only_for_an_existing_session() -> None:
      clock = Clock()
      store = SessionStore(timedelta(minutes=30), clock=clock)
      session = store.create()
      clock.value += timedelta(minutes=29)
      refreshed = store.touch(session.session_id)
      assert refreshed is not None
      clock.value += timedelta(minutes=29)
      assert store.get(session.session_id) is not None
      assert store.touch("malformed") is None
  ```

- [ ] **Step 2: Run the unit test to confirm the missing auth module fails.**

  Run: `pytest --import-mode=importlib tests/web/test_auth.py -q`

  Expected: collection fails with `ModuleNotFoundError: No module named 'glassbox.web.auth'`.

- [ ] **Step 3: Implement a thread-safe, in-memory session store.**

  Create `glassbox/web/auth.py` with the following behavior:

  ```python
  from __future__ import annotations

  import hmac
  import secrets
  from collections.abc import Callable
  from dataclasses import dataclass
  from datetime import datetime, timedelta
  from threading import RLock

  SESSION_COOKIE_NAME = "glassbox_session"


  @dataclass(frozen=True)
  class Session:
      session_id: str
      csrf_token: str
      last_activity: datetime


  def token_matches(submitted: str, configured: str) -> bool:
      return hmac.compare_digest(submitted, configured)


  class SessionStore:
      def __init__(self, idle_timeout: timedelta, *, clock: Callable[[], datetime]) -> None:
          self._idle_timeout = idle_timeout
          self._clock = clock
          self._sessions: dict[str, Session] = {}
          self._lock = RLock()

      def create(self) -> Session:
          now = self._clock()
          session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32), now)
          with self._lock:
              self._sessions[session.session_id] = session
          return session

      def get(self, session_id: str) -> Session | None:
          with self._lock:
              session = self._sessions.get(session_id)
              if session is None:
                  return None
              if self._clock() - session.last_activity >= self._idle_timeout:
                  del self._sessions[session_id]
                  return None
              return session

      def touch(self, session_id: str) -> Session | None:
          session = self.get(session_id)
          if session is None:
              return None
          refreshed = Session(session.session_id, session.csrf_token, self._clock())
          with self._lock:
              self._sessions[session_id] = refreshed
          return refreshed
  ```

  Retain the store as `app.state.sessions` when Task 3 wires `create_app`; do not provide persistence, cross-process sharing, token lookup, or token logging.

- [ ] **Step 4: Run the focused auth tests and static checks.**

  Run:

  ```shell
  pytest --import-mode=importlib tests/web/test_auth.py -q
  ruff check glassbox/web tests/web
  mypy glassbox/web
  ```

  Expected: all pass.

- [ ] **Step 5: Commit the session primitive.**

  ```shell
  git add glassbox/web/auth.py tests/web/test_auth.py glassbox/web/server.py
  git commit -m "feat: add local web sessions"
  ```

## Task 3: Login, protected route guard, cookie policy, and health endpoint

**Files:**
- Create: `glassbox/web/templates/login.html`
- Modify: `glassbox/web/server.py`
- Modify: `tests/web/test_server.py`

**Interfaces:**
- `create_app(config, clock=...)` returns an app whose `app.state.sessions` is the Task 2 `SessionStore`.
- `GET /login` returns the login form; `POST /login` consumes `access_token: Annotated[str, Form()]`.
- `GET /health` returns only `{"status": "ok"}` without authentication.
- All other routes, including the temporary `GET /` baseline endpoint, run through the session middleware. P2.2 replaces that temporary endpoint with the decision queue.

- [ ] **Step 1: Add failing HTTP-level tests using FastAPI’s `TestClient`.**

  Append to `tests/web/test_server.py`:

  ```python
  from datetime import UTC, datetime, timedelta

  from fastapi.testclient import TestClient

  from glassbox.web.server import create_app, load_server_config


  class Clock:
      def __init__(self) -> None:
          self.value = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

      def __call__(self) -> datetime:
          return self.value


  def app_for(clock: Clock):
      config = load_server_config(
          environ={"GLASSBOX_LOCAL_ACCESS_TOKEN": "t" * 32}, host="127.0.0.1", port=8787
      )
      return create_app(config, clock=clock)


  def test_health_is_public_and_contains_no_decision_data() -> None:
      response = TestClient(app_for(Clock()), base_url="http://127.0.0.1").get("/health")
      assert response.status_code == 200
      assert response.json() == {"status": "ok"}


  def test_failed_login_is_generic_and_does_not_set_a_cookie() -> None:
      response = TestClient(app_for(Clock()), base_url="http://127.0.0.1").post(
          "/login", data={"access_token": "wrong"}
      )
      assert response.status_code == 401
      assert "glassbox_session" not in response.headers.get("set-cookie", "")
      assert "wrong" not in response.text


  def test_successful_login_sets_the_intended_cookie_attributes() -> None:
      client = TestClient(app_for(Clock()), base_url="http://127.0.0.1")
      response = client.post("/login", data={"access_token": "t" * 32}, follow_redirects=False)
      cookie = response.headers["set-cookie"]
      assert response.status_code == 303
      assert "HttpOnly" in cookie
      assert "SameSite=strict" in cookie
      assert "Path=/" in cookie
      assert "Secure" not in cookie
      assert "t" * 32 not in cookie


  def test_protected_route_redirects_without_session_and_refreshes_with_session() -> None:
      clock = Clock()
      client = TestClient(app_for(clock), base_url="http://127.0.0.1")
      assert client.get("/", follow_redirects=False).headers["location"] == "/login"
      client.post("/login", data={"access_token": "t" * 32})
      clock.value += timedelta(minutes=29)
      assert client.get("/").status_code == 503
      clock.value += timedelta(minutes=29)
      assert client.get("/", follow_redirects=False).status_code == 503


  def test_idle_session_is_rejected_after_its_timeout() -> None:
      clock = Clock()
      client = TestClient(app_for(clock), base_url="http://127.0.0.1")
      client.post("/login", data={"access_token": "t" * 32})
      clock.value += timedelta(minutes=30, seconds=1)
      response = client.get("/", follow_redirects=False)
      assert response.status_code == 303
      assert response.headers["location"] == "/login"
  ```

- [ ] **Step 2: Run the route tests and confirm they fail because routes and templates are not registered.**

  Run: `pytest --import-mode=importlib tests/web/test_server.py -q`

  Expected: login, health, cookie, and guard assertions fail against the empty Task 1 app.

- [ ] **Step 3: Implement the login template and complete the app factory.**

  Create `glassbox/web/templates/login.html`:

  ```html
  <!doctype html>
  <html lang="en">
    <head><meta charset="utf-8"><title>Glassbox local access</title></head>
    <body>
      <main>
        <h1>Glassbox local access</h1>
        {% if error %}<p role="alert">Access denied.</p>{% endif %}
        <form action="/login" method="post">
          <label for="access-token">Local access token</label>
          <input id="access-token" name="access_token" type="password" autocomplete="current-password" required>
          <button type="submit">Continue</button>
        </form>
      </main>
    </body>
  </html>
  ```

  Replace the Task 1 placeholder `create_app()` body with these route semantics:

  ```python
  templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
  sessions = SessionStore(config.session_idle_timeout, clock=clock)
  app = FastAPI()
  app.state.sessions = sessions

  @app.middleware("http")
  async def require_session(request: Request, call_next: RequestResponseEndpoint) -> Response:
      if request.url.path in {"/login", "/health"}:
          return await call_next(request)
      session_id = request.cookies.get(SESSION_COOKIE_NAME, "")
      session = sessions.get(session_id)
      if session is None:
          return RedirectResponse("/login", status_code=303)
      request.state.csrf_token = session.csrf_token
      response = await call_next(request)
      if response.status_code < 400:
          sessions.touch(session_id)
      return response
  ```

  Implement `GET /login` as a `TemplateResponse` with `error=False`. Implement `POST /login` with `token_matches(access_token, config.access_token)`: on mismatch render the same template with `error=True` and `401`; on success create a session, return a `303` redirect to `/`, and call `set_cookie(SESSION_COOKIE_NAME, session.session_id, httponly=True, samesite="strict", path="/")` without `secure=True`. Implement `GET /health` as `JSONResponse({"status": "ok"})`. Implement the authenticated root placeholder as `PlainTextResponse("Planner views are not available yet.", status_code=503)`.

  Keep the configured token out of all response content, logs, session records, URLs, and error messages. The only per-session value exposed to subsequent templates is `request.state.csrf_token`; P2.3 will consume it for feedback forms.

- [ ] **Step 4: Run targeted tests and prove a `web -> sdk` violation is rejected.**

  Run:

  ```shell
  pytest --import-mode=importlib tests/web/test_auth.py tests/web/test_server.py tests/test_cli.py tests/test_architecture.py -q
  ruff check glassbox tests
  mypy glassbox
  lint-imports
  ```

  Expected: all pass. Then temporarily add `from glassbox import trace` to `glassbox/web/server.py`, run `lint-imports` and confirm that it fails on `web-dependencies`, remove that temporary import, and rerun `lint-imports` successfully.

- [ ] **Step 5: Commit the complete P2.1 security shell.**

  ```shell
  git add glassbox/web/server.py glassbox/web/templates/login.html tests/web/test_server.py
  git commit -m "feat: add local web authentication"
  ```

## Task 4: Operator documentation, evidence recording, and final verification

**Files:**
- Modify: `README.md`
- Modify: `TODO.md`
- Modify: `docs/superpowers/specs/2026-09-03-glassbox-p2-security-design.md` only if implementation reveals a factual discrepancy

**Interfaces:**
- Documents `GLASSBOX_LOCAL_ACCESS_TOKEN` as an operator-supplied process variable and `glassbox serve --port 8787` as the only P2.1 startup command.
- Does not document `.env` loading, remote hosts, `--env-file`, a `Secure` cookie, decision views, or feedback as available features.

- [ ] **Step 1: Write the documentation acceptance assertions as a checklist before editing prose.**

  Confirm each proposed README sentence satisfies all of the following:

  ```text
  [ ] Names GLASSBOX_LOCAL_ACCESS_TOKEN.
  [ ] States that the value comes from the process environment.
  [ ] States the 32-character minimum without printing an example secret.
  [ ] Shows glassbox serve --port 8787.
  [ ] States the service is loopback-only and opens at http://127.0.0.1:8787/login.
  [ ] Does not promise the P2.2 queue/card, P2.3 feedback, or P2.4 export.
  ```

- [ ] **Step 2: Update the README with a minimal operator section.**

  Add this section before `## Development`:

  ```markdown
  ## Run the local planner server

  The P2.1 server is a loopback-only security foundation. Set a local access
  token in the process environment (at least 32 characters), then start it:

  ```shell
  export GLASSBOX_LOCAL_ACCESS_TOKEN='replace-with-a-secret-from-your-existing-secret-tool'
  glassbox serve --port 8787
  ```

  Open `http://127.0.0.1:8787/login` and enter the same token. Glassbox does
  not load `.env` files or bind the server to a network interface. Planner
  views and feedback are introduced in later P2 work.
  ```

- [ ] **Step 3: Record implementation evidence in the P2.1 TODO items.**

  Replace the two P2.1 unchecked items with:

  ```markdown
  - [x] Verify loopback port binding before implementation (both `127.0.0.1` and `::1` bound successfully on 2026-09-04).
  - [x] Refine P2 into a security- and usability-tested implementation plan (approved design: `docs/superpowers/specs/2026-09-03-glassbox-p2-security-design.md`; implementation plan: `docs/superpowers/plans/2026-09-04-glassbox-p2-security-baseline.md`).
  ```

  Do not mark P2.2, P2.3, or P2.4 complete. If Task 1’s IPv6 preflight fails because the operator has disabled IPv6, leave the first item unchecked and report that the design’s IPv6 acceptance test remains implemented but cannot be operationally verified on this host.

- [ ] **Step 4: Run the full verification suite.**

  Run:

  ```shell
  pytest --import-mode=importlib -q
  ruff check .
  mypy glassbox
  lint-imports
  glassbox serve --host 0.0.0.0 --port 8787
  ```

  Expected: all four quality commands pass; `lint-imports` reports six kept contracts; the final command exits `2` before opening a listener and prints an inability-to-start message mentioning loopback-only binding. Run the normal server manually with a temporary process environment token, load `/health`, complete a login, and stop it without recording the token in output or source control.

- [ ] **Step 5: Commit documentation and P2.1 planning evidence.**

  ```shell
  git add README.md TODO.md docs/superpowers/specs/2026-09-03-glassbox-p2-security-design.md
  git commit -m "docs: document local web server security"
  ```

## Self-review

- **Spec coverage:** Task 1 covers exact loopback validation, process-environment token loading, before-bind failure, CLI startup, dependency declaration, and the active web boundary. Task 2 covers constant-time token comparison plus opaque, in-memory, expiring sessions. Task 3 covers `/login`, `/health`, cookie attributes, session refresh/expiry, generic failed authentication, authenticated template CSRF exposure, and protected-route redirects. Task 4 covers the required loopback preflight, operator documentation, TODO evidence, and full quality gate. The explicitly deferred P2.2–P2.4 screens and mutations remain excluded.
- **Placeholder scan:** This plan has no unspecified implementation directives. The only temporary root response is intentionally specified as a 503 P2.1 security-shell endpoint and is replaced by the P2.2 queue.
- **Type consistency:** `ServerConfig`, `load_server_config`, `create_app`, `run_server`, `SessionStore`, `Session`, `token_matches`, and `SESSION_COOKIE_NAME` are introduced before their consuming tasks and use the same signatures throughout.
