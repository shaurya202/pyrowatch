"""Pipeline: one refresh cycle. Runs automatically every POLL_INTERVAL_MINUTES.

Steps:
  1. fetch VIIRS detections (FIRMS, or DEMO when no MAP_KEY)
  2. de-duplicate + group them into fire groups
  3. WFIGS official incidents + perimeters
  4. NWS weather at each group center (closest-in-time to last detection)
  5. AirNow (or DEMO) air quality at each location
  6. score every (location, group) pair within the horizon
  7. alerts
  8. freshness bookkeeping + pruning
"""
from __future__ import annotations

import threading
import time as _time

from . import airnow, alerts, cluster, db, demo, firms, geo, nws, score, wfigs
from .config import AIRNOW_API_KEY, DEMO_AQI, DEMO_FIRMS, FEEDS, WFIGS_ENABLED
from .util import age_hours, iso, now_iso, parse_iso, utcnow

LOCK = threading.Lock()
_WFIGS_CACHE: dict = {"expires": 0, "data": None}
_AIR_CACHE: dict = {"expires": 0, "by_loc": {}}


def _bbox_all(locations: list[dict], margin: float = 10.0) -> tuple:
    bboxes = [geo.bbox_around(l["lat"], l["lon"], max(30.0, l["radius_miles"] or 15)) for l in locations]
    return geo.bbox_pad(geo.bbox_union(bboxes), margin)


def run_cycle(reason: str = "auto") -> dict:
    """Block while one refresh cycle runs. Returns a summary dict."""
    if not LOCK.acquire(blocking=False):
        return {"status": "busy"}
    try:
        return _run(reason)
    finally:
        LOCK.release()


def _run(reason: str) -> dict:
    started = utcnow()
    summary = {"status": "running", "started": iso(started), "reason": reason,
               "new_detections": 0, "new_groups": 0, "alerts": 0,
               "scored": 0, "errors": []}

    locations = db.list_locations()
    if not locations:
        summary.update(status="skipped", finished=now_iso(), message="no locations saved")
        db.set_meta("cycle", summary)
        return summary

    demo_now = DEMO_FIRMS
    if not demo_now and db.q("SELECT 1 FROM detections WHERE demo=1 LIMIT 1"):
        db.x("DELETE FROM detections WHERE demo=1")
        db.x("DELETE FROM groups WHERE demo=1")

    # ------------------------------------------------------------------ 1
    big_bbox = _bbox_all(locations, margin=15.0)
    if demo_now:
        rows = demo.demo_detections(locations[0]["lat"], locations[0]["lon"], now=started)
        db.set_meta("fresh_firms", {"ok": True, "at": now_iso(), "demo": True,
                                    "rows": len(rows), "error": None})
    else:
        try:
            rows = firms.fetch_all(big_bbox, feeds=FEEDS)
            db.set_meta("fresh_firms", {"ok": True, "at": now_iso(), "demo": False,
                                        "rows": len(rows), "error": None})
        except Exception as e:
            rows = []
            db.set_meta("fresh_firms", {"ok": False, "at": now_iso(), "demo": False,
                                        "rows": 0, "error": str(e)})
            summary["errors"].append(f"firms: {e}")
    for r in rows:
        r.setdefault("fetched_utc", now_iso())

    # ------------------------------------------------------------------ 2
    new_rows = db.upsert_detections(rows)
    summary["new_detections"] = len(new_rows)
    groups = db.load_groups()
    links = cluster.batch(new_rows, groups, now_utc=started)
    summary["new_groups"] = links.get("created_groups", 0)
    db.save_groups_and_link(groups, links.get("links", []))

    fresh_gids = [g["id"] for g in groups if age_hours(parse_iso(g["last_acq_utc"]), ref=started) <= 72.0]
    fresh_groups = {g["id"]: g for g in groups if g["id"] in set(fresh_gids)}

    # ------------------------------------------------------------------ 3
    if WFIGS_ENABLED:
        try:
            if _WFIGS_CACHE["expires"] <= _time.time() or _WFIGS_CACHE["data"] is None:
                _WFIGS_CACHE["data"] = wfigs.fetch(big_bbox)
                _WFIGS_CACHE["expires"] = _time.time() + 600
            data = _WFIGS_CACHE["data"]
            perim_rows = [{
                "lat_group": 0.0, "lon_group": 0.0,
                "irwin": p.get("irwin") or p.get("name") or "?",
                "name": p.get("name"), "kind": p.get("kind"),
                "acres": p.get("acres"), "contained_pct": p.get("contained_pct"),
                "ring": p["ring"], "updated_utc": p.get("updated"),
            } for p in data.get("perimeters", [])]
            db.save_perimeters(perim_rows)
            for g in fresh_groups.values():
                inc, _per = wfigs.match_group(g["center_lat"], g["center_lon"], data)
                if inc:
                    g["wfigs"] = inc
            db.set_meta("fresh_wfigs", {"ok": True, "at": now_iso(),
                                        "points": len(data.get("points") or []),
                                        "perimeters": len(data.get("perimeters") or []),
                                        "error": None})
        except Exception as e:
            db.set_meta("fresh_wfigs", {"ok": False, "at": now_iso(), "error": str(e)})
            summary["errors"].append(f"wfigs: {e}")
            data = {"points": [], "perimeters": []}
    else:
        data = {"points": [], "perimeters": []}

    # ------------------------------------------------------------------ 4
    weather_ok = 0
    weather_err = None
    for g in fresh_groups.values():
        try:
            w = nws.get_conditions(g["center_lat"], g["center_lon"], g["last_acq_utc"])
            if w:
                g["weather"] = w
                weather_ok += 1
        except Exception as e:
            weather_err = str(e)
            g["weather"] = g.get("weather")  # keep last good snapshot
        # persist weather below via save_groups_and_link (single write).
    db.save_groups_and_link(groups, [])
    if weather_ok:
        db.set_meta("fresh_weather", {"ok": True, "at": now_iso(), "groups": weather_ok,
                                      "error": weather_err})
    elif weather_err:
        db.set_meta("fresh_weather", {"ok": False, "at": now_iso(), "error": weather_err})

    # ------------------------------------------------------------------ 5
    aqi_ok = 0
    aqi_err = None
    for loc in locations:
        reading = None
        if AIRNOW_API_KEY:
            try:
                cache_key = (loc["id"], round(loc["lat"], 2), round(loc["lon"], 2))
                by_loc = _AIR_CACHE.setdefault("by_loc", {})
                timing = by_loc.get(cache_key)
                if timing and timing[0] > _time.time():
                    reading = timing[1]
                else:
                    reading = airnow.current_pm25(loc["lat"], loc["lon"])
                    if reading:
                        by_loc[cache_key] = (_time.time() + 1500, reading)
            except Exception as e:
                aqi_err = str(e)
                summary["errors"].append(f"airnow loc#{loc['id']}: {e}")
        elif DEMO_AQI and demo_now:
            reading = demo.demo_pm25(loc["lat"], loc["lon"], now=started)
        if reading:
            db.update_location(loc["id"], aqi=reading)
            aqi_ok += 1
    if aqi_ok:
        db.set_meta("fresh_air", {"ok": True, "at": now_iso(), "locations": aqi_ok,
                                  "error": aqi_err})
    elif aqi_err:
        db.set_meta("fresh_air", {"ok": False, "at": now_iso(), "error": aqi_err})

    # ------------------------------------------------------------------ 6
    group_ids = list(fresh_groups.keys())
    dets_by_group = db.detections_for_groups(group_ids)
    aqi_by_loc = {l["id"]: l.get("aqi") for l in db.list_locations()}

    loc_threats: dict[int, list[dict]] = {}
    snap_rows: list[dict] = []
    for loc in locations:
        threats = []
        for gid, g in fresh_groups.items():
            s = score.score_one(loc, g, dets_by_group.get(gid) or [], g.get("weather"),
                                aqi_by_loc.get(loc["id"]), started)
            if s.get("skipped"):
                continue
            t = {
                "group_id": gid,
                "score": s["score"],
                "category": s["category"],
                "reasons": s["reasons"],
                "limited": s["limited"],
                "distance_miles": s["distance_miles"],
                "age_hours": s["age_hours"],
                "demo": bool(g.get("demo")),
                "detection_count": g["detection_count"],
                "confidence": g["confidence"],
                "frp": g["frp"],
                "satellites": list(g["satellites"]),
                "first_acq_utc": g["first_acq_utc"],
                "last_acq_utc": g["last_acq_utc"],
                "wfigs": g.get("wfigs"),
                "center_lat": g["center_lat"],
                "center_lon": g["center_lon"],
            }
            threats.append(t)
            snap_rows.append({
                "location_id": loc["id"], "group_id": gid, "ts_utc": now_iso(),
                "score": s["score"], "reasons": s["reasons"], "limited": s["limited"],
                "distance_miles": s["distance_miles"],
            })
        threats.sort(key=lambda t: -t["score"])
        loc_threats[loc["id"]] = threats
        summary["scored"] += len(threats)
    if snap_rows:
        db.insert_snapshots(snap_rows)

    # ------------------------------------------------------------------ 7
    alert_rows: list[dict] = []
    alert_locations: dict[int, dict] = {}
    for loc in locations:
        fired_rows, state_updates = alerts.evaluate_location(loc, loc_threats.get(loc["id"], []))
        alert_rows.extend(fired_rows)
        for st in state_updates:
            db.save_group_state(st)
        if fired_rows:
            alert_locations[loc["id"]] = loc
    for a in alert_rows:
        a["location_name"] = (alert_locations.get(a["location_id"]) or {}).get("name")
    db.insert_alerts(alert_rows)
    try:
        from . import notify
        summary["notified"] = notify.deliver(alert_rows)
    except Exception as e:  # noqa: BLE001 — delivery must never kill the cycle
        summary.setdefault("errors", []).append(f"notify: {e}")
    summary["alerts"] = len(alert_rows)
    summary["alert_reasons"] = [a["reason"] for a in alert_rows]

    # ------------------------------------------------------------------ 8
    try:
        db.prune()
    except Exception:
        pass
    summary["finished"] = now_iso()
    summary["took_seconds"] = round((utcnow() - started).total_seconds(), 1)
    summary["status"] = "ok"
    db.set_meta("cycle", summary)
    return summary
