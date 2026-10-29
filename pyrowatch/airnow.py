"""EPA AirNow client — PM2.5 AQI near a location (free API key required).

Uses the on-map "current observation by lat/lon" endpoint. AirNow readings are
preliminary when first published and monitors can be several miles away.
Timestamps: the observation time returned by AirNow is in the monitor's local
timezone; we store it verbatim plus the time we retrieved it (UTC), and note
that in the UI ("as of retrieval").
"""
from __future__ import annotations

import requests

from .config import AIRNOW_API_KEY
from .util import now_iso

_URL = "https://www.airnowapi.org/aq/observation/current/ziplatlong/"


def aqi_to_pm25(aqi: float) -> float:
    """EPA inverse PM2.5-AQI mapping (approx µg/m³)."""
    cl = [0, 12.0, 35.4, 55.4, 150.4, 250.4, 350.4, 500.4]
    il = [0, 50, 100, 150, 200, 300, 400, 500]
    if aqi is None:
        return None
    aqi = max(0.0, min(500.0, float(aqi)))
    for i in range(1, len(il)):
        if aqi <= il[i]:
            acl_span = cl[i] - cl[i - 1]
            il_span = il[i] - il[i - 1]
            return round((cl[i - 1] + (aqi - il[i - 1]) * acl_span / il_span), 1)
    return round(cl[-1], 1) if aqi <= 500 else 500.5


def pm25_to_aqi(pm25: float) -> int:
    """EPA forward PM2.5-AQI mapping."""
    cl = [0.0, 12.0, 35.4, 55.4, 150.4, 250.4, 350.4, 500.4]
    il = [0, 50, 100, 150, 200, 300, 400, 500]
    if pm25 is None:
        return None
    p = max(0.0, float(pm25))
    for i in range(1, len(cl)):
        if p <= cl[i]:
            return int((il[i - 1] + (p - cl[i - 1]) * (il[i] - il[i - 1]) / (cl[i] - cl[i - 1])))
    return 500


class AirNowError(Exception):
    pass


def current_pm25(lat: float, lon: float, distance_miles: int = 25) -> dict | None:
    """Nearest PM2.5 observation to (lat, lon). None if no data; raises on hard errors."""
    if not AIRNOW_API_KEY:
        return None
    params = {
        "format": "application/json",
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "distance": distance_miles,
        "API_KEY": AIRNOW_API_KEY,
    }
    r = requests.get(_URL, params=params, timeout=25)
    if r.status_code != 200:
        raise AirNowError(f"AirNow HTTP {r.status_code}: {r.text[:120]}")
    rows = r.json()
    if isinstance(rows, dict):
        err_list = rows.get("WebServiceError")
        if isinstance(err_list, list):
            for err in err_list:
                msg = err.get("Message") or ""
                if "no observations" in msg.lower() or "not found" in msg.lower():
                    return None
                raise AirNowError(f"AirNow: {msg}")
        msg = rows.get("ReportError") or ""
        if msg:
            raise AirNowError(f"AirNow: {msg}")
        rows = rows.get("Observations") or []
    pm = None
    for row in rows or []:
        name = (row.get("parameterName") or row.get("ParameterName") or "").upper().replace(" ", "")
        if "PM2.5" in name or name == "PM25":
            pm = row
            break
    if pm is None:
        return None
    aqi_raw = pm.get("nowcastAQI") if pm.get("nowcastAQI") is not None else pm.get("AQI")
    if aqi_raw is None:
        aqi_raw = pm.get("aqi")
    aqi = int(round(float(aqi_raw))) if aqi_raw is not None else None
    conc = pm.get("Value") or pm.get("value") or pm.get("RawConcentration") or pm.get("Concentration") or None
    pm25 = float(conc) if conc is not None else (aqi_to_pm25(aqi) if aqi is not None else None)
    date = (pm.get("dateObserved") or pm.get("DateObserved") or "").strip()
    hour = pm.get("hourObserved") if pm.get("hourObserved") is not None else pm.get("HourObserved")
    tz = (pm.get("localTimeZone") or pm.get("LocalTimeZone") or "").strip()
    if hour is not None:
        if isinstance(hour, (int, float)):
            hour_str = f"{int(hour):02d}:00"
        else:
            hour_s = str(hour).strip()
            if ":" in hour_s:
                hour_str = hour_s
            else:
                try:
                    hour_str = f"{int(hour_s):02d}:00"
                except ValueError:
                    hour_str = hour_s
        observed_local = f"{date} {hour_str} {tz}".strip().replace("  ", " ")
    else:
        observed_local = date

    cat = pm.get("aqiCategoryName") or pm.get("Category")
    cat_name = cat.get("Name") if isinstance(cat, dict) else (cat if isinstance(cat, str) else None)
    area = pm.get("reportingAreaName") or pm.get("ReportingArea") or pm.get("siteName")
    state = pm.get("stateCode") or pm.get("StateCode") or None
    site_name = pm.get("siteName")
    return {
        "pm25": pm25,
        "aqi": aqi,
        "category": cat_name,
        "observed_local": observed_local,
        "observed_tz_note": "AirNow reports observation time in monitor-local time",
        "retrieved_utc": now_iso(),
        "monitor_area": area,
        "state": state,
        "site_name": site_name,
        "source": "AirNow",
    }
