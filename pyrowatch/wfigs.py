"""NIFC WFIGS client — current wildland-fire incident points and perimeters.

Points:        https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0
Perimeters:    https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0
No key required; refreshed from IRWIN every 5 minutes upstream.
"""
from __future__ import annotations

import json
from typing import Any

import requests

from . import geo
from .config import WFIGS_PERIMETERS_URL, WFIGS_POINTS_URL


def _query(url: str, bbox: tuple[float, float, float, float], with_geometry: bool, limit: int = 800) -> list[dict]:
    west, south, east, north = bbox
    params = {
        "where": "1=1",
        "geometry": json.dumps({"xmin": west, "ymin": south, "xmax": east, "ymax": north,
                                "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "outSR": 4326,
        "returnGeometry": "true" if with_geometry else "false",
        "resultRecordCount": limit,
        "resultOffset": 0,
        "f": "geojson",
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    return (data.get("features") or [])


def _find(props: dict, *substrings: str, default=None) -> Any:
    low = {k.lower(): v for k, v in props.items()}
    for sub in substrings:
        for key, v in low.items():
            if sub in key and v not in (None, ""):
                return v
    return default


_KNOWN_KIND = {"WF": "Wildfire", "RX": "Prescribed", "CX": "Complex", "SH": "Other"}


def _decimate(ring: list[list[float]], max_pts: int = 400) -> list[list[float]]:
    if len(ring) <= max_pts:
        return ring
    step = max(1, len(ring) // max_pts)
    return ring[::step] + [ring[-1]]


def _largest_ring(geom: dict) -> list[list[float]] | None:
    """Take outer ring with most vertices (proxy for largest area)."""
    polys: list[list[list[list[float]]]] = []
    if geom.get("type") == "Polygon":
        polys = [geom.get("coordinates") or []]
    elif geom.get("type") == "MultiPolygon":
        polys = geom.get("coordinates") or []
    best = None
    for poly in polys:
        if not poly:
            continue
        ring = poly[0]
        if best is None or len(ring) > len(best):
            best = ring
    if best is None or len(best) < 4:
        return None
    out = [[float(pt[1]), float(pt[0])] for pt in best if len(pt) >= 2]  # geojson is [lon,lat]
    return _decimate(out)


def fetch(bbox: tuple[float, float, float, float]) -> dict:
    """Fetch incidents + perimeters intersecting bbox. Returns {'points': [...], 'perimeters': [...]}."""
    pts: list[dict] = []
    for feat in _query(WFIGS_POINTS_URL, bbox, with_geometry=False):
        props = feat.get("properties") or {}
        lat = _find(props, "initiallatitude")
        lon = _find(props, "initiallongitude")
        if lat is None or lon is None:
            continue
        cat = str(_find(props, "incidenttypecategory", default="") or "")
        pts.append({
            "lat": float(lat),
            "lon": float(lon),
            "name": _find(props, "incidentname", default=None),
            "kind": _KNOWN_KIND.get(cat.upper(), "Wildland fire" if cat else "Unknown"),
            "type_code": cat or None,
            "acres": _to_f(_find(props, "incidentsize", "discoveryacres")),
            "contained_pct": _to_f(_find(props, "percentcontained")),
            "discovered": _find(props, "firediscoverydatetime"),
            "updated": _find(props, "modifiedondatetime_dt"),
            "containment_dt": _find(props, "containmentdatetime"),
            "irwin": _find(props, "irwinid"),
            "ufid": _find(props, "uniquefireidentifier"),
            "county": _find(props, "poocounty"),
            "state": _find(props, "poostate"),
        })

    perims: list[dict] = []
    for feat in _query(WFIGS_PERIMETERS_URL, bbox, with_geometry=True):
        props = feat.get("properties") or {}
        key = _find(props, "irwinid")
        ring = _largest_ring(feat.get("geometry") or {})
        if ring is None:
            continue
        cat = str(_find(props, "incidenttypecategory", default="") or "")
        perims.append({
            "ring": ring,
            "name": _find(props, "incidentname", default=None),
            "kind": _KNOWN_KIND.get(cat.upper(), "Wildfire" if cat else "Unknown"),
            "type_code": cat or None,
            "acres": _to_f(_find(props, "acres")),
            "contained_pct": _to_f(_find(props, "percentcontained")),
            "irwin": key,
            "updated": _find(props, "modifiedondatetime_dt"),
        })
    return {"points": pts, "perimeters": perims}


def _to_f(v) -> float | None:
    try:
        return float(v)
    except Exception:
        return None


def match_group(group_lat: float, group_lon: float, data: dict,
                near_miles: float = 8.0, perimeter_near_miles: float = 3.0) -> tuple[dict | None, dict | None]:
    """Match a detection group to an official WFIGS incident.

    Returns (incident, perimeter) - either may be None.
    Prefers being inside/near a real perimeter; falls back to nearest incident point.
    """
    per = None
    # nearest perimeter by ring distance or containment
    best_d = float("inf")
    for p in data.get("perimeters", []):
        if geo.point_in_ring(group_lat, group_lon, p["ring"]):
            return ({**_incident_view(p, source="inside official perimeter"), "matched_via": "perimeter"},
                    p)
        d = geo.ring_distance_miles(group_lat, group_lon, p["ring"])
        if d < best_d:
            best_d, per = d, p
    if per and best_d <= perimeter_near_miles:
        incident = _incident_view(per, source="near official perimeter")
        incident["matched_via"] = "perimeter"
        incident["distance_to_perimeter_miles"] = round(best_d, 2)
        return incident, per

    # fall back to nearest incident location point
    best_d = float("inf")
    inc = None
    for p in data.get("points", []):
        d = geo.haversine_miles(group_lat, group_lon, p["lat"], p["lon"])
        if d < best_d:
            best_d, inc = d, p
    if inc and best_d <= near_miles:
        incident = _incident_view(inc, source="matched to official incident point")
        incident["matched_via"] = "incident_point"
        incident["distance_to_point_miles"] = round(best_d, 2)
        return incident, None
    return None, None


def _incident_view(p: dict, source: str) -> dict:
    out = {k: p.get(k) for k in ("name", "kind", "type_code", "acres", "contained_pct",
                                 "discovered", "updated", "irwin", "county", "state") if p.get(k) is not None}
    out["source"] = source
    return out
