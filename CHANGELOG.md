# Changelog

All notable changes to tiny-router are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.7.0] - 2026-10-06

### Added
- **New sidecar module `tiny_router_sse.py`** (zero new dependencies; lazily imports `tiny_router`). Adds Server-Sent Events (SSE) support and a streaming-aware OpenAPI export on top of the v0.6.0 core. The sidecar is opt-in: existing `tiny_router` users see no behaviour change unless they `import tiny_router_sse`.
- **`SSEEvent(data, event=, id=, retry=, comment=)`** - structured SSE event dataclass with a `.format()` method that emits wire-format bytes. Handles multiline data (each line becomes its own `data:` field per spec), CRLF/CR/LF line splitting, JSON-encodes dicts/lists, and emits comment, event, id, retry, and data fields in the spec-compliant order.
- **`SSEResponse(generator, *, status, headers, keepalive_interval)`** - a `StreamingResponse` subclass pre-configured with the standard SSE headers (`content-type: text/event-stream; charset=utf-8`, `cache-control: no-store`, `x-accel-buffering: no`, `connection: keep-alive`). Yield `SSEEvent` instances, raw strings, or arbitrary JSON-serialisable objects; each becomes a valid SSE frame.
- **`sse_response(generator, *, status, headers)`** - convenience constructor for `SSEResponse`. Most users will use this.
- **`sse_keepalive(interval=SSE_KEEPALIVE_INTERVAL)`** - returns a keepalive `SSEEvent` (a single `: keepalive` comment). Use for periodic injection to keep proxies from closing the connection.
- **`format_sse_event(event)`** - format an `SSEEvent` as UTF-8 bytes.
- **`install_streaming_decorator(router)`** - one-call setup that monkey-patches the router's `get`/`post`/`put`/`patch`/`delete`/`head`/`options` decorators to accept a `streaming: bool = False` keyword. Idempotent. After calling, `@app.get("/events", streaming=True)` registers the route AND marks it as streaming for OpenAPI export.
- **`mark_streaming(router, method, path)`** / **`is_streaming_route(router, method, path)`** / **`add_streaming_route(router, method, pattern)`** - the underlying registry and helpers. Exposed for libraries that want to integrate without using the decorator.
- **`openapi_spec_streaming_overlay(spec, router)`** - mutates an existing `openapi_spec()` result to add a `text/event-stream` content type entry to any route marked streaming. Existing JSON content types are preserved; SSE is added alongside so clients can pick via the `Accept` header.
- **`SSE_KEEPALIVE_INTERVAL = 15.0`** - default keepalive interval in seconds.
- **New test file `test_v070_sse.py`** (29 tests). Combined suite now 135/135.

### Changed
- Bumped `pyproject.toml` to `0.7.0`.
- The sidecar pattern (zero-dep core + opt-in extra module) continues: `tiny_router_sse.py` joins `tiny_validator_mcp.py` and `tiny_log`'s redaction bridge in the family.

### Notes
- `install_streaming_decorator` is monkey-patching, which is the standard pattern for opt-in kwargs on existing APIs. It only patches the specific `Router` instance passed in (not the class), so other routers remain untouched.
- The SSE content type is added to the `200` response. For HTTP streaming that starts with a non-200 status (e.g. redirects), users should declare the SSE content type manually via `openapi_spec_streaming_overlay` after building the spec.
- `SSEResponse.__iter__` accepts three shapes: `SSEEvent` (full formatting), `str` (passed through with trailing newline), and anything else (JSON-serialised into a single `data:` line). This matches the three patterns the SSE ecosystem has converged on.
- Multiline `data` is escaped per the SSE spec: each line becomes its own `data:` field. CR, LF, and CRLF are all recognised as line separators.
- The wire format always terminates with `

` (the SSE message boundary). Clients that consume this directly (e.g. `EventSource`) handle the boundary automatically.
- For nginx-fronted deployments, the `x-accel-buffering: no` header disables proxy-level buffering. Without it, nginx will hold the entire stream before forwarding it to the client.

### Closes
- Open question from `dev-reports/2026-10-05-developer-tools-landscape.md`: "Does `tiny-router`'s `to_openapi_spec()` need a streaming / SSE-aware mode for agent streaming endpoints? Two internal users asked this week." Delivered as a 380-LOC sidecar (not the 40-LOC spec tweak originally suggested) because the SSE event format, keepalive handling, and decorator integration are non-trivial enough to warrant their own test surface.

## [0.6.0] — 2026-10-04

### Added
- **`Router.from_blueprint(blueprint, *, handlers=None, prefix="")`** — declarative route registration from a dict (or list of dicts). Build a Router from a JSON/YAML-friendly description of routes, static files, sub-mounts, and middleware, with handler callables resolved from a separate `handlers` dict. Supports inline callables, named-handler strings, nested blueprints (via `mount`), static file dirs, middleware registration, and tag preservation. New test file `test_v060_blueprint.py` (16 tests).
- **`Router.from_json(path, *, handlers=None, prefix="")`** — convenience constructor: read a JSON file and call `from_blueprint`. Pure stdlib, no PyYAML dependency.
- **`Router.from_yaml(path, *, handlers=None, prefix="", yaml_loader=None)`** — same shape for YAML; lazy-imports PyYAML and raises a clear `ImportError` if missing. Defaults to `yaml.SafeLoader` for safety.

### Changed
- Bumped `__version__` to `0.6.0`.
- `test_version` updated to `0.6.0`. Combined suite now 77/77 (61 existing + 16 new).

### Notes
- Blueprint list-merging reaches into `Router._routes` / `_static_routes` / `_mounted_routers` / `_ws_routes` / `_middlewares` / `_error_handlers` / `_error_middlewares`. This is the documented escape hatch; future versions may expose it as `Router.merge(other)`.
- `from_yaml` does not bundle a YAML parser; install PyYAML as an optional dep (`pip install pyyaml`) to enable it. The JSON path is the always-available default.
- `from_blueprint` accepts `handler` as either a string (looked up in `handlers` dict) or a callable (in-line). This matches the two patterns the ecosystem has converged on: type-annotated YAML configs + Python handler dicts for static analysis, and dynamic plugin loaders that pass callables directly.

## [0.5.0] — 2026-09-25

### Added
- **`openapi_endpoint(router, *, path="/openapi.json")`** — registers a built-in route that serves the generated `openapi_spec(router)` as JSON with `Content-Type: application/json; charset=utf-8` and `Cache-Control: no-store`. Configurable `path=`, `methods=`, `title=`, `version=`, `description=`, `servers=`. One call replaces the boilerplate of mounting a static OpenAPI file. 4 tests.
- **`jwt_refresh_pair(access_payload, refresh_payload, *, secret, access_ttl_seconds=900, refresh_ttl_seconds=2_592_000)`** — mints a paired access + refresh JWT and returns a dict ready to JSON-serialize as a token endpoint response. Caller-supplied `iat` / `exp` are overwritten so callers can't mint eternal tokens; refresh carries `typ=refresh` automatically. 4 tests.
- **`jwt_refresh_exchange(refresh_token, *, secret, access_ttl_seconds=900)`** — validates a refresh token (signature, exp, aud, iss, typ=refresh), then re-mints an access token whose claims are the refresh token's claims minus `typ`. Enforces `typ=refresh` so an attacker can't exchange a leaked access token for a fresh one. 6 tests.
- README: new section on `/openapi.json` endpoint + JWT refresh flow.
- New test file `test_tiny_router_v050.py` (15 tests). Combined suite now 101/101.

### Changed
- Bumped `__version__` to `0.5.0`.
- `__all__` now exports `openapi_endpoint`, `jwt_refresh_pair`, `jwt_refresh_exchange`.
- `test_version` test updated to `0.5.0`.

### Notes
- `jwt_refresh_exchange` requires `leeway=0` by default. Pass a positive leeway only if you understand the trade-off (clock-skew tolerance can mask revoked tokens).
- `openapi_endpoint` emits `Cache-Control: no-store` because every reload adds new routes — clients should always re-fetch.

## [0.4.0] — 2026-09-24

### Added
- **`jwt_auth()` middleware + `jwt_decode()` / `jwt_encode()` helpers** — stdlib-only HS256 verifier. Validates `exp` (with `leeway` seconds), `nbf`, `aud`, `iss`. Constant-time signature check. No `PyJWT` required. 9 tests.
- **`request_id()` middleware** — stamps every request with a correlation ID (inbound `x-request-id` if present, else `secrets.token_hex(8)`). Exposes via `req.state["request_id"]`; echoes back on response. 2 tests.
- **`openapi_spec(router)`** — generates an OpenAPI 3.1-compatible JSON dict from registered routes. Normalises internal regex patterns back into the public `/{name}` form; preserves tags. 2 tests.
- New version banner: `tiny-router v0.4.0 serving on http://...`.
- README: new sections for JWT auth, request-ID, and OpenAPI export.
- New test file `test_tiny_router_v040.py` (14 tests). Combined suite now 75/75.

### Changed
- Bumped `__version__` to `0.4.0`.
- `__all__` now exports `jwt_auth`, `jwt_decode`, `jwt_encode`, `JWTError`, `request_id`, `openapi_spec`.

### Notes
- `JWTError` only supports HS256 (the only widely-deployable shared-secret algorithm; RSA/EC keys would require bignum libraries).
- `openapi_spec()` is metadata-only — request/response schemas are not inferred from handler signatures. Add return-type annotations in v0.5.0.
- `request_id()` uses `secrets.token_hex(8)` by default (4B values; collision-resistant at <1B distinct req/s without external state).

## [0.3.0] — 2026-08-17

### Added
- **Async handlers**: write `async def` route handlers and the router awaits them automatically. Sync and async handlers coexist in the same app.
- **`Depends()` dependency injection**: FastAPI-style. Pass `Depends(callable)` as a default argument and the router resolves it (recursively for nested Depends), caching per-request. `Depends(fn, use_cache=False)` for non-cached.
- **`HTTPError` exception**: raise typed errors from handlers, dependencies, or middleware — `raise HTTPError(404, "not found")` produces the right Response without try/except boilerplate.
- **`AsyncResponse` class** (exported for symmetry with `Response` and `StreamingResponse`).
- **`get_routes()` now reports `async`** boolean for each route, so tooling can introspect handler kind.
- New status text mapping: `503 Service Unavailable`.
- New test classes `TestAsyncHandlers` and `TestDepends` (12 new tests, suite now 61 tests).

### Changed
- Bumped `__version__` to `0.3.0`.
- README features list now highlights async + Depends + HTTPError.
- Server banner now prints the version: `tiny-router v0.3.0 serving on http://...`.

### Notes
- Async handlers run on a fresh `asyncio.run()` per request inside the stdlib WSGI server. For high-throughput async workloads, deploy behind an ASGI server (future v0.4.0 work).
- `Depends()` resolution is recursive; `req` and path params are excluded from injection by name.
- Per-request dependency cache lives in `req.state` and is keyed by callable id, so it's GC'd with the request.

## [0.2.0] — 2026-07-23

### Added
- Streaming responses, static file serving, sub-router mounting, CORS middleware, rate limiter middleware, JSON helpers, route tagging.

## [0.1.0] — 2026-07-01

### Added
- Initial release: routing, path params, middleware, error handlers, WSGI, stdlib server.