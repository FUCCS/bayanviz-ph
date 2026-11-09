from __future__ import annotations

import asyncio
import hashlib
import inspect
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

from .catalog import LOCATIONS, manifest, weather_frame
from .charts import PALETTE, REGISTRY as CHARTS, code_for, interpretation, prepare_chart, render_chart
from .connectors import fetch_nasa, fetch_weather, parse_nasa_payload
from .multisource import MS_BUILDERS, MS_CHARTS, merge_sources, multisource_code, multisource_interpretation, normalize_nasa, prepare_multisource_chart, render_multisource_chart, validate_sources
from .processing import MIN_PERIOD_COVERAGE, RAINY_DAY_MM, WEATHER_COLUMNS, climate_summary, date_bounds, filter_hash, filter_weather, period_table, prepare_weather, safe_number

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
    variables: list[str] = Field(default_factory=lambda: WEATHER_COLUMNS.copy())


class UseUploadRequest(BaseModel):
    upload_id: str
    mapping: dict[str, str] = Field(default_factory=dict)


class AnalysisRequest(BaseModel):
    chart_id: str
    source: str = "curated"
    locations: list[str] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    variable: str = "temperature_mean_c"
    aggregation: str = "annual"
    relationship_aggregation: str = "daily"


class ScopeRequest(BaseModel):
    source: str = "curated"
    locations: list[str] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None


class MultiValidateRequest(BaseModel):
    locations: list[str] = Field(min_length=1, max_length=3)
    start_date: date
    end_date: date
    weather_variables: list[str] = Field(default_factory=lambda: WEATHER_COLUMNS.copy())
    nasa_variables: list[str] = Field(default_factory=lambda: ["solar_radiation", "nasa_temperature_mean_c"])
    include_nasa: bool = True
    method: str = "merge"
    nasa_upload_id: str | None = None
    nasa_mapping: dict[str, str] = Field(default_factory=dict)
    nasa_solar_unit: str | None = None
    nasa_temperature_unit: str | None = None
    nasa_time_standard: str | None = None


class MultiMergeRequest(BaseModel):
    validation_id: str


def mapped_nasa_upload(upload: pd.DataFrame, request: MultiValidateRequest) -> tuple[pd.DataFrame, dict]:
    """Treat user files as declared, unverified source data, never as a live API response."""
    allowed = {"location_id", "date", "nasa_solar_radiation", "nasa_temperature_mean_c", "latitude", "longitude"}
    mapping = request.nasa_mapping
    if set(mapping) - allowed or len(set(mapping.values())) != len(mapping.values()) or set(mapping.values()) - set(upload):
        raise ValueError("The uploaded NASA column mapping is invalid or ambiguous")
    frame = upload.rename(columns={source: target for target, source in mapping.items()}).copy()
    if frame.columns.duplicated().any() or not {"location_id", "date", "nasa_solar_radiation"}.issubset(frame):
        raise ValueError("Uploaded secondary data needs distinct location, date, and solar radiation columns")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce", utc=True).dt.tz_localize(None)
    if frame.date.isna().any():
        raise ValueError("Uploaded secondary data contains invalid dates")
    frame["location_id"] = frame.location_id.astype(str).str.strip()
    frame = frame[frame.location_id.isin(request.locations) & frame.date.between(pd.Timestamp(request.start_date), pd.Timestamp(request.end_date))].copy()
    if frame.empty:
        raise ValueError("Uploaded secondary data has no selected location-date records")
    trusted_metadata = upload.attrs.get("nasa_metadata", {})
    if trusted_metadata:
        if set(frame.location_id) - set(trusted_metadata):
            raise ValueError("Uploaded NASA JSON metadata does not cover every selected location")
        metadata = {key: trusted_metadata[key].copy() for key in frame.location_id.unique()}
    else:
        if request.nasa_time_standard != "UTC" or not request.nasa_solar_unit:
            raise ValueError("Declare UTC daily time and the source-reported solar unit for uploaded tabular data")
        if "nasa_temperature_mean_c" in frame and frame.nasa_temperature_mean_c.notna().any() and request.nasa_temperature_unit not in {"C", "°C", "degC"}:
            raise ValueError("Declare a supported Celsius unit for uploaded comparison temperature")
        metadata = {}
        for key, group in frame.groupby("location_id"):
            grid_lat = pd.to_numeric(group["latitude"], errors="coerce").dropna().iloc[0] if "latitude" in group and pd.to_numeric(group["latitude"], errors="coerce").notna().any() else None
            grid_lon = pd.to_numeric(group["longitude"], errors="coerce").dropna().iloc[0] if "longitude" in group and pd.to_numeric(group["longitude"], errors="coerce").notna().any() else None
            parameters = {"ALLSKY_SFC_SW_DWN": {"units": request.nasa_solar_unit}}
            if "nasa_temperature_mean_c" in frame and frame.nasa_temperature_mean_c.notna().any():
                parameters["T2M"] = {"units": request.nasa_temperature_unit}
            metadata[key] = {"provider": "Uploaded secondary climate data", "dataset_version": "uploaded-declared", "parameter_metadata": parameters, "grid_latitude": grid_lat, "grid_longitude": grid_lon, "time_standard": "UTC"}
    return frame[[col for col in ["location_id", "date", "nasa_solar_radiation", "nasa_temperature_mean_c"] if col in frame]], metadata


@app.get("/api/health")
def health():
    return {"ok": True, "service": "BayanViz PH"}


@app.post("/api/session")
def start_session():
    prune()
    token = secrets.token_urlsafe(32)
    sessions[token] = {"expires": time.time() + SESSION_TTL, "uploads": {}, "weather": None, "weather_source": None, "retrieved_at": None, "requests": [], "multisource": None, "merged": None}
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
        "name": "BayanViz PH", "subtitle": "Philippine Historical Climate Data Analytics",
        "locations": list(LOCATIONS.values()),
        "charts": [{"id": key, "chapter": row[0], "topic": row[1], "function": row[2], "objective": row[3], "needs": row[5]} for key, row in CHARTS.items()] + [{"id": key, "chapter": "Climate integration", "topic": row[0], "function": row[1], "objective": row[2], "needs": "merged"} for key, row in MS_CHARTS.items()],
        "variables": WEATHER_COLUMNS,
        "upload_limit_mb": MAX_UPLOAD_MB, "upload_limit_rows": MAX_UPLOAD_ROWS,
        "upload_limit_columns": MAX_UPLOAD_COLUMNS, "uploads_per_session": MAX_UPLOADS_PER_SESSION,
    }


@app.get("/api/sources")
def sources():
    weather = weather_frame()
    prepared = prepare_weather(weather)
    quality = {
        "valid_temperature_records": int(prepared.temperature_mean_c.notna().sum()),
        "missing_temperature_records": int(prepared.temperature_mean_c.isna().sum()),
        "flagged_weather_outliers": int(prepared.outlier_flag.sum()),
    }
    return {
        "weather": {"status": "Prepared Real Dataset" if len(weather) else "Unavailable", "records": len(weather), "manifest": manifest("weather")},
        "live_weather": {"status": "Live API", "provider": "Open-Meteo Historical Weather", "model": "ERA5", "interactive_max_days": 367, "interactive_max_locations": 3, "supported_period": ["2010-01-01", "2025-12-31"]},
        "live_solar": {"status": "Live API", "provider": "NASA POWER Daily", "parameters": ["ALLSKY_SFC_SW_DWN", "T2M"], "time_standard": "UTC", "interactive_max_days": 367, "interactive_max_locations": 3},
        "coverage": "Partial coverage — five registered representative weather points in the prepared snapshot, 2024–2025.",
        "quality": quality,
    }


def selected_weather(source: str, state: dict) -> tuple[pd.DataFrame, str, dict]:
    if source not in {"curated", "session"}:
        raise ValueError("Choose the prepared or session dataset")
    if source == "session" and state["weather"] is not None:
        return state["weather"].copy(), state["weather_source"], {"retrieved_at_utc": state["retrieved_at"]}
    return prepare_weather(weather_frame()), "Prepared Real Dataset", manifest("weather")


@app.post("/api/dataset")
def dataset(request: ScopeRequest, auth=Depends(session)):
    _, state = auth
    try:
        weather, status, metadata = selected_weather(request.source, state)
        unknown = set(request.locations) - set(weather.location_id.unique())
        if unknown:
            raise ValueError(f"Locations are absent from the selected dataset: {', '.join(sorted(unknown))}")
        bounds = date_bounds(weather, request.locations)
        subset = weather[weather.location_id.isin(request.locations)] if request.locations else weather
        if request.start_date:
            subset = subset[subset.date.ge(pd.Timestamp(request.start_date))]
        if request.end_date:
            subset = subset[subset.date.le(pd.Timestamp(request.end_date))]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    variables = [name for name in WEATHER_COLUMNS if weather[name].notna().any()]
    return {**bounds, "available_locations": sorted(weather.location_id.unique().tolist()), "selected_observations": len(subset), "selected_valid_temperature": int(subset.temperature_mean_c.count()), "selected_valid_rainfall": int(subset.precipitation_mm.count()), "source": status, "variables": variables, "retrieved_at": metadata.get("retrieved_at_utc"), "model": sorted(weather.model.unique().tolist())}


@app.post("/api/summary")
def summary(request: ScopeRequest, auth=Depends(session)):
    _, state = auth
    try:
        weather, status, metadata = selected_weather(request.source, state)
        selected = filter_weather(weather, request.locations, str(request.start_date) if request.start_date else None, str(request.end_date) if request.end_date else None)
        bounds = date_bounds(weather, request.locations)
        reference = weather[weather.location_id.isin(request.locations)] if request.locations else weather
        return climate_summary(selected, str(request.start_date or bounds["min_date"]), str(request.end_date or bounds["max_date"]), metadata, status, reference)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/summary/code")
def summary_code(request: ScopeRequest, auth=Depends(session)):
    result = summary(request, auth)
    return {"source": inspect.getsource(climate_summary), "preparation": inspect.getsource(period_table), "summary": result}


@app.post("/api/summary/package")
def summary_package(request: ScopeRequest, auth=Depends(session)):
    _, state = auth
    result = summary(request, auth)
    weather, status, metadata = selected_weather(request.source, state)
    selected = filter_weather(weather, request.locations, str(request.start_date) if request.start_date else None, str(request.end_date) if request.end_date else None)
    reference = weather[weather.location_id.isin(request.locations)] if request.locations else weather
    script = "import calendar\nimport json\nimport numpy as np\nimport pandas as pd\n\n"
    script += f"RAINY_DAY_MM = {RAINY_DAY_MM!r}\nMIN_PERIOD_COVERAGE = {MIN_PERIOD_COVERAGE!r}\n\n"
    script += inspect.getsource(safe_number) + "\n" + inspect.getsource(period_table) + "\n" + inspect.getsource(climate_summary) + "\n"
    script += "weather = pd.read_csv('weather_input.csv', parse_dates=['date'])\nreference = pd.read_csv('reference.csv', parse_dates=['date'])\n"
    script += f"result = climate_summary(weather, {result['selected_period'][0]!r}, {result['selected_period'][1]!r}, {metadata!r}, {status!r}, reference)\n"
    script += "print(json.dumps(result, indent=2))\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("reproduce_summary.py", script)
        archive.writestr("weather_input.csv", selected.to_csv(index=False))
        archive.writestr("reference.csv", reference.to_csv(index=False))
        archive.writestr("README.json", json.dumps({"filters": request.model_dump(mode="json"), "source": status, "model": result["model"], "note": "Run python reproduce_summary.py after installing pandas and numpy."}, indent=2))
        archive.writestr("requirements.txt", "pandas\nnumpy\n")
    return Response(buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="climate-summary-reproduction.zip"'})


@app.post("/api/summary/script")
def summary_script(request: ScopeRequest, auth=Depends(session)):
    package = summary_package(request, auth)
    with zipfile.ZipFile(io.BytesIO(package.body)) as archive:
        script = archive.read("reproduce_summary.py")
    return Response(script, media_type="text/x-python", headers={"Content-Disposition": 'attachment; filename="reproduce_summary.py"'})


@app.post("/api/fetch/weather")
async def fetch(request: FetchRequest, auth=Depends(session)):
    token, state = auth
    if len(state["requests"]) >= 12:
        raise HTTPException(429, "Session request limit reached")
    state["requests"].append(time.time())
    try:
        if not request.variables or set(request.variables) - set(WEATHER_COLUMNS):
            raise ValueError("Select one or more supported climate variables")
        frame, errors, cache_hits = await fetch_weather(request.locations, request.start_date, request.end_date, request.variables)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if frame.empty:
        raise HTTPException(502, {"message": "Provider returned no usable locations", "errors": errors})
    state["multisource"] = None
    state["merged"] = None
    for key, value in list(results.items()):
        if value["session_id"] == token:
            del results[key]
    state["weather"] = prepare_weather(frame)
    state["weather_source"] = "Cached API" if cache_hits == len(request.locations) else "Live API"
    state["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    actual = date_bounds(state["weather"])
    return {"status": state["weather_source"], "records": len(frame), "errors": errors, "cache_hits": cache_hits, "preview": frame.head(8).to_dict("records"), "retrieved_at": state["retrieved_at"], "observation_period": [actual["min_date"], actual["max_date"]], "variables": [name for name in WEATHER_COLUMNS if state["weather"][name].notna().any()]}


@app.post("/api/multisource/validate")
async def multisource_validate(request: MultiValidateRequest, auth=Depends(session)):
    """Fetch and preview both providers without merging or changing active data."""
    _, state = auth
    if len(state["requests"]) >= 12:
        raise HTTPException(429, "Session request limit reached")
    state["requests"].append(time.time())
    if request.method not in {"merge", "compare"}:
        raise HTTPException(400, "Choose Merge Datasets or Compare Data Sources")
    if not request.weather_variables or set(request.weather_variables) - set(WEATHER_COLUMNS):
        raise HTTPException(400, "Choose supported Open-Meteo variables")
    if not request.include_nasa and request.method == "compare":
        raise HTTPException(400, "Source comparison requires NASA POWER")
    try:
        uploaded = request.nasa_upload_id is not None and request.include_nasa
        if uploaded:
            stored = state["uploads"].get(request.nasa_upload_id)
            if stored is None:
                raise ValueError("Uploaded secondary dataset is not in this anonymous session")
            upload_frame, upload_metadata = mapped_nasa_upload(stored, request)
        weather_result, nasa_result = await asyncio.gather(
            fetch_weather(request.locations, request.start_date, request.end_date, request.weather_variables, "UTC"),
            asyncio.sleep(0, result=(upload_frame, upload_metadata, [], 0)) if uploaded else fetch_nasa(request.locations, request.start_date, request.end_date, request.nasa_variables) if request.include_nasa else asyncio.sleep(0, result=(None, {}, [], 0)),
        )
        weather_raw, weather_errors, weather_cache_hits = weather_result
        nasa_raw, metadata, nasa_errors, nasa_cache_hits = nasa_result
        if weather_raw.empty or (request.include_nasa and nasa_raw.empty):
            raise ValueError("Provider returned no usable data: " + "; ".join(weather_errors + nasa_errors))
        if weather_raw.duplicated(["location_id", "date"]).any():
            raise ValueError("Open-Meteo returned duplicate location-date keys")
        weather = prepare_weather(weather_raw)
        nasa, transform = normalize_nasa(nasa_raw, metadata) if request.include_nasa else (None, None)
        report = validate_sources(weather, nasa, metadata, request.locations)
        if uploaded:
            report["providers"][1] = "Uploaded secondary climate data (declared NASA-compatible)"
            report["warnings"].append("Uploaded secondary values and declared units/time standard are user-supplied; they are not authenticated against NASA POWER.")
            report["secondary_origin"] = "user-upload"
        else:
            report["secondary_origin"] = "NASA POWER API" if request.include_nasa else None
        report["provider_errors"] = weather_errors + nasa_errors
        report["cache_hits"] = {"openmeteo": weather_cache_hits, "nasa": nasa_cache_hits}
        report["unit_transformation"] = transform
        if report["provider_errors"]:
            report["warnings"].append("Some requested locations failed; review provider errors before analysis.")
        validation_id = secrets.token_urlsafe(16)
        retrieved_at = datetime.now(timezone.utc).isoformat()
        state["multisource"] = {"validation_id": validation_id, "primary": weather, "secondary": nasa, "metadata": metadata, "report": report, "selection": request.model_dump(mode="json"), "retrieved_at": retrieved_at}
        return {"validation_id": validation_id, "report": report, "retrieved_at": retrieved_at, "primary_preview": json.loads(weather.head(8).to_json(orient="records", date_format="iso")), "nasa_preview": json.loads(nasa.head(8).to_json(orient="records", date_format="iso")) if nasa is not None else []}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/multisource/merge")
def multisource_merge(request: MultiMergeRequest, auth=Depends(session)):
    """Activate only the exact dataset that passed this session's validation."""
    token, state = auth
    validated = state.get("multisource")
    if not validated or request.validation_id != validated["validation_id"]:
        raise HTTPException(409, "Validate the selected datasets before merging")
    try:
        merged, quality = merge_sources(validated["primary"], validated["secondary"], validated["report"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    state["merged"] = merged
    state["weather"] = merged
    state["weather_source"] = " + ".join(validated["report"]["providers"])
    state["retrieved_at"] = validated["retrieved_at"]
    for key, value in list(results.items()):
        if value["session_id"] == token:
            del results[key]
    return {"status": quality["status"], "merge_quality": quality, "observation_period": [str(merged.date.min().date()), str(merged.date.max().date())], "records": len(merged), "source": state["weather_source"], "manifest": {"providers": validated["report"]["providers"], "requested_locations": validated["report"]["requested_locations"], "openmeteo_grid": validated["report"]["openmeteo_grid"], "nasa_grid": validated["report"]["nasa_grid"], "nasa_parameter_metadata": validated["report"]["nasa_parameter_metadata"], "unit_transformation": validated["report"]["unit_transformation"], "time_standard": "UTC", "retrieved_at": validated["retrieved_at"]}}


@app.get("/api/multisource/report")
def multisource_report(auth=Depends(session)):
    _, state = auth
    validated = state.get("multisource")
    if not validated:
        raise HTTPException(404, "No validated multi-source dataset in this session")
    return validated["report"]


@app.get("/api/multisource/report.csv")
def multisource_report_csv(auth=Depends(session)):
    report = multisource_report(auth)
    rows = [{"metric": key, "value": json.dumps(value, default=str) if isinstance(value, (dict, list)) else value} for key, value in report.items()]
    return Response(pd.DataFrame(rows).to_csv(index=False), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="merge-quality-report.csv"'})


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
    if suffix == ".json":
        payload = json.loads(content)
        if isinstance(payload, dict) and isinstance(payload.get("properties"), dict) and isinstance(payload.get("parameters"), dict):
            coordinates = payload.get("geometry", {}).get("coordinates", [])
            if not isinstance(coordinates, list) or len(coordinates) < 2:
                raise ValueError("NASA JSON needs point coordinates for location matching")
            lon, lat = coordinates[:2]
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                raise ValueError("NASA JSON has invalid point coordinates")
            nearby = min(LOCATIONS.values(), key=lambda loc: (loc["latitude"] - lat)**2 + (loc["longitude"] - lon)**2)
            if (nearby["latitude"] - lat)**2 + (nearby["longitude"] - lon)**2 >= 0.02:
                raise ValueError("NASA JSON point is not close to a registered sample location")
            selected = ("solar_radiation", "nasa_temperature_mean_c") if "T2M" in payload["properties"].get("parameter", {}) else ("solar_radiation",)
            frame, info = parse_nasa_payload(payload, nearby["id"], selected)
            frame.attrs["nasa_metadata"] = {nearby["id"]: info}
            return frame
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
        return pd.DataFrame(payload if isinstance(payload, list) else payload.get("data", []))
    raise ValueError("Use CSV, Parquet, or supported weather JSON")


@app.post("/api/upload/preview")
async def upload_preview(file: UploadFile = File(...), auth=Depends(session)):
    _, state = auth
    if len(state["uploads"]) >= MAX_UPLOADS_PER_SESSION:
        raise HTTPException(429, "This session has reached its upload limit; clear your data to start over")
    name = Path(file.filename or "upload").name
    if Path(name).suffix.lower() not in {".csv", ".parquet", ".json"}:
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
    date_column = df["date"] if "date" in df else df["time"] if "time" in df else None
    dates = pd.to_datetime(date_column, errors="coerce").dropna() if date_column is not None else pd.Series(dtype="datetime64[ns]")
    detected_nasa = next(iter(df.attrs.get("nasa_metadata", {}).values()), None)
    return {"upload_id": upload_id, "filename": name, "rows": len(df), "columns": list(df.columns), "preview": preview, "missing_by_column": {str(col): int(df[col].isna().sum()) for col in df.columns}, "required_weather": ["location_id", "date", "model"], "observation_period": [str(dates.min().date()), str(dates.max().date())] if len(dates) else None, "detected_nasa": {"solar_unit": detected_nasa["parameter_metadata"]["ALLSKY_SFC_SW_DWN"]["units"], "temperature_unit": detected_nasa["parameter_metadata"].get("T2M", {}).get("units"), "time_standard": detected_nasa["time_standard"], "location_id": detected_nasa["requested_location_id"]} if detected_nasa else None}


@app.post("/api/upload/use")
def use_upload(request: UseUploadRequest, auth=Depends(session)):
    token, state = auth
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
        cleaned = prepare_weather(mapped)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    count = len(cleaned)
    if not count:
        raise HTTPException(400, "No valid records remained after validation")
    if not any(cleaned[name].notna().any() for name in WEATHER_COLUMNS):
        raise HTTPException(400, "No supported numeric climate measurements found")
    state["multisource"] = None
    state["merged"] = None
    for key, value in list(results.items()):
        if value["session_id"] == token:
            del results[key]
    state["weather"] = cleaned
    state["weather_source"] = "Uploaded Dataset"
    state["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    return {"status": "Uploaded Dataset", "valid_records": count, "observation_period": [str(cleaned.date.min().date()), str(cleaned.date.max().date())], "variables": [name for name in WEATHER_COLUMNS if cleaned[name].notna().any()]}


@app.post("/api/analysis")
def analyze(request: AnalysisRequest, auth=Depends(session)):
    token, state = auth
    integrated = request.chart_id in MS_CHARTS
    if request.chart_id not in CHARTS and not integrated:
        raise HTTPException(404, "Unknown chart")
    try:
        if integrated and (request.source != "session" or state.get("merged") is None or (state.get("multisource") or {}).get("secondary") is None):
            raise ValueError("Validate and merge Open-Meteo with NASA POWER before this analysis")
        weather, weather_status, _ = selected_weather(request.source, state)
        unknown = set(request.locations) - set(weather.location_id.unique())
        if unknown:
            raise ValueError(f"Locations are absent from the selected dataset: {', '.join(sorted(unknown))}")
        weather = filter_weather(weather, request.locations, str(request.start_date) if request.start_date else None, str(request.end_date) if request.end_date else None)
        if integrated:
            table, units, facts, caveats = prepare_multisource_chart(request.chart_id, weather)
            if state["multisource"]["report"].get("secondary_origin") == "user-upload":
                caveats.append("Secondary values and declared units/time standard were uploaded by a visitor and are not authenticated NASA POWER API data.")
        else:
            table, units, caveats, facts = prepare_chart(request.chart_id, weather, request.variable, request.aggregation, request.relationship_aggregation)
        if table.empty:
            raise ValueError("No valid observations after filtering")
        figure = render_multisource_chart(request.chart_id, table) if integrated else render_chart(request.chart_id, table)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    filters = request.model_dump(mode="json")
    version = f"weather:{hashlib.sha256(weather.to_csv(index=False).encode()).hexdigest()[:12]}"
    source_list = state["multisource"]["report"]["providers"] if integrated else [weather_status, "Open-Meteo ERA5 reanalysis" if weather_status != "Uploaded Dataset" else "Uploaded weather dataset"]
    result_id = secrets.token_urlsafe(24)
    record = {
        "analysis_id": result_id, "dataset_version": version, "filter_hash": filter_hash(filters), "chart_id": request.chart_id,
        "renderer": "Folium" if request.chart_id in {"map", "solar_map"} else "Plotly for Python" if request.chart_id == "treemap" else "Seaborn / Matplotlib" if request.chart_id != "mosaic" else "Statsmodels / Matplotlib",
        "figure": figure, "supporting_data": table.head(30).replace({pd.NaT: None}).to_json(orient="records", date_format="iso"),
        "supporting_columns": list(table.columns), "supporting_count": len(table),
        "computed_facts": multisource_interpretation(request.chart_id, table, facts, caveats) if integrated else interpretation(request.chart_id, table, caveats, facts), "python_source": multisource_code(request.chart_id) if integrated else code_for(request.chart_id),
        "statistics": facts,
        "sources": source_list,
        "limitations": caveats, "units": units, "filters": filters, "sample_size": len(table),
        "session_id": token, "expires": time.time() + SESSION_TTL, "table": table,
    }
    if integrated:
        validated = state["multisource"]
        if validated["report"].get("secondary_origin") == "user-upload":
            record["python_source"]["preparation"] = inspect.getsource(mapped_nasa_upload) + "\n\n" + record["python_source"]["preparation"]
        record["primary_input"] = validated["primary"]
        record["nasa_input"] = validated["secondary"]
        record["source_manifest"] = {"report": validated["report"], "selection": validated["selection"], "retrieved_at": validated["retrieved_at"]}
    results[result_id] = record
    return {key: (json.loads(value) if key == "supporting_data" else value) for key, value in record.items() if key not in ("session_id", "expires", "table", "primary_input", "nasa_input", "source_manifest")}


def own_result(analysis_id: str, auth: tuple[str, dict]) -> dict:
    token, _ = auth
    result = results.get(analysis_id)
    if not result or result["session_id"] != token:
        raise HTTPException(404, "Analysis not found in this session")
    return result


@app.get("/api/analysis/{analysis_id}")
def get_analysis(analysis_id: str, auth=Depends(session)):
    result = own_result(analysis_id, auth)
    return {key: (json.loads(value) if key == "supporting_data" else value) for key, value in result.items() if key not in ("session_id", "expires", "table", "primary_input", "nasa_input", "source_manifest")}


@app.get("/api/export/{analysis_id}/data")
def export_data(analysis_id: str, auth=Depends(session)):
    result = own_result(analysis_id, auth)
    return Response(result["table"].to_csv(index=False), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{result["chart_id"]}-evidence.csv"'})


def reproduction_script(result: dict) -> str:
    chart_id = result["chart_id"]
    builder = MS_BUILDERS[chart_id] if chart_id in MS_BUILDERS else CHARTS[chart_id][4]
    source = result["python_source"]["visualization"]
    script = """import html\nimport pandas as pd\nimport numpy as np\nimport seaborn as sns\nimport matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nimport plotly.express as px\nimport folium\nfrom statsmodels.graphics.mosaicplot import mosaic\n\n"""
    script += f"PALETTE = {PALETTE!r}\n\n{source}\n"
    script += "table = pd.read_csv('chart_input.csv', parse_dates=['date'] if 'date' in pd.read_csv('chart_input.csv', nrows=0).columns else None)\n"
    if chart_id in MS_BUILDERS:
        script += "primary = pd.read_csv('openmeteo_input.csv', parse_dates=['date'])\nnasa = pd.read_csv('nasa_input.csv', parse_dates=['date'])\n"
        script += "merged = pd.merge(primary, nasa, on=['location_id', 'date'], how='left', validate='one_to_one', indicator=True)\nprint('Validated merged rows:', len(merged))\n"
    script += f"output = {builder.__name__}(table)\n"
    script += "output.write_html('chart.html')\n" if chart_id == "treemap" else "open('chart.html', 'w').write(output)\n" if chart_id in {"map", "solar_map"} else "output.savefig('chart.png', dpi=145, bbox_inches='tight')\n"
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
        if chart_id in MS_BUILDERS:
            archive.writestr("openmeteo_input.csv", result["primary_input"].to_csv(index=False))
            archive.writestr("nasa_input.csv", result["nasa_input"].to_csv(index=False))
            archive.writestr("source_manifest.json", json.dumps(result["source_manifest"], indent=2, default=str))
        archive.writestr("README.json", json.dumps(meta, indent=2))
        archive.writestr("requirements.txt", "pandas\nnumpy\nseaborn\nmatplotlib\nplotly\nfolium\nstatsmodels\n")
    return Response(buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{chart_id}-reproduction.zip"'})
