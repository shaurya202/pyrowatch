"""NASA FIRMS Area API client — VIIRS NRT thermal-anomaly detections.

Docs: https://firms.modaps.eosdis.nasa.gov/api/area/
Each VIIRS point is the center of a ~375 m pixel where unusual heat was sensed.
Confidence for VIIRS NRT is 'l' (low), 'n' (nominal) or 'h' (high).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

import requests

from .config import DAY_RANGE, FIRMS_AREA_URL, FIRMS_MAP_KEY


class FirmsConfigError(Exception):
    pass


def _norm_conf(raw: str) -> str:
    raw = (raw or "").strip().lower()
    if raw in ("l", "n", "h"):
        return raw
    # some feeds ship a 0-100 integer
    try:
        v = int(float(raw))
    except Exception:
        return "n"
    if v >= 80:
        return "h"
    if v >= 40:
        return "n"
    return "l"


def _acq_iso(date_s: str, time_s: str) -> str:
    t = (time_s or "").strip().zfill(4)
    dt = datetime.strptime(f"{date_s.strip()} {t}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_feed(feed: str, bbox: tuple[float, float, float, float], day_range: int | None = None) -> list[dict]:
    """Fetch one VIIRS NRT feed for a bbox = (west, south, east, north).

    Returns rows: {satellite, source, demo, lat, lon, acq_utc, confidence, frp, daynight}
    """
    if not FIRMS_MAP_KEY:
        raise FirmsConfigError("FIRMS MAP_KEY not configured (get one free at firms.modaps.eosdis.nasa.gov/api/map_key/)")
    west, south, east, north = (round(float(x), 5) for x in bbox)
    dr = day_range or DAY_RANGE
    url = f"{FIRMS_AREA_URL}/{FIRMS_MAP_KEY}/{feed}/{west},{south},{east},{north}/{dr}"
    r = requests.get(url, timeout=45)
    text = (r.text or "").strip()
    if r.status_code != 200:
        raise RuntimeError(f"FIRMS HTTP {r.status_code}: {text[:120]}")
    if not text:
        return []
    if "invalid" in text[:64].lower() or "not authorized" in text[:64].lower():
        raise FirmsConfigError(f"FIRMS rejected the MAP_KEY: {text[:100]}")

    out: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            lat = round(float(row["latitude"]), 5)
            lon = round(float(row["longitude"]), 5)
        except Exception:
            continue
        try:
            frp = float(row.get("frp") or 0) or None
        except Exception:
            frp = None
        out.append(
            {
                "satellite": (row.get("satellite") or feed).strip(),
                "source": feed,
                "demo": 0,
                "lat": lat,
                "lon": lon,
                "acq_utc": _acq_iso(row.get("acq_date", ""), row.get("acq_time", "0000")),
                "confidence": _norm_conf(row.get("confidence", "n")),
                "frp": frp,
                "daynight": (row.get("daynight") or "").strip() or None,
            }
        )
    return out


def fetch_all(bbox: tuple[float, float, float, float], feeds: list[str] | None = None,
              day_range: int | None = None) -> list[dict]:
    """Fetch every configured VIIRS NRT feed and concatenate. Raises on hard errors."""
    rows: list[dict] = []
    errors: list[str] = []
    for feed in (feeds or []):
        try:
            rows.extend(fetch_feed(feed, bbox, day_range))
        except FirmsConfigError:
            raise
        except Exception as e:  # per-feed resilience
            errors.append(f"{feed}: {e}")
    if errors and not rows:
        raise RuntimeError("All FIRMS feeds failed: " + "; ".join(errors))
    return rows
