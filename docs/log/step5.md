# Step 5: the viewer

Date: 2026-11-06, 07:00 to 07:40 UTC (2026-11-05, 23:00 to 23:40 Pacific). Everything below was run; the browser checks were made in the Claude desktop app's built-in browser pane against `serve.py`. "Unverified" marks what was not checked.

## 1. Inputs built for the page

`data/base/buildings.pmtiles` (37.6 MB, gitignored) from the step 2 OSM GeoPackage's `buildings` layer (530,826 footprints, 518,026 with a `height`, 4,346 with `levels`), written by the GDAL 3.13.3 bundled in QGIS.app through `prep/copy_base.py` (now also `--only-buildings`):

```
ogr2ogr -f PMTiles data/base/buildings.pmtiles corridor_osm__osm.gpkg -dialect SQLite -sql "SELECT geom, height, levels, ROUND(CASE ... END, 1) AS height_m FROM buildings" -nln buildings -dsco MINZOOM=13 -dsco MAXZOOM=15
37.8 s; zoom 14 sample: 65,990 features, height_m 1 to 335.3, mean 5.9
```

`height_m` is the OSM height in metres (values like "4.4"; "18 m"; "24.46'" and "241 ft" converted from feet; "74.3;241 ft" takes the first number), else levels times 3.5, else 8. The first attempt failed because the SQLite `-sql` needs the geometry column named in the SELECT. The SQL is recorded in `data/base/SOURCE.json` with the file's sha256.

`serve.py`: stdlib `ThreadingHTTPServer` on 127.0.0.1:8080 serving the repository with `Range` support (206 with `Content-Range`, checked with curl on the PMTiles header: bytes 0-15 of 11,562,971, body starts `PMTiles`), `Cache-Control: no-store`, path traversal refused (404 for `/../etc/passwd`). With `--screenshots` it accepts `POST /__screenshot?name=x.png` with a PNG data URL and writes `docs/img/x.png`; nothing else is writable.

## 2. Libraries and terms

- MapLibre GL JS 6.16.0 from unpkg. Finding: MapLibre 6 ships only an ES module (`dist/maplibre-gl.mjs`, named exports, no default); the classic `dist/maplibre-gl.js` of the tooling notes does not exist (404 on unpkg and jsdelivr). The page therefore loads `app.js` as `type="module"` with `import * as maplibregl`.
- pmtiles 4.5.0 from unpkg as its classic bundle `dist/pmtiles.js`. Its ES build imports a bare `fflate` specifier the browser cannot resolve.
- Basemap: OpenFreeMap Positron style, `https://tiles.openfreemap.org/styles/positron` (HTTP 200, 55 layers, source `https://tiles.openfreemap.org/planet`). The site says the public instance is "completely free", no limits on map views or requests, no registration, no key, commercial use allowed, and that attribution is required and "If you are using MapLibre, they are automatically added". The required text, also in the panel: OpenFreeMap, © OpenMapTiles, Data from OpenStreetMap. Chosen over OSM raster tiles because the terms allow it and vector tiles fit the 3D view.
- Terrain: AWS Terrain Tiles, `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png` (a tile fetched with HTTP 200, image/png), encoding terrarium, exaggeration 1.3, hillshade from the same source. The joerd attribution page gives per-source credit lines, quoted in the panel and in the map's attribution control: 3DEP, GMTED2010 and SRTM data courtesy of the U.S. Geological Survey, ETOPO1 NOAA. The AWS registry page states no licence text of its own and links to that page; the URL pattern is not printed on the registry page (unverified beyond the tile answering).

## 3. The page

`viewer/index.html`, `style.css`, `app.js`; no build step.

- Roads: the `segments` layer of `corridor.pmtiles` with `promoteId: segment_id`; `live/segments.geojson` is fetched every 30 s and written into feature state (speed, source, bus and detector speeds, counts, age); ids that left the snapshot have their state removed. Colour is an absolute km/h ramp (0 dark red to 100 dark green), grey when no state; width by highway class and zoom. Hover shows name, class, segment id, `length_m`, OSM id, speed limit, the source (buses, detector, both) and both speeds with counts and age.
- Vehicles: circles by agency (Metro, Big Blue Bus, Culver CityBus, LADOT), fading with age, route label from zoom 13; popup with agency, route, vehicle, trip, last bus speed, bearing, age. Shapes as thin lines in the route colour, stops from zoom 14, both toggleable.
- Events: incidents (red with "!"), closures (orange dashed lines, points where begin equals end), signs (blue, pale when blank); popups with text, time, in-effect flag. Detectors coloured on the same ramp, small and grey without data; popup with speed, volume per 30 s, colour code and label, age. Cameras as dark circles; the popup loads the Caltrans still directly with a cache-busting query.
- Terrain on, hillshade, buildings extruded by `height_m` from zoom 13, pitch 55 by default, "Go flat" button.
- Panel: feed table from `status.json` (snapshot age, per feed vehicles in the corridor and errors, per traffic source age and errors, the ATMS deprecated note, pair counts), layer toggles, legend, "Assumptions and method", attribution with the LADOT notice in bold.

## 4. Browser check

`caffeinate -i python -m poller run --minutes 40` was running (started 07:16 UTC) and `python serve.py --screenshots`. Page `http://127.0.0.1:8080/viewer/`.

Verified, with the values read from the page by script:

- Style, PMTiles (206 responses), live GeoJSON and status loaded; terrain tiles and OpenFreeMap sprites and fonts fetched with 200; `map.loaded()` true, 8 sources, all own layers present, 10 toggles built.
- Roads colour: at zoom 10.6, 13,491 road features rendered, 537 with a speed in feature state; sample Wilshire Boulevard segment 26768 with bus 45.6 km/h, source bus, age 104 s. Panel: cycle 11, snapshot 9 s old, Big Blue Bus 66 of 68 in the corridor, Metro 543 of 1,085, ATMS 12 corridor stations with a speed and the deprecated note, 2 incidents, 27 closures, 23 signs, 1,161 segments with a speed.
- Vehicles move: positions of 607 vehicles recorded, after one 30 s refresh 91 of the 98 vehicles then in view had a different position.
- Popups: a Metro 233 vehicle ("vehicle 1575, trip 10233002162304-JUNE26, bearing 30°, position 42 s old"); a road hover ("Westwood Boulevard secondary, segment 21429, 37 m, limit 25 mph, 31.3 km/h from buses, bus 31.3 km/h (1 obs), detector – (0 obs), newest 4 min ago"); camera 369 I-405 North of Sunset with the Caltrans still loaded (320 by 260 pixels, a "temporarily unavailable" placeholder image at 23:21 Pacific, fetched from cwwp2.dot.ca.gov); sign I-405 South at Santa Monica Blvd, blank; detector 769373 US-101 South, no data, segment 21962.
- Terrain and buildings: at zoom 15.3 and pitch 62 over Westwood, 4,221 extruded buildings rendered with heights 12 to 17 m in the sample, terrain elevation 128 m at the centre. "Go flat" takes the pitch to 0 and the label to "Go 3D"; pressing again restores 55.
- Screenshots: `docs/img/overview.jpg` (corridor at zoom 10.4, pitch 50) and `docs/img/wilshire-3d.jpg` (Westwood and Wilshire at zoom 15.3, pitch 62), saved from the map canvas through `serve.py --screenshots`, then reduced to 1,400 px JPEG with `sips`. They show the map canvas only, not the panel.
- Console: no failed resource after the fixes below; warnings remain from the OpenFreeMap style itself (`highway-shield-*` filters with a null value), not from this page.

Fixed during the check: the CDN script paths (above); the camera layer first used an emoji, which asked the font server for a glyph range it lacks (404), replaced by a circle with a "C"; shapes with an empty `route_color` produced the colour "#", now grey.

The final reload with both fixes, in a fresh tab: `map.loaded()` true, zero resources with a status of 400 or more, an empty console, the camera layer of type circle, the shapes colour expression in place. (A first attempt to re-read it failed because the browser pane was hidden and a hidden page does not finish its first render.)

Not verified: the detector popup with a reading (the clicked station had no data; the template is the same); the page on a phone; behaviour when the poller is not running (the panel shows the last snapshot's age in red past 120 s, by code, not seen).

## 5. Inherited

- `uv run python serve.py` then `http://127.0.0.1:8080/viewer/`; the poller must be running for fresh data, otherwise the page shows the last snapshot with its age.
- The colour ramp is absolute speed; step 6 replaces it with the ratio to baseline and the legend text changes with it.
- `live/segments.geojson` grows with the number of segments with a reading (1.5 MB in the peak); feature state handles 1,400 segments without visible cost. If it grows past a few MB, write it as a compact id-to-speed JSON instead.
- Buildings tiles are 37.6 MB and rebuilt by `prep/copy_base.py --only-buildings`.
