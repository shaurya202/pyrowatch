"""FIRMS client: config errors, CSV parsing, per-feed resilience (all offline)."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "pyrowatch")

import pytest

from pyrowatch import firms


CSV_SAMPLE = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_ti5,frp,daynight\n"
    "40.01500,-105.27050,351.2,0.9,0.8,2026-10-21,1430,VIIRS_SNPP_S-NPP,"
    "VIIRS,n,2.0NRT,290.1,45.6,D\n"
    "40.02000,-105.28000,330.0,1.0,0.9,2026-10-21,1500,VIIRS_NOAA20_N20,"
    "VIIRS,85,2.0NRT,288.0,122.0,N\n"
    "46.41039,30.66365,342.5,0.5,0.5,2026-10-21,1632,NOAA21,VIIRS,10,2.0NRT,220.0,0.0,D\n"
)


class FakeResp:
    status_code = 200

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def test_norm_conf_letters_and_numbers():
    assert firms._norm_conf("l") == "l"
    assert firms._norm_conf("h") == "h"
    assert firms._norm_conf("n") == "n"
    assert firms._norm_conf("85") == "h"
    assert firms._norm_conf("50") == "n"
    assert firms._norm_conf("10") == "l"
    assert firms._norm_conf("junk") == "n"


def test_acq_iso():
    assert firms._acq_iso("2026-10-21", "1430") == "2026-10-21T14:30:00Z"
    assert firms._acq_iso("2026-10-21", "30") == "2026-10-21T00:30:00Z"


def test_requires_map_key(monkeypatch):
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "")
    with pytest.raises(firms.FirmsConfigError):
        firms.fetch_feed("VIIRS_SNPP_NRT", (1, 2, 3, 4), 1)


def test_parses_csv(monkeypatch):
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "K")
    monkeypatch.setattr(firms.requests, "get", lambda *a, **k: FakeResp(CSV_SAMPLE))
    rows = firms.fetch_feed("VIIRS_SNPP_NRT", (1, 2, 3, 4), 1)
    assert len(rows) == 3
    r0 = rows[0]
    assert r0["lat"] == 40.015
    assert r0["acq_utc"] == "2026-10-21T14:30:00Z"
    assert r0["confidence"] == "n"
    assert r0["frp"] == 45.6
    assert r0["daynight"] == "D"
    assert rows[1]["satellite"] == "VIIRS_NOAA20_N20"
    assert rows[1]["confidence"] == "h"   # numeric 85 mapped to high


def test_rejects_invalid_key_response(monkeypatch):
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "BAD")
    monkeypatch.setattr(firms.requests, "get",
                        lambda *a, **k: FakeResp("Invalid MAP_KEY"))
    with pytest.raises(firms.FirmsConfigError):
        firms.fetch_feed("VIIRS_SNPP_NRT", (1, 2, 3, 4), 1)


def test_fetch_all_survives_one_dead_feed(monkeypatch):
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "K")

    def boom(*a, **k):
        raise RuntimeError("feed down")

    def fake(feed, bbox, dr=None):
        if feed.endswith("OK"):
            return [{"satellite": feed, "source": feed, "demo": 0, "lat": 1.0, "lon": 2.0,
                     "acq_utc": "2026-10-21T00:00:00Z", "confidence": "n", "frp": None,
                     "daynight": None}]
        boom(feed)

    monkeypatch.setattr(firms, "fetch_feed", fake)
    rows = firms.fetch_all((1, 2, 3, 4), feeds=["A_OK", "B_DEAD"])
    assert [r["satellite"] for r in rows] == ["A_OK"]


def test_fetch_all_raises_when_everything_fails(monkeypatch):
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "K")

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(firms, "fetch_feed", boom)
    with pytest.raises(RuntimeError):
        firms.fetch_all((1, 2, 3, 4), feeds=["A", "B"])
