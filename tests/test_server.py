"""HTTP API (FastAPI TestClient): health, locations CRUD, dashboard, history."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "pyrowatch")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(fresh_db, monkeypatch):
    """App instance against the isolated DB with external sources stubbed offline."""
    from pyrowatch import server

    monkeypatch.setattr("pyrowatch.pipeline.nws.get_conditions", lambda *a, **k: None)
    monkeypatch.setattr("pyrowatch.pipeline.wfigs.fetch",
                        lambda bbox: {"points": [], "perimeters": []})
    monkeypatch.setattr("pyrowatch.pipeline.AIRNOW_API_KEY", "")
    monkeypatch.setattr("pyrowatch.pipeline.WFIGS_ENABLED", False)
    monkeypatch.setattr("pyrowatch.pipeline.DEMO_FIRMS", True)
    monkeypatch.setattr("pyrowatch.config.DEMO_FIRMS", True)
    import pyrowatch.notify as notify
    monkeypatch.setattr(notify, "deliver",
                        lambda rows: {"delivered": 0, "results": [], "errors": [],
                                      "sent_to": [], "at": "test"})
    return TestClient(server.create_app(with_poll=False))


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    r2 = client.get("/health")
    assert r2.status_code == 200 and r2.json()["ok"] is True


def test_add_list_delete_location_with_coords(client):
    r = client.post("/api/locations",
                    json={"name": "Cabin", "lat": 40.5, "lon": -105.0, "radius_miles": 10})
    assert r.status_code == 200
    loc = r.json()
    assert loc["id"] == 1 and loc["lat"] == 40.5

    rows = client.get("/api/locations").json()
    assert [x["name"] for x in rows] == ["Cabin"]

    assert client.delete("/api/locations/1").json() == {"ok": True}
    assert client.get("/api/locations").json() == []


def test_edit_without_address_keeps_point_regression(client):
    """Editing must keep coordinates when the address field is left empty."""
    client.post("/api/locations",
                json={"name": "Home", "lat": 40.015, "lon": -105.2705, "radius_miles": 15,
                      "address": "Boulder, CO (original)"})
    r = client.post("/api/locations/1", json={"name": "Home", "radius_miles": 25})
    assert r.status_code == 200 and r.json() == {"ok": True}
    loc = client.get("/api/locations").json()[0]
    assert loc["lat"] == 40.015 and loc["lon"] == -105.2705
    assert loc["radius_miles"] == 25
    assert loc["address"] == "Boulder, CO (original)"


def test_edit_renames_only(client):
    client.post("/api/locations",
                json={"name": "Old", "lat": 39.0, "lon": -104.0, "radius_miles": 15})
    r = client.post("/api/locations/1", json={"name": "New name", "radius_miles": 5})
    assert r.status_code == 200
    loc = client.get("/api/locations").json()[0]
    assert loc["name"] == "New name"
    assert loc["lat"] == 39.0


def test_edit_missing_location_404(client):
    assert client.post("/api/locations/99",
                       json={"name": "x", "radius_miles": 5}).status_code == 404


def test_add_location_needs_something(client):
    r = client.post("/api/locations", json={"name": "Bare", "radius_miles": 5})
    assert r.status_code in (400, 422)


def test_add_location_invalid_coords_bounds_400(client):
    # lat > 90
    r1 = client.post("/api/locations", json={"name": "BadLat1", "lat": 95.0, "lon": -105.0})
    assert r1.status_code == 400
    # lat < -90
    r2 = client.post("/api/locations", json={"name": "BadLat2", "lat": -95.0, "lon": -105.0})
    assert r2.status_code == 400
    # lon > 180
    r3 = client.post("/api/locations", json={"name": "BadLon1", "lat": 40.0, "lon": 190.0})
    assert r3.status_code == 400
    # lon < -180
    r4 = client.post("/api/locations", json={"name": "BadLon2", "lat": 40.0, "lon": -190.0})
    assert r4.status_code == 400
    # lat, lon via address string
    r5 = client.post("/api/locations", json={"name": "BadAddr", "address": "95.0, -105.0"})
    assert r5.status_code == 400


def test_refresh_then_dashboard(client):
    r = client.post("/api/refresh")
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"
    assert client.get("/api/dashboard").json()["locations"] == []

    client.post("/api/locations", json={"name": "My place", "lat": 47.6062, "lon": -122.3321})

    d = client.get("/api/dashboard").json()
    assert [loc["name"] for loc in d["locations"]] == ["My place"]
    assert d["groups"], "demo fires should exist"
    assert d["freshness"]["firms"]["ok"] is True
    threats = d["locations"][0]["threats"]
    assert threats and threats[0]["demo"] is True
    assert "limited" in threats[0] and "reasons" in threats[0]


def test_dashboard_field_contract(client):
    client.post("/api/locations", json={"name": "My place", "lat": 47.6062, "lon": -122.3321})
    d = client.get("/api/dashboard").json()
    for g in d["groups"]:
        assert set(g) >= {"id", "center_lat", "center_lon", "detection_count",
                          "confidence", "satellites", "demo", "first_acq_utc",
                          "last_acq_utc", "weather"}
    alerts = d["alerts"]
    assert alerts and set(alerts[0]) >= {"type", "fired_utc", "location_name",
                                         "reason", "sources"}


def test_geocode_suggest(client, monkeypatch):
    """Test the /api/geocode/suggest endpoint returns suggested addresses."""
    # Test short query returns empty
    r = client.get("/api/geocode/suggest?q=a")
    assert r.status_code == 200
    assert r.json() == []

    # Mock suggest_addresses to avoid real network calls in test
    fake = [
        {"address": "1600 Amphitheatre Pkwy, Mountain View, CA", "lat": 37.422, "lon": -122.084, "name": "Googleplex"}
    ]
    monkeypatch.setattr("pyrowatch.server.suggest_addresses", lambda q, limit=5: fake)
    r2 = client.get("/api/geocode/suggest?q=Amphitheatre")
    assert r2.status_code == 200
    assert len(r2.json()) == 1
    assert r2.json()[0]["name"] == "Googleplex"


def test_history_endpoint_shape(client):
    client.post("/api/locations", json={"name": "My place", "lat": 47.6062, "lon": -122.3321})
    rows = client.get("/api/history/1").json()
    assert isinstance(rows, list)
    if rows:
        assert {"ts_utc", "score", "distance_miles"} <= set(rows[0])


def test_alerts_endpoint(client):
    client.post("/api/locations", json={"name": "My place", "lat": 47.6062, "lon": -122.3321})
    rows = client.get("/api/alerts?limit=10").json()
    assert rows and rows[0]["type"] in ("new_group", "new_satellite", "score_increase")
    assert "location_name" in rows[0] or rows[0].get("location_id")


def test_meta_endpoint(client):
    m = client.get("/api/meta").json()
    assert m["config"]["demo_firms"] is True
    cycle = m["freshness"].get("cycle")
    assert cycle is None or cycle.get("status") in ("ok", "skipped", "busy")
