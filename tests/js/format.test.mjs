import test from "node:test";
import assert from "node:assert/strict";
import {
  speedParts, durationText, relativeAge, pacificTime, pacificDateTime, roadClassName, parseLimitMph,
  limitComparison, sourceText, headingText, freshness, modeLabel, sourceLabel, plural,
} from "../../viewer/format.js";

test("speeds are shown in mph first with the km/h value kept", () => {
  assert.deepEqual(speedParts(40), { mph: 25, kmh: 40 });
  assert.deepEqual(speedParts(104.6), { mph: 65, kmh: 105 });
  assert.equal(speedParts(null), null);
});

test("durations and relative ages read like speech", () => {
  assert.equal(durationText(5), "5 s");
  assert.equal(durationText(119), "2 min");
  assert.equal(durationText(180), "3 min");
  assert.equal(durationText(7500), "2 h 5 min");
  assert.equal(durationText(3600), "1 h");
  assert.equal(durationText(90000), "1 d");
  assert.equal(relativeAge(4), "just now");
  assert.equal(relativeAge(40), "40 s ago");
  assert.equal(relativeAge(null), "unknown age");
});

test("times are Pacific with the zone named", () => {
  assert.equal(pacificTime("2026-11-06T23:19:31+00:00"), "3:19 PM PST");
  assert.equal(pacificTime(1794000000), "1:20 PM PST");
  assert.equal(pacificDateTime("2026-07-04T19:05:00Z"), "Jul 4, 12:05 PM PDT");
  assert.equal(pacificTime(""), "");
});

test("road classes are plain words", () => {
  assert.equal(roadClassName("motorway"), "Freeway");
  assert.equal(roadClassName("primary"), "Primary road");
  assert.equal(roadClassName("residential"), "Residential street");
  assert.equal(roadClassName("motorway_link"), "Freeway ramp");
  assert.equal(roadClassName("bus_guideway"), "Bus guideway");
  assert.equal(roadClassName(undefined), "Road");
});

test("posted limits parse from OSM maxspeed", () => {
  assert.equal(parseLimitMph("40 mph"), 40);
  assert.equal(parseLimitMph("65"), 40);  // a bare OSM number is km/h
  assert.equal(parseLimitMph("signals"), null);
  assert.equal(parseLimitMph(null), null);
});

test("the limit comparison is worded by ratio", () => {
  assert.equal(limitComparison(64.4, 40), "traffic at or above the posted limit");   // 40 mph on 40
  assert.equal(limitComparison(51, 40), "traffic a little under the posted limit");   // 32 mph on 40
  assert.equal(limitComparison(32, 40), "traffic at about half the posted limit");    // 20 mph on 40
  assert.equal(limitComparison(10, 40), "traffic well under the posted limit");       // 6 mph on 40
  assert.equal(limitComparison(null, 40), null);
  assert.equal(limitComparison(30, null), null);
});

test("sources, headings, plurals", () => {
  assert.equal(sourceText("bus", 3, 0), "from 3 buses");
  assert.equal(sourceText("vds", 0, 1), "from 1 detector");
  assert.equal(sourceText("both", 2, 1), "from 2 buses and 1 detector");
  assert.equal(sourceText(undefined, 0, 0), "");
  assert.equal(headingText(0), "heading N");
  assert.equal(headingText(44), "heading NE");
  assert.equal(headingText(359), "heading N");
  assert.equal(headingText(null), null);
  assert.equal(plural(1, "incident"), "1 incident");
  assert.equal(plural(2, "incident"), "2 incidents");
});

test("freshness depends on the expected cadence", () => {
  assert.equal(freshness(30), "fresh");
  assert.equal(freshness(300), "aging");
  assert.equal(freshness(601), "stale");
  assert.equal(freshness(null), "unknown");
  assert.equal(freshness(240, 300), "fresh");   // a 4-minute-old snapshot from a 5-minute channel is normal
  assert.equal(freshness(400, 300), "aging");
  assert.equal(freshness(901, 300), "stale");
});

test("the mode pill and the data source words", () => {
  assert.deepEqual(modeLabel({ source: "local", interval_s: 30, cadence_s: 30 }), { kind: "live", text: "Live, refreshes every 30 s", cadence: 30 });
  assert.deepEqual(modeLabel({ source: "github-actions", interval_s: 30, cadence_s: 300 }), { kind: "channel", text: "Live via GitHub Actions, published every 5 min", cadence: 300 });
  assert.deepEqual(modeLabel({ source: "local", interval_s: 30, cadence_s: 300 }), { kind: "scheduled", text: "Scheduled, new snapshot about every 5 min", cadence: 300 });
  assert.deepEqual(modeLabel(undefined, 30), { kind: "live", text: "Live, refreshes every 30 s", cadence: 30 });
  assert.equal(sourceLabel("channel"), "via the live branch");
  assert.equal(sourceLabel("site"), "from this site's copy");
  assert.equal(sourceLabel("local"), "");
});

import { rampColour } from "../../viewer/format.js";

test("ramp colours interpolate and clamp", () => {
  const stops = [[0, "#000000"], [10, "#0000ff"], [20, "#ffffff"]];
  assert.equal(rampColour(-5, stops), "#000000");
  assert.equal(rampColour(5, stops), "#000080");
  assert.equal(rampColour(10, stops), "#0000ff");
  assert.equal(rampColour(15, stops), "#8080ff");
  assert.equal(rampColour(99, stops), "#ffffff");
});
