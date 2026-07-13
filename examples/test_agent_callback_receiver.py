"""Tests for the hardened callback receiver example.

Runs end-to-end against the in-process dispatch path so we don't have to
spin up a real HTTP server. Exercises auth, payload validation, dedupe,
rate limiting, and the operational endpoints.

Run:
    python3 examples/test_agent_callback_receiver.py
"""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import agent_callback_receiver as acr  # noqa: E402


class CallbackReceiverTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reset module-level state so each test is independent.
        acr.COUNTS.clear()
        acr.DEDUPE._seen.clear()
        acr.RATE._hits.clear()

    def _hdrs(self, **extra: str) -> dict[str, str]:
        base = {"x-callback-token": "local-dev-token", "x-source-ip": "127.0.0.1"}
        for key, value in extra.items():
            # Support "x_source_ip" style, "delivery_id" style, etc.
            header_name = "x-" + key.replace("_", "-")
            base[header_name] = value
        return base

    def test_health_endpoint_is_public(self) -> None:
        status, _, payload = acr.APP.dispatch("GET", "/health", b"", {})
        self.assertEqual(status, 200)
        self.assertIn("ok", json.loads(payload))

    def test_ready_endpoint_is_public(self) -> None:
        status, _, payload = acr.APP.dispatch("GET", "/ready", b"", {})
        self.assertEqual(status, 200)
        body = json.loads(payload)
        self.assertTrue(body["ready"])

    def test_status_requires_token(self) -> None:
        status, _, _ = acr.APP.dispatch("GET", "/status", b"", {})
        self.assertEqual(status, 401)

    def test_status_returns_counts(self) -> None:
        status, _, payload = acr.APP.dispatch("GET", "/status", b"", self._hdrs())
        self.assertEqual(status, 200)
        body = json.loads(payload)
        self.assertIn("counts", body)
        self.assertIn("dedupe", body)

    def test_callback_requires_token(self) -> None:
        status, _, _ = acr.APP.dispatch("POST", "/callbacks/x", b"{}", {})
        self.assertEqual(status, 401)

    def test_callback_happy_path(self) -> None:
        body = json.dumps({"event": "job.done", "job_id": "j-1", "status": "ok"}).encode()
        status, _, payload = acr.APP.dispatch(
            "POST", "/callbacks/stripe", body, self._hdrs(delivery_id="d-1")
        )
        self.assertEqual(status, 202)
        body_json = json.loads(payload)
        self.assertTrue(body_json["accepted"])
        self.assertEqual(body_json["provider"], "stripe")
        self.assertIn("rate_remaining", body_json)

    def test_callback_dedupes_by_delivery_id(self) -> None:
        body = json.dumps({"event": "job.done", "job_id": "j-1", "status": "ok"}).encode()
        first, _, _ = acr.APP.dispatch(
            "POST", "/callbacks/stripe", body, self._hdrs(delivery_id="d-2")
        )
        second, _, payload = acr.APP.dispatch(
            "POST", "/callbacks/stripe", body, self._hdrs(delivery_id="d-2")
        )
        self.assertEqual(first, 202)
        self.assertEqual(second, 200)
        self.assertTrue(json.loads(payload)["duplicate"])

    def test_callback_dedupes_by_body_hash_when_no_delivery_id(self) -> None:
        body = json.dumps({"event": "job.done", "job_id": "j-2"}).encode()
        first, _, _ = acr.APP.dispatch("POST", "/callbacks/x", body, self._hdrs())
        second, _, payload = acr.APP.dispatch("POST", "/callbacks/x", body, self._hdrs())
        self.assertEqual(first, 202)
        self.assertEqual(second, 200)
        self.assertTrue(json.loads(payload)["duplicate"])

    def test_callback_rejects_invalid_payload(self) -> None:
        body = json.dumps({"wrong": "shape"}).encode()
        status, _, payload = acr.APP.dispatch(
            "POST", "/callbacks/x", body, self._hdrs(delivery_id="d-3")
        )
        self.assertEqual(status, 422)
        self.assertIn("event", json.loads(payload)["error"])

    def test_callback_rejects_malformed_json(self) -> None:
        status, _, payload = acr.APP.dispatch(
            "POST", "/callbacks/x", b"{not json", self._hdrs(delivery_id="d-4")
        )
        self.assertEqual(status, 400)
        self.assertIn("invalid_json", json.loads(payload)["error"])

    def test_rate_limiter_isolates_sources(self) -> None:
        # Reset rate state for this test specifically.
        acr.RATE._hits.clear()
        original_max = acr.RATE.max
        acr.RATE.max = 2
        try:
            body = json.dumps({"event": "x"}).encode()
            for i in range(2):
                acr.APP.dispatch(
                    "POST", "/callbacks/x", body, self._hdrs(delivery_id=f"a-{i}")
                )
            # Third from same source is rate-limited.
            status, _, _ = acr.APP.dispatch(
                "POST", "/callbacks/x", body, self._hdrs(delivery_id="a-3")
            )
            self.assertEqual(status, 429)
            # Different source can still post.
            status, _, _ = acr.APP.dispatch(
                "POST",
                "/callbacks/x",
                body,
                self._hdrs(delivery_id="b-1", source_ip="10.0.0.1"),
            )
            self.assertEqual(status, 202)
        finally:
            acr.RATE.max = original_max
            acr.RATE._hits.clear()


if __name__ == "__main__":
    unittest.main(verbosity=2)