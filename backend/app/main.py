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
from .connectors import fetch_weather
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


@app.get("/api/health")
def health():
    return {"ok": True, "service": "BayanViz PH"}


@app.post("/api/session")
def start_session():
    prune()
    token = secrets.token_urlsafe(32)
    sessions[token] = {"expires": time.time() + SESSION_TTL, "uploads": {}, "weather": None, "weather_source": None, "retrieved_at": None, "requests": []}
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
        "charts": [{"id": key, "chapter": row[0], "topic": row[1], "function": row[2], "objective": row[3], "needs": row[5]} for key, row in CHARTS.items()],
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
    state["weather"] = prepare_weather(frame)
    state["weather_source"] = "Cached API" if cache_hits == len(request.locations) else "Live API"
    state["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    actual = date_bounds(state["weather"])
    return {"status": state["weather_source"], "records": len(frame), "errors": errors, "cache_hits": cache_hits, "preview": frame.head(8).to_dict("records"), "retrieved_at": state["retrieved_at"], "observation_period": [actual["min_date"], actual["max_date"]], "variables": [name for name in WEATHER_COLUMNS if state["weather"][name].notna().any()]}


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
    return {"upload_id": upload_id, "filename": name, "rows": len(df), "columns": list(df.columns), "preview": preview, "missing_by_column": {str(col): int(df[col].isna().sum()) for col in df.columns}, "required_weather": ["location_id", "date", "model"], "observation_period": [str(dates.min().date()), str(dates.max().date())] if len(dates) else None}


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
        cleaned = prepare_weather(mapped)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    count = len(cleaned)
    if not count:
        raise HTTPException(400, "No valid records remained after validation")
    if not any(cleaned[name].notna().any() for name in WEATHER_COLUMNS):
        raise HTTPException(400, "No supported numeric climate measurements found")
    state["weather"] = cleaned
    state["weather_source"] = "Uploaded Dataset"
    state["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    return {"status": "Uploaded Dataset", "valid_records": count, "observation_period": [str(cleaned.date.min().date()), str(cleaned.date.max().date())], "variables": [name for name in WEATHER_COLUMNS if cleaned[name].notna().any()]}


@app.post("/api/analysis")
def analyze(request: AnalysisRequest, auth=Depends(session)):
    token, state = auth
    if request.chart_id not in CHARTS:
        raise HTTPException(404, "Unknown chart")
    try:
        weather, weather_status, _ = selected_weather(request.source, state)
        unknown = set(request.locations) - set(weather.location_id.unique())
        if unknown:
            raise ValueError(f"Locations are absent from the selected dataset: {', '.join(sorted(unknown))}")
        weather = filter_weather(weather, request.locations, str(request.start_date) if request.start_date else None, str(request.end_date) if request.end_date else None)
        table, units, caveats, facts = prepare_chart(request.chart_id, weather, request.variable, request.aggregation, request.relationship_aggregation)
        if table.empty:
            raise ValueError("No valid observations after filtering")
        figure = render_chart(request.chart_id, table)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    filters = request.model_dump(mode="json")
    version = f"weather:{hashlib.sha256(weather.to_csv(index=False).encode()).hexdigest()[:12]}"
    source_list = [weather_status, "Open-Meteo ERA5 reanalysis" if weather_status != "Uploaded Dataset" else "Uploaded weather dataset"]
    result_id = secrets.token_urlsafe(24)
    record = {
        "analysis_id": result_id, "dataset_version": version, "filter_hash": filter_hash(filters), "chart_id": request.chart_id,
        "renderer": "Folium" if request.chart_id == "map" else "Plotly for Python" if request.chart_id == "treemap" else "Seaborn / Matplotlib" if request.chart_id != "mosaic" else "Statsmodels / Matplotlib",
        "figure": figure, "supporting_data": table.head(30).replace({pd.NaT: None}).to_json(orient="records", date_format="iso"),
        "supporting_columns": list(table.columns), "supporting_count": len(table),
        "computed_facts": interpretation(request.chart_id, table, caveats, facts), "python_source": code_for(request.chart_id),
        "statistics": facts,
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
    script = """import html\nimport pandas as pd\nimport numpy as np\nimport seaborn as sns\nimport matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nimport plotly.express as px\nimport folium\nfrom statsmodels.graphics.mosaicplot import mosaic\n\n"""
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
