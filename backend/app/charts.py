from __future__ import annotations

import base64
import html
import io
import inspect
import json
import threading

import folium
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.express as px
import seaborn as sns
from statsmodels.graphics.mosaicplot import mosaic

from .catalog import LOCATIONS
from .advanced import ADVANCED_CHARTS, prepare_advanced
from .processing import CLIMATE_COLUMNS, RAINY_DAY_MM, correlation_table, period_table

RENDER_LOCK = threading.Lock()
PALETTE = {"rainy": "#1f7fa8", "dry": "#e2a548"}


def build_bar(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.barplot(data=table, x="month", y="average_monthly_mm", hue="location_name", errorbar=None, ax=ax)
    years = sorted({year for item in table.years_covered for year in str(item).split(",")})
    title = f"Monthly rainfall in {years[0]}" if len(years) == 1 else f"Monthly rainfall averaged across {len(years)} years"
    ax.set(xlabel="Month", ylabel="Average complete-month precipitation total (mm)", title=title)
    ax.legend(title="Location", bbox_to_anchor=(1.01, 1), loc="upper left")
    fig.tight_layout()
    return fig


def build_line(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 5))
    variable = str(table.variable.iloc[0])
    unit = "mm" if variable == "precipitation_mm" else "km/h" if variable == "wind_speed_max_kmh" else "°C"
    sns.lineplot(data=table, x="period_index", y="measure_value", hue="location_name", estimator=None, ax=ax)
    ticks = table[["period_index", "period"]].drop_duplicates().sort_values("period_index")
    stride = max(1, len(ticks) // 10)
    ticks = ticks.iloc[::stride]
    ax.set_xticks(ticks.period_index, ticks.period, rotation=35)
    ax.set(xlabel="Observation period", ylabel=f"{variable.replace('_', ' ').title()} ({unit})", title="Historical climate trend at uploaded points")
    ax.legend(title="Location", bbox_to_anchor=(1.01, 1), loc="upper left")
    fig.tight_layout()
    return fig


def build_year_compare(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.lineplot(data=table, x="year", y="temperature_mean_c", hue="location_name", marker="o", estimator=None, ax=ax)
    ax.set(xlabel="Calendar year", ylabel="Annual mean daily temperature (°C)", title="Year-by-year temperature at registered points")
    ax.set_xticks(sorted(table.year.unique()))
    ax.legend(title="Location", bbox_to_anchor=(1.01, 1), loc="upper left")
    fig.tight_layout()
    return fig


def build_box(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 5))
    variable = str(table.variable.iloc[0])
    unit = "mm" if variable == "precipitation_mm" else "km/h" if variable == "wind_speed_max_kmh" else "°C"
    sns.boxplot(data=table, x="location_name", y="measure_value", color="#5d9fba", ax=ax)
    ax.set(xlabel="Location", ylabel=f"Daily {variable.replace('_', ' ')} ({unit})", title="Daily climate distribution")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    return fig


def build_bubble(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.scatterplot(data=table, x="precipitation_mm", y="temperature_mean_c", size="wind_speed_max_kmh", sizes=(35, 340), hue="location_name", alpha=0.68, ax=ax)
    ax.set(xlabel="Daily precipitation (mm)" if table.aggregation.iloc[0] == "daily" else "Complete-month precipitation total (mm)", ylabel="Mean temperature (°C)", title="Temperature and rainfall · bubble area = maximum wind speed")
    ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=8)
    fig.tight_layout()
    return fig


def build_rain_wind(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.scatterplot(data=table, x="precipitation_mm", y="wind_speed_max_kmh", hue="location_name", alpha=0.58, ax=ax)
    ax.set(xlabel="Daily precipitation (mm)" if table.aggregation.iloc[0] == "daily" else "Complete-month precipitation total (mm)", ylabel="Maximum wind speed (km/h)", title="Rainfall and wind speed at paired periods")
    ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=8)
    fig.tight_layout()
    return fig


def build_heatmap(table: pd.DataFrame):
    grid = table.pivot_table(index="location_name", columns="month", values="average_monthly_mm", aggfunc="first")
    fig, ax = plt.subplots(figsize=(9, 4.8))
    sns.heatmap(grid, cmap="YlGnBu", annot=True, fmt=".0f", linewidths=0.4, cbar_kws={"label": "Average complete-month total (mm)"}, ax=ax)
    ax.set(xlabel="Month", ylabel="Location", title="Seasonal rainfall patterns")
    fig.tight_layout()
    return fig


def build_mosaic(table: pd.DataFrame):
    counts = {(str(row["location_name"]), str(row["condition"])): int(row["count"]) for row in table.to_dict("records")}
    fig, ax = plt.subplots(figsize=(10, 5))
    mosaic(counts, ax=ax, labelizer=lambda key: key[-1] if counts[key] >= 2 else "", properties=lambda key: {"color": PALETTE.get(key[-1], "#7294aa"), "alpha": 0.85})
    ax.set_title("Rainy and dry observed days by location")
    fig.tight_layout()
    return fig


def build_treemap(table: pd.DataFrame):
    fig = px.treemap(table, path=["region", "location_name", "month"], values="rainy_days", color="rainy_days", color_continuous_scale="Blues", title="Rainy-day observations by region, location, and month")
    fig.update_layout(margin=dict(t=55, l=12, r=12, b=12), coloraxis_colorbar_title="Rainy days")
    return fig


def build_map(table: pd.DataFrame):
    center = [float(table.latitude.mean()), float(table.longitude.mean())]
    map_obj = folium.Map(location=center, zoom_start=7, tiles="OpenStreetMap", control_scale=True)
    point_layer = folium.FeatureGroup(name="Weather locations", show=True)
    rain_layer = folium.FeatureGroup(name="Mean daily rainfall circles", show=True)
    for row in table.itertuples():
        lat, lon = float(row.latitude), float(row.longitude)
        label = html.escape(str(row.location_name))
        temp = float(row.temperature_value_c) if pd.notna(row.temperature_value_c) else np.nan
        rainfall = float(row.average_daily_rain_mm) if pd.notna(row.average_daily_rain_mm) else 0.0
        radius = float(np.clip(5 + 2.5 * np.sqrt(max(rainfall, 0)), 5, 22))
        color = "gray" if np.isnan(temp) else "red" if temp >= 28 else "orange" if temp >= 26 else "blue"
        selected_value = f"{float(row.selected_measure_value):.1f} {html.escape(str(row.selected_measure_unit))}" if pd.notna(row.selected_measure_value) else "unavailable"
        temperature = f"{temp:.1f} °C" if np.isfinite(temp) else "unavailable"
        popup = f"<b>{label}</b><br>{html.escape(str(row.province))} · {html.escape(str(row.region))} sampling point<br>Period: {html.escape(str(row.period_start))} to {html.escape(str(row.period_end))}<br>Selected {html.escape(str(row.selected_variable))}: {selected_value}<br>Valid selected observations: {int(row.selected_valid_days)} of {int(row.observed_days)} ({row.selected_coverage_pct:.1f}%); missing {int(row.selected_missing_days)}<br>Mean temperature: {temperature}; mean daily rainfall: {rainfall:.1f} mm<br>Source model: {html.escape(str(row.model))}<br>Difference from other loaded sampling points: {row.difference_from_loaded_mean:+.1f} {html.escape(str(row.selected_measure_unit))}"
        folium.Marker([lat, lon], popup=folium.Popup(popup, max_width=300), tooltip=label, icon=folium.Icon(color=color, icon="cloud")).add_to(point_layer)
        folium.CircleMarker([lat, lon], radius=radius, color="#0d6b9a", fill=True, fill_opacity=0.4, popup=folium.Popup(popup, max_width=300)).add_to(rain_layer)
    point_layer.add_to(map_obj)
    rain_layer.add_to(map_obj)
    folium.LayerControl(collapsed=False).add_to(map_obj)
    if len(table) > 1:
        map_obj.fit_bounds([[float(table.latitude.min()), float(table.longitude.min())], [float(table.latitude.max()), float(table.longitude.max())]])
    else:
        map_obj.location = center
        map_obj.options["zoom"] = 10
    color_measure = str(table.selected_variable.iloc[0]) if str(table.selected_variable.iloc[0]).startswith("temperature_") else "temperature_mean_c"
    legend = f'<div style="position:fixed;bottom:22px;left:18px;z-index:9999;background:white;padding:10px 13px;border:1px solid #b7cbd8;border-radius:7px;font:12px sans-serif;color:#163a51;box-shadow:0 2px 8px #0002"><b>Climate symbols</b><br>Marker: {html.escape(color_measure)} (&lt;26 blue, 26–&lt;28 orange, ≥28 red)<br>Circle radius: 5 + 2.5√(mean daily rainfall in mm), capped 5–22 px<br>ERA5 uploaded points; OSM is the background map only.</div>'
    map_obj.get_root().html.add_child(folium.Element(legend))
    return map_obj.get_root().render()


REGISTRY = {
    "bar": ("Chapter 08", "Seaborn bar graph", "sns.barplot()", "Compare mean monthly precipitation totals at locations", build_bar, "weather"),
    "line": ("Chapter 08", "Seaborn line graph", "sns.lineplot()", "Inspect daily, monthly, or annual climate trends", build_line, "weather"),
    "year_compare": ("Chapter 08", "Annual temperature comparison", "sns.lineplot()", "Compare complete-year mean temperatures at the same location", build_year_compare, "weather"),
    "box": ("Chapter 08", "Seaborn box plot", "sns.boxplot()", "Compare daily climate distributions", build_box, "weather"),
    "bubble": ("Chapter 08", "Bubble chart", "sns.scatterplot()", "Explore paired temperature and rainfall; bubble area is wind speed", build_bubble, "weather"),
    "rain_wind": ("Chapter 08", "Rainfall–wind relationship", "sns.scatterplot()", "Explore paired rainfall and maximum wind speed", build_rain_wind, "weather"),
    "heatmap": ("Chapter 08", "Heatmap", "pivot_table(); sns.heatmap()", "Compare seasonal precipitation patterns", build_heatmap, "weather"),
    "mosaic": ("Chapter 08", "Mosaic plot", "mosaic()", "Compare rainy and dry day frequencies", build_mosaic, "weather"),
    "treemap": ("Chapter 08", "Treemap", "px.treemap()", "Explore additive rainy-day observation counts", build_treemap, "weather"),
    "map": ("Chapter 09", "Markers and map symbols", "folium.Map(); folium.Marker(); folium.CircleMarker()", "Map uploaded climate statistics", build_map, "weather"),
    **{key: ("Chapter 08", title, function, objective, builder, "weather") for key, (title, function, objective, builder) in ADVANCED_CHARTS.items()},
}


def prepare_chart(chart_id: str, weather: pd.DataFrame, variable: str = "temperature_mean_c", aggregation: str = "annual", relationship_aggregation: str = "daily", hot_threshold: float = 35.0, wind_threshold: float = 50.0) -> tuple[pd.DataFrame, str, list[str], dict]:
    """Construct the chart input from the already filtered daily observations."""
    if chart_id not in REGISTRY:
        raise ValueError("Unknown chart")
    if weather.empty:
        raise ValueError("No observations for the selected dates and locations")
    if chart_id in ADVANCED_CHARTS:
        table, units, caveats = prepare_advanced(chart_id, weather, hot_threshold, wind_threshold)
        facts = {"threshold": hot_threshold if chart_id == "hot_days" else wind_threshold if chart_id == "strong_wind_days" else None}
        if chart_id in {"temperature_humidity", "rain_duration"}:
            x, y = ("relative_humidity_mean_pct", "temperature_mean_c") if chart_id == "temperature_humidity" else ("precipitation_hours", "precipitation_mm")
            facts["paired_observations"] = len(table)
            facts["pearson_r"] = float(table[x].corr(table[y])) if len(table) >= 3 and table[x].nunique() > 1 and table[y].nunique() > 1 else None
        return table, units, caveats, facts
    map_metrics = {"mean_daily_rain_mm", "rainy_day_pct", "temperature_range_c", "coverage_pct", "nasa_solar_radiation_kwh_m2_day"}
    if variable not in {"temperature_mean_c", "temperature_min_c", "temperature_max_c", "precipitation_mm", "wind_speed_max_kmh"} and not (chart_id == "map" and variable in set(CLIMATE_COLUMNS) | map_metrics):
        raise ValueError("Choose a supported weather measure")
    if aggregation not in {"daily", "monthly", "annual"}:
        raise ValueError("Choose daily, monthly, or annual grouping")
    if relationship_aggregation not in {"daily", "monthly"}:
        raise ValueError("Choose daily or monthly relationship grouping")
    location_name = lambda key: LOCATIONS.get(key, {}).get("name", key)
    region_name = lambda key: LOCATIONS.get(key, {}).get("region", "Uploaded location")
    caveats = ["ERA5 values are modeled estimates at representative coordinates, not province-wide station observations.", "Partial periods and missing values are never filled with invented observations."]
    facts = {}
    if chart_id == "year_compare":
        annual = period_table(weather, "annual")
        annual = annual[annual.complete_temperature & annual.temperature_mean_c.notna()].copy()
        annual = annual.groupby("location_id").filter(lambda group: group.year.nunique() >= 2)
        if annual.empty:
            raise ValueError("Select at least two calendar years with 90% valid daily temperatures at the same location")
        annual["location_name"] = annual.location_id.map(location_name)
        table = annual[["location_id", "location_name", "year", "temperature_mean_c", "temperature_valid_days", "expected_days", "temperature_coverage_pct"]].sort_values(["location_id", "year"])
        changes = []
        for key, group in table.groupby("location_id"):
            first, last = group.iloc[0], group.iloc[-1]
            changes.append({"location_id": key, "location_name": first.location_name, "first_year": int(first.year), "last_year": int(last.year), "change_c": round(float(last.temperature_mean_c - first.temperature_mean_c), 2)})
        return table, "°C", caveats + ["Only calendar years with at least 90% valid daily temperature values are shown. Gaps are not interpolated.", "This compares air temperature, not heat index; heat index also requires humidity."], {"annual_changes": changes}
    if chart_id in {"bar", "heatmap", "treemap"}:
        monthly = period_table(weather, "monthly")
        monthly = monthly[monthly.complete_rainfall & monthly.precipitation_mm.notna()].copy()
        if monthly.empty:
            raise ValueError("No months meet the 90% valid-rainfall threshold in this selection")
        if chart_id == "treemap":
            monthly["region"] = monthly.location_id.map(region_name)
            monthly["location_name"] = monthly.location_id.map(location_name)
            table = monthly.groupby(["region", "location_name", "month"], as_index=False).agg(rainy_days=("rainy_days", "sum"), valid_days=("precipitation_valid_days", "sum"))
            table = table[table.rainy_days.gt(0)].copy()
            table["month"] = table.month.astype(str).str.zfill(2)
            return table, "rainy days", caveats + ["Rainy means daily precipitation ≥1 mm. Counts are additive observations, not rainfall depths."], facts
        table = monthly.groupby(["location_id", "month"], as_index=False).agg(average_monthly_mm=("precipitation_mm", "mean"), valid_months=("precipitation_mm", "count"), years_covered=("year", lambda years: ",".join(map(str, sorted(years.unique())))))
        table["location_name"] = table.location_id.map(location_name)
        return table, "mm", caveats + ["Only months with at least 90% valid daily rainfall values contribute to the monthly comparison."], facts
    if chart_id == "line":
        if aggregation == "daily":
            table = weather.dropna(subset=[variable])[["location_id", "date", variable]].copy().rename(columns={variable: "measure_value"})
            table["period"] = table.date.dt.strftime("%Y-%m-%d")
            table["valid_days"] = 1
            table["expected_days"] = 1
        else:
            periods = period_table(weather, aggregation)
            measure = variable
            table = periods[["location_id", "period", "expected_days", measure, "temperature_valid_days", "precipitation_valid_days", "wind_valid_days"]].copy()
            table = table.rename(columns={measure: "measure_value"})
            count_col = "precipitation_valid_days" if variable == "precipitation_mm" else "wind_valid_days" if variable == "wind_speed_max_kmh" else "temperature_valid_days"
            table["valid_days"] = table[count_col]
            table = table.drop(columns=["temperature_valid_days", "precipitation_valid_days", "wind_valid_days"])
            if variable == "precipitation_mm":
                table = table[table.valid_days.ge(np.ceil(table.expected_days * 0.9))]
        table = table.dropna(subset=["measure_value"]).sort_values(["location_id", "period"])
        if table.empty:
            raise ValueError("No valid values for this variable and period")
        positions = {period: i for i, period in enumerate(sorted(table.period.unique()))}
        table["period_index"] = table.period.map(positions)
        table["location_name"] = table.location_id.map(location_name)
        table["variable"] = variable
        return table, "mm" if variable == "precipitation_mm" else "km/h" if variable == "wind_speed_max_kmh" else "°C", caveats + ["Rainfall totals require at least 90% valid days per full calendar period; valid-day counts and expected days appear in evidence."], facts
    if chart_id == "box":
        table = weather.dropna(subset=[variable])[["location_id", "date", variable]].copy().rename(columns={variable: "measure_value"})
        table["variable"] = variable
        table["location_name"] = table.location_id.map(location_name)
        return table, "mm" if variable == "precipitation_mm" else "km/h" if variable == "wind_speed_max_kmh" else "°C", caveats + ["Extreme values remain in the distribution; inspect flagged outliers before interpretation."], facts
    if chart_id in {"bubble", "rain_wind"}:
        x, y = ("precipitation_mm", "temperature_mean_c") if chart_id == "bubble" else ("precipitation_mm", "wind_speed_max_kmh")
        paired, correlation = correlation_table(weather, x, y, relationship_aggregation)
        if chart_id == "bubble":
            if relationship_aggregation == "daily":
                wind = weather[["location_id", "date", "wind_speed_max_kmh"]].copy()
                paired = paired.merge(wind, on=["location_id", "date"], how="inner")
            else:
                wind = period_table(weather, "monthly")[["location_id", "period", "wind_speed_max_kmh"]]
                paired = paired.merge(wind, on=["location_id", "period"], how="inner")
            paired = paired.dropna(subset=["wind_speed_max_kmh"])
            if len(paired) >= 3 and paired[x].nunique() > 1 and paired[y].nunique() > 1:
                correlation = float(paired[x].corr(paired[y]))
            else:
                correlation = None
        if paired.empty:
            raise ValueError("No paired observations with the selected variables")
        paired["location_name"] = paired.location_id.map(location_name)
        paired["aggregation"] = relationship_aggregation
        paired["pearson_r"] = correlation
        facts = {"pearson_r": correlation, "paired_observations": len(paired)}
        return paired, "mm / °C / km/h" if chart_id == "bubble" else "mm / km/h", caveats + ["Pearson correlation describes association in matched location-period observations, not causation.", "Monthly relationships include only periods with at least 90% valid temperature and rainfall days."], facts
    if chart_id == "mosaic":
        observed = weather.dropna(subset=["precipitation_mm"]).copy()
        observed["condition"] = np.where(observed.precipitation_mm.ge(RAINY_DAY_MM), "rainy", "dry")
        table = observed.groupby(["location_id", "condition"], as_index=False).size().rename(columns={"size": "count"})
        table["location_name"] = table.location_id.map(location_name)
        return table, "observed days", caveats + ["Rainy means daily precipitation ≥1 mm; missing rainfall days are excluded from both categories."], facts
    rows = []
    for key, group in weather.groupby("location_id"):
        loc = LOCATIONS.get(key)
        if not loc:
            continue
        valid_rain = int(group.precipitation_mm.count())
        temperature_range = (group.temperature_max_c - group.temperature_min_c).mean()
        measures = {
            "precipitation_mm": (group.precipitation_mm.sum(min_count=1), "mm total"),
            "mean_daily_rain_mm": (group.precipitation_mm.mean(), "mm/valid day"),
            "rainy_day_pct": (100 * group.precipitation_mm.ge(RAINY_DAY_MM).sum() / valid_rain if valid_rain else np.nan, "% valid rain days"),
            "temperature_range_c": (temperature_range, "°C mean daily range"),
            "coverage_pct": (100 * group.temperature_mean_c.count() / len(group), "% valid temperature records"),
        }
        if variable in measures:
            selected_measure, unit = measures[variable]
            valid_selected = valid_rain if variable in {"precipitation_mm", "mean_daily_rain_mm", "rainy_day_pct"} else int((group.temperature_max_c - group.temperature_min_c).count()) if variable == "temperature_range_c" else int(group.temperature_mean_c.count())
        else:
            if variable not in group or group[variable].count() == 0:
                continue
            selected_measure = group[variable].max() if variable in {"wind_speed_max_kmh", "wind_gust_max_kmh", "temperature_max_c"} else group[variable].mean()
            valid_selected = int(group[variable].count())
            unit = "%" if variable in {"relative_humidity_mean_pct", "cloud_cover_mean_pct"} else "hours" if variable in {"sunshine_duration_hours", "precipitation_hours"} else "km/h" if variable in {"wind_speed_max_kmh", "wind_gust_max_kmh"} else "kWh/m²/day" if variable == "nasa_solar_radiation_kwh_m2_day" else "MJ/m²" if variable == "shortwave_radiation_mj_m2" else "°"
            if variable.startswith("temperature_") or variable in {"apparent_temperature_mean_c", "dew_point_mean_c"}:
                unit = "°C"
        if pd.isna(selected_measure):
            continue
        rows.append({"location_id": key, "location_name": loc["name"], "province": loc["province"], "region": loc["region"], "latitude": loc["latitude"], "longitude": loc["longitude"], "period_start": str(group.date.min().date()), "period_end": str(group.date.max().date()), "model": ", ".join(sorted(group.model.astype(str).unique())), "observed_days": len(group), "temperature_value_c": group.temperature_mean_c.mean(), "temperature_mean_c": group.temperature_mean_c.mean(), "temperature_valid_days": int(group.temperature_mean_c.count()), "average_daily_rain_mm": group.precipitation_mm.mean(), "rain_valid_days": valid_rain, "selected_variable": variable, "selected_measure_value": selected_measure, "selected_measure_unit": unit, "selected_valid_days": valid_selected, "selected_missing_days": len(group) - valid_selected, "selected_coverage_pct": round(100 * valid_selected / len(group), 1)})
    table = pd.DataFrame(rows)
    if table.empty:
        raise ValueError("No selected map measure has valid observations at registered locations")
    reference = table.selected_measure_value.mean()
    table["difference_from_loaded_mean"] = table.selected_measure_value - reference
    return table, str(table.selected_measure_unit.iloc[0]), caveats + ["Comparison is against the mean of loaded sampling points with the same selected metric; it is not a regional average.", "Map circles use mean daily rainfall over valid days. OSM provides map tiles only. Unregistered coordinates are omitted."], facts


def render_chart(chart_id: str, table: pd.DataFrame) -> dict:
    builder = REGISTRY[chart_id][4]
    if chart_id == "map":
        return {"type": "html", "content": builder(table)}
    if chart_id == "treemap":
        return {"type": "plotly", "content": json.loads(builder(table).to_json())}
    with RENDER_LOCK:
        fig = builder(table)
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=145, bbox_inches="tight", facecolor="white")
        plt.close(fig)
    return {"type": "png", "content": base64.b64encode(buffer.getvalue()).decode("ascii")}


def interpretation(chart_id: str, table: pd.DataFrame, caveats: list[str], facts: dict) -> dict:
    count = len(table)
    if chart_id in ADVANCED_CHARTS:
        if chart_id in {"temperature_humidity", "rain_duration"}:
            r = facts["pearson_r"]
            finding = f"{count} paired daily observations; Pearson r = {r:.3f}." if r is not None else f"{count} paired daily observations; Pearson r is unavailable because values are constant or too few."
        elif chart_id in {"hot_days", "strong_wind_days"}:
            valid = int(table.valid_days.sum())
            hits = int(table.days_above_threshold.sum())
            finding = f"{hits} of {valid} valid daily observations ({100 * hits / valid:.1f}%) met the user-selected threshold of {facts['threshold']:g} {table.threshold_unit.iloc[0]}." if valid else "No valid daily observations met the field requirement."
        elif chart_id == "coverage":
            available = table[table.field_status.eq("available")]
            finding = f"{len(available)} available location-year-field cells; {int(table.field_status.eq('absent').sum())} cells have an absent optional field. Coverage uses calendar days as denominator."
        elif chart_id == "temperature_range":
            finding = f"Median daily maximum-minus-minimum temperature is {table.temperature_range_c.median():.1f} °C from {count} valid paired days."
        else:
            valid = table[table.period_status.eq("≥90% valid days") & table.precipitation_mm.notna()]
            finding = f"{len(valid)} calendar periods have at least 90% valid rainfall days; {int(table.period_status.ne('≥90% valid days').sum())} periods are partial or below coverage threshold."
        return {"label": "Statistical Interpretation", "shows": REGISTRY[chart_id][3], "findings": finding, "evidence": f"The evidence table has {count} chart-input rows; the full table is downloadable.", "relevance": "Compare observations at the registered sampling points and examine data coverage before drawing conclusions.", "limitations": caveats}
    if chart_id in {"bubble", "rain_wind"}:
        r = facts["pearson_r"]
        if r is None:
            finding = f"Pearson correlation is unavailable for {count} paired observations because the sample is too small or a variable is constant."
        else:
            strength = "weak" if abs(r) < 0.3 else "moderate" if abs(r) < 0.7 else "strong"
            finding = f"Pearson r = {r:.3f} across {count} matched observations: a {strength} {'positive' if r > 0 else 'negative'} association."
    elif chart_id in {"bar", "heatmap"}:
        leader = table.sort_values("average_monthly_mm", ascending=False).iloc[0]
        finding = f"The highest shown complete-month mean is {leader.average_monthly_mm:.1f} mm at {leader.location_name} in month {int(leader.month)}, covering year(s) {leader.years_covered} ({int(leader.valid_months)} valid months)."
    elif chart_id == "year_compare":
        changes = facts["annual_changes"]
        finding = "; ".join(f"{item['location_name']}: {item['first_year']} to {item['last_year']} changed by {item['change_c']:+.2f} °C" for item in changes)
        finding += ". Each point is a mean of valid daily temperatures in a calendar year."
    elif chart_id == "line":
        leader = table.sort_values("measure_value", ascending=False).iloc[0]
        unit = "mm" if leader.variable == "precipitation_mm" else "km/h" if leader.variable == "wind_speed_max_kmh" else "°C"
        finding = f"The highest shown period value is {leader.measure_value:.1f} {unit} at {leader.location_name} in {leader.period}, with {int(leader.valid_days)} of {int(leader.expected_days)} expected days."
    elif chart_id == "box":
        unit = "mm" if table.variable.iloc[0] == "precipitation_mm" else "km/h" if table.variable.iloc[0] == "wind_speed_max_kmh" else "°C"
        finding = f"The median of {count} valid daily values is {table.measure_value.median():.1f} {unit}; standard deviation is {table.measure_value.std():.1f} {unit}."
    elif chart_id == "treemap":
        leader = table.sort_values("rainy_days", ascending=False).iloc[0]
        finding = f"The largest shown group is {leader.location_name}, month {leader.month}: {int(leader.rainy_days)} rainy-day observations."
    elif chart_id == "mosaic":
        rainy = int(table.loc[table.condition.eq("rainy"), "count"].sum())
        total = int(table["count"].sum())
        finding = f"{rainy} of {total} valid precipitation days ({100 * rainy / total:.1f}%) met the 1 mm rainy-day threshold."
    else:
        leader = table.sort_values("selected_measure_value", ascending=False).iloc[0]
        finding = f"The highest mapped {leader.selected_variable} is {leader.selected_measure_value:.1f} {leader.selected_measure_unit} at {leader.location_name}, from {int(leader.selected_valid_days)} valid observations ({leader.selected_coverage_pct:.1f}% of loaded days)."
    return {"label": "Statistical Interpretation", "shows": REGISTRY[chart_id][3], "findings": finding, "evidence": f"The evidence table has {count} chart-input rows; the first 30 appear here and the full table is downloadable.", "relevance": "Compare locations and periods for further climate research; association does not establish a cause.", "limitations": caveats}


def code_for(chart_id: str) -> dict:
    entry = REGISTRY[chart_id]
    highlights = {"bar": ["sns.barplot"], "line": ["sns.lineplot"], "year_compare": ["sns.lineplot"], "box": ["sns.boxplot"], "bubble": ["sns.scatterplot"], "rain_wind": ["sns.scatterplot"], "heatmap": ["pivot_table", "sns.heatmap"], "mosaic": ["mosaic("], "treemap": ["px.treemap"], "map": ["folium.Map", "folium.Marker", "folium.CircleMarker"]}
    preparation = inspect.getsource(prepare_advanced) + "\n\n" + inspect.getsource(prepare_chart) if chart_id in ADVANCED_CHARTS else inspect.getsource(prepare_chart)
    return {"visualization": inspect.getsource(entry[4]), "preparation": preparation, "topic": {"chapter": entry[0], "topic": entry[1], "library_function": entry[2], "objective": entry[3]}, "highlights": highlights.get(chart_id, [entry[2].split(";")[-1].strip().replace("()", "")])}
