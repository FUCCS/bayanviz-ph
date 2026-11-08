from __future__ import annotations

import calendar
import hashlib
import json

import numpy as np
import pandas as pd

WEATHER_COLUMNS = ["temperature_mean_c", "temperature_min_c", "temperature_max_c", "precipitation_mm", "wind_speed_max_kmh"]
FACILITY_CATEGORIES = ("pharmacy", "clinic", "bank", "atm", "retail", "school")


def classify_osm_tags(tags: dict) -> str | None:
    """Assign one primary category so counts are additive."""
    amenity, shop, healthcare = tags.get("amenity"), tags.get("shop"), tags.get("healthcare")
    if amenity == "pharmacy" or shop == "chemist":
        return "pharmacy"
    if amenity == "clinic" or healthcare == "clinic":
        return "clinic"
    if amenity == "bank":
        return "bank"
    if amenity == "atm":
        return "atm"
    if amenity == "school":
        return "school"
    if shop in {"supermarket", "convenience", "department_store", "grocery"}:
        return "retail"
    return None


def prepare_weather(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate weather observations without replacing missing values with zero."""
    df = frame.copy()
    required = {"location_id", "date", "model"}
    if not required.issubset(df):
        raise ValueError(f"Weather data needs {', '.join(sorted(required))}")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in WEATHER_COLUMNS:
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["location_id", "date", "model"])
    df = df.drop_duplicates(["location_id", "date", "model"], keep="last")
    df["outlier_flag"] = (df["temperature_mean_c"].lt(-15) | df["temperature_mean_c"].gt(50) | df["precipitation_mm"].lt(0) | df["precipitation_mm"].gt(1000))
    return df.sort_values(["location_id", "date"]).reset_index(drop=True)


def prepare_facilities(frame: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate OSM identities; retain original tags and category distinctions."""
    df = frame.copy()
    required = {"osm_type", "osm_id", "category", "latitude", "longitude", "province", "region"}
    if not required.issubset(df):
        raise ValueError(f"Facility data needs {', '.join(sorted(required))}")
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df[df["category"].isin(FACILITY_CATEGORIES) & df["latitude"].between(4, 22) & df["longitude"].between(116, 128)]
    # An OSM element has one identity even when its tags mention several services.
    # The ingestion classifier assigns one primary category; don't count it twice.
    df = df.drop_duplicates(["osm_type", "osm_id"])
    if "name" in df:
        cleaned_name = df["name"].fillna("").astype(str).str.strip().str.casefold()
        duplicate_key = pd.DataFrame({
            "name": cleaned_name, "category": df["category"],
            "latitude": df["latitude"].round(3), "longitude": df["longitude"].round(3),
        }, index=df.index)
        df["suspected_duplicate"] = cleaned_name.ne("") & cleaned_name.ne("unnamed mapped feature") & duplicate_key.duplicated(keep=False)
    else:
        df["suspected_duplicate"] = False
    return df.reset_index(drop=True)


def monthly_rainfall(weather: pd.DataFrame) -> pd.DataFrame:
    """Sum valid daily rainfall per location and month; retain completeness."""
    df = weather.copy()
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    grouped = df.groupby(["location_id", "year", "month"], as_index=False).agg(
        total_mm=("precipitation_mm", lambda s: s.sum(min_count=1)),
        valid_days=("precipitation_mm", "count"),
    )
    grouped["expected_days"] = grouped.apply(lambda r: calendar.monthrange(int(r.year), int(r.month))[1], axis=1)
    grouped["complete"] = grouped["valid_days"] == grouped["expected_days"]
    return grouped


def filter_hash(filters: dict) -> str:
    return hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()[:16]
