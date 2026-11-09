import io
import inspect
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.charts import REGISTRY
from app.main import app, parse_upload
from app.processing import RAINY_DAY_MM, climate_summary, correlation_table, date_bounds, filter_weather, period_table, prepare_weather

client = TestClient(app)


def session():
    header = {"X-Session-Id": client.post("/api/session").json()["session_id"]}
    dates = pd.date_range("2024-01-01", "2025-12-31", freq="D")
    rows = []
    for place, offset in (("dumaguete", 0), ("cebu", 2), ("bacolod", 1), ("tagbilaran", 1), ("siquijor", 2)):
        for date in dates:
            month = date.month
            rows.append({"location_id": place, "date": date.date().isoformat(), "model": "test_fixture",
                         "temperature_mean_c": 24 + offset + month / 2 + date.day % 4,
                         "temperature_min_c": 21 + offset + month / 2,
                         "temperature_max_c": 29 + offset + month / 2,
                         "precipitation_mm": (month * 2 + date.day % 5) / 3,
                         "wind_speed_max_kmh": 10 + month + date.day % 3})
    csv = pd.DataFrame(rows).to_csv(index=False)
    preview = client.post("/api/upload/preview", headers=header, files={"file": ("fixture.csv", csv, "text/csv")})
    assert preview.status_code == 200, preview.text
    used = client.post("/api/upload/use", headers=header, json={"upload_id": preview.json()["upload_id"], "time_standard": "UTC"})
    assert used.status_code == 200, used.text
    return header


def sample():
    return prepare_weather(pd.DataFrame([
        {"location_id": "dumaguete", "date": "2024-01-01", "model": "era5", "temperature_mean_c": 26, "precipitation_mm": 1, "wind_speed_max_kmh": 10},
        {"location_id": "dumaguete", "date": "2024-01-02", "model": "era5", "temperature_mean_c": 27, "precipitation_mm": None, "wind_speed_max_kmh": 12},
        {"location_id": "dumaguete", "date": "2024-01-03", "model": "era5", "temperature_mean_c": 28, "precipitation_mm": 3, "wind_speed_max_kmh": 14},
        {"location_id": "cebu", "date": "2024-02-01", "model": "era5", "temperature_mean_c": 30, "precipitation_mm": 2, "wind_speed_max_kmh": 11},
    ]))


def test_bounds_and_invalid_ranges():
    weather = sample()
    assert date_bounds(weather) == {"min_date": "2024-01-01", "max_date": "2024-02-01", "records": 4, "locations": ["cebu", "dumaguete"]}
    assert date_bounds(weather, ["dumaguete"])["max_date"] == "2024-01-03"
    assert len(filter_weather(weather, ["dumaguete"], "2024-01-02", "2024-01-03")) == 2
    with pytest.raises(ValueError, match="Start date"):
        filter_weather(weather, [], "2024-02-01", "2024-01-01")
    with pytest.raises(ValueError, match="loaded observations"):
        filter_weather(weather, [], "2023-12-31", "2024-02-01")


def test_weather_duplicates_missing_and_units():
    weather = sample()
    duplicate = pd.concat([weather, weather.iloc[[0]]], ignore_index=True)
    clean = prepare_weather(duplicate)
    assert len(clean) == 4
    assert clean.precipitation_mm.isna().sum() == 1
    assert clean.temperature_mean_c.mean() == 27.75


def test_monthly_and_annual_rainfall_are_per_location_and_incomplete():
    weather = sample()
    monthly = period_table(weather, "monthly")
    dumaguete = monthly[monthly.location_id.eq("dumaguete")].iloc[0]
    assert dumaguete.precipitation_mm == 4
    assert dumaguete.precipitation_valid_days == 2
    assert dumaguete.expected_days == 31
    assert not dumaguete.complete_rainfall
    annual = period_table(weather, "annual")
    assert annual.loc[annual.location_id.eq("dumaguete"), "precipitation_mm"].iloc[0] == 4
    assert annual.loc[annual.location_id.eq("cebu"), "precipitation_mm"].iloc[0] == 2


def test_summary_uses_filtered_values_and_completeness():
    weather = filter_weather(sample(), ["dumaguete"], "2024-01-01", "2024-01-03")
    summary = climate_summary(weather, "2024-01-01", "2024-01-03", {}, "Uploaded Dataset")
    assert summary["mean_temperature_c"] == 27
    assert summary["total_precipitation_mm"] == 4
    assert summary["rainy_day_frequency_pct"] == 100
    assert summary["valid_temperature_pct"] == 100
    assert summary["valid_rainfall_pct"] == 66.7
    assert summary["missing_precipitation"] == 1
    assert RAINY_DAY_MM == 1.0


def test_correlation_pairs_by_location_and_date():
    paired, r = correlation_table(sample(), "precipitation_mm", "temperature_mean_c")
    assert len(paired) == 3
    assert r is not None
    assert set(paired.period) == {"2024-01-01", "2024-01-03", "2024-02-01"}
    tiny = sample().iloc[:2]
    assert correlation_table(tiny, "precipitation_mm", "temperature_mean_c")[1] is None
    constant = sample().copy()
    constant["temperature_mean_c"] = 27
    assert correlation_table(constant, "precipitation_mm", "temperature_mean_c")[1] is None


@pytest.mark.parametrize("chart_id,figure_type", [("bar","png"),("line","png"),("box","png"),("bubble","png"),("rain_wind","png"),("heatmap","png"),("mosaic","png"),("treemap","plotly"),("map","html")])
def test_every_chart_uses_climate_and_actual_builder(chart_id, figure_type):
    header = session()
    response = client.post("/api/analysis", headers=header, json={"chart_id": chart_id, "locations": ["dumaguete"], "start_date": "2024-01-01", "end_date": "2025-12-31"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["figure"]["type"] == figure_type
    assert result["python_source"]["visualization"] == inspect.getsource(REGISTRY[chart_id][4])
    assert result["computed_facts"]["limitations"] == result["limitations"]
    assert "facility" not in json.dumps(result).lower()
    assert len(result["supporting_data"]) == min(30, result["supporting_count"])
    package = client.get("/api/export/"+result["analysis_id"]+"/package", headers=header)
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert REGISTRY[chart_id][4].__name__ in archive.read("reproduce.py").decode()


def test_map_and_interpretation_change_with_dates():
    header = session()
    def analyze(end):
        response = client.post("/api/analysis", headers=header, json={"chart_id": "map", "locations": ["dumaguete"], "start_date": "2024-01-01", "end_date": end})
        assert response.status_code == 200, response.text
        return response.json()
    first, second = analyze("2024-12-31"), analyze("2025-12-31")
    assert first["filter_hash"] != second["filter_hash"]
    assert first["supporting_data"][0]["rain_valid_days"] < second["supporting_data"][0]["rain_valid_days"]
    assert first["computed_facts"]["findings"] != second["computed_facts"]["findings"]
    assert "OpenStreetMap" in second["figure"]["content"]
    wind = client.post("/api/analysis", headers=header, json={"chart_id": "map", "locations": ["dumaguete"], "start_date": "2024-01-01", "end_date": "2025-12-31", "variable": "wind_speed_max_kmh"}).json()
    assert wind["supporting_data"][0]["selected_variable"] == "wind_speed_max_kmh"
    assert "wind_speed_max_kmh" in wind["figure"]["content"]


def test_dataset_bounds_and_date_validation_through_api():
    header = session()
    info = client.post("/api/dataset", headers=header, json={"locations": ["dumaguete"]}).json()
    assert info["min_date"] == "2024-01-01"
    assert info["max_date"] == "2025-12-31"
    assert info["selected_observations"] == 731
    result = client.post("/api/dataset", headers=header, json={"locations": ["dumaguete"], "start_date": "2025-01-01", "end_date": "2025-12-31"}).json()
    assert result["selected_observations"] == 365
    invalid = client.post("/api/summary", headers=header, json={"start_date": "2023-01-01", "end_date": "2024-01-01"})
    assert invalid.status_code == 422
    reversed_dates = client.post("/api/analysis", headers=header, json={"chart_id": "line", "start_date": "2025-01-01", "end_date": "2024-01-01"})
    assert reversed_dates.status_code == 422


def test_relationship_statistics_match_evidence():
    header = session()
    response = client.post("/api/analysis", headers=header, json={"chart_id": "bubble", "locations": ["dumaguete"], "start_date": "2025-01-01", "end_date": "2025-12-31", "relationship_aggregation": "monthly"})
    assert response.status_code == 200, response.text
    result = response.json()
    table = pd.DataFrame(result["supporting_data"])
    assert len(table) == 12
    r = table.precipitation_mm.corr(table.temperature_mean_c)
    assert result["statistics"]["pearson_r"] == pytest.approx(r)
    assert f"{r:.3f}" in result["computed_facts"]["findings"]


def test_upload_is_private_weather_only_and_revalidates_dates():
    a,b = session(),session()
    csv = b"location_id,date,model,temperature_mean_c,precipitation_mm,wind_speed_max_kmh\ndumaguete,2025-01-01,era5,27,2,12\ndumaguete,2025-01-02,era5,28,3,13\n"
    preview = client.post("/api/upload/preview", headers=a, files={"file": ("weather.csv", csv, "text/csv")}).json()
    assert preview["observation_period"] == ["2025-01-01", "2025-01-02"]
    assert client.post("/api/upload/use", headers=b, json={"upload_id": preview["upload_id"], "time_standard": "UTC"}).status_code == 404
    used = client.post("/api/upload/use", headers=a, json={"upload_id": preview["upload_id"], "time_standard": "UTC"})
    assert used.status_code == 200, used.text
    assert used.json()["valid_records"] == 2
    info = client.post("/api/dataset", headers=a, json={"source":"session"}).json()
    assert [info["min_date"],info["max_date"]] == used.json()["observation_period"]
    assert client.post("/api/analysis", headers=a, json={"chart_id":"line","source":"session","start_date":"2024-01-01"}).status_code == 422
    assert client.post("/api/upload/preview",headers=a,files={"file":("bad.pkl",b"bad")}).status_code == 400
    assert client.post("/api/upload/preview",headers=a,files={"file":("osm.json",b'{"elements":[]}')}).status_code == 400
    assert client.delete("/api/session",headers=a).json()["cleared"]


def test_open_meteo_json_adapter():
    data = {"latitude": 9.31, "longitude": 123.31, "model": "era5", "daily": {"time": ["2025-01-01"], "temperature_2m_mean": [26.4], "precipitation_sum": [2.1], "wind_speed_10m_max": [18]}}
    clean = prepare_weather(parse_upload("weather.json", json.dumps(data).encode()))
    assert clean.iloc[0].location_id == "dumaguete"
    assert clean.iloc[0].wind_speed_max_kmh == 18


def test_external_weather_fetch_is_not_exposed():
    header = {"X-Session-Id": client.post("/api/session").json()["session_id"]}
    response = client.post("/api/fetch/weather", headers=header, json={"locations": ["dumaguete"]})
    assert response.status_code == 404


def test_exported_script_runs_independently(tmp_path: Path):
    header = session()
    result = client.post("/api/analysis", headers=header, json={"chart_id": "bar"}).json()
    package = client.get("/api/export/"+result["analysis_id"]+"/package", headers=header)
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        archive.extract("reproduce.py", tmp_path)
        archive.extract("chart_input.csv", tmp_path)
    completed = subprocess.run([sys.executable, "reproduce.py"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path/"chart.png").stat().st_size > 1000


def test_end_to_end_negros_date_to_chart_code_interpretation_export():
    header = session()
    assert client.get("/api/health").json()["ok"]
    info = client.post("/api/dataset", headers=header, json={"locations":["dumaguete"]}).json()
    assert "2025-01-01" >= info["min_date"]
    scope = {"locations":["dumaguete"],"start_date":"2025-01-01","end_date":"2025-12-31"}
    summary = client.post("/api/summary", headers=header, json=scope).json()
    assert summary["valid_observations"] > 0
    chart = client.post("/api/analysis", headers=header, json={**scope,"chart_id":"bubble"}).json()
    assert chart["figure"]["type"] == "png"
    assert "sns.scatterplot" in chart["python_source"]["visualization"]
    assert chart["computed_facts"]["label"] == "Statistical Interpretation"
    assert chart["statistics"]["paired_observations"] == chart["supporting_count"]
    exported = client.get("/api/export/"+chart["analysis_id"]+"/package", headers=header)
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"


def test_summary_code_and_reproduction_package(tmp_path: Path):
    header = session()
    scope = {"locations": ["dumaguete"], "start_date": "2025-01-01", "end_date": "2025-12-31"}
    shown = client.post("/api/summary/code", headers=header, json=scope)
    assert shown.status_code == 200
    assert shown.json()["source"] == inspect.getsource(climate_summary)
    package = client.post("/api/summary/package", headers=header, json=scope)
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        archive.extractall(tmp_path)
    completed = subprocess.run([sys.executable, "reproduce_summary.py"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    reproduced = json.loads(completed.stdout)
    assert reproduced["mean_temperature_c"] == shown.json()["summary"]["mean_temperature_c"]
    assert reproduced["total_precipitation_mm"] == shown.json()["summary"]["total_precipitation_mm"]


def test_no_bundled_dataset_and_climate_only():
    header = {"X-Session-Id": client.post("/api/session").json()["session_id"]}
    assert client.post("/api/dataset", headers=header, json={}).status_code == 422
    assert client.get("/api/sources").json()["weather"]["records"] == 0
    catalog = client.get("/api/catalog").json()
    assert all(chart["needs"] == "weather" for chart in catalog["charts"] if chart["chapter"] in {"Chapter 08", "Chapter 09"})
    assert any(chart["needs"] == "merged" for chart in catalog["charts"])
    assert "facilities" not in client.get("/api/sources").text.lower()
