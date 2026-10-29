"""National Weather Service API client — gridpoint wind and humidity.

/points/{lat},{lon} -> forecastGridData URL -> windDirection/windSpeed/relativeHumidity
series with validTime intervals like "2026-10-21T15:00:00+00:00/PT2H".
timestamps are kept in UTC.
"""
from __future__ import annotations

import re
import time

import requests

from .config import NWS_USER_AGENT

POINTS_URL = "https://api.weather.gov/points"

_HEADERS = {"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"}


class WeatherError(Exception):
    pass


# (office, gx, gy) -> {"grid_url", "update_time", "expires"}
_GRID_CACHE: dict[tuple, dict] = {}
_GRID_TTL = 3600  # 1h

_DUR = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$")


def _parse_valid_time(vt: str) -> tuple[str, str | None]:
    """'2026-10-21T15:00:00+00:00/PT2H' -> (start_iso, end_iso|None)."""
    start, _, dur = vt.partition("/")
    end = None
    if dur:
        m = _DUR.match(dur.strip())
        if m:
            h = int(m.group(1) or 0)
            mi = int(m.group(2) or 0)
            sec = float(m.group(3) or 0)
            end_dt = _iso_dt(start) + _td(h, mi, sec)
            end = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        end = start
    return start, end


def _iso_dt(s: str):
    from datetime import datetime, timezone
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _td(h: int, mi: int, sec: float):
    from datetime import timedelta
    return timedelta(hours=h, minutes=mi, seconds=sec)


def _series_value(series: dict, target_iso: str | None) -> tuple:
    """Pick value closest-in-time to target (prefer an interval that contains it)."""
    values = series.get("values") or []
    if not values:
        return None, None, None
    target = _iso_dt(target_iso).timestamp() if target_iso else _iso_dt(values[-1]["validTime"].partition("/")[0]).timestamp()
    best = None
    best_delta = None
    for v in values:
        start, end = _parse_valid_time(v["validTime"])
        t = _iso_dt(start)
        # interval containing target gets priority
        if end and _iso_dt(end).timestamp() >= target >= t.timestamp():
            return v.get("value"), start, end
        delta = abs(t.timestamp() - target)
        if best_delta is None or delta < best_delta:
            best, best_delta = v, delta
    start, end = _parse_valid_time(best["validTime"])
    return best.get("value"), start, end


def _gridpoint(lat: float, lon: float) -> dict:
    key = (round(lat, 2), round(lon, 2))
    cached = _GRID_CACHE.get(key)
    if cached and cached["expires"] > time.time():
        return cached

    r = requests.get(f"{POINTS_URL}/{round(lat,4)},{round(lon,4)}", headers=_HEADERS, timeout=20)
    if r.status_code == 404:
        raise WeatherError("Location outside NWS coverage (US only)")
    r.raise_for_status()
    props = r.json().get("properties") or {}
    grid_url = props.get("forecastGridData")
    if not grid_url:
        raise WeatherError("No forecastGridData in /points response")
    office, gx, gy = "unknown", None, None
    m = re.search(r"/gridpoints/([A-Za-z]+)/(\d+(?:\.\d+)?),(\d+(?:\.\d+)?)/?$", grid_url)
    if m:
        office, gx, gy = m.group(1).upper(), m.group(2), m.group(3)
    entry = {"grid_url": grid_url, "office": office, "gx": gx, "gy": gy,
             "update_time": None, "expires": time.time() + _GRID_TTL}
    _GRID_CACHE[key] = entry
    return entry


def get_conditions(lat: float, lon: float, target_iso: str) -> dict | None:
    """Wind direction (from, deg), wind speed (mph), RH (%) closest in time to target_iso. None on failure."""
    g = _gridpoint(lat, lon)
    r = requests.get(g["grid_url"], headers=_HEADERS, timeout=25)
    r.raise_for_status()
    props = r.json().get("properties") or {}

    wd_value, wd_start, _ = _series_value(props.get("windDirection") or {}, target_iso)
    ws_value, _, _ = _series_value(props.get("windSpeed") or {}, target_iso)
    rh_value, _, _ = _series_value(props.get("relativeHumidity") or {}, target_iso)

    from . import geo as _geo

    wind_dir_deg = None
    if isinstance(wd_value, (int, float)):
        wind_dir_deg = float(wd_value) % 360.0
    elif isinstance(wd_value, str):
        wind_dir_deg = _geo.compass_to_deg(wd_value)

    wind_speed_mph = None
    if isinstance(ws_value, (int, float)):
        wind_speed_mph = float(ws_value) / 1.609344  # NWS grid winds are km/h
    elif isinstance(ws_value, str):
        num = re.match(r"^\s*([\d.]+)\s*k[mh]", ws_value.strip().lower())
        if num:
            wind_speed_mph = float(num.group(1)) / 1.609344

    rh_pct = float(rh_value) if isinstance(rh_value, (int, float)) else None

    update_time = props.get("updateTime")
    if update_time:
        g["update_time"] = update_time  # caching for freshness display

    out = {
        "wind_dir_deg": round(wind_dir_deg, 1) if wind_dir_deg is not None else None,
        "wind_dir_compass": _geo.compass_from_deg(wind_dir_deg) if wind_dir_deg is not None else None,
        "wind_speed_mph": round(wind_speed_mph, 1) if wind_speed_mph is not None else None,
        "rh_pct": round(rh_pct, 0) if rh_pct is not None else None,
        "forecast_update_utc": update_time,
        "valid_start_utc": wd_start,
        "office": g["office"],
        "retrieved_utc": _import_now_iso(),
        "source": "NWS forecast grid",
    }
    if out["wind_dir_deg"] is None and out["wind_speed_mph"] is None and out["rh_pct"] is None:
        return None
    return out


def _import_now_iso() -> str:
    from .util import now_iso
    return now_iso()
