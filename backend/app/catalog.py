from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
REGISTRY = json.loads((DATA / "reference" / "locations.json").read_text())
LOCATIONS = {row["id"]: row for row in REGISTRY["locations"]}
WEATHER_FILE = DATA / "samples" / "weather_era5_sample.csv"


def weather_frame() -> pd.DataFrame:
    if not WEATHER_FILE.exists():
        return pd.DataFrame(columns=["location_id", "date", "model", "temperature_mean_c", "temperature_min_c", "temperature_max_c", "precipitation_mm", "wind_speed_max_kmh"])
    return pd.read_csv(WEATHER_FILE)


def manifest(kind: str) -> dict:
    path = DATA / "manifests" / f"{kind}.json"
    return json.loads(path.read_text()) if path.exists() else {"status": "Unavailable"}
