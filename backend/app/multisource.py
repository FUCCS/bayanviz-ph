"""Validated UTC daily integration of Open-Meteo ERA5 and NASA POWER point data."""
from __future__ import annotations

import base64
import html
import inspect
import io
import json
from datetime import datetime, timezone

import folium
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from .catalog import LOCATIONS
from .charts import RENDER_LOCK
from .connectors import fetch_nasa, fetch_weather, parse_nasa_payload
from .processing import WEATHER_COLUMNS, safe_number

NASA_COLUMNS = ["nasa_solar_radiation_kwh_m2_day", "nasa_temperature_mean_c"]
MS_CHARTS = {
    "temp_solar": ("Temperature vs solar radiation", "sns.scatterplot()", "Compare paired daily temperature and solar radiation"),
    "rain_solar": ("Rainfall vs solar radiation", "sns.scatterplot()", "Compare paired rainfall and solar radiation; bubble size is mean temperature"),
    "temp_compare": ("Temperature source comparison", "sns.lineplot()", "Inspect agreement between two modeled daily temperature estimates"),
    "solar_seasonal": ("Seasonal solar radiation", "sns.boxplot()", "Compare valid daily solar radiation by calendar month"),
    "multi_correlation": ("Multivariate climate correlation", "sns.heatmap()", "Inspect complete-case climate correlations"),
    "solar_map": ("Solar radiation sample map", "folium.CircleMarker()", "Map sampled solar radiation without spatial interpolation"),
}


def normalize_nasa(frame: pd.DataFrame, metadata: dict) -> tuple[pd.DataFrame, dict]:
    """Convert only documented compatible NASA units, retaining raw values."""
    required = {"location_id", "date", "nasa_solar_radiation"}
    if not required.issubset(frame):
        raise ValueError("NASA data needs location_id, date, and solar radiation")
    df = frame.copy()
    df["date"] = pd.to_datetime(df.date, errors="coerce", utc=True).dt.tz_localize(None)
    if df.date.isna().any():
        raise ValueError("NASA data contains invalid dates")
    if df.duplicated(["location_id", "date"]).any():
        raise ValueError("NASA data has duplicate location-date keys")
    for col in ("nasa_solar_radiation", "nasa_temperature_mean_c"):
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf, -999, -9999], np.nan)
    if df.nasa_solar_radiation.lt(0).any():
        raise ValueError("NASA solar radiation contains negative non-missing values")
    units = {m.get("parameter_metadata", {}).get("ALLSKY_SFC_SW_DWN", {}).get("units") for m in metadata.values()}
    if len(units) != 1:
        raise ValueError("NASA solar units differ between selected locations")
    raw_unit = next(iter(units))
    normalized = str(raw_unit).lower().replace(" ", "").replace("²", "^2")
    if normalized in {"kw-hr/m^2/day", "kwh/m^2/day", "kwh/m2/day"}:
        factor = 1.0
    elif normalized in {"mj/m^2/day", "mj/m2/day"}:
        factor = 1 / 3.6
    else:
        raise ValueError(f"Unsupported NASA solar radiation unit: {raw_unit}")
    temp_units = {m.get("parameter_metadata", {}).get("T2M", {}).get("units") for m in metadata.values() if "T2M" in m.get("parameter_metadata", {})}
    if temp_units and temp_units not in ({"C"}, {"°C"}, {"degC"}):
        raise ValueError(f"Unsupported NASA temperature unit: {', '.join(map(str, temp_units))}")
    df["nasa_solar_radiation_kwh_m2_day"] = df.nasa_solar_radiation * factor
    return df.sort_values(["location_id", "date"]).reset_index(drop=True), {"original_solar_unit": raw_unit, "canonical_solar_unit": "kWh/m²/day", "conversion_factor": factor, "original_values_column": "nasa_solar_radiation"}


def validate_sources(primary: pd.DataFrame, secondary: pd.DataFrame | None, metadata: dict, requested_locations: list[str], primary_standard: str = "UTC") -> dict:
    """Report compatibility before any merge is attempted."""
    if primary.empty:
        raise ValueError("Open-Meteo returned no daily observations")
    if "analysis_time_standard" not in primary or primary_standard != "UTC" or not primary["analysis_time_standard"].eq("UTC").all():
        raise ValueError("Exact daily integration requires UTC Open-Meteo observations")
    if primary.duplicated(["location_id", "date"]).any():
        raise ValueError("Open-Meteo has duplicate location-date keys")
    if primary.date.isna().any():
        raise ValueError("Open-Meteo returned invalid daily dates")
    if set(primary.location_id) - set(requested_locations):
        raise ValueError("Open-Meteo returned an unrequested location")
    first, last = str(primary.date.min().date()), str(primary.date.max().date())
    primary_keys = set(zip(primary.location_id, primary.date))
    report = {"status": "Missing Secondary Data" if secondary is None else "Validated", "providers": ["Open-Meteo Historical Weather"] + (["NASA POWER"] if secondary is not None else []), "primary_dataset_version": "ERA5", "primary_rows": len(primary), "primary_coverage": [first, last], "daily_time_standard": "UTC", "requested_locations": [{key: LOCATIONS[loc][key] for key in ("id", "name", "province", "region", "latitude", "longitude")} for loc in requested_locations], "openmeteo_grid": primary[["location_id", "openmeteo_grid_latitude", "openmeteo_grid_longitude"]].drop_duplicates().to_dict("records") if {"openmeteo_grid_latitude", "openmeteo_grid_longitude"}.issubset(primary) else [], "nasa_grid": {loc: {"latitude": meta.get("grid_latitude"), "longitude": meta.get("grid_longitude")} for loc, meta in metadata.items()}, "nasa_parameter_metadata": {loc: meta.get("parameter_metadata", {}) for loc, meta in metadata.items()}, "nasa_dataset_versions": {loc: meta.get("dataset_version") for loc, meta in metadata.items()}, "primary_variables": [col for col in WEATHER_COLUMNS if col in primary and primary[col].notna().any()], "nasa_variables": [], "primary_missing_values": {col: int(primary[col].isna().sum()) for col in WEATHER_COLUMNS if col in primary}, "primary_duplicate_records": 0, "nasa_rows": 0, "nasa_missing_values": {}, "nasa_duplicate_records": 0, "matched_dates": 0, "unmatched_primary_dates": len(primary), "unmatched_nasa_dates": 0, "common_date_range": None, "warnings": ["Point samples can represent different provider grid cells; matching requested coordinates does not make grid resolution identical."]}
    if secondary is None:
        return report
    if secondary.empty:
        raise ValueError("NASA POWER returned no daily observations")
    if set(secondary.location_id) - set(requested_locations):
        raise ValueError("NASA POWER returned an unrequested location")
    if set(secondary.location_id) != set(metadata) or any(meta.get("time_standard") != "UTC" for meta in metadata.values()):
        raise ValueError("Exact daily integration requires UTC NASA POWER observations")
    if secondary.date.isna().any():
        raise ValueError("NASA POWER returned invalid daily dates")
    if secondary.duplicated(["location_id", "date"]).any():
        raise ValueError("NASA POWER has duplicate location-date keys")
    secondary_keys = set(zip(secondary.location_id, secondary.date))
    common = primary_keys & secondary_keys
    if not common:
        raise ValueError("The providers have no overlapping location-date observations")
    overlap_dates = sorted(date for _, date in common)
    report.update({"nasa_rows": len(secondary), "nasa_coverage": [str(secondary.date.min().date()), str(secondary.date.max().date())], "nasa_variables": [col for col in NASA_COLUMNS if col in secondary and secondary[col].notna().any()], "nasa_missing_values": {col: int(secondary[col].isna().sum()) for col in NASA_COLUMNS if col in secondary}, "matched_dates": len(common), "unmatched_primary_dates": len(primary_keys - secondary_keys), "unmatched_nasa_dates": len(secondary_keys - primary_keys), "common_date_range": [str(overlap_dates[0].date()), str(overlap_dates[-1].date())]})
    if report["unmatched_primary_dates"] or report["unmatched_nasa_dates"]:
        report["status"] = "Partially Matched"
    if not secondary.nasa_solar_radiation_kwh_m2_day.notna().any():
        report["warnings"].append("NASA returned dates but no valid solar radiation measurements.")
    return report


def merge_sources(primary: pd.DataFrame, secondary: pd.DataFrame | None, report: dict) -> tuple[pd.DataFrame, dict]:
    """Keep every primary row; reject many-to-many expansion with Pandas validation."""
    if report["status"] not in {"Validated", "Partially Matched", "Missing Secondary Data"}:
        raise ValueError("Validate compatible sources before merging")
    if secondary is None:
        merged = primary.copy()
        for col in ["nasa_solar_radiation", *NASA_COLUMNS]:
            merged[col] = np.nan
        merged["merge_status"] = "primary_only"
        matched = 0
    else:
        merged = pd.merge(primary, secondary, on=["location_id", "date"], how="left", validate="one_to_one", indicator=True)
        merged["merge_status"] = np.where(merged._merge.eq("both"), "matched_date", "primary_only")
        matched = int(merged._merge.eq("both").sum())
        merged = merged.drop(columns="_merge")
    paired = int(merged.nasa_solar_radiation_kwh_m2_day.notna().sum())
    quality = {"primary_rows": len(primary), "nasa_rows": 0 if secondary is None else len(secondary), "matched_rows": matched, "unmatched_rows": len(primary) - matched, "paired_solar_rows": paired, "paired_coverage_pct": round(100 * paired / len(primary), 1), "paired_coverage_formula": "100 × primary rows with valid NASA solar radiation / all primary rows", "common_date_range": report["common_date_range"], "status": report["status"]}
    return merged, quality


def paired_table(frame: pd.DataFrame, *columns: str) -> pd.DataFrame:
    required = ["location_id", "date", *columns]
    return frame[required].replace([np.inf, -np.inf], np.nan).dropna(subset=columns).copy()


def pearson(table: pd.DataFrame, x: str, y: str) -> float | None:
    return float(table[x].corr(table[y])) if len(table) >= 3 and table[x].nunique() > 1 and table[y].nunique() > 1 else None


def prepare_multisource_chart(chart_id: str, frame: pd.DataFrame) -> tuple[pd.DataFrame, str, dict, list[str]]:
    """Build chart evidence exclusively from the selected filtered UTC observations."""
    if chart_id not in MS_CHARTS:
        raise ValueError("Unknown multi-source chart")
    caveats = ["Open-Meteo ERA5 and NASA POWER represent different modeled grids, not ground-truth weather stations.", "UTC daily alignment compares matching requested dates; provider grids and model definitions still differ.", "Association does not establish causation."]
    solar = "nasa_solar_radiation_kwh_m2_day"
    if chart_id == "temp_solar":
        table = paired_table(frame, "temperature_mean_c", solar)
        units = "°C / kWh/m²/day"
        facts = {"paired_observations": len(table), "pearson_r": pearson(table, "temperature_mean_c", solar), "mean_temperature_c": safe_number(table.temperature_mean_c.mean()), "mean_solar_kwh_m2_day": safe_number(table[solar].mean())}
    elif chart_id == "rain_solar":
        table = paired_table(frame, "precipitation_mm", solar, "temperature_mean_c")
        units = "mm / kWh/m²/day / °C"
        facts = {"paired_observations": len(table), "pearson_r": pearson(table, "precipitation_mm", solar), "mean_rainfall_mm": safe_number(table.precipitation_mm.mean()), "mean_solar_kwh_m2_day": safe_number(table[solar].mean())}
    elif chart_id == "temp_compare":
        table = paired_table(frame, "temperature_mean_c", "nasa_temperature_mean_c")
        table["difference_c"] = table.temperature_mean_c - table.nasa_temperature_mean_c
        units = "°C"
        facts = {"paired_observations": len(table), "pearson_r": pearson(table, "temperature_mean_c", "nasa_temperature_mean_c"), "mean_difference_c": safe_number(table.difference_c.mean()), "mean_absolute_error_c": safe_number(table.difference_c.abs().mean()), "root_mean_squared_error_c": safe_number(np.sqrt((table.difference_c**2).mean()))}
    elif chart_id == "solar_seasonal":
        table = paired_table(frame, solar)
        table["month"] = table.date.dt.month
        units = "kWh/m²/day"
        facts = {"paired_observations": len(table), "mean_solar_kwh_m2_day": safe_number(table[solar].mean()), "solar_std_kwh_m2_day": safe_number(table[solar].std())}
    elif chart_id == "multi_correlation":
        columns = ["temperature_mean_c", "precipitation_mm", "wind_speed_max_kmh", solar]
        table = paired_table(frame, *columns)
        units = "Pearson r (unitless)"
        matrix = table[columns].corr() if len(table) >= 3 else pd.DataFrame()
        facts = {"paired_observations": len(table), "complete_case_policy": "All four variables must be valid for a location-date row", "correlations": json.loads(matrix.to_json()) if not matrix.empty else {}, "missing_by_variable": {name: int(frame[name].isna().sum()) for name in columns}}
    else:
        table = frame[["location_id", "date", solar, "temperature_mean_c", "precipitation_mm"]].replace([np.inf, -np.inf], np.nan).dropna(subset=[solar]).copy()
        grouped = table.groupby("location_id", as_index=False).agg(solar_mean=(solar, "mean"), valid_days=(solar, "count"), temperature_mean_c=("temperature_mean_c", "mean"), precipitation_mean_mm_day=("precipitation_mm", "mean"))
        grouped["latitude"] = grouped.location_id.map(lambda key: LOCATIONS[key]["latitude"])
        grouped["longitude"] = grouped.location_id.map(lambda key: LOCATIONS[key]["longitude"])
        grouped["location_name"] = grouped.location_id.map(lambda key: LOCATIONS[key]["name"])
        grouped["source_status"] = "Open-Meteo + NASA POWER"
        table = grouped
        units = "kWh/m²/day"
        facts = {"paired_observations": int(grouped.valid_days.sum()), "mapped_locations": len(grouped)}
    if table.empty:
        raise ValueError("No valid paired observations for this analysis and date range")
    if chart_id == "multi_correlation" and len(table) < 3:
        raise ValueError("At least three complete paired observations are needed for a correlation matrix")
    return table, units, facts, caveats


def build_temp_solar(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    sns.scatterplot(data=table, x="nasa_solar_radiation_kwh_m2_day", y="temperature_mean_c", hue="location_id", ax=ax)
    ax.set(xlabel="NASA POWER solar radiation (kWh/m²/day)", ylabel="Open-Meteo mean temperature (°C)", title="Temperature and solar radiation at paired sample dates")
    fig.tight_layout()
    return fig


def build_rain_solar(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    sns.scatterplot(data=table, x="nasa_solar_radiation_kwh_m2_day", y="precipitation_mm", size="temperature_mean_c", sizes=(25, 240), hue="location_id", ax=ax)
    ax.set(xlabel="NASA POWER solar radiation (kWh/m²/day)", ylabel="Open-Meteo precipitation (mm/day)", title="Rainfall and solar radiation; bubble area represents mean temperature")
    fig.tight_layout()
    return fig


def build_temp_compare(table: pd.DataFrame):
    long = table.melt(id_vars=["location_id", "date"], value_vars=["temperature_mean_c", "nasa_temperature_mean_c"], var_name="provider", value_name="temperature_c")
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    sns.lineplot(data=long, x="date", y="temperature_c", hue="provider", units="location_id", estimator=None, ax=ax)
    ax.set(xlabel="UTC date", ylabel="Mean temperature (°C)", title="Modeled temperature source comparison")
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def build_solar_seasonal(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    sns.boxplot(data=table, x="month", y="nasa_solar_radiation_kwh_m2_day", ax=ax)
    ax.set(xlabel="Calendar month", ylabel="NASA POWER solar radiation (kWh/m²/day)", title="Seasonal solar radiation at selected sample points")
    fig.tight_layout()
    return fig


def build_multi_correlation(table: pd.DataFrame):
    columns = ["temperature_mean_c", "precipitation_mm", "wind_speed_max_kmh", "nasa_solar_radiation_kwh_m2_day"]
    fig, ax = plt.subplots(figsize=(7.4, 5.4))
    sns.heatmap(table[columns].corr(), annot=True, cmap="BrBG", center=0, vmin=-1, vmax=1, ax=ax)
    ax.set_title("Complete-case climate correlations")
    fig.tight_layout()
    return fig


def build_solar_map(table: pd.DataFrame):
    center = [float(table.latitude.mean()), float(table.longitude.mean())]
    map_obj = folium.Map(location=center, zoom_start=6, tiles="OpenStreetMap", control_scale=True)
    layer = folium.FeatureGroup(name="NASA POWER solar radiation (kWh/m²/day)")
    for row in table.itertuples():
        temperature = f"{row.temperature_mean_c:.1f} °C" if pd.notna(row.temperature_mean_c) else "unavailable"
        rainfall = f"{row.precipitation_mean_mm_day:.1f} mm/day" if pd.notna(row.precipitation_mean_mm_day) else "unavailable"
        popup = f"<b>{html.escape(row.location_name)}</b><br>NASA solar: {row.solar_mean:.2f} kWh/m²/day<br>Open-Meteo temperature: {temperature}<br>Open-Meteo rainfall: {rainfall}<br>{row.valid_days} paired dates<br>{html.escape(row.source_status)}"
        folium.Marker([row.latitude, row.longitude], popup=popup, tooltip=row.location_name).add_to(layer)
        folium.CircleMarker([row.latitude, row.longitude], radius=max(5, min(22, 4 + 2 * row.solar_mean)), color="#e49a26", fill=True, fill_opacity=0.6, popup=popup).add_to(layer)
    layer.add_to(map_obj)
    folium.LayerControl(collapsed=False).add_to(map_obj)
    return map_obj.get_root().render()


MS_BUILDERS = {"temp_solar": build_temp_solar, "rain_solar": build_rain_solar, "temp_compare": build_temp_compare, "solar_seasonal": build_solar_seasonal, "multi_correlation": build_multi_correlation, "solar_map": build_solar_map}


def render_multisource_chart(chart_id: str, table: pd.DataFrame) -> dict:
    if chart_id == "solar_map":
        return {"type": "html", "content": build_solar_map(table)}
    with RENDER_LOCK:
        fig = MS_BUILDERS[chart_id](table)
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=145, bbox_inches="tight", facecolor="white")
        plt.close(fig)
    return {"type": "png", "content": base64.b64encode(buffer.getvalue()).decode("ascii")}


def multisource_code(chart_id: str) -> dict:
    title, function, objective = MS_CHARTS[chart_id]
    pipeline = [fetch_weather, fetch_nasa, parse_nasa_payload, normalize_nasa, validate_sources, merge_sources, paired_table, pearson, prepare_multisource_chart]
    return {"visualization": inspect.getsource(MS_BUILDERS[chart_id]), "preparation": "\n\n".join(inspect.getsource(step) for step in pipeline), "topic": {"chapter": "Climate integration", "topic": title, "library_function": function, "objective": objective}, "highlights": ["pd.merge(", function.split("(")[0]]}


def multisource_interpretation(chart_id: str, table: pd.DataFrame, facts: dict, caveats: list[str]) -> dict:
    count = facts["paired_observations"]
    if chart_id == "temp_compare":
        finding = f"Across {count} paired daily values, Open-Meteo minus NASA POWER mean difference is {facts['mean_difference_c']} °C; MAE {facts['mean_absolute_error_c']} °C and RMSE {facts['root_mean_squared_error_c']} °C. Neither model is ground truth."
    elif "pearson_r" in facts:
        r = facts["pearson_r"]
        finding = f"Pearson r = {r:.3f} across {count} valid paired daily values." if r is not None else f"Pearson correlation is unavailable for {count} paired values because there are fewer than three observations or a variable is constant."
    elif chart_id == "multi_correlation":
        finding = f"Correlation coefficients use {count} complete location-date rows; each cell has the same denominator."
    elif chart_id == "solar_map":
        finding = f"{facts['mapped_locations']} sampled locations contain {count} valid paired solar observations."
    else:
        finding = f"The selected dates contain {count} valid daily solar measurements; mean {facts['mean_solar_kwh_m2_day']} kWh/m²/day."
    return {"label": "Statistical Interpretation", "shows": MS_CHARTS[chart_id][2], "findings": finding, "evidence": f"The evidence table contains {len(table)} chart-input rows; the first 30 are shown and the complete table is downloadable.", "relevance": "Use these point comparisons for climate education and further investigation, not causal attribution or province-wide averages.", "limitations": caveats}
