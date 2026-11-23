"""Tests for tiny-router v0.7.4 header-redaction middleware sidecar."""

"""Tests for tiny-router v0.7.4 header-redaction middleware sidecar."""

import sys
import unittest
from pathlib import Path

import pytest
from types import SimpleNamespace

# The redaction sidecar lazily imports tiny_secret_mask. It is an optional
# dependency (the middleware raises RedactionUnavailable without it), so this
# module must import even when the dependency is missing -- otherwise pytest
# reports a collection ERROR for the WHOLE file and the tests that do not need
# the dependency never run. The sibling lookup is a convenience for a local
# checkout; CI relies on the installed package or on the skip below.
for _candidate in (
    Path(__file__).resolve().parent.parent / "tiny-secret-mask",
):
    if _candidate.is_dir():
        sys.path.insert(0, str(_candidate))

try:
    import tiny_secret_mask  # noqa: F401
    _HAS_SECRET_MASK = True
except ImportError:  # pragma: no cover - depends on the environment
    _HAS_SECRET_MASK = False

requires_secret_mask = pytest.mark.skipif(
    not _HAS_SECRET_MASK,
    reason="tiny-secret-mask is not installed; header redaction cannot be exercised",
)

import tiny_router  # noqa: E402
import tiny_router_redact_headers as rh  # noqa: E402
import tiny_router_redact_headers as _rh  # noqa: E402


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


@requires_secret_mask
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


@requires_secret_mask
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


@requires_secret_mask
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
    # Availability is resolved at call time (the module flag is frozen at
    # import, so patching it alone no longer describes "the dependency is
    # missing" — the resolver has to answer, too).
    monkeypatch.setattr(_rh, "_resolve_mask_headers", lambda: (None, False))
    monkeypatch.setattr(_rh, "MASK_AVAILABLE", False, raising=True)
    with pytest.raises(_rh.RedactionUnavailable) as exc:
        _rh.redact_headers_middleware()
    msg = str(exc.value).lower()
    assert "tiny" in msg and "install" in msg
    # The opt-out must be named in the error, or the caller has no path out.
    assert "passthrough" in msg


def test_passthrough_is_explicit_and_opt_in(monkeypatch):
    monkeypatch.setattr(_rh, "_resolve_mask_headers", lambda: (None, False))
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


class TestMissingDependencyContract(unittest.TestCase):
    """A redaction layer that cannot redact must not report success.

    This is the one behaviour that has to be exercised WITHOUT the dependency,
    so it builds the middleware against an environment where the import fails.
    """

    def test_raises_redaction_unavailable_without_dependency(self):
        import builtins

        real_import = builtins.__import__

        def failing_import(name, *args, **kwargs):
            # tiny_secret_mask is imported lazily INSIDE the middleware
            # factory, so this simulates "not installed" even on a machine
            # where it is installed -- the check must be about the current
            # interpreter, not the one that imported the module.
            if name == "tiny_secret_mask":
                raise ImportError("simulated: not installed")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = failing_import
        try:
            self.assertFalse(rh.redaction_available())
            with self.assertRaises(rh.RedactionUnavailable) as ctx:
                rh.redact_headers_middleware()
        finally:
            builtins.__import__ = real_import
        self.assertIn("tiny-secret-mask", str(ctx.exception))

    def test_passthrough_opt_out_is_explicit_and_harmless(self):
        mw = rh.redact_headers_middleware_passthrough()
        req = make_req({"Authorization": "Bearer secret"})
        self.assertEqual(mw(req, call_pass_through), b"ok")
        # The passthrough does NOT claim to have redacted anything.
        self.assertEqual(req.headers["Authorization"], "Bearer secret")
