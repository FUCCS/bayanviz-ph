from __future__ import annotations

import base64
import html
import io
import inspect
import json
import threading
from datetime import datetime

import folium
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.express as px
import seaborn as sns
from folium.plugins import MarkerCluster
from statsmodels.graphics.mosaicplot import mosaic

from .catalog import LOCATIONS
from .processing import monthly_rainfall

RENDER_LOCK = threading.Lock()
PALETTE = {"pharmacy":"#1e88a8", "clinic":"#2774b7", "bank":"#a55252", "atm":"#ca8342", "retail":"#7083b8", "school":"#679875"}


def build_bar(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.barplot(data=table, x="province", y="count", hue="category", palette=PALETTE, ax=ax, errorbar=None)
    ax.set(xlabel="Sample area", ylabel="Mapped OSM facilities", title="Mapped facilities by area and category")
    ax.tick_params(axis="x", rotation=20)
    ax.legend(title="Category", bbox_to_anchor=(1.01, 1), loc="upper left")
    fig.tight_layout()
    return fig


def build_line(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 5))
    variable = str(table.variable.iloc[0])
    unit = "mm" if variable == "precipitation_mm" else "°C"
    measure = "daily precipitation" if variable == "precipitation_mm" else "daily " + variable.replace("temperature_", "").replace("_c", "") + " temperature"
    monthly = "period_index" in table
    x = "period_index" if monthly else "year"
    sns.lineplot(data=table, x=x, y="measure_value", hue="location_name", marker="o", estimator=None, ax=ax)
    ax.set(xlabel="Year / month" if monthly else "Year", ylabel=f"Period mean of {measure} ({unit})", title=f"{'Monthly' if monthly else 'Annual'} mean of {measure} at sample points")
    if monthly:
        periods = table[["period_index", "year", "month"]].drop_duplicates().sort_values("period_index").iloc[::3]
        ax.set_xticks(periods.period_index, [f"{int(row.year)}-{int(row.month):02d}" for row in periods.itertuples()])
        ax.tick_params(axis="x", rotation=35)
    else:
        ax.set_xticks(sorted(table.year.unique()))
    ax.legend(title="Sample location", bbox_to_anchor=(1.01, 1), loc="upper left")
    fig.tight_layout()
    return fig


def build_box(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 5))
    variable = str(table.variable.iloc[0])
    unit = "mm" if variable == "precipitation_mm" else "°C"
    measure = "precipitation" if variable == "precipitation_mm" else variable.replace("temperature_", "").replace("_c", "") + " temperature"
    sns.boxplot(data=table, x="location_name", y="measure_value", color="#5d9fba", ax=ax)
    ax.set(xlabel="Sample location", ylabel=f"Daily {measure} ({unit})", title=f"Daily {measure} distributions")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    return fig


def build_bubble(table: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.scatterplot(data=table, x="precipitation_mm", y="temperature_mean_c", size="facility_count", sizes=(70, 800), hue="location_name", alpha=0.68, ax=ax)
    ax.set(xlabel="Annual precipitation at sample point (mm)", ylabel="Mean daily temperature (°C)", title="Weather and mapped facility counts")
    ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=8)
    fig.tight_layout()
    return fig


def build_heatmap(table: pd.DataFrame):
    grid = table.pivot_table(index="location_name", columns="month", values="average_monthly_mm", aggfunc="first")
    fig, ax = plt.subplots(figsize=(9, 4.8))
    sns.heatmap(grid, cmap="YlGnBu", annot=True, fmt=".0f", linewidths=0.4, cbar_kws={"label":"Average complete-month total (mm)"}, ax=ax)
    ax.set(xlabel="Month", ylabel="Sample location", title="Climatological monthly precipitation")
    fig.tight_layout()
    return fig


def build_mosaic(table: pd.DataFrame):
    counts = {(str(row["province"]), str(row["category"])): int(row["count"]) for row in table.to_dict("records")}
    fig, ax = plt.subplots(figsize=(10, 5))
    mosaic(counts, ax=ax, labelizer=lambda key: key[-1] if counts[key] >= 2 else "", properties=lambda key: {"color": PALETTE.get(key[-1], "#7294aa"), "alpha": 0.85})
    ax.set_title("Facility category composition by sample area")
    fig.tight_layout()
    return fig


def build_treemap(table: pd.DataFrame):
    fig = px.treemap(table, path=["region", "province", "category"], values="count", color="count", color_continuous_scale="Blues", title="Mapped facilities by region, area, and category")
    fig.update_layout(margin=dict(t=55, l=12, r=12, b=12), coloraxis_colorbar_title="Facilities")
    return fig


def build_map(table: pd.DataFrame):
    center = [float(table.latitude.mean()), float(table.longitude.mean())]
    map_obj = folium.Map(location=center, zoom_start=8, tiles="OpenStreetMap", control_scale=True)
    weather_layer = folium.FeatureGroup(name="Weather sample points", show=True)
    facility_layer = folium.FeatureGroup(name="OSM facilities", show=True)
    cluster = MarkerCluster().add_to(facility_layer)
    for row in table.itertuples():
        lat, lon = float(row.latitude), float(row.longitude)
        label = html.escape(str(row.name))
        if row.kind == "weather":
            value = float(row.value_c)
            radius = float(np.clip(4 + 0.5 * (value - 15), 5, 12))
            popup = f"<b>{label}</b><br>ERA5 sample location · {html.escape(str(row.province))}<br>Mean daily temperature: {value:.1f} °C ({int(row.valid_days)} valid days)"
            folium.Marker([lat, lon], popup=folium.Popup(popup, max_width=260), tooltip=label, icon=folium.Icon(color="blue", icon="cloud")).add_to(weather_layer)
            folium.CircleMarker([lat, lon], radius=radius, color="#0d6b9a", fill=True, fill_opacity=0.5, popup=folium.Popup(popup, max_width=260)).add_to(weather_layer)
        else:
            category = html.escape(str(row.category))
            popup = f"<b>{label}</b><br>{category} · {html.escape(str(row.province))}<br>© OpenStreetMap contributors"
            folium.Marker([lat, lon], popup=folium.Popup(popup, max_width=260), tooltip=category, icon=folium.Icon(color="green", icon="info-sign")).add_to(cluster)
    weather_layer.add_to(map_obj)
    facility_layer.add_to(map_obj)
    folium.LayerControl(collapsed=False).add_to(map_obj)
    map_obj.fit_bounds([[float(table.latitude.min()), float(table.longitude.min())], [float(table.latitude.max()), float(table.longitude.max())]])
    legend = '<div style="position:fixed;bottom:22px;left:18px;z-index:9999;background:white;padding:10px 13px;border:1px solid #b7cbd8;border-radius:7px;font:12px sans-serif;color:#163a51;box-shadow:0 2px 8px #0002"><b>Map symbols</b><br>Blue: ERA5 sample point<br>Green: mapped OSM feature<br>Blue circle radius 5–12 px: mean daily temperature (°C)</div>'
    map_obj.get_root().html.add_child(folium.Element(legend))
    return map_obj.get_root().render()


REGISTRY = {
    "bar": ("Chapter 08", "Seaborn bar graph", "sns.barplot()", "Compare mapped facility categories", build_bar, "facilities"),
    "line": ("Chapter 08", "Seaborn line graph", "sns.lineplot()", "Compare annual sampled temperatures", build_line, "weather"),
    "box": ("Chapter 08", "Seaborn box plot", "sns.boxplot()", "Inspect daily temperature distributions", build_box, "weather"),
    "bubble": ("Chapter 08", "Bubble chart", "sns.scatterplot()", "Compare two weather variables and facility counts", build_bubble, "both"),
    "heatmap": ("Chapter 08", "Heatmap", "pivot_table(); sns.heatmap()", "Inspect monthly rainfall seasonality", build_heatmap, "weather"),
    "mosaic": ("Chapter 08", "Mosaic plot", "mosaic()", "Compare categorical facility composition", build_mosaic, "facilities"),
    "treemap": ("Chapter 08", "Treemap", "px.treemap()", "Explore additive facility counts in a hierarchy", build_treemap, "facilities"),
    "map": ("Chapter 09", "Markers and map symbols", "folium.Marker(); folium.CircleMarker()", "Locate samples and mapped facilities", build_map, "both"),
}


def prepare_chart(chart_id: str, weather: pd.DataFrame, facilities: pd.DataFrame, variable: str = "temperature_mean_c", aggregation: str = "annual") -> tuple[pd.DataFrame, str, list[str]]:
    """Create the exact chart input table from normalized data."""
    if chart_id not in REGISTRY:
        raise ValueError("Unknown chart")
    if variable not in {"temperature_mean_c", "temperature_min_c", "temperature_max_c", "precipitation_mm"}:
        raise ValueError("Choose a supported weather measure")
    if aggregation not in {"annual", "monthly"}:
        raise ValueError("Choose annual or monthly line grouping")
    needs = REGISTRY[chart_id][5]
    if needs in ("weather", "both") and weather.empty:
        raise ValueError("Weather data unavailable for this chart")
    if needs in ("facilities", "both") and facilities.empty and chart_id != "map":
        raise ValueError("Mapped facility data unavailable for this chart")
    if chart_id in ("bar", "mosaic", "treemap"):
        fields = ["region", "province", "category"] if chart_id == "treemap" else ["province", "category"]
        table = facilities.groupby(fields, as_index=False).size().rename(columns={"size": "count"})
        return table, "facilities", ["Current OSM mapping is incomplete; counts are mapped features, not all facilities.", "Snapshot areas were assigned by nearest sample center, not verified administrative boundaries."]
    if chart_id == "line":
        df = weather.dropna(subset=[variable]).copy()
        df["year"] = df.date.dt.year
        keys = ["location_id", "year"]
        if aggregation == "monthly":
            df["month"] = df.date.dt.month
            keys.append("month")
        table = df.groupby(keys, as_index=False).agg(measure_value=(variable, "mean"), valid_days=(variable, "count"))
        if aggregation == "monthly":
            table["period_index"] = table.year + (table.month - 1) / 12
        table["variable"] = variable
        table["location_name"] = table.location_id.map(lambda key: LOCATIONS.get(key, {}).get("name", key))
        return table, "mm" if variable == "precipitation_mm" else "°C", ["Each plotted point is the mean of valid daily ERA5 estimates at a sample location for its shown period; incomplete periods are not annualized."]
    if chart_id == "box":
        table = weather.dropna(subset=[variable])[["location_id", "date", variable]].copy().rename(columns={variable: "measure_value"})
        table["variable"] = variable
        table["location_name"] = table.location_id.map(lambda key: LOCATIONS.get(key, {}).get("name", key))
        return table, "mm" if variable == "precipitation_mm" else "°C", ["Daily values are ERA5 estimates at sample points, not province-wide station measurements."]
    if chart_id == "heatmap":
        months = monthly_rainfall(weather)
        complete = months[months.complete & months.total_mm.notna()]
        table = complete.groupby(["location_id", "month"], as_index=False).agg(average_monthly_mm=("total_mm", "mean"), valid_months=("total_mm", "count"))
        table["location_name"] = table.location_id.map(lambda key: LOCATIONS.get(key, {}).get("name", key))
        return table, "mm", ["Only months with all expected daily precipitation values contribute; incomplete months are excluded."]
    if chart_id == "bubble":
        df = weather.copy()
        df["year"] = df.date.dt.year
        annual = df.groupby(["location_id", "year"], as_index=False).agg(precipitation_mm=("precipitation_mm", lambda s: s.sum(min_count=1)), temperature_mean_c=("temperature_mean_c", "mean"), valid_days=("precipitation_mm", "count"))
        annual["expected_days"] = annual.year.map(lambda y: 366 if pd.Timestamp(int(y), 12, 31).is_leap_year else 365)
        annual = annual[annual.valid_days == annual.expected_days].dropna(subset=["precipitation_mm", "temperature_mean_c"])
        counts = facilities.groupby("province").size()
        annual["province"] = annual.location_id.map(lambda key: LOCATIONS.get(key, {}).get("province", ""))
        annual["location_name"] = annual.location_id.map(lambda key: LOCATIONS.get(key, {}).get("name", key))
        annual["facility_count"] = annual.province.map(counts).fillna(0).astype(int)
        return annual, "mm / °C / mapped count", ["Bubble area is the current mapped facility count, not population or risk.", "Facility area labels were assigned by nearest sample center; boundaries were not checked.", "Historical ERA5 weather and current OSM features have different reference dates; the display implies no causation."]
    rows = []
    for key in weather.location_id.unique():
        if key in LOCATIONS:
            loc = LOCATIONS[key]
            valid = weather[(weather.location_id == key) & weather.temperature_mean_c.notna()]
            if valid.empty:
                continue
            rows.append({"kind":"weather", "name":loc["name"], "category":"weather sample", "province":loc["province"], "latitude":loc["latitude"], "longitude":loc["longitude"], "value_c":valid.temperature_mean_c.mean(), "valid_days":len(valid)})
    for row in facilities.head(120).itertuples():
        rows.append({"kind":"facility", "name":getattr(row, "name", "") or str(row.category), "category":row.category, "province":row.province, "latitude":row.latitude, "longitude":row.longitude, "value_c":np.nan, "valid_days":np.nan})
    return pd.DataFrame(rows), "locations / °C", [f"Map displays at most 120 of {len(facilities)} loaded facilities; counts elsewhere use all loaded records.", "Weather circles use radius = clamp(4 + 0.5 × (mean daily °C − 15), 5, 12) pixels; they do not represent risk.", "Weather points are representative locations; facility coverage and nearest-center area assignments are partial."]


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


def interpretation(chart_id: str, table: pd.DataFrame, caveats: list[str]) -> dict:
    count = len(table)
    if chart_id in ("bar", "mosaic", "treemap") and count:
        leader = table.sort_values("count", ascending=False).iloc[0]
        finding = f"The largest shown group is {leader['category']} in {leader['province']}: {int(leader['count'])} mapped features."
    elif chart_id == "heatmap" and count:
        leader = table.sort_values("average_monthly_mm", ascending=False).iloc[0]
        finding = f"The largest complete-month average shown is {leader['average_monthly_mm']:.1f} mm at {leader['location_name']} in month {int(leader['month'])}."
    elif chart_id == "line" and count:
        leader = table.sort_values("measure_value", ascending=False).iloc[0]
        period = f"{int(leader['year'])}-{int(leader['month']):02d}" if "month" in table else str(int(leader['year']))
        unit = "mm" if leader["variable"] == "precipitation_mm" else "°C"
        finding = f"The highest sampled period mean shown is {leader['measure_value']:.1f} {unit} at {leader['location_name']} in {period}, based on {int(leader['valid_days'])} valid days."
    elif chart_id == "box" and count:
        median = table.measure_value.median()
        unit = "mm" if table.variable.iloc[0] == "precipitation_mm" else "°C"
        finding = f"Across {count} valid daily samples, the median plotted value is {median:.1f} {unit}; the observed range is {table.measure_value.min():.1f}–{table.measure_value.max():.1f} {unit}."
    elif chart_id == "bubble" and count:
        leader = table.sort_values("precipitation_mm", ascending=False).iloc[0]
        finding = f"The largest complete-year precipitation total shown is {leader['precipitation_mm']:.1f} mm at {leader['location_name']} in {int(leader['year'])}; its bubble represents {int(leader['facility_count'])} currently mapped facilities."
    else:
        finding = f"The map displays {count} locations or features in this selection."
    return {
        "label": "Statistical Interpretation",
        "shows": REGISTRY[chart_id][3],
        "findings": finding,
        "evidence": f"The evidence table contains {count} chart-input rows; the first 30 are shown here and the full table is downloadable.",
        "relevance": "Use this comparison to form questions for local planning or further field verification.",
        "limitations": caveats,
    }


def code_for(chart_id: str) -> dict:
    entry = REGISTRY[chart_id]
    highlights = {
        "bar": ["sns.barplot", "groupby"], "line": ["sns.lineplot", "groupby"],
        "box": ["sns.boxplot"], "bubble": ["sns.scatterplot", "groupby"],
        "heatmap": ["pivot_table", "sns.heatmap"], "mosaic": ["mosaic(", "groupby"],
        "treemap": ["px.treemap", "groupby"], "map": ["folium.Marker", "folium.CircleMarker"],
    }
    return {"visualization": inspect.getsource(entry[4]), "preparation": inspect.getsource(prepare_chart), "topic": {"chapter": entry[0], "topic": entry[1], "library_function": entry[2], "objective": entry[3]}, "highlights": highlights[chart_id]}
