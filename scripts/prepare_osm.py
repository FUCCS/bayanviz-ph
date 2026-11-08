"""Prepare selected OSM facilities offline from PBF or reviewed geospatial data.

A PBF needs --boundaries (unique area_id, province, region, geometry),
--boundary-source, --boundary-license, and --snapshot-date. NCR and independent
cities need explicit rows in that reviewed boundary file. Unassigned and
multiply matched features are exported separately for review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point, Polygon

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.processing import classify_osm_tags, prepare_facilities


def read_osm(path: Path, snapshot_date: str) -> gpd.GeoDataFrame:
    import osmium

    class SelectedPOIs(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.rows: list[dict] = []

        def add(self, osm_type: str, element, point: Point) -> None:
            tags = dict(element.tags)
            category = classify_osm_tags(tags)
            if category and point.is_valid and not point.is_empty:
                self.rows.append({
                    "osm_type": osm_type, "osm_id": int(element.id),
                    "name": tags.get("name", ""), "category": category,
                    "snapshot_date": snapshot_date,
                    "source_tags": json.dumps(tags, ensure_ascii=False),
                    "geometry": point,
                })

        def node(self, node) -> None:
            if node.location.valid():
                self.add("node", node, Point(node.location.lon, node.location.lat))

        def way(self, way) -> None:
            if not classify_osm_tags(dict(way.tags)):
                return
            coords = [(ref.location.lon, ref.location.lat) for ref in way.nodes if ref.location.valid()]
            if len(coords) < 2:
                return
            shape = Polygon(coords) if len(coords) >= 4 and coords[0] == coords[-1] else LineString(coords)
            if shape.is_valid and not shape.is_empty:
                self.add("way", way, shape.representative_point())

    handler = SelectedPOIs()
    with tempfile.TemporaryDirectory(prefix="bayanviz-osm-index-") as directory:
        index = Path(directory) / "node-locations.idx"
        handler.apply_file(str(path), locations=True, idx=f"sparse_file_array,{index}")
    return gpd.GeoDataFrame(handler.rows, geometry="geometry", crs="EPSG:4326")


def read_reviewed_geodata(path: Path) -> gpd.GeoDataFrame:
    frame = gpd.read_file(path).to_crs(4326)
    if frame.empty:
        raise ValueError("No features in input")
    frame["geometry"] = frame.geometry.representative_point()
    if "source_tags" not in frame:
        frame["source_tags"] = "{}"
    return frame


def assign_areas(points: gpd.GeoDataFrame, boundaries_path: Path | None):
    if boundaries_path is None:
        if not {"area_id", "province", "region"}.issubset(points):
            raise ValueError("Input without --boundaries needs reviewed area_id, province, and region fields")
        return points, points.iloc[:0].copy(), points.iloc[:0].copy()
    boundaries = gpd.read_file(boundaries_path).to_crs(4326)
    if not {"area_id", "province", "region", "geometry"}.issubset(boundaries) or boundaries.area_id.isna().any() or boundaries.area_id.duplicated().any():
        raise ValueError("Boundary file needs unique nonempty area_id plus province, region, and geometry")
    source = points.copy()
    source["_row_id"] = range(len(source))
    joined = gpd.sjoin(source, boundaries[["area_id", "province", "region", "geometry"]], how="left", predicate="within")
    match_count = joined.groupby("_row_id")["index_right"].count()
    ambiguous_ids = set(match_count[match_count > 1].index)
    unmatched_ids = set(match_count[match_count == 0].index)
    return (
        joined[~joined._row_id.isin(ambiguous_ids | unmatched_ids)].copy(),
        joined[joined._row_id.isin(unmatched_ids)].copy(),
        joined[joined._row_id.isin(ambiguous_ids)].copy(),
    )


def write_csv(frame: gpd.GeoDataFrame, path: Path) -> None:
    rows = frame.copy()
    rows["latitude"] = rows.geometry.y
    rows["longitude"] = rows.geometry.x
    rows = rows.drop(columns=["geometry", "index_right", "_row_id"], errors="ignore")
    rows.to_csv(path, index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--boundaries", type=Path)
    parser.add_argument("--boundary-source")
    parser.add_argument("--boundary-license")
    parser.add_argument("--snapshot-date")
    args = parser.parse_args()
    if not args.input.exists():
        parser.error("Input file does not exist")
    is_osm = args.input.suffix.lower() in {".pbf", ".osm", ".opl"}
    if is_osm and (not args.boundaries or not args.snapshot_date):
        parser.error("OSM PBF/XML/OPL input requires --boundaries and --snapshot-date")
    if args.boundaries and (not args.boundary_source or not args.boundary_license):
        parser.error("--boundaries requires --boundary-source and --boundary-license")
    if args.snapshot_date:
        date.fromisoformat(args.snapshot_date)
    points = read_osm(args.input, args.snapshot_date) if is_osm else read_reviewed_geodata(args.input)
    if points.empty:
        raise SystemExit("No selected OSM facilities in input")
    matched, unmatched, ambiguous = assign_areas(points, args.boundaries)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_csv(unmatched, args.output.with_name(args.output.stem + "-unassigned.csv"))
    write_csv(ambiguous, args.output.with_name(args.output.stem + "-ambiguous.csv"))
    matched_frame = matched.copy()
    matched_frame["latitude"] = matched_frame.geometry.y
    matched_frame["longitude"] = matched_frame.geometry.x
    clean = prepare_facilities(pd.DataFrame(matched_frame.drop(columns=["geometry", "index_right", "_row_id"], errors="ignore")))
    clean.to_csv(args.output, index=False)
    manifest = {
        "schema_version": "1.0", "source": f"OpenStreetMap via {args.input.name}",
        "license": "ODbL 1.0 (© OpenStreetMap contributors)",
        "boundary_source": args.boundary_source or "pre-reviewed input",
        "boundary_license": args.boundary_license or "see input provenance",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "observation_period": args.snapshot_date or "see input snapshot_date column",
        "geographic_coverage": "Features within supplied reviewed boundaries; unmatched and ambiguous reported separately",
        "row_count": len(clean), "unassigned_count": len(unmatched),
        "ambiguous_count": len(set(ambiguous.get("_row_id", []))),
        "checksum_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "transformation_history": [
            "Selected six mutually exclusive POI categories",
            "Used node coordinates or representative way points",
            "Spatially joined to reviewed areas",
            "Excluded unmatched and multiply matched points for review",
            "Deduplicated OSM type/ID and flagged suspected same-name representations",
        ],
        "limitations": [
            "OSM relation features are not included",
            "Representative way points may differ from entrance locations",
            "Administrative coverage depends on the supplied boundary file",
        ],
    }
    args.output.with_suffix(args.output.suffix + ".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {len(clean)} matched facilities; {len(unmatched)} unassigned and {manifest['ambiguous_count']} multiply matched need review")


if __name__ == "__main__":
    main()
