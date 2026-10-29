"""Geodesy: distances, bboxes, compass, wind geometry, point-in-polygon."""
import sys

sys.path.insert(0, "pyrowatch")
sys.path.insert(0, ".")

from pyrowatch.geo import (bbox_pad, bbox_union, compass_from_deg, compass_to_deg,
                           downwind_angle, haversine_miles, point_in_ring,
                           ring_distance_miles, wind_toward_deg)

BOULDER = (40.0150, -105.2705)
DENVER = (39.7392, -104.9903)


def test_haversine_boulder_denver():
    d = haversine_miles(*BOULDER, *DENVER)
    assert 23.0 < d < 26.5


def test_zero_distance():
    assert haversine_miles(*BOULDER, *BOULDER) < 0.01


def test_bbox_pad_grows():
    b = (-105.0, 40.0, -104.0, 40.5)
    wb, sb, eb, nb = bbox_pad(b, 10.0)
    assert wb < b[0] and sb < b[1] and eb > b[2] and nb > b[3]


def test_bbox_union_contains_all():
    u = bbox_union([(-1, 0, 0, 1), (-2, -0.5, 0.5, 0.5)])
    assert [round(v, 6) for v in u] == [-2, -0.5, 0.5, 1]


def test_compass_roundtrip():
    for name in ["N", "NNE", "NE", "E", "S", "W", "NW"]:
        deg = compass_to_deg(name)
        assert compass_from_deg(deg) == name


def test_wind_toward_is_source_plus_180():
    assert wind_toward_deg(0) == 180.0
    assert wind_toward_deg(270) == 90.0


def test_downwind_angle_directly_downwind():
    # wind FROM the north (0deg) is blowing south; target south of source -> 0
    src_lat, src_lon = 40.1, -105.0
    dst_lat, dst_lon = 40.0, -105.0
    assert downwind_angle(0, src_lat, src_lon, dst_lat, dst_lon) < 1.0


def test_downwind_angle_directly_upwind():
    # target north of source while wind blows south -> ~180
    assert downwind_angle(0, 40.1, -105.0, 40.2, -105.0) > 170.0


def test_point_in_ring():
    square = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    assert point_in_ring(0, 0, square)
    assert not point_in_ring(2, 2, square)


def test_ring_distance():
    square = [(40, -105), (40, -104), (41, -104), (41, -105)]
    d = ring_distance_miles(40.001, -104.999, square)
    assert d < 0.5
