import json
import subprocess
from pathlib import Path

import httpx
import pytest

from poller.feeds import GtfsRtFeed
from poller.publish_live import build_commit, publish_once, wait_for_files
from prep.publish import assemble


def test_feed_retries_with_backoff_then_succeeds():
    calls = {"n": 0}
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(200, content=b"")  # an empty FeedMessage parses to no vehicles

    feed = GtfsRtFeed("bbb", "bbb", "https://example.test/vehicles", client=httpx.Client(transport=httpx.MockTransport(handler)),
                      backoff_s=(1.0, 3.0), sleep=waits.append)
    assert feed.fetch() == []
    assert calls["n"] == 3 and waits == [1.0, 3.0] and feed.retries == 2


def test_feed_gives_up_after_the_last_attempt():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    feed = GtfsRtFeed("bbb", "bbb", "https://example.test/vehicles", client=httpx.Client(transport=httpx.MockTransport(handler)),
                      backoff_s=(0.0,), sleep=lambda s: None)
    with pytest.raises(httpx.ConnectTimeout):
        feed.fetch()
    assert feed.retries == 1


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _live_files(live: Path, stamp: str) -> None:
    live.mkdir(exist_ok=True)
    (live / "status.json").write_text(json.dumps({"generated": stamp}))
    (live / "segments.json").write_text(json.dumps({"generated": stamp, "rows": [{"segment_id": 1, "speed_kmh": 30.0}]}))
    (live / "vehicles.geojson").write_text('{"type":"FeatureCollection","features":[]}')


def test_publish_live_replaces_one_commit_on_the_remote(tmp_path: Path):
    repo, remote, live = tmp_path / "repo", tmp_path / "remote.git", tmp_path / "live"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README").write_text("x")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "README")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _live_files(live, "2026-11-08T07:00:00Z")
    first = publish_once(repo, live, str(remote), "live")
    assert first.pushed and set(first.files) == {"status.json", "segments.json", "vehicles.geojson"}
    assert _git(remote, "rev-list", "--count", "live") == "1"
    assert _git(remote, "ls-tree", "--name-only", "live").split() == ["segments.json", "status.json", "vehicles.geojson"]
    assert json.loads(_git(remote, "show", "live:status.json")) == {"generated": "2026-11-08T07:00:00Z"}

    _live_files(live, "2026-11-08T07:05:00Z")
    (live / "cameras.geojson").write_text('{"type":"FeatureCollection","features":[]}')
    second = publish_once(repo, live, str(remote), "live")
    assert second.commit != first.commit
    assert _git(remote, "rev-list", "--count", "live") == "1", "the branch is always one commit"
    assert "cameras.geojson" in _git(remote, "ls-tree", "--name-only", "live")
    assert json.loads(_git(remote, "show", "live:status.json")) == {"generated": "2026-11-08T07:05:00Z"}
    # the working tree and index of the checkout are untouched
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"

    dry = publish_once(repo, live, str(remote), "live", dry_run=True)
    assert not dry.pushed and _git(remote, "rev-parse", "live") == second.commit


def test_publish_live_refuses_without_the_required_files(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (tmp_path / "live").mkdir()
    (tmp_path / "live" / "status.json").write_text("{}")
    with pytest.raises(FileNotFoundError):
        build_commit(repo, tmp_path / "live", "m")


def test_pages_assembly_without_live_files_when_optional(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "viewer").mkdir(parents=True)
    (repo / "viewer" / "index.html").write_text("<html>")
    (repo / "data" / "base").mkdir(parents=True)
    for t in ["corridor.pmtiles", "buildings.pmtiles"]:
        (repo / "data" / "base" / t).write_bytes(b"PMTiles")
    with pytest.raises(FileNotFoundError):
        assemble(repo, tmp_path / "site")
    sizes = assemble(repo, tmp_path / "site", optional_live=True)
    assert sizes["live"] == 0 and (tmp_path / "site" / "live").is_dir() and (tmp_path / "site" / "viewer" / "index.html").exists()


def test_segments_json_is_written_beside_the_geojson(tmp_path: Path):
    from poller.snapshot import WINDOW_S, write_segments
    from tests.test_speed import straight_world

    base, _ = straight_world()
    n = write_segments(tmp_path / "segments.geojson", base, [(1, "bus", 25.0, 3, 1_794_000_000), (2, "vds", 60.0, 1, 1_794_000_010)], now=1_794_000_030)
    assert n == 2
    gj = json.loads((tmp_path / "segments.geojson").read_text())
    compact = json.loads((tmp_path / "segments.json").read_text())
    assert compact["window_s"] == WINDOW_S == gj["window_s"] and compact["generated"] == gj["generated"]
    assert compact["rows"] == [f["properties"] for f in gj["features"]]
    assert compact["rows"][0]["segment_id"] == 1 and compact["rows"][0]["speed_kmh"] == 25.0 and compact["rows"][0]["source"] == "bus"
    assert compact["rows"][1]["source"] == "vds" and compact["rows"][1]["age_s"] == 20
    assert len((tmp_path / "segments.json").read_bytes()) < len((tmp_path / "segments.geojson").read_bytes())


def test_wait_for_files_returns_at_once_when_present_and_false_on_timeout(tmp_path: Path):
    live = tmp_path / "live"
    assert wait_for_files(live, timeout_s=0, sleep=0) is False
    _live_files(live, "2026-11-08T07:00:00Z")
    assert wait_for_files(live, timeout_s=0, sleep=0) is True
