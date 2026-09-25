"""Tests for tiny_router v0.5.0 — openapi_endpoint + jwt_refresh helpers."""

from __future__ import annotations

import json
import time
import unittest
from io import BytesIO

from tiny_router import (
    Router,
    Request,
    JWTError,
    jwt_decode,
    jwt_encode,
    jwt_refresh_pair,
    jwt_refresh_exchange,
    openapi_endpoint,
    openapi_spec,
)


def _wsgi_call(
    app: Router, method: str, path: str, body: bytes = b"", headers: dict | None = None
) -> tuple[int, dict, bytes]:
    """WSGI call helper (mirrors test_tiny_router_v040.py)."""
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


# openapi_endpoint


class TestOpenAPIEndpoint(unittest.TestCase):
    def test_serves_spec_at_default_path(self) -> None:
        app = Router()

        @app.get("/users", tags=["users"])
        def list_users(req: Request) -> dict:
            return {"users": []}

        @app.get("/users/{id}", tags=["users"])
        def get_user(req: Request, id: str) -> dict:
            return {"id": id}

        openapi_endpoint(
            app,
            title="Users API",
            version="2.0.0",
            description="v2 endpoints",
        )

        status, hdrs, body = _wsgi_call(app, "GET", "/openapi.json")
        self.assertEqual(status, 200)
        self.assertIn("application/json", hdrs.get("content-type", ""))
        spec = json.loads(body.decode("utf-8"))
        self.assertEqual(spec["info"]["title"], "Users API")
        self.assertEqual(spec["info"]["version"], "2.0.0")
        self.assertEqual(spec["info"]["description"], "v2 endpoints")
        self.assertIn("/users", spec["paths"])
        self.assertIn("/users/{id}", spec["paths"])

    def test_serves_spec_at_custom_path(self) -> None:
        app = Router()
        openapi_endpoint(app, path="/api/docs.json")
        status, _, body = _wsgi_call(app, "GET", "/api/docs.json")
        self.assertEqual(status, 200)
        spec = json.loads(body.decode("utf-8"))
        self.assertEqual(spec["openapi"], "3.1.0")
        # The default path should NOT also exist
        status2, _, _ = _wsgi_call(app, "GET", "/openapi.json")
        self.assertEqual(status2, 404)

    def test_registers_on_custom_methods(self) -> None:
        app = Router()
        openapi_endpoint(app, methods=("GET", "HEAD"), path="/openapi.json")
        s_get, _, _ = _wsgi_call(app, "GET", "/openapi.json")
        s_head, _, _ = _wsgi_call(app, "HEAD", "/openapi.json")
        s_post, _, _ = _wsgi_call(app, "POST", "/openapi.json")
        self.assertEqual(s_get, 200)
        self.assertEqual(s_head, 200)
        self.assertEqual(s_post, 404)

    def test_response_includes_no_store_header(self) -> None:
        app = Router()
        openapi_endpoint(app)
        _, hdrs, _ = _wsgi_call(app, "GET", "/openapi.json")
        self.assertEqual(hdrs.get("cache-control"), "no-store")

    def test_empty_router_returns_empty_paths(self) -> None:
        app = Router()
        openapi_endpoint(app)
        status, _, body = _wsgi_call(app, "GET", "/openapi.json")
        self.assertEqual(status, 200)
        spec = json.loads(body.decode("utf-8"))
        self.assertEqual(spec["paths"], {})


# jwt_refresh_pair


class TestJWTRefreshPair(unittest.TestCase):
    def test_emits_access_and_refresh_tokens(self) -> None:
        pair = jwt_refresh_pair(
            {"sub": "u-1", "scope": "read"},
            {"sub": "u-1"},
            secret=SECRET,
        )
        self.assertIn("access_token", pair)
        self.assertIn("refresh_token", pair)
        self.assertEqual(pair["token_type"], "Bearer")
        self.assertEqual(pair["expires_in"], 900)
        # Both decode cleanly (passing leeway to avoid wall-clock drift)
        access = jwt_decode(pair["access_token"], SECRET, leeway=10**9)
        refresh = jwt_decode(pair["refresh_token"], SECRET, leeway=10**9)
        self.assertEqual(access["sub"], "u-1")
        self.assertEqual(access["scope"], "read")
        self.assertEqual(refresh["sub"], "u-1")
        self.assertEqual(refresh["typ"], "refresh")

    def test_access_token_has_short_ttl(self) -> None:
        now = 1_700_000_000
        pair = jwt_refresh_pair(
            {"sub": "u-1"},
            {"sub": "u-1"},
            secret=SECRET,
            access_ttl_seconds=120,
            now=now,
        )
        # Pass a generous leeway so the test doesn't depend on wall-clock time.
        access = jwt_decode(pair["access_token"], SECRET, leeway=10**9)
        refresh = jwt_decode(pair["refresh_token"], SECRET, leeway=10**9)
        self.assertEqual(access["exp"] - access["iat"], 120)
        self.assertEqual(refresh["exp"] - refresh["iat"], 60 * 60 * 24 * 30)

    def test_access_payload_overrides_iat_and_exp(self) -> None:
        # User-supplied iat/exp are overwritten so callers can't mint
        # eternal access tokens by stuffing iat=0 / exp=10^10.
        now = 1_700_000_000
        pair = jwt_refresh_pair(
            {"sub": "u-1", "iat": 0, "exp": 10**12},
            {"sub": "u-1"},
            secret=SECRET,
            access_ttl_seconds=900,
            now=now,
        )
        access = jwt_decode(pair["access_token"], SECRET, leeway=10**9)
        self.assertEqual(access["iat"], now)
        self.assertEqual(access["exp"], now + 900)

    def test_refresh_payload_sets_typ_marker(self) -> None:
        # Caller-supplied typ is overwritten.
        pair = jwt_refresh_pair(
            {"sub": "u-1"},
            {"sub": "u-1", "typ": "access"},
            secret=SECRET,
        )
        refresh = jwt_decode(pair["refresh_token"], SECRET, leeway=10**9)
        self.assertEqual(refresh["typ"], "refresh")


# jwt_refresh_exchange


class TestJWTRefreshExchange(unittest.TestCase):
    def test_exchange_valid_refresh_mints_access(self) -> None:
        # Use the current wall clock as the base so the freshly minted
        # access token's ``exp`` is in the future when decoded.
        now = int(time.time())
        pair = jwt_refresh_pair(
            {"sub": "u-1", "scope": "read write"},
            {"sub": "u-1", "device": "mac-01", "scope": "read write"},
            secret=SECRET,
            now=now,
        )
        result = jwt_refresh_exchange(
            pair["refresh_token"],
            secret=SECRET,
            now=now + 1,
        )
        self.assertIn("access_token", result)
        self.assertEqual(result["token_type"], "Bearer")
        self.assertEqual(result["expires_in"], 900)
        new_access = jwt_decode(result["access_token"], SECRET)
        # Claims carried over from refresh token, minus typ, plus fresh iat/exp
        self.assertEqual(new_access["sub"], "u-1")
        self.assertEqual(new_access["scope"], "read write")
        self.assertEqual(new_access["device"], "mac-01")
        self.assertNotIn("typ", new_access)
        self.assertEqual(new_access["iat"], now + 1)
        self.assertEqual(new_access["exp"], now + 1 + 900)

    def test_exchange_rejects_access_token(self) -> None:
        pair = jwt_refresh_pair({"sub": "u-1"}, {"sub": "u-1"}, secret=SECRET)
        with self.assertRaises(JWTError) as ctx:
            jwt_refresh_exchange(pair["access_token"], secret=SECRET)
        self.assertIn("not a refresh token", str(ctx.exception))

    def test_exchange_rejects_bad_signature(self) -> None:
        pair = jwt_refresh_pair({"sub": "u-1"}, {"sub": "u-1"}, secret=SECRET)
        with self.assertRaises(JWTError):
            jwt_refresh_exchange(pair["refresh_token"], secret="wrong-secret")

    def test_exchange_rejects_expired_refresh(self) -> None:
        now = 1_700_000_000
        pair = jwt_refresh_pair(
            {"sub": "u-1"},
            {"sub": "u-1"},
            secret=SECRET,
            refresh_ttl_seconds=60,
            now=now,
        )
        with self.assertRaises(JWTError) as ctx:
            jwt_refresh_exchange(
                pair["refresh_token"],
                secret=SECRET,
                now=now + 3600,  # way past expiry
                leeway=0,
            )
        self.assertIn("expired", str(ctx.exception))

    def test_exchange_enforces_audience(self) -> None:
        now = int(time.time())
        pair = jwt_refresh_pair(
            {"sub": "u-1"},
            {"sub": "u-1", "aud": "service-a"},
            secret=SECRET,
            now=now,
        )
        with self.assertRaises(JWTError):
            jwt_refresh_exchange(
                pair["refresh_token"],
                secret=SECRET,
                audience="service-b",
            )
        # Same audience should succeed
        result = jwt_refresh_exchange(
            pair["refresh_token"],
            secret=SECRET,
            audience="service-a",
        )
        self.assertIn("access_token", result)


# End-to-end: openapi_endpoint + jwt_refresh_pair


class TestV050Composition(unittest.TestCase):
    def test_login_endpoint_emits_pair_served_by_openapi(self) -> None:
        app = Router()

        @app.post("/auth/login")
        def login(req: Request) -> dict:
            # /auth/login would call jwt_refresh_pair(...) in real code.
            return jwt_refresh_pair(
                {"sub": "u-1"},
                {"sub": "u-1"},
                secret=SECRET,
            )

        openapi_endpoint(app, title="Auth Demo")

        # /openapi.json should describe /auth/login
        status, _, body = _wsgi_call(app, "GET", "/openapi.json")
        self.assertEqual(status, 200)
        spec = json.loads(body.decode("utf-8"))
        self.assertIn("/auth/login", spec["paths"])
        self.assertIn("post", spec["paths"]["/auth/login"])

        # /auth/login should actually return a token pair
        status2, _, body2 = _wsgi_call(app, "POST", "/auth/login")
        self.assertEqual(status2, 200)
        pair = json.loads(body2.decode("utf-8"))
        self.assertIn("access_token", pair)
        self.assertIn("refresh_token", pair)


if __name__ == "__main__":
    unittest.main(verbosity=2)
