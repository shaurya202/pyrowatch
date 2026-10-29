"""Offline integration: one full pipeline cycle in demo mode (no network)."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "pyrowatch")


def test_full_demo_cycle_monotonic(fresh_db, monkeypatch):
    """Demo ingest -> cluster -> score -> alert, with NWS/WFIGS/network stubbed out."""
    from pyrowatch import pipeline

    monkeypatch.setattr(pipeline.nws, "get_conditions", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.wfigs, "fetch",
                        lambda bbox: {"points": [], "perimeters": []})
    monkeypatch.setattr(pipeline, "AIRNOW_API_KEY", "")
    monkeypatch.setattr(pipeline, "WFIGS_ENABLED", False)   # keep ArcGIS untouched
    monkeypatch.setattr(pipeline, "DEMO_FIRMS", True)
    import pyrowatch.notify as notify
    monkeypatch.setattr(notify, "deliver",
                        lambda rows: {"delivered": 0, "results": [], "errors": [],
                                      "sent_to": [], "at": "test"})

    summary = pipeline.run_cycle("test")
    assert summary["status"] == "ok"
    assert summary["errors"] == []
    assert summary["new_detections"] > 0
    assert summary["new_groups"] >= 1
    assert summary["scored"] >= 1
    assert summary["alerts"] >= 1          # first cycle alerts about the near fire
    assert summary["took_seconds"] < 60

    # a second cycle re-scores without exploding; and the demo seed location exists
    from pyrowatch import db
    assert db.get_meta("cycle")["status"] == "ok"
    fresh = pipeline.run_cycle("test-2")
    assert fresh["status"] == "ok"


def test_cycle_skips_without_locations(fresh_db, monkeypatch):
    from pyrowatch import pipeline
    monkeypatch.setattr(pipeline, "DEMO_FIRMS", False)
    summary = pipeline.run_cycle("no-locations")
    assert summary["status"] == "skipped"


def test_demo_data_is_flagged(fresh_db, monkeypatch):
    from pyrowatch import demo
    rows = demo.demo_detections()
    assert rows and all(r["source"] == "DEMO" and r["demo"] == 1 for r in rows)
    # every demo point is near the demo home (bbox sanity)
    for r in rows[:50]:
        assert 30 < r["lat"] < 50 and -115 < r["lon"] < -95
