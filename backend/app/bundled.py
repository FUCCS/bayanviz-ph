"""Validated, shared nationwide files built from the supplied provider responses."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .catalog import LOCATIONS, ROOT
from .multisource import merge_sources, normalize_nasa, validate_sources
from .processing import date_bounds, prepare_weather, validate_coordinates

DATA = ROOT / "dataset"


@dataclass
class RegionalBundle:
    weather: pd.DataFrame
    nasa: pd.DataFrame
    merged: pd.DataFrame
    metadata: dict
    report: dict
    quality: dict


def manifest() -> dict:
    return json.loads((DATA / "regional_manifest.json").read_text())


def summary() -> dict:
    info = manifest()
    return {"locations": [row["location_id"] for row in info["sources"]],
            "observation_period": info["period"], "weather_rows": info["outputs"]["open_meteo"]["rows"],
            "nasa_rows": info["outputs"]["nasa_power"]["rows"], "time_standard": info["time_standard"]}


@lru_cache(maxsize=1)
def load_bundle() -> RegionalBundle:
    info = manifest()
    if info["time_standard"] != "UTC" or info["period"] != ["2016-01-01", "2025-12-31"]:
        raise ValueError("Bundled regional manifest has an unsupported date standard or period")
    frames = {}
    for label in ("open_meteo", "nasa_power"):
        path = DATA / f"regional_{label}.parquet"
        if hashlib.sha256(path.read_bytes()).hexdigest() != info["outputs"][label]["parquet_sha256"]:
            raise ValueError(f"Bundled {label} file does not match its checked manifest")
        frame = pd.read_parquet(path)
        if len(frame) != info["outputs"][label]["rows"] or frame.duplicated(["location_id", "date"]).any():
            raise ValueError(f"Bundled {label} has missing or duplicate regional daily keys")
        frames[label] = frame
    requested = [row["location_id"] for row in info["sources"]]
    if len(requested) != 18 or set(requested) - set(LOCATIONS):
        raise ValueError("Bundled files do not match the 18 registered regional points")
    weather = prepare_weather(frames["open_meteo"])
    if len(weather) != len(frames["open_meteo"]) or set(weather.location_id) != set(requested):
        raise ValueError("Bundled weather failed regional key validation")
    validate_coordinates(weather, "Bundled Open-Meteo")
    raw_nasa = frames["nasa_power"]
    raw_nasa["date"] = pd.to_datetime(raw_nasa.date, errors="raise")
    if set(raw_nasa.location_id) != set(requested):
        raise ValueError("Bundled NASA locations differ from weather locations")
    validate_coordinates(raw_nasa, "Bundled NASA POWER")
    if set(zip(weather.location_id, weather.date)) != set(zip(raw_nasa.location_id, raw_nasa.date)):
        raise ValueError("Bundled weather and NASA dates are not one-to-one compatible")
    metadata = {item["location_id"]: {"time_standard": "UTC", "dataset_version": "NASA POWER supplied archive",
                "grid_latitude": item["nasa_point"][0], "grid_longitude": item["nasa_point"][1],
                "parameter_metadata": {"ALLSKY_SFC_SW_DWN": {"units": "kW-hr/m^2/day"}, "T2M": {"units": "C"},
                                       "RH2M": {"units": "%"}, "WS10M": {"units": "m/s"}}} for item in info["sources"]}
    nasa, transform = normalize_nasa(raw_nasa, metadata)
    report = validate_sources(weather, nasa, metadata, requested)
    report.update({"providers": ["Bundled Open-Meteo ERA5", "Bundled NASA POWER"],
                   "primary_origin": "supplied-provider-files", "secondary_origin": "supplied-provider-files",
                   "unit_transformation": transform, "source_files": info["sources"]})
    merged, quality = merge_sources(weather, nasa, report)
    if len(merged) != len(weather):
        raise ValueError("Bundled one-to-one merge changed the primary row count")
    return RegionalBundle(weather, nasa, merged, metadata, report, quality)
