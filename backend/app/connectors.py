from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone

import httpx
import numpy as np
import pandas as pd

from .catalog import LOCATIONS

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
NASA_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
VARIABLES = {"temperature_mean_c": "temperature_2m_mean", "temperature_min_c": "temperature_2m_min", "temperature_max_c": "temperature_2m_max", "precipitation_mm": "precipitation_sum", "wind_speed_max_kmh": "wind_speed_10m_max", "sunshine_duration_hours": "sunshine_duration", "wind_gust_max_kmh": "wind_gusts_10m_max", "precipitation_hours": "precipitation_hours", "wind_direction_dominant_deg": "wind_direction_10m_dominant", "shortwave_radiation_mj_m2": "shortwave_radiation_sum"}
HOURLY_VARIABLES = {"relative_humidity_mean_pct": "relative_humidity_2m", "apparent_temperature_mean_c": "apparent_temperature", "cloud_cover_mean_pct": "cloud_cover", "dew_point_mean_c": "dew_point_2m"}
EXPECTED_UNITS = {"temperature_mean_c": "°C", "temperature_min_c": "°C", "temperature_max_c": "°C", "precipitation_mm": "mm", "wind_speed_max_kmh": "km/h", "sunshine_duration_hours": "s", "wind_gust_max_kmh": "km/h", "precipitation_hours": "h", "wind_direction_dominant_deg": "°", "shortwave_radiation_mj_m2": "MJ/m²", "relative_humidity_mean_pct": "%", "apparent_temperature_mean_c": "°C", "cloud_cover_mean_pct": "%", "dew_point_mean_c": "°C"}
NASA_VARIABLES = {"solar_radiation": "ALLSKY_SFC_SW_DWN", "nasa_temperature_mean_c": "T2M", "nasa_relative_humidity_pct": "RH2M", "nasa_wind_speed_m_s": "WS10M"}
WEATHER_CACHE_TTL = 6 * 3600
MAX_WEATHER_DAYS = 3653
CACHE_MAX_ENTRIES = 128
weather_cache: dict[tuple[str, str, str, tuple[str, ...], str], tuple[float, pd.DataFrame]] = {}
nasa_cache: dict[tuple[str, str, str, tuple[str, ...]], tuple[float, pd.DataFrame, dict]] = {}


async def fetch_weather(location_ids: list[str], start: date, end: date, variables: list[str] | None = None, timezone_name: str = "Asia/Manila") -> tuple[pd.DataFrame, list[str], int]:
    if not location_ids or len(location_ids) > 3 or any(key not in LOCATIONS for key in location_ids):
        raise ValueError("Choose one to three registered locations")
    if (end - start).days < 0 or (end - start).days >= MAX_WEATHER_DAYS or end >= date.today():
        raise ValueError("Choose up to 3,653 historical weather days ending before today")
    selected = tuple(sorted(set(variables or ("temperature_mean_c", "temperature_min_c", "temperature_max_c", "precipitation_mm", "wind_speed_max_kmh"))))
    if not selected or set(selected) - set(VARIABLES) - set(HOURLY_VARIABLES):
        raise ValueError("Choose supported climate variables")
    if set(selected) & set(HOURLY_VARIABLES) and (end - start).days > 366:
        raise ValueError("Hourly-derived daily fields are limited to 367 days per request; use the existing daily-only fields for a ten-year fetch")
    if timezone_name not in {"Asia/Manila", "UTC"}:
        raise ValueError("Choose Asia/Manila or UTC daily time")
    if len(location_ids) > 1:
        parts = await asyncio.gather(*(fetch_weather([key], start, end, variables, timezone_name) for key in location_ids))
        frames = [frame for frame, _, _ in parts if not frame.empty]
        return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(),
                [error for _, errors, _ in parts for error in errors],
                sum(hits for _, _, hits in parts))
    if (end - start).days >= 365:
        # Large archive queries can time out upstream. A failed year must not
        # quietly turn a requested ten-year dataset into a partial series.
        chunks = []
        errors = []
        cache_hits = 0
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(end, chunk_start + timedelta(days=364))
            frame, chunk_errors, hits = await fetch_weather(location_ids, chunk_start, chunk_end, variables, timezone_name)
            if frame.empty or chunk_errors:
                errors.extend(chunk_errors or [f"{location_ids[0]}: no data for {chunk_start} to {chunk_end}"])
            else:
                chunks.append(frame)
                cache_hits += hits
            chunk_start = chunk_end + timedelta(days=1)
        if errors:
            return pd.DataFrame(), errors, cache_hits
        return pd.concat(chunks, ignore_index=True), [], cache_hits
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
            params = {"latitude": loc["latitude"], "longitude": loc["longitude"], "start_date": start.isoformat(), "end_date": end.isoformat(), "timezone": timezone_name, "models": "era5"}
            daily_selected = [name for name in selected if name in VARIABLES]
            hourly_selected = [name for name in selected if name in HOURLY_VARIABLES]
            if daily_selected:
                params["daily"] = ",".join(VARIABLES[name] for name in daily_selected)
            if hourly_selected:
                params["hourly"] = ",".join(HOURLY_VARIABLES[name] for name in hourly_selected)
            for attempt in range(3):
                try:
                    response = await client.get(ARCHIVE_URL, params=params)
                    response.raise_for_status()
                    payload = response.json()
                    daily = payload.get("daily", {})
                    hourly = payload.get("hourly", {})
                    dates = daily.get("time") or sorted({stamp[:10] for stamp in hourly.get("time", [])})
                    if not isinstance(dates, list) or not dates or any(name not in daily or not isinstance(daily[name], list) or len(daily[name]) != len(dates) for name in (VARIABLES[var] for var in daily_selected)):
                        raise ValueError("Open-Meteo returned incomplete daily arrays")
                    units = payload.get("daily_units", {})
                    for name in selected:
                        provider = VARIABLES.get(name) or HOURLY_VARIABLES[name]
                        reported = (units if name in VARIABLES else payload.get("hourly_units", {})).get(provider)
                        if reported != EXPECTED_UNITS[name]:
                            raise ValueError(f"Open-Meteo returned unexpected units for {name}")
                    frame = pd.DataFrame({"location_id": key, "date": dates, "model": "era5"})
                    for name in daily_selected:
                        values = pd.to_numeric(pd.Series(daily[VARIABLES[name]]), errors="coerce")
                        frame[name] = values / 3600 if name == "sunshine_duration_hours" else values
                    if hourly_selected:
                        stamps = hourly.get("time", [])
                        if not stamps or any(HOURLY_VARIABLES[name] not in hourly or len(hourly[HOURLY_VARIABLES[name]]) != len(stamps) for name in hourly_selected):
                            raise ValueError("Open-Meteo returned incomplete hourly arrays")
                        hourly_frame = pd.DataFrame({"date": [stamp[:10] for stamp in stamps]})
                        for name in hourly_selected:
                            hourly_frame[name] = pd.to_numeric(pd.Series(hourly[HOURLY_VARIABLES[name]]), errors="coerce")
                        frame = frame.merge(hourly_frame.groupby("date", as_index=False)[hourly_selected].mean(), on="date", how="left", validate="one_to_one")
                    frame["analysis_time_standard"] = timezone_name
                    frame["openmeteo_grid_latitude"] = payload.get("latitude")
                    frame["openmeteo_grid_longitude"] = payload.get("longitude")
                    frame["openmeteo_dataset_version"] = "ERA5"
                    frame.attrs["provider_units"] = {name: (units if name in VARIABLES else payload.get("hourly_units", {})).get(VARIABLES.get(name) or HOURLY_VARIABLES.get(name)) for name in selected}
                    frames.append(frame)
                    while len(weather_cache) >= CACHE_MAX_ENTRIES:
                        weather_cache.pop(next(iter(weather_cache)))
                    weather_cache[cache_key] = (time.monotonic() + WEATHER_CACHE_TTL, frame.copy())
                    break
                except (httpx.HTTPError, KeyError, ValueError) as exc:
                    permanent = isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500 and exc.response.status_code != 429
                    if attempt == 2 or permanent:
                        if isinstance(exc, httpx.HTTPStatusError):
                            try:
                                reason = str(exc.response.json().get("reason", ""))[:180]
                            except (ValueError, AttributeError):
                                reason = ""
                            errors.append(f"{key}: Open-Meteo HTTP {exc.response.status_code}" + (f" — {reason}" if reason else ""))
                        else:
                            errors.append(f"{key}: {type(exc).__name__}: {str(exc)[:180]}")
                    else:
                        await asyncio.sleep(1.5 * 2**attempt)
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
        row = {"location_id": location_id, "date": pd.to_datetime(key, format="%Y%m%d", errors="raise"), "nasa_solar_radiation": np.nan, "nasa_temperature_mean_c": np.nan, "nasa_relative_humidity_pct": np.nan, "nasa_wind_speed_m_s": np.nan}
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
        raise ValueError("Select NASA solar radiation and supported optional comparison fields")
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
