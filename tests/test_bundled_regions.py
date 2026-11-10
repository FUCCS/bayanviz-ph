"""The supplied 18-region archives must work as a selectable dataset and as uploads."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.bundled import DATA, load_bundle, summary
from app.main import app


def test_bundled_archive_selects_all_regions_and_keeps_chart_features():
    bundle = load_bundle()
    assert summary()["weather_rows"] == summary()["nasa_rows"] == 65754
    assert bundle.quality["matched_rows"] == 65754
    assert bundle.quality["unmatched_nasa_rows"] == 0
    client = TestClient(app)
    token = client.post("/api/session").json()["session_id"]
    headers = {"X-Session-Id": token}
    try:
        selected = client.post("/api/bundled/select", headers=headers)
        assert selected.status_code == 200, selected.text[:300]
        assert selected.json()["records"] == 65754
        assert len(selected.json()["locations"]) == 18
        snapshot = client.get("/api/session", headers=headers).json()
        assert snapshot["active_source"] == "bundled"
        assert snapshot["bundled_bounds"]["records"] == 65754
        assert client.post("/api/session/source", headers=headers, json={"source": "bundled"}).status_code == 200
        dataset = client.post("/api/dataset", headers=headers, json={"source": "bundled"})
        assert dataset.status_code == 200
        assert len(dataset.json()["available_locations"]) == 18
        periods = client.post("/api/periods?frequency=annual&page_size=10", headers=headers, json={"source": "bundled"})
        assert periods.status_code == 200
        assert periods.json()["total_rows"] == 180
        assert len(periods.json()["rows"]) == 10
        scope = {"source": "bundled", "start_date": "2025-01-01", "end_date": "2025-01-31"}
        map_result = client.post("/api/analysis", headers=headers, json={**scope, "chart_id": "map"})
        assert map_result.status_code == 200, map_result.text[:300]
        assert map_result.json()["supporting_count"] == 18
        assert map_result.json()["python_source"]["visualization"]
        assert map_result.json()["computed_facts"]
        solar_map = client.post("/api/analysis", headers=headers, json={**scope, "chart_id": "solar_map"})
        assert solar_map.status_code == 200, solar_map.text[:300]
        assert solar_map.json()["supporting_count"] == 18
        validated = client.post("/api/multisource/validate", headers=headers, json={
            "locations": ["ph_nir_01", "ph_r07_01"], "start_date": "2025-01-01", "end_date": "2025-01-31",
            "weather_source": "bundled", "nasa_source": "bundled", "method": "merge",
            "weather_variables": ["temperature_mean_c", "precipitation_mm"],
        })
        assert validated.status_code == 200, validated.text[:300]
        assert validated.json()["report"]["matched_dates"] == 62
        merged = client.post("/api/multisource/merge", headers=headers,
                             json={"validation_id": validated.json()["validation_id"]})
        assert merged.status_code == 200
        assert merged.json()["records"] == 62
    finally:
        client.delete("/api/session", headers=headers)


def test_full_size_regional_csv_uploads_validate_and_merge():
    client = TestClient(app)
    token = client.post("/api/session").json()["session_id"]
    headers = {"X-Session-Id": token}
    try:
        weather_file = DATA / "regional_open_meteo.csv"
        preview = client.post("/api/upload/preview", headers=headers,
                              files={"file": (weather_file.name, weather_file.read_bytes(), "text/csv")})
        assert preview.status_code == 200, preview.text[:300]
        assert preview.json()["rows"] == 65754
        assert preview.json()["columns"][0] == "location_id"
        used = client.post("/api/upload/use", headers=headers, json={"upload_id": preview.json()["upload_id"],
                              "time_standard": "UTC", "canonical_units_confirmed": True})
        assert used.status_code == 200, used.text[:300]
        assert len(used.json()["locations"]) == 18

        nasa_file = DATA / "regional_nasa_power.csv"
        nasa = client.post("/api/upload/preview", headers=headers,
                           files={"file": (nasa_file.name, nasa_file.read_bytes(), "text/csv")})
        assert nasa.status_code == 200, nasa.text[:300]
        assert nasa.json()["rows"] == 65754
        validated = client.post("/api/multisource/validate", headers=headers, json={
            "locations": summary()["locations"], "start_date": "2016-01-01", "end_date": "2025-12-31",
            "weather_source": "upload", "nasa_source": "upload", "nasa_upload_id": nasa.json()["upload_id"],
            "weather_variables": ["temperature_mean_c", "precipitation_mm"],
            "nasa_solar_unit": "kW-hr/m^2/day", "nasa_temperature_unit": "C",
            "nasa_humidity_unit": "%", "nasa_wind_unit": "m/s", "nasa_time_standard": "UTC",
        })
        assert validated.status_code == 200, validated.text[:300]
        assert validated.json()["report"]["matched_dates"] == 65754
        merged = client.post("/api/multisource/merge", headers=headers,
                             json={"validation_id": validated.json()["validation_id"]})
        assert merged.status_code == 200, merged.text[:300]
        assert merged.json()["merge_quality"]["unmatched_nasa_rows"] == 0
        assert merged.json()["records"] == 65754
    finally:
        client.delete("/api/session", headers=headers)
