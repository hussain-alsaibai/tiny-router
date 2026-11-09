"""Tests for tiny-router v0.7.4 header-redaction middleware sidecar."""

"""Tests for tiny-router v0.7.4 header-redaction middleware sidecar."""

import sys
import unittest
from pathlib import Path

import pytest
from types import SimpleNamespace

# The redaction sidecar lazily imports tiny_secret_mask. It is an optional
# dependency (the middleware is a passthrough without it), so the test
# locates a sibling checkout when there is one and otherwise relies on the
# installed package. No absolute paths: this test must pass in CI.
for _candidate in (
    Path(__file__).resolve().parent.parent / "tiny-secret-mask",
):
    if _candidate.is_dir():
        sys.path.insert(0, str(_candidate))

# Make sure tiny_secret_mask is importable
import tiny_secret_mask
import tiny_router
import tiny_router_redact_headers as rh
import tiny_router_redact_headers as _rh


def make_req(headers=None):
    return SimpleNamespace(
        method="GET",
        path="/",
        headers=headers if headers is not None else {},
        body=b"",
        state={},
    )


def call_pass_through(req):
    return b"ok"


class TestHeaderStats(unittest.TestCase):
    def test_default_init(self):
        stats = rh.HeaderStats()
        self.assertEqual(stats.headers_seen, 0)
        self.assertEqual(stats.redactions, 0)
        self.assertIsNone(stats.last_redacted_header)

    def test_as_dict(self):
        stats = rh.HeaderStats(headers_seen=5, redactions=2, last_redacted_header="Authorization")
        d = stats.as_dict()
        self.assertEqual(d["headers_seen"], 5)
        self.assertEqual(d["redactions"], 2)
        self.assertEqual(d["last_redacted_header"], "Authorization")


class TestRedactHeadersMiddleware(unittest.TestCase):
    def test_authorization_redacted(self):
        mw = rh.redact_headers_middleware()
        req = make_req({"Authorization": "Bearer xyz"})
        mw(req, call_pass_through)
        self.assertEqual(req.headers["Authorization"], "[REDACTED:authorization]")

    def test_cookie_redacted(self):
        mw = rh.redact_headers_middleware()
        req = make_req({"Cookie": "session=abc"})
        mw(req, call_pass_through)
        self.assertEqual(req.headers["Cookie"], "[REDACTED:cookie]")

    def test_x_api_key_redacted(self):
        mw = rh.redact_headers_middleware()
        req = make_req({"X-API-Key": "secret"})
        mw(req, call_pass_through)
        self.assertEqual(req.headers["X-API-Key"], "[REDACTED:x_api_key]")

    def test_non_sensitive_preserved(self):
        mw = rh.redact_headers_middleware()
        req = make_req({"X-Request-Id": "550e8400-e29b-41d4-a716-446655440000"})
        mw(req, call_pass_through)
        self.assertEqual(req.headers["X-Request-Id"], "550e8400-e29b-41d4-a716-446655440000")

    def test_content_type_preserved(self):
        mw = rh.redact_headers_middleware()
        req = make_req({"Content-Type": "application/json"})
        mw(req, call_pass_through)
        self.assertEqual(req.headers["Content-Type"], "application/json")

    def test_multiple_headers_mixed(self):
        mw = rh.redact_headers_middleware()
        req = make_req({
            "Authorization": "Bearer xyz",
            "X-Request-Id": "abc",
            "X-API-Key": "secret",
        })
        mw(req, call_pass_through)
        self.assertEqual(req.headers["Authorization"], "[REDACTED:authorization]")
        self.assertEqual(req.headers["X-Request-Id"], "abc")
        self.assertEqual(req.headers["X-API-Key"], "[REDACTED:x_api_key]")

    def test_empty_headers(self):
        mw = rh.redact_headers_middleware()
        req = make_req({})
        mw(req, call_pass_through)
        self.assertEqual(req.headers, {})

    def test_case_insensitive(self):
        mw = rh.redact_headers_middleware()
        req = make_req({"AUTHORIZATION": "Bearer xyz"})
        mw(req, call_pass_through)
        self.assertEqual(req.headers["AUTHORIZATION"], "[REDACTED:authorization]")

    def test_idempotent(self):
        mw = rh.redact_headers_middleware()
        req = make_req({"Authorization": "Bearer xyz"})
        mw(req, call_pass_through)
        once = dict(req.headers)
        mw(req, call_pass_through)
        self.assertEqual(req.headers, once)

    def test_call_next_still_called(self):
        called = []
        def next_fn(req):
            called.append(req)
            return b"body"
        mw = rh.redact_headers_middleware()
        req = make_req({"Authorization": "Bearer xyz"})
        result = mw(req, next_fn)
        self.assertEqual(called, [req])
        self.assertEqual(result, b"body")


class TestRedactorWithStats(unittest.TestCase):
    def test_returns_tuple(self):
        mw, stats = rh.redactor_with_stats()
        self.assertTrue(callable(mw))
        self.assertIsInstance(stats, rh.HeaderStats)

    def test_stats_increment_on_call(self):
        mw, stats = rh.redactor_with_stats()
        req = make_req({"Authorization": "Bearer xyz", "X-API-Key": "secret"})
        mw(req, call_pass_through)
        self.assertEqual(stats.headers_seen, 1)
        self.assertEqual(stats.redactions, 2)
        self.assertIsNotNone(stats.last_redacted_header)

    def test_stats_no_redactions(self):
        mw, stats = rh.redactor_with_stats()
        req = make_req({"X-Request-Id": "abc"})
        mw(req, call_pass_through)
        self.assertEqual(stats.headers_seen, 1)
        self.assertEqual(stats.redactions, 0)
        self.assertIsNone(stats.last_redacted_header)


class TestRouterIntegration(unittest.TestCase):
    def test_router_with_middleware(self):
        app = tiny_router.Router()
        app.use(rh.redact_headers_middleware())
        self.assertEqual(len(app._middlewares), 1)

    def test_router_with_stats(self):
        app = tiny_router.Router()
        mw, stats = rh.redactor_with_stats()
        app.use(mw)
        self.assertEqual(len(app._middlewares), 1)
        self.assertIsInstance(stats, rh.HeaderStats)


if __name__ == "__main__":
    unittest.main()


# ---- the middleware must not silently no-op -------------------------------
#
# AGENTS.md, 2026-10-26: a redaction function that cannot do its job must
# raise, with an explicitly named opt-out for callers who want a no-op.
# The original implementation installed a passthrough middleware when
# tiny_secret_mask was missing, so a deployment could believe it was
# scrubbing Authorization headers while copying them straight through.


def test_missing_dependency_raises_instead_of_passing_through(monkeypatch):
    monkeypatch.setattr(_rh, "MASK_AVAILABLE", False, raising=True)
    with pytest.raises(_rh.RedactionUnavailable) as exc:
        _rh.redact_headers_middleware()
    msg = str(exc.value).lower()
    assert "tiny" in msg and "install" in msg
    # The opt-out must be named in the error, or the caller has no path out.
    assert "passthrough" in msg


def test_passthrough_is_explicit_and_opt_in(monkeypatch):
    monkeypatch.setattr(_rh, "MASK_AVAILABLE", False, raising=True)
    stats = _rh.HeaderStats()
    mw = _rh.redact_headers_middleware_passthrough(stats=stats)
    req = SimpleNamespace(headers={"authorization": "Bearer " + "t" * 24})
    mw(req, lambda r: r)
    # Unredacted on purpose - the caller wrote "passthrough" at the call site.
    assert req.headers["authorization"].startswith("Bearer ")
    assert stats.headers_seen == 1
    assert stats.redactions == 0


def test_passthrough_does_not_count_phantom_redactions():
    stats = _rh.HeaderStats()
    mw = _rh.redact_headers_middleware_passthrough(stats=stats)
    req = SimpleNamespace(headers={"x-trace": "abc"})
    mw(req, lambda r: r)
    assert stats.redactions == 0
    assert stats.last_redacted_header is None


def test_middleware_constructs_when_dependency_is_present():
    """The failure must be about the missing dependency, not about the
    middleware being unbuildable in the normal case."""
    if not _rh.MASK_AVAILABLE:
        pytest.skip("tiny_secret_mask not installed here")
    assert callable(_rh.redact_headers_middleware())
