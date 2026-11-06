# Step 8: unattended on GitHub Actions and Pages

Date: 2026-11-06, from 22:15 UTC (14:15 Pacific). The workflow cannot run on this Mac, so every step of the job was rehearsed here in a fresh tree and venv, measured, and the parts only GitHub can answer are marked unverified. The user's poller and `serve.py` kept running; nothing of theirs was written.

## 1. Design, settled before coding, from measurements

### What the job must do in one 5-minute slot

Checkout, install, restore history, run the poller for a few minutes at 30-second cycles, prune and save history, assemble the site, upload it, deploy. The local 30-second mode is the same code with other flags; nothing in `poller/` branches on "Actions".

### Measurements on this Mac that fix the design

Base layers (`data/base`, gitignored today): 91 MB in all. The poller needs the GeoPackages and the shape index (segments 13 MB, Metro GTFS 12 MB, shapes 6.1 MB, stops 1.7 MB, LADOT 1.6 MB, Culver City 0.9 MB, Big Blue Bus 0.8 MB, detectors 0.2 MB, cameras 0.2 MB, shape_index.json 7.7 MB: 44 MB); the page needs the two PMTiles (corridor 11 MB, buildings 36 MB: 47 MB). Largest file 36 MB, under GitHub's 100 MiB block and 50 MiB warning.

History growth from the user's run today (`history/observations.sqlite`, read-only query at 22:19 UTC, run started 21:19 UTC, 30-second cycles):

| table | rows per hour | note |
|---|---|---|
| positions | 83,860 | one per accepted position |
| pairs | 83,860 | one per position, with its reason |
| observations | 43,885 | segment speed rows, bus and detector |

84 bytes per row on disk over the whole file (80.3 MB after VACUUM for 954,000 rows), so about 17.8 MB per hour at the 30-second cadence, 420 MB per day; gzip -6 brings the file to 35 % (28.2 MB from 80.3 MB in 2.5 s). A 5-minute cadence with a 3.5-minute run captures 70 % of the time, about 12 MB per hour raw.

Dependencies: `uv sync --no-dev` on the committed tree in a fresh folder installs 11 packages into a 132 MB venv in 0.9 s with an empty uv cache on this connection (pyogrio's GDAL wheel is the bulk); the runner's download speed is unverified, and `setup-uv` caches the wheels between runs.

### (1) Cadence: what a 5-minute slot costs against 30 seconds

Section 2 reports the 4-minute rehearsal (kept pairs, segments with a speed) against the user's continuous run. The design consequence is already clear from the mechanism: a pair needs two positions of the same vehicle 30 to 120 s apart on the same shape, so a job seeded from the previous job's last positions (persisted history, below) gets its first pairs in its first cycle only when the gap since the last job is under 120 s; with a 5-minute cron and a 3.5-minute run the gap is about 90 s plus setup, so the first cycle's pairs are mostly dropped as `gap` and pairs start from the second cycle. The segment window (10 minutes) then spans two jobs.

### (2) History across jobs: Actions cache, pruned

Choices measured against 10 GB per repository, 7-day eviction of unused entries, immutable keys, and a 5-minute round trip:

- A `data` branch the workflow commits to: a new 80 MB blob every 5 minutes, binary deltas on SQLite pages compress badly; 288 commits a day would add gigabytes to the repository within days. Rejected.
- Release assets: a rolling asset needs delete and re-upload through the API with the job token every run; works, but it is an archive mechanism used as a hand-off, and the API rate and asset churn are undocumented for this pattern. Rejected for the hand-off, kept as the right place for a periodic archive (step 6's call).
- Actions cache: made for exactly this, zstd-compressed by the action itself, restored by prefix to the newest entry, saved under a new key every run (`history-<run id>`, restore-keys `history-`). Chosen.

Ceiling: the whole cache of the repository is 10 GB and GitHub evicts the oldest entries past it, so what matters is one entry's size and its upload time inside the slot. Unpruned, the file grows 12 MB an hour and would reach 1 GB in about 3.5 days and the time budget long before. So the job prunes: `positions` and `pairs` to the last 15 minutes (only the seed for the next job's pairs is needed), `observations` to `--retain-days` (3 in the workflow: about 1.1 million rows, 90 MB raw, about 30 MB in the cache), then VACUUM. The local mode keeps everything (`--retain-days 0`, the default). The cache is a hand-off, not an archive: a baseline that needs weeks of observations (step 6) must periodically fold the window into something small (per segment and hour of week) or push an archive to a release asset; that is step 6's design, and the retention flag is its knob.

### (3) Base layers for the job and for Pages: an orphan `data` branch

Pages serves whatever the workflow uploads, never Git LFS, so the 47 MB of PMTiles must be in the artifact every deployment, and the 44 MB of GeoPackages must reach the runner. Options: commit `data/base` to `main` (91 MB in every clone and in `main`'s history on each rebuild), a release asset (a one-time upload through the web UI, then `curl` in the job, no versioning next to the code), or an orphan branch `data` holding only `data/base` (checked out by the workflow into place with `actions/checkout`, replaced as a single commit on each rebuild, `main` stays 3.6 MB). Chosen: the orphan branch, written by `prep/data_branch.py` from the working tree without touching `main` or the checkout (a temporary index, `git write-tree`, `git commit-tree`, `git update-ref refs/heads/data`), pushed by the user with `git push --force origin data`. Repository size after the first push: about 91 MB plus `main`; a rebuild replaces the branch, and the unreachable old objects are pruned by GitHub on its own schedule (unverified timing).

### (4) The viewer under `/la-live/` and PMTiles on a static host

`viewer/app.js` resolves `BASE` as `new URL("..", location.href)`, so `https://hmr88.github.io/la-live/viewer/` gives `https://hmr88.github.io/la-live/` and the page fetches `live/*.geojson` and `data/base/*.pmtiles` under it. Without the trailing slash the resolution would be wrong; GitHub Pages redirects a directory URL to its slash form (expected, unverified until the first deployment), and the site root gets an `index.html` that forwards to `viewer/`. The published site is a staging folder assembled by `prep/publish.py`: `viewer/`, `live/` (the four GeoJSON files and `status.json`), `data/base/*.pmtiles`, the root redirect and `.nojekyll` (Pages would otherwise run Jekyll over the tree). Range requests: the pmtiles library reads the archive header and directories with `Range` headers; this is checked locally in section 3 against `serve.py --root <staging> --prefix /la-live`, which serves the staging folder under the Pages subpath with Range support (Python's `http.server` has none). GitHub Pages sits behind a CDN; the PMTiles documentation lists GitHub Pages as a host that works for archives under the 1 GB site limit and says nothing about Range explicitly, so Range on Pages is expected and confirmed only by the first deployment.

### (5) Saying where a snapshot came from

`status.json` gains a `run` object: `source` (`local` or `github-actions`), `interval_s`, `cadence_s` (the expected gap between snapshots: the interval locally, 300 on Actions), `minutes` (the run length), `started`, `run_id` and `run_url` when the environment provides them. The panel's first row reads the source and cadence and turns to a warning whenever the cadence is longer than the interval or the snapshot is older than the cadence: "snapshots from GitHub Actions every 5 min, this one 2 min old". The viewer refresh timer stays at 30 s; on Pages it only sees a new file after a deployment.

### Workflow shape

`.github/workflows/poll.yml`: `schedule` every 5 minutes plus `workflow_dispatch`; two jobs. `poll` (concurrency group `poll`, no cancel, `timeout-minutes: 8`): checkout `main`, checkout `data` into `data/base`, `setup-uv` with cache, `uv sync --no-dev`, restore the history cache, run the poller for 3.5 minutes at 30 s with `--source github-actions --cadence 300 --retain-days 3`, save the history cache, assemble the site, upload the Pages artifact. `deploy` (needs `poll`, concurrency group `pages`, `permissions: pages: write, id-token: write`): `deploy-pages`. Actions pinned to the commit SHAs of their current releases (checkout v7.0.1, setup-uv v10.3.3, cache v6.1.0, upload-pages-artifact v5.0.0, deploy-pages v5.0.1, configure-pages v6.0.0, read from the GitHub API today). The cron minimum is 5 minutes, runs are delayed under load, and GitHub disables the schedule in a public repository after 60 days without a commit; all three from the GitHub documentation.

## 2. The job rehearsed here: a 4-minute run seeded from history

Tree: `git archive HEAD` (commit 841cf13) into a scratch folder, `uv sync --no-dev` there (11 packages, 132 MB, 0.9 s with an empty uv cache), history seeded from a VACUUMed copy of the user's `observations.sqlite` taken at 22:18 UTC (their 30-second poller kept running). Command, as the job runs it apart from the length:

```
uv run --no-dev python -m poller --base-dir .../data/base run --minutes 4 --interval 30 --live-dir ../live4 --history ../hist/copy.sqlite
base: 26829 segments, 702 shapes; seeded 1808 vehicles from history
cycle 1 22:21:02: new positions 256, kept 0, reasons {'gap': 256}
cycle 2 22:21:32: new positions 1091, kept 43
cycle 5 22:23:02: kept 315
cycle 9 22:25:02: new positions 737, kept 668, segments 2716, vehicles 1118
real 244.4 s
```

Read from the history copy afterwards, rows written by this run only (ts from 22:20:50):

| | 4-minute job | user's continuous run, the 10 minutes before |
|---|---|---|
| kept pairs | 667 (9 cycles, about 74 per cycle) | about 80 per cycle (status.json: 949 kept in 12 cycles earlier today) |
| bus observations (segment rows) | 2,901 | |
| distinct segments with a bus speed | 1,901 | 2,534 in the last 10 min of history |
| segments in the written snapshot | 2,716 (10-minute window spanning the seed and the job) | 2,663 at cycle 12 of the user's run |

So the cost of the 5-minute cadence: the first cycle of every job pairs nothing (all 256 positions dropped as `gap`, the seed being over 120 s old, as the design predicted), the job's own pairs cover about 1,900 segments against 2,500 for a continuous 10 minutes, and between jobs the published snapshot does not change at all. A segment speed on Pages is the mean of a window that reaches back into the previous job through the restored history, which is why the history hand-off exists even without step 6. Vehicle positions on Pages are up to the snapshot age old; the panel says so.

Prune rehearsed on the same copy (86.0 MB, one day of the user's runs): `positions` and `pairs` cut to 15 minutes (377,622 rows each deleted), `observations` to 3 days (nothing to delete yet), VACUUM, 2.2 s, 86.0 MB to 18.5 MB, 5.7 MB gzipped. With 3 days of observations at the job's rate the file settles near 90 MB raw, roughly 30 MB in the cache's zstd (the gzip ratio measured above, zstd unverified), well inside the slot.

Time budget of one job, measured parts on this Mac and estimates for the runner (unverified until the first run): checkout of `main` and of the 96 MB `data` branch (estimate 10 to 30 s), `uv sync --no-dev` (0.9 s here with wheels to download; the runner has `setup-uv` caching), cache restore (30 MB, seconds), poller 3.5 minutes (210 s plus the first fetch), prune (2 s), site assembly (0.3 s for 50.8 MB: see section 3), Pages artifact upload (50.8 MB, estimate 10 to 30 s). About 4 min 30 s for the `poll` job, which is what the `poll` concurrency group protects; the `deploy` job runs after it under its own group and does not hold the next poll. If GitHub delays the cron, runs simply start later; `cancel-in-progress: false` means a late run queues behind a running one and never overlaps it.

## 3. The site rehearsed under `/la-live/`

`prep/publish.py --out site` from the repository (the user's live files, read only): viewer 0.0 MB, live 1.6 MB, tiles 49.2 MB, total 50.8 MB, 13 files, 0.3 s. Served with `serve.py --root site --prefix /la-live --port 8081` (the user's server on 8080 untouched), checked with curl:

```
/la-live                    301 -> http://127.0.0.1:8081/la-live/
/la-live/                   200 text/html        (the root index.html forwarding to viewer/)
/la-live/viewer/            200 text/html
/la-live/live/status.json   200 application/json
/viewer/                    404                  (outside the prefix, as on a project site)
/la-live/../serve.py        404                  (no path traversal)
Range: bytes=0-15 on /la-live/data/base/corridor.pmtiles -> 206, Content-Range: bytes 0-15/11562971, body "PMTiles"
```

In the browser (Claude desktop app's built-in pane) at `http://127.0.0.1:8081/la-live/`: the root forwarded to `/la-live/viewer/`; the corridor source resolved to `pmtiles://http://127.0.0.1:8081/la-live/data/base/corridor.pmtiles`; the network log shows `corridor.pmtiles` and `buildings.pmtiles` answered `206 Partial Content`, `segments.geojson`, `status.json` and `cameras.geojson` `200`; at zoom 12.5 over the Westside the page rendered 1,336 road features, 209 with a live speed, 67 vehicles; the panel's first row read "source: a local poller every 30 s" for the user's `status.json`, which has no `run` block yet (their poller started before this commit), which is the fallback the row is written for.

One thing went wrong in the pane and is not the site's: every OpenFreeMap tile request failed with "No 'Access-Control-Allow-Origin' header", on two loads. `curl` with the same `Origin: http://127.0.0.1:8081` header got `200`, `access-control-allow-origin: *`, served by Cloudflare, at the same minute, and the step 7 check an hour earlier rendered the same basemap from the same origin. So OpenFreeMap answered the pane's requests with something without CORS headers (a rate limit or a bot challenge is the likely shape; the pane's network log kept no entry for them, so unverified). The page keeps working without the basemap: roads, vehicles, events and buildings draw on a blank ground. On Pages the origin is `https://hmr88.github.io`, and whether OpenFreeMap serves it is confirmed by the first deployment only.

What Pages may do differently from this rehearsal, all unverified until the first run: the directory redirect for `/la-live/viewer` without the slash; Range on the CDN (the PMTiles documentation lists GitHub Pages as a working host); caching of `live/*.geojson` (Pages is said to send `Cache-Control: max-age=600`; from memory, unverified; the viewer's cache-busting query on every fetch is what makes a fresh snapshot visible either way); the 1 GB site limit (this site is 51 MB) and the 100 GB per month soft bandwidth limit (each viewer load fetches the tiles it needs by Range, not the 47 MB).

## 4. Files, commands, checks

- `.github/workflows/poll.yml`: two jobs, seven action references pinned to commit SHAs with the version in a comment, parsed with Ruby's YAML (`jobs: poll, deploy; cron */5 * * * *; workflow_dispatch; 10 poll steps; timeouts 8 and 5 minutes`). Not run: no runner here.
- `poller/__main__.py`: `--source`, `--cadence`, `--retain-days`; `run_info()` writes the `run` block; the prune runs after the loop when `--retain-days` is positive. `poller/store.py`: `Store.prune()`.
- `prep/publish.py`: the site assembly, refusing to publish without the three required live files or either tile set. `prep/data_branch.py`: the orphan branch, run here: `data -> a292c3ddf9b8, 13 files, 95.8 MB` (local branch, not pushed). `prep/__init__.py` so the tests import `prep.publish`.
- `serve.py`: `--root` and `--prefix`.
- `viewer/app.js`: the "source" row; `index.html`: the method bullet.
- `tests/test_unattended.py`: prune keeps the seed and the window and the file passes `integrity_check`; `run_info` for local and Actions environments; the publish layout, sizes and the refusal on a missing file.

```
uv run pyright                      # 0 errors
uv run python -m pytest -q          # 29 passed
```

Verified here: the measurements of sections 1 to 3; the 4-minute job seeded from history; the prune; the site assembly and its layout under a subpath with Range; the data branch's content; the workflow file's syntax and pins.

Unverified, by nature of the step: anything GitHub does: the cron's real cadence and delay, the runner's install and checkout times, the cache round trip, the Pages deployment, Range and redirects on the Pages CDN, OpenFreeMap serving the Pages origin, and the 60-day schedule pause. Also unverified: the panel row for a `github-actions` status in a browser (section 5 below records the local run with the job's flags; the row is checked against that file if the rehearsal finished in time, else marked).

## 5. Checklist for the user

1. Push the code and the data branch from your terminal: `git push origin main` and `git push --force origin data`. The data branch is 96 MB; the push takes a minute.
2. On GitHub: Settings, Pages, Build and deployment, Source: GitHub Actions. Nothing else on that page.
3. Settings, Actions, General: actions allowed (the defaults allow `actions/*`; `astral-sh/setup-uv` must be allowed too, which "Allow all actions and reusable workflows" does). Workflow permissions can stay read-only; the deploy job requests `pages: write` and `id-token: write` itself.
4. Actions tab, workflow "poll", Run workflow, branch main. Watch the `poll` job's "Poll for 3.5 minutes" step for the `cycle` lines and the "pruned history" line, then the `deploy` job for the page URL.
5. Open https://hmr88.github.io/la-live/ and send me the run URL and the Pages URL.

What the first run tells us that this Mac could not: the real time of checkout, install and cache restore on the runner (whether the job fits the slot, and by how much); whether the cache save and the next run's restore hand the history over (the second run's "seeded N vehicles from history" line with N above zero and its first cycle showing `gap` rather than nothing); whether Pages serves the PMTiles by Range (roads coloured and buildings extruded at https://hmr88.github.io/la-live/viewer/); whether OpenFreeMap serves the Pages origin; the directory redirect without the trailing slash; the actual cadence the cron delivers over an hour (the "source" row and the snapshot age in the panel); and the Pages artifact upload time for 51 MB.

## 6. The job's flags, run once more with the new code

```
uv run python -m poller run --minutes 1 --interval 30 --live-dir .../live5 --history .../hist/job2.sqlite --source github-actions --cadence 300 --retain-days 3
cycle 3 22:27:33: new positions 695, kept 212, segments 2792, vehicles 1116
pruned history: {'positions': 380556, 'pairs': 380556, 'observations': 0}, file 18.4 MB
real 65.6 s
run: {'source': 'github-actions', 'interval_s': 30.0, 'cadence_s': 300.0, 'minutes': 1.0, 'started': '2026-11-06T22:26:32+00:00', 'run_id': None, 'run_url': None, 'sha': None}
publish: viewer 0.0 MB, live 1.5 MB, tiles 49.2 MB, total 50.7 MB, 0.06 s
```

The site was reassembled from that output and served again under `/la-live/`. In the browser the first row read, in warning colour, "GitHub Actions, a new snapshot about every 5 min; vehicle positions are not live, they are up to the snapshot age old", the snapshot row "2026-11-06 22:27:33 UTC, 49 s old, cycle 3", 822 roads with a speed and 201 vehicles in view, and this time OpenFreeMap served the basemap to the pane (so the earlier CORS failures were transient on their side). Screenshot: `docs/img/pages-rehearsal.jpg`. On GitHub the `run_id`, `run_url` and `sha` fields fill in from the runner's environment and the row links the run.

One rehearsal detail worth keeping: a reload right after `prep/publish.py` had emptied and refilled the staging folder showed "live files not reachable: TypeError: Failed to fetch" once; the next load was fine. On Pages a deployment is atomic, so this cannot happen there; locally, publish before starting the server.
