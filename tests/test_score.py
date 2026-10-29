"""Context score: distance/recency dominance, boosters, limited-data handling."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "pyrowatch")

from pyrowatch import score

BOULDER = {"id": 1, "name": "Home", "lat": 40.0150, "lon": -105.2705}


def _group(dist_deg=0.18, age_h=2.0, conf="h", frp=40.0, count=6, sats=None, dets=True):
    """A group ~ dist_deg*69 miles away, age_h old, with one synthetic detection."""
    sats = sats or ["VIIRS_SNPP"]
    g = {
        "center_lat": BOULDER["lat"] + dist_deg,
        "center_lon": BOULDER["lon"],
        "first_acq_utc": "2026-10-20T00:00:00Z",
        "last_acq_utc": _ts(age_h),
        "detection_count": count,
        "confidence": conf,
        "frp": frp,
        "satellites": sats,
        "demo": 0,
        "wfigs": None,
        "weather": None,
    }
    dets = [{
        "lat": BOULDER["lat"] + dist_deg - 0.005,
        "lon": BOULDER["lon"],
    }] if dets else []
    return g, dets


def _ts(hours_ago: float) -> str:
    import datetime as dt
    base = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=hours_ago)
    return base.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_recent_and_near_beats_old_and_far():
    g_near, d_near = _group(dist_deg=0.07, age_h=0.5)      # ~5 mi, 30 min
    g_far, d_far = _group(dist_deg=0.72, age_h=36.0)       # ~50 mi, 36 h
    s_near = score.score_one(BOULDER, g_near, d_near, None, None)
    s_far = score.score_one(BOULDER, g_far, d_far, None, None)
    assert s_near["score"] > s_far["score"] + 20


def test_score_bounds():
    g, dets = _group(dist_deg=0.02, age_h=0.2, conf="h", frp=200.0, count=10,
                     sats=["VIIRS_SNPP", "VIIRS_NOAA20", "VIIRS_NOAA21"])
    s = score.score_one(BOULDER, g, dets, None, None)
    assert 0.0 <= s["score"] <= 99.0


def test_missing_weather_and_air_listed_as_limited():
    g, dets = _group()
    s = score.score_one(BOULDER, g, dets, None, None)
    assert any("weather" in x for x in s["limited"])
    assert any("PM2.5" in x for x in s["limited"])
    # missing inputs are excluded from the denominator, never scored as zero
    assert s["score"] >= 25


def test_downwind_boost():
    # wind from the north; group north of the location -> location is downwind
    g, dets = _group(dist_deg=0.18, age_h=1.0)
    g["center_lat"] = BOULDER["lat"] + 0.18
    wind = {"wind_dir_deg": 0.0, "wind_dir_compass": "N", "wind_speed_mph": 20.0,
            "rh_pct": 50.0}
    calm = score.score_one(BOULDER, g, dets, None, None)
    wind_score = score.score_one(BOULDER, g, dets, wind, None)
    assert wind_score["score"] > calm["score"]
    assert any("downwind" in r.lower() for r in wind_score["reasons"])


def test_upwind_no_boost_and_reason_present():
    # wind from the north; group SOUTH of the location -> location is upwind
    g, dets = _group(dist_deg=0.18, age_h=1.0)
    g["center_lat"] = BOULDER["lat"] - 0.18
    wind = {"wind_dir_deg": 0.0, "wind_dir_compass": "N", "wind_speed_mph": 20.0,
            "rh_pct": 50.0}
    s = score.score_one(BOULDER, g, dets, wind, None)
    assert any("upwind" in r.lower() for r in s["reasons"])


def test_upwind_not_added_when_wind_below_min_mph():
    # wind speed 2.0 < WEATHER_MIN_WIND_MPH (5.0) -> calm, no upwind reason
    g, dets = _group(dist_deg=0.18, age_h=1.0)
    g["center_lat"] = BOULDER["lat"] - 0.18
    wind = {"wind_dir_deg": 0.0, "wind_dir_compass": "N", "wind_speed_mph": 2.0,
            "rh_pct": 50.0}
    s = score.score_one(BOULDER, g, dets, wind, None)
    assert not any("upwind" in r.lower() for r in s["reasons"])


def test_elevated_pm25_adds_point():
    g, dets = _group()
    clean = score.score_one(BOULDER, g, dets, None, None)
    smoky = score.score_one(BOULDER, g, dets, None, {"pm25": 70.0})
    assert smoky["score"] > clean["score"]
    assert any("PM2.5" in r for r in smoky["reasons"])


def test_dry_air_adds_point():
    g, dets = _group()
    humid = score.score_one(BOULDER, g, dets, {"rh_pct": 80.0}, None)
    dry = score.score_one(BOULDER, g, dets, {"rh_pct": 10.0}, None)
    assert dry["score"] > humid["score"]
    assert any("Dry" in r for r in dry["reasons"])


def test_multi_satellite_reason():
    g, dets = _group(sats=["VIIRS_SNPP", "VIIRS_NOAA20"])
    s = score.score_one(BOULDER, g, dets, None, None)
    assert any("multiple satellites" in r for r in s["reasons"])


def test_reasons_contain_distance_and_age():
    g, dets = _group(dist_deg=0.10, age_h=3.0)
    s = score.score_one(BOULDER, g, dets, None, None)
    assert s["reasons"][0].startswith("Nearest detection")
    assert "h old" in s["reasons"][0]


def test_beyond_horizon_is_skipped():
    g, dets = _group(dist_deg=3.0, age_h=1.0)  # ~207 mi
    s = score.score_one(BOULDER, g, dets, None, None)
    assert s.get("skipped") and s["distance_miles"] > 150


def test_falls_back_to_group_center_without_detections():
    g, dets = _group(dets=False)
    s = score.score_one(BOULDER, g, [], None, None)
    assert 0 <= s["score"] <= 99
