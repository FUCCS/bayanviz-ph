"""Controlled climate records for V2 analytical calculations and compatibility."""
import asyncio

import numpy as np
import pandas as pd
import pytest

from app.advanced import ADVANCED_CHARTS
from app.catalog import LOCATIONS
from app.charts import code_for, prepare_chart, render_chart
from app.connectors import HOURLY_VARIABLES, VARIABLES, fetch_weather, weather_cache
from app.main import chart_compatibility
from app.processing import SCHEMA_VERSION, daily_temperature_range, observation_coverage, prepare_weather, threshold_days


def weather_sample():
    dates = list(pd.date_range("2024-01-01", "2024-12-31")) + list(pd.date_range("2025-01-01", "2025-01-31"))
    return prepare_weather(pd.DataFrame({
        "location_id": "dumaguete", "date": dates, "model": "era5",
        "temperature_mean_c": 27, "temperature_min_c": 23,
        "temperature_max_c": [35] * 10 + [30] * (len(dates) - 10),
        "precipitation_mm": [2] * 366 + [3] * 31,
        "wind_speed_max_kmh": [50] * 7 + [20] * (len(dates) - 7),
        "relative_humidity_mean_pct": 75,
        "precipitation_hours": 4,
    }))


def test_schema_backward_compatibility_and_quality():
    old = prepare_weather(pd.DataFrame({"location_id": ["dumaguete"], "date": ["2024-01-01"], "model": ["era5"], "temperature_mean_c": [27], "precipitation_mm": [1]}))
    assert SCHEMA_VERSION == "climate-daily-v2"
    assert pd.isna(old.relative_humidity_mean_pct.iloc[0])
    report = chart_compatibility(old)
    assert report["annual_rain"]["available"]
    assert report["coverage"]["available"]
    assert report["temperature_humidity"]["missing_fields"] == ["relative_humidity_mean_pct"]
    with pytest.raises(ValueError, match="maximum temperature below minimum"):
        prepare_weather(pd.DataFrame({"location_id": ["dumaguete"], "date": ["2024-01-01"], "model": ["era5"], "temperature_min_c": [30], "temperature_max_c": [25]}))


def test_annual_rainfall_partial_monthly_cells_and_coverage():
    weather = weather_sample()
    annual, _, _, _ = prepare_chart("annual_rain", weather)
    assert annual.loc[annual.year.eq(2024), "precipitation_mm"].iloc[0] == 732
    assert annual.loc[annual.year.eq(2024), "period_status"].iloc[0] == "≥90% valid days"
    assert annual.loc[annual.year.eq(2025), "period_status"].iloc[0] == "partial or <90% valid days"
    monthly, _, _, _ = prepare_chart("rain_year_month", weather)
    assert monthly.loc[monthly.year.eq(2025) & monthly.month.eq(1), "precipitation_mm"].iloc[0] == 93
    assert monthly.loc[monthly.year.eq(2025) & monthly.month.eq(1), "precipitation_valid_days"].iloc[0] == 31
    coverage = observation_coverage(weather, ["precipitation_mm", "cloud_cover_mean_pct"], "annual")
    assert coverage.loc[coverage.year.eq(2024) & coverage.variable.eq("precipitation_mm"), "coverage_pct"].iloc[0] == 100
    assert coverage.loc[coverage.variable.eq("cloud_cover_mean_pct"), "field_status"].eq("absent").all()


def test_thresholds_temperature_range_and_relationships():
    weather = weather_sample()
    assert daily_temperature_range(weather).iloc[0] == 12
    assert threshold_days(weather, "temperature_max_c", 35).days_above_threshold.sum() == 10
    assert threshold_days(weather, "wind_speed_max_kmh", 50).days_above_threshold.sum() == 7
    hot, _, _, facts = prepare_chart("hot_days", weather, hot_threshold=36)
    assert hot.days_above_threshold.sum() == 0 and facts["threshold"] == 36
    wind, _, _, facts = prepare_chart("strong_wind_days", weather, wind_threshold=50)
    assert wind.days_above_threshold.sum() == 7 and facts["threshold"] == 50
    paired, _, _, facts = prepare_chart("temperature_humidity", weather)
    assert len(paired) == facts["paired_observations"] and facts["pearson_r"] is None
    duration, _, _, _ = prepare_chart("rain_duration", weather)
    assert len(duration) == len(weather)


def test_new_chart_builders_and_code_are_real():
    weather = weather_sample()
    for chart_id in ADVANCED_CHARTS:
        table, _, _, _ = prepare_chart(chart_id, weather)
        result = render_chart(chart_id, table)
        assert result["type"] == "png" and len(result["content"]) > 100
        assert ADVANCED_CHARTS[chart_id][3].__name__ in code_for(chart_id)["visualization"]


def test_map_reaches_every_registered_region():
    assert len({loc["region"] for loc in LOCATIONS.values()}) == 18
    weather = prepare_weather(pd.DataFrame([{"location_id": key, "date": "2025-01-01", "model": "era5", "temperature_mean_c": 27, "precipitation_mm": 2, "wind_speed_max_kmh": 15} for key in LOCATIONS]))
    table, _, _, _ = prepare_chart("map", weather)
    assert len(table) == len(LOCATIONS)
    assert table.region.nunique() == 18
    assert table.selected_valid_days.eq(1).all()
    assert table.period_start.eq("2025-01-01").all()


def test_openmeteo_hourly_aggregation_and_sunshine_units(monkeypatch):
    from app import connectors
    weather_cache.clear()
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"daily": {"time": ["2025-01-01"], "sunshine_duration": [7200]}, "daily_units": {"sunshine_duration": "s"},
                    "hourly": {"time": ["2025-01-01T00:00", "2025-01-01T01:00"], "relative_humidity_2m": [60, 80]},
                    "hourly_units": {"relative_humidity_2m": "%"}, "latitude": 9.25, "longitude": 123.25}
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr(connectors.httpx, "AsyncClient", Client)
    frame, errors, _ = asyncio.run(fetch_weather(["dumaguete"], pd.Timestamp("2025-01-01").date(), pd.Timestamp("2025-01-01").date(), ["sunshine_duration_hours", "relative_humidity_mean_pct"], "UTC"))
    assert not errors
    assert frame.sunshine_duration_hours.iloc[0] == 2
    assert frame.relative_humidity_mean_pct.iloc[0] == 70
    assert VARIABLES["sunshine_duration_hours"] == "sunshine_duration"
    assert HOURLY_VARIABLES["relative_humidity_mean_pct"] == "relative_humidity_2m"


def test_long_weather_fetch_uses_complete_bounded_chunks(monkeypatch):
    from app import connectors

    weather_cache.clear()
    requested = []

    class Response:
        def __init__(self, params):
            self.params = params

        def raise_for_status(self):
            pass

        def json(self):
            days = pd.date_range(self.params["start_date"], self.params["end_date"])
            return {"daily": {"time": days.strftime("%Y-%m-%d").tolist(),
                              "temperature_2m_mean": [25.0] * len(days)},
                    "daily_units": {"temperature_2m_mean": "°C"}}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, _url, params):
            requested.append((params["start_date"], params["end_date"]))
            return Response(params)

    monkeypatch.setattr(connectors.httpx, "AsyncClient", Client)
    frame, errors, hits = asyncio.run(fetch_weather(["dumaguete"], pd.Timestamp("2024-01-01").date(), pd.Timestamp("2025-12-31").date(), ["temperature_mean_c"], "UTC"))
    assert not errors and hits == 0 and len(frame) == 731
    assert len(requested) == 3
    assert max((pd.Timestamp(end) - pd.Timestamp(start)).days for start, end in requested) <= 364
    assert not frame.duplicated(["location_id", "date"]).any()
