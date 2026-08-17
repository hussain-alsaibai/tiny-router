# Changelog

All notable changes to tiny-router are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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