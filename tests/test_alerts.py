"""Alert rules: new_group / new_satellite / score_increase + cooldown."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "pyrowatch")

import pytest

from pyrowatch import alerts

LOC = {"id": 1, "name": "Home", "lat": 40.0, "lon": -105.0, "radius_miles": 15}


@pytest.fixture(autouse=True)
def _isolated_db(fresh_db):
    """Every alert test gets its own DB (states persist between step() calls)."""
    yield


def _threat(score=50.0, sats=("VIIRS_SNPP",)):
    return {
        "group_id": 7,
        "score": score,
        "distance_miles": 12.0,
        "confidence": "n",
        "detection_count": 5,
        "satellites": list(sats),
        "last_acq_utc": "2026-10-21T18:00:00Z",
        "demo": True,
        "wfigs": None,
        "reasons": [],
    }


def step(loc, threat):
    """One pipeline-sized evaluation: evaluate + persist the returned states."""
    fired, states = alerts.evaluate_location(loc, [threat])
    for st in states:
        alerts.db.save_group_state(st)
    return fired


def test_first_seen_inside_radius_fires_new_group():
    fired = step(LOC, _threat())
    assert [a["type"] for a in fired] == ["new_group"]
    assert fired[0]["location_id"] == 1
    assert fired[0]["detail"]["distance_miles"] == 12.0
    assert fired[0]["detail"]["detection_time_utc"] == "2026-10-21T18:00:00Z"


def test_outside_radius_never_fires():
    threat = _threat()
    threat["distance_miles"] = 25.0
    assert step(LOC, threat) == []


def test_repeat_same_satellite_is_silent():
    assert [a["type"] for a in step(LOC, _threat())] == ["new_group"]
    assert step(LOC, _threat()) == []          # same satellite overpass again


def test_new_satellite_fires_once():
    step(LOC, _threat())
    fired = step(LOC, _threat(sats=("VIIRS_SNPP", "VIIRS_NOAA20")))
    assert [a["type"] for a in fired] == ["new_satellite"]
    assert fired[0]["detail"]["new_sources"] == ["VIIRS_NOAA20"]
    # third pass with the same pair -> quiet again
    assert step(LOC, _threat(sats=("VIIRS_SNPP", "VIIRS_NOAA20"))) == []


def test_score_increase_needs_delta():
    step(LOC, _threat(score=20.0))
    fired = step(LOC, _threat(score=30.0))     # +10 < ALERT_SCORE_DELTA (15)
    assert not any(a["type"] == "score_increase" for a in fired)
    fired2 = step(LOC, _threat(score=55.0))    # +25 >= 15
    assert any(a["type"] == "score_increase" for a in fired2)


def test_score_increase_cooldown_blocks_immediate_refire():
    """A fresh +15 jump is silenced while inside the cooldown window."""
    import datetime as dt
    from pyrowatch import util

    step(LOC, _threat(score=20.0))                        # new_group (prev None)
    fired = step(LOC, _threat(score=35.0))                # +15 -> fires score_increase
    assert any(a["type"] == "score_increase" for a in fired)
    fired2 = step(LOC, _threat(score=50.0))               # another +15, seconds later
    assert not any(a["type"] == "score_increase" for a in fired2)

    # once the recorded cooldown is stale again, the next +15 fires
    state = alerts.db.get_group_state(LOC["id"], 7)
    old = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=1)
    state["notes"]["score_increase"] = util.iso(old)
    alerts.db.save_group_state(state)
    fired3 = step(LOC, _threat(score=65.0))
    assert any(a["type"] == "score_increase" for a in fired3)


def test_state_notes_record_types():
    fired, states = alerts.evaluate_location(LOC, [_threat(sats=("VIIRS_SNPP", "VIIRS_NOAA20"))])
    notes = states[0]["notes"]
    assert "new_group" in notes
    assert states[0]["first_seen_utc"]
    assert sorted(states[0]["sats_seen"]) == ["VIIRS_NOAA20", "VIIRS_SNPP"]


def test_demo_note_in_reason_text():
    fired = step(LOC, _threat())
    assert "demo data" in fired[0]["reason"]


def test_build_reason_includes_wfigs_name():
    threat = _threat()
    threat["wfigs"] = {"name": "Left Hand Fire"}
    fired = alerts.evaluate_location(LOC, [threat])[0]
    alert = fired[0]
    assert alert["detail"]["wfigs"] == "Left Hand Fire"


def test_fire_initially_outside_triggers_new_group_when_moving_inside():
    # Initially outside radius (25 mi > 15 mi radius)
    threat = _threat()
    threat["distance_miles"] = 25.0
    fired1 = step(LOC, threat)
    assert fired1 == []

    # Check state recorded in_radius = False
    st = alerts.db.get_group_state(LOC["id"], threat["group_id"])
    assert st is not None
    assert st["notes"].get("in_radius") is False

    # Moves inside radius (12 mi <= 15 mi radius)
    threat["distance_miles"] = 12.0
    fired2 = step(LOC, threat)
    assert [a["type"] for a in fired2] == ["new_group"]
    assert fired2[0]["detail"]["distance_miles"] == 12.0

    # Stays inside on subsequent cycle -> quiet (no new_group refire)
    fired3 = step(LOC, threat)
    assert fired3 == []
