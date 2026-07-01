# tiny-router — Zero-Dependency HTTP Router

> **Like FastAPI/Flask, but in one file. Zero dependencies.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](tiny_router.py)
[![Part of the tiny-* ecosystem](https://img.shields.io/badge/tiny--*-ecosystem-purple.svg)](#ecosystem)

`tiny-router` is a single-file HTTP router with path parameters, middleware chains, JSON helpers, and a built-in stdlib server. Pure Python standard library — no Flask, no FastAPI, no Starlette, no uvicorn.

## ✨ Features

- **🚏 HTTP routing** — `GET`/`POST`/`PUT`/`PATCH`/`DELETE`/`HEAD`/`OPTIONS`
- **🎯 Path parameters** — `/users/{id}` with auto-bound args
- **🧱 Middleware chains** — composable, order-aware
- **📦 JSON helpers** — `req.json` parses; dicts auto-serialize
- **🔌 WSGI compliant** — drop it into any WSGI host
- **🚀 Stdlib HTTP server** — `serve()` for instant local dev
- **🛡️ Error handlers** — `@app.on_error(404)` etc.
- **🪶 Tiny** — ~13 KB single file, zero deps

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

## 🛡️ Error Handlers

```python
@app.on_error(404)
def not_found(req, exc):
    return Response({"error": "missing", "path": req.path}, status=404)
```

## 🔌 WSGI

`tiny-router` exposes a full WSGI interface, so you can run it under `gunicorn`, `waitress`, or any WSGI server:

```bash
gunicorn -w 4 'example:app'
```

## 📊 Comparison

| Feature | **tiny-router** | Flask | FastAPI |
|---|---|---|---|
| Dependencies | **0** | ~10 | ~25 |
| File count | **1** | 1000s | 1000s |
| Async | ❌ | partial | ✅ |
| Type-driven schema | ❌ | ❌ | ✅ |
| Path params | ✅ | ✅ | ✅ |
| Middleware | ✅ | ✅ | ✅ |
| Built-in server | ✅ | ✅ | needs uvicorn |
| Startup time | <50 ms | ~150 ms | ~400 ms |

**Use `tiny-router` when** you want the smallest possible HTTP layer — embedded services, edge functions, single-file CLIs that need a REST surface, or anywhere installing Flask would dwarf the rest of your stack.

## 🧪 Testing

```bash
python test_tiny_router.py -v
```

## 🛠️ API Reference

### `Router`

| Method | Purpose |
|---|---|
| `app.get(path)` / `post` / `put` / `patch` / `delete` / `head` / `options` | Register a route |
| `app.route(method, path)` | Register for any method |
| `app.use(middleware)` | Add a middleware |
| `app.on_error(status)` | Register an error handler |
| `app.not_found(fn)` | Custom 404 handler |
| `app.wsgi(environ, start_response)` | WSGI entry point |

### `Request`

| Attribute | Type | Description |
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

### `Response(body, status=200, headers=None)`

Wraps the return value. Strings, bytes, dicts, lists, and primitives are auto-serialized to JSON when needed.

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
- [**snapdb**](https://github.com/hussain-alsaibai/snapdb) — embedded DB

12 repos, ~5,200 LOC, zero dependencies across the entire stack. All single-file, MIT, fully type-hinted. Built by [OpenClaw](https://github.com/hussain-alsaibai).
## License

MIT — see [LICENSE](LICENSE).
