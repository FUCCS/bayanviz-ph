from __future__ import annotations

import calendar
import hashlib
import json

import numpy as np
import pandas as pd

WEATHER_COLUMNS = ["temperature_mean_c", "temperature_min_c", "temperature_max_c", "precipitation_mm", "wind_speed_max_kmh"]
RAINY_DAY_MM = 1.0
MIN_PERIOD_COVERAGE = 0.9


def prepare_weather(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize daily estimates; retain missing values and flag suspect extremes."""
    df = frame.copy()
    required = {"location_id", "date", "model"}
    if not required.issubset(df):
        raise ValueError(f"Weather data needs {', '.join(sorted(required))}")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in WEATHER_COLUMNS:
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["location_id", "date", "model"])
    df["location_id"] = df.location_id.astype(str).str.strip()
    df = df[df.location_id.ne("")]
    df = df.drop_duplicates(["location_id", "date", "model"], keep="last")
    df["outlier_flag"] = (df.temperature_mean_c.lt(-15) | df.temperature_mean_c.gt(50) | df.precipitation_mm.lt(0) | df.precipitation_mm.gt(1000))
    return df.sort_values(["location_id", "date", "model"]).reset_index(drop=True)


def date_bounds(weather: pd.DataFrame, locations: list[str] | None = None) -> dict:
    df = weather[weather.location_id.isin(locations)] if locations else weather
    if df.empty:
        return {"min_date": None, "max_date": None, "records": 0, "locations": []}
    return {"min_date": str(df.date.min().date()), "max_date": str(df.date.max().date()), "records": len(df), "locations": sorted(df.location_id.unique().tolist())}


def filter_weather(weather: pd.DataFrame, locations: list[str], start: str | None, end: str | None) -> pd.DataFrame:
    df = weather[weather.location_id.isin(locations)] if locations else weather
    bounds = date_bounds(df)
    if not bounds["min_date"]:
        raise ValueError("No observations for the selected locations")
    lower, upper = start or bounds["min_date"], end or bounds["max_date"]
    if lower > upper:
        raise ValueError("Start date must not be after end date")
    if lower < bounds["min_date"] or upper > bounds["max_date"]:
        raise ValueError(f"Select dates within the loaded observations: {bounds['min_date']} to {bounds['max_date']}")
    return df[df.date.between(pd.Timestamp(lower), pd.Timestamp(upper))].copy()


def period_table(weather: pd.DataFrame, frequency: str) -> pd.DataFrame:
    """Aggregate at each location, never sum rainfall across locations."""
    if frequency not in {"monthly", "annual"}:
        raise ValueError("Choose monthly or annual aggregation")
    df = weather.copy()
    df["year"] = df.date.dt.year
    df["month"] = df.date.dt.month
    keys = ["location_id", "year"] + (["month"] if frequency == "monthly" else [])
    table = df.groupby(keys, as_index=False).agg(
        temperature_mean_c=("temperature_mean_c", "mean"),
        temperature_valid_days=("temperature_mean_c", "count"),
        temperature_std_c=("temperature_mean_c", "std"),
        precipitation_mm=("precipitation_mm", lambda s: s.sum(min_count=1)),
        precipitation_valid_days=("precipitation_mm", "count"),
        precipitation_std_mm=("precipitation_mm", "std"),
        wind_speed_max_kmh=("wind_speed_max_kmh", "max"),
        wind_valid_days=("wind_speed_max_kmh", "count"),
        rainy_days=("precipitation_mm", lambda s: int(s.ge(RAINY_DAY_MM).sum())),
    )
    if frequency == "monthly":
        table["expected_days"] = [calendar.monthrange(int(y), int(m))[1] for y, m in zip(table.year, table.month)]
        table["period"] = table.year.astype(str) + "-" + table.month.astype(str).str.zfill(2)
    else:
        table["expected_days"] = [366 if calendar.isleap(int(y)) else 365 for y in table.year]
        table["period"] = table.year.astype(str)
    table["temperature_coverage_pct"] = (100 * table.temperature_valid_days / table.expected_days).round(1)
    table["rainfall_coverage_pct"] = (100 * table.precipitation_valid_days / table.expected_days).round(1)
    table["complete_temperature"] = table.temperature_valid_days.ge(np.ceil(table.expected_days * MIN_PERIOD_COVERAGE))
    table["complete_rainfall"] = table.precipitation_valid_days.ge(np.ceil(table.expected_days * MIN_PERIOD_COVERAGE))
    return table


def monthly_rainfall(weather: pd.DataFrame) -> pd.DataFrame:
    table = period_table(weather, "monthly")
    return table.rename(columns={"precipitation_mm": "total_mm", "precipitation_valid_days": "valid_days", "complete_rainfall": "complete"})


def correlation_table(weather: pd.DataFrame, x: str, y: str, aggregation: str = "daily") -> tuple[pd.DataFrame, float | None]:
    """Pair observations by location and date/period before Pearson correlation."""
    if aggregation == "daily":
        table = weather[["location_id", "date", x, y]].dropna().copy()
        table["period"] = table.date.dt.strftime("%Y-%m-%d")
    elif aggregation == "monthly":
        table = period_table(weather, "monthly")
        table = table[table.complete_rainfall]
        if x.startswith("temperature_") or y.startswith("temperature_"):
            table = table[table.complete_temperature]
        if x.startswith("wind_") or y.startswith("wind_"):
            table = table[table.wind_valid_days.ge(np.ceil(table.expected_days * MIN_PERIOD_COVERAGE))]
        table = table[["location_id", "period", x, y]].dropna().copy()
    else:
        raise ValueError("Choose daily or monthly paired observations")
    if len(table) < 3 or table[x].nunique() < 2 or table[y].nunique() < 2:
        return table, None
    return table, float(table[x].corr(table[y]))


def safe_number(value) -> float | None:
    return round(float(value), 2) if value is not None and pd.notna(value) and np.isfinite(value) else None


def climate_summary(weather: pd.DataFrame, start: str, end: str, manifest: dict, source: str, reference: pd.DataFrame | None = None) -> dict:
    if weather.empty:
        raise ValueError("No observations in selected range")
    expected = (pd.Timestamp(end) - pd.Timestamp(start)).days + 1
    locations = int(weather.location_id.nunique())
    denominator = expected * locations
    valid_temp = int(weather.temperature_mean_c.count())
    valid_rain = int(weather.precipitation_mm.count())
    monthly = period_table(weather, "monthly")
    years = period_table(weather, "annual")
    per_location = weather.groupby("location_id").agg(rain_mm=("precipitation_mm", lambda s: s.sum(min_count=1))).rain_mm
    total_rain = float(per_location.iloc[0]) if locations == 1 and pd.notna(per_location.iloc[0]) else None
    monthly_rain = monthly[monthly.complete_rainfall].precipitation_mm
    complete_temperature_months = monthly[monthly.complete_temperature].temperature_mean_c
    complete_years = years[years.complete_temperature].sort_values(["location_id", "year"])
    year_over_year = []
    for key, group in complete_years.groupby("location_id"):
        for current, previous in zip(group.iloc[1:].itertuples(), group.iloc[:-1].itertuples()):
            if current.year == previous.year + 1:
                year_over_year.append({"location_id": key, "year": int(current.year), "temperature_change_c": safe_number(current.temperature_mean_c - previous.temperature_mean_c)})
    reference_data = reference if reference is not None else weather
    reference_start = str(reference_data.date.min().date())
    reference_end = str(reference_data.date.max().date())
    reference_mean = reference_data.temperature_mean_c.mean()
    quarters = weather.assign(calendar_quarter=weather.date.dt.quarter).groupby("calendar_quarter", as_index=False).agg(
        mean_temperature_c=("temperature_mean_c", "mean"),
        mean_daily_rain_mm=("precipitation_mm", "mean"),
        valid_temperature_days=("temperature_mean_c", "count"),
        valid_rain_days=("precipitation_mm", "count"),
    )
    return {
        "mean_temperature_c": safe_number(weather.temperature_mean_c.mean()),
        "maximum_temperature_c": safe_number(weather.temperature_max_c.max()),
        "minimum_temperature_c": safe_number(weather.temperature_min_c.min()),
        "total_precipitation_mm": safe_number(total_rain),
        "average_monthly_precipitation_mm": safe_number(monthly_rain.mean()),
        "rainy_day_frequency_pct": safe_number(100 * int(weather.precipitation_mm.ge(RAINY_DAY_MM).sum()) / valid_rain) if valid_rain else None,
        "maximum_wind_kmh": safe_number(weather.wind_speed_max_kmh.max()),
        "valid_observations": valid_temp,
        "valid_temperature_pct": round(100 * valid_temp / denominator, 1),
        "valid_rainfall_pct": round(100 * valid_rain / denominator, 1),
        "loaded_records": len(weather), "selected_period": [start, end], "observation_period": [str(weather.date.min().date()), str(weather.date.max().date())],
        "locations": locations, "missing_temperature": int(weather.temperature_mean_c.isna().sum()),
        "missing_precipitation": int(weather.precipitation_mm.isna().sum()),
        "incomplete_months": int((~monthly.complete_rainfall | ~monthly.complete_temperature).sum()),
        "incomplete_years": int((~years.complete_rainfall | ~years.complete_temperature).sum()),
        "rainy_threshold_mm": RAINY_DAY_MM, "source": source, "model": sorted(weather.model.unique().tolist()),
        "retrieved_at": manifest.get("retrieved_at_utc"),
        "duplicate_records_retained": int(weather.duplicated(["location_id", "date", "model"]).sum()),
        "temperature_range_c": safe_number(weather.temperature_max_c.max() - weather.temperature_min_c.min()),
        "monthly_temperature_variability_c": safe_number(complete_temperature_months.std()),
        "monthly_rainfall_variability_mm": safe_number(monthly_rain.std()),
        "seasonal_quarter_averages": [{key: safe_number(value) if isinstance(value, (float, np.floating)) else int(value) for key, value in row.items()} for row in quarters.to_dict("records")],
        "year_over_year_temperature": year_over_year,
        "temperature_anomaly_c": safe_number(weather.temperature_mean_c.mean() - reference_mean),
        "anomaly_reference_period": [reference_start, reference_end],
        "anomaly_method": "Difference between selected valid-daily mean and all available valid-daily mean for the same selected locations; not a long-term climate normal.",
        "precipitation_note": "Total precipitation is shown only for one selected sample location; rainfall depths must not be summed across locations." if locations != 1 else "Sum of valid daily precipitation at this sample location; incomplete days are disclosed.",
    }


def filter_hash(filters: dict) -> str:
    return hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()[:16]
