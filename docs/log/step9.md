# Step 9: a cadence that holds, without Pages in the loop

Date: 2026-11-08, from 07:10 UTC (2026-11-07, 23:10 Pacific). The workflow cannot run here; every part was rehearsed locally against a scratch git remote and the parts only GitHub can answer are marked unverified. Nothing was pushed.

## 1. What the first 28 hours on GitHub showed

From the public Actions API for HMR88/la-live (`/actions/runs`, 07:12 UTC): seven runs, all green, each about 4 minutes. The six scheduled ones started at 03:17, 10:24, 16:33, 20:27, 23:48 and 02:45 UTC; the `*/5` cron fired once every 2.5 to 7 hours. The documentation says a schedule "can be delayed during periods of high loads"; for this repository the delay is the rule. Two more facts from the parent session: the viewer commit pushed on 2026-11-08 did not appear on Pages because the site is rebuilt only inside a scheduled run, and Big Blue Bus answered every one of 8 fetches from the runner with a connect timeout while it answers from this Mac.

## 2. Design, from measurements

### The job runs for hours and publishes every five minutes

One `poll` job runs the poller continuously for 340 minutes (the job limit is 6 hours; `timeout-minutes: 355`). A publisher process next to it pushes the snapshot files every 300 s to a channel the viewer reads directly, so a Pages deployment is no longer between the poller and the reader. At the end, the job saves history to the Actions cache and **starts the next run itself** with `gh workflow run` on its own token (`actions: write`): the docs name `workflow_dispatch` and `repository_dispatch` as the two GITHUB_TOKEN-triggered events that do create runs. The 30-minute cron stays as the restart after a failure or a cancelled run, delayed as it may be. Concurrency group `poll` with `cancel-in-progress: false`: at most one run is pending and a newer pending run replaces the older, so a cron fire during a long job simply waits and starts when the job ends. The chain step runs only when the job succeeded, so a cancelled run stays stopped and a failing run does not loop every few minutes; the cron picks it up later.

Gap between jobs: job end to the next job's first snapshot is checkout plus install plus cache restore plus the first cycle, about 1 to 2 minutes, once every 5 hours 40 minutes. Inside a job the cadence is the publish interval, 5 minutes, independent of the cron.

### The channel: an orphan branch `live`, read from raw.githubusercontent.com

Measured on 2026-11-08 07:15 UTC against this repository and a public release asset:

| | raw.githubusercontent.com (branch file) | release asset (`github.com/<repo>/releases/download/...`) |
|---|---|---|
| CORS | `access-control-allow-origin: *` on every response | the 302 redirects to `release-assets.githubusercontent.com`; that response carried **no** `access-control-allow-origin` header, so a browser fetch fails |
| caching | `cache-control: max-age=300`, Fastly `x-cache: MISS` then `HIT` on the same URL; a URL with a fresh query string was a `MISS` (`source-age: 0`), so a per-fetch `?t=` reaches the origin | the asset came back with `age: 3135`, `x-cache: HIT, HIT`: 52 minutes old from the CDN, no way to bust it from a browser |
| write path | `git push --force` of one commit with the checkout's own token (`contents: write`), no API calls | two REST calls per file per cycle (delete, upload) |

So (a) wins on both counts and (b) is rejected on CORS alone. The channel is a single-commit orphan branch `live` holding the snapshot files at its root, force-pushed every 5 minutes by `poller/publish_live.py`, which builds the commit with a temporary index (`git add` into `GIT_INDEX_FILE`, `write-tree`, `commit-tree`) and never touches the working tree or the current branch, the same way `prep/data_branch.py` does. The viewer reads `https://raw.githubusercontent.com/HMR88/la-live/live/<file>?t=<now>`. What raw's origin does right after a force-push (whether it serves the new commit at once) is unverified until the first run; the 5-minute CDN cache is bypassed by the query string as measured.

Growth from unreachable objects: one snapshot set is 1.23 MB raw and 142 KB gzipped as measured on tonight's files (segments.geojson 952 KB, of which the geometry is dead weight for the viewer, which joins by `segment_id` and never reads it). The poller therefore also writes `live/segments.json`, the properties only (517 KB, 43 KB gzipped for 2,572 rows), and the channel carries that instead of the GeoJSON: roughly 70 KB of new packed objects per push, 288 pushes a day, about 20 MB a day of unreachable objects on GitHub's side until its garbage collection runs. The repository is 63 MB today (the data branch). GitHub's gc schedule is not documented; if the size counter grows past a few hundred MB the fix is a one-time `git push --force` of a fresh orphan and a support request, or a lower publish rate; the local rehearsal in section 5 measures the per-push pack size on a bare remote.

### Pages: on push, plus a daily safety net

A second workflow `pages.yml` deploys the site on every push to `main` that touches `viewer/**`, `prep/publish.py` or itself, daily at 10:00 UTC, and by hand. It checks out `main`, the `data` branch into `data/base`, the `live` branch into `live/` when it exists (so the site's own copy of the snapshots is at most a day old), assembles with `prep/publish.py` and deploys. The `poll` workflow no longer deploys Pages at all.

### The viewer reads the channel first

On any host other than localhost, `app.js` fetches each live file from the channel with a cache-busting query and falls back to the site's own `live/` copy when the channel fails (network error or non-200). On localhost it reads the local files, which a local poller keeps fresher than any channel. The status pill says where the data came from ("via the live branch" or "from this site's copy") and how old the snapshot is; for a run published every 5 minutes the freshness dot turns amber only past cadence plus 90 s and red past three cadences, so a 4-minute-old snapshot from a 5-minute channel is green. `segments.json` replaces `segments.geojson` in the viewer and in the Pages site.

### Feeds: retry with backoff, and the Big Blue Bus note

`GtfsRtFeed.fetch` makes up to three attempts (waits of 1 s and 3 s) before an error counts against the cycle, and reports `retries` in `status.json`. Big Blue Bus: a probe step in the job (`curl --max-time 10` against the feed URL, printed in the job log) records whether the runner can reach it; the panel's Details view shows the feed's last error, and "How it works" says that Big Blue Bus is unreachable from GitHub's runners and appears only on local runs, from the 8 of 8 timeouts measured by the parent session on 2026-11-07.

### Once, by hand

Nothing new: push `main`, then run "poll" once from the Actions tab (or wait for the cron). The `pages` workflow runs on that same push. No tokens, no settings beyond those of step 8.

## 3. Growth of the live branch, measured

Five consecutive `publish_once` calls against an empty bare repository, with `status.json`, `segments.json` and `vehicles.geojson` changed between pushes (the other three files identical, so their blobs are reused):

```
push 1: remote objects  8 loose ( 60 KB)   one snapshot set, 322 KB raw
push 2: remote objects 13 loose ( 96 KB)
push 3: remote objects 18 loose (132 KB)
push 4: remote objects 23 loose (168 KB)
push 5: remote objects 28 loose (204 KB)
```

36 KB and 5 objects per push at tonight's quiet hour (370 segment rows); with a daytime `segments.json` of 2,500 rows (517 KB raw, 43 KB compressed) it is about 70 KB per push. At 288 pushes a day the unreachable objects grow by 10 to 20 MB a day on GitHub's side until its garbage collection runs, on a schedule GitHub does not publish. At that rate the repository's size counter would pass the 1 GB recommendation in 50 to 100 days without any collection; the first weeks on GitHub will show the real figure in the repository's size on the API (`size` was 63,398 KB on 2026-11-08). The remedy if it climbs: a one-time fresh orphan push and a support request for collection, or a longer publish interval.

## 4. The viewer, checked against a local channel

Own server (`serve.py --root <scratch> --screenshots`, port 8081) over a scratch root with the base layers from the `data` branch, the rehearsal poller's `live/` folder, and a `channel/` folder filled from the rehearsal's bare remote with `git archive live` (the same files raw.githubusercontent.com would serve). The viewer takes `?channel=<url>` so the local rehearsal can point it at that folder; the default channel on a public host is the repository's `live` branch, and localhost reads its own files.

- `?channel=http://127.0.0.1:8081/channel/`: the network log shows `status.json`, `segments.json`, `vehicles.geojson`, `events.geojson`, `detectors.geojson` and `cameras.geojson` fetched from `/channel/` with a fresh `?t=` each refresh; the pill read "Live via GitHub Actions, published every 1 min · 2 min ago · via the live branch" (the rehearsal poller ran with `--cadence 60`), and its tooltip "snapshot written Nov 7, 11:24 PM PST, read via the live branch".
- `?channel=http://127.0.0.1:9/nowhere/` (a port the browser refuses): every file fell back to the site's `live/` copy and the pill read "… 32 s ago · from this site's copy", with one console warning per outage rather than one per file.
- Without the parameter on localhost the pill shows no source suffix and reads the local files.

Unverified until the first run: raw.githubusercontent.com serving the newest commit right after a force-push (the CDN cache is bypassed by the query string; the origin's own behaviour is not documented), and the pill on the public site.

## 5. The job loop rehearsed locally

A bare repository stood in for GitHub; the poller ran against the base layers from the `data` branch with a copy of the history, and the publisher ran beside it exactly as the workflow step does, with a 60 s interval instead of 300 so six pushes fit in six minutes:

```
uv run python -m poller --base-dir <scratch>/data/base run --minutes 6 --interval 30 --live-dir <scratch>/live --history <scratch>/observations.sqlite --source github-actions --cadence 60 &
uv run python -m poller.publish_live --remote <scratch>/live-remote.git --branch live --every 60 --while-pid $!
publish failed (1): live files missing: ['segments.json']      <- the first attempt, 2 s after the poller started
07:25:03Z pushed f12eb0aaad8d (6 files, 248 KB)
07:26:03Z pushed 006b97685090 (6 files, 309 KB)
07:27:04Z pushed e12ce1ea6537 (6 files, 337 KB)
07:28:05Z pushed 2bb6f2786814 (6 files, 375 KB)
07:29:06Z pushed a01e1a3d87b5 (6 files, 397 KB)
07:30:04Z pushed 6b0228c57353 (6 files, 458 KB)            <- the final push after the poller exited
publisher exited 0
```

The remote afterwards: `rev-list --count live` = 1 (always one commit), the tree holds `cameras.geojson`, `detectors.geojson`, `events.geojson`, `segments.json` (213 KB), `status.json`, `vehicles.geojson`, author "la-live poll", 43 loose objects (352 KB) left behind by the seven pushes. The poller's last cycles: 13 cycles, 263 kept pairs, 1,060 segments with a speed at 23:30 Pacific on a Saturday night. The first publish attempt failed because the poller writes its first files 20 to 40 s after starting; the publisher now waits for the required files (up to 180 s, or until the poller exits) before its first push, so the job log carries no spurious failure. Each push is one `git push --force <commit>:refs/heads/live` with the checkout's credentials, no API call.

## 6. Files, checks, what the user does

- `.github/workflows/poll.yml`: one `poll` job, 355-minute timeout, `permissions: contents: write, actions: write`, cron `*/30`, dispatch; steps: checkout `main` and `data`, uv, cache restore, a feed reachability probe (four `curl --max-time 10` lines printed into the job log, Big Blue Bus among them), the poller for 340 minutes with the publisher beside it, cache save (`if: always()`), `gh workflow run poll.yml --ref main` (`if: success()`). Actions pinned as in step 8.
- `.github/workflows/pages.yml`: push to `main` on `viewer/**`, `prep/publish.py`, itself; cron `0 10 * * *`; dispatch; checks out `main` and `data`, pulls the `live` branch's files into `live/` when the branch exists, `python3 prep/publish.py --out site --optional-live`, upload, deploy; concurrency group `pages`.
- `poller/publish_live.py`: `build_commit`, `publish_once`, `wait_for_files`, a loop with `--every`, `--while-pid`, `--once`, `--dry-run`; failures are logged and the loop goes on. `poller/feeds.py`: `GtfsRtFeed.fetch` with two retries (1 s, 3 s) and a `retries` counter that `status.json` reports per feed. `poller/snapshot.py`: `segments.json` beside `segments.geojson`, `WINDOW_S` moved here. `prep/publish.py`: `segments.json` in the site, `--optional-live`.
- `viewer/app.js`: `fetchLive()` reads the channel first and the site's copy second, with one warning per outage; `?channel=`; `segments.json`; the pill carries the data source and uses cadence-aware freshness; Details shows retries and the Big Blue Bus note on an Actions status. `viewer/format.js`: `freshness(age, cadence)`, `modeLabel` with the channel wording, `sourceLabel`. `index.html`: the method bullet. `style.css`: panel figures wrap at 390 px (the nit from the UX round).
- Tests: `tests/test_step9.py` (feed retry then success, give up after the last attempt, publisher replaces one commit on a bare remote and leaves the checkout untouched, dry run, refusal without the required files, Pages assembly without live files, `segments.json` content, `wait_for_files`); `tests/test_unattended.py` updated for `segments.json`; `tests/js/format.test.mjs` for cadence-aware freshness and the pill words. `tests/__init__.py` so a test can import another's fixture.

```
uv run pyright                          # 0 errors
uv run python -m pytest -q              # 36 passed
node --test tests/js/format.test.mjs    # 10 passed
```

Verified here: the measurements of sections 1 to 3; the channel-first and fallback paths in the browser; the job loop against a bare remote with a 6-minute poller run and seven pushes; the suites.

Unverified, by nature of the step: the self-chaining dispatch on GitHub (documented behaviour, not run); whether a run of 340 minutes stays within the runner's limits and memory over hours (the longest local run was 61 cycles); raw.githubusercontent.com's origin behaviour right after a force-push; the growth counter on GitHub over weeks; the Pages deploy on push (the step 8 deploy job is unchanged in substance); Big Blue Bus from the runner (the probe step records it on every run from now on).

Once, by hand: push `main` (the `pages` workflow runs on that push), then Actions, "poll", "Run workflow". After the first job the chain keeps itself going; the `live` branch appears on the first publish, about a minute into the run, and the public page reads it from then on. No settings beyond step 8's, no tokens.

Principles that changed a decision: Prove it works (the release-asset channel died on a measured missing CORS header and a 52-minute `age`, not on an opinion); Attack the premise (the cron was never going to deliver a cadence, so the job chains itself and the cron becomes a restart); Laziness protocol (a force-pushed branch is one git command with the credentials already there, no API client); Subtract before you add (the viewer stopped loading 950 KB of geometry it never read, which also shrank every push); Encode lessons in structure (the first-attempt failure in the rehearsal became `wait_for_files`, not a note).
