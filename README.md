# la-live

Live transit and traffic for Los Angeles, built from open data and open-source software. Vehicles from every agency that publishes GTFS-Realtime, freeway speeds from Caltrans detectors, incidents and closures from CHP and Caltrans, vehicle counts from Caltrans traffic cameras, and surface-street traffic inferred from the buses themselves, drawn on the OpenStreetMap road network in a browser with terrain and buildings.

Status: steps 1 to 5, 7 and 8 of the plan done on 2026-11-05 and 06 (step 8's first run on GitHub is the user's to start). Valhalla serves map-matching tiles for the corridor (`docker compose up -d`), the base layers come from a qgis-mcp run (`prep/copy_base.py`), `python -m poller run` turns the live bus feeds of LA Metro, Big Blue Bus, Culver CityBus and LADOT into segment speeds, adds Caltrans detector speeds (a deprecated but live feed), CHP incidents, lane closures and message signs, and writes snapshots under `live/`; `python -m cameras run` samples 15 s of video from a configured subset of Caltrans cameras, one stream at a time, and counts vehicles per direction with YOLO11 and ByteTrack; `serve.py` serves the `viewer/` page, a MapLibre map with terrain, extruded buildings, roads coloured by speed, vehicles, events, detectors, cameras and camera counts. Run logs with every check: [docs/log](docs/log). The plan with every verified data source is [docs/plan.md](docs/plan.md); the source inventories are under [docs/research](docs/research). First area: the Westside to downtown corridor.

## How it fits together

| Part | What it does | Where |
|---|---|---|
| Preparation | Roads, transit shapes, detectors and cameras for the area, matched to road segments and written as vector tiles. Runs through [qgis-mcp](https://github.com/HMR88/QGIS) and Valhalla. | `prep/` |
| Poller | A service that reads the live feeds every 30 to 60 seconds, turns bus movements and detector readings into segment speeds, keeps a history and writes snapshots. | `poller/` |
| Viewer | A static MapLibre GL JS page that draws the snapshots: roads coloured by speed (absolute now, ratio to baseline after step 6), moving vehicles, incidents, closures, signs, detectors, cameras, terrain and extruded buildings. Speeds in mph first, times Pacific, a status pill for mode and freshness ([docs/log/viewer-ux.md](docs/log/viewer-ux.md)). Served by `serve.py`. | `viewer/` |
| Cameras | A separate process: 15 s of HLS video per camera from `cameras/subset.json`, decoded with PyAV, tracked with Ultralytics YOLO11 small and ByteTrack on Apple's mps, counted across a line per direction. Writes `live/cameras.geojson` and `history/cameras.sqlite`. Speed needs a per-camera calibration file; none exists yet. Installed with `uv sync --extra cameras`. | `cameras/` |

## Requirements

- macOS on Apple Silicon (built on an M3 Pro, 18 GB), Docker through OrbStack or Docker Desktop.
- QGIS 3.44 with qgis-mcp for the preparation step.
- No API keys for the first version. LA Metro, Big Blue Bus, Culver CityBus and LADOT publish their feeds openly; Caltrans and CHP feeds are open.

## Running it

```
docker compose up -d                                   # Valhalla on :8002, tiles from data/valhalla/custom_files
.venv/bin/python prep/copy_base.py <qgis-mcp run dir>  # base layers into data/base/ with SOURCE.json
.venv/bin/python -m poller build-index                 # shapes onto segments, data/base/shape_index.json
.venv/bin/python -m poller run --minutes 20            # live/vehicles.geojson, live/segments.geojson, live/status.json, history/observations.sqlite
.venv/bin/python prep/step3_report.py                  # summary of a run
.venv/bin/python prep/step4_compare.py                 # bus speeds against Caltrans detector speeds
uv run python serve.py                                 # http://127.0.0.1:8080/viewer/ with HTTP Range for the PMTiles
.venv/bin/python -m poller run --minutes 340 --source github-actions --cadence 300 --retain-days 3   # what the Actions job runs, with poller.publish_live beside it
uv sync --extra cameras                                # PyAV, Ultralytics, torch (about 1 GB), only for the camera sampler
.venv/bin/python -m cameras preview --out /tmp/cams    # one frame per camera with its counting line, to place lines in cameras/subset.json
.venv/bin/python -m cameras run --sheets 101 1196      # live/cameras.geojson, history/cameras.sqlite, a round every 5 min, Ctrl-C to stop
.venv/bin/python prep/step7_compare.py                 # camera counts against detector volumes
```

## Running unattended

Two workflows. `.github/workflows/poll.yml` runs the poller around the clock on GitHub Actions: one job polls for 340 minutes at 30-second cycles while a publisher force-pushes the snapshot files every 5 minutes to the orphan branch `live`, then the job starts its successor itself; a 30-minute cron restarts it after a failure. `.github/workflows/pages.yml` deploys `viewer/`, the base tiles and a copy of the snapshots to GitHub Pages at `https://hmr88.github.io/la-live/` on every push to `main` that touches the viewer, daily, and by hand. The page reads the `live` branch first (`raw.githubusercontent.com`, CORS open, a fresh query string misses its 5-minute cache) and falls back to the site's copy; `?channel=<url>` points a fork at its own branch and `?channel=` disables it. History passes between runs through the Actions cache (3 days of observations, 15 minutes of positions). The base layers live on the orphan branch `data`, written by `prep/data_branch.py`. Design, measurements and limits: [docs/log/step8.md](docs/log/step8.md) and [docs/log/step9.md](docs/log/step9.md). Cameras stay a local job. Big Blue Bus does not answer GitHub's runners, so its buses appear on local runs only. Once, by hand:

1. Push `main` and the data branch: `git push origin main` and `git push --force origin data` (the branch is one commit; `git log -- data` is the folder, `git log refs/heads/data` the branch).
2. Settings, Pages, "Build and deployment", Source: **GitHub Actions**.
3. Settings, Actions, General: actions allowed (the defaults allow `actions/*`; `astral-sh/setup-uv` must be allowed too, which "Allow all actions and reusable workflows" does). Workflow permissions can stay read-only: each workflow asks for what it needs (`contents: write` and `actions: write` for poll, `pages: write` and `id-token: write` for pages).
4. Actions tab, workflow "poll", "Run workflow" once; it then keeps itself running. The "pages" workflow runs on the push itself.
5. The public repository must see a commit at least every 60 days or GitHub pauses the schedules.

To rehearse the job here: `uv run python prep/publish.py --out /tmp/site` then `uv run python serve.py --root /tmp/site --prefix /la-live --port 8081` and open `http://127.0.0.1:8081/la-live/`; the publisher runs against a local bare repository with `uv run python -m poller.publish_live --remote /tmp/live.git --once`.

The venv is managed by uv (`uv sync`). For an unattended run on a laptop put `caffeinate -i` in front of the command, or the Mac sleeps and the cycles stall. Checks: `.venv/bin/pyright`, `.venv/bin/python -m pytest tests` (the camera tests need the `cameras` extra) and `node --test tests/js/format.test.mjs` for the viewer's formatting rules.

## Licence

AGPL-3.0, see [LICENSE](LICENSE). The choice is deliberate: it allows the Ultralytics detector for the camera counts, and it keeps every hosted copy of this service open.

## Data attribution

OpenStreetMap contributors (ODbL). LA Metro, data reproduced unmodified and not used in advertising. City of Santa Monica Big Blue Bus (ODC-By). LADOT Transit: LADOT Transit information provided on this site is subject to change without notice. For the most current information, go to ladottransit.com. Culver CityBus. Caltrans (public domain unless indicated; camera images may carry additional rights; no warranty of accuracy). California Highway Patrol. Geofabrik for the OpenStreetMap extract.
