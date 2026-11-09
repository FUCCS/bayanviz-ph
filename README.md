# BayanViz PH

Philippine historical climate analytics for the Chapter 08 and Chapter 09 training exercises. This is an educational project, not an official KOICA service.

## Run locally

Use Python 3.14 for the pinned visualization dependencies:

    python3.14 -m venv .venv
    .venv/bin/python -m pip install -r backend/requirements-dev.txt
    MPLCONFIGDIR=/tmp/bayanviz-mpl .venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

In another terminal:

    cd frontend
    npm ci
    npm run dev

Open http://127.0.0.1:5173. For hosting, set `VITE_API_BASE_URL` to the backend HTTPS origin.

## Use your own source data

No observations are bundled or loaded by default. Open **Dataset Upload**, choose a weather CSV, Parquet, or supported Open-Meteo JSON file, map its columns, declare UTC daily dates, and click **Use Dataset**. The required weather columns are `location_id`, `date`, and `model`; provide at least one of `temperature_mean_c`, `temperature_min_c`, `temperature_max_c`, `precipitation_mm`, or `wind_speed_max_kmh`. Units are °C, mm, and km/h. The header-only template is in `frontend/public/weather-template.csv`.

For paired analyses, open **Multi-Source Manager** and upload a NASA POWER JSON download or compatible solar CSV/Parquet. Map `location_id`, `date`, and `nasa_solar_radiation`; optional `nasa_temperature_mean_c` supports temperature comparison. Declare the source-reported units and UTC daily standard, then validate and merge. The app matches exact location/date keys, reports missing and unmatched records, and keeps absent secondary values missing. No external climate API call is made during this workflow.

Uploads are limited to 5 MB, 50,000 rows, 60 columns, and three files per anonymous session by default. Uploaded files and analysis results are kept in process memory for three hours and vanish after a backend restart. The location IDs must match `data/reference/locations.json`; the five registered coordinates are point locations, not province-wide averages. The application cannot infer national coverage from these locations.

## Test

    MPLCONFIGDIR=/tmp/bayanviz-mpl .venv/bin/python -m pytest -q
    cd frontend && npm test -- --run && npm run build

See `docs/` for source definitions, deployment, and the demonstration guide.
