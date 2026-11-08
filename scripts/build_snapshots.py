"""Build a small, real, explicitly partial presentation snapshot.

Run from the repository root with: python scripts/build_snapshots.py
One-time Overpass use is for preparing a reviewed snapshot, never a public app backend.
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
from app.processing import classify_osm_tags, prepare_facilities, prepare_weather

DATA = ROOT / "data"
USER_AGENT = "BayanVizPH/0.1 educational snapshot preparation"


def write_manifest(kind: str, file: Path, source: str, period: str, coverage: str, history: list[str]) -> None:
    manifest = {
        "schema_version": "1.0", "source": source, "license": "CC BY 4.0" if kind == "weather" else "ODbL 1.0 (© OpenStreetMap contributors)",
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


async def facilities(client: httpx.AsyncClient) -> None:
    parts = []
    for key, loc in LOCATIONS.items():
        lat, lon = loc["latitude"], loc["longitude"]
        parts.append(f'node(around:6000,{lat},{lon})[amenity~"^(pharmacy|clinic|bank|atm|school)$"];way(around:6000,{lat},{lon})[amenity~"^(pharmacy|clinic|bank|atm|school)$"];node(around:6000,{lat},{lon})[shop~"^(chemist|supermarket|convenience|department_store|grocery)$"];way(around:6000,{lat},{lon})[shop~"^(chemist|supermarket|convenience|department_store|grocery)$"];')
    query = "[out:json][timeout:80];(" + "".join(parts) + ");out center tags;"
    response = await client.post("https://overpass-api.de/api/interpreter", data={"data": query}, timeout=120)
    response.raise_for_status()
    payload = response.json()
    rows = []
    for item in payload.get("elements", []):
        tags = item.get("tags", {})
        cat = classify_osm_tags(tags)
        center = item.get("center", item)
        lat, lon = center.get("lat"), center.get("lon")
        if not cat or lat is None or lon is None: continue
        nearest = min(LOCATIONS.values(), key=lambda loc: (loc["latitude"]-lat)**2 + (loc["longitude"]-lon)**2)
        rows.append({"osm_type": item["type"], "osm_id": item["id"], "name": tags.get("name", "Unnamed mapped feature"), "category": cat, "latitude": lat, "longitude": lon, "province": nearest["province"], "region": nearest["region"], "snapshot_date": datetime.now(timezone.utc).date().isoformat(), "source_tags": json.dumps(tags, ensure_ascii=False), "assignment_method": "nearest registered center within query radius; not verified administrative membership"})
    output = DATA / "samples" / "facilities_osm_sample.csv"
    prepare_facilities(pd.DataFrame(rows)).to_csv(output, index=False)
    write_manifest("facilities", output, "OpenStreetMap via one-time Overpass bounded sample query", datetime.now(timezone.utc).date().isoformat(), "6 km radius around five sample city centers; assignments approximate, not province-wide", ["Queried six selected categories", "Preserved source tags", "Deduplicated OSM type/ID", "Assigned nearest sample center; boundary membership unverified"])
    print(f"facilities: {len(pd.read_csv(output))} rows")


async def main() -> None:
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        await weather(client)
        await facilities(client)


if __name__ == "__main__":
    asyncio.run(main())
