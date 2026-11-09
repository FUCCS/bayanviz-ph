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

No observations are bundled or loaded by default. Open **Dataset Upload**, choose a weather CSV, Parquet, or supported Open-Meteo JSON file, map its columns, declare UTC daily dates, and click **Use Dataset**. Alternatively, open **API Sources**, select a location and a short historical date range, click **Fetch Data**, then **Use Dataset**. Switch between uploaded and fetched weather in the dashboard's **Dataset** dropdown. The required weather columns are `location_id`, `date`, and `model`; provide at least one of `temperature_mean_c`, `temperature_min_c`, `temperature_max_c`, `precipitation_mm`, or `wind_speed_max_kmh`. Units are °C, mm, and km/h. The header-only template is in `frontend/public/weather-template.csv`.

For paired analyses, open **Multi-Source Manager** and choose uploaded weather or Open-Meteo ERA5 API as the primary source. Choose NASA POWER Daily API or upload a NASA POWER JSON download or compatible solar CSV/Parquet as the secondary source. For a solar upload, map `location_id`, `date`, and `nasa_solar_radiation`; optional comparison temperature, humidity, and wind fields have separate unit declarations. Declare the source-reported units and UTC daily standard, then validate and merge. Uploaded datasets can cover all registered regions; live API integration remains limited to three locations and 367 days when NASA POWER is selected. The app matches exact location/date keys one to one, reports missing and unmatched records on each side, and retains every primary row. The **Monthly and annual summaries** table is calculated in Python and paginated. The standalone weather API can fetch up to 3,653 daily-only days in smaller provider requests. If the backend's provider request fails, the API Sources page retries directly from the user's browser and sends the response to Python for validation of dates, units, coordinates, and record keys. Browser-relayed responses are labeled as such because the server cannot independently authenticate their origin. Successful server-side responses are cached in memory for six hours, while a free Render instance can still take time to wake.

The **Year-by-year temperature comparison** chart uses only calendar years with at least 90% valid daily mean temperatures at the same location. Its code, evidence table, and computed interpretation are available through the chart actions. It measures air temperature, not heat index.

Uploads have no application-level file-size or row cap by default (`MAX_UPLOAD_MB=0`, `MAX_UPLOAD_ROWS=0`), while retaining a 60-column and three-file limit per anonymous session. `UPLOAD_PARSE_TIMEOUT_SECONDS=0` disables the application-level parse deadline. Set positive values for these environment variables to impose limits. Hosting request duration, multipart buffering, and available RAM still set practical limits. The weather upload asks users to confirm the canonical column units; NASA CSV solar, temperature, humidity, and wind units are declared separately. Uploaded files and analysis results live in one backend process for three hours after the last request. The browser retains the session ID across refreshes in the same tab, but a backend restart or process replacement discards the data. **Download session backup** saves both uploaded source tables as a ZIP for user-managed persistence; reupload its CSVs in a new session. Render's free filesystem is ephemeral, so the backup must be kept outside Render. Location IDs must match `data/reference/locations.json`; the 21 registered coordinates cover 18 regions as sampling points, not region-wide averages. CSV coordinates, when supplied, are checked against the registered location. Backend memory and request duration still constrain much larger files or simultaneous users on a free instance.

## Test

    MPLCONFIGDIR=/tmp/bayanviz-mpl .venv/bin/python -m pytest -q
    cd frontend && npm test -- --run && npm run build

See `docs/` for source definitions, deployment, and the demonstration guide.
