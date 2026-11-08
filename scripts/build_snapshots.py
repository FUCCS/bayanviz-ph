"""Build a small, real, explicitly partial presentation snapshot.

Run from the repository root with: python scripts/build_snapshots.py
This deliberately refreshes the five-point climate presentation snapshot.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.catalog import LOCATIONS
from app.processing import prepare_weather

DATA = ROOT / "data"
USER_AGENT = "BayanVizPH/0.1 educational snapshot preparation"


def write_manifest(kind: str, file: Path, source: str, period: str, coverage: str, history: list[str]) -> None:
    manifest = {
        "schema_version": "1.0", "source": source, "license": "See current Open-Meteo terms and ERA5/ECMWF attribution",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "observation_period": period,
        "geographic_coverage": coverage, "row_count": len(pd.read_csv(file)),
        "checksum_sha256": hashlib.sha256(file.read_bytes()).hexdigest(), "transformation_history": history,
        "status": "Prepared Real Dataset", "coverage_status": "Partial coverage",
    }
    (DATA / "manifests" / f"{kind}.json").write_text(json.dumps(manifest, indent=2) + "\n")


async def weather(client: httpx.AsyncClient) -> None:
    frames = []
    for key, loc in LOCATIONS.items():
        response = await client.get("https://archive-api.open-meteo.com/v1/archive", params={
            "latitude": loc["latitude"], "longitude": loc["longitude"],
            "start_date": "2024-01-01", "end_date": "2025-12-31", "daily": "temperature_2m_mean,temperature_2m_min,temperature_2m_max,precipitation_sum,wind_speed_10m_max",
            "timezone": "Asia/Manila", "models": "era5",
        }, timeout=90)
        response.raise_for_status()
        daily = response.json()["daily"]
        frames.append(pd.DataFrame({
            "location_id": key, "date": daily["time"], "model": "era5",
            "temperature_mean_c": daily.get("temperature_2m_mean"), "temperature_min_c": daily.get("temperature_2m_min"),
            "temperature_max_c": daily.get("temperature_2m_max"), "precipitation_mm": daily.get("precipitation_sum"),
            "wind_speed_max_kmh": daily.get("wind_speed_10m_max"),
        }))
    output = DATA / "samples" / "weather_era5_sample.csv"
    prepare_weather(pd.concat(frames, ignore_index=True)).drop(columns=["outlier_flag"]).to_csv(output, index=False)
    write_manifest("weather", output, "Open-Meteo Historical Weather API, ERA5", "2024-01-01 to 2025-12-31", "Five representative Visayas city sample points; not provincial averages", ["Fetched daily ERA5 estimates", "Normalized units to °C, mm, km/h", "Deduplicated location/date/model"])
    print(f"weather: {len(pd.read_csv(output))} rows")


async def main() -> None:
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        await weather(client)


if __name__ == "__main__":
    asyncio.run(main())
