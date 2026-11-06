# Step 7: cameras for the corridor subset

Date: 2026-11-06, from 21:20 UTC (13:20 Pacific). Everything below was run on this Mac (Apple Silicon, no Homebrew, no system ffmpeg). "Unverified" marks what was not checked. The user's poller and `serve.py` were running in their own terminals throughout; the sampler never touches the files they write.

## 1. Design, settled before coding

### Data shape

Input, per camera, from `data/base/cameras.gpkg` (step 2, 154 Caltrans CCTV records inside the corridor, EPSG:32611 points with `lon`/`lat` in EPSG:4326): `cctv_id`, `name` (the Caltrans number in parentheses), `route`, `direction`, `postmile`, `stream_url` (null where Caltrans offers no stream), `image_url`, `image_minutes`, `seg_segment_id` (the nearest step 2 road segment).

Config, `cameras/subset.json`: the list of cameras the sampler visits, in order. Each entry: `cctv_id`, an optional counting `line` as two image points in fractions of width and height (`[[x0, y0], [x1, y1]]`, default a horizontal line across the frame at 55 % of the height, left to right), optional `a_label` and `b_label` for the two crossing directions, and an optional `calibration` file name. Fractions, not pixels, because the same camera serves 720p and 1080p variants.

Directions. The line is directed from its first point to its second. The side of a point is the sign of the 2D cross product of the line vector and the vector from the first point to the point, in image coordinates (y down). On the default line the positive side is the lower half of the frame. Direction A is a crossing from the negative side to the positive side (downwards on screen, towards the camera for a camera looking along the road); B is the opposite. The Caltrans record carries one `direction` string per camera and it is not stated whether that is the lens direction or the side of the freeway the mast stands on, so the metadata does not allow a safe mapping to route directions. The output therefore labels directions A and B, and `a_label`/`b_label` in the config let a person set "I-10 East" once they have looked at that camera's contact sheet. None are set in this step.

A sample is 15 seconds of video by presentation timestamps, decoded from the live HLS playlist. Tracks are the detector's tracker output: per track id, a list of (t, x, y) with (x, y) the bottom centre of the box, the point nearest the ground.

Counting rules (pure functions in `cameras/counting.py`):

- A crossing is two consecutive track points on opposite sides of the line whose joining segment intersects the line segment itself, not its extension.
- A track counts once, in the direction of its first crossing. Later crossings of the same track are ignored, so tracker jitter across the line (A then B then A) counts one vehicle, and a point exactly on the line (side zero) keeps the last side and never counts on its own.
- A track's direction label for density comes from the sign of its displacement along the line normal when that exceeds 10 px; shorter tracks are undirected.

Outputs per camera and direction: `count` (tracks crossing), `vehicles_per_min` (count scaled to 60 s), `flow_px_s` (mean over the counted tracks of path length in pixels divided by track duration, a relative flow index with no physical unit), `density` (mean per frame of boxes whose track carries that direction label; `density_all` counts every box). Speed in km/h only when a calibration file exists.

Calibration format, `cameras/calibration/<cctv_id>.json`: `{"crs": "EPSG:26911", "points": [{"px": [x, y], "ground": [E, N]}, ... four entries]}`. `cameras/speed.py` builds the homography image to ground with OpenCV, projects the bottom-centre track points to ground metres and takes, per direction, the median of the per-track speeds (ground path length over duration). The code path is tested on a synthetic calibration; no camera is calibrated in this step, so every output carries `speed_kmh: null` and `speed_status: "uncalibrated"`, never a guessed number.

Output `live/cameras.geojson`: a FeatureCollection of points in EPSG:4326, one per subset camera, properties `cctv_id`, `name`, `route`, `direction`, `segment_id`, `sampled_at` (UTC ISO), `duration_s`, `status` (ok, no_stream, decode_error), `resolution`, `fps`, `frames`, `a` and `b` objects (`label`, `count`, `vehicles_per_min`, `flow_px_s`, `density`), `density_all`, `tracks`, `speed_kmh`, `speed_status`, `model`, `model_version`, `device`, `decode_s`, `infer_s`, `error`. History: `history/cameras.sqlite`, table `samples` keyed by (cctv_id, sampled_at) with the same numbers flattened, plus `detector_readings` with the ATMS volume read from `live/detectors.geojson` at sample time for the comparison. The sampler reads the poller's files and writes none of them.

### Stream etiquette

One stream open at a time, cameras in config order, 15 s each, a round no more often than every 300 s (`--every`), `Ctrl-C` closes the open container, writes the snapshot and exits 0. The playlist offers three segments of about 9.7 s, so 15 s are already published when the container opens and decoding takes under a second; the stream is held open for about 2 s per camera, not 15.

### Decode and detector

PyAV 19.0.2 (wheel bundles FFmpeg 8 libraries, LGPL/GPL components as built by the PyAV project; PyAV itself BSD-3) opens the m3u8 URL directly. `imageio-ffmpeg` is pinned as the documented fallback but its binary path is not used: PyAV worked at the first attempt (section 2). Ultralytics 8.4.203 (AGPL-3.0, matching this repository) with YOLO11 small weights from the Ultralytics release assets, ByteTrack via `model.track(persist=True)`, device `mps`, `imgsz=960`, COCO classes car, motorcycle, bus, truck. `lap` is pinned because Ultralytics otherwise installs it at run time. The Ultralytics settings file gets `sync=False` so nothing is sent to their platform.

### Viewer

`live/cameras.geojson` becomes a GeoJSON source refreshed with the other snapshots. A "Camera counts" layer draws a ring around each sampled camera, sized by vehicles per minute (A plus B) and faded with sample age; the camera popup gains the latest counts per direction, density, flow index, sample time and age, model name, and the speed line stating "uncalibrated". Road colours are untouched (step 6).

## 2. Proof on the real stream, before the design was fixed

Install: `uv add --optional cameras "av>=15" "ultralytics>=8.3" "imageio-ffmpeg>=0.6" "lap>=0.5.12"` then `uv sync --extra cameras`. Resolved to av 19.0.2, ultralytics 8.4.203, torch 2.15.0, torchvision 0.29.2, opencv-python 5.0.0.93, lap 0.5.14, imageio-ffmpeg 0.8.1, all pinned in `uv.lock`. The poller install stays without them (`uv sync` without the extra).

Finding at import: both `av` and `cv2` wheels bundle a `libavdevice` and Objective-C prints "Class AVFFrameReceiver is implemented in both" twice. No crash followed in any run of this step; the sampler imports `av` first and never opens a capture device. Unverified beyond the runs listed here.

PyAV on CCTV-101 (I-10 McClure West), `av.open(url)` with a user agent option:

```
open 1.77 s; streams [('data', None), ('video', 'h264')]
resolution 1280 720 base_rate 15/1 time_base 1/90000
decoded 227 frames spanning 15.07 s in 0.46 s; median frame dt 0.067 -> 14.93 fps
```

So the first attempt (PyAV) worked and the fallback (imageio-ffmpeg) was not needed.

YOLO11 on the same 227 frames, `model.track(..., device="mps")`, after one warm-up call (the first mps call compiles kernels and took 16.8 s for the 227 frames; the numbers below exclude it). `predictor.model.device` reports `mps`, which is the proof that mps runs; `YOLO.model` itself stays on cpu, a wrapper detail.

| model | imgsz | tracking fps on mps | boxes per frame | distinct track ids in 15 s |
|---|---|---|---|---|
| yolo11n | 640 | 41.1 | 2.52 | 15 |
| yolo11n | 960 | 35.1 | 4.14 | 10 |
| yolo11s | 640 | 33.5 | 3.44 | 16 |
| yolo11s | 960 | 31.4 | 5.11 | 12 |

Plain predict, 100 frames, yolo11n 640: mps 60.4 fps, cpu 40.7 fps. More boxes per frame give fewer, longer tracks (fewer id fragments), which is what the counting line needs. Chosen: yolo11s at 960, 2.1 times real time for a 15 fps stream. The missed vehicles in the nano 640 frame were the far ones; near the counting line at mid frame the boxes are 40 to 80 px.

## 3. Tuning on saved clips, and what it changed in the design

Three 15 s clips (CCTV-101 McClure West, CCTV-1196 I-405 north of Manchester, CCTV-1028 I-10 Cloverfield) were saved once to disk and every comparison below ran on them, so the Caltrans server saw three stream opens for all of it.

Detector recall, plain `predict`, boxes per frame at confidence 0.1 and 0.25, mps:

| model | imgsz | fps | 101 @0.1 / @0.25 | 1196 @0.1 / @0.25 | 1028 @0.1 / @0.25 |
|---|---|---|---|---|---|
| yolo11s | 960 | 53 / 50 | 21.2 / 7.5 | 19.9 / 6.9 | 30.2 / 16.2 |
| yolo11s | 1280 | 39 / 40 | 21.5 / 9.1 | 9.8 / 2.1 | 31.4 / 15.8 |
| yolo11m | 960 | 28 / 28 | 19.8 / 10.3 | 10.2 / 3.4 | 26.4 / 14.2 |
| yolo11m | 1280 | 15 / 16 | 20.4 / 10.3 | 4.4 / 0.8 | 21.4 / 8.9 |

The medium model and the native 1280 input are both worse on the oblique Manchester view, so yolo11s at 960 stays. Most vehicles in these soft 720p frames score between 0.1 and 0.3.

The first trial round then showed the real defect: on CCTV-1196 the detector found 20 boxes per frame but the tracker returned ids for 2 per frame (median track length 3 frames), and the line counted 2 vehicles in 15 s on a six-lane freeway. Cause: Ultralytics' ByteTrack default `fuse_score: True` multiplies the box overlap by the detection confidence before matching, so a 0.2 detection never matched its own track. Attack the premise: the thresholds were not the problem, the cost function was.

Tracking on the three clips, yolo11s 960, counts across the lines of section 4, tracked boxes per frame and median track length:

| tracker | 101 A/B, tracked/f, len | 1196 A/B, tracked/f, len | 1028 A/B, tracked/f, len |
|---|---|---|---|
| default bytetrack.yaml, conf 0.25 | 0/5, 8.1, 23 f | 2/0, 2.3, 3 f | 22/15, 11.8, 15 f |
| thresholds lowered, fuse on | 0/5, 8.1, 23 f | 2/0, 2.3, 3 f | 22/15, 11.8, 15 f |
| fuse off, match 0.8 | 0/8, 19.8, 48 f | 11/28, 14.8, 10 f | 33/22, 23.5, 17 f |
| fuse off, match 0.9 (chosen, `cameras/bytetrack_cctv.yaml`) | 0/7, 19.4, 59 f | 14/29, 15.1, 15 f | 34/24, 24.0, 27 f |
| botsort.yaml default | 0/6, 9.1, 28 f | 2/0, 2.1, 3 f | 19/16, 12.0, 23 f |

Detection confidence floor 0.05 at `track()`, tracker thresholds 0.15 / 0.05 / 0.15, buffer 30, `fuse_score: False`, `match_thresh: 0.9`. Camera 101 shows no A crossings because its line at 40 % of the height sits above the near lanes and the far lanes carry both directions on the left of the frame; that is the kind of thing the contact sheet is for.

Tried and rejected: a staleness test comparing the 2-minute still with a stream frame. Mean absolute grey difference after resizing both to 320 by 180 was 20 to 52 for every camera with no gap between the live ones and the two stale ones, because the still is a 320 by 260 resample of the same encoder output. The stills of CCTV-1005 and CCTV-359 carried the same stale burned-in clock (09/12/2026 05:25 and 11/05/2026 05:41 on 2026-11-06 13:47 Pacific) as their streams, so the still is no fallback for a frozen camera, only for a missing stream. The burned-in clock is the only staleness signal and it is left to the human reading the sheet; `cameras/subset.json` notes the two cameras.

## 4. Lines per camera

`python -m cameras preview --out <dir>` saves one frame per subset camera with the configured line and height ticks at 0.3 to 0.7 drawn; lines were then set by eye (fractions of height, horizontal across the full width): 101 at 0.40, 1028 at 0.45, 1005 at 0.45, 1235 at 0.60, 985 at 0.55, 1026 at 0.60, 359 at 0.50, 1196 at 0.50, 1194 at 0.50, 1190 at 0.40, 25 at 0.50. CCTV-1194 is out of focus (a grey blur) and is kept in the subset to show what the sampler reports for a useless view. CCTV-1026 is a 960 by 540 stream.

## 5. Viewer

`viewer/app.js`: a `camera_counts` GeoJSON source from `live/cameras.geojson`, refreshed with the other snapshots; a `camera-counts` circle layer under the camera dots, radius 9 to 24 px and colour from a four-stop flow ramp by `vehicles_per_min` (A plus B), opacity fading from 5 to 30 minutes of sample age, hollow grey where `status` is not `ok`; a "Camera counts" toggle; a legend line; a "camera sampler" row in the feeds table (cameras counted, newest sample age, the sampler's note, or "not found" when the file is missing); the camera popup gains the counts block (sample time and age, A and B crossings, per minute, flow, density, all boxes, tracks, the speed line saying not calibrated, resolution, fps, frames, model, device, decode and inference time, and the sentence that A and B are sides of a line). Clicking a ring opens the same popup. `index.html`: a method bullet and the Ultralytics and PyAV attribution line. Road colours untouched.

Browser check (Claude desktop app's built-in browser, my own `serve.py --port 8081 --screenshots`, the user's server on 8080 untouched), values read from the page by script after round 1:

- The feeds table row read "11 of 11 cameras counted, newest sample 5 s old; 15.0 s samples, one camera at a time, a round every 300.0 s".
- `queryRenderedFeatures` on `camera-counts` at zoom 14.2 over Cloverfield returned CCTV-1028, status ok, 246.6 vehicles per minute, 51 s old; at zoom 10.56 over the whole corridor it returned all 11 subset cameras.
- The toggle set the layer's visibility to `none` and back to `visible`.
- The popup of CCTV-1028 (a real click) showed the Caltrans still (loaded, 320 by 260) and the counts text: "A (down on screen): 32 crossings in 15.08 s = 127.3/min, flow 116.1 px/s, density 13.36; B (up on screen): 30 crossings in 15.08 s = 119.3/min, flow 108.9 px/s, density 12.37; all boxes 27.32 per frame, 145 tracks; speed: not calibrated, none given; 1280x720 at 12.05 fps, 182 frames; yolo11s (Ultralytics 8.4.203) on mps; decode 0.13 s, inference 3.85 s". Those are the numbers the sampler printed for that sample.
- Console: three warnings from the OpenFreeMap Positron style's own shield filters (`Expected value to be of type number, but found null`), nothing from this page.
- `docs/img/cameras-popup.jpg` (browser screenshot, 800 by 600) and `docs/img/cameras-overview.jpg` (canvas, all 11 rings over the corridor).

Unverified: the row shown when `live/cameras.geojson` does not exist (the code path exists, the file was present throughout), the phone layout, and a popup for a `no_stream` camera (none occurred in the run).

## 6. Licences, read from the installed packages

| package | version | licence as declared in its metadata | note |
|---|---|---|---|
| ultralytics | 8.4.203 | AGPL-3.0 (classifier: AGPLv3+) | the reason this repository is AGPL-3.0; YOLO11s weights from the Ultralytics GitHub release v8.4.0, same licence |
| av (PyAV) | 19.0.2 | BSD-3-Clause | the wheel bundles FFmpeg 8 shared libraries whose own `license` string reads "LGPL version 3 or later" (`--enable-version3`, no `--enable-gpl` in the recorded configuration), plus libx264, libx265, libvpx, dav1d, SVT-AV1, opus, webp, vmaf dylibs. x264 and x265 are GPL projects; their presence in an LGPL-labelled build is for the user to weigh if the wheel is redistributed. This project only decodes H.264 with FFmpeg's own decoder. |
| opencv-python | 5.0.0.93 | Apache-2.0 | drawing and the homography |
| torch | 2.15.0 | Apache-2.0 and BSD and others (License-Expression in its metadata) | 565 MB |
| torchvision | 0.29.2 | BSD | |
| imageio-ffmpeg | 0.8.1 | BSD-2-Clause | pinned fallback, unused |
| lap | 0.5.14 | BSD-2-Clause | ByteTrack's assignment solver |

The `cameras` extra adds about 1.0 GB to the venv (site-packages 1.2 GB in all, torch 565 MB, cv2 119 MB, av 45 MB). The weights file is 19.3 MB under `data/models/` (gitignored). The Ultralytics settings file at `~/Library/Application Support/Ultralytics/settings.json` has `sync` set to false by the sampler at start, so no run data goes to Ultralytics' platform.

Caltrans: the stream service is used as section 1 says (one open at a time, about 2 s per camera, 11 cameras per 5 minutes); the camera images "may carry additional rights" per the data page, so the contact sheets under `docs/img/` reproduce Caltrans frames with their logo and clock intact.

## 7. Environment finding: the editable install is not reliable on this Mac

While running the suite as `.venv/bin/pytest` (not `python -m pytest`) every test module failed to import `poller` and `cameras`, although `uv run python -c "import poller"` worked (that form has the working directory on `sys.path`). `python -v` showed why:

```
Skipping hidden .pth file: '.../site-packages/la_live.pth'
Skipping hidden .pth file: '.../site-packages/distutils-precedence.pth'
```

`ls -lO` showed the macOS `hidden` flag (UF_HIDDEN) on `.venv` and on every entry under it (129 of 129 in site-packages), while the repository root and the project folders carry none. Clearing it with `chflags nohidden` on the `.pth` files made `.venv/bin/pytest` pass (26 tests), and a no-op `uv sync --extra cameras` ("Checked 59 packages") put the flag back on the whole tree at once. So uv 0.13.7 marks the venv hidden on macOS and CPython 3.13.18's `site.py` refuses hidden `.pth` files. The step 3 log blamed hatchling's `_editable_impl_*.pth` for the same symptom; the file name was never the cause, the flag was, and the uv_build backend only worked while its freshly written `.pth` had not been flagged yet. A stray `la_live 2.pth` from 10:41 Pacific sits next to the real one, a duplicate from some earlier write; it is harmless and outside the repository.

Fix in the repository, independent of the flag: `[tool.pytest.ini_options] pythonpath = ["."]` in `pyproject.toml`, and the two `prep/` scripts that import `poller` (`step3_report.py`, `step4_compare.py`) put the repository root on `sys.path` themselves. `python -m poller`, `python -m cameras` and `prep/step7_compare.py` never needed the `.pth`. Verified with the venv still flagged: `.venv/bin/pytest -q` 26 passed, both prep scripts answer `--help`.

## 8. The run: five rounds, 21:49:49 to 22:10:47 UTC

```
caffeinate -i uv run python -m cameras run --rounds 5 --every 300 --sheets 101 1196
warm-up 4.5 s; model yolo11s ultralytics 8.4.203 on mps, imgsz 960
round 1 at 21:49:49Z took 56.7 s; round 2 at 21:54:50Z 60.2 s; round 3 at 21:59:50Z 60.9 s; round 4 at 22:04:51Z 62.7 s; round 5 at 22:09:51Z 62.0 s
```

55 samples, 55 with status `ok`, none `no_stream` or `decode_error`; every stream opened at every round. The user's poller and `serve.py` ran untouched throughout (the sampler read `live/detectors.geojson` and wrote `live/cameras.geojson` and `history/cameras.sqlite` only). One round of 11 cameras takes about 61 s of wall time, of which about 60 s is open plus decode plus inference (0.8 to 1.0 s open, 0.1 to 0.2 s decode, 2.8 to 5.0 s inference per camera); the rest of the 300 s is sleep.

Per camera over the five rounds (means over the rounds; crossings listed per round in order):

| camera | view | resolution | fps | frames | open s | decode s | infer s | A crossings per round | B crossings per round | A /min | B /min | tracks | boxes/frame |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 101 | I-10 McClure West, Santa Monica | 1280x720 | 14.93 | 215 | 1.0 | 0.2 | 4.8 | 8 4 9 8 6 | 6 7 4 4 2 | 27.6 | 17.8 | 51.8 | 8.9 |
| 1028 | I-10 east of Cloverfield | 1280x720 | 12.05 | 182 | 1.0 | 0.1 | 4.0 | 32 27 28 29 23 | 30 40 27 28 27 | 110.6 | 120.9 | 136.6 | 25.0 |
| 1005 | I-10 Crenshaw (stale loop) | 1280x720 | 14.93 | 227 | 0.8 | 0.2 | 4.9 | 24 21 18 21 34 | 25 34 35 22 28 | 94.0 | 114.7 | 131.8 | 14.5 |
| 1235 | I-10 Maple Ave | 1280x720 | 7.52 and 14.93 | 127 | 0.8 | 0.1 | 2.8 | 4 4 3 2 4 | 28 24 29 20 32 | 13.5 | 105.8 | 145.0 | 29.2 |
| 985 | I-10 Channing St | 1280x720 | 14.93 | 227 | 0.8 | 0.2 | 4.5 | 27 25 27 28 32 | 5 1 2 2 2 | 110.7 | 9.6 | 94.6 | 9.6 |
| 1026 | I-10 at Route 110 | 960x540 | 14.93 | 227 | 0.8 | 0.1 | 4.4 | 12 19 13 10 16 | 5 10 8 3 9 | 55.8 | 27.9 | 73.8 | 8.3 |
| 359 | I-405 La Tijera (stale loop) | 1280x720 | 14.93 | 227 | 0.8 | 0.2 | 4.6 | 4 5 3 1 2 | 10 12 7 6 6 | 11.9 | 32.7 | 68.6 | 2.8 |
| 1196 | I-405 north of Manchester | 1280x720 | 14.93 | 226 | 0.8 | 0.2 | 4.9 | 10 8 16 14 7 | 28 23 28 22 12 | 44.0 | 90.4 | 136.4 | 10.7 |
| 1194 | I-405 south of Manchester (out of focus) | 1280x720 | 14.93 | 226 | 0.8 | 0.1 | 4.2 | 0 0 0 0 0 | 0 0 0 0 0 | 0.0 | 0.0 | 29.4 | 0.7 |
| 1190 | I-405 south of Century | 1280x720 | 14.93 | 226 | 1.0 | 0.1 | 5.0 | 3 0 1 1 0 | 39 27 31 50 41 | 4.0 | 150.4 | 180.8 | 25.0 |
| 25 | I-5 Meadowdale | 1280x720 | 14.93 | 227 | 0.8 | 0.1 | 4.7 | 33 39 37 35 37 | 25 14 22 13 14 | 144.2 | 70.1 | 125.8 | 13.8 |

Readings: the fps is the measured median frame interval; CCTV-1028 delivers 12 fps and CCTV-1235 7.5 fps in four rounds and 15 in one. The mps device is confirmed by `predictor.model.device` and by the 2.1 times real time inference. The round-to-round spread of a camera's crossings (for example 1190 B: 39, 27, 31, 50, 41) is the natural spread of 15 s samples plus tracking noise; a 15 s count has a counting error of several vehicles.

Lag: the burned-in Caltrans clock in the last contact sheets reads 14:07:05 to 14:07:20 Pacific for CCTV-101 whose sample started 22:09:51 UTC (14:09:51 Pacific), and 14:09:22 to 14:09:37 for CCTV-1196 sampled at 22:10:30 UTC. So the decoded 15 s sit 1 to 3 minutes behind wall clock: the decoder starts at the oldest of the three published segments, and the encoder adds its own delay. `sampled_at` is the time the sampler started on that camera, not the time of the footage.

### Hand-count evidence

`docs/img/cctv-101-sheet.jpg` and `docs/img/cctv-1196-sheet.jpg` show the last round's sample of each camera: one frame per second, 16 frames, the counting line in yellow, every tracked box with its track id, green for a track labelled A (down on screen), orange for B, grey undirected, an asterisk and a thick box on the tracks that were counted. The counts for those exact clips, from `history/cameras.sqlite`:

| camera | sampled_at (UTC) | footage clock (Pacific) | frames | A crossings | A /min | B crossings | B /min | tracks | boxes/frame |
|---|---|---|---|---|---|---|---|---|---|
| 101 | 22:09:51 | 14:07:05 to 14:07:20 | 227 | 6 | 23.9 | 2 | 8.0 | 71 | 14.54 |
| 1196 | 22:10:30 | 14:09:22 to 14:09:37 | 226 | 7 | 28.0 | 12 | 48.0 | 114 | 6.82 |

What I can say from the sheets without a hand count: on CCTV-101 the large near vehicles are the weak point (the dark SUV at 14:07:07 and the red car at 14:07:10 carry no box in those frames, the striped car at 14:07:11 does), so the A count there is an undercount of the near lane; on CCTV-1196 several large trucks in the near lanes carry no box and the far carriageway has many unboxed cars, so 19 crossings in 15 s on a freeway this busy is an undercount, probably by a factor near two. The user's count of the same frames is the check the plan asks for; the per-second sheet is enough to count crossings of the yellow line by eye, and the tracker's ids let a missed or doubled vehicle be named.

### Camera against detector

`prep/step7_compare.py` found 15 camera-sample by station pairs, all for CCTV-25 (I-5 Meadowdale, pm 21.34) against three ATMS stations on I-5 within a mile that reported a volume: 718371 Dorris N (pm 21.47), 718370 Dorris S (pm 21.47), 716955 Stadium S (pm 21.93). No I-10 or I-405 station in the subset reported a volume (the ATMS feed covers 16 corridor stations, all on routes 2, 5, 101 and 110). The camera segment (25373) is not the stations' segment, so the match is by route and postmile, not by segment.

| sampled | camera A /min | camera B /min | camera both /min | Dorris N vol/30 s (per min) | Dorris S (per min) | Stadium S (per min) |
|---|---|---|---|---|---|---|
| 21:50:41Z | 131.4 | 99.6 | 231.0 | 13 (26) | 10 (20) | 9 (18) |
| 21:55:44Z | 155.3 | 55.8 | 211.1 | 13 (26) | 10 (20) | 11 (22) |
| 22:00:46Z | 147.4 | 87.6 | 235.0 | 12 (24) | 11 (22) | 11 (22) |
| 22:05:47Z | 139.4 | 51.8 | 191.2 | 8 (16) | 7 (14) | 9 (18) |
| 22:10:47Z | 147.4 | 55.8 | 203.2 | 12 (24) | 6 (12) | 9 (18) |

The mismatch is plain: the camera counts 190 to 235 vehicles per minute over both carriageways, each station reports 12 to 26 per minute. A ratio of eight to ten between a camera seeing all lanes of both directions and a single station is what a per-lane figure would give on a freeway with four to five lanes per direction; the ATMS documentation read in step 4 does not say whether `volume_30s` is per lane or per station, so that reading is unverified. The station speeds fell from 42 to 19 km/h on Dorris N between 21:50 and 22:05 while the camera's A count stayed at 131 to 155 per minute, consistent with dense slow traffic in that direction. More pairs need a camera on a reporting I-10 or I-405 station, which the feed does not offer today.

## 9. Commands

```
uv add --optional cameras "av>=15" "ultralytics>=8.3" "imageio-ffmpeg>=0.6" "lap>=0.5.12"; uv sync --extra cameras
uv run python -m cameras preview --out <dir>                          # one frame per camera with the line, to place lines
caffeinate -i uv run python -m cameras run --rounds 5 --every 300 --sheets 101 1196
uv run python prep/step7_compare.py
uv run python serve.py --port 8081 --screenshots                       # my own server for the browser check; the user's 8080 untouched
uv run pyright; uv run python -m pytest -q                            # 0 errors; 26 tests
```

## 10. Verified, unverified, principles

Verified on the real streams: all 11 subset streams open (and 37 of 154 corridor cameras answer a playlist at all, 45 answer 404, 26 have no stream URL, the rest timed out in the probe); PyAV decodes them directly; YOLO11s tracks on mps at 31 to 50 fps; the sampler holds one stream at a time and a round of 11 takes 61 s; five rounds wrote 55 samples with no failure; the viewer draws the rings and the popup from the real file; the tests cover crossings per direction, a touching track, a double crossing, a crossing beyond the segment, the fraction-to-pixel line, the synthetic calibration speed, the GeoJSON writer, the SQLite store and the detector matching.

Unverified: the counts against a hand count (the evidence is in the two sheets, the count is the user's); whether `volume_30s` is per lane; the speed path on a real camera (no calibration exists); a `no_stream` or `decode_error` sample in the viewer (none occurred); the phone layout; behaviour over hours (five rounds only); whether the Caltrans encoders deliver the two stale loops to everyone or only to this client.

Principles that changed a decision: Prove it works (the first trial round's sheet, not the fps number, exposed the tracker defect); Attack the premise (the fix was the tracker's cost function, not thresholds or a bigger model, which were both measured and rejected); Model the domain (directions are a sign on a directed line, tracks count once on their first crossing, so jitter is handled by the rule rather than by a filter); Boundary discipline (subset config and calibration files are pydantic models, camera records are parsed once from the GeoPackage); Laziness protocol (the still-versus-stream staleness check was measured, found useless and deleted rather than shipped with a threshold); Encode lessons in structure (the `.pth` finding became `pythonpath` in pyproject and a shim in the two scripts, not a note to run `chflags`).
