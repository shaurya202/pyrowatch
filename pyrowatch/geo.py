"""Geodesy helpers: distances, bboxes, bearings, wind geometry, point-in-polygon."""
from __future__ import annotations

import math
import re
from typing import Iterable, Sequence

MILES_PER_DEG_LAT = 69.0

_STRDIR = re.compile(r"^\s*([NSEW]{1,3})\s*$")


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles."""
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = math.sin((rlat2 - rlat1) / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin((rlon2 - rlon1) / 2) ** 2
    return 2 * 3958.7613 * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def bbox_around(lat: float, lon: float, radius_miles: float) -> tuple[float, float, float, float]:
    """Axis-aligned bbox (west, south, east, north) roughly covering radius_miles around a point."""
    dlat = radius_miles / MILES_PER_DEG_LAT
    lat_rad = math.radians(max(-89.0, min(89.0, lat)))
    dlon = radius_miles / (MILES_PER_DEG_LAT * max(0.01, math.cos(lat_rad)))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def bbox_union(bboxes: Iterable[Sequence[float]]) -> tuple[float, float, float, float]:
    w = s = float("inf"); e = n = -float("inf")
    for west, south, east, north in bboxes:
        w = min(w, west); s = min(s, south); e = max(e, east); n = max(n, north)
    return (w, s, e, n)


def bbox_pad(bbox: Sequence[float], miles: float) -> tuple[float, float, float, float]:
    west, south, east, north = bbox
    w, s, _, _ = bbox_around(south, west, miles)
    _, _, e, n = bbox_around(north, east, miles)
    return (w, s, e, n)


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing (0=N, 90=E) from point 1 to point 2."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(rlat2)
    x = math.cos(rlat1) * math.sin(rlat2) - math.sin(rlat1) * math.cos(rlat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def bearing_between_points(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> float:
    return bearing_deg(from_lat, from_lon, to_lat, to_lon)


def angle_diff(a: float, b: float) -> float:
    """Smallest absolute angle between two bearings, 0..180."""
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def wind_toward_deg(wind_from_deg: float) -> float:
    """Wind direction is where wind COMES FROM; +180 is where it is going."""
    return (wind_from_deg + 180.0) % 360.0


def downwind_angle(wind_from_deg: float, from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> float:
    """0 = target directly downwind of source, 180 = upwind."""
    toward = wind_toward_deg(wind_from_deg)
    brg = bearing_deg(from_lat, from_lon, to_lat, to_lon)
    return angle_diff(toward, brg)


_COMPASS = [
    (0.0, "N"), (22.5, "NNE"), (45.0, "NE"), (67.5, "ENE"), (90.0, "E"), (112.5, "ESE"),
    (135.0, "SE"), (157.5, "SSE"), (180.0, "S"), (202.5, "SSW"), (225.0, "SW"),
    (247.5, "WSW"), (270.0, "W"), (292.5, "WNW"), (315.0, "NW"), (337.5, "NNW"),
]

_COMPASS_MAP = {name: deg for deg, name in _COMPASS}


def compass_from_deg(deg: float | int) -> str:
    deg = float(deg) % 360.0
    best = min(_COMPASS, key=lambda p: angle_diff(deg, p[0]))
    return best[1]


def compass_to_deg(name: str) -> float | None:
    m = _STRDIR.match(name or "")
    if not m:
        return None
    return _COMPASS_MAP.get(m.group(1).upper())


def point_in_ring(lat: float, lon: float, ring: Sequence[Sequence[float]]) -> bool:
    """Even-odd ray casting. ring = [[lat, lon], ...] (open or closed). Must contain >2 points."""
    if len(ring) < 4:
        # Not a usable polygon; fall back to tiny tolerance around first vertex.
        lat1 = float(ring[0][0]); lon1 = float(ring[0][1])
        return abs(lat - lat1) < 1e-9 and abs(lon - lon1) < 1e-9
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        lati, loni = float(ring[i][0]), float(ring[i][1])
        latj, lonj = float(ring[j][0]), float(ring[j][1])
        if (loni > lon) != (lonj > lon):
            x = (lon - lonj) / (loni - lonj) if loni != lonj else 0.0
            if lat < x * (lati - latj) + latj:
                inside = not inside
        j = i
    return inside


def ring_distance_miles(lat: float, lon: float, ring: Sequence[Sequence[float]]) -> float:
    """Approx min distance to ring vertices (good enough within a few miles)."""
    best = float("inf")
    for plat, plon in ring:
        d = haversine_miles(lat, lon, plat, plon)
        best = min(best, d) if d < best else best
    return best
