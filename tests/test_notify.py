"""Notification delivery: webhook, email, desktop, failures captured."""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
sys.path.insert(0, "pyrowatch")

import pyrowatch.notify as notify


ALERT = {
    "location_id": 1, "group_id": 7, "type": "new_group",
    "fired_utc": "2026-10-21T18:00:00Z", "score": 61.7, "score_prev": None,
    "distance_miles": 12.1, "confidence": "h",
    "sources": ["VIIRS_SNPP"], "reason": "Something hot nearby",
    "detail": {}, "location_name": "Home",
}


def test_one_line_components():
    line = notify._one_line(ALERT)
    assert "New fire detected" in line
    assert "Home" in line
    assert "12.1 mi away" in line
    assert "61.7" in line


def test_deliver_does_nothing_without_config(fresh_db, monkeypatch):
    monkeypatch.setattr(notify, "ALERT_WEBHOOK_URL", "")
    monkeypatch.setattr(notify, "ALERT_EMAIL_TO", [])
    monkeypatch.setattr(notify, "SMTP_HOST", "")
    monkeypatch.setattr(notify, "ALERT_DESKTOP", False)
    out = notify.deliver([ALERT])
    assert out["delivered"] == 0 and out["sent_to"] == []
    # empty list is a no-op that still records an entry in meta
    empty = notify.deliver([])
    assert empty["at"]


def test_deliver_no_alerts_no_channels(fresh_db, monkeypatch):
    monkeypatch.setattr(notify, "ALERT_WEBHOOK_URL", "http://hook")
    out = notify.deliver([])  # nothing to send -> zero network calls
    assert out["delivered"] == 0
    assert out["results"] == []


def test_webhook_json_payload(fresh_db, monkeypatch):
    monkeypatch.setattr(notify, "ALERT_WEBHOOK_URL", "http://hook")
    monkeypatch.setattr(notify, "ALERT_WEBHOOK_FORMAT", "json")
    monkeypatch.setattr(notify, "ALERT_DESKTOP", False)  # count only the webhook
    sent = {}
    monkeypatch.setattr(notify.requests, "post",
                        lambda url, json=None, timeout=None: sent.update(
                            url=url, payload=json) or SimpleNamespace(status_code=200, text=""))
    out = notify.deliver([ALERT])
    assert out["delivered"] == 1
    payload = sent["payload"]
    assert payload["source"] == "pyrowatch"
    assert payload["events"][0]["text"].startswith("New fire detected")


def test_webhook_slack_payload(fresh_db, monkeypatch):
    monkeypatch.setattr(notify, "ALERT_WEBHOOK_URL", "http://hook")
    monkeypatch.setattr(notify, "ALERT_WEBHOOK_FORMAT", "slack")
    monkeypatch.setattr(notify, "ALERT_DESKTOP", False)
    sent = {}
    monkeypatch.setattr(notify.requests, "post",
                        lambda url, json=None, timeout=None: sent.update(payload=json)
                        or SimpleNamespace(status_code=200, text=""))
    out = notify.deliver([ALERT])
    assert out["delivered"] == 1
    blocks = sent["payload"]["blocks"]
    assert blocks[0]["text"]["type"] == "mrkdwn"
    assert any("Home" in b["text"]["text"] for b in blocks[1:])


def test_webhook_http_error_is_captured(fresh_db, monkeypatch):
    monkeypatch.setattr(notify, "ALERT_WEBHOOK_URL", "http://hook")
    monkeypatch.setattr(notify, "ALERT_DESKTOP", False)
    monkeypatch.setattr(notify.requests, "post",
                        lambda *a, **k: SimpleNamespace(status_code=500, text="boom"))
    out = notify.deliver([ALERT])
    assert out["delivered"] == 0
    assert any("boom" in (e or "") for e in out["errors"])


class FakeSMTP:
    calls = []

    def __init__(self, host, port, timeout=None):
        FakeSMTP.calls.append({"host": host, "port": port})
        self.logged_in = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.logged_in = False

    def login(self, u, p):
        self.logged_in = True

    def send_message(self, msg):
        FakeSMTP.messages.append(msg)

    messages = []


def test_email_send(fresh_db, monkeypatch):
    import smtplib
    FakeSMTP.messages = []
    FakeSMTP.calls = []
    monkeypatch.setattr(notify, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(notify, "SMTP_PORT", 587)
    monkeypatch.setattr(notify, "SMTP_TLS", True)
    monkeypatch.setattr(notify, "SMTP_SSL", False)
    monkeypatch.setattr(notify, "SMTP_USER", "u@x")
    monkeypatch.setattr(notify, "SMTP_PASS", "pw")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    res = notify.send_email([ALERT], ["to@x"])
    assert res["ok"]
    msg = FakeSMTP.messages[0]
    assert "[PyroWatch]" in msg["Subject"]
    assert "to@x" in msg["To"]
    assert "Home" in msg.get_content() and "Something hot nearby" in msg.get_content()


def test_email_failure_returns_not_raises(fresh_db, monkeypatch):
    import smtplib
    import pyrowatch.notify as n

    class BoomSMTP:
        def __init__(self, *a, **k):
            raise OSError("unreachable")

    monkeypatch.setattr(n, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(n, "SMTP_TLS", True)
    monkeypatch.setattr(n, "SMTP_SSL", False)
    monkeypatch.setattr(smtplib, "SMTP", BoomSMTP)
    res = notify.send_email([ALERT], ["to@x"])
    assert res["ok"] is False and "unreachable" in res["error"]


def test_desktop_toast_dispatch(fresh_db, monkeypatch):
    monkeypatch.setattr(notify, "ALERT_DESKTOP", True)
    monkeypatch.setattr(notify, "ALERT_WEBHOOK_URL", "")
    monkeypatch.setattr(notify, "ALERT_EMAIL_TO", [])
    seen = {}
    monkeypatch.setattr(notify, "send_desktop",
                        lambda rows: seen.update(count=len(rows)) or
                        {"channel": "desktop", "ok": True, "error": None})
    out = notify.deliver([ALERT])
    assert seen["count"] == 1
    assert out["delivered"] == 1


def test_desktop_non_windows_reports_error(fresh_db, monkeypatch):
    import sys as _sys
    monkeypatch.setattr(notify, "ALERT_DESKTOP", True)
    monkeypatch.setattr(_sys, "platform", "linux")
    res = notify.send_desktop([ALERT])
    assert res["ok"] is False and "Windows" in res["error"]


def test_xml_escape_quotes():
    escaped = notify._xml_escape('<tag attr="val">&\'</tag>')
    assert '&quot;' in escaped
    assert '&amp;' in escaped
    assert '&apos;' in escaped
    assert '&lt;' in escaped
    assert '&gt;' in escaped


def test_email_ssl_with_tls_does_not_call_starttls(fresh_db, monkeypatch):
    import smtplib
    starttls_called = []

    class SSLSMTP:
        def __init__(self, host, port, timeout=None):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def starttls(self):
            starttls_called.append(True)
        def login(self, u, p):
            pass
        def send_message(self, msg):
            pass

    monkeypatch.setattr(notify, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(notify, "SMTP_PORT", 465)
    monkeypatch.setattr(notify, "SMTP_TLS", True)
    monkeypatch.setattr(notify, "SMTP_SSL", True)
    monkeypatch.setattr(smtplib, "SMTP_SSL", SSLSMTP)
    res = notify.send_email([ALERT], ["to@x"])
    assert res["ok"] is True
    assert len(starttls_called) == 0


def test_desktop_default_is_platform_aware(monkeypatch):
    import importlib
    import sys as _sys
    import pyrowatch.config as cfg

    monkeypatch.delenv("ALERT_DESKTOP", raising=False)
    monkeypatch.setattr(_sys, "platform", "darwin")
    importlib.reload(cfg)
    assert cfg.ALERT_DESKTOP is False

    monkeypatch.setattr(_sys, "platform", "win32")
    importlib.reload(cfg)
    assert cfg.ALERT_DESKTOP is True

