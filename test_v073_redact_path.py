"""Tests for tiny_router_redact_path sidecar (tiny_router v0.7.3).

Run: python3 -m unittest tests.test_tiny_router_redact_path -v
"""

from __future__ import annotations

import unittest

from tiny_router_redact_path import (
    RedactionStats,
    __version__,
    redact_path_middleware,
    redactor_with_stats,
)


class _Req:
    def __init__(self, path):
        self.path = path


class TestVersion(unittest.TestCase):
    def test_version(self) -> None:
        self.assertEqual(__version__, "0.7.3")


class TestRedactionStats(unittest.TestCase):
    def test_init(self) -> None:
        s = RedactionStats()
        self.assertEqual(s.paths_seen, 0)
        self.assertEqual(s.redactions, 0)
        self.assertIsNone(s.last_redacted_path)

    def test_observe_redaction(self) -> None:
        s = RedactionStats()
        s.observe("/u/550e8400-e29b-41d4-a716-446655440000", "/u/[REDACTED:uuid]")
        self.assertEqual(s.redactions, 1)
        self.assertEqual(s.paths_seen, 1)

    def test_observe_passthrough(self) -> None:
        s = RedactionStats()
        s.observe("/api/health", "/api/health")
        self.assertEqual(s.redactions, 0)
        self.assertEqual(s.paths_seen, 1)

    def test_as_dict(self) -> None:
        s = RedactionStats()
        s.observe("/u/550e8400-e29b-41d4-a716-446655440000", "/u/[REDACTED:uuid]")
        d = s.as_dict()
        self.assertIn("paths_seen", d)
        self.assertIn("redactions", d)
        self.assertIn("last_redacted_path", d)


class TestMiddleware(unittest.TestCase):
    def test_redacts_uuid(self) -> None:
        def nxt(req):
            return "ok:" + req.path

        mw = redact_path_middleware()
        req = _Req("/u/550e8400-e29b-41d4-a716-446655440000")
        result = mw(req, nxt)
        self.assertEqual(result, "ok:/u/[REDACTED:uuid]")
        self.assertEqual(req.path, "/u/[REDACTED:uuid]")

    def test_redacts_aws(self) -> None:
        mw = redact_path_middleware()
        req = _Req("/keys/AKIAIOSFODNN7EXAMPLE/active")
        mw(req, lambda r: None)
        self.assertTrue("[REDACTED:" in req.path and "]" in req.path, msg=f"expected redaction tag in {req.path!r}")

    def test_redacts_github_pat(self) -> None:
        mw = redact_path_middleware()
        pat = "ghp_" + "a" * 40
        req = _Req("/" + pat)
        mw(req, lambda r: None)
        self.assertNotIn(pat, req.path)
        self.assertTrue("[REDACTED:" in req.path and "]" in req.path, msg=f"expected redaction tag in {req.path!r}")

    def test_passthrough_no_match(self) -> None:
        mw = redact_path_middleware()
        req = _Req("/api/health")
        mw(req, lambda r: None)
        self.assertEqual(req.path, "/api/health")

    def test_idempotent(self) -> None:
        mw = redact_path_middleware()
        req = _Req("/u/550e8400-e29b-41d4-a716-446655440000")
        mw(req, lambda r: None)
        first = req.path
        mw(req, lambda r: None)
        self.assertEqual(first, req.path)

    def test_handles_empty_path(self) -> None:
        mw = redact_path_middleware()
        req = _Req("")
        result = mw(req, lambda r: "ok")
        self.assertEqual(result, "ok")

    def test_handles_missing_path_attr(self) -> None:
        class NoPath:
            pass

        mw = redact_path_middleware()
        req = NoPath()
        result = mw(req, lambda r: "ok")
        self.assertEqual(result, "ok")

    def test_stats_counted(self) -> None:
        stats = RedactionStats()
        mw = redact_path_middleware(stats=stats)
        mw(_Req("/u/550e8400-e29b-41d4-a716-446655440000"), lambda r: None)
        mw(_Req("/api/health"), lambda r: None)
        self.assertEqual(stats.redactions, 1)
        self.assertEqual(stats.paths_seen, 2)


class TestRedactorWithStats(unittest.TestCase):
    def test_returns_pair(self) -> None:
        mw, stats = redactor_with_stats()
        self.assertIsNotNone(mw)
        self.assertIsInstance(stats, RedactionStats)
        req = _Req("/u/550e8400-e29b-41d4-a716-446655440000")
        mw(req, lambda r: None)
        self.assertEqual(stats.redactions, 1)


if __name__ == "__main__":
    unittest.main()
