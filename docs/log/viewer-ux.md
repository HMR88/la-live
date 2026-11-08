# Viewer UX pass

Date: 2026-11-08, 05:30 to 06:30 UTC (2026-11-07, 21:30 to 22:30 Pacific). Scope: `viewer/` only (`index.html`, `style.css`, `app.js`, new `format.js`), nothing in the poller, cameras or workflow. The user's notes: the road popup was not intuitive (segment id, length, OSM id, an unexplained "limit 40 mph") and the feeds panel was too much text with no indicators.

## 1. Decisions

- **mph first, km/h after it in smaller type**, in popups and the legend. The data stays in km/h everywhere (`live/*.geojson`, feature state, the colour ramp); `format.js` converts at display time only. The scale control is imperial.
- **Pacific time**, relative where it reads better ("3 min ago", "seen 32 s ago") with the absolute time ("Nov 7, 9:31 PM PST") in the Details view and on hover of the status pill's age. `Intl.DateTimeFormat` with `America/Los_Angeles` names the zone (PST or PDT), so a reader elsewhere is not misled.
- **Road popup**: street name as the title, the road class in plain words (Freeway, Freeway ramp, Highway, Primary road, Secondary road, Tertiary road, Residential street, Busway, and a tag-to-words fallback), then one large line: the speed with a colour chip from the same ramp as the road on the map, or "No reading in the last 10 minutes" in grey. Then "from 2 buses, newest 2 min ago", then "Posted limit 35 mph (OpenStreetMap), traffic at about half the posted limit" when both exist, and a one-line hint that bus speeds between stops run below car speeds when buses are the source. Segment id, length and OSM id are gone. The comparison wording comes from the ratio of measured mph to the limit: at or above 0.9 "at or above the posted limit", 0.7 to 0.9 "a little under", 0.4 to 0.7 "about half", below 0.4 "well under". A bare OSM `maxspeed` number is km/h by OSM convention and is converted; in this corridor every value carries "mph" (10,262 at 35 mph, 3,315 at 30, 1,228 at 65, 1,032 at 55, 700 at 25, 533 at 40, 321 at 45; 9,364 segments have none).
- **Bus popup**: "Route 17" large, the agency name in its colour, speed in mph with km/h and "heading SE" from the bearing, "seen 32 s ago", then the bus number and trip in small type with the sentence that the speed is the last pair of positions on the route, not a speedometer.
- **Camera, detector, incident, closure, sign, stop, route popups** follow the same shape: title, kicker, one key line, two or three facts. The camera title is the Caltrans name with the "I-10 : (105)" prefix stripped, the detector shows its station name and route, never the VDS number; incident dispatch notes sit behind a disclosure.
- **Panel**: title and one-line subtitle, a status pill ("Live, refreshes every 30 s" or "Scheduled, new snapshot about every 5 min") with a dot coloured by snapshot age (green under 2 min, amber under 10, red beyond) and the age itself; feeds grouped under Transit, Traffic and Cameras as rows of dot, name and one figure ("504 buses in corridor", "14 stations reporting", "4 incidents", "7 lane closures", "23 signs in service", "11 of 11, newest 1 d ago"); a Details disclosure, closed by default, with the snapshot time, mode and run link, roads with a speed, bus pairs kept, per-feed vehicle counts and errors, per-source copy age and the deprecated-feed warning. Row dots: green when a feed has no errors, amber with errors but data, red with errors and nothing in the corridor.
- **Layer toggles as chips** with `aria-pressed`, the 3D button among them. **Legend** as a gradient bar labelled 0 to 60 mph, built from the same km/h stops as the map.
- **Typography and layout**: system font stack at 13.5 px, a card with 14 px corners, a soft shadow and a slight blur, the body scrolling on its own under a fixed header and pill, a collapse button; under 480 px the card spans the width and starts collapsed to the header and pill, and the map controls move below the header. Attribution unchanged in substance, including the LADOT notice in bold.
- One click dispatcher instead of a handler per layer: the topmost layer under the pointer answers in a fixed order (buses, incidents, closures, signs, detectors, camera rings, cameras, stops, roads, routes), so a road and the route shape beneath it no longer open two popups at once. Roads still answer on hover.
- The camera row's age is computed from each sample's `sampled_at`, not from the `age_s` frozen in the file at write time; a day-old file now reads "1 d ago" with a red dot instead of "just now".

## 2. Logic moved into testable functions

`viewer/format.js` (ES module, no DOM): `speedParts`, `durationText`, `relativeAge`, `pacificTime`, `pacificDateTime`, `roadClassName`, `parseLimitMph`, `limitComparison`, `sourceText`, `plural`, `headingText`, `freshness`, `modeLabel`, `rampColour`. Tests in `tests/js/format.test.mjs` with Node's built-in runner, 9 tests:

```
node --test tests/js/format.test.mjs      # 9 passed (node v24.14.1)
```

Pinned values include 40 km/h as 25 mph, "2026-11-06T23:19:31+00:00" as 3:19 PM PST, a July date as PDT, 25 mph on a 40 limit, and the ramp colour at the midpoint of two stops.

## 3. Browser check

Own server and data, the user's untouched: `data/base` is absent from the working tree today, so the base layers were extracted from the local `data` branch into a scratch folder, a 25-minute poller run wrote its snapshots there (21:31 Pacific, a quiet hour: 79 vehicles in the corridor at cycle 1, about 600 later), and `serve.py --root <scratch> --screenshots` served it on port 8081. Built-in browser pane of the Claude desktop app, viewport emulated at 1280 by 820 and at 390 by 844.

Verified (values read from the page by script):

- Panel: pill "Live, refreshes every 30 s · 24 s ago" with a green dot; Transit rows Big Blue Bus 58, Culver CityBus 16, LADOT 12, LA Metro 504 buses in corridor; Traffic rows 13 to 14 stations reporting, 4 incidents, 7 lane closures, 23 signs in service; Cameras "11 of 11, newest 1 d ago" with a red dot (the file is from 2026-11-06); 12 chips; legend labels 0, 15, 30, 45, 60 mph. `docs/img/ux-panel.jpg`.
- Road popup with a reading: "North Main Street / Secondary road / 17 mph 28 km/h / from 1 bus, newest 5 min ago / Posted limit 35 mph OpenStreetMap, traffic at about half the posted limit / Bus speeds between stops run below car speeds." One popup, no route shape popup under it. `docs/img/ux-road-reading.jpg`.
- Road popup without: "Pico Boulevard / Secondary road / No reading in the last 10 minutes / Posted limit 30 mph OpenStreetMap". `docs/img/ux-road-none.jpg`.
- Bus popup: "Route 17 / Big Blue Bus / 32 mph 52 km/h, heading SE / seen 32 s ago / Bus 1812, trip 1847070. Speed is the last pair of positions on the route, not a live speedometer." `docs/img/ux-bus.jpg`.
- Phone width 390: the panel spans the width and starts collapsed (header and pill, 124 px tall); the collapse button expands it to a scrolling body (828 px card, 789 px of body); the map controls sit below the header. `docs/img/ux-phone.jpg`.
- Console: only the three warnings from the OpenFreeMap Positron style's own shield filters; nothing from this page. The errors logged during the check came from my own probing scripts querying a layer before the style had loaded.

Before and after: the old panel (`docs/img/overview.jpg`, `docs/img/cameras-popup.jpg` from steps 5 and 7) listed every feed as a sentence with UTC times and showed "limit 40 mph" with no explanation; the new one is in the screenshots above.

Unverified: the "Scheduled" pill on a real GitHub Actions status (the wording comes from `modeLabel`, tested; it was seen on the step 8 rehearsal file before this redesign, not after); the camera, detector, incident, closure and sign popups in the browser after the redesign (their HTML builders share the same `pop()` shell as the road and bus popups, and the camera row was checked; no camera popup click was made tonight); hover popups on touch devices; dark mode (none).

## 4. Checks

```
node --test tests/js/format.test.mjs   # 9 passed
uv run pyright                         # 0 errors (Python unchanged)
uv run python -m pytest -q             # 29 passed
```
