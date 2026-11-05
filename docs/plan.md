# LA live transit and traffic: plan

Date: 2026-11-05. Status: plan, nothing built. Every source named here was fetched and checked the same day; the inventories are `docs/research/transit-feeds.md`, `docs/research/traffic-feeds.md` and `docs/research/tooling.md`. Anything not checked is marked UNVERIFIED.

## 1. What changed between the brainstorm and the facts

| Brainstorm assumption | What the sources say | Consequence |
|---|---|---|
| PeMS gives live freeway speeds | PeMS is login-walled and its real-time feeds need Caltrans approval as a reseller. Caltrans District 7 still publishes a deprecated ATMS text feed of detector speeds every minute, open, no key. | Freeway speeds come from the ATMS feed, labelled deprecated. PeMS, once you have a free account, supplies the historical baseline by hand export. |
| Every agency needs a key | Big Blue Bus, Culver CityBus and LADOT publish GTFS-Realtime with no key. LA Metro publishes a free, key-less WebSocket with JSON positions every 3 seconds that browsers may call. Only Metrolink needs a key. | The first version needs no registration at all. A Swiftly key for Metro's protobuf feeds and a Metrolink key are optional later. |
| Cameras give stills only | 592 Caltrans cameras in District 7; 573 refresh a still every 2 minutes and 486 offer a live 720p HLS video stream, open, with CORS. | Video makes counting, direction and calibrated speed estimates possible, which stills do not. |
| A map-matching engine is the hard part | Valhalla ships an arm64 image under MIT with map matching built in. | Docker Desktop is the only install on your side. |

## 2. The corridor

Westside to downtown: Santa Monica, Culver City, Mid-City, Koreatown, downtown. Bounding box lon -118.52 to -118.22, lat 33.98 to 34.09, about 28 by 12 km. Transit: Metro E Line and the Wilshire, Venice, Santa Monica Blvd, Pico and Olympic bus routes, Big Blue Bus, Culver CityBus, DASH. Freeways: I-10, I-405, US-101, I-110. Scaling to the county later is a bounding box change plus more agencies; nothing in the design is corridor-specific.

## 3. Architecture

Three parts, each a folder in a new repository `la-live` (name open), with qgis-mcp as a dependency for the preparation step.

### 3.1 Preparation (batch, qgis-mcp)

Run once, rerun when data changes. A `transit_corridor` recipe plus a GTFS entry in the format registry (GDAL's GTFS driver reads the zips as stops, routes, trips, shapes layers; verified present in the bundle).

Outputs, all in EPSG:26911 (NAD83 / UTM 11N) for analysis and EPSG:4326 for the browser:
- `segments`: OSM roads split at intersections, with a stable segment id, highway class, direction, length. From the Geofabrik SoCal extract (641 MB, ODbL), clipped to the corridor. The existing network recipe does the cleaning.
- `shapes`: GTFS route shapes per agency, each snapped to a sequence of segment ids through Valhalla `trace_attributes`, with the cumulative distance of every stop along the shape (needed to exclude dwell).
- `detectors`: ATMS VDS stations placed on freeway segments by route, direction and postmile. Their published lon/lat are to two decimals, about 1 km, so postmile matching is required. UNVERIFIED until tried.
- `cameras`: the CCTV records inside the corridor with their stream URLs and the segment each camera looks at.
- Vector tiles: `roads.pmtiles`, `transit.pmtiles` written by GDAL's PMTiles driver (verified present; writing not yet run), with a buildings and terrain layer for the 3D twin.

### 3.2 Poller (service, Python)

A long-running process, started with Docker Compose next to Valhalla. Every cycle (30 s for transit, 60 s for ATMS and CHP, 5 min for closures):

1. Fetch each feed. Decode protobuf with `gtfs-realtime-bindings` 3.0 (Apache 2); Metro over the WebSocket as JSON.
2. For each vehicle, keep the previous position. Snap both to the vehicle's trip shape (known from the trip id), compute distance along the shape and elapsed time. Drop the pair if either point is within a stop's dwell window or the gap exceeds 2 minutes. The result is a speed observation on the segments between the two points.
3. Read ATMS speeds per VDS, CHP incidents (parse the quoted XML and the LATLON encoding, drop truncated files by checking the closing tag), closures, signs.
4. Write a snapshot: `live/vehicles.geojson`, `live/segments.geojson` (speed, speed ratio, observation count, source, age), `live/events.geojson`, `live/cameras.geojson`. Append observations to a SQLite or Parquet history.
5. Baseline: per segment, day of week and hour, the median speed over the last 4 to 8 weeks of observations. Colours are the ratio of current to baseline, the way Google does it, not absolute speed. Until the history exists the map shows absolute speed and says so.

Everything the browser cannot call (no CORS, keys) goes through this process. It never serves the public; it writes files.

### 3.3 Viewer (static, MapLibre GL JS)

MapLibre GL JS 6.16 (BSD-3) with PMTiles 4.5 (BSD-3). Roads coloured by speed ratio with width by class, vehicles as moving points with route labels, incidents and closures as symbols, cameras as points with a thumbnail and the latest count, a time slider over the history, a layer list and the assumptions panel. The 3D twin lives in the same page: MapLibre draws terrain from a raster DEM tile set and extrudes buildings from the tiles, so there is one viewer, not a Cesium one beside it. Cesium stays for the inundation scenes. The page refreshes the snapshots every 15 to 30 seconds.

### 3.4 Cameras (phase 2, vision)

Per camera in the corridor, every few minutes: open the HLS stream, read 10 to 15 seconds, run a detector with a tracker, count vehicles crossing a line per direction, estimate speed from the tracks against a one-time calibration of the camera to the ground (four road points clicked on the image). Output per camera and direction: vehicles per minute, mean speed, density. This is a proxy for the segment the camera sees, shown as a point and, where the segment has no other source, used to colour it.

Honest limits: the counts are good, the speeds depend on the calibration, and the Caltrans rule against degrading the stream service means short samples, never continuous streaming of hundreds of cameras. The 2-minute stills are the fallback where a stream is down.

Licence decision needed. Ultralytics YOLO is the easiest tool and runs on Apple Silicon, but it is AGPL-3.0, which requires the whole project, hosted service included, to be released under AGPL-3.0. That is fine for an open project if you accept AGPL for the repository. The permissive alternatives (RT-DETR, YOLOX, Apache 2) have no confirmed Apple Silicon support and more setup. See section 7.

## 4. Hosting

- Now: everything on this Mac. Docker Compose runs Valhalla and the poller; the viewer is a folder served by `python3 -m http.server`.
- Unattended, free: a GitHub Actions workflow every 5 minutes (the minimum, with delays under load) that runs the poller once and publishes the snapshots and the viewer to GitHub Pages from a public repository. Map matching in Actions is possible with a prebuilt Valhalla tile tarball cached in the repository or a release asset. Latency 5 to 10 minutes, no history finer than that, no camera video. Public repositories pay nothing for runner minutes.
- Unattended, live: the Compose stack on a small VPS, 30-second cadence, history on disk, cameras sampled. The viewer stays on Pages. Cost a few dollars a month; the only non-free element, and optional.

## 5. Data model

```
segment      id, osm_way_id, highway, oneway, length_m, geom (LineString)
shape_segment shape_id, seq, segment_id, from_m, to_m
stop_on_shape shape_id, stop_id, dist_m
observation  ts, segment_id, source (bus|vds|camera), speed_kmh, weight, vehicle_id
baseline     segment_id, dow, hour, p50_kmh, n
detector     vds_id, segment_id, route, direction, postmile
camera       cctv_id, segment_id, stream_url, image_url, calibration (4 points)
```

## 6. Order of work, each step ending in a check

1. You install Docker Desktop (or OrbStack) and open it once. I build Valhalla tiles for the corridor from the SoCal extract and match one Big Blue Bus trace to prove the engine. Check: the matched edges follow Wilshire.
2. GTFS registry format and the `transit_corridor` recipe in qgis-mcp; roads, shapes, stops, detectors and cameras for the corridor; PMTiles written. Check: every shape snaps end to end; counts per agency match the zips.
3. Poller with Big Blue Bus, Culver CityBus, LADOT and the Metro WebSocket; bus-derived segment speeds; snapshots. Check: a bus on Wilshire during the evening peak shows a lower speed than at 6 am, and dwell exclusion removes the zeros.
4. ATMS, CHP, closures and signs in the poller. Check: a CHP incident on the I-10 appears within two minutes of its LogTime; VDS speeds match the colour codes Caltrans publishes in the same row.
5. Viewer with roads, vehicles, events, time slider, terrain and buildings. Check: in a browser, foreground tab, 30-second refresh.
6. Two weeks of history, then the baseline and ratio colours. Check: Sunday morning reads free-flow everywhere.
7. Cameras for the corridor subset. Check: counts from one stream against a hand count of the same 15 seconds.
8. Actions workflow publishing to Pages; VPS only if you want the 30-second version.

## 7. Decisions needed from you

1. Repository: a new `la-live` repository beside this one, or a folder inside it? I recommend a new one; it has its own service and licence.
2. Licence for that repository: AGPL-3.0 (lets us use Ultralytics for the cameras) or Apache-2.0/MIT (then RT-DETR or YOLOX, with unconfirmed Apple Silicon support, UNVERIFIED).
3. Docker Desktop or OrbStack on this Mac. Either is an install I cannot do for you.
4. Metrolink: request the key now, or leave Metrolink to the alerts feed, which is open?
5. PeMS account: worth applying now so the baseline can use their history, even though nothing live comes from it.

## 8. Attribution the viewer must carry

OpenStreetMap contributors (ODbL); LA Metro ("provided by LA Metro", data reproduced unmodified, no advertising use); City of Santa Monica Big Blue Bus (ODC-By); LADOT Transit with its required bold notice that the information is subject to change; Culver City (no terms stated); Caltrans (public domain unless indicated, camera images may carry additional rights, no warranty); CHP (no terms found); Geofabrik for the extract.
