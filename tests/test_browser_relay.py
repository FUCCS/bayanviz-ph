"""The browser fallback must validate complete real-provider-shaped responses."""
from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from app.catalog import LOCATIONS
from app.main import app


def payload_for(location_id: str) -> dict:
    point = LOCATIONS[location_id]
    return {
        "latitude": point["latitude"], "longitude": point["longitude"], "utc_offset_seconds": 0,
        "daily": {"time": ["2025-01-01", "2025-01-02"], "temperature_2m_mean": [26.0, 27.0], "precipitation_sum": [2.0, 3.0]},
        "daily_units": {"temperature_2m_mean": "°C", "precipitation_sum": "mm"},
    }


def test_browser_relay_validates_and_loads_two_locations(monkeypatch):
    client = TestClient(app)
    token = client.post("/api/session").json()["session_id"]
    headers = {"X-Session-Id": token}
    body = {
        "locations": ["dumaguete", "bacolod"], "start_date": "2025-01-01", "end_date": "2025-01-02",
        "variables": ["temperature_mean_c", "precipitation_mm"],
        "chunks": [{"location_id": key, "start_date": "2025-01-01", "end_date": "2025-01-02", "payload": payload_for(key)} for key in ("dumaguete", "bacolod")],
    }
    try:
        incomplete = deepcopy(body)
        incomplete["chunks"].pop()
        assert client.post("/api/fetch/weather/browser", headers=headers, json=incomplete).status_code == 400
        wrong_units = deepcopy(body)
        wrong_units["chunks"][0]["payload"]["daily_units"]["temperature_2m_mean"] = "°F"
        assert client.post("/api/fetch/weather/browser", headers=headers, json=wrong_units).status_code == 422
        wrong_grid = deepcopy(body)
        wrong_grid["chunks"][0]["payload"]["latitude"] = 0
        assert client.post("/api/fetch/weather/browser", headers=headers, json=wrong_grid).status_code == 422
        missing_date = deepcopy(body)
        missing_date["chunks"][0]["payload"]["daily"]["time"].pop()
        assert client.post("/api/fetch/weather/browser", headers=headers, json=missing_date).status_code == 422
        result = client.post("/api/fetch/weather/browser", headers=headers, json=body)
        assert result.status_code == 200, result.text
        assert result.json()["records"] == 4
        assert result.json()["status"] == "Browser API relay"
        dataset = client.post("/api/dataset", headers=headers, json={"source": "api"})
        assert dataset.status_code == 200
        assert dataset.json()["available_locations"] == ["bacolod", "dumaguete"]
        summary = client.post("/api/summary", headers=headers, json={"source": "api"})
        assert summary.status_code == 200
        chart = client.post("/api/analysis", headers=headers, json={"source": "api", "chart_id": "map"})
        assert chart.status_code == 200, chart.text[:300]
        assert chart.json()["supporting_count"] == 2
        async def blocked_provider(*args, **kwargs):
            raise AssertionError("Multi-Source Manager should reuse the validated API dataset")
        monkeypatch.setattr("app.main.fetch_weather", blocked_provider)
        validated = client.post("/api/multisource/validate", headers=headers, json={
            "locations": ["dumaguete", "bacolod"], "start_date": "2025-01-01", "end_date": "2025-01-02",
            "weather_variables": ["temperature_mean_c", "precipitation_mm"],
            "weather_source": "api", "include_nasa": False, "method": "merge",
        })
        assert validated.status_code == 200, validated.text
        assert validated.json()["report"]["cache_hits"]["openmeteo"] == 2
    finally:
        client.delete("/api/session", headers=headers)
