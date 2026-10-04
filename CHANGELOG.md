# Changelog

All notable changes to tiny-router are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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