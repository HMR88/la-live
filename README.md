# la-live

Live transit and traffic for Los Angeles, built from open data and open-source software. Vehicles from every agency that publishes GTFS-Realtime, freeway speeds from Caltrans detectors, incidents and closures from CHP and Caltrans, vehicle counts from Caltrans traffic cameras, and surface-street traffic inferred from the buses themselves, drawn on the OpenStreetMap road network in a browser with terrain and buildings.

Status: step 1 of the plan done on 2026-11-05. Valhalla 3.9.1 serves map-matching tiles for the corridor from a clipped Geofabrik extract (`docker compose up -d`), and `prep/trace_match.py` collects a Big Blue Bus trace and matches it; the run and its checks are in [docs/log/step1.md](docs/log/step1.md). The plan with every verified data source is [docs/plan.md](docs/plan.md); the source inventories are under [docs/research](docs/research). First area: the Westside to downtown corridor.

## How it fits together

| Part | What it does | Where |
|---|---|---|
| Preparation | Roads, transit shapes, detectors and cameras for the area, matched to road segments and written as vector tiles. Runs through [qgis-mcp](https://github.com/HMR88/QGIS) and Valhalla. | `prep/` |
| Poller | A service that reads the live feeds every 30 to 60 seconds, turns bus movements and detector readings into segment speeds, keeps a history and writes snapshots. | `poller/` |
| Viewer | A static MapLibre GL JS page that draws the snapshots: roads coloured by speed against their own baseline, moving vehicles, incidents, cameras, a time slider, terrain and buildings. | `viewer/` |
| Cameras | Short video samples from Caltrans cameras, counted and timed with an open detector. Phase 2. | `cameras/` |

## Requirements

- macOS on Apple Silicon (built on an M3 Pro, 18 GB), Docker through OrbStack or Docker Desktop.
- QGIS 3.44 with qgis-mcp for the preparation step.
- No API keys for the first version. LA Metro, Big Blue Bus, Culver CityBus and LADOT publish their feeds openly; Caltrans and CHP feeds are open.

## Licence

AGPL-3.0, see [LICENSE](LICENSE). The choice is deliberate: it allows the Ultralytics detector for the camera counts, and it keeps every hosted copy of this service open.

## Data attribution

OpenStreetMap contributors (ODbL). LA Metro, data reproduced unmodified and not used in advertising. City of Santa Monica Big Blue Bus (ODC-By). LADOT Transit: LADOT Transit information provided on this site is subject to change without notice. For the most current information, go to ladottransit.com. Culver CityBus. Caltrans (public domain unless indicated; camera images may carry additional rights; no warranty of accuracy). California Highway Patrol. Geofabrik for the OpenStreetMap extract.
