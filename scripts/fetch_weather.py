"""Fetch a versioned ERA5 weather dataset offline in bounded yearly chunks.

Example:
  python scripts/fetch_weather.py --start 2010-01-01 --end 2025-12-31 \
    --locations dumaguete bacolod cebu tagbilaran siquijor \
    --output data/prepared/weather_2010_2025.csv

This script is for deliberate offline preparation, not a visitor-triggered API.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.catalog import LOCATIONS
from app.connectors import fetch_weather
from app.processing import prepare_weather


def chunks(start: date, end: date):
    current = start
    while current <= end:
        finish = min(end, date(current.year, 12, 31))
        yield current, finish
        current = finish + timedelta(days=1)


async def run(args) -> None:
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if end < start or end >= date.today():
        raise ValueError("Use a historical date range ending before today")
    locations = args.locations or list(LOCATIONS)
    unknown = set(locations) - set(LOCATIONS)
    if unknown:
        raise ValueError(f"Unknown location IDs: {', '.join(sorted(unknown))}")
    frames, errors = [], []
    for first, last in chunks(start, end):
        for offset in range(0, len(locations), 3):
            group = locations[offset:offset + 3]
            frame, partial_errors, _ = await fetch_weather(group, first, last)
            if not frame.empty:
                frames.append(frame)
            errors.extend(f"{first}–{last}: {message}" for message in partial_errors)
            print(f"{first}–{last} · {', '.join(group)} · {len(frame)} rows · {len(partial_errors)} errors")
            await asyncio.sleep(args.delay)
    if not frames:
        raise RuntimeError(f"No observations retrieved: {errors}")
    clean = prepare_weather(pd.concat(frames, ignore_index=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    clean.to_csv(args.output, index=False)
    manifest = {
        "schema_version": "1.0", "source": "Open-Meteo Historical Weather API, ERA5",
        "license": "See current Open-Meteo terms and ERA5/ECMWF attribution",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "observation_period": f"{start} to {end}",
        "geographic_coverage": f"{len(locations)} registered representative sample points; not province-wide means",
        "location_ids": locations, "row_count": len(clean),
        "checksum_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "transformation_history": ["Fetched bounded calendar-year ERA5 daily chunks", "Normalized units to °C, mm, and km/h", "Deduplicated location/date/model", "Flagged unusual values; did not replace missing values with zero"],
        "partial_errors": errors,
        "coverage_status": "Partial coverage" if errors else "Complete for selected point/date combinations, subject to missing values",
    }
    args.output.with_suffix(args.output.suffix + ".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {len(clean)} rows to {args.output}; {len(errors)} partial errors")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--locations", nargs="*", choices=sorted(LOCATIONS))
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "prepared" / "weather_2010_2025.csv")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between bounded provider requests")
    args = parser.parse_args()
    if args.delay < 0.25:
        parser.error("Keep at least 0.25 seconds between requests")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
