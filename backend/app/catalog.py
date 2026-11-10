from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
REGISTRY = json.loads((DATA / "reference" / "locations.json").read_text())
REGIONAL_REGISTRY = json.loads((ROOT / "dataset" / "regions.json").read_text())
LOCATIONS = {row["id"]: row for row in [*REGISTRY["locations"], *REGIONAL_REGISTRY]}


def nearest_location(latitude: float, longitude: float) -> dict:
    """Keep city JSON uploads mapped to their original IDs; register new regional points separately."""
    city_points = [row for row in REGISTRY["locations"] if abs(row["latitude"] - latitude) <= 0.75 and abs(row["longitude"] - longitude) <= 0.75]
    candidates = city_points or list(LOCATIONS.values())
    return min(candidates, key=lambda row: (row["latitude"] - latitude) ** 2 + (row["longitude"] - longitude) ** 2)
