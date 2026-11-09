# Data sources and coverage

BayanViz ships with no observation dataset. Users upload their own weather and optional solar files. The app does not fetch climate data from external APIs during analysis.

Weather uploads accept CSV, Parquet, or an Open-Meteo-style JSON download. Required fields are `location_id`, `date`, `model`, and at least one supported numeric weather field. Dates must be daily UTC dates for paired analysis; weather units are °C, mm, and km/h. Users declare the time standard. The app validates location IDs, duplicate location/date keys, and usable numeric fields.

Secondary solar uploads accept NASA POWER JSON or compatible CSV/Parquet. Tabular uploads need an explicit column mapping and user-declared solar unit and UTC daily time standard. NASA JSON retains reported parameter units and metadata. Solar is normalized to kWh/m²/day. These declarations are not independent proof of provider origin or quality.

The integration report discloses coverage, missing values, matched and unmatched location/date pairs, conversion factors, and source metadata. A one-to-one left merge keeps every primary weather row and leaves missing solar values empty. Paired analyses use only valid pairs.

`data/reference/locations.json` contains five registered point coordinates in Visayas. These are point locations, not administrative averages or national coverage. The map uses OpenStreetMap background tiles, which are not an analytical dataset.
