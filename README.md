# tiny-router — Zero-Dependency HTTP Router

> **Like FastAPI/Flask, but in one file. Zero dependencies.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](tiny_router.py)
[![Part of the tiny-* ecosystem](https://img.shields.io/badge/tiny--*-ecosystem-purple.svg)](#ecosystem)
[![Build](https://img.shields.io/badge/build-passing-brightgreen.svg)](.github/workflows/ci.yml)
[![Benchmark](https://img.shields.io/badge/benchmark-76K%20req%2Fs-blueviolet)](benchmark.py)
[![Version](https://img.shields.io/badge/version-0.2.0-blue.svg)](tiny_router.py)

`tiny-router` is a single-file HTTP router with path parameters, middleware chains, CORS, streaming, static file serving, sub-routers, rate limiting, JSON helpers, and a built-in stdlib server. Pure Python standard library — no Flask, no FastAPI, no Starlette, no uvicorn.

## ✨ Features

- **🚏 HTTP routing** — `GET`/`POST`/`PUT`/`PATCH`/`DELETE`/`HEAD`/`OPTIONS`
- **🎯 Path parameters** — `/users/{id}` with auto-bound args
- **🧱 Middleware chains** — composable, order-aware
- **🌐 CORS middleware** — built-in `cors()` factory for preflight + headers
- **📦 StreamingResponse** — yield chunks with chunked transfer encoding
- **📁 Static file serving** — `app.static("/static", "./public")`
- **🔌 Sub-router mounting** — `app.mount("/api", api_router)`
- **🛡️ Error handlers** — `@app.on_error(ValueError)` for specific exceptions
- **🚦 Rate limiter** — token-bucket `rate_limiter()` middleware
- **🔍 Query param parsing** — type coercion with `req.query_param("page", cast=int)`
- **🏷️ Route grouping** — tags for API organization
- **📦 JSON helpers** — `req.json` parses; dicts auto-serialize
- **🔌 WSGI compliant** — drop it into any WSGI host
- **🚀 Stdlib HTTP server** — `serve()` for instant local dev
- **🪶 Tiny** — ~26 KB single file, zero deps

## 🚀 Quick Start

```python
from tiny_router import Router, Response, serve

app = Router()

@app.get("/")
def home(req):
    return {"hello": "world"}

@app.get("/users/{id}")
def user(req, id):
    return {"id": int(id), "name": f"User {id}"}

@app.post("/items")
def create(req):
    data = req.json
    return {"created": True, **data}, 201

if __name__ == "__main__":
    serve(app, host="127.0.0.1", port=8000)
```

## 📦 Installation

```bash
pip install tiny-router
```

Or just copy `tiny_router.py` into your project — it's a single file with zero dependencies.

## 🧱 Middleware

```python
@app.use
def auth(req, nxt):
    if not req.headers.get("authorization"):
        return Response({"error": "missing token"}, status=401)
    return nxt(req)
```

Middleware are composable. The first registered is outermost:

```python
app.use(mw_a)  # outermost
app.use(mw_b)  # inner
```

### CORS Middleware

```python
from tiny_router import Router, cors

app = Router()
app.use(cors(allow_origins=["*"]))  # allow all origins

# Or restrict:
app.use(cors(
    allow_origins=["https://example.com"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    allow_credentials=True,
    max_age=3600,
))
```

The built-in `cors()` factory handles preflight `OPTIONS` requests automatically.

### Rate Limiter Middleware

```python
from tiny_router import Router, rate_limiter

app = Router()
app.use(rate_limiter(max_requests=60, window_seconds=60))
```

A simple token-bucket implementation. Set a custom key function for per-user limits:

```python
def client_ip(req):
    return req.headers.get("x-forwarded-for", req.headers.get("host", "unknown"))

app.use(rate_limiter(max_requests=100, window_seconds=60, key_func=client_ip))
```

## 📦 StreamingResponse

```python
from tiny_router import Router, StreamingResponse

app = Router()

@app.get("/stream")
def stream_numbers(req):
    def generate():
        for i in range(10):
            yield f"chunk {i}\n"
    return StreamingResponse(generate(), headers={"content-type": "text/plain"})

@app.get("/events")
def stream_events(req):
    def generate():
        import time
        for i in range(5):
            yield f"data: event {i}\n\n"
            time.sleep(0.5)
    return StreamingResponse(generate(), headers={"content-type": "text/event-stream"})
```

> **Note:** StreamingResponse provides chunked transfer encoding when used with a WSGI server that supports generator return values (like gunicorn). The built-in `serve()` collects the stream into a single body for compatibility.

## 📁 Static File Serving

```python
from tiny_router import Router, serve

app = Router()
app.static("/static", "./public")
app.static("/media", "./media")

@app.get("/")
def home(req):
    return {"message": "visit /static/style.css for the stylesheet"}

if __name__ == "__main__":
    serve(app)
```

Supports automatic content-type detection for common file types (HTML, CSS, JS, images, fonts, etc.) and path traversal protection.

## 🔌 Sub-Routers / Mounting

```python
from tiny_router import Router, serve

app = Router()
api = Router(prefix="/api")
v2 = Router(prefix="/v2")

# Mount sub-routers
app.mount("/api", api)

# Sub-router with version prefix
v1 = Router(prefix="/api/v1")
app.mount("/api/v1", v1)

@api.get("/users")
def list_users(req):
    return {"users": ["alice", "bob"]}

@api.get("/items")
def list_items(req):
    return {"items": ["widget", "gadget"]}

@v1.get("/users")
def v1_users(req):
    return {"users": [], "version": "v1"}

@v2.get("/users")
def v2_users(req):
    return {"users": [], "version": "v2"}

# Also works with tags/groups:
admin = Router()
admin_tags = ["admin"]

@admin.get("/stats")
def stats(req):
    return {"visits": 1337}

app.mount("/admin", admin)
```

## 🛡️ Error Handling

### Exception Error Handlers

Catch specific exception types with `@app.on_error()`:

```python
from tiny_router import Router, Response

app = Router()

@app.on_error(ValueError)
def handle_bad_input(req, exc):
    return {"error": "invalid input", "detail": str(exc)}, 400

@app.on_error(PermissionError)
def handle_forbidden(req, exc):
    return {"error": "forbidden"}, 403

@app.on_error(Exception)
def handle_generic(req, exc):
    return {"error": "internal error"}, 500

@app.get("/divide/{a}/{b}")
def divide(req, a, b):
    result = int(a) / int(b)
    return {"result": result}
```

### Status Code Error Handlers

```python
@app.on_status(404)
def not_found(req, exc):
    return Response({"error": "missing", "path": req.path}, status=404)

@app.not_found
def custom_404(req):
    return Response({"custom": "this page does not exist"}, status=404)
```

## 🔍 Query Parameter Parsing

```python
from tiny_router import Router

app = Router()

@app.get("/search")
def search(req):
    page = req.query_param("page", default=1, cast=int)
    limit = req.query_param("limit", default=10, cast=int)
    q = req.query_param("q", default="")
    active = req.query_param("active", default=False, cast=bool)
    # Get all values for multi-value params
    tags = req.query_params("tag")
    return {
        "query": q,
        "page": page,
        "limit": limit,
        "active": active,
        "tags": tags,
    }
```

## 🏷️ Route Groups / Tags

```python
from tiny_router import Router

app = Router()

@app.get("/health", tags=["monitoring"])
def health(req):
    return {"status": "ok"}

@app.get("/metrics", tags=["monitoring"])
def metrics(req):
    return {"requests": 42}

@app.get("/users", tags=["users"])
def list_users(req):
    return {"users": []}

# List routes by tag
monitoring_routes = app.get_routes(tag="monitoring")
print(monitoring_routes)
# [
#   {"method": "GET", "pattern": "^/health$", "handler": "health", "tags": ["monitoring"]},
#   {"method": "GET", "pattern": "^/metrics$", "handler": "metrics", "tags": ["monitoring"]},
# ]
```

## 🔌 WSGI

`tiny-router` exposes a full WSGI interface, so you can run it under `gunicorn`, `waitress`, or any WSGI server:

```bash
gunicorn -w 4 'example:app'
```

## 📊 Benchmark

```bash
python benchmark.py
```

Typical results on modern hardware:
- **76,000+ req/s** for simple GET routes
- Minimal memory overhead (~2 MB process)
- Sub-50 ms startup time

## Agent Workflow Fit

`tiny-router` is useful when an autonomous agent needs a tiny local control plane without pulling in an ASGI stack:

- **Webhook receivers** — accept GitHub, Stripe, Telegram, or internal callback events in a single-file service
- **Tool adapters** — expose a small REST surface around scripts, queues, caches, or model workers
- **Bounty repro harnesses** — stand up a minimal HTTP app that demonstrates an issue without framework noise
- **Cron dashboards** — serve status endpoints for scheduled jobs, health checks, and local automation

## 🛠️ API Reference

### `Router`

| Method | Purpose |
|---|---|
| `app.get(path, tags=[])` / `post` / `put` / `patch` / `delete` / `head` / `options` | Register a route |
| `app.route(method, path, tags=[])` | Register for any method |
| `app.use(middleware)` | Add a middleware |
| `app.on_error(exception_type)` | Register error handler for exception type |
| `app.on_status(status)` | Register error handler for status code |
| `app.not_found(fn)` | Custom 404 handler |
| `app.static(prefix, directory)` | Serve static files |
| `app.mount(prefix, sub_router)` | Mount a sub-router |
| `app.get_routes(tag=None)` | List routes, optionally filtered by tag |
| `app.wsgi(environ, start_response)` | WSGI entry point |

### `Request`

| Attribute / Method | Type | Description |
|---|---|---|
| `method` | `str` | HTTP method |
| `path` | `str` | URL path |
| `query` | `dict[str, list[str]]` | Parsed query string |
| `headers` | `dict[str, str]` | Request headers |
| `body` | `bytes` | Raw body |
| `params` | `dict[str, str]` | Path parameters |
| `state` | `dict[str, Any]` | Per-request scratch space |
| `json` | `Any` | Parsed JSON body |
| `form` | `dict[str, str]` | Parsed form body |
| `query_param(name, default, cast)` | `Any` | Single query param with type coercion |
| `query_params(name, cast)` | `list[Any]` | All values for a query param |

### `Response(body, status=200, headers=None)`

Wraps the return value. Strings, bytes, dicts, lists, and primitives are auto-serialized to JSON when needed.

### `StreamingResponse(generator, status=200, headers=None)`

Yields chunks from a generator. Strings, bytes, and dicts are auto-serialized.

### Middleware Factories

| Factory | Purpose |
|---|---|
| `cors(allow_origins, ...)` | CORS headers and preflight |
| `rate_limiter(max_requests, window_seconds, key_func)` | Token-bucket rate limiter |

## 📊 Comparison

| Feature | **tiny-router** | Flask | FastAPI |
|---|---|---|---|
| Dependencies | **0** | ~10 | ~25 |
| File count | **1** | 1000s | 1000s |
| Async | ❌ | partial | ✅ |
| Type-driven schema | ❌ | ❌ | ✅ |
| Path params | ✅ | ✅ | ✅ |
| Middleware | ✅ | ✅ | ✅ |
| CORS built-in | ✅ | ❌ (extension) | ❌ (extension) |
| Streaming | ✅ | ✅ | ✅ |
| Static files | ✅ | ✅ | ❌ |
| Sub-routers | ✅ | ✅ (blueprints) | ✅ (routers) |
| Rate limiter built-in | ✅ | ❌ (extension) | ❌ (extension) |
| Built-in server | ✅ | ✅ | needs uvicorn |
| Startup time | <50 ms | ~150 ms | ~400 ms |

**Use `tiny-router` when** you want the smallest possible HTTP layer — embedded services, edge functions, single-file CLIs that need a REST surface, or anywhere installing Flask would dwarf the rest of your stack.

## 🧪 Testing

```bash
python test_tiny_router.py -v
python examples/test_agent_callback_receiver.py -v
```

## 🛠️ Recipes

- **`examples/agent_callback_receiver.py`** — a hardened async-agent
  webhook receiver. Shared-token auth, optional HMAC signature check,
  payload schema validation, TTL-based delivery dedupe, sliding-window
  rate limits per source, structured JSON logs with `x-request-id`
  correlation, and `/health`, `/ready`, and `/status` endpoints.

## Ecosystem

Part of the **tiny-*** zero-dependency toolkit for Python agent infrastructure:

- [**tiny-router**](https://github.com/hussain-alsaibai/tiny-router) — HTTP router, 76K req/s
- [**tiny-log**](https://github.com/hussain-alsaibai/tiny-log) — structured logging
- [**tiny-validator**](https://github.com/hussain-alsaibai/tiny-validator) — input validation, 247K val/s
- [**tiny-config**](https://github.com/hussain-alsaibai/tiny-config) — layered config loader
- [**tiny-cli**](https://github.com/hussain-alsaibai/tiny-cli) — CLI builder with colors
- [**fast-cache**](https://github.com/hussain-alsaibai/fast-cache) — LRU + TTL + SWR cache
- [**tiny-rate**](https://github.com/hussain-alsaibai/tiny-rate) — rate limiter (token / fixed / sliding)
- [**tiny-retry**](https://github.com/hussain-alsaibai/tiny-retry) — retry + backoff + circuit breaker
- [**tiny-pool**](https://github.com/hussain-alsaibai/tiny-pool) — ThreadPool + AsyncPool
- [**tiny-agent**](https://github.com/hussain-alsaibai/tiny-agent) — zero-dep agent framework
- [**tiny-mcp**](https://github.com/hussain-alsaibai/tiny-mcp) — Model Context Protocol
- [**tiny-embed**](https://github.com/hussain-alsaibai/tiny-embed) — embeddings + vector search
- [**tiny-compose**](https://github.com/hussain-alsaibai/tiny-compose) — Stack any decorators in any order, declaratively
- [**tiny-trace**](https://github.com/hussain-alsaibai/tiny-trace) — OTel-compatible tracing, sync + async, W3C propagation
- [**tiny-secret**](https://github.com/hussain-alsaibai/tiny-secret) — Zero-dep secret loader + redacting printer
- [**tiny-cron**](https://github.com/hussain-alsaibai/tiny-cron) — cron-style scheduler + intervals
- [**tiny-flags**](https://github.com/hussain-alsaibai/tiny-flags) — feature flags, percentage rollout
- [**tiny-queue**](https://github.com/hussain-alsaibai/tiny-queue) — persistent FIFO queue, retries
- [**tiny-metrics**](https://github.com/hussain-alsaibai/tiny-metrics) — Prometheus-compatible metrics
- [**tiny-timeout**](https://github.com/hussain-alsaibai/tiny-timeout) — hard timeouts + cooperative deadlines
- [**tiny-idempotency**](https://github.com/hussain-alsaibai/tiny-idempotency) — Stripe-style idempotency keys
- [**tiny-budget**](https://github.com/hussain-alsaibai/tiny-budget) — runtime cost + token enforcement for AI agents
- [**tiny-eventbus**](https://github.com/hussain-alsaibai/tiny-eventbus) — durable pub/sub with JSONL replay
- [**snapdb**](https://github.com/hussain-alsaibai/snapdb) — embedded DB

21 repos, ~14,700 LOC, zero dependencies across the entire stack. All single-file, MIT, fully type-hinted. Built by [OpenClaw](https://github.com/hussain-alsaibai).

## License

MIT — see [LICENSE](LICENSE).
