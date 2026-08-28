"""tiny_router — zero-dependency HTTP router for Python.

A single-file HTTP routing library with path parameters, middleware chains,
CORS, streaming, static file serving, sub-routers, rate limiting, dependency
injection, async handlers (v0.3.0+), and a built-in WSGI server.
No external packages required.

Usage:
    from tiny_router import Router, Response, serve, Depends

    app = Router()

    @app.get("/")
    def home(req):
        return {"hello": "world"}

    @app.get("/users/{id}")
    def user(req, id: str):
        return {"id": int(id)}

    # Async handlers (v0.3.0+) — detected automatically
    @app.get("/slow")
    async def slow(req):
        await asyncio.sleep(0.01)
        return {"ok": True}

    # Dependency injection (v0.3.0+) — FastAPI-style Depends()
    def auth(req: Request) -> dict:
        token = req.headers.get("authorization", "")
        if not token:
            raise HTTPError(401, "unauthorized")
        return {"user": token}

    @app.get("/me")
    def me(req, user=Depends(auth)):
        return user

    if __name__ == "__main__":
        serve(app, host="127.0.0.1", port=8000)
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs
from pathlib import Path


__version__ = "0.3.0"


# ---------- Errors ----------


class HTTPError(Exception):
    """Exception raised by handlers/middleware to return an HTTP error response.

    Example:
        def handler(req):
            raise HTTPError(404, "user not found")
    """

    def __init__(self, status: int, message: str = "", headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.message = message or ""
        self.headers = dict(headers or {})
        super().__init__(f"{status} {message}")


# ---------- Response ----------


class Response:
    """HTTP response wrapper. Auto-serialized when returned from a handler."""

    def __init__(
        self,
        body: Any = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers: dict[str, str] = dict(headers or {})
        self._body = body

    @property
    def body(self) -> bytes:
        if isinstance(self._body, (bytes, bytearray)):
            return bytes(self._body)
        if isinstance(self._body, str):
            return self._body.encode("utf-8")
        if self._body is None:
            return b""
        # Default to JSON for dicts/lists/primitives.
        return json.dumps(self._body, default=str).encode("utf-8")

    def set_header(self, name: str, value: str) -> "Response":
        self.headers[name] = value
        return self


# ---------- StreamingResponse ----------


# ---------- WebSocket ----------


class WebSocket:
    """Lightweight WebSocket connection object passed to ws() handlers.

    Note: The built-in stdlib HTTP server does not support WebSocket upgrades.
    For real WebSocket support, run tiny-router behind a WSGI server that handles
    the upgrade (e.g. gunicorn with a WebSocket worker, or a reverse proxy like
    nginx). This class gives you the handler API and session management so your
    code is WSGI-server-agnostic.

    For testing or in-process use, see `tiny_router.websocket_server()` which
    starts a standalone ThreadingHTTPServer on a separate port.

    Example with a WSGI server that sets ws_scope:
        @app.ws("/ws")
        async def ws_handler(ws: WebSocket):
            await ws.accept()
            async for msg in ws:
                await ws.send(f"echo: {msg}")
            await ws.close()
    """

    def __init__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.scope = scope
        self._receive = receive
        self._send = send
        self._closed = False

    @property
    def path(self) -> str:
        return self.scope.get("path", "/")

    @property
    def query_params(self) -> dict[str, str]:
        return self.scope.get("query_string", {})

    @property
    def headers(self) -> dict[str, str]:
        return {
            k.decode(): v.decode()
            for k, v in self.scope.get("headers", [])
        }

    async def accept(self) -> None:
        await self.send({"type": "websocket.accept", "text": ""})

    async def send(self, data: str | dict[str, Any]) -> None:
        if self._closed:
            return
        if isinstance(data, str):
            await self._send({"type": "websocket.send", "text": data})
        else:
            await self._send(data)

    async def close(self, code: int = 1000) -> None:
        if self._closed:
            return
        self._closed = True
        await self._send({"type": "websocket.close", "code": code})

    async def __aiter__(self) -> AsyncIterator[str]:
        """Iterate over incoming text messages."""
        while not self._closed:
            msg = await self._receive()
            msg_type = msg.get("type", "")
            if msg_type == "websocket.disconnect":
                self._closed = True
                break
            if msg_type == "websocket.receive":
                yield msg.get("text", "")


# Import for type hint only — don't actually use async in sync code path
from typing import AsyncIterator, Awaitable


class StreamingResponse:
    """Response that yields chunks using Transfer-Encoding: chunked.

    Example:
        @app.get("/stream")
        def stream(req):
            def generate():
                for i in range(5):
                    yield f"chunk {i}\\n"
            return StreamingResponse(generate())
    """

    def __init__(
        self,
        generator: Iterable[Any],
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers: dict[str, str] = dict(headers or {})
        self._generator = generator

    def __iter__(self) -> Iterable[bytes]:
        for chunk in self._generator:
            if isinstance(chunk, str):
                yield chunk.encode("utf-8")
            elif isinstance(chunk, bytes):
                yield chunk
            else:
                yield json.dumps(chunk, default=str).encode("utf-8")


# ---------- Async support (v0.3.0) ----------


class AsyncResponse:
    """Marker wrapping an awaitable coroutine.

    The router will `await` it instead of treating it as a sync return value.
    Handlers can simply be `async def` — the router detects coroutine
    functions automatically.
    """
    __slots__ = ("awaitable",)

    def __init__(self, awaitable: Any) -> None:
        self.awaitable = awaitable


def _is_async_callable(fn: Callable) -> bool:
    """Return True if fn is an async (coroutine) function."""
    return inspect.iscoroutinefunction(fn)


# ---------- Dependency injection (v0.3.0) ----------


class Depends:
    """Marker for a dependency that the router will resolve and inject.

    FastAPI-style: pass Depends(callable) as a default argument to a handler.
    The router inspects handler signatures, calls dependencies (which may also
    have their own Depends), caches per-request results in `req.state`, and
    passes the resolved values as kwargs.

    Example:
        def get_db(req: Request) -> DB:
            return req.state["db"]

        @app.get("/items")
        def list_items(req, db=Depends(get_db)):
            return db.query_all()
    """

    __slots__ = ("callable", "use_cache")

    def __init__(self, callable: Callable, use_cache: bool = True) -> None:
        self.callable = callable
        self.use_cache = use_cache


def _resolve_dependencies(
    handler: Callable,
    request: Request,
) -> dict[str, Any]:
    """Inspect handler signature, resolve Depends()-marked parameters.

    Recursively resolves nested Depends() in dependency callables.

    Returns a dict of {param_name: resolved_value} ready to splat into the
    handler call. `req` and path params are excluded — handled separately.
    """
    sig = inspect.signature(handler)
    resolved: dict[str, Any] = {}
    dep_cache_key = f"_deps:{id(handler)}"
    dep_cache: dict[int, Any] = request.state.setdefault(dep_cache_key, {})

    for name, param in sig.parameters.items():
        if name == "req" or name == "request":
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if isinstance(param.default, Depends):
            dep = param.default
            cache_id = id(dep.callable)
            if dep.use_cache and cache_id in dep_cache:
                resolved[name] = dep_cache[cache_id]
            else:
                # Recursively resolve nested Depends() in the dep's signature
                nested = _resolve_dependencies(dep.callable, request)
                # The dep itself receives (req, **nested)
                value = dep.callable(request, **nested)
                if dep.use_cache:
                    dep_cache[cache_id] = value
                resolved[name] = value
        elif param.default is inspect.Parameter.empty:
            # No default and not Depends — leave it; path params fill it later
            pass

    return resolved


# ---------- Request ----------


class Request:
    """Lightweight request object passed to handlers."""

    __slots__ = (
        "method",
        "path",
        "query",
        "headers",
        "body",
        "params",
        "state",
    )

    def __init__(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        headers: dict[str, str],
        body: bytes,
        params: dict[str, str] | None = None,
    ) -> None:
        self.method = method
        self.path = path
        self.query: dict[str, list[str]] = query
        self.headers = headers
        self.body = body
        self.params: dict[str, str] = dict(params or {})
        self.state: dict[str, Any] = {}

    @property
    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))

    @property
    def form(self) -> dict[str, str]:
        raw = self.body.decode("utf-8", errors="replace")
        parsed = parse_qs(raw)
        return {k: v[0] for k, v in parsed.items()}

    def query_param(self, name: str, default: Any = None, cast: type | None = None) -> Any:
        """Get a single query parameter value with optional type coercion.

        Example:
            req.query_param("page", default=1, cast=int)
        """
        values = self.query.get(name)
        if not values:
            return default
        val = values[0]
        if cast is not None:
            try:
                if cast is bool:
                    return val.lower() in ("1", "true", "yes")
                return cast(val)
            except (ValueError, TypeError):
                return default
        return val

    def query_params(self, name: str, cast: type | None = None) -> list[Any]:
        """Get all values for a query parameter, optionally cast."""
        values = self.query.get(name, [])
        if cast is not None:
            result: list[Any] = []
            for v in values:
                try:
                    if cast is bool:
                        result.append(v.lower() in ("1", "true", "yes"))
                    else:
                        result.append(cast(v))
                except (ValueError, TypeError):
                    result.append(v)
            return result
        return values


# ---------- Router internals ----------


_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _compile_path(pattern: str) -> re.Pattern[str]:
    """Convert `/users/{id}` to a regex with a named group."""
    parts: list[str] = []
    last = 0
    for m in _PARAM_RE.finditer(pattern):
        parts.append(re.escape(pattern[last : m.start()]))
        parts.append(f"(?P<{m.group(1)}>[^/]+)")
        last = m.end()
    parts.append(re.escape(pattern[last:]))
    return re.compile("^" + "".join(parts) + "$")


Handler = Callable[..., Any]
Middleware = Callable[[Request, Callable[[Request], Any]], Any]


class Router:
    """A tiny HTTP router. Register handlers with @app.get/@app.post etc.

    v0.3.0: handlers may be sync or async; FastAPI-style `Depends()` is
    supported for dependency injection.
    """

    def __init__(self, prefix: str = "") -> None:
        self._routes: list[tuple[re.Pattern[str], str, Handler, list[str]]] = []
        self._ws_routes: list[tuple[re.Pattern[str], Callable, list[str]]] = []
        self._middlewares: list[Middleware] = []
        self._error_handlers: dict[int, Handler] = {}
        self._not_found: Handler | None = None
        self._static_routes: list[tuple[str, str]] = []
        self._mounted_routers: list[tuple[str, Router]] = []
        self._error_middlewares: dict[type, Callable] = {}
        self._prefix = prefix.rstrip("/")

    # ---- registration ----

    def add(
        self,
        method: str,
        pattern: str,
        tags: list[str] | None = None,
    ) -> Callable[[Handler], Handler]:
        method = method.upper()
        full_pattern = self._prefix + pattern
        compiled = _compile_path(full_pattern)

        def decorator(fn: Handler) -> Handler:
            self._routes.append((compiled, method, fn, tags or []))
            return fn

        return decorator

    def get(
        self, pattern: str, tags: list[str] | None = None
    ) -> Callable[[Handler], Handler]:
        return self.add("GET", pattern, tags=tags)

    def post(
        self, pattern: str, tags: list[str] | None = None
    ) -> Callable[[Handler], Handler]:
        return self.add("POST", pattern, tags=tags)

    def put(
        self, pattern: str, tags: list[str] | None = None
    ) -> Callable[[Handler], Handler]:
        return self.add("PUT", pattern, tags=tags)

    def patch(
        self, pattern: str, tags: list[str] | None = None
    ) -> Callable[[Handler], Handler]:
        return self.add("PATCH", pattern, tags=tags)

    def delete(
        self, pattern: str, tags: list[str] | None = None
    ) -> Callable[[Handler], Handler]:
        return self.add("DELETE", pattern, tags=tags)

    def head(
        self, pattern: str, tags: list[str] | None = None
    ) -> Callable[[Handler], Handler]:
        return self.add("HEAD", pattern, tags=tags)

    def options(
        self, pattern: str, tags: list[str] | None = None
    ) -> Callable[[Handler], Handler]:
        return self.add("OPTIONS", pattern, tags=tags)

    def route(
        self,
        method: str,
        pattern: str,
        tags: list[str] | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.add(method, pattern, tags=tags)

    def ws(self, pattern: str, tags: list[str] | None = None) -> Callable:
        """Register an async WebSocket handler.

        The handler receives a `WebSocket` object. Use `await ws.accept()` to accept,
        `async for msg in ws:` to iterate messages, and `await ws.send()` to reply.

        Requires a WSGI server that supports ASGI/WebSocket (e.g. gunicorn with
        uvicorn workers, or a custom server). The built-in `serve()` does not
        support WebSocket upgrades.

        Example:
            @app.ws("/ws")
            async def echo_ws(ws):
                await ws.accept()
                async for msg in ws:
                    await ws.send(f"echo: {msg}")

        For a self-contained WebSocket server (no external deps), use:
            tiny_router.websocket_server(app, host, port)
        """
        full_pattern = self._prefix + pattern
        compiled = _compile_path(full_pattern)

        def decorator(fn: Callable) -> Callable:
            self._ws_routes.append((compiled, fn, tags or []))
            return fn

        return decorator

    # ---- static files ----

    def static(self, prefix: str, directory: str) -> "Router":
        """Serve static files from *directory* at *prefix*.

        Example:
            app.static("/static", "./public")
            # GET /static/style.css serves ./public/style.css
        """
        prefix = prefix.rstrip("/")
        directory = os.path.abspath(directory)
        self._static_routes.append((prefix, directory))
        return self

    # ---- sub-routers ----

    def mount(self, prefix: str, sub_router: Router) -> "Router":
        """Mount a sub-router at the given prefix.

        Example:
            api = Router(prefix="/api")
            app.mount("/api", api)
        """
        prefix = prefix.rstrip("/")
        if sub_router._prefix and not sub_router._prefix.startswith(prefix):
            sub_router._prefix = prefix + sub_router._prefix
        elif sub_router._prefix:
            pass  # already set
        else:
            sub_router._prefix = prefix
        self._mounted_routers.append((prefix, sub_router))
        return self

    # ---- middleware ----

    def use(self, middleware: Middleware) -> "Router":
        """Register a middleware. Order matters: outermost first.

        v0.3.0: middleware may also be async.
        """
        self._middlewares.append(middleware)
        return self

    # ---- error handlers ----

    def on_error(self, exception_type: type[Exception]) -> Callable:
        """Register an error handler for a specific exception type.

        Example:
            @app.on_error(ValueError)
            def handle_value_error(req, exc):
                return {"error": "bad value"}, 400
        """
        def decorator(fn: Callable) -> Callable:
            self._error_middlewares[exception_type] = fn
            return fn
        return decorator

    def on_status(self, status: int) -> Callable[[Handler], Handler]:
        """Register an error handler for an HTTP status code.

        (Renamed from on_error to avoid conflict with exception error handlers)
        """
        def decorator(fn: Handler) -> Handler:
            self._error_handlers[status] = fn
            return fn

        return decorator

    def not_found(self, fn: Handler) -> Handler:
        self._not_found = fn
        return fn

    # ---- dispatch ----

    def _dispatch(self, request: Request) -> Any:
        # Apply middleware chain (sync path)
        def core(req: Request) -> Any:
            return self._handle(req)

        chain: Callable[[Request], Any] = core
        for mw in reversed(self._middlewares):
            next_in_chain = chain

            def make(m: Middleware, nxt: Callable[[Request], Any]) -> Middleware:
                def wrapped(req: Request) -> Any:
                    return m(req, nxt)

                return wrapped

            chain = make(mw, next_in_chain)

        try:
            result = chain(request)
            # If anything in the chain returned a coroutine, run it.
            if inspect.iscoroutine(result):
                # Run the coroutine on a fresh loop (stdlib WSGI is sync).
                return asyncio.run(result)
            return result
        except HTTPError as exc:
            return Response(
                {"error": exc.message} if exc.message else {"error": status_text(exc.status)},
                status=exc.status,
                headers=exc.headers,
            )
        except Exception as exc:  # noqa: BLE001
            # Check type-specific error handlers first
            for exc_type, handler in self._error_middlewares.items():
                if isinstance(exc, exc_type):
                    return self._normalize(handler(request, exc))
            # Then check status-based handlers
            status = getattr(exc, "status", 500)
            if status in self._error_handlers:
                return self._normalize(self._error_handlers[status](request, exc))
            return Response({"error": str(exc)}, status=status)

    def _handle(self, request: Request) -> Any:
        # Check mounted sub-routers first
        for prefix, sub_router in self._mounted_routers:
            if request.path.startswith(prefix + "/") or request.path == prefix:
                if hasattr(sub_router, '_dispatch'):
                    return sub_router._dispatch(request)

        # Check static routes
        for prefix, directory in self._static_routes:
            if request.path.startswith(prefix + "/") or request.path == prefix:
                if request.method != "GET":
                    return Response({"error": "method not allowed"}, status=405)
                rel_path = request.path[len(prefix):].lstrip("/")
                if not rel_path:
                    # Try index files
                    for index in ("index.html", "index.htm"):
                        idx_path = os.path.join(directory, index)
                        if os.path.isfile(idx_path):
                            return self._serve_file(idx_path)
                    return Response({"error": "directory listing not supported"}, status=404)
                # Path traversal protection
                full_path = os.path.normpath(os.path.join(directory, rel_path))
                if not full_path.startswith(directory):
                    return Response({"error": "forbidden"}, status=403)
                if not os.path.isfile(full_path):
                    return Response({"error": "file not found"}, status=404)
                return self._serve_file(full_path)

        # Check registered routes
        for pattern, method, handler, _tags in self._routes:
            if method != request.method:
                continue
            match = pattern.match(request.path)
            if match:
                request.params.update(match.groupdict())
                # v0.3.0: resolve dependencies
                resolved_deps = _resolve_dependencies(handler, request)
                merged = {**resolved_deps, **request.params}
                result = handler(request, **merged)
                if inspect.iscoroutine(result):
                    # Async handler: run on a fresh event loop.
                    result = asyncio.run(result)
                return self._normalize(result)

        if request.method == "OPTIONS":
            return Response(
                "",
                status=204,
                headers={
                    "Allow": "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD",
                },
            )

        if self._not_found is not None:
            return self._normalize(self._not_found(request))

        return Response({"error": "not found", "path": request.path}, status=404)

    @staticmethod
    def _serve_file(full_path: str) -> Response:
        """Read and return a static file with inferred content type."""
        mime_types = {
            ".html": "text/html",
            ".htm": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            ".txt": "text/plain",
            ".xml": "application/xml",
            ".pdf": "application/pdf",
            ".woff": "font/woff",
            ".woff2": "font/woff2",
            ".ttf": "font/ttf",
            ".otf": "font/otf",
            ".map": "application/json",
        }
        ext = os.path.splitext(full_path)[1].lower()
        content_type = mime_types.get(ext, "application/octet-stream")
        with open(full_path, "rb") as f:
            data = f.read()
        return Response(
            data,
            status=200,
            headers={"content-type": content_type},
        )

    @staticmethod
    def _normalize(result: Any) -> Response:
        if isinstance(result, Response):
            return result
        if isinstance(result, StreamingResponse):
            # For WSGI / built-in server, collect the stream into a single body
            body = b"".join(result)
            resp = Response(body, status=result.status, headers=result.headers)
            if "content-type" not in {k.lower() for k in resp.headers}:
                resp.headers["content-type"] = "application/json"
            return resp
        if isinstance(result, tuple):
            body, status = (result[0], result[1]) if len(result) >= 2 else (result[0], 200)
            headers = result[2] if len(result) >= 3 else None
            return Response(body, status=status, headers=headers)
        return Response(result, status=200)

    # ---- route listing / groups ----

    def get_routes(self, tag: str | None = None) -> list[dict[str, Any]]:
        """List registered routes and WebSocket handlers, optionally filtered by tag."""
        result = []
        for pattern, method, handler, tags in self._routes:
            if tag is None or tag in tags:
                result.append({
                    "method": method,
                    "pattern": pattern.pattern,
                    "handler": handler.__name__,
                    "tags": tags,
                    "async": _is_async_callable(handler),
                })
        for pattern, handler, tags in self._ws_routes:
            if tag is None or tag in tags:
                result.append({
                    "method": "WS",
                    "pattern": pattern.pattern,
                    "handler": handler.__name__,
                    "tags": tags,
                })
        return result

    # ---- WSGI ----

    def wsgi(self, environ: dict, start_response: Callable) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        query_raw = environ.get("QUERY_STRING", "")
        query = parse_qs(query_raw, keep_blank_values=True)
        body_len = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(body_len) if body_len else b""
        headers = {
            k[5:].replace("_", "-").lower(): v
            for k, v in environ.items()
            if k.startswith("HTTP_")
        }
        if "CONTENT_TYPE" in environ:
            headers["content-type"] = environ["CONTENT_TYPE"]

        request = Request(method, path, query, headers, body)
        response = self._dispatch(request)

        # _dispatch always returns a Response (async coros are run via asyncio.run)
        assert isinstance(response, Response), "router did not produce a Response"

        if "content-type" not in {k.lower() for k in response.headers}:
            response.headers["content-type"] = "application/json"

        start_response(
            f"{response.status} {status_text(response.status)}",
            [(k, v) for k, v in response.headers.items()],
        )
        return [response.body]

    __call__ = wsgi


# ---------- CORS Middleware ----------


def cors(
    allow_origins: list[str] | None = None,
    allow_methods: list[str] | None = None,
    allow_headers: list[str] | None = None,
    allow_credentials: bool = False,
    expose_headers: list[str] | None = None,
    max_age: int = 600,
) -> Middleware:
    """CORS middleware factory.

    Example:
        app = Router()
        app.use(cors(allow_origins=["*"]))
    """
    if allow_origins is None:
        allow_origins = ["*"]
    if allow_methods is None:
        allow_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
    if allow_headers is None:
        allow_headers = ["Content-Type", "Authorization", "X-Requested-With"]
    if expose_headers is None:
        expose_headers = []

    def cors_middleware(req: Request, nxt: Callable[[Request], Response]) -> Response:
        # Handle preflight
        if req.method == "OPTIONS":
            origin = req.headers.get("origin", "")
            if "*" not in allow_origins and origin not in allow_origins:
                return Response("", status=204)
            resp = Response("", status=204)
            acrm = req.headers.get("access-control-request-method", "")
            if acrm and acrm.upper() not in {m.upper() for m in allow_methods}:
                return resp
            resp.headers["Access-Control-Allow-Origin"] = (
                origin if origin else "*"
            )
            resp.headers["Access-Control-Allow-Methods"] = ", ".join(allow_methods)
            resp.headers["Access-Control-Allow-Headers"] = ", ".join(allow_headers)
            if allow_credentials:
                resp.headers["Access-Control-Allow-Credentials"] = "true"
            if expose_headers:
                resp.headers["Access-Control-Expose-Headers"] = ", ".join(expose_headers)
            resp.headers["Access-Control-Max-Age"] = str(max_age)
            return resp

        # Normal request
        response = nxt(req)
        origin = req.headers.get("origin", "")
        if "*" in allow_origins or origin in allow_origins:
            response.headers["Access-Control-Allow-Origin"] = (
                origin if origin else "*"
            )
            if allow_credentials:
                response.headers["Access-Control-Allow-Credentials"] = "true"
            if expose_headers:
                response.headers["Access-Control-Expose-Headers"] = ", ".join(expose_headers)
        return response

    return cors_middleware


# ---------- Rate Limiter Middleware ----------


def rate_limiter(
    max_requests: int = 10,
    window_seconds: float = 1.0,
    key_func: Callable[[Request], str] | None = None,
) -> Middleware:
    """Token-bucket rate limiter middleware factory.

    Example:
        app = Router()
        app.use(rate_limiter(max_requests=60, window_seconds=60))
    """
    if key_func is None:
        def key_func(req: Request) -> str:
            return req.headers.get("x-forwarded-for", req.headers.get("host", "unknown"))

    bucket: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_refill)

    def refill(key: str, now: float) -> tuple[float, float]:
        tokens, last_refill = bucket.get(key, (float(max_requests), 0.0))
        elapsed = now - last_refill
        new_tokens = min(float(max_requests), tokens + elapsed * (max_requests / window_seconds))
        return new_tokens, now

    def rate_middleware(req: Request, nxt: Callable[[Request], Response]) -> Response:
        key = key_func(req)
        now = time.monotonic()
        tokens, last_refill = refill(key, now)
        bucket[key] = (tokens - 1.0, last_refill)
        if tokens < 1.0:
            return Response(
                {"error": "rate limit exceeded"},
                status=429,
                headers={
                    "Retry-After": str(int(window_seconds)),
                    "X-RateLimit-Limit": str(max_requests),
                },
            )
        response = nxt(req)
        response.headers["X-RateLimit-Remaining"] = str(int(tokens - 1))
        return response

    return rate_middleware


# ---------- Status text ----------


_STATUS_TEXTS = {
    200: "OK",
    201: "Created",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


def status_text(status: int) -> str:
    return _STATUS_TEXTS.get(status, "OK")


# ---------- Built-in HTTP server ----------


def _make_handler(router: Router) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return  # silence default logging; users can override

        def _run(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = self.rfile.read(length) if length else b""
                request = Request(
                    method=method,
                    path=self.path.split("?", 1)[0],
                    query=parse_qs(self.path.split("?", 1)[1], keep_blank_values=True)
                    if "?" in self.path
                    else {},
                    headers={k: v for k, v in self.headers.items()},
                    body=body,
                )
                response = router._dispatch(request)
                # _dispatch guarantees Response (async coros already awaited)
                if "content-type" not in {k.lower() for k in response.headers}:
                    response.headers["content-type"] = "application/json"
                self.send_response(response.status, status_text(response.status))
                for k, v in response.headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(response.body)))
                self.end_headers()
                if response.body:
                    self.wfile.write(response.body)
            except Exception as exc:  # noqa: BLE001
                self.send_response(500, "Internal Server Error")
                self.send_header("Content-Type", "application/json")
                payload = json.dumps({"error": str(exc)}).encode("utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            self._run("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._run("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._run("PUT")

        def do_PATCH(self) -> None:  # noqa: N802
            self._run("PATCH")

        def do_DELETE(self) -> None:  # noqa: N802
            self._run("DELETE")

        def do_HEAD(self) -> None:  # noqa: N802
            self._run("HEAD")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._run("OPTIONS")

    return _Handler


def serve(
    router: Router,
    host: str = "127.0.0.1",
    port: int = 8000,
    threaded: bool = True,
) -> None:
    """Start a stdlib HTTP server for the given router. Blocks forever."""
    handler_cls = _make_handler(router)
    server_cls = ThreadingHTTPServer if threaded else BaseHTTPServer  # type: ignore[name-defined]
    httpd = server_cls((host, port), handler_cls)
    print(f"tiny-router v{__version__} serving on http://{host}:{port} (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        httpd.server_close()


__all__ = [
    "Router",
    "Request",
    "Response",
    "StreamingResponse",
    "AsyncResponse",
    "Depends",
    "HTTPError",
    "WebSocket",
    "Middleware",
    "Handler",
    "cors",
    "rate_limiter",
    "serve",
    "status_text",
    "__version__",
]