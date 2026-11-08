// Pure formatting rules for the viewer: units, ages, times, wording. No DOM, no map, so node can test them
// (node --test tests/js). The data stays in km/h and UTC; Los Angeles readers see mph and Pacific time first.

const MPH_PER_KMH = 0.621371;

export function kmhToMph(kmh) {
  return kmh * MPH_PER_KMH;
}

/** {mph, kmh} rounded for display, or null when there is no reading. */
export function speedParts(kmh) {
  if (kmh === null || kmh === undefined || Number.isNaN(kmh)) return null;
  return { mph: Math.round(kmhToMph(kmh)), kmh: Math.round(kmh) };
}

/** "40 s", "3 min", "2 h 5 min", "3 d"; null for no value. */
export function durationText(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return null;
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60), rem = m % 60;
  if (h < 24) return rem ? `${h} h ${rem} min` : `${h} h`;
  return `${Math.round(h / 24)} d`;
}

/** "just now", "40 s ago", "3 min ago". */
export function relativeAge(seconds) {
  if (seconds === null || seconds === undefined) return "unknown age";
  if (seconds < 10) return "just now";
  return `${durationText(seconds)} ago`;
}

const PACIFIC = "America/Los_Angeles";

/** "3:19 PM PST" for an ISO string or epoch seconds; "" for nothing. */
export function pacificTime(value) {
  const d = toDate(value);
  if (!d) return "";
  return new Intl.DateTimeFormat("en-US", { timeZone: PACIFIC, hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(d);
}

/** "Nov 6, 3:19 PM PST". */
export function pacificDateTime(value) {
  const d = toDate(value);
  if (!d) return "";
  return new Intl.DateTimeFormat("en-US", { timeZone: PACIFIC, month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(d);
}

function toDate(value) {
  if (value === null || value === undefined || value === "") return null;
  const d = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

const ROAD_CLASS = {
  motorway: "Freeway", motorway_link: "Freeway ramp", trunk: "Highway", trunk_link: "Highway ramp",
  primary: "Primary road", primary_link: "Primary road ramp", secondary: "Secondary road", secondary_link: "Secondary road ramp",
  tertiary: "Tertiary road", tertiary_link: "Tertiary road ramp", residential: "Residential street", unclassified: "Minor road",
  living_street: "Living street", service: "Service road", busway: "Busway", pedestrian: "Pedestrian street",
};

/** OSM highway tag in plain words. Unknown tags are shown as words, not left as tags. */
export function roadClassName(highway) {
  if (!highway) return "Road";
  return ROAD_CLASS[highway] || highway.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** Posted limit in mph from an OSM maxspeed value: "40 mph" -> 40, "65" -> 40 (bare numbers are km/h in OSM), else null. */
export function parseLimitMph(maxspeed) {
  if (!maxspeed) return null;
  const m = /^\s*(\d+(?:\.\d+)?)\s*(mph)?\s*$/i.exec(String(maxspeed));
  if (!m) return null;
  const n = parseFloat(m[1]);
  return Math.round(m[2] ? n : kmhToMph(n));
}

/** Plain words comparing a measured speed with the posted limit, or null without both. */
export function limitComparison(speedKmh, limitMph) {
  if (speedKmh === null || speedKmh === undefined || !limitMph) return null;
  const ratio = kmhToMph(speedKmh) / limitMph;
  if (ratio >= 0.9) return "traffic at or above the posted limit";
  if (ratio >= 0.7) return "traffic a little under the posted limit";
  if (ratio >= 0.4) return "traffic at about half the posted limit";
  return "traffic well under the posted limit";
}

/** "from 3 buses", "from 1 detector", "from 2 buses and 1 detector". */
export function sourceText(src, nBus, nVds) {
  const parts = [];
  if ((src === "bus" || src === "both") && nBus) parts.push(plural(nBus, "bus", "buses"));
  if ((src === "vds" || src === "both") && nVds) parts.push(plural(nVds, "detector"));
  return parts.length ? `from ${parts.join(" and ")}` : "";
}

export function plural(n, word, words = word + "s") {
  return `${n} ${n === 1 ? word : words}`;
}

const POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

/** "heading NE" from degrees clockwise from north; null without a bearing. */
export function headingText(bearing) {
  if (bearing === null || bearing === undefined || Number.isNaN(bearing)) return null;
  const i = Math.round((((bearing % 360) + 360) % 360) / 45) % 8;
  return `heading ${POINTS[i]}`;
}

/** Snapshot freshness: green under 2 min, amber under 10, red beyond; grey without a value. */
export function freshness(ageSeconds) {
  if (ageSeconds === null || ageSeconds === undefined) return "unknown";
  if (ageSeconds < 120) return "fresh";
  if (ageSeconds < 600) return "aging";
  return "stale";
}

/** The status pill's words from status.json's run block (or its absence). */
export function modeLabel(run, intervalS) {
  const interval = run?.interval_s ?? intervalS ?? 30;
  const cadence = run?.cadence_s ?? interval;
  if (run?.source === "github-actions" || cadence > interval) {
    return { kind: "scheduled", text: `Scheduled, new snapshot about every ${Math.round(cadence / 60)} min` };
  }
  return { kind: "live", text: `Live, refreshes every ${Math.round(interval)} s` };
}

/** Colour on a [[value, "#rrggbb"], ...] ramp by linear interpolation, clamped at the ends. */
export function rampColour(value, stops) {
  if (value <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    const [v1, c1] = stops[i];
    if (value <= v1) {
      const [v0, c0] = stops[i - 1];
      const t = (value - v0) / (v1 - v0);
      const a = hex(c0), b = hex(c1);
      return "#" + a.map((x, k) => Math.round(x + (b[k] - x) * t).toString(16).padStart(2, "0")).join("");
    }
  }
  return stops[stops.length - 1][1];
}

function hex(c) {
  return [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
}
