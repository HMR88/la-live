# Step 4: Caltrans detectors, CHP incidents, closures and signs in the poller

Date: 2026-11-06, 05:30 to 06:20 UTC (2026-11-05, 21:30 to 22:20 Pacific, a Thursday late evening). Everything below was run; outputs are quoted in short. "Unverified" marks what was not checked.

## 1. The sources as they really are (sampled 05:34 UTC)

| Source | Fetch | Content | Finding |
|---|---|---|---|
| ATMS `webupdate.txt` | 200, 65 KB, 0.4 s | 2,128 VDS rows, 179 CMS rows, `### Created: Thu Nov  5 21:34:37 2026` | Live: the HTTP Last-Modified header matched the clock to the second and the Created line is Pacific local time. 271 of 2,128 rows had a speed. Deprecated by Caltrans, said so in `status.json`. |
| CHP `sa.xml` | 200, 181 KB, 0.5 s | 17 dispatch centres, 138 logs, LACC has 32 | Parsed with ElementTree after checking the closing `</State>` tag. A copy cut at 131,072 bytes is rejected. Superseded on 2026-11-06, see the addendum. |
| LCS `lcsStatusD07.json` | 200, 13.2 MB, 4.0 s | 4,317 closures | 591 current at fetch time, 12 of them with both ends in the corridor box. |
| CMS `cmsStatusD07.json` | 200, 172 KB, 0.3 s | 145 signs | 23 in service in the box, 17 showing text (all "MOVE OVER OR / SLOW DOWN FOR / CRASH RESPONDERS"). |

ATMS colour codes, read from the data, not from a document (unverified against Caltrans): speed ranges in mph per code were 1: 2 to 17, 9: 21 to 34, 3: 37 to 50, 2: 50 to 70, and 0 with speed 0 for 1,813 rows (no data). Three rows carried colour 0 with a speed of 75 to 78 and filled volume and good-lane fields; they are kept as readings. A reading is valid when speed > 0 and the good-lanes percentage > 0. The brief named codes 13/1/3/2; no 13 was seen in this file.

The corridor has 387 stations in `detectors.gpkg`, 299 with a segment from step 2. At 05:34 UTC only 15 corridor stations had a valid speed, 8 of them with a segment: I-5 at Dorris and south of SR-2 (both directions) and US-101 at Hollywood, Edgeware and Argyle. No station on I-10 or I-405 inside the corridor reported. That is the state of the deprecated feed, not of the parser: 1,857 of 2,128 rows district-wide had 0% good lanes.

CHP LATLON "34036997:118283991" is lat 34.036997, lon -118.283991 (degrees times 1e6, the west sign dropped and restored). LogTime "Nov  5 2026  9:31PM" is Pacific. Closure records carry begin and end lon/lat as strings, start and end epochs, an indefinite flag, and the Caltrans codes 10-97 (closure in effect) and 10-98 (closure ended). "Current" here means start passed, end not passed or indefinite, and no 10-98; the 10-98 exclusion is one step beyond the brief and is labelled `ended` in the record.

## 2. The code

- `poller/traffic.py`: typed records `VdsReading`, `AtmsSnapshot`, `Incident`, `Closure`, `Sign`; pure parsers `parse_webupdate`, `parse_chp`, `parse_lcs`, `parse_cms` that raise `ValueError` on bad content; `CachedSource[T]` fetches a URL at most every `interval_s` seconds (60 s for ATMS, CHP and CMS, 300 s for LCS), with a User-Agent, keeps the last good parse, and counts errors with the last message. A failed fetch leaves the previous result in place.
- `poller/base.py`: `Detector` records from `detectors.gpkg` (station id, segment id or None, lon/lat converted from EPSG:32611, route, direction, postmile, name).
- `poller/__main__.py`: each 30 s cycle polls the four sources (they return cached results when not due). Valid readings on stations with a segment become `SegmentSpeed` rows with source "vds", weight 1, agency "caltrans", vehicle id = station id, and ts = the file's Created time, so an unchanged file adds no rows (the store's primary key makes the replay a no-op).
- `poller/store.py`: `segment_speeds` groups by segment and source.
- `poller/snapshot.py`: `segments.geojson` now carries `source` (bus, vds or both), `speed_bus_kmh`, `n_bus`, `speed_vds_kmh`, `n_vds`, and `speed_kmh` is the detector speed where one exists (cars) else the bus speed. New `events.geojson` (incidents as points, closures as begin-to-end lines, signs as points, each with type, source, text, time) and `detectors.geojson` (every corridor station with its latest speed in km/h and mph, volume per 30 s, colour code and label, good-lane percentage, age, segment id).
- `status.json`: a `traffic_sources` block per source (fetches, errors, last error, age of the last good copy) and a `traffic` block with the ATMS counts and the "deprecated feed, still live" note.
- `prep/step4_compare.py`: the bus-versus-detector comparison, strict and relaxed.

Tests (`tests/test_traffic.py`, 6 new, 15 in all): the webupdate parser on a sample with VDS, CMS and Created rows (valid and no-data rows, the colour 0 with speed case, a copy without the Created line rejected); the CHP parser on quoted values, the LATLON encoding, dispatch filtering, and two truncated copies rejected; the closure filter on current, future, past, indefinite and ended-early records; the CMS line join; detector readings to segment rows only for stations with a segment.

```
.venv/bin/pyright poller/ tests/ prep/   -> 0 errors
.venv/bin/python -m pytest -q tests      -> 15 passed in 0.44s
```

## 3. The live run

```
.venv/bin/python -m poller run --minutes 30 --interval 30
started 05:39:00 UTC (21:39 Pacific), 61 cycles, ended 06:52:43 UTC
```

The run took 74 minutes for 61 cycles. Cycles 1 to 49 were 30.0 s apart; six gaps of up to 1,098 s followed from 06:03 UTC. `pmset -g log` shows the Mac on battery entering "Maintenance Sleep" repeatedly that evening, so the gaps are the machine sleeping, not the loop. The poller recovers on wake (the Metro WebSocket reconnects, the cached sources refetch). Long unattended runs need `caffeinate -i` in front of the command or the app's keep-awake. Unverified: that the gaps map one to one onto sleep intervals.

Sources over the run: ATMS 32 fetches, CHP 32, CMS 32, LCS 9, zero errors on all four, last good copy 30 s old at the end. The ATMS file had 2,128 stations and 276 valid readings at the last cycle; 15 corridor stations with a speed, 8 of them on a segment, so 8 rows per ATMS update: 231 detector rows on 8 segments over 32 distinct Created times. Detector speeds ran 55 to 113 km/h, mean 98 (free flow, colour 2, late evening). In `detectors.geojson`, 387 stations: 367 colour 0, 18 colour 2, 1 colour 9, 1 colour 3.

Events in the corridor at the last cycle: 2 CHP incidents (both "MZP-Assist CT with Maintenance", one at 15th St logged 05:54 UTC, one at 777 W Washington Blvd logged 22:38 UTC the day before with a Caltrans detail working until 07:00), 19 current closures (SR-1 lane closures at Marina del Rey, full overnight closures of US-101 between Alvarado and 1st Street for paving and bridge work, I-405 at Jefferson and at Getty Center Dr, SR-2 westbound at Bundy, I-110 at MLK), 8 of them with the 10-97 "in effect" code, and 23 in-service signs, 17 showing the crash-responder message. `events.geojson` held 44 features.

Buses: 748 Metro, 93 Big Blue Bus, 26 Culver CityBus and 20 LADOT vehicles in the corridor; 1,719 kept pairs, 8,528 bus rows, 137 of them on motorway segments (82 segments, median 88.7 km/h). `segments.geojson` at the end held 29 features: 21 bus, 8 vds, none with both.

## 4. Bus versus detector

```
.venv/bin/python prep/step4_compare.py --since 1793943510
strict  (same segment, same 10-minute window): n=0
relaxed (bus on motorway segments within 300 m of a reporting station, same window): n=2, both -44.2 km/h
  station 717472 US-101 S Hollywood: bus mean 59.7, detector mean 103.9
```

Fewer than 30 pairs, by a wide margin. Two causes, both outside the code: only eight corridor stations report, all on I-5 and US-101 north and east of downtown and none on I-10 or I-405 where the Westside express buses run; and at 21:40 to 22:50 on a Thursday the express services are finished. The two pairs that exist show a bus 44 km/h slower than the detector on a free-flowing US-101, in the expected direction. The comparison has to be rerun in a weekday peak, when step 3's run saw 1,166 bus rows on motorways; the script is ready and the detector rows will have accumulated in the history by then. Unverified: whether the two speed series move together.

## 5. Verified, not verified, inherited

Verified: all four sources parsed live with zero errors over 74 minutes, each at its own interval; the truncation guard on CHP; detector rows carry the file's Created time and an unchanged file adds no rows (231 rows for 32 distinct Created times, 8 stations each, over 32 fetches); the snapshots exist and load as GeoJSON; 15 tests; pyright clean.

Not verified: the colour code meanings beyond what the data shows; the "in effect" and "ended" codes against Caltrans's definitions; whether a stale CHP file (one was seen in the research) would be served again; the bus-versus-detector relation (two pairs); the cause of the cycle gaps beyond the pmset log.

Also fixed on the way: Python 3.13 skips `.pth` files whose name starts with an underscore, which is how hatchling names its editable hook, so `import poller` failed outside `python -m`. The build backend is now `uv_build`, which writes `la_live.pth`; `prep/step3_report.py` and `prep/step4_compare.py` run from the repository root again.

Inherited: `speed_kmh` on a segment is the detector speed where one exists, the bus speed otherwise, with both kept side by side; the viewer should colour by `source`. The deprecated ATMS feed covers almost nothing of the Westside freeways; the cameras (step 7) are the only other freeway source. The 13 MB LCS file every 5 minutes is the heaviest fetch (4 s).

## Addendum 2026-11-06: the CHP file is cut by its server in busy hours

At 10:50 on a weekday the poller reported three CHP fetches, three errors, `sa.xml without closing </State> (truncated)`. Checked by hand: HEAD reports a Content-Length of 226,368 then 228,982 bytes with `Accept-Ranges: bytes`; every GET returns exactly 151,552 bytes (plain, with the F5 session cookie, gzip-encoded, HTTP/1.0); a Range request for bytes 150000 to 999999 returns 206 with 1,552 bytes, and any range starting past 151,552 returns 416. The cut is on the server side, not in transit, so no fetch strategy recovers the rest. The night-time file (181 KB at step 4) fitted under the cut, which is why 74 minutes then passed clean.

Inside the cut file the Los Angeles dispatch block was whole (bytes 78,639 to 133,970 of 151,552, with its closing tag; the cut fell in the Orange County block that follows). `parse_chp` now parses the dispatch block on its own: the whole block when its closing tag is present, otherwise every `<Log>` element complete before the cut; it raises only when the block is absent. Live: 35 LACC logs from the cut file through `CachedSource.poll`, zero errors. Tests: a cut after the block loses nothing, a cut inside the block keeps the logs before it, a cut before the block is rejected (17 tests, pyright clean). What is still lost: nothing for LACC today; if the LA block itself ever runs past the cut, logs after the cut are dropped silently and the incident count will dip without an error in `status.json`.
