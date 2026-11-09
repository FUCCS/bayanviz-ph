# BayanViz PH

Philippine Historical Climate Data Analytics. Subtitle: Advanced Data Visualization and Geographic Analysis.

This educational demonstration follows Chapter 08 (Advanced Data Visualization) and Chapter 09 (Map Data Analysis) of the supplied training presentation. It is not an official KOICA service.

## Run locally

Use Python 3.14 for the pinned visualization dependencies:

    python3.14 -m venv .venv
    .venv/bin/python -m pip install -r backend/requirements-dev.txt
    MPLCONFIGDIR=/tmp/bayanviz-mpl .venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

In a second terminal:

    cd frontend
    npm ci
    npm run dev

Open http://127.0.0.1:5173. For a hosted backend, set VITE_API_BASE_URL to its verified HTTPS origin.

The checked-in real ERA5 snapshot contains 3,655 daily rows for five representative Visayas sample points in 2024–2025. It is partial geographic and temporal coverage, not a nationwide dataset or province-wide mean. The offline weather ingestion script supports the 2010–2025 study period for the registered points. Expand and validate the location registry before making nationwide claims.

## Test

    MPLCONFIGDIR=/tmp/bayanviz-mpl .venv/bin/python -m pytest -q
    cd frontend && npm run test && npm run build

Use the Overview date-range controls to select dates within the loaded dataset, choose a sample location or Negros Oriental, and Apply Filters. The eight climate KPI cards, Chapter 08 visualizations, two relationship plots, and Chapter 09 Folium map use the selected weather rows. Each chart exposes its actual Python builder, deterministic interpretation, evidence, and reproduction package. API Sources fetches bounded Open-Meteo periods; Dataset Upload accepts compatible CSV, Parquet, or weather JSON in a private session.

Multi-Source Manager adds live Open-Meteo ERA5 and NASA POWER daily retrieval, UTC compatibility validation, a one-to-one left merge, source comparison, six paired-source charts/maps, source manifests, and quality reports. A separate analysis date range reruns paired calculations on filtered observations. The original Asia/Manila analytics and upload path remain available. The live manager currently uses registered sample points, up to three locations and 367 dates per request; upload mapping is weather-only, and full-period integrated datasets require a separate bounded retrieval workflow.

The five-point snapshot is a presentation fallback. The API is limited to three points and 367 days per visitor request. The full 2010–2025 period is prepared separately with:

    .venv/bin/python scripts/fetch_weather.py --start 2010-01-01 --end 2025-12-31

Review provider terms and quotas before a bulk fetch. Matplotlib uses the noninteractive Agg backend and a rendering lock. Plotly figures are generated in Python; Folium uses OpenStreetMap only for background tiles. Session uploads and results expire after three hours and disappear on restart.

See docs for course mapping, data definitions, source coverage, deployment, and a demonstration walkthrough.
