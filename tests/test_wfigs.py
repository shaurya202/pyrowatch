"""WFIGS matching: perimeter containment, near-perimeter, incident-point fallback."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "pyrowatch")

from pyrowatch import wfigs


def test_largest_ring_flips_coordinates():
    geom = {"type": "Polygon", "coordinates": [
        [[-105.0, 40.0], [-104.0, 40.0], [-104.0, 40.1], [-105.0, 40.1], [-105.0, 40.0]]]}
    ring = wfigs._largest_ring(geom)
    assert ring[0] == [40.0, -105.0]        # [lat, lon] inside the app


def test_largest_ring_handles_3d_coordinates():
    # 3D points [lon, lat, elevation]
    geom = {"type": "Polygon", "coordinates": [
        [[-105.0, 40.0, 1600.0], [-104.0, 40.0, 1610.0], [-104.0, 40.1, 1620.0],
         [-105.0, 40.1, 1605.0], [-105.0, 40.0, 1600.0]]]}
    ring = wfigs._largest_ring(geom)
    assert ring is not None
    assert ring[0] == [40.0, -105.0]


def test_largest_ring_picks_most_vertices():
    tri = [[0, 0], [1, 0], [1, 1], [0, 0]]
    big = [[i * 0.01, 0] for i in range(12)] + [[0.1, 1.0], [0, 0]]
    other = [[9.9, 9.9], [9.99, 9.9], [9.99, 9.99], [9.9, 9.9]]
    # real GeoJSON shape: each polygon is [outer_ring] inside a list of polygons
    ring = wfigs._largest_ring({"type": "MultiPolygon",
                                "coordinates": [[other], [tri], [big]]})
    assert len(ring) >= 10


def _data():
    return {
        "points": [
            {"lat": 40.05, "lon": -105.05, "name": "Foo Incident", "kind": "Wildfire",
             "acres": 12.0, "contained_pct": 0.0, "irwin": "1111"},
        ],
        "perimeters": [
            {"irwin": "2222", "name": "Bar Fire", "kind": "Wildfire", "acres": 900.0,
             "contained_pct": 40.0,
             "ring": [[40.20, -105.20], [40.20, -105.00], [40.30, -105.00], [40.30, -105.20]]},
        ],
    }


def test_inside_perimeter_matches():
    inc, per = wfigs.match_group(40.25, -105.10, _data())
    assert inc["name"] == "Bar Fire"
    assert inc["matched_via"] == "perimeter"
    assert per is not None


def test_near_perimeter_matches():
    inc, _ = wfigs.match_group(40.33, -105.00, _data())    # ~2 mi above the ring edge
    assert inc and inc["matched_via"] == "perimeter"
    assert "distance_to_perimeter_miles" in inc


def test_falls_back_to_incident_point():
    inc, per = wfigs.match_group(40.06, -105.06, _data())   # ~half mile from Foo point
    assert inc["name"] == "Foo Incident"
    assert inc["matched_via"] == "incident_point"
    assert per is None


def test_no_match_returns_none_pair():
    assert wfigs.match_group(45.0, -110.0, _data()) == (None, None)
