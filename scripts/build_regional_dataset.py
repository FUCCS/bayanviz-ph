"""Build the checked, nationwide daily CSV/Parquet files from supplied provider JSON.

Run from any directory: python scripts/build_regional_dataset.py
No network calls or synthetic observations are used.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "dataset"
REGIONS = json.loads((DATA / "regions.json").read_text())
DATES = pd.date_range("2016-01-01", "2025-12-31").strftime("%Y-%m-%d").tolist()
WEATHER_FIELDS = {
    "temperature_mean_c": ("temperature_2m_mean", "°C"),
    "temperature_min_c": ("temperature_2m_min", "°C"),
    "temperature_max_c": ("temperature_2m_max", "°C"),
    "precipitation_mm": ("precipitation_sum", "mm"),
    "wind_speed_max_kmh": ("wind_speed_10m_max", "km/h"),
    "relative_humidity_mean_pct": ("relative_humidity_2m_mean", "%"),
    "apparent_temperature_mean_c": ("apparent_temperature_mean", "°C"),
    "cloud_cover_mean_pct": ("cloud_cover_mean", "%"),
    "sunshine_duration_hours": ("sunshine_duration", "s"),
    "wind_gust_max_kmh": ("wind_gusts_10m_max", "km/h"),
    "precipitation_hours": ("precipitation_hours", "h"),
    "wind_direction_dominant_deg": ("wind_direction_10m_dominant", "°"),
    "dew_point_mean_c": ("dew_point_2m_mean", "°C"),
    "shortwave_radiation_mj_m2": ("shortwave_radiation_sum", "MJ/m²"),
}
NASA_FIELDS = {
    "nasa_solar_radiation": ("ALLSKY_SFC_SW_DWN", "kW-hr/m^2/day"),
    "nasa_temperature_mean_c": ("T2M", "C"),
    "nasa_relative_humidity_pct": ("RH2M", "%"),
    "nasa_wind_speed_m_s": ("WS10M", "m/s"),
}


def source_files(folder: str) -> list[tuple[Path, dict, float, float]]:
    found = []
    for path in sorted((DATA / folder).glob("*.json")):
        payload = json.loads(path.read_text())
        latitude, longitude = ((payload["latitude"], payload["longitude"]) if folder == "open-meteo"
                               else tuple(reversed(payload["geometry"]["coordinates"][:2])))
        found.append((path, payload, float(latitude), float(longitude)))
    if len(found) != len(REGIONS):
        raise ValueError(f"Expected {len(REGIONS)} {folder} files, found {len(found)}")
    return found


def pair_files(files: list[tuple[Path, dict, float, float]]) -> dict[str, tuple[Path, dict, float, float]]:
    paired = {}
    used = set()
    for region in REGIONS:
        ranked = sorted(files, key=lambda item: (item[2] - region["latitude"]) ** 2 + (item[3] - region["longitude"]) ** 2)
        path, payload, latitude, longitude = ranked[0]
        if path in used or abs(latitude - region["latitude"]) > 0.75 or abs(longitude - region["longitude"]) > 0.75:
            raise ValueError(f"No unique nearby source for {region['id']}")
        used.add(path)
        paired[region["id"]] = (path, payload, latitude, longitude)
    return paired


def numeric(values: list, length: int) -> pd.Series:
    if not isinstance(values, list) or len(values) != length:
        raise ValueError("Source has incomplete daily values")
    return pd.to_numeric(pd.Series(values), errors="coerce").replace([-999, -9999], np.nan)


def build() -> dict:
    weather_files = pair_files(source_files("open-meteo"))
    nasa_files = pair_files(source_files("nasa_power"))
    weather_parts, nasa_parts, provenance = [], [], []
    for region in REGIONS:
        key = region["id"]
        weather_path, weather_json, weather_lat, weather_lon = weather_files[key]
        nasa_path, nasa_json, nasa_lat, nasa_lon = nasa_files[key]
        daily = weather_json["daily"]
        if daily.get("time") != DATES or weather_json.get("utc_offset_seconds") != 0:
            raise ValueError(f"Open-Meteo dates or UTC standard differ for {key}")
        weather = pd.DataFrame({"location_id": key, "date": DATES, "model": "era5", "source": "Open-Meteo ERA5 archive",
                                "analysis_time_standard": "UTC", "latitude": weather_lat, "longitude": weather_lon,
                                "openmeteo_grid_latitude": weather_lat, "openmeteo_grid_longitude": weather_lon})
        for target, (provider, unit) in WEATHER_FIELDS.items():
            if weather_json["daily_units"].get(provider) != unit:
                raise ValueError(f"Unexpected Open-Meteo {provider} unit for {key}")
            values = numeric(daily.get(provider), len(DATES))
            weather[target] = values / 3600 if target == "sunshine_duration_hours" else values
        weather_parts.append(weather)

        if nasa_json["header"].get("time_standard") != "UTC":
            raise ValueError(f"NASA date standard is not UTC for {key}")
        parameters = nasa_json["properties"]["parameter"]
        nasa = pd.DataFrame({"location_id": key, "date": DATES, "source": "NASA POWER Daily",
                             "time_standard": "UTC", "latitude": nasa_lat, "longitude": nasa_lon})
        for target, (provider, unit) in NASA_FIELDS.items():
            if nasa_json["parameters"].get(provider, {}).get("units") != unit:
                raise ValueError(f"Unexpected NASA {provider} unit for {key}")
            values = parameters.get(provider)
            if not isinstance(values, dict) or list(values) != [day.replace("-", "") for day in DATES]:
                raise ValueError(f"NASA daily dates differ for {key}: {provider}")
            nasa[target] = numeric(list(values.values()), len(DATES))
        nasa_parts.append(nasa)
        provenance.append({"location_id": key, "weather_file": str(weather_path.relative_to(ROOT)),
                           "weather_sha256": hashlib.sha256(weather_path.read_bytes()).hexdigest(),
                           "nasa_file": str(nasa_path.relative_to(ROOT)),
                           "nasa_sha256": hashlib.sha256(nasa_path.read_bytes()).hexdigest(),
                           "weather_grid": [weather_lat, weather_lon], "nasa_point": [nasa_lat, nasa_lon]})
    outputs = {}
    for label, parts in (("open_meteo", weather_parts), ("nasa_power", nasa_parts)):
        frame = pd.concat(parts, ignore_index=True).sort_values(["location_id", "date"]).reset_index(drop=True)
        if len(frame) != len(REGIONS) * len(DATES) or frame.duplicated(["location_id", "date"]).any():
            raise ValueError(f"{label} rows do not have a validated one-to-one regional daily key")
        csv_path = DATA / f"regional_{label}.csv"
        parquet_path = DATA / f"regional_{label}.parquet"
        frame.to_csv(csv_path, index=False)
        frame.to_parquet(parquet_path, index=False)
        outputs[label] = {"rows": len(frame), "columns": list(frame.columns), "missing_values": {col: int(frame[col].isna().sum()) for col in frame},
                          "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(), "parquet_sha256": hashlib.sha256(parquet_path.read_bytes()).hexdigest()}
    manifest = {"period": [DATES[0], DATES[-1]], "time_standard": "UTC", "regional_points": len(REGIONS), "sources": provenance, "outputs": outputs}
    (DATA / "regional_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    result = build()
    print(f"Built {result['regional_points']} regions: {result['outputs']['open_meteo']['rows']} weather and {result['outputs']['nasa_power']['rows']} NASA daily rows")
