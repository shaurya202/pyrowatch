"""AirNow: AQI<->PM2.5 conversions and the observation client (offline)."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "pyrowatch")

import pytest

from pyrowatch import airnow

GOOD_JSON = [
    {"ParameterName": "OZONE", "AQI": 42},
    {"ParameterName": "PM2.5", "AQI": 113, "Value": 39.9, "DateObserved": "2026-10-21",
     "HourObserved": 15, "LocalTimeZone": "MST", "Category": {"Name": "Unhealthy for Sensitive Groups"},
     "ReportingArea": "Boulder", "StateCode": "CO"},
]


class FakeResp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_pm25_to_aqi_boundaries():
    assert airnow.pm25_to_aqi(0) == 0
    assert airnow.pm25_to_aqi(12.0) == 50
    assert airnow.pm25_to_aqi(35.4) == 100
    assert airnow.pm25_to_aqi(55.4) == 150
    assert airnow.pm25_to_aqi(300.0) >= 250
    assert airnow.pm25_to_aqi(None) is None


def test_aqi_to_pm25_boundaries():
    assert airnow.aqi_to_pm25(0) == 0
    assert airnow.aqi_to_pm25(50) == 12.0
    assert airnow.aqi_to_pm25(100) == 35.4
    assert airnow.aqi_to_pm25(None) is None


def test_conversion_roundtrip_reasonably_close():
    for pm25 in (5, 20, 45, 90):
        back = airnow.aqi_to_pm25(airnow.pm25_to_aqi(pm25))
        assert abs(back - pm25) <= 1.5


def test_no_key_means_none(monkeypatch):
    monkeypatch.setattr(airnow, "AIRNOW_API_KEY", "")
    assert airnow.current_pm25(40, -105) is None


def test_current_pm25_parses_response(monkeypatch):
    monkeypatch.setattr(airnow, "AIRNOW_API_KEY", "K")
    monkeypatch.setattr(airnow.requests, "get", lambda *a, **k: FakeResp(GOOD_JSON))
    out = airnow.current_pm25(40.0, -105.0)
    assert out["pm25"] == 39.9
    assert out["aqi"] == 113
    assert out["category"] == "Unhealthy for Sensitive Groups"
    assert out["monitor_area"] == "Boulder"
    assert "retrieved_utc" in out


def test_error_response_raises(monkeypatch):
    monkeypatch.setattr(airnow, "AIRNOW_API_KEY", "K")
    monkeypatch.setattr(airnow.requests, "get",
                        lambda *a, **k: FakeResp({"ReportError": "No data"}))
    with pytest.raises(airnow.AirNowError):
        airnow.current_pm25(40.0, -105.0)


def test_falls_back_to_aqi_when_no_concentration(monkeypatch):
    rows = [dict(GOOD_JSON[1])]
    rows[0].pop("Value")
    monkeypatch.setattr(airnow, "AIRNOW_API_KEY", "K")
    monkeypatch.setattr(airnow.requests, "get", lambda *a, **k: FakeResp(rows))
    out = airnow.current_pm25(40.0, -105.0)
    assert abs(out["pm25"] - airnow.aqi_to_pm25(113)) < 0.05   # back-converted from AQI


def test_new_ziplatlong_format_parses(monkeypatch):
    new_json = [
        {
            "dateObserved": "2026-10-28",
            "hourObserved": "15:00",
            "localTimeZone": "MDT",
            "reportingAreaName": "Denver-Boulder",
            "siteID": "080131001",
            "siteName": "Boulder - CU - 2102 Athens St.",
            "parameterName": "PM2.5",
            "nowcastAQI": 18,
            "aqiCategoryName": "Good",
            "reportingAgency": "Colorado Department of Public Health and Environment",
            "lookupBehavior": "Closest Reading By Pollutant",
            "consideredMonitors": "All",
            "lookupBoundary": "50 Miles",
        }
    ]
    monkeypatch.setattr(airnow, "AIRNOW_API_KEY", "K")
    monkeypatch.setattr(airnow.requests, "get", lambda *a, **k: FakeResp(new_json))
    out = airnow.current_pm25(40.0, -105.0)
    assert out["aqi"] == 18
    assert abs(out["pm25"] - airnow.aqi_to_pm25(18)) < 0.05
    assert out["category"] == "Good"
    assert out["monitor_area"] == "Denver-Boulder"
    assert out["observed_local"] == "2026-10-28 15:00 MDT"
    assert out["site_name"] == "Boulder - CU - 2102 Athens St."


def test_new_service_no_observations_returns_none(monkeypatch):
    err_json = {
        "WebServiceError": [
            {"Message": "There are no observations available for the requested latitude/longitude: No observations were found for all monitors within 50 miles."}
        ]
    }
    monkeypatch.setattr(airnow, "AIRNOW_API_KEY", "K")
    monkeypatch.setattr(airnow.requests, "get", lambda *a, **k: FakeResp(err_json))
    assert airnow.current_pm25(40.0, -105.0) is None


def test_new_service_hard_error_raises(monkeypatch):
    err_json = {
        "WebServiceError": [
            {"Message": "Invalid API key"}
        ]
    }
    monkeypatch.setattr(airnow, "AIRNOW_API_KEY", "K")
    monkeypatch.setattr(airnow.requests, "get", lambda *a, **k: FakeResp(err_json))
    with pytest.raises(airnow.AirNowError, match="Invalid API key"):
        airnow.current_pm25(40.0, -105.0)

