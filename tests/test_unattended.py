import json
import sqlite3
from pathlib import Path

from poller.__main__ import SEED_KEEP_S, run_info
from poller.model import Observation, PairResult, SegmentSpeed
from poller.store import Store
from prep.publish import assemble


def test_prune_keeps_the_seed_and_the_window(tmp_path: Path):
    store = Store(tmp_path / "h.sqlite")
    now = 1_794_000_000
    old, recent = now - 4 * 86400, now - 60
    store.write(
        [Observation("bbb", "1", "t", "r", old, -118.4, 34.0, None), Observation("bbb", "1", "t", "r", recent, -118.4, 34.0, None)],
        [PairResult("bbb", "1", old - 30, old, "kept", "s", 20.0, 100.0), PairResult("bbb", "1", recent - 30, recent, "kept", "s", 20.0, 100.0)],
        [SegmentSpeed(old, 1, "bus", 20.0, 1.0, "1", "bbb"), SegmentSpeed(now - 2 * 86400, 1, "bus", 22.0, 1.0, "1", "bbb"), SegmentSpeed(recent, 1, "bus", 24.0, 1.0, "1", "bbb")])
    deleted = store.prune(now, SEED_KEEP_S, SEED_KEEP_S, 3 * 86400)
    assert deleted == {"positions": 1, "pairs": 1, "observations": 1}
    assert [o.ts for o in store.latest_positions()] == [recent]
    assert store.conn.execute("SELECT COUNT(*) FROM observations").fetchone() == (2,)
    store.close()
    assert sqlite3.connect(tmp_path / "h.sqlite").execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_run_info_local_and_actions():
    local = run_info("local", 30, 30, 20, 1_794_000_000, {})
    assert local["source"] == "local" and local["cadence_s"] == 30 and local["run_url"] is None and local["started"] == "2026-11-06T21:20:00+00:00"
    env = {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "HMR88/la-live", "GITHUB_RUN_ID": "42", "GITHUB_SHA": "abc"}
    gh = run_info("github-actions", 30, 300, 3.5, 1_794_000_000, env)
    assert gh["run_url"] == "https://github.com/HMR88/la-live/actions/runs/42" and gh["run_id"] == "42" and gh["sha"] == "abc" and gh["cadence_s"] == 300


def test_publish_assembles_the_pages_layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "viewer").mkdir(parents=True)
    (repo / "viewer" / "index.html").write_text("<html>")
    (repo / "viewer" / "app.js").write_text("// js")
    (repo / "live").mkdir()
    for f in ["vehicles.geojson", "segments.geojson", "events.geojson", "detectors.geojson"]:
        (repo / "live" / f).write_text('{"type":"FeatureCollection","features":[]}')
    (repo / "live" / "segments.json").write_text('{"rows":[]}')
    (repo / "live" / "status.json").write_text(json.dumps({"run": {"source": "github-actions"}}))
    (repo / "data" / "base").mkdir(parents=True)
    (repo / "data" / "base" / "corridor.pmtiles").write_bytes(b"PMTiles" + bytes(100))
    (repo / "data" / "base" / "buildings.pmtiles").write_bytes(b"PMTiles" + bytes(50))
    (repo / "data" / "base" / "segments.gpkg").write_bytes(bytes(10))  # not published
    out = tmp_path / "site"
    sizes = assemble(repo, out)
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()) == [
        ".nojekyll", "data/base/buildings.pmtiles", "data/base/corridor.pmtiles", "index.html",
        "live/detectors.geojson", "live/events.geojson", "live/segments.geojson", "live/segments.json", "live/status.json", "live/vehicles.geojson",
        "viewer/app.js", "viewer/index.html"]
    assert sizes["tiles"] == 107 + 57 and sizes["total"] == sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    assert 'url=viewer/' in (out / "index.html").read_text()
    (repo / "live" / "status.json").unlink()
    try:
        assemble(repo, out)
    except FileNotFoundError as e:
        assert "status.json" in str(e)
    else:
        raise AssertionError("missing status.json must refuse to publish")
