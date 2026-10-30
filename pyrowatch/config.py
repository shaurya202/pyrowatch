"""Central configuration. Reads .env (project root) with environment overrides."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:  # optional dependency; env vars alone work fine without it
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

E = os.environ


def _s(name: str, default: str = "") -> str:
    v = E.get(name)
    return default if v is None or v.strip() == "" else v.strip()


def _i(name: str, default: int) -> int:
    try:
        return int(E.get(name, "").strip())
    except Exception:
        return default


def _f(name: str, default: float) -> float:
    try:
        return float(E.get(name, "").strip())
    except Exception:
        return default


def _b(name: str, default: bool) -> bool:
    return E.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# External API keys (both free)
# ---------------------------------------------------------------------------
FIRMS_MAP_KEY = _s("FIRMS_MAP_KEY") or _s("FIRMS_API_KEY")
AIRNOW_API_KEY = _s("AIRNOW_API_KEY")

# ---------------------------------------------------------------------------
# FIRMS: Which VIIRS NRT feeds to poll, and how many days back (1..5)
# ---------------------------------------------------------------------------
FEEDS = [f.strip().upper() for f in _s("FIRMS_FEEDS", "VIIRS_SNPP_NRT,VIIRS_NOAA20_NRT,VIIRS_NOAA21_NRT").split(",") if f.strip()]
DAY_RANGE = max(1, min(5, _i("FIRMS_DAY_RANGE", 2)))

FIRMS_AREA_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# ---------------------------------------------------------------------------
# Polling / storage
# ---------------------------------------------------------------------------
POLL_INTERVAL_MINUTES = max(2.0, _f("POLL_INTERVAL_MINUTES", 10.0))
_DEFAULT_DB = "/tmp/pyrowatch.db" if E.get("VERCEL") else "data/pyrowatch.db"
DB_PATH = Path(_s("DB_PATH", _DEFAULT_DB))
if not DB_PATH.is_absolute():
    DB_PATH = ROOT / DB_PATH

# DEMO_MODE: auto -> demo data when no FIRMS key; force -> always demo; off -> never
DEMO_MODE = _s("DEMO_MODE", "auto").lower()
DEMO_AQI = _b("DEMO_AQI", True)  # synthesize air-quality readings in demo mode

# If a FIRMS key exists we are NOT in demo mode for detections
DEMO_FIRMS: bool = (DEMO_MODE == "force") or (DEMO_MODE == "auto" and not FIRMS_MAP_KEY)

# ---------------------------------------------------------------------------
# External endpoints that need no key
# ---------------------------------------------------------------------------
NWS_USER_AGENT = _s("NWS_USER_AGENT", "PyroWatch/1.0 (personal informational app)")
CENSUS_GEOCODE_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"

# WFIGS (NIFC) authoritative services - resolved from NIFC Open Data / ArcGIS item metadata
WFIGS_ENABLED = _b("WFIGS_ENABLED", True)
WFIGS_POINTS_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "WFIGS_Incident_Locations_Current/FeatureServer/0/query"
)
WFIGS_PERIMETERS_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"
)

# ---------------------------------------------------------------------------
# Detection grouping
# ---------------------------------------------------------------------------
GROUP_RADIUS_MILES = _f("GROUP_RADIUS_MILES", 1.5)   # detections within this of a group center = same fire
GROUP_MAX_GAP_HOURS = _f("GROUP_MAX_GAP_HOURS", 20.0)  # gap between detections above this starts a new group

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
SCORING_HORIZON_MILES = _f("SCORING_HORIZON_MILES", 150.0)  # max distance from a location that gets scored

# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
ALERT_SCORE_DELTA = _f("ALERT_SCORE_DELTA", 15.0)
ALERT_SCORE_COOLDOWN_MIN = _f("ALERT_SCORE_COOLDOWN_MIN", 240.0)
WEATHER_MIN_WIND_MPH = 5.0  # below this, downwind math is meaningless

# ---------------------------------------------------------------------------
# Alert delivery channels (all optional; see README "Notifications")
#   webhook  — POST JSON to a URL (e.g. Discord/Slack relay, your own code)
#   email    — standard SMTP; needs SMTP_HOST + recipients
#   desktop  — Windows toast notification
# ---------------------------------------------------------------------------
ALERT_WEBHOOK_URL = _s("ALERT_WEBHOOK_URL")             # "" disables webhook
ALERT_WEBHOOK_FORMAT = _s("ALERT_WEBHOOK_FORMAT", "json")  # "json" | "slack"
ALERT_EMAIL_TO = [a.strip() for a in _s("ALERT_EMAIL_TO").split(",") if a.strip()]
SMTP_HOST = _s("SMTP_HOST")
SMTP_PORT = _i("SMTP_PORT", 587)
SMTP_TLS = _b("SMTP_TLS", True)
SMTP_SSL = _b("SMTP_SSL", False)  # with SMTP_SSL the port is usually 465
SMTP_USER = _s("SMTP_USER")
SMTP_PASS = _s("SMTP_PASS")
ALERT_DESKTOP = _b("ALERT_DESKTOP", sys.platform == "win32")  # Windows toast, max one per cycle

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
HOST = _s("HOST", "127.0.0.1")
PORT = _i("PORT", 8000)


def describe() -> dict:
    """Snapshot of configuration used by the UI status chips."""
    return {
        "firms_key": bool(FIRMS_MAP_KEY),
        "airnow_key": bool(AIRNOW_API_KEY),
        "feeds": FEEDS,
        "day_range": DAY_RANGE,
        "poll_interval_minutes": POLL_INTERVAL_MINUTES,
        "demo_mode": DEMO_MODE,
        "demo_firms": DEMO_FIRMS,
        "demo_aqi": DEMO_AQI and not AIRNOW_API_KEY,
        "wfigs": WFIGS_ENABLED,
        "notify": {
            "webhook": bool(ALERT_WEBHOOK_URL),
            "email": bool(ALERT_EMAIL_TO and SMTP_HOST),
            "desktop": bool(ALERT_DESKTOP),
        },
    }
