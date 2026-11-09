# Data sources and coverage

BayanViz ships with no observation dataset. Users upload their own weather and optional solar files, fetch Open-Meteo ERA5 weather, or fetch NASA POWER Daily solar data during multi-source validation. Uploaded and fetched weather remain separate choices in the dataset dropdown.

Weather uploads accept CSV, Parquet, or an Open-Meteo-style JSON download. Required fields are `location_id`, `date`, `model`, and at least one supported numeric weather field. Dates must be daily UTC dates for paired analysis; weather units are °C, mm, and km/h. Users declare the time standard. The app validates location IDs, duplicate location/date keys, and usable numeric fields.

Secondary solar uploads accept NASA POWER JSON or compatible CSV/Parquet. Tabular uploads need an explicit column mapping and user-declared solar unit and UTC daily time standard. NASA JSON retains reported parameter units and metadata. Solar is normalized to kWh/m²/day. These declarations are not independent proof of provider origin or quality.

Live API requests use registered point coordinates and bounded historical dates. A one-location, seven-day weather request is the default, and the standalone weather fetch supports up to 3,653 days for multi-year comparison. The solar API and paired-source workflow remain limited to 367 days. Successful provider responses are cached in process memory for six hours; cache entries disappear when the backend restarts. Provider latency and free-host cold starts can still delay or fail a request, so uploads remain a usable source path.

The integration report discloses coverage, missing values, matched and unmatched location/date pairs, conversion factors, and source metadata. A one-to-one left merge keeps every primary weather row and leaves missing solar values empty. Paired analyses use only valid pairs.

`data/reference/locations.json` contains five registered point coordinates in Visayas. These are point locations, not administrative averages or national coverage. The map uses OpenStreetMap background tiles, which are not an analytical dataset.
