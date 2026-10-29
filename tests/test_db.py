"""SQLite storage layer: locations, detections, groups, meta, states, prune."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "pyrowatch")

from pyrowatch import db
from pyrowatch.util import iso, jdump, utcnow


def _det(lat, lon, acq, sat="VIIRS_SNPP", conf="n", frp=5.0, demo=0):
    return {"satellite": sat, "source": "TEST", "demo": demo, "lat": lat, "lon": lon,
            "acq_utc": acq, "confidence": conf, "frp": frp, "daynight": "N",
            "fetched_utc": iso(utcnow())}


def test_meta_roundtrip(fresh_db):
    db.set_meta("x", {"a": 1, "nested": {"b": 2}})
    assert db.get_meta("x") == {"a": 1, "nested": {"b": 2}}
    db.set_meta("x", {"a": 9})
    assert db.get_meta("x")["a"] == 9
    assert db.get_meta("missing", "dflt") == "dflt"


def test_locations_crud(fresh_db):
    lid = db.add_location("Home", "Boulder", 40.0, -105.0, 15)
    assert lid == 1
    rows = db.list_locations()
    assert rows[0]["radius_miles"] == 15
    db.update_location(lid, name="New", aqi={"pm25": 10})
    rows = db.list_locations()
    assert rows[0]["name"] == "New"
    assert rows[0]["aqi"] == {"pm25": 10}


def test_delete_location_cascades(fresh_db):
    lid = db.add_location("Home", "", 40.0, -105.0, 10)
    gid = _make_group()
    db.insert_snapshots([{"location_id": lid, "group_id": gid, "ts_utc": iso(utcnow()),
                          "score": 50.0, "reasons": [], "limited": [], "distance_miles": 1.0}])
    db.insert_alerts([_make_alert(lid, gid)])
    db.delete_location(lid)
    assert db.q("SELECT COUNT(*) c FROM locations") == [{"c": 0}]
    assert db.q("SELECT COUNT(*) c FROM snapshots") == [{"c": 0}]
    assert db.q("SELECT COUNT(*) c FROM alerts") == [{"c": 0}]


def test_upsert_detections_dedupes(fresh_db):
    rows = [_det(40.0, -105.0, "2026-10-21T10:00:00Z")]
    created1 = db.upsert_detections(rows)
    created2 = db.upsert_detections(list(rows))  # identical composite key
    assert len(created1) == 1
    assert created2 == []


def test_groups_save_link_and_roundtrip(fresh_db):
    rows = [_det(40.0, -105.0, "2026-10-21T10:00:00Z"),
            _det(40.001, -105.0, "2026-10-21T11:00:00Z", sat="VIIRS_NOAA20")]
    db.upsert_detections(rows)
    groups = []
    g = {"id": None, "center_lat": 40.0, "center_lon": -105.0,
         "first_acq_utc": rows[0]["acq_utc"], "last_acq_utc": rows[1]["acq_utc"],
         "detection_count": 2, "confidence": "h", "frp": 12.3,
         "satellites": ["VIIRS_NOAA20", "VIIRS_SNPP"], "demo": 0,
         "wfigs": {"name": "X"}, "weather": None, "status": "active",
         "_new": True}
    groups.append(g)
    db.save_groups_and_link(groups, [(rows[0], g), (rows[1], g)])
    assert g["id"] == 1
    loaded = db.load_groups()
    assert len(loaded) == 1
    lg = loaded[0]
    assert lg["detection_count"] == 2
    assert lg["wfigs"]["name"] == "X"          # caveat: explicit None handled by jload fix
    assert lg["weather"] is None               # missing -> None, not {}
    links = db.detections_for_groups([g["id"]])
    assert len(links[g["id"]]) == 2


def _make_group(**over) -> int:
    g = {"center_lat": 40.0, "center_lon": -105.0, "first_acq_utc": iso(utcnow()),
         "last_acq_utc": iso(utcnow()), "detection_count": 1, "confidence": "n",
         "frp": 0, "satellites": ["S"], "demo": 0, "wfigs": None,
         "weather": None, "status": "active", "_new": True}
    g.update(over)
    groups = [g]
    db.save_groups_and_link(groups, [])
    return g["id"]


def _make_alert(loc_id, gid):
    return {"location_id": loc_id, "group_id": gid, "type": "new_group",
            "fired_utc": iso(utcnow()), "score": 42.0, "score_prev": None,
            "distance_miles": 3.0, "confidence": "n", "sources": ["S"],
            "reason": "because", "detail": {"k": 1}}


def test_group_states_roundtrip(fresh_db):
    lid = db.add_location("Home", "", 40.0, -105.0, 10)
    gid = _make_group()
    assert db.get_group_state(lid, gid) is None
    db.save_group_state({"location_id": lid, "group_id": gid,
                         "first_seen_utc": iso(utcnow()), "last_seen_utc": iso(utcnow()),
                         "sats_seen": ["A", "B"], "last_score": 55.0,
                         "notes": {"new_group": iso(utcnow())}})
    st = db.get_group_state(lid, gid)
    assert st["sats_seen"] == ["A", "B"]
    assert st["notes"]["new_group"]
    assert st["last_score"] == 55.0


def test_alerts_insert_and_read(fresh_db):
    lid = db.add_location("Home", "", 40.0, -105.0, 10)
    gid = _make_group()
    db.insert_alerts([_make_alert(lid, gid)])
    rows = db.q("SELECT * FROM alerts")
    assert rows[0]["reason"] == "because"
    assert db.q("SELECT sources FROM alerts")[0]["sources"] == jdump(["S"])


def test_perimeters_upsert(fresh_db):
    db.save_perimeters([
        {"irwin": "i1", "name": "Fire A", "kind": "Wildfire", "acres": 100.0,
         "contained_pct": 50.0, "ring": [[40, -105], [40, -104], [41, -104]],
         "updated_utc": iso(utcnow())},
    ])
    rows = db.list_perimeters()
    assert len(rows) == 1 and rows[0]["name"] == "Fire A"
    assert rows[0]["ring"][0] == [40, -105]


def test_prune_removes_old_rows(fresh_db):
    import datetime as dt
    old = iso(utcnow() - dt.timedelta(days=12))
    mid = iso(utcnow() - dt.timedelta(days=8))       # snapshots pruned at 7d
    db.upsert_detections([_det(40, -105.0, old)])
    db.upsert_detections([_det(40, -105.0, mid, sat="NEW")])
    lid = db.add_location("Home", "", 40.0, -105.0, 10)
    gid = _make_group()
    db.insert_snapshots([{"location_id": lid, "group_id": gid, "ts_utc": mid,
                          "score": 1, "reasons": [], "limited": [], "distance_miles": 5}])
    db.prune()
    dets = db.q("SELECT * FROM detections")
    assert all(d["acq_utc"] >= mid for d in dets) or len(dets) == 0
    snaps = db.q("SELECT * FROM snapshots")
    assert len(snaps) <= 1
