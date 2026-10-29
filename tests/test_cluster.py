"""Grouping of detections into fire clusters."""
import sys

sys.path.insert(0, "pyrowatch")
sys.path.insert(0, ".")

from pyrowatch import cluster
from pyrowatch.util import iso, utcnow


def _row(lat, lon, hours_ago, sat="VIIRS_SNPP", conf="n", frp=10.0, demo=0):
    return {"lat": lat, "lon": lon, "acq_utc": iso(utcnow()) if hours_ago == 0 else
            __import__("datetime").datetime.fromtimestamp(
                utcnow().timestamp() - hours_ago * 3600, tz=__import__("datetime").timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "satellite": sat, "confidence": conf, "frp": frp, "demo": demo}


def test_first_row_creates_new_group():
    groups = []
    g, is_new = cluster.assign(_row(40.0, -105.0, 1), groups)
    assert is_new and len(groups) == 1
    assert g["detection_count"] == 1


def test_close_in_time_rows_merge_into_one_group():
    groups = []
    cluster.assign(_row(40.0, -105.0, 1), groups)
    cluster.assign(_row(40.005, -104.999, 2, sat="VIIRS_NOAA20"), groups)
    assert len(groups) == 1
    g = groups[0]
    assert g["detection_count"] == 2
    assert sorted(g["satellites"]) == ["VIIRS_NOAA20", "VIIRS_SNPP"]
    assert g["confidence"] in ("n", "l", "h")


def test_far_row_starts_new_group():
    groups = []
    cluster.assign(_row(40.0, -105.0, 1), groups)
    _, is_new = cluster.assign(_row(40.5, -105.0, 1), groups)  # ~35 mi away
    assert is_new and len(groups) == 2


def test_time_gap_starts_new_group():
    groups = []
    # Detection 1 at 40h ago
    cluster.assign(_row(40.0, -105.0, 40), groups)
    # Detection 2 at 1h ago (>20h AFTER group's last detection) -> beyond GROUP_MAX_GAP_HOURS (20)
    _, is_new = cluster.assign(_row(40.0, -105.0, 1), groups)
    assert is_new and len(groups) == 2


def test_time_gap_before_group_starts_new_group():
    groups = []
    # Detection 1 at 1h ago
    cluster.assign(_row(40.0, -105.0, 1), groups)
    # Detection 2 at 40h ago (>20h BEFORE group's last detection) -> starts new group
    _, is_new = cluster.assign(_row(40.0, -105.0, 40), groups)
    assert is_new and len(groups) == 2


def test_merge_tracks_first_last_and_max_frp():
    groups = []
    cluster.assign(_row(40.0, -105.0, 3, frp=20.0), groups)
    g = cluster.assign(_row(40.0, -105.0, 1, frp=90.0), groups)[0]
    assert g["frp"] == 90.0
    assert g["detection_count"] == 2


def test_demo_flag_propagates():
    groups = []
    cluster.assign(_row(40.0, -105.0, 1), groups)
    g = cluster.assign(_row(40.0, -105.0, 2, demo=1), groups)[0]
    assert g["demo"] == 1


def test_batch_counts_new_groups_and_links_every_row():
    rows = [_row(40.0, -105.0, 1), _row(40.001, -105.0, 2),
            _row(41.0, -104.0, 1, sat="VIIRS_NOAA20")]
    out = cluster.batch(rows, [])
    assert out["created_groups"] == 2
    assert len(out["links"]) == 3
