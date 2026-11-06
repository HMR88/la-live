// la-live viewer. MapLibre GL JS 6.16 as an ES module from unpkg (MapLibre 6
// ships no classic script build) and pmtiles 4.5 as its classic bundle (its ES
// build imports a bare "fflate" a browser cannot resolve). No build step.
// Coordinates everywhere are WGS84 lon/lat (EPSG:4326), as MapLibre expects.
import * as maplibregl from "https://unpkg.com/maplibre-gl@6.16.0/dist/maplibre-gl.mjs";
const pmtiles = window.pmtiles;

const BASE = new URL("..", location.href).href; // repository root as served by serve.py
const LIVE = {
  segments: BASE + "live/segments.geojson",
  vehicles: BASE + "live/vehicles.geojson",
  events: BASE + "live/events.geojson",
  detectors: BASE + "live/detectors.geojson",
  status: BASE + "live/status.json",
};
const REFRESH_MS = 30000;
const CORRIDOR = [[-118.52, 33.98], [-118.22, 34.09]];
const AGENCY_COLOUR = { metro: "#e4572e", bbb: "#1f5fbf", ccb: "#2a9d8f", ladot: "#8e44ad" };
const AGENCY_NAME = { metro: "LA Metro", bbb: "Big Blue Bus", ccb: "Culver CityBus", ladot: "LADOT" };
// Absolute km/h ramp for roads and detectors. Step 6 replaces it with a ratio to baseline.
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
map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");

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
  for (const [name, url] of Object.entries(LIVE)) {
    if (name !== "status" && name !== "segments") map.addSource(name, { type: "geojson", data: url });
  }

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

// Live segment speeds join the road tiles by segment_id through feature state.
let liveIds = new Set();
async function refreshSegments() {
  const r = await fetch(LIVE.segments + "?t=" + Date.now(), { cache: "no-store" });
  const fc = await r.json();
  const next = new Set();
  for (const f of fc.features) {
    const p = f.properties;
    next.add(p.segment_id);
    map.setFeatureState({ source: "corridor", sourceLayer: "segments", id: p.segment_id },
      { speed: p.speed_kmh, src: p.source, bus: p.speed_bus_kmh, vds: p.speed_vds_kmh, n_bus: p.n_bus, n_vds: p.n_vds, age: p.age_s });
  }
  for (const id of liveIds) {
    if (!next.has(id)) map.removeFeatureState({ source: "corridor", sourceLayer: "segments", id });
  }
  liveIds = next;
  return { count: fc.features.length, generated: fc.generated };
}

async function refreshStatus() {
  const r = await fetch(LIVE.status + "?t=" + Date.now(), { cache: "no-store" });
  const s = await r.json();
  const rows = [];
  const gen = Date.parse(s.generated);
  const age = Math.round((Date.now() - gen) / 1000);
  rows.push(`<tr><td>snapshot</td><td>${s.generated.replace("T", " ").slice(0, 19)} UTC, <span class="${age > 120 ? "warn" : "ok"}">${age} s old</span>, cycle ${s.cycle}</td></tr>`);
  for (const [name, f] of Object.entries(s.feeds)) {
    const label = { bbb: "Big Blue Bus", ccb: "Culver CityBus", ladot: "LADOT", metro_ws: "LA Metro (WebSocket)" }[name] || name;
    const err = f.errors ? `<span class="warn">${f.errors} errors</span>` : `<span class="ok">ok</span>`;
    rows.push(`<tr><td>${label}</td><td>${f.in_corridor ?? 0} of ${f.vehicles ?? 0} vehicles in the corridor, ${err}</td></tr>`);
  }
  for (const [name, src] of Object.entries(s.traffic_sources || {})) {
    const label = { atms: "Caltrans detectors (ATMS)", chp: "CHP incidents", lcs: "Caltrans closures", cms: "Caltrans signs" }[name] || name;
    const err = src.errors ? `<span class="warn">${src.errors} errors</span>` : `<span class="ok">ok</span>`;
    const extra = name === "atms" && s.traffic?.atms
      ? `, ${s.traffic.atms.corridor_stations_with_speed} corridor stations with a speed. <span class="warn">Feed deprecated by Caltrans, still live.</span>` : "";
    const ev = name !== "atms" && s.traffic?.events ? `, ${s.traffic.events[{ chp: "incidents", lcs: "closures", cms: "signs" }[name]] ?? ""} in the corridor` : "";
    rows.push(`<tr><td>${label}</td><td>last good copy ${src.age_s ?? "?"} s old, ${err}${extra}${ev}</td></tr>`);
  }
  const pr = s.pairs_by_reason || {};
  rows.push(`<tr><td>bus pairs</td><td>${pr.kept ?? 0} kept of ${Object.values(pr).reduce((a, b) => a + b, 0)} since start; ${s.segments_in_snapshot} segments with a speed in the last 10 min</td></tr>`);
  document.querySelector("#feeds tbody").innerHTML = rows.join("");
}

async function refresh() {
  document.getElementById("clock").textContent = new Date().toISOString().slice(11, 19) + " UTC";
  try {
    for (const name of ["vehicles", "events", "detectors"]) map.getSource(name).setData(LIVE[name] + "?t=" + Date.now());
    await refreshSegments();
    await refreshStatus();
  } catch (err) {
    console.error("refresh failed", err);
    document.querySelector("#feeds tbody").innerHTML = `<tr><td class="warn">live files not reachable: ${err}</td></tr>`;
  }
}

function buildToggles() {
  const groups = { Roads: ["roads"], Vehicles: ["vehicles", "vehicle-labels"], Shapes: ["shapes"], Stops: ["stops"],
    Events: ["closures", "closure-points", "signs", "incidents", "incident-labels"], Detectors: ["detectors"], Cameras: ["cameras", "camera-labels"],
    Buildings: ["buildings-3d"], Hillshade: ["hillshade"] };
  const box = document.getElementById("toggles");
  for (const [label, ids] of Object.entries(groups)) {
    const el = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = true;
    cb.addEventListener("change", () => ids.forEach((id) => map.setLayoutProperty(id, "visibility", cb.checked ? "visible" : "none")));
    el.append(cb, " " + label);
    box.append(el);
  }
  const terrain = document.createElement("label");
  const tcb = document.createElement("input");
  tcb.type = "checkbox"; tcb.checked = true;
  tcb.addEventListener("change", () => map.setTerrain(tcb.checked ? { source: "dem", exaggeration: 1.3 } : null));
  terrain.append(tcb, " Terrain");
  box.append(terrain);
  const btn = document.getElementById("pitch");
  btn.addEventListener("click", () => {
    const flat = map.getPitch() > 1;
    map.easeTo({ pitch: flat ? 0 : 55, bearing: flat ? 0 : -15, duration: 800 });
    btn.textContent = flat ? "Go 3D" : "Go flat";
  });
}

function buildLegend() {
  const el = document.getElementById("legend");
  el.innerHTML = SPEED_STOPS.map(([v, c]) => `<span style="background:${c}">${v}</span>`).join("") + `<span style="background:#bdbdbd;color:#333;text-shadow:none">none</span>`;
  const agencies = Object.entries(AGENCY_NAME).map(([k, n]) => `<span class="swatch" style="background:${AGENCY_COLOUR[k]}"></span>${n}`).join(" &nbsp; ");
  el.insertAdjacentHTML("afterend", `<p class="small">${agencies}</p>`);
}

function fmt(v, unit = "") { return v === null || v === undefined ? "–" : `${v}${unit}`; }
function ageText(s) { return s === null || s === undefined ? "–" : s < 120 ? `${s} s` : `${Math.round(s / 60)} min`; }

function wirePopups() {
  const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });
  map.on("mousemove", "roads", (e) => {
    const f = e.features[0];
    const p = f.properties, st = f.state || {};
    map.getCanvas().style.cursor = "crosshair";
    const src = st.src ? { bus: "buses", vds: "detector", both: "buses and detector" }[st.src] : "no reading in the last 10 min";
    hover.setLngLat(e.lngLat).setHTML(
      `<b>${p.name || "(unnamed)"}</b> <i>${p.highway}</i><br>segment ${p.segment_id}, ${Math.round(p.length_m)} m, osm ${p.osm_id}${p.maxspeed ? ", limit " + p.maxspeed : ""}<br>` +
      (st.src ? `<b>${fmt(st.speed, " km/h")}</b> from ${src}<br>bus ${fmt(st.bus, " km/h")} (${st.n_bus ?? 0} obs), detector ${fmt(st.vds, " km/h")} (${st.n_vds ?? 0} obs), newest ${ageText(st.age)} ago` : src)
    ).addTo(map);
  });
  map.on("mouseleave", "roads", () => { map.getCanvas().style.cursor = ""; hover.remove(); });

  const click = (layer, html) => map.on("click", layer, (e) => {
    const f = e.features[0];
    const coords = f.geometry.type === "Point" ? f.geometry.coordinates.slice() : e.lngLat;
    new maplibregl.Popup({ offset: 10 }).setLngLat(coords).setHTML(html(f.properties)).addTo(map);
    e.originalEvent.stopPropagation();
  });
  for (const layer of ["vehicles", "vehicle-labels"]) click(layer, (p) =>
    `<b>${AGENCY_NAME[p.agency] || p.agency} ${p.route || ""}</b><br>vehicle ${p.vehicle_id}${p.trip_id ? ", trip " + p.trip_id : ""}<br>` +
    `last bus speed ${fmt(p.speed_kmh, " km/h")}, bearing ${fmt(p.bearing, "°")}<br>position ${ageText(p.age_s)} old`);
  click("incidents", (p) => `<b>CHP incident</b> ${p.time?.slice(11, 16)} UTC<br>${p.text}<br><small>${(JSON.parse(p.details || "[]")).join("<br>")}</small>`);
  for (const layer of ["closures", "closure-points"]) click(layer, (p) =>
    `<b>Caltrans closure</b>${p.in_effect ? " (in effect, 10-97)" : ""}<br>${p.text}<br>from ${p.time?.replace("T", " ").slice(0, 16)} to ${p.end ? p.end.replace("T", " ").slice(0, 16) : "indefinite"} UTC`);
  click("signs", (p) => `<b>Sign ${p.route} ${p.direction}</b>, ${p.location}<br>${p.text ? "<code>" + p.text + "</code>" : "<i>" + (p.display || "blank") + "</i>"}`);
  click("detectors", (p) => `<b>Caltrans detector ${p.station_id}</b> ${p.route} ${p.direction} pm ${fmt(p.postmile)}<br>${p.name}<br>` +
    (p.speed_kmh ? `<b>${p.speed_kmh} km/h</b> (${p.speed_mph} mph), ${p.volume_30s} vehicles per 30 s, code ${p.colour} ${p.colour_label}, ${ageText(p.age_s)} old` : `no data (code ${p.colour ?? "–"}), segment ${p.segment_id ?? "none"}`));
  click("cameras", (p) => `<b>Caltrans camera ${p.cctv_id}</b> ${p.route} ${p.direction}<br>${p.name}<br>` +
    (p.image_url ? `<img src="${p.image_url}?t=${Date.now()}" alt="camera still" loading="lazy"><small>still from Caltrans, refreshed every ${p.image_minutes} min</small>` : "no image URL"));
  click("stops", (p) => `<b>${p.stop_name}</b><br>${p.agency_name}, stop ${p.stop_id}`);
  click("shapes", (p) => `<b>${p.agency_name} ${p.route_short_name || p.route_id}</b> ${p.route_long_name}<br>shape ${p.shape_id}, direction ${p.direction_id}, ${p.trips} trips`);
  for (const layer of ["vehicles", "incidents", "closures", "signs", "detectors", "cameras", "stops"]) {
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
