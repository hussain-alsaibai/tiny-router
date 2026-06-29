"""Tests for tiny_router. Run with: python test_tiny_router.py"""

from __future__ import annotations

import json
import unittest
from io import BytesIO

from tiny_router import Request, Response, Router, serve, status_text


def _wsgi_call(app: Router, method: str, path: str, body: bytes = b"") -> tuple[int, dict, bytes]:
    captured: dict = {}

    def start_response(status: str, headers: list) -> None:
        captured["status"] = status
        captured["headers"] = headers

    if "?" in path:
        path_only, qs = path.split("?", 1)
    else:
        path_only, qs = path, ""

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path_only,
        "QUERY_STRING": qs,
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": "application/json",
        "wsgi.input": BytesIO(body),
        "SERVER_NAME": "test",
        "SERVER_PORT": "80",
        "wsgi.url_scheme": "http",
        "wsgi.errors": BytesIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": True,
        "wsgi.version": (1, 0),
    }
    result = b"".join(app.wsgi(environ, start_response))
    status_code = int(captured["status"].split(" ", 1)[0])
    headers = {k.lower(): v for k, v in captured["headers"]}
    return status_code, headers, result


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
        def get_user(req: Request, id: str) -> dict:  # noqa: A002
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
        self.assertEqual(status, 404)  # not matched

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


class TestMiddleware(unittest.TestCase):
    def test_middleware_chain(self) -> None:
        app = Router()
        order: list[str] = []

        def mw_a(req: Request, nxt) -> Response:  # type: ignore[no-untyped-def]
            order.append("a-pre")
            r = nxt(req)
            order.append("a-post")
            return r

        def mw_b(req: Request, nxt) -> Response:  # type: ignore[no-untyped-def]
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

        def auth(req: Request, nxt) -> Response:  # type: ignore[no-untyped-def]
            if req.headers.get("authorization") != "secret":
                return Response({"error": "unauthorized"}, status=401)
            return nxt(req)

        app.use(auth)

        @app.get("/secret")
        def secret(req: Request) -> dict:
            return {"data": "ok"}

        status, _h, _b = _wsgi_call(app, "GET", "/secret")
        self.assertEqual(status, 401)


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


class TestHelpers(unittest.TestCase):
    def test_status_text(self) -> None:
        self.assertEqual(status_text(200), "OK")
        self.assertEqual(status_text(404), "Not Found")
        self.assertEqual(status_text(418), "OK")  # fallback

    def test_serve_imports(self) -> None:
        # Just verify the symbol is exported; don't actually start a server.
        self.assertTrue(callable(serve))


if __name__ == "__main__":
    unittest.main(verbosity=2)
