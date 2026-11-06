# Step 3: the poller, bus positions to segment speeds

Date: 2026-11-06, 01:00 to 02:10 UTC (2026-11-05, 17:00 to 18:10 Pacific, a Thursday evening peak). Everything below was run; outputs are quoted in short. "Unverified" marks what was not checked.

## 1. Inputs from step 2

`prep/copy_base.py <run dir>` copies the qgis-mcp run `20261105-141314-la-corridor-base-layers` into `data/base/` (gitignored) and writes `data/base/SOURCE.json` with the run id and each file's size and sha256. Copied: segments (26,829, EPSG:32611), shapes (702), stops (8,253), detectors, cameras, corridor.pmtiles and the four GTFS GeoPackages (their `trips` tables give trip_id to shape_id and route_id; 47,721 trips). The QGIS repository was only read.

Segments carry primary, secondary, tertiary, motorway and link classes plus busway and trunk. No residential or unclassified streets. Route ends on such streets therefore have no segment to charge.

## 2. What the feeds really send (checked live, 01:17 UTC)

| Agency | Feed | Vehicles | trip_id matches GTFS | route_id in feed |
|---|---|---|---|---|
| Big Blue Bus | protobuf | 137 | 0 of 137 (the feed's trip ids are not the GTFS trip ids) | the route short name ("2", "R12") |
| Culver CityBus | protobuf | 35 | 29 of 35 | the GTFS route_id, which equals the short name ("2", "6R") |
| LADOT | protobuf | 229 | 229 of 229 | empty |
| LA Metro | WebSocket `wss://api.metro.net/ws/LACMTA/vehicle_positions` | about 1,700 | the GTFS trip ids ("10014005111601-JUNE26") | the GTFS route_id ("14-13201"), plus `route_code` "14" |

The Metro WebSocket sends every vehicle every 3 s as one JSON object each (5,170 messages in 12 s), GTFS-Realtime VehiclePosition in camelCase: `vehicle.trip.tripId`, `routeId`, `position.latitude/longitude/bearing/speed`, `timestamp` as an epoch string. The docs (lacmta.github.io/metro-api-v2/docs/websockets) say the route-code path segment is optional and omitting it sends all routes; there is no "all" keyword. No key, no header.

Consequences in the code: `BaseData.candidate_shapes` resolves trip to shape first, then route_id against the GTFS route_id, then against the short name. Big Blue Bus therefore always goes through its route's shapes, and LADOT through its trip.

## 3. The package

`poller/` in the uv venv, pyright standard mode clean:

- `model.py`: `Observation` (agency, vehicle_id, trip_id, route_id, ts, lon, lat, bearing), `SegmentSpeed`, `PairResult`; the corridor box; pyproj transformers EPSG:4326 to EPSG:32611. Every distance is in metres in UTM 11N.
- `feeds.py`: `GtfsRtFeed` (httpx, User-Agent `la-live/0.1`) and `MetroWebSocketFeed` (a thread with an asyncio loop that keeps the newest message per vehicle and reconnects after 5 s on any error). Both satisfy the `Feed` protocol, so a Swiftly protobuf feed with an Authorization header would be one more `GtfsRtFeed`.
- `base.py`: loads the GeoPackages with pyogrio (no geopandas); segments, shapes, stops, trips; indexes by osm_id, route_id and short name.
- `shape_index.py`: for each shape, the part inside the corridor box (plus 1.5 km) is posted to Valhalla `trace_attributes` (`costing: bus`, `map_snap`, search radius 40 m). Each returned edge's midpoint is matched to the nearest segment with the same osm_id within 20 m; the edge ends are projected onto the full shape to get from_m and to_m. Stops of the same agency within 30 m of the line are projected too. Cached in `data/base/shape_index.json` (8 MB). `python -m poller build-index` rebuilds it.
- `speed.py`: the pair rules from the brief. Order: gap > 120 s; no candidate shape; shape without segments; either point > 50 m off the shape; moved backwards by more than 20 m; along-shape distance < 20 m; either end within 30 m (along the shape) of a stop; speed > 110 km/h. A kept pair charges its speed to every mapped segment between the two projections, weight = metres covered. The shape is chosen among the candidates within 50 m, preferring one the vehicle moves forward along, and locked for the vehicle only after a kept pair.
- `store.py`: SQLite (WAL) with natural primary keys: positions (agency, vehicle_id, ts), pairs (agency, vehicle_id, ts1), observations (agency, vehicle_id, ts, segment_id). `INSERT OR IGNORE` makes a replay a no-op. On start the engine is seeded with the latest position per vehicle from the store.
- `snapshot.py`: atomic writes (tmp then rename) of `live/vehicles.geojson`, `live/segments.geojson` (length-weighted mean over the last 600 s, n, age) and `live/status.json`.
- `__main__.py`: `run --minutes N --interval 30`.

Tests (`tests/test_speed.py`, 9, synthetic straight shape in UTM with two 500 m segments and a stop at 250 m): 300 m in 30 s gives 36.0 km/h on segment 1 with weight 100 m and segment 2 with weight 200 m; a pair ending 10 m from the stop is "dwell"; a 121 s gap is "gap" and the next pair is kept; short, backwards and too_fast; the Big Blue Bus short-name join without a trip id; a reverse shape is chosen when the vehicle moves the other way; the store is idempotent and a seeded engine does not re-pair; the Metro JSON and a protobuf message parse to the same `Observation`.

```
.venv/bin/pyright poller/ tests/ prep/   -> 0 errors
.venv/bin/python -m pytest -q tests      -> 9 passed in 0.43s
```

## 4. Shape index

```
python -m poller build-index   (Valhalla 3.9.1 from step 1, 4 workers)
702 shapes in 19 s: end_to_end 559, partial 77, no_segments 66, errors 0
  metro 428 / 39 / 59, ladot 78 / 11 / 7, bbb 25 / 21 / 0, ccb 28 / 6 / 0  (end to end / partial / none)
```

"End to end" means at least 95% of the shape's metres inside the corridor are covered by mapped segments and Valhalla reported no unmatched point. A first build that traced whole shapes had 27 Valhalla errors ("map_snap algorithm failed") and 434 partials: those shapes leave the corridor where there are no tiles. Tracing only the in-corridor pieces removed every error. The 66 shapes with no segments and most partials run on residential streets that the segments layer does not hold (Big Blue Bus route ends, LADOT DASH loops). Median coverage of the partials is 0.92.

Dwell detection uses stop geometry within 30 m of the shape, not `stop_times`. It is simpler and needs no trip join, at the cost of also excluding the opposite direction's stop across the street. Conservative by design; `stop_times` can refine it later.

## 5. The live run

```
.venv/bin/python -m poller run --minutes 25 --interval 30
01:25:41 to 01:50:44 UTC (17:25 to 17:50 Pacific, Thursday evening peak), 51 cycles, no feed errors, no WebSocket reconnect
```

Feeds at the last cycle (vehicles in feed / inside the corridor box): Big Blue Bus 136 / 131, Culver CityBus 44 / 42, LADOT 219 / 124, Metro 1,736 / 922 (769,000 WebSocket messages in 25 minutes). Distinct vehicles with positions in the corridor over the run: Metro 1,006, LADOT 144, Big Blue Bus 134, Culver CityBus 44.

Positions stored: 39,639 (Metro 25,290, Big Blue Bus 6,471, LADOT 6,250, Culver CityBus 1,628). Pairs by outcome:

| Outcome | All | Metro | BBB | LADOT | CCB | Meaning |
|---|---|---|---|---|---|---|
| kept | 6,605 | 3,487 | 1,319 | 1,494 | 305 | charged to segments |
| dwell | 13,066 | 8,260 | 2,090 | 2,157 | 559 | an end within 30 m of a stop |
| short | 8,782 | 4,965 | 1,759 | 1,677 | 381 | under 20 m along the shape |
| no_shape | 5,643 | 4,418 | 795 | 236 | 194 | no candidate shape within 50 m |
| gap | 3,082 | 3,063 | 0 | 18 | 1 | over 120 s between reports |
| off_shape | 1,125 | 482 | 212 | 359 | 72 | a point over 50 m from the shape |
| backwards | 795 | 298 | 211 | 176 | 110 | moved against the shape by over 20 m |
| no_segment | 346 | 153 | 85 | 105 | 3 | the covered stretch has no mapped segment |
| unmapped_shape | 179 | 153 | 0 | 26 | 0 | shape with no segments at all |
| too_fast | 16 | 11 | 0 | 2 | 3 | over 110 km/h |

Reading the drops. Dwell is the largest because stops on these routes sit every 200 to 300 m and a bus moves 100 to 200 m in 30 s, so most pairs touch a stop window; this is the rule as specified, and it is conservative. Short is mostly vehicles standing (layovers, red lights). Metro's no_shape pairs come from 283 vehicles on 91 trips; 90 of those trips are in the GTFS and their routes have a corridor shape, but the vehicle is more than 50 m from every corridor shape of its route: they run patterns (branches, short turns) whose shape the step 2 layer does not hold. Metro's gaps (mean 179 s, max 1,615 s) are vehicles whose reported timestamp stops advancing for minutes; the WebSocket keeps re-sending the old position. Backwards distances for Metro average 421 m and reach 24 km: a vehicle reassigned to a new trip while its shape stays locked until the trip id changes. Unverified: each of these explanations was read from the numbers, not confirmed vehicle by vehicle.

Observation rows: 23,523 on 8,471 distinct segments (of 26,829). Kept pairs had a 30 s gap for the polled feeds and 38 s on average for Metro.

Speeds, km/h, length-weighted rows:

| Set | n | p10 | p50 | p90 | mean |
|---|---|---|---|---|---|
| all corridor observations | 23,523 | 5.9 | 18.8 | 40.6 | 22.1 |
| Wilshire Boulevard segments | 1,829 | 7.8 | 21.4 | 40.1 | 22.6 |
| Wilshire, Metro only | 1,366 | | | | 25.4 (weighted) |
| Wilshire, Big Blue Bus only | 378 | | | | 23.9 (weighted) |
| primary roads | 12,052 | 5.7 | 18.4 | 38.1 | 20.5 |
| secondary | 8,015 | 5.9 | 18.3 | 38.8 | 20.9 |
| motorway (express buses) | 1,166 | 13.4 | 43.3 | 92.4 | 48.7 |

Plausibility for 17:25 to 17:50 on a Thursday: bus speeds between stops on Wilshire of 20 to 25 km/h with a tenth of the pairs under 8 km/h, and freeway buses at 43 km/h median with a p90 of 92, read as an evening peak. The numbers are bus speeds between stops, not car speeds; the ratio to a baseline (step 6) is what the map will colour. Unverified: no independent speed source was compared (step 4 brings the Caltrans detectors for the freeways).

Snapshots at the last cycle: `live/segments.geojson` 4,888 segment features with a speed in the last 10 minutes (381 of them Wilshire, median 24.2 km/h), `live/vehicles.geojson` 1,217 vehicles seen in the last 5 minutes, `live/status.json` with per-feed counts, errors and the pair counters.

## 6. Restart check

```
.venv/bin/python -m poller run --minutes 1   (right after the 25-minute run)
seeded 1328 vehicles from history; cycle 1 kept 64 pairs at once
rows before: positions 39639, pairs 39639, observations 23523
rows after:  positions 41786, pairs 41786, observations 25184; duplicate (agency, vehicle_id, ts) keys: 0
```

Seeding from the store means the first cycle after a restart already pairs with the last stored position. Only new rows were added.

## 7. Verified, not verified, inherited

Verified: every feed read live with zero errors over 25 minutes; the Big Blue Bus short-name join and the LADOT trip join on real data; the shape index numbers; the synthetic tests; the restart idempotency on the real store; snapshot files exist and are valid GeoJSON (loaded back by the report script).

Not verified: the drop explanations above; the speeds against any other source; Culver CityBus vehicles whose trip ids are not in the GTFS (6 of 35) and LADOT trip ids of the form "30-gb6wnfq" that are not in the trips table (regenerated daily, the copy is from 2026-11-05 13:51); the two Metro feeds the WebSocket replaces (Swiftly) were not used; the effect of the empty Valhalla admin database on the shape index.

For the next steps: `prep/step3_report.py` reruns every number above from the store. The 30 m dwell window drops two thirds of pairs; using `stop_times` to exclude only scheduled stops of the vehicle's direction would recover the opposite-direction stops. The segments layer needs residential streets where routes end (or those stretches stay unmeasured). `live/segments.geojson` at 1.5 MB per cycle is fine locally; the viewer step should read it with a 30 s refresh as planned.
