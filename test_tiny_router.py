"""Tests for tiny_router. Run with: python test_tiny_router.py -v"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from io import BytesIO

from tiny_router import (
    Request,
    Response,
    Router,
    StreamingResponse,
    cors,
    rate_limiter,
    serve,
    status_text,
)


def _wsgi_call(
    app: Router, method: str, path: str, body: bytes = b"", headers: dict | None = None
) -> tuple[int, dict, bytes]:
    captured: dict = {}

    def start_response(status: str, headers_list: list) -> None:
        captured["status"] = status
        captured["headers"] = headers_list

    if "?" in path:
        path_only, qs = path.split("?", 1)
    else:
        path_only, qs = path, ""

    hdrs = dict(headers or {})
    environ: dict = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path_only,
        "QUERY_STRING": qs,
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": "application/json",
        "SERVER_NAME": "test",
        "SERVER_PORT": "80",
        "wsgi.url_scheme": "http",
        "wsgi.errors": BytesIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": True,
        "wsgi.version": (1, 0),
    }
    # Add custom headers as HTTP_* environ vars
    for k, v in hdrs.items():
        environ[f"HTTP_{k.upper().replace('-', '_')}"] = v
    # Special case for content-type to avoid conflict
    if "content-type" in hdrs:
        environ["CONTENT_TYPE"] = hdrs["content-type"]
    environ["wsgi.input"] = BytesIO(body) if body else BytesIO(b"")

    result = b"".join(app.wsgi(environ, start_response))
    status_code = int(captured["status"].split(" ", 1)[0])
    headers_dict = {k.lower(): v for k, v in captured["headers"]}
    return status_code, headers_dict, result


class TestRouting(unittest.TestCase):
    def test_basic_get(self) -> None:
        app = Router()

        @app.get("/")
        def home(req: Request) -> dict:
            return {"hello": "world"}

        status, _h, body = _wsgi_call(app, "GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"hello": "world"})

    def test_path_param(self) -> None:
        app = Router()

        @app.get("/users/{id}")
        def get_user(req: Request, id: str) -> dict:
            return {"id": int(id)}

        status, _h, body = _wsgi_call(app, "GET", "/users/42")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"id": 42})

    def test_post_with_json(self) -> None:
        app = Router()

        @app.post("/items")
        def create(req: Request) -> tuple:
            data = req.json
            return {"created": True, "name": data["name"]}, 201

        status, _h, body = _wsgi_call(
            app, "POST", "/items", body=json.dumps({"name": "thing"}).encode()
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body), {"created": True, "name": "thing"})

    def test_404(self) -> None:
        app = Router()

        @app.get("/")
        def home(req: Request) -> dict:
            return {}

        status, _h, body = _wsgi_call(app, "GET", "/missing")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(body))

    def test_custom_not_found(self) -> None:
        app = Router()

        @app.get("/")
        def home(req: Request) -> dict:
            return {}

        @app.not_found
        def nf(req: Request) -> Response:
            return Response({"custom": "miss"}, status=404)

        status, _h, body = _wsgi_call(app, "GET", "/missing")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body), {"custom": "miss"})

    def test_method_not_allowed(self) -> None:
        app = Router()

        @app.get("/")
        def home(req: Request) -> dict:
            return {}

        status, _h, _b = _wsgi_call(app, "POST", "/")
        self.assertEqual(status, 404)

    def test_multiple_params(self) -> None:
        app = Router()

        @app.get("/orgs/{org}/repos/{repo}")
        def get_repo(req: Request, org: str, repo: str) -> dict:
            return {"org": org, "repo": repo}

        status, _h, body = _wsgi_call(app, "GET", "/orgs/acme/repos/widgets")
        self.assertEqual(json.loads(body), {"org": "acme", "repo": "widgets"})

    def test_query_params(self) -> None:
        app = Router()

        @app.get("/search")
        def search(req: Request) -> dict:
            return {"q": req.query.get("q", [""])[0]}

        status, _h, body = _wsgi_call(app, "GET", "/search?q=python")
        self.assertEqual(json.loads(body), {"q": "python"})


class TestQueryParamParsing(unittest.TestCase):
    def test_query_param_default(self) -> None:
        app = Router()

        @app.get("/items")
        def items(req: Request) -> dict:
            page = req.query_param("page", default=1, cast=int)
            return {"page": page}

        status, _h, body = _wsgi_call(app, "GET", "/items")
        self.assertEqual(json.loads(body), {"page": 1})

    def test_query_param_int_coercion(self) -> None:
        app = Router()

        @app.get("/items")
        def items(req: Request) -> dict:
            page = req.query_param("page", default=1, cast=int)
            limit = req.query_param("limit", default=10, cast=int)
            return {"page": page, "limit": limit}

        status, _h, body = _wsgi_call(app, "GET", "/items?page=3&limit=20")
        self.assertEqual(json.loads(body), {"page": 3, "limit": 20})

    def test_query_param_bool_coercion(self) -> None:
        app = Router()

        @app.get("/items")
        def items(req: Request) -> dict:
            active = req.query_param("active", default=False, cast=bool)
            return {"active": active}

        status, _h, body = _wsgi_call(app, "GET", "/items?active=true")
        self.assertEqual(json.loads(body), {"active": True})

        status, _h, body = _wsgi_call(app, "GET", "/items?active=false")
        self.assertFalse(json.loads(body)["active"])

    def test_query_param_float_coercion(self) -> None:
        app = Router()

        @app.get("/items")
        def items(req: Request) -> dict:
            price = req.query_param("price", default=0.0, cast=float)
            return {"price": price}

        status, _h, body = _wsgi_call(app, "GET", "/items?price=19.99")
        self.assertEqual(json.loads(body), {"price": 19.99})

    def test_query_params_multi(self) -> None:
        app = Router()

        @app.get("/items")
        def items(req: Request) -> dict:
            tags = req.query_params("tag")
            return {"tags": tags}

        status, _h, body = _wsgi_call(app, "GET", "/items?tag=a&tag=b&tag=c")
        self.assertEqual(json.loads(body), {"tags": ["a", "b", "c"]})


class TestMiddleware(unittest.TestCase):
    def test_middleware_chain(self) -> None:
        app = Router()
        order: list[str] = []

        def mw_a(req: Request, nxt) -> Response:
            order.append("a-pre")
            r = nxt(req)
            order.append("a-post")
            return r

        def mw_b(req: Request, nxt) -> Response:
            order.append("b-pre")
            r = nxt(req)
            order.append("b-post")
            return r

        app.use(mw_a)
        app.use(mw_b)

        @app.get("/")
        def home(req: Request) -> dict:
            order.append("handler")
            return {}

        status, _h, _b = _wsgi_call(app, "GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(order, ["a-pre", "b-pre", "handler", "b-post", "a-post"])

    def test_middleware_can_short_circuit(self) -> None:
        app = Router()

        def auth(req: Request, nxt) -> Response:
            if req.headers.get("authorization") != "secret":
                return Response({"error": "unauthorized"}, status=401)
            return nxt(req)

        app.use(auth)

        @app.get("/secret")
        def secret(req: Request) -> dict:
            return {"data": "ok"}

        status, _h, _b = _wsgi_call(app, "GET", "/secret")
        self.assertEqual(status, 401)


class TestCORSMiddleware(unittest.TestCase):
    def test_cors_allow_all(self) -> None:
        app = Router()
        app.use(cors(allow_origins=["*"]))

        @app.get("/")
        def home(req: Request) -> dict:
            return {"hello": "world"}

        status, headers, body = _wsgi_call(
            app, "GET", "/", headers={"origin": "https://example.com"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("access-control-allow-origin"), "https://example.com")

    def test_cors_preflight(self) -> None:
        app = Router()
        app.use(cors(allow_origins=["https://example.com"]))

        @app.get("/")
        def home(req: Request) -> dict:
            return {"hello": "world"}

        status, headers, body = _wsgi_call(
            app,
            "OPTIONS",
            "/",
            headers={
                "origin": "https://example.com",
                "access-control-request-method": "GET",
            },
        )
        self.assertEqual(status, 204)
        self.assertEqual(
            headers.get("access-control-allow-origin"), "https://example.com"
        )
        self.assertIn("GET", headers.get("access-control-allow-methods", ""))

    def test_cors_restricted_origin(self) -> None:
        app = Router()
        app.use(cors(allow_origins=["https://trusted.com"]))

        @app.get("/")
        def home(req: Request) -> dict:
            return {"hello": "world"}

        status, headers, body = _wsgi_call(
            app, "GET", "/", headers={"origin": "https://evil.com"}
        )
        self.assertEqual(status, 200)
        self.assertNotIn("access-control-allow-origin", headers)

    def test_cors_credentials(self) -> None:
        app = Router()
        app.use(cors(allow_origins=["https://example.com"], allow_credentials=True))

        @app.get("/")
        def home(req: Request) -> dict:
            return {"hello": "world"}

        status, headers, body = _wsgi_call(
            app, "GET", "/", headers={"origin": "https://example.com"}
        )
        self.assertEqual(headers.get("access-control-allow-credentials"), "true")

    def test_cors_expose_headers(self) -> None:
        app = Router()
        app.use(cors(allow_origins=["*"], expose_headers=["x-custom-header"]))

        @app.get("/")
        def home(req: Request) -> dict:
            return {"hello": "world"}

        status, headers, body = _wsgi_call(
            app, "GET", "/", headers={"origin": "https://example.com"}
        )
        self.assertIn("x-custom-header", headers.get("access-control-expose-headers", ""))


class TestStreamingResponse(unittest.TestCase):
    def test_streaming_basic(self) -> None:
        app = Router()

        @app.get("/stream")
        def stream(req: Request) -> StreamingResponse:
            def generate():
                for i in range(3):
                    yield f"chunk {i}"
            return StreamingResponse(generate(), headers={"content-type": "text/plain"})

        status, headers, body = _wsgi_call(app, "GET", "/stream")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"chunk 0chunk 1chunk 2")

    def test_streaming_with_status(self) -> None:
        app = Router()

        @app.get("/events")
        def events(req: Request) -> StreamingResponse:
            def generate():
                yield '{"event": "connected"}'
            return StreamingResponse(generate(), status=201)

        status, headers, body = _wsgi_call(app, "GET", "/events")
        self.assertEqual(status, 201)
        self.assertEqual(body, b'{"event": "connected"}')

    def test_streaming_bytes_yield(self) -> None:
        app = Router()

        @app.get("/binary")
        def binary(req: Request) -> StreamingResponse:
            def generate():
                yield b"\x00\x01\x02"
            return StreamingResponse(generate())

        status, headers, body = _wsgi_call(app, "GET", "/binary")
        self.assertEqual(body, b"\x00\x01\x02")

    def test_streaming_dict_yield(self) -> None:
        app = Router()

        @app.get("/dict-stream")
        def dict_stream(req: Request) -> StreamingResponse:
            def generate():
                yield {"msg": "hello"}
                yield {"msg": "world"}
            return StreamingResponse(generate())

        status, headers, body = _wsgi_call(app, "GET", "/dict-stream")
        # Each chunk is JSON-serialized independently; the body is concatenated
        chunks = body.split(b"}{") if b"}{" in body else [body]
        if len(chunks) > 1:
            chunks = [chunks[0] + b"}", b"{" + chunks[1]]
        result = [json.loads(c) for c in chunks]
        self.assertEqual(result, [{"msg": "hello"}, {"msg": "world"}])


class TestStaticFileServing(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.index_path = os.path.join(self.temp_dir, "index.html")
        with open(self.index_path, "w") as f:
            f.write("<h1>Hello</h1>")

        self.css_path = os.path.join(self.temp_dir, "style.css")
        with open(self.css_path, "w") as f:
            f.write("body { color: red; }")

        # Nested directory
        self.nested_dir = os.path.join(self.temp_dir, "js")
        os.makedirs(self.nested_dir, exist_ok=True)
        self.js_path = os.path.join(self.nested_dir, "app.js")
        with open(self.js_path, "w") as f:
            f.write("console.log('hi');")

    def test_static_file_serving(self) -> None:
        app = Router()
        app.static("/static", self.temp_dir)

        status, headers, body = _wsgi_call(app, "GET", "/static/style.css")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"body { color: red; }")
        self.assertIn("text/css", headers.get("content-type", ""))

    def test_static_html(self) -> None:
        app = Router()
        app.static("/static", self.temp_dir)

        status, headers, body = _wsgi_call(app, "GET", "/static/index.html")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"<h1>Hello</h1>")
        self.assertIn("text/html", headers.get("content-type", ""))

    def test_static_nested(self) -> None:
        app = Router()
        app.static("/static", self.temp_dir)

        status, headers, body = _wsgi_call(app, "GET", "/static/js/app.js")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"console.log('hi');")
        self.assertIn("javascript", headers.get("content-type", ""))

    def test_static_404(self) -> None:
        app = Router()
        app.static("/static", self.temp_dir)

        status, headers, body = _wsgi_call(app, "GET", "/static/nonexistent.txt")
        self.assertEqual(status, 404)

    def test_static_path_traversal_blocked(self) -> None:
        app = Router()
        app.static("/static", self.temp_dir)

        status, headers, body = _wsgi_call(app, "GET", "/static/../../etc/passwd")
        self.assertEqual(status, 403)

    def test_static_only_get(self) -> None:
        app = Router()
        app.static("/static", self.temp_dir)

        status, headers, body = _wsgi_call(app, "POST", "/static/style.css")
        self.assertEqual(status, 405)


class TestSubRouterMounting(unittest.TestCase):
    def test_mounted_sub_router(self) -> None:
        app = Router()
        api = Router(prefix="/api")

        @api.get("/users")
        def list_users(req: Request) -> dict:
            return {"users": ["alice", "bob"]}

        app.mount("/api", api)

        status, headers, body = _wsgi_call(app, "GET", "/api/users")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"users": ["alice", "bob"]})

    def test_multiple_mounted_routers(self) -> None:
        app = Router()
        api = Router(prefix="/api")
        admin = Router(prefix="/admin")

        @api.get("/items")
        def list_items(req: Request) -> dict:
            return {"items": ["widget"]}

        @admin.get("/stats")
        def stats(req: Request) -> dict:
            return {"visits": 42}

        app.mount("/api", api)
        app.mount("/admin", admin)

        status, headers, body = _wsgi_call(app, "GET", "/api/items")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"items": ["widget"]})

        status, headers, body = _wsgi_call(app, "GET", "/admin/stats")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"visits": 42})

    def test_mounted_404(self) -> None:
        app = Router()
        api = Router(prefix="/api")

        @api.get("/users")
        def list_users(req: Request) -> dict:
            return {"users": []}

        app.mount("/api", api)

        status, headers, body = _wsgi_call(app, "GET", "/api/missing")
        self.assertEqual(status, 404)

    def test_mounted_router_with_tags(self) -> None:
        app = Router()
        admin = Router(prefix="/admin")

        @admin.get("/dashboard", tags=["admin", "monitoring"])
        def dashboard(req: Request) -> dict:
            return {"ok": True}

        app.mount("/admin", admin)

        status, headers, body = _wsgi_call(app, "GET", "/admin/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True})


class TestErrorHandlers(unittest.TestCase):
    def test_on_error_value_error(self) -> None:
        app = Router()

        @app.on_error(ValueError)
        def handle_value_error(req: Request, exc: Exception) -> tuple:
            return {"error": "bad value", "detail": str(exc)}, 400

        @app.get("/items/{count}")
        def items(req: Request, count: str) -> dict:
            n = int(count)
            if n < 0:
                raise ValueError(f"negative count: {n}")
            return {"count": n}

        # Negative value triggers ValueError -> 400
        status, headers, body = _wsgi_call(app, "GET", "/items/-1")
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))
        self.assertIn("negative count", json.loads(body)["detail"])

        # Positive value works normally
        status, headers, body = _wsgi_call(app, "GET", "/items/5")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"count": 5})

    def test_on_error_permission_error(self) -> None:
        app = Router()

        @app.on_error(PermissionError)
        def handle_permission(req: Request, exc: Exception) -> tuple:
            return {"error": "forbidden"}, 403

        @app.get("/secret")
        def secret(req: Request) -> dict:
            raise PermissionError("access denied")

        status, headers, body = _wsgi_call(app, "GET", "/secret")
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body), {"error": "forbidden"})

    def test_on_error_generic_exception(self) -> None:
        app = Router()

        @app.on_error(Exception)
        def handle_any(req: Request, exc: Exception) -> tuple:
            return {"error": "something went wrong"}, 500

        @app.get("/crash")
        def crash(req: Request) -> dict:
            raise RuntimeError("boom")

        status, headers, body = _wsgi_call(app, "GET", "/crash")
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(body), {"error": "something went wrong"})

    def test_on_status_error_handler(self) -> None:
        app = Router()

        # on_status registers a handler for HTTP-level exception status codes
        @app.on_status(400)
        def handle_bad_request(req: Request, exc: Exception) -> Response:
            return Response({"custom": "bad request handled"}, status=400)

        @app.get("/items/{count}")
        def items(req: Request, count: str) -> dict:
            n = int(count)
            if n < 0:
                # Raise with a status attribute so on_status can catch it
                exc = Exception("bad request")
                exc.status = 400
                raise exc
            return {"count": n}

        status, headers, body = _wsgi_call(app, "GET", "/items/-1")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body).get("custom"), "bad request handled")


class TestRateLimiter(unittest.TestCase):
    def test_rate_limiter_allows_requests(self) -> None:
        app = Router()
        app.use(rate_limiter(max_requests=10, window_seconds=60))

        @app.get("/")
        def home(req: Request) -> dict:
            return {"ok": True}

        status, headers, body = _wsgi_call(app, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("x-ratelimit-remaining", headers)

    def test_rate_limiter_blocks_when_exhausted(self) -> None:
        app = Router()
        app.use(rate_limiter(max_requests=1, window_seconds=60))

        @app.get("/")
        def home(req: Request) -> dict:
            return {"ok": True}

        # First request should succeed
        status1, headers1, body1 = _wsgi_call(app, "GET", "/")
        self.assertEqual(status1, 200)

        # Second request should be rate limited
        status2, headers2, body2 = _wsgi_call(app, "GET", "/")
        self.assertEqual(status2, 429)

    def test_rate_limiter_custom_key(self) -> None:
        def ip_key(req: Request) -> str:
            return req.headers.get("x-forwarded-for", "unknown")

        app = Router()
        app.use(rate_limiter(max_requests=1, window_seconds=60, key_func=ip_key))

        @app.get("/")
        def home(req: Request) -> dict:
            return {"ok": True}

        # Different IPs should be allowed
        status1, _h, _b = _wsgi_call(
            app, "GET", "/", headers={"x-forwarded-for": "10.0.0.1"}
        )
        self.assertEqual(status1, 200)

        status2, _h, _b = _wsgi_call(
            app, "GET", "/", headers={"x-forwarded-for": "10.0.0.2"}
        )
        self.assertEqual(status2, 200)

        # Same IP should be blocked
        status3, _h, _b = _wsgi_call(
            app, "GET", "/", headers={"x-forwarded-for": "10.0.0.1"}
        )
        self.assertEqual(status3, 429)


class TestRouteGroups(unittest.TestCase):
    def test_route_tags(self) -> None:
        app = Router()

        @app.get("/health", tags=["monitoring"])
        def health(req: Request) -> dict:
            return {"status": "ok"}

        @app.get("/users", tags=["users"])
        def users(req: Request) -> dict:
            return {"users": []}

        @app.get("/metrics", tags=["monitoring"])
        def metrics(req: Request) -> dict:
            return {"count": 42}

        monitoring_routes = app.get_routes(tag="monitoring")
        self.assertEqual(len(monitoring_routes), 2)
        for r in monitoring_routes:
            self.assertIn("monitoring", r["tags"])

        all_routes = app.get_routes()
        self.assertEqual(len(all_routes), 3)

    def test_route_tags_none(self) -> None:
        app = Router()

        @app.get("/")
        def home(req: Request) -> dict:
            return {}

        routes = app.get_routes()
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["tags"], [])


class TestResponse(unittest.TestCase):
    def test_response_string(self) -> None:
        r = Response("hi")
        self.assertEqual(r.body, b"hi")
        self.assertEqual(r.status, 200)

    def test_response_dict(self) -> None:
        r = Response({"k": 1})
        self.assertEqual(r.body, b'{"k": 1}')

    def test_response_with_status(self) -> None:
        r = Response(None, status=204)
        self.assertEqual(r.body, b"")

    def test_response_set_header(self) -> None:
        r = Response("ok").set_header("x-custom", "value")
        self.assertEqual(r.headers["x-custom"], "value")


class TestHelpers(unittest.TestCase):
    def test_status_text(self) -> None:
        self.assertEqual(status_text(200), "OK")
        self.assertEqual(status_text(404), "Not Found")
        self.assertEqual(status_text(429), "Too Many Requests")
        self.assertEqual(status_text(418), "OK")  # fallback

    def test_serve_imports(self) -> None:
        self.assertTrue(callable(serve))

    def test_version(self) -> None:
        from tiny_router import __version__
        self.assertEqual(__version__, "0.3.0")

    def test_depends_class_exists(self) -> None:
        from tiny_router import Depends, HTTPError, AsyncResponse
        self.assertTrue(callable(Depends))
        self.assertTrue(issubclass(HTTPError, Exception))
        self.assertIsNotNone(AsyncResponse)


class TestIntegration(unittest.TestCase):
    """Integration tests combining multiple features."""

    def test_cors_with_middleware_chain(self) -> None:
        """CORS middleware + auth middleware = both apply."""
        app = Router()
        app.use(cors(allow_origins=["*"]))

        order: list[str] = []

        def log_mw(req: Request, nxt) -> Response:
            order.append("log")
            return nxt(req)

        app.use(log_mw)

        @app.get("/")
        def home(req: Request) -> dict:
            order.append("handler")
            return {"ok": True}

        status, headers, body = _wsgi_call(
            app, "GET", "/", headers={"origin": "https://example.com"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("access-control-allow-origin"), "https://example.com")
        self.assertEqual(order, ["log", "handler"])

    def test_static_and_route_together(self) -> None:
        """Static files + normal routes coexist."""
        app = Router()
        temp_dir = tempfile.mkdtemp()
        with open(os.path.join(temp_dir, "test.txt"), "w") as f:
            f.write("static content")

        app.static("/files", temp_dir)

        @app.get("/api/status")
        def status(req: Request) -> dict:
            return {"status": "running"}

        # Static
        s, _h, b = _wsgi_call(app, "GET", "/files/test.txt")
        self.assertEqual(b, b"static content")

        # Route
        s, _h, b = _wsgi_call(app, "GET", "/api/status")
        self.assertEqual(json.loads(b), {"status": "running"})


class TestAsyncHandlers(unittest.TestCase):
    """v0.3.0: async def handlers are awaited automatically."""

    def test_async_handler(self) -> None:
        from tiny_router import Router, Request
        import asyncio

        app = Router()

        @app.get("/slow")
        async def slow(req: Request) -> dict:
            await asyncio.sleep(0)
            return {"ok": True, "kind": "async"}

        s, _h, b = _wsgi_call(app, "GET", "/slow")
        self.assertEqual(s, 200)
        self.assertEqual(json.loads(b), {"ok": True, "kind": "async"})

    def test_async_handler_error(self) -> None:
        from tiny_router import Router, Request

        app = Router()

        @app.get("/boom")
        async def boom(req: Request) -> dict:
            raise ValueError("async-bang")

        s, _h, b = _wsgi_call(app, "GET", "/boom")
        self.assertEqual(s, 500)
        self.assertIn("async-bang", json.loads(b)["error"])

    def test_get_routes_reports_async(self) -> None:
        from tiny_router import Router

        app = Router()

        @app.get("/sync")
        def sync_h(req):  # type: ignore
            return {}

        @app.get("/asyn")
        async def asyn_h(req):  # type: ignore
            return {}

        routes = app.get_routes()
        by_handler = {r["handler"]: r for r in routes}
        self.assertFalse(by_handler["sync_h"]["async"])
        self.assertTrue(by_handler["asyn_h"]["async"])


class TestDepends(unittest.TestCase):
    """v0.3.0: FastAPI-style Depends() dependency injection."""

    def test_simple_depends(self) -> None:
        from tiny_router import Router, Request, Depends

        app = Router()
        calls = []

        def get_user(req: Request) -> dict:
            calls.append(req.headers.get("authorization"))
            return {"name": "alice"}

        @app.get("/me")
        def me(req: Request, user=Depends(get_user)) -> dict:
            return user

        s, _h, b = _wsgi_call(app, "GET", "/me", headers={"authorization": "tok"})
        self.assertEqual(s, 200)
        self.assertEqual(json.loads(b), {"name": "alice"})
        self.assertEqual(calls, ["tok"])

    def test_depends_cached_per_request(self) -> None:
        from tiny_router import Router, Request, Depends

        app = Router()
        calls = []

        def expensive(req: Request) -> dict:
            calls.append(1)
            return {"expensive": True}

        @app.get("/cached")
        def handler(req: Request, x=Depends(expensive), y=Depends(expensive)) -> dict:
            return {"x": x, "y": y}

        s, _h, b = _wsgi_call(app, "GET", "/cached")
        self.assertEqual(s, 200)
        # expensive() should only have been called once per request
        self.assertEqual(len(calls), 1)

    def test_chained_depends(self) -> None:
        from tiny_router import Router, Request, Depends

        app = Router()

        def base(req: Request) -> str:
            return "base-value"

        def derived(req: Request, b=Depends(base)) -> dict:
            return {"derived_from": b}

        @app.get("/chain")
        def chain(req: Request, d=Depends(derived)) -> dict:
            return d

        s, _h, b = _wsgi_call(app, "GET", "/chain")
        self.assertEqual(s, 200)
        self.assertEqual(json.loads(b), {"derived_from": "base-value"})

    def test_http_error_in_depends(self) -> None:
        from tiny_router import Router, Request, Depends, HTTPError

        app = Router()

        def require_auth(req: Request) -> dict:
            if not req.headers.get("authorization"):
                raise HTTPError(401, "no auth")
            return {"ok": True}

        @app.get("/protected")
        def protected(req: Request, _u=Depends(require_auth)) -> dict:
            return {"ok": True}

        s, _h, b = _wsgi_call(app, "GET", "/protected")
        self.assertEqual(s, 401)
        self.assertEqual(json.loads(b), {"error": "no auth"})

    def test_async_handler_with_depends(self) -> None:
        from tiny_router import Router, Request, Depends
        import asyncio

        app = Router()

        def get_id(req: Request) -> str:
            return "id-42"

        @app.get("/items")
        async def items(req: Request, item_id=Depends(get_id)) -> dict:
            await asyncio.sleep(0)
            return {"item_id": item_id}

        s, _h, b = _wsgi_call(app, "GET", "/items")
        self.assertEqual(s, 200)
        self.assertEqual(json.loads(b), {"item_id": "id-42"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
