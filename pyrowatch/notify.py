"""Alert delivery — pushes fired alerts out beyond the dashboard/API.

Channels (all optional, configured via .env — see README "Notifications"):
  * webhook  — POST JSON {events: [...]} to ALERT_WEBHOOK_URL (Slack-compatible
               payloads are supported via ALERT_WEBHOOK_FORMAT=slack)
  * email    — plain SMTP, one message per cycle containing every alert
  * desktop  — Windows toast (WinRT via PowerShell), max one per cycle

Delivery failures NEVER break the pipeline cycle: they are caught, counted,
and the last result is stored in meta under "notify" for /api/meta freshness.
"""
from __future__ import annotations

import traceback

import requests

from . import db
from .config import (ALERT_DESKTOP, ALERT_EMAIL_TO, ALERT_WEBHOOK_FORMAT,
                     ALERT_WEBHOOK_URL, SMTP_HOST, SMTP_PASS, SMTP_PORT,
                     SMTP_TLS, SMTP_USER, SMTP_SSL)
from .util import now_iso

TYPE_LABELS = {"new_group": "New fire detected",
               "new_satellite": "New satellite detection",
               "score_increase": "Conditions worsening"}


def _one_line(alert: dict) -> str:
    """Compact single-line human text for one alert dict (as built by alerts.py)."""
    loc = alert.get("location_name") or f"location #{alert.get('location_id')}"
    kind = TYPE_LABELS.get(alert.get("type", ""), alert.get("type", "alert"))
    bits = [f"{kind} — {loc}"]
    if alert.get("distance_miles") is not None:
        bits.append(f"{alert['distance_miles']} mi away")
    if alert.get("score") is not None:
        prev = alert.get("score_prev")
        bits.append("score " + (f"{prev:g}→{alert['score']:g}" if prev is not None else f"{alert['score']:g}"))
    reason = alert.get("reason")
    if reason:
        bits.append(reason)
    wfigs = ((alert.get("detail") or {}).get("wfigs"))
    if wfigs:
        bits.append(f"official incident: {wfigs}")
    return " · ".join(bits)


# ------------------------------------------------------------------ webhook

def _slack_payload(alerts: list[dict]) -> dict:
    return {
        "text": "\n".join(["PyroWatch alerts:"] + ["• " + _one_line(a) for a in alerts]),
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": "*PyroWatch alert*"}},
            {"type": "section", "text": {"type": "mrkdwn",
                                         "text": "\n".join("• " + _one_line(a) for a in alerts)}},
        ]}


def _json_payload(alerts: list[dict]) -> dict:
    return {"source": "pyrowatch", "sent_utc": now_iso(),
            "count": len(alerts),
            "events": [
                {**a, "text": _one_line(a)} for a in alerts
            ]}


def send_webhook(alerts: list[dict], url: str, fmt: str = "json", timeout: float = 20) -> dict:
    payload = _slack_payload(alerts) if fmt == "slack" else _json_payload(alerts)
    r = requests.post(url, json=payload, timeout=timeout)
    return {"channel": "webhook", "status_code": r.status_code,
            "ok": 200 <= r.status_code < 300,
            "error": None if 200 <= r.status_code < 300 else (r.text or "")[:200]}


# -------------------------------------------------------------------- email

def send_email(alerts: list[dict], to_addrs: list[str], timeout: float = 30) -> dict:
    """One SMTP message per cycle with all alerts (stdlib smtplib)."""
    from email.message import EmailMessage
    import smtplib

    who = sorted({(a.get("location_name") or "") for a in alerts})
    subject = (f"[PyroWatch] {len(alerts)} alert(s)" +
               (f" — {', '.join(x for x in who if x)}" if who else ""))
    body = "\n\n".join(
        f"• {_one_line(a)}\n  sources: {', '.join(a.get('sources') or []) or '—'}"
        for a in alerts)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_USER or "pyrowatch@localhost"
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body)

    last_error = None
    for smtp_cls in ((smtplib.SMTP_SSL,) if SMTP_SSL else (smtplib.SMTP,)):
        try:
            with smtp_cls(SMTP_HOST, SMTP_PORT, timeout=timeout) as s:
                if SMTP_TLS and not SMTP_SSL:
                    s.starttls()
                if SMTP_USER and SMTP_PASS:
                    s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
            return {"channel": "email", "ok": True, "error": None, "to": to_addrs}
        except Exception as e:  # noqa: BLE001 — any SMTP problem → report, don't raise
            last_error = str(e)
    return {"channel": "email", "ok": False, "error": last_error, "to": to_addrs}


# ------------------------------------------------------------------ desktop

def send_desktop(alerts: list[dict]) -> dict:
    """One Windows toast per cycle (WinRT via PowerShell 5.1; no extra deps)."""
    head = f"PyroWatch — {len(alerts)} alert(s)"
    body = "\n".join(_one_line(a) for a in alerts[:4])
    try:
        _toast_windows(head, body)
        return {"channel": "desktop", "ok": True, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"channel": "desktop", "ok": False, "error": str(e)[:200]}


_PWSH_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$xmlBytes = [Convert]::FromBase64String('{B64_XML}')
$xmlText = [System.Text.Encoding]::UTF8.GetString($xmlBytes)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($xmlText)
$toast = New-Object Windows.UI.Notifications.ToastNotification($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('PyroWatch').Show($toast)
"""


def _toast_windows(title: str, body: str) -> None:
    import base64
    import subprocess
    import sys

    if sys.platform != "win32":
        raise RuntimeError("Desktop toasts are only supported on Windows")
    xml_text = (
        '<toast activationType="protocol" launch="pyrowatch://alerts">\n'
        '  <visual><binding template="ToastGeneric">\n'
        f'    <text>{_xml_escape(title)}</text>\n'
        f'    <text>{_xml_escape(body)}</text>\n'
        '  </binding></visual>\n'
        '</toast>'
    )
    b64_xml = base64.b64encode(xml_text.encode("utf-8")).decode("ascii")
    ps = _PWSH_TEMPLATE.replace("{B64_XML}", b64_xml)
    res = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True, timeout=45)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout or "")[-200:])


def _xml_escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&apos;")
        .replace('"', "&quot;")
    )


# ------------------------------------------------------------------ dispatch

def _channel_config() -> list[str]:
    active = []
    if ALERT_WEBHOOK_URL:
        active.append("webhook")
    if ALERT_EMAIL_TO and SMTP_HOST:
        active.append("email")
    if ALERT_DESKTOP:
        active.append("desktop")
    return active


def deliver(alert_rows: list[dict]) -> dict:
    """Send a batch of fired alerts through every configured channel.

    Never raises. Result is persisted to meta["notify"] and returned.
    """
    out: dict = {"at": now_iso(), "delivered": 0, "sent_to": _channel_config(),
                 "results": [], "errors": []}
    if not alert_rows:
        db.set_meta("notify", out)
        return out

    for chan in _channel_config():
        try:
            if chan == "webhook":
                res = send_webhook(alert_rows, ALERT_WEBHOOK_URL, ALERT_WEBHOOK_FORMAT)
            elif chan == "email":
                res = send_email(alert_rows, ALERT_EMAIL_TO)
            else:
                res = send_desktop(alert_rows)
            out["results"].append(res)
            if res.get("ok"):
                out["delivered"] += 1
            else:
                out["errors"].append(res.get("error"))

        except Exception:  # noqa: BLE001 — channel failures must not abort the cycle
            tb = traceback.format_exc(limit=1)
            out["results"].append({"channel": chan, "ok": False, "error": tb[-200:]})
            out["errors"].append(f"{chan}: {tb.strip().splitlines()[-1]}")

    db.set_meta("notify", out)
    return out


def describe_alerts(alert_rows: list[dict]) -> list[str]:
    return [_one_line(a) for a in alert_rows]
