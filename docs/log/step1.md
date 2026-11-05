# Step 1: Valhalla tiles for the corridor and one matched bus trace

Date: 2026-11-05, 21:00 to 21:30 UTC. Machine: Apple M3 Pro, 18 GB, macOS 26.6.2, OrbStack (Docker 29.8.2, arm64, 12 CPUs, 9.4 GB for the VM). Everything below was run; outputs are quoted in short. "Unverified" marks what was not checked.

## 1. OSM data

Download of the Geofabrik SoCal extract (ODbL, data to 2026-11-03):

```
curl -sS -L -o data/downloads/socal-latest.osm.pbf https://download.geofabrik.de/north-america/us/california/socal-latest.osm.pbf
200 672615407 bytes 138.5 s
md5 8c35a27da56c18b25ea3bed58374d193 (matches socal-latest.osm.pbf.md5 on the server)
```

Decision: clip to the corridor instead of building the whole extract. The full build was not attempted (unverified whether it fits in 9.4 GB). The corridor is the stated scope and the clip is a 54 MB file. Laziness protocol: the smallest input that proves the engine.

Clip path that worked: osmium-tool in Docker. `iboates/osmium:latest` has a linux/arm64 build (digest `sha256:a83d327427c3653cd5419a8b1832b1def760b5a23172c5cdb7bc48edd2ac1f08`, osmium 1.19.0). The image's entrypoint is `osmium`, so the command starts with the subcommand. Bounding box is the corridor plus a 2 km margin (0.022 deg of longitude, 0.018 deg of latitude at this latitude).

```
docker run --rm -v "$PWD/data/downloads:/data" iboates/osmium:latest extract \
  --strategy complete_ways --bbox -118.542,33.962,-118.198,34.108 \
  --overwrite -o /data/corridor.osm.pbf /data/socal-latest.osm.pbf
19.3 s wall. corridor.osm.pbf 54,447,194 bytes
osmium fileinfo -e: 6,704,606 nodes, 749,778 ways, 13,352 relations, last timestamp 2026-11-03T20:52:11Z
```

The container's memory was not measured (the `time -l` wrapper measured only the Docker client, 48 MB). Unverified. pyosmium was not needed and not tried.

The reported bounding box of the clip is wider than the request (lon -118.58 to -117.18) because `complete_ways` keeps every node of a way that crosses the edge. Expected.

## 2. Valhalla

Image `ghcr.io/valhalla/valhalla-scripted:latest` pulled 2026-11-05 21:01 UTC: linux/arm64, created 2026-11-05T19:39Z, Valhalla 3.9.1-652aefdaa. Pinned in `docker-compose.yml` by digest `sha256:1fbcad26388aad3b1af56b57127128fdc7e5a5536da852c293ffc13c9dc170b4`.

Service: port 8002, volume `./data/valhalla/custom_files:/custom_files` (gitignored), `corridor.osm.pbf` copied into it. Env: 4 server threads, elevation off, transit off, admins and time zones on, tar on, `use_tiles_ignore_pbf` so restarts reuse the tiles.

```
docker compose up -d            started 21:05:50 UTC
GET /status -> 200              at 21:06:11 UTC (23 s after start)
{"version":"3.9.1-652aefdaa","tileset_last_modified":1793912771,"available_actions":["route","locate","sources_to_targets","optimized_route","isochrone","trace_route","trace_attributes","height","transit_available","expansion","centroid","status","tile"]}
```

Build log, in short: 169,667 routable ways, 902,184 nodes, 5,336 turn restrictions, 409,348 graph edges, 284,399 graph nodes, 5 tiles at level 0 to 2. Output `valhalla_tiles/` 67 MB and `valhalla_tiles.tar` 65 MB. The time-zone database (123 MB) and the default speeds config come from the image, not from a download (observed: both appeared within seconds, no download line in the log).

A forced rebuild (`force_rebuild: "True"` once, then reverted) to measure memory: 27 s from `up` to `/status` 200, peak container memory 402 MiB sampled every second with `docker stats`.

Known limitation: the admin database holds 0 areas. The clip cuts the California boundary relation, so Valhalla logs `California (165475) is degenerate and will be skipped`. Effects: no country or state on edges and the drive-on-right default applies (correct for LA). Fix when it matters: build admins from the full extract or clip with `--strategy smart`. Unverified whether this changes any match.

Probe with three hand-typed points near Wilshire and San Vicente (`shape_match: map_snap`) returned edges named "West San Vicente Boulevard" then "Wilshire Boulevard" with OSM way ids, so the graph covers the corridor and the request shape in `docs/research/tooling.md` is right. The path is `POST /trace_attributes`, confirmed by the request working and by `available_actions` in `/status`. The docs page (valhalla.github.io/valhalla/api/map-matching/) does not print the path.

## 3. The bus trace

`prep/trace_match.py` runs in the uv venv (`pyproject.toml`: Python 3.12, `gtfs-realtime-bindings` 3.0, `httpx` 0.28, `pydantic`; pyright standard mode passes with 0 errors). It has two commands, `collect` and `match`, so a trace can be matched again after a tile rebuild without polling again. Idempotent: `match` overwrites its two output files for the same trace.

Finding at the boundary: the Big Blue Bus realtime feed fills `trip.route_id` with the route short name ("2", "R12"), not the GTFS `route_id` (4099). The script joins on `route_short_name` from `routes.txt` and keeps route 2, R10 and any route whose long name contains "Wilshire" (that adds route 16, Wilshire Bl/Bundy Dr). The feed header advances every 30 s, so a 20 s poll repeats one feed in three; points are deduplicated on (vehicle id, timestamp).

```
.venv/bin/python prep/trace_match.py collect --minutes 10 --interval 20
21:03:38 to 21:13:39 UTC, 31 polls, 11 vehicles per poll on the kept routes
wrote data/traces/bbb_wilshire_20261105T211338Z.geojson: 210 points from 10 vehicles, 21 points each
```

R10 had no vehicles in the feed at this hour (13:00 Pacific; it is a peak express). Route 2 had 6 vehicles, 5 of them inside the corridor box.

## 4. Map matching

```
.venv/bin/python prep/trace_match.py match data/traces/bbb_wilshire_20261105T211338Z.geojson
```

Request per vehicle: `POST http://localhost:8002/trace_attributes` with `shape` = [{lat, lon, time: epoch seconds}], `costing: auto`, `shape_match: walk_or_snap`, `trace_options: {search_radius: 50, gps_accuracy: 10}` and an include filter for edge names, way id, length, speed, road class, shape indices, matched point, type, edge index, distances, and `shape`. All ten requests answered 200 in under one second each (the whole run took 1 s). The response has no `confidence_score` (the docs do not list one for `trace_attributes`), so the script reports matched, interpolated and unmatched point counts instead.

| Vehicle | Route | Points | Matched / interpolated / unmatched | Edges | km | On Wilshire | Edge names |
|---|---|---|---|---|---|---|---|
| 2117 | 2 | 21 | 18 / 3 / 0 | 120 | 3.39 | 100% | Wilshire Boulevard |
| 2508 | 2 | 21 | 20 / 1 / 0 | 136 | 2.64 | 100% | Wilshire Boulevard |
| 2106 | 2 | 21 | 18 / 3 / 0 | 112 | 2.19 | 91% | Wilshire Boulevard, 4th Street |
| 1320 | 2 | 21 | 17 / 4 / 0 | 59 | 1.71 | 1% | UCLA campus streets, Westwood Boulevard, Wilshire Boulevard |
| 1318 | 2 | 21 | 9 / 12 / 0 | 29 | 0.69 | 0% | 4th Street, Main Street, Olympic Drive (Santa Monica terminal) |
| 1349 | 2 | 21 | 8 / 13 / 0 | 25 | 0.79 | 0% | UCLA campus streets |
| 1601 | 16 | 21 | 16 / 5 / 0 | 69 | 1.97 | 31% | Colorado Avenue, South Centinela Avenue, Wilshire Boulevard |
| 2501, 2509, 2515 | 16 | 21 each | 20/1/0, 17/4/0, 19/2/0 | 80 to 90 | 2.1 to 3.5 | 0% | Venice, Washington, Lincoln, Olympic (the Playa del Rey leg) |

"Interpolated" points are ones Valhalla placed by interpolation between matched neighbours; the high counts for 1318 and 1349 are vehicles standing at a terminal, where consecutive positions repeat.

Checks on the two 100% vehicles (from `_matched.json`):

```
2117 trip 918030: snap distance mean 12.2 m, max 16.2 m; 79 distinct OSM ways, all road_class primary; 3.39 km in 599 s = 20.3 km/h
2508 trip 5030:   snap distance mean 8.7 m,  max 12.2 m; 79 distinct OSM ways, all road_class primary; 2.64 km in 595 s = 15.9 km/h
2117 raw points span lon -118.475 to -118.448, lat 34.038 to 34.058 (Santa Monica to Westwood along Wilshire)
matched_edges.geojson: 810 LineString features, 0 degenerate, 388 named Wilshire
```

Result files:

- `data/traces/bbb_wilshire_20261105T211338Z.geojson`: the raw points (EPSG:4326) with vehicle id, route, trip id, epoch timestamp.
- `data/traces/bbb_wilshire_20261105T211338Z_matched.json`: per vehicle, the counts above, the full edge list (names, way id, length km, speed, road class, shape indices) and the matched points.
- `data/traces/bbb_wilshire_20261105T211338Z_matched_edges.geojson`: one LineString per matched edge, cut from Valhalla's polyline6 `shape` by the edge shape indices, with names and OSM way id. Open it in QGIS over OSM to see the path.

These three files are committed as the evidence for this step (about 0.5 MB).

## 5. Verified and not verified

Verified: the extract's md5; the clip's object counts; Valhalla `/status` 200 and the action list; tiles rebuilt twice with the same result; the matched edges for the Wilshire vehicles carry the name "Wilshire Boulevard" and OSM way ids; zero unmatched points over 210; pyright clean; the script ran end to end on live data.

Not verified: whether the full 641 MB extract builds inside the 9.4 GB VM; the clip's container memory; the effect of the empty admin database on matching; the matched path drawn over a basemap (the GeoJSON exists, nobody has looked at it on a map yet); edge-level speeds (this step only proves matching, speed inference is step 3).

## 6. What the next step inherits

- `docker compose up -d` brings Valhalla back in a few seconds from the tar; replace the PBF and set `force_rebuild` once to rebuild.
- `prep/trace_match.py` is the pattern for the poller: decode, filter on short name, dedupe on (vehicle, timestamp), POST to `trace_attributes`. The join on short name must be checked per agency.
- Route ends and terminals produce off-route edges and repeated points; dwell exclusion (plan 3.2) needs the stop positions.
- R10 (Rapid 10) had no vehicles at 13:00 Pacific; collect a trace during a weekday peak to cover it.
