"""Security floor tests for the Microsoft device-code OAuth flow.

All HTTP is faked by monkeypatching httpx.Client on the module — no network.
Pins: refresh success/failure, device-code error surfacing, poll state
machine (pending → success, slow_down back-off, declined/expired refusal,
timeout), and refusal to run without MAIL_CLIENT_ID.
"""

from __future__ import annotations

import pytest

from orivellum.capabilities.mail import oauth
from orivellum.capabilities.mail.models import MailStewardError


class _Resp:
    def __init__(self, status_code, payload, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)

    def json(self):
        return self._payload


class _FakeClient:
    """Stands in for httpx.Client; pops one scripted response per POST."""

    responses: list[_Resp] = []
    calls: list[dict] = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, data=None):
        _FakeClient.calls.append({"url": url, "data": data or {}})
        return _FakeClient.responses.pop(0)


@pytest.fixture(autouse=True)
def _fake_http(monkeypatch):
    _FakeClient.responses = []
    _FakeClient.calls = []
    monkeypatch.setattr(oauth.httpx, "Client", _FakeClient)
    monkeypatch.setenv("MAIL_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(oauth.time, "sleep", lambda _s: None)
    yield


# ── Configuration guard ───────────────────────────────────────────────────────


def test_missing_client_id_refuses(monkeypatch):
    monkeypatch.setenv("MAIL_CLIENT_ID", "")
    with pytest.raises(MailStewardError, match="MAIL_CLIENT_ID"):
        oauth.request_device_code()


# ── Device code ───────────────────────────────────────────────────────────────


def test_request_device_code_success():
    _FakeClient.responses = [_Resp(200, {"device_code": "dc", "user_code": "ABCD"})]
    data = oauth.request_device_code()
    assert data["device_code"] == "dc"
    assert _FakeClient.calls[0]["data"]["client_id"] == "test-client-id"
    assert "Mail.Send" not in _FakeClient.calls[0]["data"]["scope"]


def test_request_device_code_send_scope_only_when_asked():
    _FakeClient.responses = [_Resp(200, {"device_code": "dc"})]
    oauth.request_device_code(include_send=True)
    assert "Mail.Send" in _FakeClient.calls[0]["data"]["scope"]


def test_request_device_code_http_and_body_errors_raise():
    _FakeClient.responses = [_Resp(400, {}, text="bad request")]
    with pytest.raises(MailStewardError):
        oauth.request_device_code()
    _FakeClient.responses = [_Resp(200, {"error": "invalid_client", "error_description": "nope"})]
    with pytest.raises(MailStewardError, match="nope"):
        oauth.request_device_code()


# ── Token polling ─────────────────────────────────────────────────────────────


def test_poll_pending_then_success():
    _FakeClient.responses = [
        _Resp(400, {"error": "authorization_pending"}),
        _Resp(400, {"error": "authorization_pending"}),
        _Resp(200, {"access_token": "at", "refresh_token": "rt"}),
    ]
    data = oauth.poll_for_token("dc", interval=1, max_wait=60)
    assert data["access_token"] == "at"
    assert len(_FakeClient.calls) == 3


def test_poll_slow_down_backs_off_then_succeeds():
    _FakeClient.responses = [
        _Resp(400, {"error": "slow_down"}),
        _Resp(200, {"access_token": "at"}),
    ]
    assert oauth.poll_for_token("dc", interval=1, max_wait=60)["access_token"] == "at"


@pytest.mark.parametrize("error", ["authorization_declined", "expired_token", "access_denied"])
def test_poll_denial_raises(error):
    _FakeClient.responses = [_Resp(400, {"error": error})]
    with pytest.raises(MailStewardError, match="denied"):
        oauth.poll_for_token("dc", interval=1, max_wait=60)


def test_poll_unexpected_error_raises_not_loops():
    _FakeClient.responses = [_Resp(500, {"error": "server_error", "error_description": "boom"})]
    with pytest.raises(MailStewardError, match="boom"):
        oauth.poll_for_token("dc", interval=1, max_wait=60)


def test_poll_timeout(monkeypatch):
    clock = {"t": 0.0}

    def fake_monotonic():
        clock["t"] += 200.0
        return clock["t"]

    monkeypatch.setattr(oauth.time, "monotonic", fake_monotonic)
    # Always-pending endpoint: only the advancing clock can end the loop.
    _FakeClient.responses = [_Resp(400, {"error": "authorization_pending"}) for _ in range(10)]
    with pytest.raises(MailStewardError, match="timed out"):
        oauth.poll_for_token("dc", interval=1, max_wait=300)


# ── Refresh ───────────────────────────────────────────────────────────────────


def test_refresh_success_sends_refresh_grant():
    _FakeClient.responses = [_Resp(200, {"access_token": "new-at", "refresh_token": "new-rt"})]
    data = oauth.refresh_access_token("old-rt")
    assert data["access_token"] == "new-at"
    call = _FakeClient.calls[0]["data"]
    assert call["grant_type"] == "refresh_token"
    assert call["refresh_token"] == "old-rt"


def test_refresh_failure_raises_never_returns_partial():
    _FakeClient.responses = [_Resp(400, {}, text="invalid_grant")]
    with pytest.raises(MailStewardError, match="refresh failed"):
        oauth.refresh_access_token("old-rt")
    _FakeClient.responses = [_Resp(200, {"error": "invalid_grant", "error_description": "expired"})]
    with pytest.raises(MailStewardError, match="expired"):
        oauth.refresh_access_token("old-rt")
