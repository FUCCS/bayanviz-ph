from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
REGISTRY = json.loads((DATA / "reference" / "locations.json").read_text())
LOCATIONS = {row["id"]: row for row in REGISTRY["locations"]}
