# Data dictionary

## Weather

Observation key: location_id, date, model. Curated data is ERA5 historical reanalysis with Asia/Manila daily dates at five representative city coordinates. It is not a municipal station record or a province-wide average.

| Field | Meaning |
|---|---|
| location_id | Registry key in data/reference/locations.json |
| date | Local daily observation date |
| model | era5 in curated data |
| temperature_mean_c, temperature_min_c, temperature_max_c | 2 m air temperature in °C |
| precipitation_mm | Daily precipitation total in mm |
| wind_speed_max_kmh | Maximum daily 10 m wind speed in km/h |

Missing observations remain missing. Duplicate observation keys keep the latest row. Unusual values are flagged for review, not silently dropped. Monthly rainfall sums valid daily depths per sample location; only complete months enter the heatmap. Annual rainfall in the bubble chart requires complete years. Line and box charts can show mean, minimum, or maximum daily temperature, or daily precipitation; line grouping is annual or monthly and always averages valid daily observations within the shown period. Temperature means use valid daily values.

## Facilities

Identity key: osm_type, osm_id. Chart categories are mutually exclusive, with priority pharmacy, clinic, bank, ATM, school, and selected retail. A bank element tagged with an ATM stays in the bank category; a separately mapped ATM element is counted as an ATM. Features are not merged solely by name. Same-name, same-category features within an approximate 0.001° coordinate cell are flagged as suspected duplicates and kept for review.

| Field | Meaning |
|---|---|
| osm_type, osm_id | OSM element identity |
| name | Escaped before map popups |
| category | pharmacy, clinic, bank, atm, retail, or school |
| latitude, longitude | Feature point or center |
| province, region | Approximate sample-area assignment; not verified administrative membership |
| snapshot_date | Retrieval date, not establishment date |
| source_tags | Original OSM tags as JSON |
| assignment_method | How the feature was assigned to a sample area |
| suspected_duplicate | Possible duplicate representation; never automatically merged by name |

Retail means supermarket, convenience, department_store, or grocery; chemist is pharmacy. Missing OSM features do not prove actual absence. Current features must not be treated as historical 2010–2025 counts. No facilities-per-resident metric is calculated.

## Upload mapping

Weather requires location ID, date, and model. Facilities require OSM type/ID, category, coordinates, province, and region. CSV, Parquet, Point GeoJSON, supported Open-Meteo JSON, and bounded Overpass JSON are parsed. Open-Meteo JSON without explicit model metadata is labeled `uploaded_model_unspecified`; a nearby registered sample point is matched only within approximately 0.14 degrees. Overpass elements are assigned to a nearby registered center for demonstration, not by validated boundaries; elements farther away are excluded. Polygon GeoJSON must be preprocessed offline. Limits: 5 MB, 50,000 rows, 60 columns, and three files per session. The downloadable templates contain illustrative values only.
