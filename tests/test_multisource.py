"""Synthetic provider fixtures exercise the integration without claiming live observations."""
import io
import json
import zipfile

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.connectors import parse_nasa_payload
from app.main import app
from app.multisource import merge_sources, normalize_nasa, prepare_multisource_chart, validate_sources
from app.processing import prepare_weather

client = TestClient(app)


def seed_weather(header, frame):
    csv = frame.drop(columns=["analysis_time_standard", "openmeteo_grid_latitude", "openmeteo_grid_longitude"], errors="ignore").to_csv(index=False)
    preview = client.post("/api/upload/preview", headers=header, files={"file": ("weather.csv", csv, "text/csv")})
    assert preview.status_code == 200, preview.text
    used = client.post("/api/upload/use", headers=header, json={"upload_id": preview.json()["upload_id"], "time_standard": "UTC"})
    assert used.status_code == 200, used.text


def provider_frames():
    weather = prepare_weather(pd.DataFrame({
        "location_id": ["dumaguete"] * 4,
        "date": ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"],
        "model": ["era5"] * 4,
        "analysis_time_standard": ["UTC"] * 4,
        "openmeteo_grid_latitude": [9.25] * 4,
        "openmeteo_grid_longitude": [123.25] * 4,
        "temperature_mean_c": [25, 26, 27, 28],
        "precipitation_mm": [3, 2, 1, 4],
        "wind_speed_max_kmh": [12, 14, 16, 18],
    }))
    raw = pd.DataFrame({
        "location_id": ["dumaguete"] * 4,
        "date": ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-05"],
        "nasa_solar_radiation": [4, 5, 6, -999],
        "nasa_temperature_mean_c": [24, 25, 26, 27],
    })
    metadata = {"dumaguete": {"time_standard": "UTC", "dataset_version": "synthetic-test", "grid_latitude": 9.5, "grid_longitude": 123.5, "parameter_metadata": {"ALLSKY_SFC_SW_DWN": {"units": "kW-hr/m^2/day"}, "T2M": {"units": "C"}}}}
    nasa, transform = normalize_nasa(raw, metadata)
    return weather, nasa, metadata, transform


def test_nasa_parser_preserves_metadata_and_missing_values():
    payload = {"header": {"api_version": "test", "fill_value": -999}, "geometry": {"coordinates": [123.5, 9.5]}, "parameters": {"ALLSKY_SFC_SW_DWN": {"units": "kW-hr/m^2/day"}, "T2M": {"units": "C"}}, "properties": {"parameter": {"ALLSKY_SFC_SW_DWN": {"20200101": 4.5, "20200102": -999}, "T2M": {"20200101": 26, "20200102": 27}}}}
    frame, meta = parse_nasa_payload(payload, "dumaguete", ("solar_radiation", "nasa_temperature_mean_c"))
    assert len(frame) == 2
    assert pd.isna(frame.loc[1, "nasa_solar_radiation"])
    assert meta["grid_longitude"] == 123.5
    assert meta["parameter_metadata"]["T2M"]["units"] == "C"
    with pytest.raises(ValueError, match="omitted T2M"):
        parse_nasa_payload({**payload, "properties": {"parameter": {"ALLSKY_SFC_SW_DWN": {"20200101": 4}}}}, "dumaguete", ("solar_radiation", "nasa_temperature_mean_c"))


def test_validation_merge_coverage_and_time_alignment():
    weather, nasa, metadata, transform = provider_frames()
    assert transform["conversion_factor"] == 1
    report = validate_sources(weather, nasa, metadata, ["dumaguete"])
    assert report["status"] == "Partially Matched"
    assert report["matched_dates"] == 3
    assert report["common_date_range"] == ["2020-01-01", "2020-01-03"]
    merged, quality = merge_sources(weather, nasa, report)
    assert len(merged) == len(weather)
    assert quality["paired_solar_rows"] == 3
    assert quality["paired_coverage_pct"] == 75
    assert pd.isna(merged.loc[3, "nasa_solar_radiation_kwh_m2_day"])
    assert merged.loc[3, "merge_status"] == "primary_only"
    assert merged.loc[0, "temperature_mean_c"] == 25
    with pytest.raises(ValueError, match="duplicate"):
        validate_sources(weather, pd.concat([nasa, nasa.iloc[[0]]]), metadata, ["dumaguete"])
    with pytest.raises(ValueError, match="UTC"):
        validate_sources(weather.assign(analysis_time_standard="Asia/Manila"), nasa, metadata, ["dumaguete"])
    with pytest.raises(ValueError, match="no overlapping"):
        validate_sources(weather, nasa.assign(date=nasa.date + pd.Timedelta(days=100)), metadata, ["dumaguete"])


def test_unit_conversion_and_unsupported_units():
    _, nasa, metadata, _ = provider_frames()
    raw = nasa[["location_id", "date", "nasa_solar_radiation", "nasa_temperature_mean_c"]].copy()
    metadata["dumaguete"]["parameter_metadata"]["ALLSKY_SFC_SW_DWN"]["units"] = "MJ/m^2/day"
    converted, info = normalize_nasa(raw, metadata)
    assert converted.loc[0, "nasa_solar_radiation_kwh_m2_day"] == pytest.approx(4 / 3.6)
    assert info["original_solar_unit"] == "MJ/m^2/day"
    metadata["dumaguete"]["parameter_metadata"]["ALLSKY_SFC_SW_DWN"]["units"] = "unknown"
    with pytest.raises(ValueError, match="Unsupported"):
        normalize_nasa(raw, metadata)


def test_filtered_statistics_and_constant_series():
    weather, nasa, metadata, _ = provider_frames()
    merged, _ = merge_sources(weather, nasa, validate_sources(weather, nasa, metadata, ["dumaguete"]))
    subset = merged[merged.date.le(pd.Timestamp("2020-01-02"))]
    table, _, facts, _ = prepare_multisource_chart("temp_solar", subset)
    assert len(table) == facts["paired_observations"] == 2
    assert facts["pearson_r"] is None
    table, _, facts, _ = prepare_multisource_chart("temp_solar", merged)
    assert facts["pearson_r"] == pytest.approx(1)
    constant = merged.copy()
    constant["temperature_mean_c"] = 26
    assert prepare_multisource_chart("temp_solar", constant)[2]["pearson_r"] is None
    compare = prepare_multisource_chart("temp_compare", merged)[2]
    assert compare["mean_difference_c"] == 1
    assert compare["root_mean_squared_error_c"] == 1


def test_api_validate_then_merge_chart_code_and_export(monkeypatch):
    weather, nasa, metadata, _ = provider_frames()
    raw_nasa = nasa[["location_id", "date", "nasa_solar_radiation", "nasa_temperature_mean_c"]]

    sid = client.post("/api/session").json()["session_id"]
    header = {"X-Session-Id": sid}
    seed_weather(header, weather)
    solar_csv = raw_nasa.to_csv(index=False)
    solar_preview = client.post("/api/upload/preview", headers=header, files={"file": ("solar.csv", solar_csv, "text/csv")}).json()
    upload_fields = {"nasa_upload_id": solar_preview["upload_id"], "nasa_mapping": {column: column for column in raw_nasa.columns}, "nasa_solar_unit": "kWh/m^2/day", "nasa_temperature_unit": "C", "nasa_time_standard": "UTC"}
    early = client.post("/api/multisource/merge", headers=header, json={"validation_id": "unknown"})
    assert early.status_code == 409
    validated = client.post("/api/multisource/validate", headers=header, json={"locations": ["dumaguete"], "start_date": "2020-01-01", "end_date": "2020-01-04", **upload_fields})
    assert validated.status_code == 200, validated.text
    body = validated.json()
    assert body["report"]["matched_dates"] == 3
    assert body["primary_preview"] and body["nasa_preview"]
    merged = client.post("/api/multisource/merge", headers=header, json={"validation_id": body["validation_id"]})
    assert merged.status_code == 200, merged.text
    assert merged.json()["merge_quality"]["paired_coverage_pct"] == 75
    chart = client.post("/api/analysis", headers=header, json={"chart_id": "temp_solar", "source": "session", "locations": ["dumaguete"], "start_date": "2020-01-01", "end_date": "2020-01-03"})
    assert chart.status_code == 200, chart.text
    result = chart.json()
    assert result["figure"]["type"] == "png"
    assert "sns.scatterplot" in result["python_source"]["visualization"]
    assert "pd.merge(" in result["python_source"]["preparation"]
    assert "3 valid paired" in result["computed_facts"]["findings"]
    package = client.get("/api/export/" + result["analysis_id"] + "/package", headers=header)
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert {"openmeteo_input.csv", "nasa_input.csv", "source_manifest.json", "chart_input.csv", "reproduce.py"}.issubset(archive.namelist())
    for chart_id in ("rain_solar", "temp_compare", "solar_seasonal", "multi_correlation", "solar_map"):
        response = client.post("/api/analysis", headers=header, json={"chart_id": chart_id, "source": "session", "locations": ["dumaguete"], "start_date": "2020-01-01", "end_date": "2020-01-03"})
        assert response.status_code == 200, (chart_id, response.text)
        assert response.json()["figure"]["type"] == ("html" if chart_id == "solar_map" else "png")
        assert response.json()["computed_facts"]["limitations"] == response.json()["limitations"]
    assert client.get("/api/multisource/report.csv", headers=header).status_code == 200


def test_existing_openmeteo_analysis_still_works_without_nasa():
    sid = client.post("/api/session").json()["session_id"]
    header = {"X-Session-Id": sid}
    assert client.post("/api/analysis", headers=header, json={"chart_id": "map"}).status_code == 422
    assert client.post("/api/analysis", headers=header, json={"chart_id": "temp_solar", "source": "session"}).status_code == 422


def test_uploaded_nasa_json_keeps_provenance_and_merges(monkeypatch):
    weather, _, _, _ = provider_frames()

    sid = client.post("/api/session").json()["session_id"]
    header = {"X-Session-Id": sid}
    seed_weather(header, weather)
    payload = {"header": {"api": {"version": "uploaded-fixture"}, "time_standard": "UTC", "fill_value": -999}, "geometry": {"coordinates": [123.308, 9.306]}, "parameters": {"ALLSKY_SFC_SW_DWN": {"units": "kW-hr/m^2/day"}, "T2M": {"units": "C"}}, "properties": {"parameter": {"ALLSKY_SFC_SW_DWN": {"20200101": 4, "20200102": 5, "20200103": 6}, "T2M": {"20200101": 24, "20200102": 25, "20200103": 26}}}}
    upload = client.post("/api/upload/preview", headers=header, files={"file": ("power.json", json.dumps(payload), "application/json")})
    assert upload.status_code == 200, upload.text
    assert upload.json()["detected_nasa"]["time_standard"] == "UTC"
    validated = client.post("/api/multisource/validate", headers=header, json={"locations": ["dumaguete"], "start_date": "2020-01-01", "end_date": "2020-01-04", "nasa_upload_id": upload.json()["upload_id"], "nasa_mapping": {"location_id": "location_id", "date": "date", "nasa_solar_radiation": "nasa_solar_radiation", "nasa_temperature_mean_c": "nasa_temperature_mean_c"}})
    assert validated.status_code == 200, validated.text
    assert validated.json()["report"]["secondary_origin"] == "user-upload"
    assert any("not authenticated" in warning for warning in validated.json()["report"]["warnings"])
    merged = client.post("/api/multisource/merge", headers=header, json={"validation_id": validated.json()["validation_id"]})
    assert merged.status_code == 200
    assert merged.json()["merge_quality"]["paired_solar_rows"] == 3


def test_uploaded_tabular_secondary_requires_units_utc_and_mapping(monkeypatch):
    weather, _, _, _ = provider_frames()

    sid = client.post("/api/session").json()["session_id"]
    header = {"X-Session-Id": sid}
    seed_weather(header, weather)
    csv = "site,day,solar,temp\ndumaguete,2020-01-01,4,24\ndumaguete,2020-01-02,5,25\n"
    upload = client.post("/api/upload/preview", headers=header, files={"file": ("nasa.csv", csv, "text/csv")}).json()
    base = {"locations": ["dumaguete"], "start_date": "2020-01-01", "end_date": "2020-01-04", "nasa_upload_id": upload["upload_id"], "nasa_mapping": {"location_id": "site", "date": "day", "nasa_solar_radiation": "solar", "nasa_temperature_mean_c": "temp"}}
    assert client.post("/api/multisource/validate", headers=header, json=base).status_code == 422
    assert client.post("/api/multisource/validate", headers=header, json={**base, "nasa_solar_unit": "MJ/m^2/day", "nasa_temperature_unit": "C", "nasa_time_standard": "LST"}).status_code == 422
    good = client.post("/api/multisource/validate", headers=header, json={**base, "nasa_solar_unit": "MJ/m^2/day", "nasa_temperature_unit": "C", "nasa_time_standard": "UTC"})
    assert good.status_code == 200, good.text
    assert good.json()["report"]["unit_transformation"]["conversion_factor"] == pytest.approx(1 / 3.6)


@pytest.mark.parametrize("weather_source,nasa_source", [("api", "api"), ("api", "upload"), ("upload", "api")])
def test_api_and_upload_source_combinations(monkeypatch, weather_source, nasa_source):
    weather, nasa, metadata, _ = provider_frames()
    raw_nasa = nasa[["location_id", "date", "nasa_solar_radiation", "nasa_temperature_mean_c"]]

    async def fake_weather(*args):
        assert args[-1] == "UTC"
        return weather, [], 0

    async def fake_nasa(*_args):
        return raw_nasa, metadata, [], 0

    monkeypatch.setattr("app.main.fetch_weather", fake_weather)
    monkeypatch.setattr("app.main.fetch_nasa", fake_nasa)
    header = {"X-Session-Id": client.post("/api/session").json()["session_id"]}
    if weather_source == "upload":
        seed_weather(header, weather)
    params = {"locations": ["dumaguete"], "start_date": "2020-01-01", "end_date": "2020-01-04", "weather_source": weather_source, "nasa_source": nasa_source}
    if nasa_source == "upload":
        preview = client.post("/api/upload/preview", headers=header, files={"file": ("solar.csv", raw_nasa.to_csv(index=False), "text/csv")})
        assert preview.status_code == 200
        params.update({"nasa_upload_id": preview.json()["upload_id"], "nasa_mapping": {column: column for column in raw_nasa.columns}, "nasa_solar_unit": "kWh/m^2/day", "nasa_temperature_unit": "C", "nasa_time_standard": "UTC"})
    validated = client.post("/api/multisource/validate", headers=header, json=params)
    assert validated.status_code == 200, validated.text
    report = validated.json()["report"]
    assert report["primary_origin"] == ("Open-Meteo API" if weather_source == "api" else "user-upload")
    assert report["secondary_origin"] == ("NASA POWER API" if nasa_source == "api" else "user-upload")
    merged = client.post("/api/multisource/merge", headers=header, json={"validation_id": validated.json()["validation_id"]})
    assert merged.status_code == 200, merged.text
    assert merged.json()["merge_quality"]["paired_solar_rows"] == 3
