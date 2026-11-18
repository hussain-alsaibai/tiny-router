"""Wire-level tests: a byte counted over a real socket, not through a shim.

The 204/HEAD defect survived four review cycles because every existing test
called `router._dispatch()` or a hand-rolled WSGI shim, neither of which ever
reads the bytes that leave the process. `test_tiny_router.py:302` asserted on
a Response object's `.status`; the status was always right. What was wrong was
the framing written after it.

These tests open a TCP connection to a real ThreadingHTTPServer and count the
body bytes themselves.
"""

import socket
import threading

import pytest
from http.server import ThreadingHTTPServer

import tiny_router as tr


@pytest.fixture()
def server():
    app = tr.Router()

    @app.get("/nothing")
    def nothing(req):
        return tr.Response(status=204)

    @app.get("/full")
    def full(req):
        return tr.Response(body=b"abcdef", status=200)

    @app.head("/headroute")
    def headroute(req):
        return tr.Response(body=b"abcdef", status=200)

    @app.get("/not-modified")
    def not_modified(req):
        return tr.Response(status=304)

    @app.get("/deleted")
    def deleted(req):
        return tr.Response(status=204, body=b"this must never be sent")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), tr._make_handler(app))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _raw(port, method, path):
    """Return (status_line, headers dict, body bytes) for one request."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        sock.sendall(
            f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n"
            f"Connection: close\r\n\r\n".encode()
        )
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        sock.close()
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status_line = lines[0]
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status_line, headers, body


def test_204_sends_no_body(server):
    status, headers, body = _raw(server, "GET", "/nothing")
    assert "204" in status
    assert body == b""


def test_204_does_not_advertise_a_representation(server):
    """A 204 must not claim a content-type it is forbidden to send."""
    status, headers, _ = _raw(server, "GET", "/nothing")
    assert "204" in status
    assert "content-type" not in headers


def test_204_with_a_body_still_sends_zero_bytes(server):
    """A handler that sets a body on a 204 must not desync the connection."""
    status, _, body = _raw(server, "GET", "/deleted")
    assert "204" in status
    assert body == b""


def test_head_declares_the_get_length_but_sends_no_body(server):
    status, headers, body = _raw(server, "HEAD", "/headroute")
    assert "200" in status
    assert headers.get("content-length") == "6"
    assert body == b""


def test_head_is_not_served_by_a_get_route(server):
    """HEAD is registered explicitly via @app.head, never inferred from GET.

    Asserting the 404 here is deliberate: it records that routing a HEAD to a
    GET handler is a missing feature, not an accident of this fix. If a future
    version adds GET->HEAD fallback on purpose, this test fails and forces the
    change to be documented rather than discovered by a client.
    """
    status, _, _ = _raw(server, "HEAD", "/full")
    assert "404" in status


def test_304_sends_no_body(server):
    status, _, body = _raw(server, "GET", "/not-modified")
    assert "304" in status
    assert body == b""


def test_get_still_sends_its_body(server):
    """Guard against the fix over-applying and stripping every response."""
    status, headers, body = _raw(server, "GET", "/full")
    assert "200" in status
    assert headers.get("content-length") == "6"
    assert body == b"abcdef"
