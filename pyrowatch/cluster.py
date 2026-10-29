"""Group satellite detections that appear close together within a similar period.

One pole of the same fire produces many points (multiple satellites, repeated
overpasses). Instead of treating each as an independent fire, we cluster them
into groups: a detection joins the **nearest group whose center is within
GROUP_RADIUS_MILES and whose last detection is within GROUP_MAX_GAP_HOURS**;
otherwise it starts a new group. Group-level fields follow the spec:
center, first/last detection, count, max confidence, max FRP, satellite list.
"""
from __future__ import annotations

from . import geo
from .config import GROUP_MAX_GAP_HOURS, GROUP_RADIUS_MILES
from .util import parse_iso

CONF_RANK = {"l": 0, "n": 1, "h": 2}


def max_conf(a: str, b: str) -> str:
    return b if CONF_RANK.get(b, 1) > CONF_RANK.get(a, 1) else a


def assign(row: dict, groups: list[dict]) -> tuple[dict, bool]:
    """Place one detection row into an existing group or a new one.

    groups: mutable list of group dicts (mirrors DB rows, plus '_new' flag).
    Returns (group, is_new_group).
    """
    lat, lon, acq = row["lat"], row["lon"], row["acq_utc"]
    best, best_d = None, None
    for g in groups:
        d = geo.haversine_miles(lat, lon, g["center_lat"], g["center_lon"])
        if d > GROUP_RADIUS_MILES:
            continue
        gap_h = abs((parse_iso(acq) - parse_iso(g["last_acq_utc"])).total_seconds()) / 3600.0
        if gap_h > GROUP_MAX_GAP_HOURS:
            continue
        if best is None or d < best_d:
            best, best_d = g, d
    if best is not None:
        _merge(best, row)
        return best, False

    g = {
        "id": None,
        "center_lat": lat,
        "center_lon": lon,
        "first_acq_utc": acq,
        "last_acq_utc": acq,
        "detection_count": 1,
        "confidence": row.get("confidence") or "n",
        "frp": row.get("frp") or 0.0,
        "satellites": [row.get("satellite")],
        "demo": int(row.get("demo", 0)),
        "wfigs": None,
        "weather": None,
        "status": "active",
        "created_utc": None,
        "updated_utc": None,
        "_new": True,
    }
    groups.append(g)
    return g, True


def _merge(g: dict, row: dict) -> None:
    n = g["detection_count"]
    g["center_lat"] = round((g["center_lat"] * n + row["lat"]) / (n + 1), 6)
    g["center_lon"] = round((g["center_lon"] * n + row["lon"]) / (n + 1), 6)
    g["detection_count"] = n + 1
    if row["acq_utc"] < g["first_acq_utc"]:
        g["first_acq_utc"] = row["acq_utc"]
    if row["acq_utc"] > g["last_acq_utc"]:
        g["last_acq_utc"] = row["acq_utc"]
    g["confidence"] = max_conf(g.get("confidence") or "n", row.get("confidence") or "n")
    if row.get("frp") and (row["frp"] > (g.get("frp") or 0)):
        g["frp"] = row["frp"]
    sats = set(g.get("satellites") or [])
    sats.add(row["satellite"])
    g["satellites"] = sorted(sats)
    if row.get("demo"):
        g["demo"] = 1


def batch(rows: list[dict], groups: list[dict], now_utc=None) -> dict:
    """Ingest a batch of (already deduplicated, newly inserted) detections sorted by acq time.

    Returns {"created_groups": int, "links": [(row, group), ...]} — the caller
    uses links to bind the new detection rows to their group ids in the DB.
    """
    links: list[tuple[dict, dict]] = []
    created = 0
    for row in sorted(rows, key=lambda r: (r["acq_utc"], r["lat"])):
        g, is_new = assign(row, groups)
        if is_new:
            created += 1
        links.append((row, g))
    return {"created_groups": created, "links": links}
