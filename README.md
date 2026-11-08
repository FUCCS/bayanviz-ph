# BayanViz PH

Philippine Climate and Infrastructure Analytics. Explore the Data. Understand the Visuals. Learn the Python.

This educational demonstration follows Chapter 08 (Advanced Data Visualization) and Chapter 09 (Map Data Analysis) of the supplied course presentation. It is not an official KOICA information service.

## Run locally

Use Python 3.14. This project's pinned Matplotlib version had no wheel for Python 3.15 on this Mac.

    python3.14 -m venv .venv
    .venv/bin/python -m pip install -r backend/requirements-dev.txt
    MPLCONFIGDIR=/tmp/bayanviz-mpl .venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

In a second terminal:

    cd frontend
    npm ci
    npm run dev

Open http://127.0.0.1:5173. Use the VITE_API_BASE_URL environment variable for a nonlocal backend.

The checked-in snapshot is partial real data: five ERA5 sample locations for 2024–2025 and a bounded OSM city-vicinity snapshot. The dashboard never claims national completeness.

## Test

    .venv/bin/python -m pytest -q
    cd frontend && npm run test && npm run build

The Python builders, course registry, aggregation, API, and scripts are in backend/app and scripts. Data provenance is in data/manifests. See docs for course mapping, schema, source limitations, deployment, and demonstration.

For offline PBF, GeoPackage, or GeoJSON processing, install `scripts/requirements-offline.txt` in a separate environment if needed. The public API does not require GeoPandas or PyOsmium. `scripts/prepare_osm.py --help` lists boundary and snapshot-date inputs; its output includes separate unassigned and ambiguous review files.

`scripts/fetch_weather.py` prepares a selected historical ERA5 period in bounded calendar-year chunks. Its default period is 2010–2025; run it deliberately after reviewing provider terms and quotas. The checked-in presentation snapshot remains the smaller 2024–2025 subset.

The app uses Matplotlib's Agg backend and a rendering lock. Plotly figures are generated in Python and delivered as figure JSON. Folium generates map HTML. Temporary uploads and results are process-local for three hours and disappear on restart.
