# Changelog

All notable changes to tiny-router are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.7.4] - 2026-10-15

### Added
- **New sidecar module `tiny_router_redact_headers.py`** (zero new dependencies; lazily imports tiny_secret_mask >= 0.5.0). Adds a Middleware that calls tiny_secret_mask.mask_headers(req.headers) and mutates req.headers in place before downstream handlers see it.
- `redact_headers_middleware(*, stats=None)` - returns a tiny-router-compatible middleware. Mutates req.headers in place. No-op if tiny_secret_mask is not installed.
- `redactor_with_stats()` - convenience that returns `(middleware, stats)`. Useful for wiring to /metrics.
- `HeaderStats` - counter object with `headers_seen`, `redactions`, `last_redacted_header`, and `as_dict()`. Same shape as tiny_headers_redact for cross-compat.

### Notes
- The middleware is order-sensitive: register it AFTER any middleware that injects headers (e.g. JWT middleware adding X-User-Id) so injected headers are also redacted. Register it BEFORE any middleware that persists headers (logging, audit, trace export) so persisted headers are redacted.
- The header-redaction rule set is owned by tiny_secret_mask v0.5.0+, NOT by this sidecar. Adding a new rule (say, X-Vendor-API-Token) is a one-PR change to tiny_secret_mask HEADER_REDACT_RULES, not this file.
- Falls back to a no-op if tiny_secret_mask is not installed. This is intentional: a deployment that does not redact headers is the same as the v0.7.3 default.
- v0.7.4 closes the credential-leak loop in tiny-router: path (v0.7.3) + header (v0.7.4).
- 13 new tests in `test_v074_redact_headers.py`.

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


## [0.7.3] - 2026-10-14

### Added
- **New sidecar module `tiny_router_redact_path.py`** (zero new dependencies; lazily imports `tiny_router` and `tiny_secret_mask >= 0.4.0`). Adds a `Middleware` that calls `tiny_secret_mask.mask_route_path(req.path)` and mutates `req.path` in place before downstream handlers see it.
- `redact_path_middleware(*, stats=None)` - returns a tiny-router-compatible middleware. Mutates `req.path` in place. No-op if `tiny_secret_mask` is not installed (older tiny-router users are unaffected).
- `redactor_with_stats()` - convenience that returns `(middleware, stats)`. Useful for wiring to `/metrics`.
- `RedactionStats` - counter object with `paths_seen`, `redactions`, `last_redacted_path`, and `as_dict()`. Same shape as `tiny-route-redact` for cross-compat.
- **New test file `test_v073_redact_path.py`** (15 tests).

### Notes
- The middleware is order-sensitive: register it BEFORE `request_id()` middleware if you want the request_id logged alongside the redacted path. If you register it after, the path is redacted for downstream handlers but the access log line still sees the unredacted path - which is usually fine because the access log line is generated by the HTTP server, not the router.
- The path-redaction rule set is owned by `tiny_secret_mask` v0.4.0+, NOT by this sidecar. Adding a new path-pattern (say, AWS secret access keys) is a one-PR change to `tiny_secret_mask` PATH_REDACT_RULES, not this file.
- Falls back to a no-op if `tiny_secret_mask` is not installed. This is intentional: a deployment that does not redact paths is the same as the v0.7.0 default.

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