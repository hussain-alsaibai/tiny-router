"""Tests for tiny_router v0.4.0 — JWT auth, request_id, openapi_spec."""

from __future__ import annotations

import json
import time
import unittest
from io import BytesIO

from tiny_router import (
    Router,
    Request,
    Response,
    JWTError,
    jwt_decode,
    jwt_encode,
    jwt_auth,
    request_id,
    openapi_spec,
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
    for k, v in hdrs.items():
        environ[f"HTTP_{k.upper().replace('-', '_')}"] = v
    environ["wsgi.input"] = BytesIO(body) if body else BytesIO(b"")

    result = b"".join(app.wsgi(environ, start_response))
    status_code = int(captured["status"].split(" ", 1)[0])
    headers_dict = {k.lower(): v for k, v in captured["headers"]}
    return status_code, headers_dict, result


SECRET = "super-secret-test-key"


class TestJWTEncodeDecode(unittest.TestCase):
    def test_roundtrip(self) -> None:
        token = jwt_encode({"sub": "u-1", "role": "admin"}, SECRET)
        claims = jwt_decode(token, SECRET)
        self.assertEqual(claims["sub"], "u-1")
        self.assertEqual(claims["role"], "admin")

    def test_bad_signature(self) -> None:
        token = jwt_encode({"sub": "u-1"}, SECRET)
        tampered = token[:-2] + ("AB" if token[-2:] != "AB" else "CD")
        with self.assertRaises(JWTError):
            jwt_decode(tampered, SECRET)

    def test_expired_token(self) -> None:
        token = jwt_encode({"sub": "u-1", "exp": int(time.time()) - 100}, SECRET)
        with self.assertRaisesRegex(JWTError, "expired"):
            jwt_decode(token, SECRET)

    def test_leeway_allows_grace(self) -> None:
        token = jwt_encode({"sub": "u-1", "exp": int(time.time()) - 5}, SECRET)
        # Without leeway → expires; with leeway of 30 sec → accepted.
        with self.assertRaises(JWTError):
            jwt_decode(token, SECRET)
        claims = jwt_decode(token, SECRET, leeway=30)
        self.assertEqual(claims["sub"], "u-1")

    def test_audience_required(self) -> None:
        token = jwt_encode({"sub": "u", "aud": "other"}, SECRET)
        with self.assertRaisesRegex(JWTError, "audience"):
            jwt_decode(token, SECRET, audience="api.example.com")

    def test_issuer_required(self) -> None:
        token = jwt_encode({"sub": "u", "iss": "attacker"}, SECRET)
        with self.assertRaisesRegex(JWTError, "issuer"):
            jwt_decode(token, SECRET, issuer="trusted.example.com")

    def test_malformed_token(self) -> None:
        with self.assertRaises(JWTError):
            jwt_decode("not.a.real.jwt", SECRET)


class TestJWTAuthMiddleware(unittest.TestCase):
    def test_missing_token_is_401(self) -> None:
        app = Router()
        app.use(jwt_auth(secret=SECRET))

        @app.get("/protected")
        def p(req: Request) -> dict:
            return {"ok": True}

        s, h, b = _wsgi_call(app, "GET", "/protected")
        self.assertEqual(s, 401)
        self.assertIn("www-authenticate", h)
        self.assertIn("Bearer", h["www-authenticate"])
        self.assertEqual(json.loads(b), {"error": "missing bearer token"})

    def test_valid_token_attaches_claims(self) -> None:
        app = Router()
        app.use(jwt_auth(secret=SECRET))

        @app.get("/me")
        def me(req: Request) -> dict:
            return {"sub": req.state["jwt"]["sub"]}

        token = jwt_encode({"sub": "user-42"}, SECRET)
        s, _h, b = _wsgi_call(app, "GET", "/me",
                              headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(s, 200)
        self.assertEqual(json.loads(b), {"sub": "user-42"})

    def test_invalid_signature_is_401(self) -> None:
        app = Router()
        app.use(jwt_auth(secret=SECRET))

        @app.get("/p")
        def p(req: Request) -> dict:
            return {"ok": True}

        token = jwt_encode({"sub": "x"}, "different-secret")
        s, _h, b = _wsgi_call(app, "GET", "/p",
                              headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(s, 401)
        body = json.loads(b)
        self.assertIn("invalid token", body["error"])


class TestRequestIDMiddleware(unittest.TestCase):
    def test_generates_id_when_absent(self) -> None:
        app = Router()
        app.use(request_id())

        @app.get("/x")
        def x(req: Request) -> dict:
            return {"rid": req.state["request_id"]}

        s, h, b = _wsgi_call(app, "GET", "/x")
        self.assertEqual(s, 200)
        rid_returned = json.loads(b)["rid"]
        self.assertEqual(h.get("x-request-id"), rid_returned)
        self.assertGreater(len(rid_returned), 4)

    def test_correlates_existing_id(self) -> None:
        app = Router()
        app.use(request_id())

        @app.get("/x")
        def x(req: Request) -> dict:
            return {"rid": req.state["request_id"]}

        s, h, b = _wsgi_call(app, "GET", "/x",
                             headers={"X-Request-ID": "abc-123"})
        self.assertEqual(s, 200)
        self.assertEqual(json.loads(b)["rid"], "abc-123")
        self.assertEqual(h["x-request-id"], "abc-123")


class TestOpenAPISpec(unittest.TestCase):
    def test_basic_routes(self) -> None:
        app = Router()

        @app.get("/users", tags=["users"])
        def list_users(req: Request) -> dict:
            return {"users": []}

        @app.get("/users/{id}", tags=["users"])
        def get_user(req: Request, id: str) -> dict:
            return {"id": id}

        @app.post("/users", tags=["users"])
        def create(req: Request) -> dict:
            return {"created": True}

        spec = openapi_spec(app, title="Users API", version="2.0.0",
                            description="v2 endpoints")

        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertEqual(spec["info"]["title"], "Users API")
        self.assertEqual(spec["info"]["version"], "2.0.0")
        self.assertEqual(spec["info"]["description"], "v2 endpoints")
        self.assertIn("/users", spec["paths"])
        self.assertIn("/users/{id}", spec["paths"])

        users_item = spec["paths"]["/users"]
        self.assertIn("get", users_item)
        self.assertIn("post", users_item)
        self.assertEqual(users_item["get"]["tags"], ["users"])

        detail_item = spec["paths"]["/users/{id}"]
        params = detail_item["get"]["parameters"]
        self.assertEqual(len(params), 1)
        self.assertEqual(params[0]["name"], "id")
        self.assertEqual(params[0]["in"], "path")
        self.assertTrue(params[0]["required"])

    def test_empty_router(self) -> None:
        app = Router()
        spec = openapi_spec(app)
        self.assertEqual(spec["paths"], {})
        self.assertEqual(spec["openapi"], "3.1.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
