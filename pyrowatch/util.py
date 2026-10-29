"""Small shared helpers: timestamps, json, numbers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """UTC ISO-8601 with Z suffix (the app's canonical storage format)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return iso(utcnow())


def parse_iso(s: str) -> datetime:
    if not s:
        raise ValueError("empty timestamp")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_hours(dt: datetime, ref: datetime | None = None) -> float:
    ref = ref or utcnow()
    return max(0.0, (ref - dt).total_seconds() / 3600.0)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def jdump(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


_UNSET = object()


def jload(s: str | None, default: Any = _UNSET) -> Any:
    """json.loads with a sentinel default so callers can ask for `None`."""
    if default is _UNSET:
        default = {}
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


def rnd(x: float | int | None, n: int = 3) -> float | None:
    if x is None:
        return None
    try:
        return round(float(x), n)
    except Exception:
        return None
