from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import secrets
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .catalog import LOCATIONS, REGISTRY, WEATHER_FILE, FACILITY_FILE, facility_frame, manifest, weather_frame
from .charts import PALETTE, REGISTRY as CHARTS, code_for, interpretation, prepare_chart, render_chart
from .connectors import fetch_weather
from .processing import classify_osm_tags, filter_hash, prepare_facilities, prepare_weather

app = FastAPI(title="BayanViz PH API", version="0.1.0")
origins = [s.strip() for s in os.getenv("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if s.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type", "X-Session-Id"])
SESSION_TTL = 3 * 3600
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "5"))
MAX_UPLOAD_ROWS = int(os.getenv("MAX_UPLOAD_ROWS", "50000"))
MAX_UPLOAD_COLUMNS = int(os.getenv("MAX_UPLOAD_COLUMNS", "60"))
MAX_UPLOADS_PER_SESSION = int(os.getenv("MAX_UPLOADS_PER_SESSION", "3"))
UPLOAD_PARSE_TIMEOUT_SECONDS = int(os.getenv("UPLOAD_PARSE_TIMEOUT_SECONDS", "15"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
sessions: dict[str, dict] = {}
results: dict[str, dict] = {}


def prune() -> None:
    now = time.time()
    for token, value in list(sessions.items()):
        if value["expires"] < now:
            del sessions[token]
    for key, value in list(results.items()):
        if value["expires"] < now:
            del results[key]


def session(x_session_id: str | None = Header(default=None)) -> tuple[str, dict]:
    prune()
    if not x_session_id or x_session_id not in sessions:
        raise HTTPException(401, "Start a new anonymous session")
    sessions[x_session_id]["expires"] = time.time() + SESSION_TTL
    return x_session_id, sessions[x_session_id]


class FetchRequest(BaseModel):
    locations: list[str] = Field(min_length=1, max_length=3)
    start_date: date
    end_date: date


class UseUploadRequest(BaseModel):
    upload_id: str
    kind: str
    mapping: dict[str, str] = Field(default_factory=dict)


class AnalysisRequest(BaseModel):
    chart_id: str
    source: str = "curated"
    locations: list[str] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    categories: list[str] = Field(default_factory=list)
    variable: str = "temperature_mean_c"
    aggregation: str = "annual"


@app.get("/api/health")
def health():
    return {"ok": True, "service": "BayanViz PH"}


@app.post("/api/session")
def start_session():
    prune()
    token = secrets.token_urlsafe(32)
    sessions[token] = {"expires": time.time() + SESSION_TTL, "uploads": {}, "weather": None, "facilities": None, "weather_source": None, "facilities_source": None, "requests": []}
    return {"session_id": token, "expires_in_seconds": SESSION_TTL}


@app.delete("/api/session")
def clear_session(auth=Depends(session)):
    token, _ = auth
    sessions.pop(token, None)
    for key, value in list(results.items()):
        if value["session_id"] == token:
            del results[key]
    return {"cleared": True}


@app.get("/api/catalog")
def catalog():
    return {
        "name": "BayanViz PH", "subtitle": "Philippine Climate and Infrastructure Analytics",
        "locations": list(LOCATIONS.values()),
        "charts": [{"id": key, "chapter": row[0], "topic": row[1], "function": row[2], "objective": row[3], "needs": row[5]} for key, row in CHARTS.items()],
        "categories": ["pharmacy", "clinic", "bank", "atm", "retail", "school"],
        "upload_limit_mb": MAX_UPLOAD_MB, "upload_limit_rows": MAX_UPLOAD_ROWS,
        "upload_limit_columns": MAX_UPLOAD_COLUMNS, "uploads_per_session": MAX_UPLOADS_PER_SESSION,
    }


@app.get("/api/sources")
def sources():
    weather = weather_frame()
    facilities = facility_frame()
    prepared = prepare_weather(weather)
    quality = {
        "valid_temperature_records": int(prepared.temperature_mean_c.notna().sum()),
        "missing_temperature_records": int(prepared.temperature_mean_c.isna().sum()),
        "flagged_weather_outliers": int(prepared.outlier_flag.sum()),
        "unassigned_facilities": int(facilities.province.isna().sum() + facilities.province.eq("").sum()),
        "area_assignment": "nearest registered center; administrative boundaries not validated",
    }
    return {
        "weather": {"status": "Prepared Real Dataset" if len(weather) else "Unavailable", "records": len(weather), "manifest": manifest("weather")},
        "facilities": {"status": "Prepared Real Dataset" if len(facilities) else "Unavailable", "records": len(facilities), "manifest": manifest("facilities")},
        "live_weather": {"status": "Live API", "provider": "Open-Meteo Historical Weather", "model": "ERA5", "interactive_max_days": 367, "interactive_max_locations": 3},
        "live_osm": {"status": "Unavailable", "reason": "Interactive OSM connector is disabled; use the prepared snapshot or upload an export."},
        "coverage": "Partial coverage — registered sample points and any loaded OSM snapshot only. No national completeness claim.",
        "quality": quality,
    }


@app.post("/api/fetch/weather")
async def fetch(request: FetchRequest, auth=Depends(session)):
    token, state = auth
    if len(state["requests"]) >= 12:
        raise HTTPException(429, "Session request limit reached")
    state["requests"].append(time.time())
    try:
        frame, errors, cache_hits = await fetch_weather(request.locations, request.start_date, request.end_date)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if frame.empty:
        raise HTTPException(502, {"message": "Provider returned no usable locations", "errors": errors})
    state["weather"] = prepare_weather(frame)
    state["weather_source"] = "Cached API" if cache_hits == len(request.locations) else "Live API"
    return {"status": state["weather_source"], "records": len(frame), "errors": errors, "cache_hits": cache_hits, "preview": frame.head(8).to_dict("records"), "retrieved_at": datetime.now(timezone.utc).isoformat(), "observation_period": [str(request.start_date), str(request.end_date)]}


def parse_upload(name: str, content: bytes) -> pd.DataFrame:
    suffix = Path(name).suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(io.BytesIO(content), nrows=MAX_UPLOAD_ROWS + 1)
    if suffix == ".parquet":
        import pyarrow.parquet as pq
        metadata = pq.ParquetFile(io.BytesIO(content)).metadata
        if metadata.num_rows > MAX_UPLOAD_ROWS or metadata.num_columns > MAX_UPLOAD_COLUMNS:
            raise ValueError("Parquet file exceeds row or column limits")
        return pd.read_parquet(io.BytesIO(content))
    if suffix in (".json", ".geojson"):
        payload = json.loads(content)
        if isinstance(payload, dict) and "daily" in payload and "time" in payload["daily"]:
            frame = pd.DataFrame(payload["daily"]).rename(columns={
                "time": "date", "temperature_2m_mean": "temperature_mean_c",
                "temperature_2m_min": "temperature_min_c", "temperature_2m_max": "temperature_max_c",
                "precipitation_sum": "precipitation_mm", "wind_speed_10m_max": "wind_speed_max_kmh",
            })
            lat, lon = payload.get("latitude"), payload.get("longitude")
            nearby = min(LOCATIONS.values(), key=lambda loc: (loc["latitude"] - lat)**2 + (loc["longitude"] - lon)**2) if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) else None
            frame["location_id"] = nearby["id"] if nearby and (nearby["latitude"] - lat)**2 + (nearby["longitude"] - lon)**2 < 0.02 else "uploaded_point"
            frame["model"] = "era5" if "era5" in str(payload.get("model", "")).lower() else "uploaded_model_unspecified"
            return frame
        if isinstance(payload, dict) and "elements" in payload:
            rows = []
            for item in payload["elements"]:
                center = item.get("center", item)
                tags = item.get("tags", {})
                category = classify_osm_tags(tags)
                lat, lon = center.get("lat"), center.get("lon")
                if lat is None or lon is None or category is None:
                    continue
                nearest = min(LOCATIONS.values(), key=lambda loc: (loc["latitude"] - lat)**2 + (loc["longitude"] - lon)**2)
                if (nearest["latitude"] - lat)**2 + (nearest["longitude"] - lon)**2 > 0.02:
                    continue
                rows.append({"osm_type": item.get("type"), "osm_id": item.get("id"), "latitude": lat, "longitude": lon, "name": tags.get("name", ""), "source_tags": json.dumps(tags), "category": category, "province": nearest["province"], "region": nearest["region"], "assignment_method": "nearest registered center; boundary unverified"})
            return pd.DataFrame(rows)
        if isinstance(payload, dict) and "features" in payload:
            rows = []
            for feature in payload["features"]:
                row = dict(feature.get("properties") or {})
                geometry = feature.get("geometry") or {}
                if geometry.get("type") != "Point":
                    raise ValueError("Only Point GeoJSON is supported; preprocess polygons offline")
                coordinates = geometry.get("coordinates", [])
                if len(coordinates) < 2:
                    raise ValueError("Point GeoJSON needs longitude and latitude")
                row.update(longitude=coordinates[0], latitude=coordinates[1])
                rows.append(row)
            return pd.DataFrame(rows)
        return pd.DataFrame(payload if isinstance(payload, list) else payload.get("data", []))
    raise ValueError("Use CSV, Parquet, GeoJSON, or supported JSON")


@app.post("/api/upload/preview")
async def upload_preview(file: UploadFile = File(...), auth=Depends(session)):
    _, state = auth
    if len(state["uploads"]) >= MAX_UPLOADS_PER_SESSION:
        raise HTTPException(429, "This session has reached its upload limit; clear your data to start over")
    name = Path(file.filename or "upload").name
    if Path(name).suffix.lower() not in {".csv", ".parquet", ".json", ".geojson"}:
        raise HTTPException(400, "Unsupported file type")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Upload exceeds {MAX_UPLOAD_MB} MB")
    try:
        df = await asyncio.wait_for(asyncio.to_thread(parse_upload, name, content), timeout=UPLOAD_PARSE_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise HTTPException(408, "File parsing took too long") from exc
    except Exception as exc:
        raise HTTPException(400, f"Could not parse file: {type(exc).__name__}") from exc
    if df.empty or len(df) > MAX_UPLOAD_ROWS or len(df.columns) > MAX_UPLOAD_COLUMNS:
        raise HTTPException(400, f"Upload must have 1–{MAX_UPLOAD_ROWS:,} rows and at most {MAX_UPLOAD_COLUMNS} columns")
    upload_id = secrets.token_urlsafe(16)
    state["uploads"][upload_id] = df
    preview = df.head(8).fillna("").astype(str).to_dict("records")
    return {"upload_id": upload_id, "filename": name, "rows": len(df), "columns": list(df.columns), "preview": preview, "missing_by_column": {str(col): int(df[col].isna().sum()) for col in df.columns}, "required_weather": ["location_id", "date", "model"], "required_facilities": ["osm_type", "osm_id", "category", "latitude", "longitude", "province", "region"]}


@app.post("/api/upload/use")
def use_upload(request: UseUploadRequest, auth=Depends(session)):
    _, state = auth
    df = state["uploads"].get(request.upload_id)
    if df is None:
        raise HTTPException(404, "Upload not found in this session")
    if len(set(request.mapping.values())) != len(request.mapping):
        raise HTTPException(400, "Each destination field needs a distinct source column")
    for target, source in request.mapping.items():
        if source not in df:
            raise HTTPException(400, f"Source column {source} is missing")
    mapped = df.rename(columns={source: target for target, source in request.mapping.items()})
    try:
        if request.kind == "weather":
            cleaned = prepare_weather(mapped)
        elif request.kind == "facilities":
            cleaned = prepare_facilities(mapped)
        else:
            raise ValueError("Choose weather or facilities")
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    count = len(cleaned)
    if not count:
        raise HTTPException(400, "No valid records remained after validation")
    state[request.kind] = cleaned
    state[f"{request.kind}_source"] = "Uploaded Dataset"
    return {"status": "Uploaded Dataset", "kind": request.kind, "valid_records": count}


def selected_tables(request: AnalysisRequest, state: dict) -> tuple[pd.DataFrame, pd.DataFrame, str, str]:
    if request.source == "session":
        weather = state["weather"] if state["weather"] is not None else weather_frame()
        facilities = state["facilities"] if state["facilities"] is not None else facility_frame()
        weather_status = state["weather_source"] or "Prepared Real Dataset"
        facility_status = state["facilities_source"] or "Prepared Real Dataset"
    else:
        weather, facilities, weather_status, facility_status = weather_frame(), facility_frame(), "Prepared Real Dataset", "Prepared Real Dataset"
    weather = prepare_weather(weather) if not weather.empty else weather
    facilities = prepare_facilities(facilities) if not facilities.empty else facilities
    if request.locations:
        unknown = set(request.locations) - set(LOCATIONS)
        if unknown:
            raise ValueError(f"Unknown locations: {', '.join(sorted(unknown))}")
        weather = weather[weather.location_id.isin(request.locations)]
        selected_provinces = {LOCATIONS[key]["province"] for key in request.locations}
        facilities = facilities[facilities.province.isin(selected_provinces)]
    if request.start_date:
        weather = weather[weather.date >= pd.Timestamp(request.start_date)]
    if request.end_date:
        weather = weather[weather.date <= pd.Timestamp(request.end_date)]
    if request.categories:
        facilities = facilities[facilities.category.isin(request.categories)]
    return weather, facilities, weather_status, facility_status


@app.post("/api/analysis")
def analyze(request: AnalysisRequest, auth=Depends(session)):
    token, state = auth
    if request.chart_id not in CHARTS:
        raise HTTPException(404, "Unknown chart")
    try:
        weather, facilities, weather_status, facility_status = selected_tables(request, state)
        table, units, caveats = prepare_chart(request.chart_id, weather, facilities, request.variable, request.aggregation)
        if table.empty:
            raise ValueError("No valid observations after filtering")
        figure = render_chart(request.chart_id, table)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    filters = request.model_dump(mode="json")
    version = f"weather:{hashlib.sha256(weather.to_csv(index=False).encode()).hexdigest()[:12]};facilities:{hashlib.sha256(facilities.to_csv(index=False).encode()).hexdigest()[:12]}"
    needs = CHARTS[request.chart_id][5]
    status = weather_status if needs == "weather" else facility_status if needs == "facilities" else weather_status if weather_status == facility_status else f"{weather_status} + {facility_status}"
    source_list = [status]
    if needs in ("weather", "both") and len(weather):
        source_list.append("Open-Meteo ERA5 reanalysis" if weather_status != "Uploaded Dataset" else "Uploaded weather dataset")
    if needs in ("facilities", "both") and len(facilities):
        source_list.append("© OpenStreetMap contributors" if facility_status != "Uploaded Dataset" else "Uploaded facility dataset")
    result_id = secrets.token_urlsafe(24)
    record = {
        "analysis_id": result_id, "dataset_version": version, "filter_hash": filter_hash(filters), "chart_id": request.chart_id,
        "renderer": "Folium" if request.chart_id == "map" else "Plotly for Python" if request.chart_id == "treemap" else "Seaborn / Matplotlib" if request.chart_id != "mosaic" else "Statsmodels / Matplotlib",
        "figure": figure, "supporting_data": table.head(30).replace({pd.NaT: None}).to_json(orient="records", date_format="iso"),
        "supporting_columns": list(table.columns), "supporting_count": len(table),
        "computed_facts": interpretation(request.chart_id, table, caveats), "python_source": code_for(request.chart_id),
        "sources": source_list,
        "limitations": caveats, "units": units, "filters": filters, "sample_size": len(table),
        "session_id": token, "expires": time.time() + SESSION_TTL, "table": table,
    }
    results[result_id] = record
    return {key: (json.loads(value) if key == "supporting_data" else value) for key, value in record.items() if key not in ("session_id", "expires", "table")}


def own_result(analysis_id: str, auth: tuple[str, dict]) -> dict:
    token, _ = auth
    result = results.get(analysis_id)
    if not result or result["session_id"] != token:
        raise HTTPException(404, "Analysis not found in this session")
    return result


@app.get("/api/analysis/{analysis_id}")
def get_analysis(analysis_id: str, auth=Depends(session)):
    result = own_result(analysis_id, auth)
    return {key: (json.loads(value) if key == "supporting_data" else value) for key, value in result.items() if key not in ("session_id", "expires", "table")}


@app.get("/api/export/{analysis_id}/data")
def export_data(analysis_id: str, auth=Depends(session)):
    result = own_result(analysis_id, auth)
    return Response(result["table"].to_csv(index=False), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{result["chart_id"]}-evidence.csv"'})


def reproduction_script(result: dict) -> str:
    chart_id = result["chart_id"]
    builder = CHARTS[chart_id][4]
    source = result["python_source"]["visualization"]
    script = """import html\nimport pandas as pd\nimport numpy as np\nimport seaborn as sns\nimport matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nimport plotly.express as px\nimport folium\nfrom folium.plugins import MarkerCluster\nfrom statsmodels.graphics.mosaicplot import mosaic\n\n"""
    script += f"PALETTE = {PALETTE!r}\n\n{source}\n"
    script += f"table = pd.read_csv('chart_input.csv')\noutput = {builder.__name__}(table)\n"
    script += "output.write_html('chart.html')\n" if chart_id == "treemap" else "open('chart.html', 'w').write(output)\n" if chart_id == "map" else "output.savefig('chart.png', dpi=145, bbox_inches='tight')\n"
    return script


@app.get("/api/export/{analysis_id}/script")
def export_script(analysis_id: str, auth=Depends(session)):
    result = own_result(analysis_id, auth)
    return Response(reproduction_script(result), media_type="text/x-python", headers={"Content-Disposition": f'attachment; filename="{result["chart_id"]}-reproduce.py"'})


@app.get("/api/export/{analysis_id}/package")
def export_package(analysis_id: str, auth=Depends(session)):
    result = own_result(analysis_id, auth)
    chart_id = result["chart_id"]
    script = reproduction_script(result)
    meta = {"analysis_id": analysis_id, "chart_id": chart_id, "filters": result["filters"], "dataset_version": result["dataset_version"], "units": result["units"], "source": result["sources"], "limitations": result["limitations"]}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("reproduce.py", script)
        archive.writestr("chart_input.csv", result["table"].to_csv(index=False))
        archive.writestr("README.json", json.dumps(meta, indent=2))
        archive.writestr("requirements.txt", "pandas\nnumpy\nseaborn\nmatplotlib\nplotly\nfolium\nstatsmodels\n")
    return Response(buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{chart_id}-reproduction.zip"'})
