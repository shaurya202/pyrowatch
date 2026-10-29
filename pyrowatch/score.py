"""The context score.

NOT an official wildfire danger rating and NOT a prediction that a fire will
reach a location — a normalized 0-100 indicator of how much *satellite-detected
heat activity nearby, combined with weather and air quality* currently suggests
relevance to that location.

Weights are designed so distance and recency dominate (base weight 60):
    base(60) = distance factor * recency factor
    boosters  = confidence(6) + FRP(5) + repeated detections / multi-satellite(8)
                + downwind alignment(10) + dry air(4) + elevated PM2.5(6)
Missing inputs are excluded from the denominator, so a missing value does NOT
act like a zero; the response then carries a "limited data" explanation list.
"""
from __future__ import annotations

import math

from . import geo
from .config import SCORING_HORIZON_MILES, WEATHER_MIN_WIND_MPH
from .util import age_hours, parse_iso

BASE_WEIGHT = 60.0
DIST_SCALE_MILES = 30.0     # e-fold distance for the base factor
AGE_SCALE_HOURS = 24.0      # e-fold age for the base factor

WEIGHTS = {
    "confidence": 6.0,
    "frp": 5.0,
    "repeats": 8.0,
    "downwind": 10.0,
    "dry": 4.0,
    "pm25": 6.0,
}

CONF_SCORE = {"l": 0.35, "n": 0.65, "h": 0.95}

CATEGORIES = (
    (25.0, "Low", "#8bc34a"),
    (50.0, "Moderate", "#ffb300"),
    (75.0, "Elevated", "#ff6d00"),
    (1000.0, "High", "#e53935"),
)


def category_for(score: float) -> tuple[str, str]:
    for cutoff, label, color in CATEGORIES:
        if score < cutoff:
            return label, color
    return CATEGORIES[-1][1], CATEGORIES[-1][2]


def _distance_factor(miles: float) -> float:
    return math.exp(-max(0.0, miles) / DIST_SCALE_MILES)


def _age_factor(hours: float) -> float:
    return math.exp(-max(0.0, hours) / AGE_SCALE_HOURS)


def score_one(location: dict, group: dict, group_detections: list[dict],
              weather: dict | None, air: dict | None, now=None) -> dict:
    """Score one (location, group) pair; returns everything the UI needs."""
    lat, lon = float(location["lat"]), float(location["lon"])

    # distance to the nearest detection of the group (points show pixel centers)
    dists = [geo.haversine_miles(lat, lon, d["lat"], d["lon"]) for d in group_detections]
    if not dists:
        dists = [geo.haversine_miles(lat, lon, group["center_lat"], group["center_lon"])]
    dist = min(dists)
    if dist > SCORING_HORIZON_MILES:
        return {"skipped": True, "distance_miles": round(dist, 1)}

    age_h = age_hours(parse_iso(group["last_acq_utc"]), ref=now) if now else age_hours(parse_iso(group["last_acq_utc"]))
    dist_f = _distance_factor(dist)
    age_f = _age_factor(age_h)
    base = BASE_WEIGHT * dist_f * age_f

    reasons: list[str] = []
    limited: list[str] = []
    boost = 0.0
    avail = BASE_WEIGHT

    demo_note = " (demo data)" if group.get("demo") else ""

    # confidence ----------------------------------------------------------
    conf = (group.get("confidence") or "n")
    avail += WEIGHTS["confidence"]
    boost += WEIGHTS["confidence"] * CONF_SCORE.get(conf, 0.65)
    if conf == "h":
        reasons.append("High-confidence satellite detection")
    elif conf == "l":
        reasons.append("Only low-confidence detections")

    # FRP ------------------------------------------------------------------
    frp = group.get("frp") or 0
    avail += WEIGHTS["frp"]
    boost += WEIGHTS["frp"] * min(1.0, math.log10(1 + max(0.0, frp)) / 2.0)
    if frp >= 60:
        reasons.append(f"Strong heat signature — FRP {frp:.0f} MW")

    # repeated detections / multiple satellites ----------------------------
    count = group.get("detection_count") or 0
    sats = group.get("satellites") or []
    avail += WEIGHTS["repeats"]
    rep = min(count, 10) / 10.0 * 4.0 + min(max(0, len(sats) - 1), 2) * 2.0
    boost += WEIGHTS["repeats"] * (rep / 8.0)
    if len(sats) > 1:
        names = ", ".join(s.replace("_NRT", "").replace("VIIRS_", "VIIRS ") for s in sats)
        reasons.append(f"Detected by multiple satellites ({names})")
    if count >= 5:
        reasons.append(f"{count} distinct heat points so far")

    # downwind alignment ---------------------------------------------------
    wind_dir_deg = (weather or {}).get("wind_dir_deg")
    wind_speed = (weather or {}).get("wind_speed_mph")
    rh = (weather or {}).get("rh_pct")
    if wind_dir_deg is None and wind_speed is None and rh is None:
        limited.append("weather (wind / humidity)")
    else:
        if wind_dir_deg is not None and wind_speed is not None:
            dw = geo.downwind_angle(wind_dir_deg, group["center_lat"], group["center_lon"], lat, lon)
            avail += WEIGHTS["downwind"]
            if wind_speed >= WEATHER_MIN_WIND_MPH and dw <= 60:
                f = (1.0 - dw / 60.0) * min(1.0, wind_speed / 20.0)
                boost += WEIGHTS["downwind"] * f
                compass = geo.compass_from_deg(wind_dir_deg)
                reasons.append(f"You are roughly downwind of this activity (wind from {compass} at {wind_speed:.0f} mph)")
            elif wind_speed is not None and wind_speed >= WEATHER_MIN_WIND_MPH and dw > 180 - 25:
                reasons.append("Location is upwind of this activity")
        elif wind_dir_deg is None or wind_speed is None:
            limited.append("downwind calculation")
        if rh is not None:
            avail += WEIGHTS["dry"]
            if rh < 35:
                boost += WEIGHTS["dry"] * max(0.0, 1.0 - rh / 35.0)
                reasons.append(f"Dry air ({rh:.0f}% RH)")

    # nearby PM2.5 ---------------------------------------------------------
    pm25 = (air or {}).get("pm25") if air else None
    if pm25 is None:
        limited.append("air quality (PM2.5)")
    else:
        avail += WEIGHTS["pm25"]
        excess = max(0.0, float(pm25) - 12.0)
        boost += WEIGHTS["pm25"] * min(1.0, excess / 55.0)
        if float(pm25) >= 20:
            reasons.append(f"Elevated nearby PM2.5 ({float(pm25):.0f} µg/m³)")

    raw = ((base + boost) / max(avail, BASE_WEIGHT)) * 100.0
    score = round(max(0.0, min(99.0, raw)), 1)

    # canonical reason list (always present)
    reasons.insert(0, f"Nearest detection {dist:.1f} mi away, {age_h:.1f}h old{demo_note}")
    out = {
        "score": score,
        "category": category_for(score)[0],
        "category_color": category_for(score)[1],
        "limited": limited,
        "reasons": reasons,
        "distance_miles": round(dist, 1),
        "age_hours": round(age_h, 2),
        "components": {
            "base": round(raw, 2),
        },
    }
    return out
