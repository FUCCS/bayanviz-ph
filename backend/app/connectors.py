from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timezone

import httpx
import pandas as pd

from .catalog import LOCATIONS

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
VARIABLES = {"temperature_mean_c": "temperature_2m_mean", "temperature_min_c": "temperature_2m_min", "temperature_max_c": "temperature_2m_max", "precipitation_mm": "precipitation_sum", "wind_speed_max_kmh": "wind_speed_10m_max"}
WEATHER_CACHE_TTL = 3600
CACHE_MAX_ENTRIES = 128
weather_cache: dict[tuple[str, str, str, tuple[str, ...]], tuple[float, pd.DataFrame]] = {}


async def fetch_weather(location_ids: list[str], start: date, end: date, variables: list[str] | None = None) -> tuple[pd.DataFrame, list[str], int]:
    if not location_ids or len(location_ids) > 3 or any(key not in LOCATIONS for key in location_ids):
        raise ValueError("Choose one to three registered sample locations")
    if (end - start).days < 0 or (end - start).days > 366 or end >= date.today():
        raise ValueError("Choose up to 367 historical days ending before today")
    selected = tuple(sorted(set(variables or VARIABLES)))
    if not selected or set(selected) - set(VARIABLES):
        raise ValueError("Choose supported climate variables")
    now = time.monotonic()
    for key, (expiry, _) in list(weather_cache.items()):
        if expiry <= now:
            del weather_cache[key]
    frames, errors, cache_hits = [], [], 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0), headers={"User-Agent": "BayanVizPH/0.1 educational analytics"}) as client:
        for key in location_ids:
            cache_key = (key, start.isoformat(), end.isoformat(), selected)
            cached = weather_cache.get(cache_key)
            if cached and cached[0] > time.monotonic():
                frames.append(cached[1].copy())
                cache_hits += 1
                continue
            loc = LOCATIONS[key]
            params = {"latitude": loc["latitude"], "longitude": loc["longitude"], "start_date": start.isoformat(), "end_date": end.isoformat(), "daily": ",".join(VARIABLES[name] for name in selected), "timezone": "Asia/Manila", "models": "era5"}
            for attempt in range(3):
                try:
                    response = await client.get(ARCHIVE_URL, params=params)
                    response.raise_for_status()
                    payload = response.json()
                    daily = payload["daily"]
                    frame = pd.DataFrame({
                        "location_id": key, "date": daily["time"], "model": "era5",
                        "temperature_mean_c": daily.get("temperature_2m_mean"),
                        "temperature_min_c": daily.get("temperature_2m_min"),
                        "temperature_max_c": daily.get("temperature_2m_max"),
                        "precipitation_mm": daily.get("precipitation_sum"),
                        "wind_speed_max_kmh": daily.get("wind_speed_10m_max"),
                    })
                    frames.append(frame)
                    while len(weather_cache) >= CACHE_MAX_ENTRIES:
                        weather_cache.pop(next(iter(weather_cache)))
                    weather_cache[cache_key] = (time.monotonic() + WEATHER_CACHE_TTL, frame.copy())
                    break
                except (httpx.HTTPError, KeyError, ValueError) as exc:
                    if attempt == 2:
                        errors.append(f"{key}: {type(exc).__name__}")
                    else:
                        await asyncio.sleep(0.5 * 2**attempt)
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return result, errors, cache_hits
