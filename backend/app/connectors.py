from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timezone

import httpx
import numpy as np
import pandas as pd

from .catalog import LOCATIONS

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
NASA_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
VARIABLES = {"temperature_mean_c": "temperature_2m_mean", "temperature_min_c": "temperature_2m_min", "temperature_max_c": "temperature_2m_max", "precipitation_mm": "precipitation_sum", "wind_speed_max_kmh": "wind_speed_10m_max"}
NASA_VARIABLES = {"solar_radiation": "ALLSKY_SFC_SW_DWN", "nasa_temperature_mean_c": "T2M"}
WEATHER_CACHE_TTL = 6 * 3600
CACHE_MAX_ENTRIES = 128
weather_cache: dict[tuple[str, str, str, tuple[str, ...], str], tuple[float, pd.DataFrame]] = {}
nasa_cache: dict[tuple[str, str, str, tuple[str, ...]], tuple[float, pd.DataFrame, dict]] = {}


async def fetch_weather(location_ids: list[str], start: date, end: date, variables: list[str] | None = None, timezone_name: str = "Asia/Manila") -> tuple[pd.DataFrame, list[str], int]:
    if not location_ids or len(location_ids) > 3 or any(key not in LOCATIONS for key in location_ids):
        raise ValueError("Choose one to three registered locations")
    if (end - start).days < 0 or (end - start).days > 366 or end >= date.today():
        raise ValueError("Choose up to 367 historical days ending before today")
    selected = tuple(sorted(set(variables or VARIABLES)))
    if not selected or set(selected) - set(VARIABLES):
        raise ValueError("Choose supported climate variables")
    if timezone_name not in {"Asia/Manila", "UTC"}:
        raise ValueError("Choose Asia/Manila or UTC daily time")
    if len(location_ids) > 1:
        parts = await asyncio.gather(*(fetch_weather([key], start, end, variables, timezone_name) for key in location_ids))
        frames = [frame for frame, _, _ in parts if not frame.empty]
        return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(),
                [error for _, errors, _ in parts for error in errors],
                sum(hits for _, _, hits in parts))
    now = time.monotonic()
    for key, (expiry, _) in list(weather_cache.items()):
        if expiry <= now:
            del weather_cache[key]
    frames, errors, cache_hits = [], [], 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0), headers={"User-Agent": "BayanVizPH/0.1 educational analytics"}) as client:
        for key in location_ids:
            cache_key = (key, start.isoformat(), end.isoformat(), selected, timezone_name)
            cached = weather_cache.get(cache_key)
            if cached and cached[0] > time.monotonic():
                frames.append(cached[1].copy())
                cache_hits += 1
                continue
            loc = LOCATIONS[key]
            params = {"latitude": loc["latitude"], "longitude": loc["longitude"], "start_date": start.isoformat(), "end_date": end.isoformat(), "daily": ",".join(VARIABLES[name] for name in selected), "timezone": timezone_name, "models": "era5"}
            for attempt in range(2):
                try:
                    response = await client.get(ARCHIVE_URL, params=params)
                    response.raise_for_status()
                    payload = response.json()
                    daily = payload["daily"]
                    dates = daily["time"]
                    if not isinstance(dates, list) or not dates or any(name not in daily or not isinstance(daily[name], list) or len(daily[name]) != len(dates) for name in (VARIABLES[var] for var in selected)):
                        raise ValueError("Open-Meteo returned incomplete daily arrays")
                    units = payload.get("daily_units", {})
                    expected_units = {"temperature_mean_c": "°C", "temperature_min_c": "°C", "temperature_max_c": "°C", "precipitation_mm": "mm", "wind_speed_max_kmh": "km/h"}
                    for name in selected:
                        if units and units.get(VARIABLES[name]) != expected_units[name]:
                            raise ValueError(f"Open-Meteo returned unexpected units for {name}")
                    frame = pd.DataFrame({
                        "location_id": key, "date": dates, "model": "era5",
                        "temperature_mean_c": daily.get("temperature_2m_mean"),
                        "temperature_min_c": daily.get("temperature_2m_min"),
                        "temperature_max_c": daily.get("temperature_2m_max"),
                        "precipitation_mm": daily.get("precipitation_sum"),
                        "wind_speed_max_kmh": daily.get("wind_speed_10m_max"),
                    })
                    frame["analysis_time_standard"] = timezone_name
                    frame["openmeteo_grid_latitude"] = payload.get("latitude")
                    frame["openmeteo_grid_longitude"] = payload.get("longitude")
                    frame["openmeteo_dataset_version"] = "ERA5"
                    frames.append(frame)
                    while len(weather_cache) >= CACHE_MAX_ENTRIES:
                        weather_cache.pop(next(iter(weather_cache)))
                    weather_cache[cache_key] = (time.monotonic() + WEATHER_CACHE_TTL, frame.copy())
                    break
                except (httpx.HTTPError, KeyError, ValueError) as exc:
                    if attempt == 1:
                        errors.append(f"{key}: {type(exc).__name__}")
                    else:
                        await asyncio.sleep(0.5 * 2**attempt)
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return result, errors, cache_hits


def parse_nasa_payload(payload: dict, location_id: str, selected: tuple[str, ...]) -> tuple[pd.DataFrame, dict]:
    """Parse only validated POWER daily point responses; preserve provider units."""
    if not isinstance(payload, dict) or not isinstance(payload.get("properties"), dict):
        raise ValueError("NASA POWER returned an unexpected response")
    values = payload["properties"].get("parameter")
    units_meta = payload.get("parameters")
    if not isinstance(values, dict) or not isinstance(units_meta, dict):
        raise ValueError("NASA POWER omitted parameter values or units")
    series = {}
    parameter_meta = {}
    for name in selected:
        code = NASA_VARIABLES[name]
        if not isinstance(values.get(code), dict) or not isinstance(units_meta.get(code), dict):
            raise ValueError(f"NASA POWER omitted {code}")
        unit = units_meta[code].get("units")
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError(f"NASA POWER omitted the unit for {code}")
        parameter_meta[code] = {"units": unit, "name": units_meta[code].get("longname", code)}
        series[code] = values[code]
    keys = set.union(*(set(item) for item in series.values()))
    if not keys or any(len(key) != 8 or not key.isdigit() for key in keys):
        raise ValueError("NASA POWER returned invalid daily dates")
    rows = []
    fill = payload.get("header", {}).get("fill_value", -999)
    for key in sorted(keys):
        row = {"location_id": location_id, "date": pd.to_datetime(key, format="%Y%m%d", errors="raise"), "nasa_solar_radiation": np.nan, "nasa_temperature_mean_c": np.nan}
        for name in selected:
            raw = series[NASA_VARIABLES[name]].get(key)
            number = pd.to_numeric(raw, errors="coerce")
            row["nasa_solar_radiation" if name == "solar_radiation" else name] = np.nan if pd.isna(number) or not np.isfinite(number) or number in (-999, -9999, fill) else float(number)
        rows.append(row)
    coordinates = payload.get("geometry", {}).get("coordinates")
    grid = coordinates[:2] if isinstance(coordinates, list) and len(coordinates) >= 2 else [None, None]
    header = payload.get("header", {})
    metadata = {"provider": "NASA POWER", "dataset_version": header.get("api", {}).get("version") or header.get("api_version") or header.get("version") or "unreported", "parameter_metadata": parameter_meta, "grid_longitude": grid[0], "grid_latitude": grid[1], "time_standard": header.get("time_standard"), "requested_location_id": location_id, "underlying_sources": header.get("sources", [])}
    return pd.DataFrame(rows), metadata


async def fetch_nasa(location_ids: list[str], start: date, end: date, variables: list[str] | None = None) -> tuple[pd.DataFrame, dict, list[str], int]:
    if not location_ids or len(location_ids) > 3 or any(key not in LOCATIONS for key in location_ids):
        raise ValueError("Choose one to three registered locations")
    if (end - start).days < 0 or (end - start).days > 366 or end >= date.today():
        raise ValueError("Choose up to 367 historical days ending before today")
    selected = tuple(sorted(set(variables or ["solar_radiation"])))
    if not selected or set(selected) - set(NASA_VARIABLES) or "solar_radiation" not in selected:
        raise ValueError("Select NASA solar radiation and optional temperature")
    if len(location_ids) > 1:
        parts = await asyncio.gather(*(fetch_nasa([key], start, end, variables) for key in location_ids))
        frames = [frame for frame, _, _, _ in parts if not frame.empty]
        return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(),
                {key: value for _, metadata, _, _ in parts for key, value in metadata.items()},
                [error for _, _, errors, _ in parts for error in errors],
                sum(hits for _, _, _, hits in parts))
    now = time.monotonic()
    for key, (expiry, _, _) in list(nasa_cache.items()):
        if expiry <= now:
            del nasa_cache[key]
    frames, metadata, errors, cache_hits = [], {}, [], 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0), headers={"User-Agent": "BayanVizPH/0.2 educational analytics"}) as client:
        for key in location_ids:
            cache_key = (key, start.isoformat(), end.isoformat(), selected)
            cached = nasa_cache.get(cache_key)
            if cached and cached[0] > time.monotonic():
                frames.append(cached[1].copy())
                metadata[key] = cached[2].copy()
                cache_hits += 1
                continue
            loc = LOCATIONS[key]
            params = {"parameters": ",".join(NASA_VARIABLES[name] for name in selected), "community": "RE", "longitude": loc["longitude"], "latitude": loc["latitude"], "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "format": "JSON", "time-standard": "UTC"}
            for attempt in range(2):
                try:
                    response = await client.get(NASA_URL, params=params)
                    response.raise_for_status()
                    frame, info = parse_nasa_payload(response.json(), key, selected)
                    frames.append(frame)
                    metadata[key] = info
                    while len(nasa_cache) >= CACHE_MAX_ENTRIES:
                        nasa_cache.pop(next(iter(nasa_cache)))
                    nasa_cache[cache_key] = (time.monotonic() + WEATHER_CACHE_TTL, frame.copy(), info.copy())
                    break
                except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    if attempt == 1:
                        errors.append(f"{key}: {type(exc).__name__}: {exc}")
                    else:
                        await asyncio.sleep(0.5 * 2**attempt)
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return result, metadata, errors, cache_hits
