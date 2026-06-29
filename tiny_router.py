"""tiny_router — zero-dependency HTTP router for Python.

A single-file HTTP routing library with path parameters, middleware, and
a built-in WSGI server. No external packages required.

Usage:
    from tiny_router import Router, Response, serve

    app = Router()

    @app.get("/")
    def home(req):
        return {"hello": "world"}

    @app.get("/users/{id}")
    def user(req, id: str):
        return {"id": int(id)}

    @app.post("/items")
    def create(req):
        return {"created": True}, 201

    if __name__ == "__main__":
        serve(app, host="127.0.0.1", port=8000)
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs


__version__ = "0.1.0"


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
        self.query = query
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
Middleware = Callable[[Request, Callable[[Request], Response]], Response]


class Router:
    """A tiny HTTP router. Register handlers with @app.get/@app.post etc."""

    def __init__(self) -> None:
        self._routes: list[tuple[re.Pattern[str], str, Handler]] = []
        self._middlewares: list[Middleware] = []
        self._error_handlers: dict[int, Handler] = {}
        self._not_found: Handler | None = None

    # ---- registration ----

    def add(self, method: str, pattern: str) -> Callable[[Handler], Handler]:
        method = method.upper()
        compiled = _compile_path(pattern)

        def decorator(fn: Handler) -> Handler:
            self._routes.append((compiled, method, fn))
            return fn

        return decorator

    def get(self, pattern: str) -> Callable[[Handler], Handler]:
        return self.add("GET", pattern)

    def post(self, pattern: str) -> Callable[[Handler], Handler]:
        return self.add("POST", pattern)

    def put(self, pattern: str) -> Callable[[Handler], Handler]:
        return self.add("PUT", pattern)

    def patch(self, pattern: str) -> Callable[[Handler], Handler]:
        return self.add("PATCH", pattern)

    def delete(self, pattern: str) -> Callable[[Handler], Handler]:
        return self.add("DELETE", pattern)

    def head(self, pattern: str) -> Callable[[Handler], Handler]:
        return self.add("HEAD", pattern)

    def options(self, pattern: str) -> Callable[[Handler], Handler]:
        return self.add("OPTIONS", pattern)

    def route(self, method: str, pattern: str) -> Callable[[Handler], Handler]:
        return self.add(method, pattern)

    # ---- middleware ----

    def use(self, middleware: Middleware) -> "Router":
        """Register a middleware. Order matters: outermost first."""
        self._middlewares.append(middleware)
        return self

    # ---- error handlers ----

    def on_error(self, status: int) -> Callable[[Handler], Handler]:
        def decorator(fn: Handler) -> Handler:
            self._error_handlers[status] = fn
            return fn

        return decorator

    def not_found(self, fn: Handler) -> Handler:
        self._not_found = fn
        return fn

    # ---- dispatch ----

    def _dispatch(self, request: Request) -> Response:
        # Apply middleware chain
        def core(req: Request) -> Response:
            return self._handle(req)

        chain: Callable[[Request], Response] = core
        for mw in reversed(self._middlewares):
            next_in_chain = chain

            def make(m: Middleware, nxt: Callable[[Request], Response]) -> Middleware:
                def wrapped(req: Request) -> Response:
                    return m(req, nxt)

                return wrapped

            chain = make(mw, next_in_chain)

        try:
            return chain(request)
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status", 500)
            if status in self._error_handlers:
                return self._normalize(self._error_handlers[status](request, exc))
            return Response({"error": str(exc)}, status=status)

    def _handle(self, request: Request) -> Response:
        for pattern, method, handler in self._routes:
            if method != request.method:
                continue
            match = pattern.match(request.path)
            if match:
                request.params.update(match.groupdict())
                return self._normalize(handler(request, **request.params))

        if request.method == "OPTIONS":
            return Response("", status=204, headers={"Allow": "GET, POST, PUT, PATCH, DELETE, OPTIONS"})

        if self._not_found is not None:
            return self._normalize(self._not_found(request))

        return Response({"error": "not found", "path": request.path}, status=404)

    @staticmethod
    def _normalize(result: Any) -> Response:
        if isinstance(result, Response):
            return result
        if isinstance(result, tuple):
            body, status = (result[0], result[1]) if len(result) >= 2 else (result[0], 200)
            headers = result[2] if len(result) >= 3 else None
            return Response(body, status=status, headers=headers)
        return Response(result, status=200)

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
        if "content-type" not in {k.lower() for k in response.headers}:
            response.headers["content-type"] = "application/json"
        start_response(
            f"{response.status} {status_text(response.status)}",
            [(k, v) for k, v in response.headers.items()],
        )
        return [response.body]

    __call__ = wsgi


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
    500: "Internal Server Error",
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
    print(f"tiny-router serving on http://{host}:{port} (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        httpd.server_close()


__all__ = [
    "Router",
    "Request",
    "Response",
    "Middleware",
    "Handler",
    "serve",
    "status_text",
    "__version__",
]
