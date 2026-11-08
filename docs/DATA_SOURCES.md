# Data sources and coverage

The checked-in files are a **partial real-data subset**, not national coverage. The two JSON manifests in data/manifests record retrieval time, row count, period, checksum, license, and transformation history.

- Weather: [Open-Meteo Historical Weather](https://open-meteo.com/en/docs/historical-weather-api), ERA5 model, daily 2024–2025 observations at five representative points. The free API is for [noncommercial use with limits](https://open-meteo.com/en/terms). Attribution: Open-Meteo, ERA5/ECMWF.
- Facilities: © OpenStreetMap contributors, ODbL 1.0. A one-time bounded [Overpass](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html) query collected features within 6 km of five city centers. This is not a province boundary extract and is not a live production connector. The dashboard reads a prepared CSV. Nearest-center assignments require boundary validation before province-wide claims.
- Tiles: OSM standard tiles with visible attribution. Respect the [tile policy](https://operations.osmfoundation.org/policies/tiles/); do not bulk fetch.

The [Geofabrik Philippines PBF](https://download.geofabrik.de/asia/philippines.html) was approximately 584 MB on 2026-11-08 and was not committed. `scripts/prepare_osm.py` accepts a PBF and a separate reviewed administrative-boundary file, spatially assigns point and way features, and writes compact CSV plus an audit manifest. It also accepts a reviewed GeoPackage/GeoJSON with `area_id`, `province`, and `region`. PBF processing requires ample temporary disk for its node-location index. OSM relation features are omitted and must be reviewed separately. National processing must include authoritative boundaries, independent cities, NCR, and unmatched features before claiming completeness. The present sample omits that national coverage and does not treat out-of-scope facilities as zero.

The versioned location registry contains representative city coordinates in Negros Oriental, Negros Occidental, Cebu, Bohol, and Siquijor. Historical weather and current mapped features have different reference dates; plots do not imply causation or historical facility existence.
