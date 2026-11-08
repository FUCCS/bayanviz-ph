import io
import inspect
import json
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.charts import REGISTRY, prepare_chart
from app.main import app, parse_upload
from app.processing import monthly_rainfall, prepare_facilities, prepare_weather

client = TestClient(app)


def new_session():
    return {"X-Session-Id": client.post("/api/session").json()["session_id"]}


def test_weather_dedup_missing_and_incomplete_month():
    df = pd.DataFrame([
        {"location_id":"x","date":"2024-02-01","model":"era5","precipitation_mm":3},
        {"location_id":"x","date":"2024-02-01","model":"era5","precipitation_mm":4},
        {"location_id":"x","date":"2024-02-02","model":"era5","precipitation_mm":None},
    ])
    clean = prepare_weather(df)
    assert len(clean) == 2
    monthly = monthly_rainfall(clean).iloc[0]
    assert monthly.total_mm == 4
    assert monthly.valid_days == 1
    assert monthly.expected_days == 29
    assert not monthly.complete


def test_facility_identity_and_category():
    df = pd.DataFrame([
        {"osm_type":"node","osm_id":1,"category":"bank","latitude":9.3,"longitude":123.3,"province":"Negros Oriental","region":"NIR"},
        {"osm_type":"node","osm_id":1,"category":"bank","latitude":9.3,"longitude":123.3,"province":"Negros Oriental","region":"NIR"},
        {"osm_type":"node","osm_id":1,"category":"atm","latitude":9.3,"longitude":123.3,"province":"Negros Oriental","region":"NIR"},
    ])
    clean = prepare_facilities(df)
    assert len(clean) == 1
    assert set(clean.category) == {"bank"}


def test_suspected_duplicates_are_flagged_but_preserved():
    rows = [
        {"osm_type": "node", "osm_id": 1, "name": "Central Bank", "category": "bank", "latitude": 9.3068, "longitude": 123.3054, "province": "Negros Oriental", "region": "NIR"},
        {"osm_type": "way", "osm_id": 2, "name": "Central Bank", "category": "bank", "latitude": 9.3069, "longitude": 123.3053, "province": "Negros Oriental", "region": "NIR"},
    ]
    clean = prepare_facilities(pd.DataFrame(rows))
    assert len(clean) == 2
    assert clean.suspected_duplicate.all()


@pytest.mark.parametrize("chart_id,figure_type", [("bar","png"),("line","png"),("box","png"),("bubble","png"),("heatmap","png"),("mosaic","png"),("treemap","plotly"),("map","html")])
def test_all_charts_use_actual_python_source(chart_id, figure_type):
    header = new_session()
    response = client.post("/api/analysis", headers=header, json={"chart_id":chart_id,"start_date":"2024-01-01","end_date":"2025-12-31"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["figure"]["type"] == figure_type
    assert result["python_source"]["visualization"] == inspect.getsource(REGISTRY[chart_id][4])
    assert len(result["supporting_data"]) == min(30, result["supporting_count"])
    assert result["computed_facts"]["limitations"] == result["limitations"]
    package = client.get(f"/api/export/{result['analysis_id']}/package", headers=header)
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as z:
        assert "reproduce.py" in z.namelist()
        assert REGISTRY[chart_id][4].__name__ in z.read("reproduce.py").decode()


def test_two_sessions_cannot_read_each_others_result_or_upload():
    a,b = new_session(),new_session()
    result = client.post("/api/analysis", headers=a, json={"chart_id":"line"}).json()
    assert client.get(f"/api/analysis/{result['analysis_id']}", headers=b).status_code == 404
    csv = b"location_id,date,model,temperature_mean_c\ndumaguete,2025-01-01,era5,27.0\n"
    preview = client.post("/api/upload/preview", headers=a, files={"file":("weather.csv",csv,"text/csv")}).json()
    assert client.post("/api/upload/use", headers=b, json={"upload_id":preview["upload_id"],"kind":"weather"}).status_code == 404
    assert client.post("/api/upload/use", headers=a, json={"upload_id":preview["upload_id"],"kind":"weather"}).status_code == 200
    assert client.delete("/api/session", headers=a).json()["cleared"]
    assert client.get(f"/api/analysis/{result['analysis_id']}", headers=a).status_code == 401


def test_bad_upload_type_and_unknown_chart():
    header = new_session()
    assert client.post("/api/upload/preview",headers=header,files={"file":("bad.pkl",b"not data")}).status_code == 400
    assert client.post("/api/analysis",headers=header,json={"chart_id":"wrong"}).status_code == 404


def test_reproduction_package_runs_independently(tmp_path: Path):
    header = new_session()
    result = client.post("/api/analysis", headers=header, json={"chart_id": "bar"}).json()
    package = client.get(f"/api/export/{result['analysis_id']}/package", headers=header)
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        archive.extract("reproduce.py", tmp_path)
        archive.extract("chart_input.csv", tmp_path)
    completed = subprocess.run([sys.executable, "reproduce.py"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "chart.png").stat().st_size > 1000
    script = client.get(f"/api/export/{result['analysis_id']}/script", headers=header)
    assert script.status_code == 200
    assert script.text == (tmp_path / "reproduce.py").read_text()


def test_supported_provider_json_is_normalized():
    weather = {"latitude": 9.31, "longitude": 123.31, "model": "era5", "daily": {"time": ["2025-01-01"], "temperature_2m_mean": [26.4], "precipitation_sum": [2.1]}}
    clean_weather = prepare_weather(parse_upload("weather.json", json.dumps(weather).encode()))
    assert clean_weather.iloc[0].location_id == "dumaguete"
    assert clean_weather.iloc[0].temperature_mean_c == 26.4

    osm = {"elements": [{"type": "node", "id": 17, "lat": 9.31, "lon": 123.31, "tags": {"shop": "chemist", "name": "Example"}}]}
    clean_osm = prepare_facilities(parse_upload("osm.json", json.dumps(osm).encode()))
    assert len(clean_osm) == 1
    assert clean_osm.iloc[0].category == "pharmacy"
    assert clean_osm.iloc[0].province == "Negros Oriental"


def test_variable_and_aggregation_filters_change_evidence_and_interpretation():
    header = new_session()
    monthly = client.post("/api/analysis", headers=header, json={"chart_id": "line", "variable": "precipitation_mm", "aggregation": "monthly", "locations": ["dumaguete"]})
    assert monthly.status_code == 200, monthly.text
    result = monthly.json()
    assert result["units"] == "mm"
    assert result["supporting_count"] == 24
    assert all(row["variable"] == "precipitation_mm" for row in result["supporting_data"])
    leader = max(result["supporting_data"], key=lambda row: row["measure_value"])
    assert f"{leader['measure_value']:.1f} mm" in result["computed_facts"]["findings"]
    assert client.post("/api/analysis", headers=header, json={"chart_id": "box", "variable": "temperature_max_c"}).json()["units"] == "°C"
    assert client.post("/api/analysis", headers=header, json={"chart_id": "line", "variable": "invalid"}).status_code == 422


def test_mixed_session_sources_are_labeled_per_chart():
    header = new_session()
    csv = b"location_id,date,model,temperature_mean_c,precipitation_mm\ndumaguete,2025-01-01,era5,27.0,2.0\n"
    preview = client.post("/api/upload/preview", headers=header, files={"file": ("weather.csv", csv, "text/csv")}).json()
    used = client.post("/api/upload/use", headers=header, json={"upload_id": preview["upload_id"], "kind": "weather"})
    assert used.status_code == 200
    line = client.post("/api/analysis", headers=header, json={"chart_id": "line", "source": "session"}).json()
    bar = client.post("/api/analysis", headers=header, json={"chart_id": "bar", "source": "session"}).json()
    assert line["sources"][0] == "Uploaded Dataset"
    assert bar["sources"][0] == "Prepared Real Dataset"
