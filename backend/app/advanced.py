"""Additional climate analyses; all aggregates retain their observation denominators."""
from __future__ import annotations

import calendar

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from .catalog import LOCATIONS
from .processing import CLIMATE_COLUMNS, daily_temperature_range, observation_coverage, period_table, threshold_days


def prepare_advanced(chart_id: str, weather: pd.DataFrame, hot_threshold: float = 35.0, wind_threshold: float = 50.0):
    """Prepare evidence for charts using only the filtered observations."""
    caveats = ["Each value describes a registered sampling point, not a province-wide observation."]
    if chart_id == "annual_rain":
        table = period_table(weather, "annual")
        table["location_name"] = table.location_id.map(lambda key: LOCATIONS.get(key, {}).get("name", key))
        table["period_status"] = np.where(table.complete_rainfall, "≥90% valid days", "partial or <90% valid days")
        table.loc[table.precipitation_valid_days.eq(0), "precipitation_mm"] = np.nan
        caveats.append("Partial-year rainfall totals are shown separately and cannot be compared directly with complete years.")
        return table[["location_id", "location_name", "year", "precipitation_mm", "precipitation_valid_days", "expected_days", "rainfall_coverage_pct", "period_status"]], "mm", caveats
    if chart_id == "rain_year_month":
        table = period_table(weather, "monthly")
        table["location_name"] = table.location_id.map(lambda key: LOCATIONS.get(key, {}).get("name", key))
        table["period_status"] = np.where(table.complete_rainfall, "≥90% valid days", "partial or <90% valid days")
        table.loc[table.precipitation_valid_days.eq(0), "precipitation_mm"] = np.nan
        caveats.append("The heatmap masks months below 90% valid-day coverage; evidence retains their observed totals and counts.")
        return table[["location_id", "location_name", "year", "month", "precipitation_mm", "precipitation_valid_days", "expected_days", "rainfall_coverage_pct", "period_status"]], "mm", caveats
    if chart_id == "coverage":
        columns = [name for name in CLIMATE_COLUMNS if name in weather]
        table = observation_coverage(weather, columns, "annual")
        table["location_name"] = table.location_id.map(lambda key: LOCATIONS.get(key, {}).get("name", key))
        caveats.append("Absent optional fields are blank, not zero-percent observed coverage.")
        return table, "% valid calendar days", caveats
    if chart_id in {"hot_days", "strong_wind_days"}:
        column, threshold, unit = ("temperature_max_c", hot_threshold, "°C") if chart_id == "hot_days" else ("wind_speed_max_kmh", wind_threshold, "km/h")
        if column not in weather or weather[column].notna().sum() == 0:
            raise ValueError(f"{column} is unavailable in this dataset")
        table = threshold_days(weather, column, threshold)
        table["location_name"] = table.location_id.map(lambda key: LOCATIONS.get(key, {}).get("name", key))
        table["threshold"] = threshold
        table["threshold_unit"] = unit
        table["frequency_pct"] = np.where(table.valid_days.gt(0), (100 * table.days_above_threshold / table.valid_days).round(1), np.nan)
        table["expected_days"] = table.year.map(lambda year: 366 if calendar.isleap(int(year)) else 365)
        caveats.append(f"Threshold {threshold:g} {unit} is user-defined; counts include only valid daily {column} observations. Partial years are identified by valid and expected days.")
        return table, "observed days", caveats
    if chart_id == "temperature_range":
        if weather.temperature_max_c.notna().sum() == 0 or weather.temperature_min_c.notna().sum() == 0:
            raise ValueError("Daily maximum and minimum temperature are required")
        table = weather[["location_id", "date", "temperature_max_c", "temperature_min_c"]].copy()
        table["temperature_range_c"] = daily_temperature_range(weather)
        table = table.dropna(subset=["temperature_range_c"])
        table["month"] = table.date.dt.month
        table["year"] = table.date.dt.year
        table["location_name"] = table.location_id.map(lambda key: LOCATIONS.get(key, {}).get("name", key))
        caveats.append("Only days with both maximum and minimum temperature contribute to the range distribution.")
        return table, "°C", caveats
    if chart_id == "temperature_humidity":
        if weather.relative_humidity_mean_pct.notna().sum() == 0:
            raise ValueError("Relative humidity is absent; upload it or select the hourly-derived API variable")
        table = weather[["location_id", "date", "temperature_mean_c", "relative_humidity_mean_pct"]].dropna().copy()
        table["location_name"] = table.location_id.map(lambda key: LOCATIONS.get(key, {}).get("name", key))
        caveats.append("Daily temperature and humidity are paired by location and date; correlation does not establish causation.")
        return table, "°C / %", caveats
    if chart_id == "rain_duration":
        if weather.precipitation_hours.notna().sum() == 0:
            raise ValueError("Precipitation duration is absent from this dataset")
        table = weather[["location_id", "date", "precipitation_mm", "precipitation_hours"]].dropna().copy()
        table["location_name"] = table.location_id.map(lambda key: LOCATIONS.get(key, {}).get("name", key))
        caveats.append("Duration is hours with precipitation; it is not an intensity measurement.")
        return table, "mm / hours", caveats
    raise ValueError("Unknown advanced chart")


def build_annual_rain(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.lineplot(data=table, x="year", y="precipitation_mm", hue="location_name", style="period_status", markers=True, dashes=True, estimator=None, ax=ax)
    ax.set(xlabel="Calendar year", ylabel="Observed rainfall total (mm)", title="Annual precipitation at sampling points")
    fig.tight_layout()
    return fig


def build_rain_year_month(table: pd.DataFrame):
    locations = list(table.location_id.unique())
    fig, axes = plt.subplots(len(locations), 1, figsize=(10, max(4, 3.5 * len(locations))), squeeze=False)
    for ax, location in zip(axes[:, 0], locations):
        subset = table[table.location_id.eq(location)].copy()
        subset.loc[subset.period_status.ne("≥90% valid days"), "precipitation_mm"] = np.nan
        matrix = subset.pivot(index="year", columns="month", values="precipitation_mm").reindex(columns=range(1, 13))
        sns.heatmap(matrix, annot=True, fmt=".0f", cmap="Blues", cbar_kws={"label": "mm"}, ax=ax)
        ax.set(title=str(subset.location_name.iloc[0]), xlabel="Calendar month", ylabel="Year")
    fig.tight_layout()
    return fig


def build_coverage(table: pd.DataFrame):
    locations = list(table.location_id.unique())
    fig, axes = plt.subplots(len(locations), 1, figsize=(10, max(4, 4 * len(locations))), squeeze=False)
    for ax, location in zip(axes[:, 0], locations):
        subset = table[table.location_id.eq(location)]
        matrix = subset.pivot(index="variable", columns="year", values="coverage_pct")
        sns.heatmap(matrix, annot=True, fmt=".0f", vmin=0, vmax=100, cmap="YlGnBu", cbar_kws={"label": "%"}, ax=ax)
        ax.set(title=str(subset.location_name.iloc[0]), xlabel="Year", ylabel="Field")
    fig.tight_layout()
    return fig


def build_hot_days(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.barplot(data=table, x="year", y="days_above_threshold", hue="location_name", errorbar=None, ax=ax)
    ax.set(title=f"Days with maximum temperature ≥{table.threshold.iloc[0]:g} °C", ylabel="Observed days", xlabel="Year")
    fig.tight_layout()
    return fig


def build_strong_wind_days(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.barplot(data=table, x="year", y="days_above_threshold", hue="location_name", errorbar=None, ax=ax)
    ax.set(title=f"Days with maximum wind ≥{table.threshold.iloc[0]:g} km/h", ylabel="Observed days", xlabel="Year")
    fig.tight_layout()
    return fig


def build_temperature_range(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.boxplot(data=table, x="month", y="temperature_range_c", hue="location_name", ax=ax)
    ax.set(title="Daily maximum-minus-minimum temperature by month", xlabel="Month", ylabel="Daily range (°C)")
    fig.tight_layout()
    return fig


def build_temperature_humidity(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.scatterplot(data=table, x="relative_humidity_mean_pct", y="temperature_mean_c", hue="location_name", alpha=.5, ax=ax)
    ax.set(title="Paired daily temperature and humidity", xlabel="Mean relative humidity (%)", ylabel="Mean temperature (°C)")
    fig.tight_layout()
    return fig


def build_rain_duration(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.scatterplot(data=table, x="precipitation_hours", y="precipitation_mm", hue="location_name", alpha=.5, ax=ax)
    ax.set(title="Paired precipitation amount and duration", xlabel="Hours with precipitation", ylabel="Daily precipitation (mm)")
    fig.tight_layout()
    return fig


ADVANCED_CHARTS = {
    "annual_rain": ("Annual rainfall trend", "sns.lineplot()", "Compare annual totals with observed-day coverage", build_annual_rain),
    "rain_year_month": ("Month-by-year rainfall", "pivot(); sns.heatmap()", "Show calendar-year rainfall patterns and incomplete months", build_rain_year_month),
    "coverage": ("Observation coverage", "sns.heatmap()", "Show valid daily coverage and absent optional fields", build_coverage),
    "hot_days": ("Temperature extremes", "sns.barplot()", "Count valid days above the selected maximum-temperature threshold", build_hot_days),
    "strong_wind_days": ("Wind extremes", "sns.barplot()", "Count valid days above the selected maximum-wind threshold", build_strong_wind_days),
    "temperature_range": ("Daily temperature range", "sns.boxplot()", "Compare maximum-minus-minimum daily temperatures", build_temperature_range),
    "temperature_humidity": ("Temperature vs humidity", "sns.scatterplot()", "Compare paired daily temperature and humidity", build_temperature_humidity),
    "rain_duration": ("Rainfall vs duration", "sns.scatterplot()", "Compare paired precipitation totals and hours", build_rain_duration),
}
