"""Nationwide synthetic files exercise the real upload, validation, merge, and map paths."""
from __future__ import annotations

import io
import resource
import sys
import time
import zipfile

import pandas as pd
from fastapi.testclient import TestClient

from app.catalog import LOCATIONS
from app.main import app, sessions


def test_nationwide_csv_upload_merge_and_paged_analysis():
    client = TestClient(app)
    points = list({loc["region"]: loc for loc in LOCATIONS.values()}.values())
    assert len(points) == 18
    dates = pd.date_range("2016-01-01", "2025-12-31")
    assert len(dates) * len(points) == 65_754
    weather = pd.DataFrame({
        "location_id": [point["id"] for point in points for _ in dates],
        "date": list(dates.strftime("%Y-%m-%d")) * len(points),
        "model": "era5",
        "latitude": [point["latitude"] for point in points for _ in dates],
        "longitude": [point["longitude"] for point in points for _ in dates],
        "temperature_mean_c": 27.0,
        "precipitation_mm": 3.0,
        "wind_speed_max_kmh": 20.0,
        "source_note": "synthetic provenance field " + "x" * 325,
    })
    weather.loc[::101, "temperature_mean_c"] = None
    nasa = pd.DataFrame({
        "location_id": weather.location_id,
        "date": (pd.to_datetime(weather.date) + pd.Timedelta(days=1)).dt.strftime("%Y-%m-%d"),
        "latitude": weather.latitude,
        "longitude": weather.longitude,
        "nasa_solar_radiation": 4.5,
        "nasa_temperature_mean_c": 27.5,
        "source_note": "synthetic provenance field " + "y" * 325,
    })
    nasa.loc[::113, "nasa_solar_radiation"] = None
    weather_csv = weather.to_csv(index=False).encode()
    nasa_csv = nasa.to_csv(index=False).encode()
    started = time.perf_counter()
    sid = client.post("/api/session").json()["session_id"]
    header = {"X-Session-Id": sid}
    try:
        first = client.post("/api/upload/preview", headers=header, files={"file": ("nationwide-weather.csv", weather_csv, "text/csv")})
        assert first.status_code == 200, first.text
        weather_id = first.json()["upload_id"]
        assert first.json()["rows"] == 65_754
        page = client.get(f"/api/upload/{weather_id}/rows?page=2&page_size=50", headers=header)
        assert page.status_code == 200 and page.json()["total_rows"] == 65_754
        assert page.json()["rows"][0]["date"] == weather.iloc[50].date
        stored_weather = sessions[sid]["uploads"][weather_id]
        original_date = stored_weather.loc[1, "date"]
        stored_weather.loc[1, "date"] = stored_weather.loc[0, "date"]
        duplicate = client.post("/api/upload/use", headers=header, json={"upload_id": weather_id, "time_standard": "UTC", "canonical_units_confirmed": True})
        assert duplicate.status_code == 400 and "duplicate" in duplicate.text.lower()
        stored_weather.loc[1, "date"] = original_date
        original_latitude = stored_weather.loc[0, "latitude"]
        stored_weather.loc[0, "latitude"] = 0
        invalid_coordinate = client.post("/api/upload/use", headers=header, json={"upload_id": weather_id, "time_standard": "UTC", "canonical_units_confirmed": True})
        assert invalid_coordinate.status_code == 400 and "coordinates" in invalid_coordinate.text.lower()
        stored_weather.loc[0, "latitude"] = original_latitude
        used = client.post("/api/upload/use", headers=header, json={"upload_id": weather_id, "time_standard": "UTC", "canonical_units_confirmed": True})
        assert used.status_code == 200, used.text
        assert used.json()["coordinates_validated"] is True
        assert used.json()["canonical_units_confirmed"] is True
        assert len(used.json()["locations"]) == 18
        primary_seconds = time.perf_counter() - started

        second = client.post("/api/upload/preview", headers=header, files={"file": ("nationwide-nasa.csv", nasa_csv, "text/csv")})
        assert second.status_code == 200, second.text
        nasa_id = second.json()["upload_id"]
        selection = {"locations": [point["id"] for point in points], "start_date": "2016-01-01", "end_date": "2026-01-01",
                     "weather_source": "upload", "nasa_source": "upload", "nasa_upload_id": nasa_id,
                     "nasa_solar_unit": "kWh/m^2/day", "nasa_temperature_unit": "C", "nasa_time_standard": "UTC"}
        stored_nasa = sessions[sid]["uploads"][nasa_id]
        original_nasa_date = stored_nasa.loc[1, "date"]
        stored_nasa.loc[1, "date"] = stored_nasa.loc[0, "date"]
        duplicate_nasa = client.post("/api/multisource/validate", headers=header, json=selection)
        assert duplicate_nasa.status_code == 422 and "duplicate" in duplicate_nasa.text.lower()
        stored_nasa.loc[1, "date"] = original_nasa_date
        original_longitude = stored_nasa.loc[0, "longitude"]
        stored_nasa.loc[0, "longitude"] = 0
        invalid_nasa_coordinate = client.post("/api/multisource/validate", headers=header, json=selection)
        assert invalid_nasa_coordinate.status_code == 422 and "coordinates" in invalid_nasa_coordinate.text.lower()
        stored_nasa.loc[0, "longitude"] = original_longitude
        validated = client.post("/api/multisource/validate", headers=header, json=selection)
        assert validated.status_code == 200, validated.text
        assert validated.json()["report"]["matched_dates"] == 65_736
        assert validated.json()["report"]["unmatched_nasa_dates"] == 18
        assert len(validated.json()["report"]["unmatched_nasa_examples"]) == 10
        assert validated.json()["report"]["primary_missing_values"]["temperature_mean_c"] > 0
        assert validated.json()["report"]["nasa_missing_values"]["nasa_solar_radiation_kwh_m2_day"] > 0
        merged = client.post("/api/multisource/merge", headers=header, json={"validation_id": validated.json()["validation_id"]})
        assert merged.status_code == 200, merged.text
        assert merged.json()["records"] == 65_754
        assert merged.json()["merge_quality"]["unmatched_nasa_rows"] == 18
        restored = client.get("/api/session", headers=header)
        assert restored.status_code == 200
        assert restored.json()["merged_bounds"]["records"] == 65_754
        assert len(restored.json()["uploads"]) == 2
        unmatched_csv = client.get("/api/multisource/unmatched-nasa.csv", headers=header)
        assert unmatched_csv.status_code == 200
        assert len(pd.read_csv(io.BytesIO(unmatched_csv.content))) == 18
        merge_seconds = time.perf_counter() - started

        mapped = client.post("/api/analysis", headers=header, json={"chart_id": "map", "source": "session", "start_date": "2016-01-01", "end_date": "2025-12-31"})
        assert mapped.status_code == 200, mapped.text[:500]
        assert mapped.json()["supporting_count"] == 18
        assert "python_source" in mapped.json() and "computed_facts" in mapped.json()
        paired_chart = client.post("/api/analysis", headers=header, json={"chart_id": "temp_solar", "source": "session", "start_date": "2016-01-01", "end_date": "2025-12-31"})
        assert paired_chart.status_code == 200, paired_chart.text[:500]
        assert paired_chart.json()["supporting_count"] > 60_000
        assert len(paired_chart.json()["supporting_data"]) <= 30
        assert len(paired_chart.content) < 2_000_000
        for frequency, count in (("monthly", 2160), ("annual", 180)):
            response = client.post(f"/api/periods?frequency={frequency}&page=1&page_size=100", headers=header, json={"source": "session"})
            assert response.status_code == 200, response.text[:500]
            assert response.json()["total_rows"] == count
            assert len(response.json()["rows"]) == 100
            assert "solar_mean_kwh_m2_day" in response.json()["columns"]
        backup = client.get("/api/session/backup", headers=header)
        assert backup.status_code == 200
        with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
            assert {"uploaded-source-1.csv", "uploaded-source-2.csv", "manifest.json"}.issubset(archive.namelist())
        peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
        print(f"nationwide weather_bytes={len(weather_csv)} nasa_bytes={len(nasa_csv)} primary_seconds={primary_seconds:.2f} merge_seconds={merge_seconds:.2f} total_seconds={time.perf_counter()-started:.2f} peak_rss_mb={peak_mb:.0f}")
    finally:
        client.delete("/api/session", headers=header)


def test_upload_size_and_row_limits_report_clear_errors(monkeypatch):
    from app import main

    client = TestClient(app)
    sid = client.post("/api/session").json()["session_id"]
    header = {"X-Session-Id": sid}
    try:
        monkeypatch.setattr(main, "MAX_UPLOAD_MB", 1)
        monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1024 * 1024)
        too_large = client.post("/api/upload/preview", headers=header, files={"file": ("large.csv", b"x" * (1024 * 1024 + 1), "text/csv")})
        assert too_large.status_code == 413 and "1 MB" in too_large.text
        monkeypatch.setattr(main, "MAX_UPLOAD_ROWS", 2)
        too_many_rows = client.post("/api/upload/preview", headers=header, files={"file": ("rows.csv", b"date\n2020-01-01\n2020-01-02\n2020-01-03\n", "text/csv")})
        assert too_many_rows.status_code == 400 and "1–2 rows" in too_many_rows.text
    finally:
        client.delete("/api/session", headers=header)


def test_upload_has_no_application_row_cap_by_default(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 0)
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 0)
    monkeypatch.setattr(main, "MAX_UPLOAD_ROWS", 0)
    client = TestClient(app)
    sid = client.post("/api/session").json()["session_id"]
    header = {"X-Session-Id": sid}
    try:
        content = b"date,location_id,model\n" + b"2020-01-01,dumaguete,era5\n" * 100_001
        response = client.post("/api/upload/preview", headers=header, files={"file": ("many-rows.csv", content, "text/csv")})
        assert response.status_code == 200, response.text
        assert response.json()["rows"] == 100_001
    finally:
        client.delete("/api/session", headers=header)
