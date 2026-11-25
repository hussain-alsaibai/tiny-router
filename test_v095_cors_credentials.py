"""Regression tests for 0.9.5: ``allow_credentials`` must not be a wildcard grant.

Before 0.9.5, ``cors(allow_credentials=True)`` -- with the default
``allow_origins=["*"]`` -- reflected whatever ``Origin`` arrived and stamped
``Access-Control-Allow-Credentials: true`` on the reply:

    request   Origin: https://evil.example
    reply     Access-Control-Allow-Origin: https://evil.example
              Access-Control-Allow-Credentials: true

That is a valid credentialed grant, not a refusal. Any page on the internet
could therefore read authenticated responses from the API, provided the user's
browser held a session cookie. The single-word incantation a developer writes
to "allow cookies" was the full vulnerability.

The Fetch standard forbids a ``*`` origin on a credentialed response, and
browsers enforce it by withholding the response -- so even the "correct"
wildcard spelling could never have worked for the call that needed it. Both
facts point at one fix: require an explicit origin list when credentials are on.
"""

import pytest

import tiny_router as tr


class FakeRequest:
    def __init__(self, method, headers):
        self.method = method
        self.headers = headers


def pass_through(_req):
    return tr.Response("ok")


# --- the constructor refuses the dangerous combination -------------------


def test_wildcard_plus_credentials_is_rejected():
    with pytest.raises(ValueError, match="allow_credentials"):
        tr.cors(allow_credentials=True)


def test_default_origin_list_plus_credentials_is_rejected():
    """Omitting allow_origins defaults to ['*'] -- also forbidden."""
    with pytest.raises(ValueError, match="allow_credentials"):
        tr.cors(allow_origins=None, allow_credentials=True)


def test_explicit_wildcard_plus_credentials_is_rejected():
    with pytest.raises(ValueError, match="allow_credentials"):
        tr.cors(allow_origins=["*"], allow_credentials=True)


def test_error_message_names_the_remedy():
    """"Invalid config" is not actionable; the message must show the fix."""
    with pytest.raises(ValueError) as exc:
        tr.cors(allow_credentials=True)
    assert "allow_origins" in str(exc.value)


# --- the safe configurations still work ---------------------------------


def test_explicit_origin_with_credentials_echoes_only_that_origin():
    mw = tr.cors(allow_origins=["https://app.example"], allow_credentials=True)
    resp = mw(FakeRequest("GET", {"origin": "https://app.example"}), pass_through)
    assert resp.headers["Access-Control-Allow-Origin"] == "https://app.example"
    assert resp.headers["Access-Control-Allow-Credentials"] == "true"


def test_disallowed_origin_gets_no_credential_grant():
    """The whole point: an unlisted origin must receive neither header."""
    mw = tr.cors(allow_origins=["https://app.example"], allow_credentials=True)
    resp = mw(FakeRequest("GET", {"origin": "https://evil.example"}), pass_through)
    assert "Access-Control-Allow-Origin" not in resp.headers
    assert "Access-Control-Allow-Credentials" not in resp.headers


def test_disallowed_origin_preflight_carries_no_grant():
    mw = tr.cors(allow_origins=["https://app.example"], allow_credentials=True)
    req = FakeRequest(
        "OPTIONS",
        {"origin": "https://evil.example", "access-control-request-method": "GET"},
    )
    resp = mw(req, pass_through)
    assert "Access-Control-Allow-Origin" not in resp.headers
    assert "Access-Control-Allow-Credentials" not in resp.headers


def test_wildcard_without_credentials_still_allowed():
    """The common public-API config must not regress."""
    mw = tr.cors(allow_origins=["*"])
    resp = mw(FakeRequest("GET", {"origin": "https://any.example"}), pass_through)
    assert resp.headers["Access-Control-Allow-Origin"] == "https://any.example"
    assert "Access-Control-Allow-Credentials" not in resp.headers


def test_wildcard_preflight_still_works():
    mw = tr.cors(allow_origins=["*"])
    req = FakeRequest(
        "OPTIONS",
        {"origin": "https://any.example", "access-control-request-method": "GET"},
    )
    resp = mw(req, pass_through)
    assert resp.headers["Access-Control-Allow-Origin"] == "https://any.example"
    assert "GET" in resp.headers["Access-Control-Allow-Methods"]
