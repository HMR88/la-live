// la-live viewer. MapLibre GL JS 6.16 as an ES module from unpkg (MapLibre 6
// ships no classic script build) and pmtiles 4.5 as its classic bundle (its ES
// build imports a bare "fflate" a browser cannot resolve). No build step.
// Coordinates everywhere are WGS84 lon/lat (EPSG:4326), as MapLibre expects.
import * as maplibregl from "https://unpkg.com/maplibre-gl@6.16.0/dist/maplibre-gl.mjs";
import { speedParts, durationText, relativeAge, pacificTime, pacificDateTime, roadClassName, parseLimitMph, limitComparison,
  sourceText, headingText, freshness, modeLabel, sourceLabel, plural, rampColour } from "./format.js";
const pmtiles = window.pmtiles;

const BASE = new URL("..", location.href).href; // repository root as served by serve.py or Pages
// Live files: on a public host they come first from the `live` branch that the unattended job force-pushes
// every 5 minutes (raw.githubusercontent.com sends CORS * and a fresh query string misses its 5-minute cache),
// then from this site's own copy. On localhost the local poller's files are the freshest there are.
const LOCAL_HOST = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
const DEFAULT_CHANNEL = "https://raw.githubusercontent.com/HMR88/la-live/live/";
// ?channel=<url> overrides the channel (a fork's branch, or a local folder when rehearsing); ?channel= disables it.
const channelParam = new URLSearchParams(location.search).get("channel");
const CHANNEL = channelParam !== null ? (channelParam || null) : (LOCAL_HOST ? null : DEFAULT_CHANNEL);
const LIVE_FILES = { segments: "segments.json", vehicles: "vehicles.geojson", events: "events.geojson",
  detectors: "detectors.geojson", camera_counts: "cameras.geojson", status: "status.json" };
let dataSource = LOCAL_HOST ? "local" : "unknown"; // channel | site | local, for the pill
let channelWarned = false; // one console line per outage, not one per file per refresh

async function fetchLive(name) {
  const file = LIVE_FILES[name];
  if (CHANNEL) {
    try {
      const r = await fetch(CHANNEL + file + "?t=" + Date.now(), { cache: "no-store" });
      if (r.ok) { dataSource = "channel"; channelWarned = false; return await r.json(); }
      if (!channelWarned) { console.warn(`live branch answered HTTP ${r.status} for ${file}, using the site's copy`); channelWarned = true; }
    } catch (err) {
      if (!channelWarned) { console.warn("live branch unreachable, using the site's copy", err); channelWarned = true; }
    }
  }
  const r = await fetch(BASE + "live/" + file + "?t=" + Date.now(), { cache: "no-store" });
  if (!r.ok) throw new Error(`${file}: HTTP ${r.status}`);
  dataSource = CHANNEL ? "site" : "local";
  return await r.json();
}
// Vehicles per minute (both directions) ramp for the camera rings. Flow, not speed.
const FLOW_STOPS = [[0, "#fde0dd"], [40, "#fa9fb5"], [80, "#dd3497"], [120, "#7a0177"]];
let cameraReadings = {}; // cctv_id -> properties of live/cameras.geojson, for the camera popup
const REFRESH_MS = 30000;
const CORRIDOR = [[-118.52, 33.98], [-118.22, 34.09]];
const AGENCY_COLOUR = { metro: "#e4572e", bbb: "#1f5fbf", ccb: "#2a9d8f", ladot: "#8e44ad" };
const AGENCY_NAME = { metro: "LA Metro", bbb: "Big Blue Bus", ccb: "Culver CityBus", ladot: "LADOT" };
// Absolute km/h ramp for roads and detectors (the data is km/h; the legend labels it in mph). Step 6 replaces it with a ratio to baseline.
const SPEED_STOPS = [[0, "#7f0000"], [15, "#d7301f"], [30, "#fc8d59"], [45, "#fee08b"], [60, "#a6d96a"], [80, "#1a9850"], [100, "#00441b"]];

const protocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const map = new maplibregl.Map({
  container: "map",
  style: "https://tiles.openfreemap.org/styles/positron",
  bounds: CORRIDOR,
  fitBoundsOptions: { padding: { top: 20, bottom: 20, left: 360, right: 20 } },
  pitch: 55,
  bearing: -15,
  maxPitch: 75,
  hash: true,
  attributionControl: true,
  canvasContextAttributes: { preserveDrawingBuffer: true }, // lets the screenshot helper read the canvas
});
window.lamap = map; // for the browser console and the step 5 check
map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-right");

function speedRamp(value) {
  const stops = SPEED_STOPS.flat();
  return ["interpolate", ["linear"], value, ...stops];
}

map.on("load", () => {
  map.addSource("dem", {
    type: "raster-dem",
    tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"],
    encoding: "terrarium",
    tileSize: 256,
    maxzoom: 15,
    attribution: "Terrain Tiles (Mapzen); 3DEP, GMTED2010 and SRTM data courtesy of the U.S. Geological Survey; ETOPO1 NOAA",
  });
  map.setTerrain({ source: "dem", exaggeration: 1.3 });
  map.addSource("corridor", {
    type: "vector",
    url: "pmtiles://" + BASE + "data/base/corridor.pmtiles",
    promoteId: { segments: "segment_id" },
    attribution: "© OpenStreetMap contributors (ODbL), Geofabrik, LA Metro, Big Blue Bus, Culver CityBus, LADOT, Caltrans",
  });
  map.addSource("buildings", { type: "vector", url: "pmtiles://" + BASE + "data/base/buildings.pmtiles" });
  const empty = { type: "FeatureCollection", features: [] };
  for (const name of ["vehicles", "events", "detectors", "camera_counts"]) map.addSource(name, { type: "geojson", data: empty });

  // Place our layers under the basemap's labels so names stay readable.
  const firstSymbol = map.getStyle().layers.find((l) => l.type === "symbol")?.id;

  map.addLayer({ id: "hillshade", type: "hillshade", source: "dem",
    paint: { "hillshade-exaggeration": 0.35, "hillshade-shadow-color": "#5a4a3a" } }, firstSymbol);

  map.addLayer({
    id: "buildings-3d", type: "fill-extrusion", source: "buildings", "source-layer": "buildings", minzoom: 13,
    paint: {
      "fill-extrusion-color": ["interpolate", ["linear"], ["get", "height_m"], 4, "#dcd6cc", 30, "#c9c0b2", 100, "#b0a596"],
      "fill-extrusion-height": ["get", "height_m"],
      "fill-extrusion-base": 0,
      "fill-extrusion-opacity": 0.85,
    },
  }, firstSymbol);

  map.addLayer({
    id: "shapes", type: "line", source: "corridor", "source-layer": "shapes",
    paint: { "line-color": ["case", ["==", ["coalesce", ["get", "route_color"], ""], ""], "#888888", ["concat", "#", ["get", "route_color"]]],
             "line-width": 1, "line-opacity": 0.5 },
    layout: { visibility: "visible" },
  }, firstSymbol);

  const classWidth = ["match", ["get", "highway"], ["motorway", "trunk"], 2.2, ["primary"], 1.8, ["secondary"], 1.4, ["tertiary"], 1.1, 0.8];
  const speedState = ["coalesce", ["feature-state", "speed"], -1];
  map.addLayer({
    id: "roads", type: "line", source: "corridor", "source-layer": "segments",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": ["case", ["<", speedState, 0], "#bdbdbd", speedRamp(speedState)],
      "line-width": ["interpolate", ["exponential", 1.5], ["zoom"], 9, ["*", classWidth, 0.6], 13, ["*", classWidth, 2.2], 16, ["*", classWidth, 5]],
      "line-opacity": ["case", ["<", speedState, 0], 0.55, 0.95],
    },
  }, firstSymbol);

  map.addLayer({
    id: "stops", type: "circle", source: "corridor", "source-layer": "stops", minzoom: 14,
    paint: { "circle-radius": 3, "circle-color": "#fff", "circle-stroke-color": "#555", "circle-stroke-width": 1 },
  });

  map.addLayer({
    id: "closures", type: "line", source: "events", filter: ["==", ["get", "type"], "closure"],
    paint: { "line-color": "#ff7f00", "line-width": 5, "line-dasharray": [1.5, 1], "line-opacity": 0.9 },
  });
  map.addLayer({
    id: "closure-points", type: "circle", source: "events", filter: ["all", ["==", ["get", "type"], "closure"], ["==", ["geometry-type"], "Point"]],
    paint: { "circle-radius": 6, "circle-color": "#ff7f00", "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 },
  });
  map.addLayer({
    id: "signs", type: "circle", source: "events", filter: ["==", ["get", "type"], "sign"],
    paint: { "circle-radius": 5, "circle-color": ["case", ["==", ["get", "text"], ""], "#9ecae1", "#1d4e89"], "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 },
  });
  map.addLayer({
    id: "incidents", type: "circle", source: "events", filter: ["==", ["get", "type"], "incident"],
    paint: { "circle-radius": 8, "circle-color": "#d62728", "circle-stroke-color": "#fff", "circle-stroke-width": 2 },
  });
  map.addLayer({
    id: "incident-labels", type: "symbol", source: "events", filter: ["==", ["get", "type"], "incident"],
    layout: { "text-field": "!", "text-size": 12, "text-font": ["Noto Sans Bold"], "text-allow-overlap": true },
    paint: { "text-color": "#fff" },
  });

  const detSpeed = ["coalesce", ["get", "speed_kmh"], -1];
  map.addLayer({
    id: "detectors", type: "circle", source: "detectors", minzoom: 10,
    paint: {
      "circle-radius": ["case", ["<", detSpeed, 0], 3, 6],
      "circle-color": ["case", ["<", detSpeed, 0], "#d9d9d9", speedRamp(detSpeed)],
      "circle-stroke-color": "#333", "circle-stroke-width": 1,
      "circle-opacity": ["case", ["<", detSpeed, 0], 0.5, 1],
    },
  });

  const vpm = ["coalesce", ["get", "vehicles_per_min"], 0];
  const sampledOk = ["==", ["get", "status"], "ok"];
  map.addLayer({
    id: "camera-counts", type: "circle", source: "camera_counts", minzoom: 9,
    paint: {
      "circle-radius": ["case", sampledOk, ["interpolate", ["linear"], vpm, 0, 9, 60, 15, 150, 24], 9],
      "circle-color": ["case", sampledOk, ["interpolate", ["linear"], vpm, ...FLOW_STOPS.flat()], "#ffffff"],
      "circle-opacity": ["case", sampledOk, ["interpolate", ["linear"], ["coalesce", ["get", "age_s"], 0], 300, 0.9, 1800, 0.35], 0.4],
      "circle-stroke-color": ["case", sampledOk, "#7a0177", "#888"],
      "circle-stroke-width": 1.5,
    },
  });
  map.addLayer({
    id: "cameras", type: "circle", source: "corridor", "source-layer": "cameras", minzoom: 11,
    paint: { "circle-radius": 6, "circle-color": "#222", "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 },
  });
  map.addLayer({
    id: "camera-labels", type: "symbol", source: "corridor", "source-layer": "cameras", minzoom: 11,
    layout: { "text-field": "C", "text-size": 9, "text-font": ["Noto Sans Bold"], "text-allow-overlap": true },
    paint: { "text-color": "#fff" },
  });

  map.addLayer({
    id: "vehicles", type: "circle", source: "vehicles",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 3, 14, 6, 17, 9],
      "circle-color": ["match", ["get", "agency"], "metro", AGENCY_COLOUR.metro, "bbb", AGENCY_COLOUR.bbb, "ccb", AGENCY_COLOUR.ccb, "ladot", AGENCY_COLOUR.ladot, "#444"],
      "circle-stroke-color": "#fff", "circle-stroke-width": 1.2,
      "circle-opacity": ["interpolate", ["linear"], ["get", "age_s"], 60, 1, 300, 0.45],
    },
  });
  map.addLayer({
    id: "vehicle-labels", type: "symbol", source: "vehicles", minzoom: 13,
    layout: { "text-field": ["get", "route"], "text-size": 11, "text-font": ["Noto Sans Bold"], "text-offset": [0, 1.1], "text-anchor": "top", "text-optional": true },
    paint: { "text-color": "#222", "text-halo-color": "#fff", "text-halo-width": 1.5 },
  });

  buildToggles();
  buildLegend();
  refresh();
  setInterval(refresh, REFRESH_MS);
  wirePopups();
});

// Live segment speeds join the road tiles by segment_id through feature state. segments.json carries the
// properties only; the geometry is already in the tiles.
let liveIds = new Set();
async function refreshSegments() {
  const data = await fetchLive("segments");
  const next = new Set();
  for (const p of data.rows) {
    next.add(p.segment_id);
    map.setFeatureState({ source: "corridor", sourceLayer: "segments", id: p.segment_id },
      { speed: p.speed_kmh, src: p.source, bus: p.speed_bus_kmh, vds: p.speed_vds_kmh, n_bus: p.n_bus, n_vds: p.n_vds, age: p.age_s });
  }
  for (const id of liveIds) {
    if (!next.has(id)) map.removeFeatureState({ source: "corridor", sourceLayer: "segments", id });
  }
  liveIds = next;
  return { count: data.rows.length, generated: data.generated };
}

let latestStatus = null; // the last status.json, for the pill's clock tick between fetches

function dot(kind) { return `<span class="dot ${kind}"></span>`; }
function row(kind, name, figure, title = "") { return `<li title="${title}">${dot(kind)}<span class="name">${name}</span><span class="figure">${figure}</span></li>`; }
function set(id, html) { document.getElementById(id).innerHTML = html; }

async function refreshStatus() {
  const s = await fetchLive("status");
  latestStatus = s;
  renderPill();
  const feedNames = { bbb: "Big Blue Bus", ccb: "Culver CityBus", ladot: "LADOT", metro_ws: "LA Metro" };
  const transit = Object.entries(s.feeds).map(([name, f]) => {
    const kind = f.errors ? (f.in_corridor ? "warn" : "bad") : "ok";
    return row(kind, feedNames[name] || name, plural(f.in_corridor ?? 0, "bus", "buses") + " in corridor",
      `${f.in_corridor ?? 0} of ${f.vehicles ?? 0} vehicles inside the corridor, ${f.errors} fetch errors`);
  });
  set("rows-transit", transit.join(""));

  const src = s.traffic_sources || {}, tr = s.traffic || {}, ev = tr.events || {};
  const srcKind = (k) => (src[k]?.errors ? (src[k].age_s > 900 ? "bad" : "warn") : src[k] ? "ok" : "unknown");
  const traffic = [
    row(srcKind("atms"), "Detectors", tr.atms ? plural(tr.atms.corridor_stations_with_speed, "station") + " reporting" : "no data", "Caltrans ATMS speed detectors with a reading in the corridor"),
    row(srcKind("chp"), "Incidents", plural(ev.incidents ?? 0, "incident"), "CHP incidents of the Los Angeles dispatch inside the corridor"),
    row(srcKind("lcs"), "Closures", plural(ev.closures ?? 0, "lane closure"), "Caltrans lane closures current now"),
    row(srcKind("cms"), "Signs", plural(ev.signs ?? 0, "sign") + " in service", "Caltrans changeable message signs"),
  ];
  set("rows-traffic", traffic.join(""));

  const run = s.run || { source: "local", interval_s: s.interval_s, cadence_s: s.interval_s };
  const pr = s.pairs_by_reason || {};
  const total = Object.values(pr).reduce((a, b) => a + b, 0);
  const details = [
    ["Snapshot", `${pacificDateTime(s.generated)} (${s.generated.slice(11, 19)} UTC), cycle ${s.cycle}`],
    ["Mode", run.source === "github-actions"
      ? `GitHub Actions, a ${run.minutes} min run every ${Math.round((run.cadence_s ?? 300) / 60)} min${run.run_url ? `, <a href="${run.run_url}">run ${run.run_id}</a>` : ""}`
      : `local poller, a cycle every ${run.interval_s ?? s.interval_s} s`],
    ["Roads with a speed", `${s.segments_in_snapshot} segments in the last 10 min`],
    ["Bus pairs", `${pr.kept ?? 0} kept of ${total} since start`],
    ...Object.entries(s.feeds).map(([name, f]) => [feedNames[name] || name,
      `${f.in_corridor ?? 0} of ${f.vehicles ?? 0} vehicles in corridor${f.retries ? `, ${f.retries} retries` : ""}${f.errors ? `, <span class="warn-text">${f.errors} errors: ${f.last_error || ""}</span>` : ""}${f.messages ? `, ${f.messages.toLocaleString()} WebSocket messages` : ""}` +
      (name === "bbb" && run.source === "github-actions" && f.errors ? ` <span class="warn-text">Big Blue Bus does not answer GitHub's runners (connect timeouts); its buses appear on local runs only.</span>` : "")]),
    ...Object.entries(src).map(([name, x]) => [{ atms: "Detector feed", chp: "CHP feed", lcs: "Closure feed", cms: "Sign feed" }[name] || name,
      `last good copy ${relativeAge(x.age_s)}, every ${x.interval_s} s${x.errors ? `, <span class="warn-text">${x.errors} errors</span>` : ""}` +
      (name === "atms" ? `. <span class="warn-text">Deprecated by Caltrans, still live.</span>` : "")]),
  ];
  set("detail-rows", details.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join(""));
}

function renderPill() {
  const s = latestStatus;
  if (!s) return;
  const age = Math.round((Date.now() - Date.parse(s.generated)) / 1000);
  const mode = modeLabel(s.run, s.interval_s);
  document.querySelector("#pill .dot").className = "dot " + freshness(age, mode.cadence);
  document.getElementById("pill-text").textContent = mode.text;
  const ageEl = document.getElementById("pill-age");
  const from = sourceLabel(dataSource);
  ageEl.textContent = relativeAge(age) + (from ? ` · ${from}` : "");
  ageEl.title = `snapshot written ${pacificDateTime(s.generated)}${from ? ", read " + from : ""}`;
  document.getElementById("pill").title = mode.kind === "channel"
    ? "The unattended job publishes a snapshot to the live branch every 5 minutes; vehicle positions are as old as the snapshot."
    : mode.kind === "scheduled" ? "Snapshots come from a scheduled job; vehicle positions are as old as the snapshot."
    : "A local poller writes a new snapshot every cycle; the page refetches every 30 s.";
}
setInterval(renderPill, 5000);

async function refreshCameras() {
  let fc;
  try {
    fc = await fetchLive("camera_counts");
  } catch {
    map.getSource("camera_counts").setData({ type: "FeatureCollection", features: [] });
    cameraReadings = {};
    set("rows-cameras", row("unknown", "Cameras counted", "sampler not running", "live/cameras.geojson is missing"));
    return;
  }
  map.getSource("camera_counts").setData(fc);
  cameraReadings = Object.fromEntries(fc.features.map((f) => [f.properties.cctv_id, f.properties]));
  const ok = fc.features.filter((f) => f.properties.status === "ok");
  // age from sampled_at, not the file's age_s, which is frozen at write time
  const ages = fc.features.map((f) => f.properties.sampled_at).filter(Boolean).map((t) => Math.round((Date.now() - Date.parse(t)) / 1000));
  const newest = ages.length ? Math.min(...ages) : null;
  const kind = newest === null ? "unknown" : newest < 900 ? "ok" : newest < 3600 ? "warn" : "bad";
  set("rows-cameras", row(kind, "Cameras counted", `${ok.length} of ${fc.features.length}, newest ${relativeAge(newest)}`, fc.note || ""));
}

async function refresh() {
  try {
    for (const name of ["vehicles", "events", "detectors"]) map.getSource(name).setData(await fetchLive(name));
    await refreshSegments();
    await refreshStatus();
    await refreshCameras();
  } catch (err) {
    console.error("refresh failed", err);
    document.querySelector("#pill .dot").className = "dot bad";
    document.getElementById("pill-text").textContent = "Live files not reachable";
    document.getElementById("pill-age").textContent = "";
  }
}

// The panel collapses to its header and status pill on phone widths, before the map has loaded.
function wireCollapse() {
  const panel = document.getElementById("panel"), btn = document.getElementById("collapse");
  const setCollapsed = (c) => { panel.classList.toggle("collapsed", c); btn.setAttribute("aria-expanded", String(!c)); btn.title = c ? "Expand the panel" : "Collapse the panel"; };
  btn.addEventListener("click", () => setCollapsed(!panel.classList.contains("collapsed")));
  if (window.matchMedia("(max-width: 480px)").matches) setCollapsed(true);
}
wireCollapse();

function buildToggles() {
  const groups = { Roads: ["roads"], Buses: ["vehicles", "vehicle-labels"], Routes: ["shapes"], Stops: ["stops"],
    Events: ["closures", "closure-points", "signs", "incidents", "incident-labels"], Detectors: ["detectors"], Cameras: ["cameras", "camera-labels"], "Camera counts": ["camera-counts"],
    Buildings: ["buildings-3d"], Hillshade: ["hillshade"] };
  const box = document.getElementById("toggles");
  const chip = (label, on, onClick) => {
    const b = document.createElement("button");
    b.className = "chip" + (on ? " on" : ""); b.textContent = label; b.type = "button"; b.setAttribute("aria-pressed", String(on));
    b.addEventListener("click", () => { const now = !b.classList.contains("on"); b.classList.toggle("on", now); b.setAttribute("aria-pressed", String(now)); onClick(now); });
    box.append(b);
    return b;
  };
  for (const [label, ids] of Object.entries(groups)) chip(label, true, (on) => ids.forEach((id) => map.setLayoutProperty(id, "visibility", on ? "visible" : "none")));
  chip("Terrain", true, (on) => map.setTerrain(on ? { source: "dem", exaggeration: 1.3 } : null));
  const pitch = document.createElement("button");
  pitch.className = "chip action"; pitch.type = "button"; pitch.textContent = "Go flat";
  pitch.addEventListener("click", () => {
    const flat = map.getPitch() > 1;
    map.easeTo({ pitch: flat ? 0 : 55, bearing: flat ? 0 : -15, duration: 800 });
    pitch.textContent = flat ? "Go 3D" : "Go flat";
  });
  box.append(pitch);
}

function buildLegend() {
  const max = SPEED_STOPS[SPEED_STOPS.length - 1][0];
  const css = SPEED_STOPS.map(([v, c]) => `${c} ${Math.round((v / max) * 100)}%`).join(", ");
  document.getElementById("legend").style.background = `linear-gradient(90deg, ${css})`;
  const labelsMph = [0, 15, 30, 45, 60];
  document.getElementById("legend-labels").innerHTML = labelsMph.map((m, i) => `<span>${m}${i === labelsMph.length - 1 ? " mph" : ""}</span>`).join("");
  const agencies = Object.entries(AGENCY_NAME).map(([k, n]) => `<span class="swatch" style="background:${AGENCY_COLOUR[k]}"></span>${n}`).join("");
  document.getElementById("legend-agencies").innerHTML = `Buses by agency:${agencies}. Camera rings grow and darken with vehicles per minute.`;
}

// Popup building blocks. Speeds come in km/h and are shown mph first.
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function speedKey(kmh, extra = "") {
  const sp = speedParts(kmh);
  if (!sp) return `<div class="key muted">No speed</div>`;
  return `<div class="key"><span class="swatch" style="background:${rampColour(kmh, SPEED_STOPS)}"></span><b>${sp.mph} mph</b><small>${sp.kmh} km/h</small>${extra}</div>`;
}
function pop(title, kicker, body, kickerStyle = "") {
  return `<div class="pop"><h3>${esc(title)}</h3><div class="kicker" style="${kickerStyle}">${kicker}</div>${body}</div>`;
}

function roadHtml(p, st) {
  const limit = parseLimitMph(p.maxspeed);
  let body;
  if (st.src) {
    const srcWords = sourceText(st.src, st.n_bus, st.n_vds);
    body = speedKey(st.speed) +
      `<div class="fact">${srcWords}, newest ${relativeAge(st.age)}</div>` +
      (limit ? `<div class="fact">Posted limit ${limit} mph <span class="tag">OpenStreetMap</span>${limitComparison(st.speed, limit) ? `, ${limitComparison(st.speed, limit)}` : ""}</div>` : "") +
      (st.src !== "vds" ? `<p class="hint">Bus speeds between stops run below car speeds.</p>` : "");
  } else {
    body = `<div class="key muted">No reading in the last 10 minutes</div>` +
      (limit ? `<div class="fact">Posted limit ${limit} mph <span class="tag">OpenStreetMap</span></div>` : "");
  }
  return pop(p.name || "Unnamed road", roadClassName(p.highway), body);
}

function busHtml(p) {
  const agency = AGENCY_NAME[p.agency] || p.agency;
  const sp = speedParts(p.speed_kmh);
  const heading = headingText(p.bearing);
  const key = sp ? `<div class="key"><b>${sp.mph} mph</b><small>${sp.kmh} km/h${heading ? ", " + heading : ""}</small></div>`
                 : `<div class="key muted">${heading ? heading[0].toUpperCase() + heading.slice(1) : "No speed yet"}</div>`;
  return pop(p.route ? `Route ${p.route}` : agency, `<b style="color:${AGENCY_COLOUR[p.agency] || "#444"}">${esc(agency)}</b>`,
    key + `<div class="fact">seen ${relativeAge(p.age_s)}</div>` +
    `<p class="hint">Bus ${esc(p.vehicle_id)}${p.trip_id ? ", trip " + esc(p.trip_id) : ""}. Speed is the last pair of positions on the route, not a live speedometer.</p>`);
}

function cameraHtml(p) {
  const c = cameraReadings[p.cctv_id];
  const title = (p.name || "").replace(/^\S+ : \(\d+\) /, "").replace(/^\S+: \(\d+\) /, "") || `Camera ${p.cctv_id}`;
  const img = p.image_url ? `<img src="${p.image_url}?t=${Date.now()}" alt="camera still" loading="lazy">` : "";
  let body = img + `<div class="fact">Still from Caltrans, refreshed every ${p.image_minutes ?? 2} min</div>`;
  if (c && c.status === "ok") {
    const sampleAge = c.sampled_at ? Math.round((Date.now() - Date.parse(c.sampled_at)) / 1000) : c.age_s;
    body += `<div class="key"><b>${c.vehicles_per_min}</b><small>vehicles per minute, counted ${relativeAge(sampleAge)}</small></div>` +
      `<div class="fact">${c.a.count} crossing down the frame, ${c.b.count} up, in ${c.duration_s} s</div>` +
      `<p class="hint">Counted from 15 s of the stream with YOLO11 and ByteTrack. Up and down are sides of a fixed line, not route directions. No speed: this camera is not calibrated.</p>`;
  } else if (c) {
    body += `<div class="key muted">No stream count</div><div class="fact">${esc(c.status)}${c.error ? ": " + esc(c.error) : ""}</div>`;
  }
  return pop(title, `Caltrans camera, ${esc(p.route)} ${esc(p.direction)}`, body);
}

function detectorHtml(p) {
  const kicker = `Caltrans detector, ${esc(p.route && /^\d/.test(p.route) ? "Route " + p.route : p.route)} ${esc(p.direction)}`;
  const body = p.speed_kmh
    ? speedKey(p.speed_kmh) + `<div class="fact">${plural(p.volume_30s ?? 0, "vehicle")} in 30 s, ${esc(p.colour_label)}, ${relativeAge(p.age_s)}</div><p class="hint">Deprecated Caltrans feed, still live.</p>`
    : `<div class="key muted">No reading</div><div class="fact">The station reports no speed right now.</div>`;
  return pop(p.name || "Detector", kicker, body);
}

function incidentHtml(p) {
  const details = JSON.parse(p.details || "[]");
  return pop(p.text || "Incident", `CHP incident, ${pacificTime(p.time)}`,
    `<div class="fact">reported ${relativeAge(Math.round((Date.now() - Date.parse(p.time)) / 1000))}</div>` +
    (details.length ? `<details><summary>Dispatch notes</summary><div class="mono">${details.map(esc).join("\n")}</div></details>` : ""));
}

function closureHtml(p) {
  return pop("Lane closure", `Caltrans${p.in_effect ? ", in effect now" : ", scheduled"}`,
    `<div class="fact">${esc(p.text)}</div><div class="fact">${pacificDateTime(p.time)} to ${p.end ? pacificDateTime(p.end) : "open end"}</div>`);
}

function signHtml(p) {
  return pop(p.location || "Message sign", `Caltrans sign, ${esc(p.route)} ${esc(p.direction)}`,
    p.text ? `<div class="mono">${esc(p.text)}</div>` : `<div class="key muted">${esc(p.display || "Blank")}</div>`);
}

// One click handler: the topmost layer under the pointer answers, so a road and the route shape beneath it
// never open two popups at once. Roads also answer on hover.
const CLICK_ORDER = [
  ["vehicles", busHtml], ["vehicle-labels", busHtml], ["incidents", incidentHtml], ["incident-labels", incidentHtml],
  ["closure-points", closureHtml], ["closures", closureHtml], ["signs", signHtml], ["detectors", detectorHtml],
  ["camera-counts", cameraHtml], ["cameras", cameraHtml], ["camera-labels", cameraHtml], ["stops", stopHtml],
  ["roads", (p, st) => roadHtml(p, st)], ["shapes", shapeHtml],
];
const POINTER_LAYERS = CLICK_ORDER.map(([id]) => id).filter((id) => id !== "roads" && id !== "shapes");

function stopHtml(p) { return pop(p.stop_name, `${esc(p.agency_name)} stop`, ""); }
function shapeHtml(p) {
  return pop(`Route ${p.route_short_name || p.route_id}`, esc(p.agency_name),
    `<div class="fact">${esc(p.route_long_name || "")}</div><div class="fact">${plural(p.trips ?? 0, "trip")} on this shape</div>`);
}

function wirePopups() {
  const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8, maxWidth: "300px" });
  map.on("mousemove", "roads", (e) => {
    const f = e.features[0];
    map.getCanvas().style.cursor = "crosshair";
    hover.setLngLat(e.lngLat).setHTML(roadHtml(f.properties, f.state || {})).addTo(map);
  });
  map.on("mouseleave", "roads", () => { map.getCanvas().style.cursor = ""; hover.remove(); });

  map.on("click", (e) => {
    const present = new Set(CLICK_ORDER.map(([id]) => id).filter((id) => map.getLayer(id)));
    const hits = map.queryRenderedFeatures(e.point, { layers: [...present] });
    for (const [id, html] of CLICK_ORDER) {
      const f = hits.find((h) => h.layer.id === id);
      if (!f) continue;
      hover.remove();
      const coords = f.geometry.type === "Point" ? f.geometry.coordinates.slice() : e.lngLat;
      new maplibregl.Popup({ offset: 10, maxWidth: "300px" }).setLngLat(coords).setHTML(html(f.properties, f.state || {})).addTo(map);
      return;
    }
  });
  for (const layer of POINTER_LAYERS) {
    map.on("mouseenter", layer, () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; });
  }
}

// Screenshot helper for docs/log/step5.md: run saveScreenshot("overview.png") in the console
// while serve.py runs with --screenshots. Nothing calls it on its own.
window.saveScreenshot = async (name) => {
  const data = map.getCanvas().toDataURL("image/png");
  const r = await fetch(BASE + "__screenshot?name=" + encodeURIComponent(name), { method: "POST", body: data });
  return r.text();
};
