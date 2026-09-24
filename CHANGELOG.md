# Changelog

All notable changes to tiny-router are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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