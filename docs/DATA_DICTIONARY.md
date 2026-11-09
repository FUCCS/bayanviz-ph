# Data dictionary

The observation key is location_id, date, model. Curated data is daily ERA5 reanalysis in Asia/Manila time at representative coordinates. These are modeled estimates, not measurements from a station in each province or official province-wide averages.

| Field | Unit and meaning |
|---|---|
| location_id | Registered representative weather point |
| date | Daily observation date |
| model | Historical model identifier; curated data uses ERA5 |
| temperature_mean_c | Daily mean 2 m air temperature, °C |
| temperature_min_c | Daily minimum 2 m air temperature, °C |
| temperature_max_c | Daily maximum 2 m air temperature, °C |
| precipitation_mm | Daily accumulated precipitation, mm |
| wind_speed_max_kmh | Daily maximum 10 m wind speed, km/h |
| analysis_time_standard | Daily boundary used for source alignment; `UTC` in the integrated manager |
| openmeteo_grid_latitude / openmeteo_grid_longitude | Returned ERA5 grid coordinate, distinct from the requested point |
| nasa_solar_radiation | Original NASA POWER daily shortwave value in the provider-reported unit |
| nasa_solar_radiation_kwh_m2_day | Converted NASA POWER daily shortwave energy, kWh/m²/day |
| nasa_temperature_mean_c | NASA POWER daily 2 m temperature, °C, retained separately from Open-Meteo |
| merge_status | `matched_date` or `primary_only`; missing NASA measurement within a matched date remains missing |

Missing measurements remain missing. Duplicate keys retain the last row. Unusual values are flagged for review and are not silently deleted. Rainy means at least 1 mm of daily precipitation. Monthly and annual rainfall totals are summed per location, never across locations. Complete-period comparisons require at least 90% valid daily values; partial periods are identified with valid and expected day counts. Monthly rainfall averages use qualifying monthly totals. Temperature means use valid observations. Rainfall boxes retain genuine extremes.

Pearson correlations pair temperature, rainfall, or wind by the same location and day or qualifying month. At least three pairs and variation in both variables are required. Correlation describes association, not causation. Map circle size uses mean daily rainfall over valid days; marker color uses mean temperature.

The normalized upload requires location_id, date, and model, plus at least one supported numeric climate field. CSV, Parquet, and supported Open-Meteo JSON are parsed. Open-Meteo JSON without model metadata is labeled uploaded_model_unspecified. Only a nearby registered coordinate is mapped to a named registry point; unmatched uploaded locations can be charted but are omitted from the map until coordinates are validated. Limits default to 5 MB, 50,000 rows, 60 columns, and three files per session. The downloadable template contains illustrative values.

The integrated observation key is `location_id, date`. Validation rejects duplicate keys, unknown NASA units, missing UTC metadata, and nonoverlapping periods before merging. Paired coverage is 100 × primary rows with a valid NASA solar value / all selected primary rows. A matched date with a missing solar value counts as matched but not paired. Provider API versions, units, coordinates, and the unit conversion factor live in the source manifest and validation report. Pearson statistics require at least three valid pairs and nonconstant values; the correlation heatmap uses complete cases across all four plotted variables. Temperature difference means Open-Meteo minus NASA POWER, with neither source treated as ground truth.

Secondary uploads accept NASA POWER daily point JSON or compatible CSV/Parquet with manual mapping of location, date, solar radiation, optional temperature, and optional coordinates. JSON uses its returned units and time standard. Tabular files require an explicit solar unit and UTC declaration, plus a Celsius unit when comparison temperature is mapped. Uploaded source metadata is labeled user-declared and unauthenticated; non-UTC files cannot enter an exact daily merge.
