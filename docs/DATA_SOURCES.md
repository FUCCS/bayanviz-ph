# Data sources and coverage

The checked-in snapshot is a partial real-data subset, not nationwide coverage. The weather manifest in data/manifests records retrieval time, observation period, checksum, license information, row count, and transformations.

- Historical weather: [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api), ERA5 model. Daily 2024–2025 records for five representative points in Negros Oriental, Negros Occidental, Cebu, Bohol, and Siquijor. The [provider terms](https://open-meteo.com/en/terms) govern live and bulk requests. Attribute Open-Meteo and ERA5/ECMWF. ERA5 reanalysis combines observations and modeling; it is not a station record for every community.
- Map background: OpenStreetMap standard tiles with visible © OpenStreetMap contributors attribution. OSM is not an analytical dataset in this application. Respect the [tile policy](https://operations.osmfoundation.org/policies/tiles/); no bulk tile prefetching.

The location registry contains representative city coordinates, province labels, region labels, stable IDs, and a classification date. The coordinates are sample locations within provinces; data are not administrative-area averages. No validated national registry or full national ERA5 extract is checked in. scripts/fetch_weather.py prepares a chosen historical period in bounded chunks for the registered points and writes a manifest beside the output. Review and expand the registry with validated geographic identifiers and coordinates before claiming nationwide coverage.
