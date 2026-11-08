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

Missing measurements remain missing. Duplicate keys retain the last row. Unusual values are flagged for review and are not silently deleted. Rainy means at least 1 mm of daily precipitation. Monthly and annual rainfall totals are summed per location, never across locations. Complete-period comparisons require at least 90% valid daily values; partial periods are identified with valid and expected day counts. Monthly rainfall averages use qualifying monthly totals. Temperature means use valid observations. Rainfall boxes retain genuine extremes.

Pearson correlations pair temperature, rainfall, or wind by the same location and day or qualifying month. At least three pairs and variation in both variables are required. Correlation describes association, not causation. Map circle size uses mean daily rainfall over valid days; marker color uses mean temperature.

The normalized upload requires location_id, date, and model, plus at least one supported numeric climate field. CSV, Parquet, and supported Open-Meteo JSON are parsed. Open-Meteo JSON without model metadata is labeled uploaded_model_unspecified. Only a nearby registered coordinate is mapped to a named registry point; unmatched uploaded locations can be charted but are omitted from the map until coordinates are validated. Limits default to 5 MB, 50,000 rows, 60 columns, and three files per session. The downloadable template contains illustrative values.
