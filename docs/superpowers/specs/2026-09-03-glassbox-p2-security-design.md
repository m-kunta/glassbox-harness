# Glassbox P2.1 local web security design

## Purpose

P2 introduces a local planner-facing web application without exposing decision
data to the network. This design establishes the security boundary before the
Decision Queue, Decision Card, trace view, or feedback routes are implemented.

## Scope

P2.1 creates the `glassbox.web` package's server, authentication, session, and
route-guard foundations. It does not create the planner screens, feedback
storage, or static exports; those are P2.2–P2.4 work.

## Architecture

The application uses FastAPI with Jinja templates. Its public startup API
accepts a host and port, but permits only IPv4 loopback (`127.0.0.1`) and IPv6
loopback (`::1`). It rejects wildcard, unspecified, and all non-loopback hosts
before a listener is opened. The default is `127.0.0.1`.

Startup reads `GLASSBOX_LOCAL_ACCESS_TOKEN` from the project's `.env` through
the existing configuration-loading convention. Missing, empty, and
placeholder-looking tokens are startup errors. The token is never rendered,
logged, stored in the database, or included in a URL.

`GET /login` renders a local token form. `POST /login` compares the submitted
token with the configured token using a constant-time comparison. A successful
login creates a random server-side session and returns an `HttpOnly`,
`SameSite=Strict`, path-scoped cookie. Sessions expire after 30 minutes of
inactivity and are kept only in process memory; server restart invalidates all
sessions.

All application routes require a valid session except `/login` and an
unauthenticated `/health` endpoint that returns only service status. The route
guard refreshes session activity after successful authenticated requests.

The session record also contains a random CSRF token. P2.1 exposes the token to
templates only after authentication; P2.3 will require it for every feedback
POST. Authentication errors return the login page or redirect to it, never a
token-specific diagnostic.

## Components and boundaries

- `glassbox.web.server`: startup configuration, loopback validation, FastAPI
  application factory, and route registration. It may import `store` and
  `explain` when later P2 work needs them; it never imports `sdk`.
- `glassbox.web.auth`: token validation, server-side session store, cookie
  creation, session lookup/expiry, and CSRF primitives. It has no store or SDK
  dependency.
- `glassbox.web.templates`: login and later planner templates. Templates treat
  all persisted decision content as untrusted and rely on Jinja autoescaping.

The existing import-linter `web-dependencies` contract becomes active once
`glassbox.web` exists: it may depend on `store` and `explain`, never `sdk`.

## Error handling

Invalid host, missing token, or invalid timeout produces a clear startup error
before binding a port. Bad credentials do not reveal whether a configured token
exists. Expired, missing, or malformed sessions are removed and treated as
unauthenticated. Session-store failures deny access rather than granting it.

## Verification

Tests must prove:

1. startup accepts only `127.0.0.1` and `::1`, and rejects non-loopback and
   wildcard addresses before any bind;
2. missing or placeholder tokens fail startup;
3. failed login creates no session or cookie;
4. successful login creates a server-side session with `HttpOnly` and
   `SameSite=Strict` cookie attributes;
5. an authenticated request refreshes activity, while a session idle for more
   than 30 minutes is rejected;
6. protected routes redirect to login and `/health` exposes no decision data;
7. the active import-linter contract rejects `web -> sdk`.

## Deferred decisions

The live UI's queue/card/trace models, append-only feedback, static export, and
planner usability study are deliberately deferred to P2.2–P2.4. Remote access,
multi-user identity, and persistent session storage are out of scope for P2.
