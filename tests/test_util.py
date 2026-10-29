"""Timestamps, JSON helpers, and the jload sentinel contract."""
import sys
from datetime import datetime, timezone

sys.path.insert(0, "pyrowatch")
sys.path.insert(0, ".")

from pyrowatch.util import age_hours, clamp, iso, jdump, jload, parse_iso, rnd


def test_iso_parse_roundtrip():
    dt = datetime(2026, 10, 21, 12, 34, 56, tzinfo=timezone.utc)
    s = iso(dt)
    assert s == "2026-10-21T12:34:56Z"
    back = parse_iso(s)
    assert back == dt


def test_parse_iso_accepts_offset_form():
    dt = parse_iso("2026-10-21T12:34:56+00:00")
    assert dt.strftime("%H:%M:%S") == "12:34:56"


def test_age_hours_zero_for_future():
    future = datetime.now(timezone.utc)
    future_probe = datetime.fromtimestamp(future.timestamp() + 3600, tz=timezone.utc)
    assert age_hours(future_probe) == 0.0


def test_clamp_and_rnd():
    assert clamp(5, 0, 3) == 3
    assert clamp(-1, 0, 3) == 0
    assert rnd(1.23456, 2) == 1.23
    assert rnd(None) is None
    assert rnd("abc") is None


def test_jdump_jload_roundtrip():
    payload = {"a": [1, 2, "x"], "b": None}
    assert jload(jdump(payload)) == payload


def test_jload_default_is_empty_dict_or_list():
    assert jload("") == {}
    assert jload(None) == {}
    assert jload("", []) == []
    assert jload(None, {}) == {}


def test_jload_explicit_none_default_not_swallowed():
    """Regression: default=None used to collapse to {} (the wfigs/weather bug)."""
    assert jload(None, None) is None
    assert jload("", None) is None
    assert jload("null", None) is None


def test_jload_bad_json_returns_default():
    assert jload("{not json", {"fallback": 1}) == {"fallback": 1}
