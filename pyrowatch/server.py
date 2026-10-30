"""FastAPI server: JSON API + static web UI."""
from __future__ import annotations

import os
import threading
from datetime import timedelta
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import db, geo, pipeline
from .geocode import GeocodeError, geocode, suggest_addresses
from .util import age_hours, iso, jload, now_iso, parse_iso, utcnow


class LocationIn(BaseModel):
    """Request body for creating/updating a monitored location."""
    name: str = Field(min_length=1, max_length=80)
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    radius_miles: int = Field(default=15, ge=1, le=200)

    @model_validator(mode="after")
    def validate_coords(self) -> LocationIn:
        if self.lat is not None and not (-90 <= self.lat <= 90):
            raise HTTPException(400, "Latitude must be between -90 and 90")
        if self.lon is not None and not (-180 <= self.lon <= 180):
            raise HTTPException(400, "Longitude must be between -180 and 180")
        return self


def _run_initial_cycle() -> None:
    def _job():
        try:
            pipeline.run_cycle("startup")
        except Exception:
            db.set_meta("cycle", {"status": "error", "finished": now_iso(), "error": "startup"})
    threading.Thread(target=_job, daemon=True, name="pyrowatch-initial").start()


def create_app(with_poll: bool = True) -> FastAPI:
    app = FastAPI(title="PyroWatch", version="1.0.0", docs_url=None, redoc_url=None)

    db.init_db()
    if with_poll and os.environ.get("PYROWATCH_NO_POLL") != "1" and not os.environ.get("VERCEL"):
        from .poller import start_background
        start_background()
        _run_initial_cycle()

    @app.get("/api/health")
    def health():
        return {"ok": True, "time": now_iso()}

    @app.get("/health")
    def health_alias():
        return {"ok": True, "time": now_iso()}

    @app.get("/api/meta")
    def meta():
        from .config import (AIRNOW_API_KEY, DAY_RANGE, DEMO_FIRMS, FEEDS,
                             FIRMS_MAP_KEY, POLL_INTERVAL_MINUTES, SCORING_HORIZON_MILES,
                             WFIGS_ENABLED)
        from .notify import _channel_config
        return {
            "config": {
                "firms_key": bool(FIRMS_MAP_KEY),
                "airnow_key": bool(AIRNOW_API_KEY),
                "demo_firms": DEMO_FIRMS,
                "feeds": FEEDS,
                "day_range": DAY_RANGE,
                "poll_interval_minutes": POLL_INTERVAL_MINUTES,
                "wfigs": WFIGS_ENABLED,
                "scoring_horizon_miles": SCORING_HORIZON_MILES,
            },
            "delivery_channels": _channel_config(),
            "freshness": {
                "cycle": db.get_meta("cycle"),
                "firms": db.get_meta("fresh_firms"),
                "weather": db.get_meta("fresh_weather"),
                "air": db.get_meta("fresh_air"),
                "wfigs": db.get_meta("fresh_wfigs"),
            },
            "time": now_iso(),
        }

    # ------------------------------------------------------------- locations
    def _resolve_coords(payload: LocationIn, keep: tuple[float, float, str] | None = None) -> tuple[float, float, str]:
        # allow "lat,lon" pasted into the address box
        raw = (payload.address or "").strip()
        if payload.lat is not None or payload.lon is not None:
            if payload.lat is None or payload.lon is None:
                raise HTTPException(400, "Both lat and lon must be provided")
            lat = float(payload.lat)
            lon = float(payload.lon)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise HTTPException(400, "Coordinates out of bounds (-90..90, -180..180)")
            return lat, lon, raw or f"{lat:.4f}, {lon:.4f} (manual)"
        parts = raw.replace(";", ",").split(",")
        if len(parts) == 2:
            try:
                lat = float(parts[0].strip())
                lon = float(parts[1].strip())
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise HTTPException(400, "Coordinates out of bounds (-90..90, -180..180)")
                return lat, lon, f"{lat:.4f}, {lon:.4f} (manual)"
            except ValueError:
                pass
        if not raw:
            if keep:  # editing: no new address given -> keep the existing point
                return keep
            raise HTTPException(400, "Provide an address or lat,lon")
        res = geocode(raw)
        lat = float(res["lat"])
        lon = float(res["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise HTTPException(400, "Coordinates out of bounds (-90..90, -180..180)")
        return lat, lon, res["matched"]

    @app.get("/api/geocode/suggest")
    def geocode_suggest(q: str = "", limit: int = 5):
        if not q or len(q.strip()) < 2:
            return []
        return suggest_addresses(q, limit=min(max(1, limit), 10))

    @app.post("/api/locations")
    def add_location(payload: LocationIn):
        try:
            lat, lon, matched = _resolve_coords(payload)
        except GeocodeError as e:
            raise HTTPException(422, str(e))
        loc_id = db.add_location(payload.name, payload.address or matched, lat, lon, payload.radius_miles)
        pipeline.run_cycle("location-added")
        return db.q("SELECT * FROM locations WHERE id=?", (loc_id,), one=True)

    @app.post("/api/locations/{loc_id}")
    def edit_location(loc_id: int, payload: LocationIn):
        row = db.q("SELECT * FROM locations WHERE id=?", (loc_id,), one=True)
        if not row:
            raise HTTPException(404, "location not found")
        fields: dict[str, Any] = {"radius_miles": payload.radius_miles, "name": payload.name}
        keep = (row["lat"], row["lon"], row.get("address") or "")
        try:
            lat, lon, matched = _resolve_coords(payload, keep=keep)
        except GeocodeError as e:
            raise HTTPException(422, str(e))
        fields.update({"lat": lat, "lon": lon, "address": matched or row.get("address")})
        db.update_location(loc_id, **fields)
        return {"ok": True}

    @app.delete("/api/locations/{loc_id}")
    def remove_location(loc_id: int):
        db.delete_location(loc_id)
        return {"ok": True}

    @app.get("/api/locations")
    def locations():
        return db.list_locations()

    # ------------------------------------------------------------- refresh
    @app.post("/api/refresh")
    def refresh():
        summary = pipeline.run_cycle("manual")
        return summary

    # ------------------------------------------------------------- dashboard
    @app.get("/api/dashboard")
    def dashboard():
        return build_dashboard()

    @app.get("/api/history/{loc_id}")
    def history(loc_id: int):
        cutoff = iso(utcnow() - timedelta(hours=24))
        rows = db.q(
            "SELECT ts_utc, MAX(score) AS score, MAX(distance_miles) AS distance_miles "
            "FROM snapshots WHERE location_id=? AND ts_utc>=? GROUP BY ts_utc ORDER BY ts_utc",
            (loc_id, cutoff))
        return rows

    @app.get("/api/alerts")
    def get_alerts(limit: int = 100):
        lim = max(1, min(500, limit))
        rows = db.q("SELECT * FROM alerts ORDER BY fired_utc DESC LIMIT ?", (lim,))
        loc_names = {l["id"]: l["name"] for l in db.list_locations()}
        for r in rows:
            r["detail"] = jload(r.get("detail"), {})
            r["sources"] = jload(r.get("sources"), [])
            r["location_name"] = loc_names.get(r.get("location_id"), "?")
        return rows

    # ------------------------------------------------------------ static web
    from .config import ROOT
    web = ROOT / "web"
    if not (web / "index.html").exists():
        web.mkdir(exist_ok=True)
        (web / "index.html").write_text("<h1>PyroWatch web files missing</h1>", encoding="utf-8")
    app.mount("/", StaticFiles(directory=str(web), html=True), name="web")

    return app


# build_dashboard -------------------------------------------------------------

_ACTIVE_H = 72.0


def build_dashboard() -> dict:
    now = utcnow()
    locations = db.list_locations()
    groups = db.load_groups()
    g_by_id = {g["id"]: g for g in groups}

    active_ids = {g["id"] for g in groups if age_hours(parse_iso(g["last_acq_utc"]), now) <= _ACTIVE_H}

    # Latest snapshot per (location, group)
    latest: dict[tuple[int, int], dict] = {}
    cutoff = iso(now - timedelta(hours=72))
    for s in db.q("SELECT * FROM snapshots WHERE ts_utc >= ? ORDER BY ts_utc", (cutoff,)):
        if s["group_id"] in active_ids:
            latest[(s["location_id"], s["group_id"])] = s
    for key in list(latest.keys()):
        s = latest[key]
        g = g_by_id.get(key[1]) or {}
        s["reasons"] = jload(s.get("reasons"), [])
        s["limited"] = jload(s.get("limited"), [])
        threat = {
            "group_id": key[1],
            "score": s["score"],
            "reasons": s["reasons"],
            "limited": s["limited"],
            "distance_miles": s["distance_miles"],
            "cached_utc": s["ts_utc"],
            "demo": bool(g.get("demo")),
            "detection_count": g.get("detection_count"),
            "confidence": g.get("confidence"),
            "frp": g.get("frp"),
            "satellites": g.get("satellites") or [],
            "first_acq_utc": g.get("first_acq_utc"),
            "last_acq_utc": g.get("last_acq_utc"),
            "wfigs": g.get("wfigs"),
            "center_lat": g.get("center_lat"),
            "center_lon": g.get("center_lon"),
        }
        latest[key] = threat

    loc_threats: dict[int, list[dict]] = {loc["id"]: [] for loc in locations}
    for (lid, gid), t in latest.items():
        if lid in loc_threats:
            loc_threats[lid].append(t)
    for tid in loc_threats:
        loc_threats[tid].sort(key=lambda t: -t["score"])

    # detections of active groups in last 24h
    det_rows = []
    if active_ids:
        marks = ",".join("?" * len(active_ids))
        cutoff = iso(now - timedelta(hours=24))
        det_rows = db.q(f"SELECT * FROM detections WHERE group_id IN ({marks}) AND acq_utc>=?",
                        tuple(sorted(active_ids)) + (cutoff,))

    glance_groups = []
    for g in groups:
        if g["id"] not in active_ids:
            continue
        glance_groups.append({
            "id": g["id"], "center_lat": g["center_lat"], "center_lon": g["center_lon"],
            "detection_count": g["detection_count"], "confidence": g["confidence"],
            "frp": g["frp"], "satellites": g["satellites"], "demo": bool(g["demo"]),
            "first_acq_utc": g["first_acq_utc"], "last_acq_utc": g["last_acq_utc"],
            "wfigs": g.get("wfigs"), "weather": _weather_view(g.get("weather")),
        })

    perimeters = []
    for p in db.q("SELECT * FROM perimeters"):
        ring = jload(p.get("ring"), [])
        if not ring:
            continue
        lat_c = sum(r[0] for r in ring) / len(ring)
        lon_c = sum(r[1] for r in ring) / len(ring)
        near = min((geo.haversine_miles(lat_c, lon_c, loc["lat"], loc["lon"]) for loc in locations),
                   default=9999)
        if near <= 120:
            perimeters.append({
                "irwin": p.get("irwin"), "name": p.get("name"), "kind": p.get("kind"),
                "acres": p.get("acres"), "contained_pct": p.get("contained_pct"),
                "ring": ring, "updated_utc": p.get("updated_utc"),
            })

    alerts = []
    for a in db.q("SELECT * FROM alerts ORDER BY fired_utc DESC LIMIT 120"):
        alerts.append({
            "id": a["id"], "location_id": a["location_id"], "group_id": a["group_id"],
            "type": a["type"], "fired_utc": a["fired_utc"],
            "score": a["score"], "score_prev": a["score_prev"],
            "distance_miles": a["distance_miles"], "confidence": a["confidence"],
            "sources": jload(a.get("sources"), []),
            "reason": a["reason"], "detail": jload(a.get("detail"), {}),
        })
    loc_names = {loc["id"]: loc["name"] for loc in locations}
    for a in alerts:
        a["location_name"] = loc_names.get(a["location_id"], "?")

    from .config import AIRNOW_API_KEY, DEMO_FIRMS, DAY_RANGE, FEEDS, FIRMS_MAP_KEY, POLL_INTERVAL_MINUTES
    from .notify import _channel_config
    return {
        "now": now_iso(),
        "demo_firms": DEMO_FIRMS,
        "config": {
            "firms_key": bool(FIRMS_MAP_KEY), "airnow_key": bool(AIRNOW_API_KEY),
            "feeds": FEEDS, "day_range": DAY_RANGE,
            "poll_interval_minutes": POLL_INTERVAL_MINUTES,
        },
        "delivery_channels": _channel_config(),
        "locations": [
            {
                **loc,
                "threats": loc_threats.get(loc["id"], []),
                "best_threat": loc_threats[loc["id"]][0] if loc_threats.get(loc["id"]) else None,
            } for loc in locations
        ],
        "groups": glance_groups,
        "detections": det_rows,
        "perimeters": perimeters,
        "alerts": alerts,
        "freshness": {
            "cycle": db.get_meta("cycle"),
            "firms": db.get_meta("fresh_firms"),
            "weather": db.get_meta("fresh_weather"),
            "air": db.get_meta("fresh_air"),
            "wfigs": db.get_meta("fresh_wfigs"),
        },
    }


def _weather_view(w: dict | None) -> dict | None:
    if not w:
        return None
    return {
        "wind_dir_deg": w.get("wind_dir_deg"),
        "wind_dir_compass": w.get("wind_dir_compass"),
        "wind_speed_mph": w.get("wind_speed_mph"),
        "rh_pct": w.get("rh_pct"),
        "forecast_update_utc": w.get("forecast_update_utc"),
        "retrieved_utc": w.get("retrieved_utc"),
        "source": w.get("source"),
    }


app = create_app()
