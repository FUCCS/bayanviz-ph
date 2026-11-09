# Data dictionary

The app starts without observations. Users upload or fetch a weather dataset, then optionally upload or fetch a solar dataset. The weather key is `location_id, date, model`; paired integration requires one row per `location_id, date`. Accepted location IDs appear in `data/reference/locations.json`. They denote points, not province-wide averages.

| Field | Unit and meaning |
|---|---|
| `location_id` | Registered location ID |
| `date` | UTC daily date for integration |
| `model` | Source model or method identifier supplied by the uploader or API |
| `temperature_mean_c` | Daily mean air temperature, °C |
| `temperature_min_c` | Daily minimum air temperature, °C |
| `temperature_max_c` | Daily maximum air temperature, °C |
| `precipitation_mm` | Daily accumulated precipitation, mm |
| `wind_speed_max_kmh` | Daily maximum wind speed, km/h |
| `analysis_time_standard` | Declared daily boundary, `UTC` for integration |
| `nasa_solar_radiation` | Uploaded solar value in the source-reported unit |
| `nasa_solar_radiation_kwh_m2_day` | Normalized solar energy, kWh/m²/day |
| `nasa_temperature_mean_c` | Optional secondary-source temperature, °C |
| `merge_status` | `matched_date` or `primary_only` |

A weather upload needs `location_id`, `date`, `model`, and at least one supported numeric climate field. CSV, Parquet, and supported weather JSON are accepted. Duplicate location/date keys and unknown location IDs are rejected. Users declare UTC daily dates. The header-only download template contains no example observations.

The optional solar file needs mapped `location_id`, `date`, and `nasa_solar_radiation`. A NASA POWER JSON download can supply units and time standard in its metadata. Tabular files require user-declared solar units and UTC; optional comparison temperature requires a Celsius unit. Uploaded source claims are not independently verified.

Validation rejects duplicate secondary keys, unsupported units, non-UTC daily standards, and periods without matching location/date pairs. A one-to-one left merge keeps every primary day; missing solar values remain missing. The quality report discloses matched dates and valid paired solar coverage separately. Pearson correlation needs at least three nonconstant valid pairs and does not establish causation.

Weather analysis retains missing measurements and flags unusual values. Rainy days have at least 1 mm precipitation. Monthly and annual rainfall totals are summed per location, never across locations. Complete-period comparisons require at least 90% valid daily values.
