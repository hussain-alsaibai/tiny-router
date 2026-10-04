"""v0.6.0 — Tests for Router.from_blueprint / from_json / from_yaml."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from io import BytesIO

from tiny_router import Router


def _wsgi_call(app, method, path, body=b"", headers=None):
    """Call app via WSGI and return (status, body_bytes)."""
    captured = {}

    def start_response(status, headers_list):
        captured["status"] = status

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": BytesIO(body),
        "wsgi.url_scheme": "http",
        "SERVER_NAME": "test",
        "SERVER_PORT": "80",
    }
    for k, v in (headers or {}).items():
        environ[f"HTTP_{k.upper().replace('-', '_')}"] = v
    result = app.wsgi(environ, start_response)
    return captured["status"], b"".join(result)


class TestFromBlueprint(unittest.TestCase):
    def test_basic_named_handlers(self) -> None:
        def get_user(req, id):
            return {"id": id}

        app = Router.from_blueprint(
            {
                "routes": [
                    {"method": "GET", "path": "/users/{id}", "handler": "get_user"},
                ],
            },
            handlers={"get_user": get_user},
        )
        s, b = _wsgi_call(app, "GET", "/users/42")
        self.assertEqual(s, "200 OK")
        self.assertEqual(json.loads(b), {"id": "42"})

    def test_inline_callable_handler(self) -> None:
        app = Router.from_blueprint({
            "routes": [
                {"method": "GET", "path": "/health", "handler": lambda req: {"ok": True}},
            ],
        })
        s, b = _wsgi_call(app, "GET", "/health")
        self.assertEqual(s, "200 OK")
        self.assertEqual(json.loads(b), {"ok": True})

    def test_tags_preserved(self) -> None:
        app = Router.from_blueprint({
            "routes": [
                {"method": "GET", "path": "/x", "handler": "f", "tags": ["a", "b"]},
            ],
        }, handlers={"f": lambda req: {}})
        routes = app.get_routes()
        self.assertEqual(routes[0]["tags"], ["a", "b"])

    def test_static_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "hello.txt"), "w") as f:
                f.write("hi there")
            app = Router.from_blueprint({
                "static": [{"prefix": "/static", "directory": tmp}],
            })
            s, b = _wsgi_call(app, "GET", "/static/hello.txt")
            self.assertEqual(s, "200 OK")
            self.assertEqual(b, b"hi there")

    def test_method_case_insensitive(self) -> None:
        app = Router.from_blueprint({
            "routes": [
                {"method": "get", "path": "/x", "handler": "f"},
                {"method": "Post", "path": "/x", "handler": "f"},
            ],
        }, handlers={"f": lambda req: {}})
        methods = sorted([r["method"] for r in app.get_routes()])
        self.assertEqual(methods, ["GET", "POST"])

    def test_list_of_blueprints_merges_routes(self) -> None:
        app = Router.from_blueprint([
            {"routes": [{"method": "GET", "path": "/a", "handler": "fa"}]},
            {"routes": [{"method": "GET", "path": "/b", "handler": "fb"}]},
        ], handlers={
            "fa": lambda req: {"a": 1},
            "fb": lambda req: {"b": 2},
        })
        s, b = _wsgi_call(app, "GET", "/a")
        self.assertEqual(s, "200 OK")
        self.assertEqual(json.loads(b), {"a": 1})
        s, b = _wsgi_call(app, "GET", "/b")
        self.assertEqual(s, "200 OK")
        self.assertEqual(json.loads(b), {"b": 2})

    def test_nested_mount(self) -> None:
        app = Router.from_blueprint({
            "mount": [
                {
                    "prefix": "/api/v1",
                    "blueprint": {
                        "routes": [
                            {"method": "GET", "path": "/ping", "handler": "p"},
                        ],
                    },
                },
            ],
        }, handlers={"p": lambda req: {"pong": True}})
        s, b = _wsgi_call(app, "GET", "/api/v1/ping")
        self.assertEqual(s, "200 OK")
        self.assertEqual(json.loads(b), {"pong": True})

    def test_missing_handler_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            Router.from_blueprint({
                "routes": [{"method": "GET", "path": "/x", "handler": "missing"}],
            })
        self.assertIn("missing", str(ctx.exception))

    def test_no_handler_raises(self) -> None:
        with self.assertRaises(ValueError):
            Router.from_blueprint({
                "routes": [{"method": "GET", "path": "/x"}],
            })

    def test_non_callable_non_string_handler_raises(self) -> None:
        with self.assertRaises(ValueError):
            Router.from_blueprint({
                "routes": [{"method": "GET", "path": "/x", "handler": 42}],
            })

    def test_invalid_blueprint_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            Router.from_blueprint("not a dict")

    def test_path_param_works(self) -> None:
        app = Router.from_blueprint({
            "routes": [
                {"method": "GET", "path": "/items/{item_id}", "handler": "f"},
            ],
        }, handlers={"f": lambda req, item_id: {"id": item_id}})
        s, b = _wsgi_call(app, "GET", "/items/abc")
        self.assertEqual(s, "200 OK")
        self.assertEqual(json.loads(b), {"id": "abc"})

    def test_empty_blueprint_works(self) -> None:
        app = Router.from_blueprint({})
        self.assertEqual(app.get_routes(), [])

    def test_static_path_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "f.txt"), "w") as fp:
                fp.write("ok")
            app = Router.from_blueprint({
                "static": [{"prefix": "/files/", "directory": tmp}],
            })
            s, b = _wsgi_call(app, "GET", "/files/f.txt")
            self.assertEqual(s, "200 OK")
            self.assertEqual(b, b"ok")


class TestFromJson(unittest.TestCase):
    def test_from_json_file(self) -> None:
        cfg = {
            "routes": [
                {"method": "GET", "path": "/health", "handler": "h"},
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(cfg, f)
            path = f.name
        try:
            app = Router.from_json(
                path, handlers={"h": lambda req: {"ok": True}}
            )
            s, b = _wsgi_call(app, "GET", "/health")
            self.assertEqual(s, "200 OK")
            self.assertEqual(json.loads(b), {"ok": True})
        finally:
            os.unlink(path)


class TestFromYaml(unittest.TestCase):
    def test_from_yaml_missing_pyyaml(self) -> None:
        try:
            import yaml  # noqa: F401
            self.skipTest("PyYAML is installed; from_yaml round-trip is covered by from_json")
        except ImportError:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as f:
                f.write("routes: []")
                path = f.name
            try:
                with self.assertRaises(ImportError) as ctx:
                    Router.from_yaml(path)
                self.assertIn("PyYAML", str(ctx.exception))
            finally:
                os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
