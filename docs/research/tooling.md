# LA live transit and traffic: tooling inventory

Date: 2026-11-05. Facts from pages fetched that day by a research agent; anything not seen on a page is marked "not confirmed". Nothing was downloaded or run. Local checks on this Mac are in the last section.

## Map matching

| Item | Finding |
|---|---|
| Valhalla | MIT. Release 3.9.1 (2026-10-06). Images `ghcr.io/valhalla/valhalla` and `ghcr.io/valhalla/valhalla-scripted` (env-driven tile build), linux/arm64 published for `latest` (`latest-arm64`); a `3.9.1-arm64` tag was not seen. The old gis-ops/docker-valhalla repo is archived. |
| Valhalla build | `docker run -dt --name valhalla -p 8002:8002 -v $PWD/custom_files:/custom_files ghcr.io/valhalla/valhalla-scripted:latest`, drop a PBF into `custom_files`, `docker restart valhalla`. Env vars: `tile_urls`, `force_rebuild`, `build_tar`, `server_threads`, `build_admins`, `build_time_zones`, `build_elevation`, `build_transit`. Port 8002. |
| Valhalla matching | Actions `trace_route` and `trace_attributes`, POST JSON: `shape` = [{lat, lon, time?, type?}], `costing` (any but multimodal), `shape_match` = edge_walk / map_snap / walk_or_snap, `trace_options.search_radius` (m, max 100), `gps_accuracy`. The literal URL paths were not printed on the docs page (conventionally `/trace_attributes`): not confirmed. |
| OSRM | BSD-2. v26.11.0, image `ghcr.io/project-osrm/osrm-backend`, multi-arch incl. arm64 (`latest-arm64-debian`). Pipeline osrm-extract, osrm-partition, osrm-customize, osrm-routed (port 5000). `GET /match/v1/{profile}/{coords}` with `timestamps`, `radiuses`, `gaps`, `tidy`; response `tracepoints`, `matchings[].confidence`. Docker Hub `osrm/osrm-backend` is stale and amd64 only. |
| OSM extract | Geofabrik SoCal: `socal-latest.osm.pbf`, 641 MB, data to 2026-11-03, ODbL 1.0. https://download.geofabrik.de/north-america/us/california/socal.html |

## GTFS-Realtime in Python

`gtfs-realtime-bindings` 3.0.0 (2026-09-17), Apache 2.0, needs Python 3.10+ and `protobuf>=7.34.0`; `from google.transit import gtfs_realtime_pb2`; `FeedMessage().ParseFromString(bytes)`. protobuf backends: `upb` (default in wheels), `python` (pure, via `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python`). arm64 wheels for protobuf 7.34: not confirmed (irrelevant if it runs in the host venv, which takes compiled wheels).

## Vehicle detection in camera images

| Tool | Licence | Apple Silicon | Note |
|---|---|---|---|
| Ultralytics YOLO (8.4.203, YOLO26) | AGPL-3.0, or a paid Enterprise licence | MPS supported (`device=mps`) | AGPL means the whole project that uses it must be released under AGPL-3.0, including a hosted service. Usable for a public open-source project; `solutions.ObjectCounter` counts line crossings on video. |
| YOLOX | Apache-2.0 | MPS not confirmed | Last update 2023. |
| RT-DETR (official) | Apache-2.0 | MPS not confirmed | RT-DETRv4 (2025-11). Ultralytics also ships RT-DETR, but under AGPL. |
| Detectron2 | Apache-2.0 | Apple Silicon not confirmed (install notes show x86_64 only) | |
| PyTorch MPS | BSD | macOS 15+ on Apple Silicon | |

Still images refreshed once a minute give per-frame counts, density and a queue indicator per camera. They cannot give speed, flow, direction or line-crossing counts: every vehicle has left the frame before the next frame. Those need video or a burst of frames plus a camera-to-ground calibration. (Engineering reasoning, not from a page.)

## Web map

| Tool | Version | Licence | Mac |
|---|---|---|---|
| MapLibre GL JS | 6.16.0 (2026-10-25) | BSD-3 | browser |
| pmtiles JS | 4.5.0 | BSD-3; spec CC0 | `maplibregl.addProtocol("pmtiles", protocol.tile)`, source `pmtiles://https://.../x.pmtiles` |
| go-pmtiles CLI | 1.31.2 | BSD-3 | Homebrew bottles for Apple Silicon |
| tippecanoe (felt) | 2.79.0 | BSD-2 | Homebrew bottles for Apple Silicon; `-o x.pmtiles` |

PMTiles on a static host needs HTTP range requests (GitHub Pages serves them; not confirmed from the PMTiles page).

## GitHub Actions as a poller

Schedule minimum is every 5 minutes, delays happen under load and runs can be dropped; scheduled workflows in a public repo are disabled after 60 days without activity. Public repos run standard runners free; GitHub Free includes $12 of compute per month for private repos (about 2,000 Linux minutes, derived). Pages: 1 GB site, 100 GB/month soft bandwidth, 10 builds per hour unless published by a custom Actions workflow. Conclusion: Actions can publish a snapshot every 5 minutes for free, never every minute.

## This Mac (checked 2026-11-05)

- Apple M3 Pro, 18 GB, macOS 26.6.2. No Docker, no Colima, no Podman, no Homebrew installed.
- GDAL 3.13.3 in QGIS.app reads GTFS zips (`GTFS` driver), reads OSM PBF (`OSM` driver), and reads and writes PMTiles and MVT. tippecanoe is therefore optional: `ogr2ogr -f PMTiles roads.pmtiles roads.gpkg` works in principle (not yet run).
- The qgis-mcp host venv has no protobuf yet.
