"""Demo data — synthetic VIIRS-like detections so the app is fully explorable
without a FIRMS MAP_KEY. Clearly flagged as demo everywhere in the DB and UI.

Two clusters are simulated near the first watched location:
  * a growing fire ~12 mi SSW, detected by all three satellites over the last 24h
  * an aging small fire ~35 mi NNW, thinning out (20-40h old)
"""
from __future__ import annotations

import math
from datetime import timedelta

from . import airnow
from .config import FEEDS
from .geo import haversine_miles
from .util import iso, utcnow

def _rng(seed: int):
    import random
    return random.Random(seed)


def _offset(lat: float, lon: float, north_miles: float, east_miles: float) -> tuple[float, float]:
    """Place synthetic detections at roughly the same distance at any latitude."""
    new_lat = max(-89.9, min(89.9, lat + north_miles / 69.0))
    scale = max(0.01, math.cos(math.radians(lat)))
    new_lon = (lon + east_miles / (69.0 * scale) + 180) % 360 - 180
    return new_lat, new_lon


def demo_detections(lat: float, lon: float, now=None) -> list[dict]:
    """Generate VIIRS-like rows over the past ~30h. Requires no network."""
    now = now or utcnow()
    bucket = int(now.timestamp() // 600)  # deterministic within 10-min windows
    rng = _rng(bucket * 7 + 13)
    rows: list[dict] = []

    # --- main fire: passes each satellite ~ every 100 min, all within last 24h
    for idx, feed in enumerate(FEEDS):
        t = 0.4 + idx * 0.55          # hours after detection start
        while t <= 24.0:
            drift_lat, drift_lon = _offset(lat, lon, -12.2 + 0.24 * t, -7.6 + 0.12 * t)
            n = rng.randint(2, 4)
            for _ in range(n):
                point_lat = drift_lat + rng.uniform(-0.010, 0.010)
                point_lon = (drift_lon + rng.uniform(-0.012, 0.012) + 180) % 360 - 180
                r = rng.random()
                conf = "h" if r < 0.30 else ("n" if r < 0.85 else "l")
                rows.append({
                    "satellite": feed.replace("_NRT", ""),
                    "source": "DEMO",
                    "demo": 1,
                    "lat": round(point_lat, 5),
                    "lon": round(point_lon, 5),
                    "acq_utc": iso(now - timedelta(hours=t, minutes=rng.randint(0, 6))),
                    "confidence": conf,
                    "frp": round(rng.uniform(24, 95), 1),
                    "daynight": "D" if 6 <= int(t) % 24 <= 18 else "N",
                })
            t += 1.65 + rng.uniform(-0.15, 0.25)

    # --- aging small fire ~35 mi NNW: only two satellites, going quiet
    for feed in FEEDS[:2]:
        t_start = 19.0 + FEEDS.index(feed) * 1.1
        for k in range(3):
            t = t_start + k * (5.5 + rng.uniform(0, 1.5))
            if t > 40.0:
                continue
            point_lat, point_lon = _offset(lat, lon, 31.4 - 0.28 * k, -13.2 + 0.16 * k)
            point_lat += rng.uniform(-0.006, 0.006)
            point_lon = (point_lon + rng.uniform(-0.006, 0.006) + 180) % 360 - 180
            rows.append({
                "satellite": feed.replace("_NRT", ""),
                "source": "DEMO",
                "demo": 1,
                "lat": round(point_lat, 5),
                "lon": round(point_lon, 5),
                "acq_utc": iso(now - timedelta(hours=t, minutes=rng.randint(0, 9))),
                "confidence": "n" if k == 0 else "l",
                "frp": round(max(6.0, 22.0 - k * 7.0 + rng.uniform(-3, 3)), 1),
                "daynight": "N",
            })
    rows.sort(key=lambda r: r["acq_utc"])
    return rows


def demo_pm25(lat: float, lon: float, now=None) -> dict:
    """Synthetic air-quality reading, mildly elevated as the main fire stays active."""
    now = now or utcnow()
    bucket = int(now.timestamp() // 1800)
    rng = _rng(bucket)
    base = 14 + 6 * math.sin(now.timestamp() / 43200)  # slow daily cycle
    pm = max(2.0, base + rng.uniform(-3, 8) + (14 if _fire_recent() else 0))
    pm = round(min(160.0, pm), 1)
    aqi = airnow.pm25_to_aqi(pm)
    cat = ("Good" if aqi <= 50 else "Moderate" if aqi <= 100 else
           "Unhealthy for Sensitive Groups" if aqi <= 150 else "Unhealthy")
    from .util import now_iso
    return {
        "pm25": pm,
        "aqi": aqi,
        "category": cat,
        "observed_local": None,
        "retrieved_utc": now_iso(),
        "monitor_area": "DEMO (synthetic reading)",
        "state": None,
        "source": "DEMO",
    }


def _fire_recent() -> bool:
    """Roughly within the last 6h the demo fire is pumped up."""
    return (int(utcnow().timestamp() // 600) % 6) > 0


def sanity_distance_check(lat: float, lon: float) -> float:
    """Info for tests: distance from a watched location to the main demo fire."""
    fire_lat, fire_lon = _offset(lat, lon, -12.2, -7.6)
    return haversine_miles(lat, lon, fire_lat, fire_lon)
