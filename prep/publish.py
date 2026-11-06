"""Assemble the static site for GitHub Pages into a staging folder.

The site is the viewer, the live snapshots and the PMTiles the viewer reads, laid out exactly as the viewer
resolves them from its own URL (BASE = ".." of viewer/): <out>/viewer/, <out>/live/, <out>/data/base/*.pmtiles,
a root index.html forwarding to viewer/, and .nojekyll so Pages serves the tree as is.

Run: python prep/publish.py --out site [--repo .]
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

LIVE_FILES = ["vehicles.geojson", "segments.geojson", "events.geojson", "detectors.geojson", "status.json", "cameras.geojson"]
REQUIRED_LIVE = ["vehicles.geojson", "segments.geojson", "status.json"]
TILES = ["corridor.pmtiles", "buildings.pmtiles"]
ROOT_INDEX = """<!doctype html><meta charset="utf-8"><title>la-live</title>
<meta http-equiv="refresh" content="0; url=viewer/"><a href="viewer/">la-live viewer</a>
"""


def assemble(repo: Path, out: Path) -> dict[str, int]:
    """Copy the site into out (emptied first). Returns bytes per part. Missing required inputs raise."""
    missing = [f for f in REQUIRED_LIVE if not (repo / "live" / f).exists()] + [t for t in TILES if not (repo / "data" / "base" / t).exists()]
    if missing:
        raise FileNotFoundError(f"cannot publish without {missing}")
    if out.exists():
        shutil.rmtree(out)
    sizes: dict[str, int] = {}
    shutil.copytree(repo / "viewer", out / "viewer")
    sizes["viewer"] = sum(p.stat().st_size for p in (out / "viewer").rglob("*") if p.is_file())
    (out / "live").mkdir()
    sizes["live"] = 0
    for f in LIVE_FILES:
        src = repo / "live" / f
        if src.exists():
            shutil.copy2(src, out / "live" / f)
            sizes["live"] += src.stat().st_size
    (out / "data" / "base").mkdir(parents=True)
    sizes["tiles"] = 0
    for t in TILES:
        shutil.copy2(repo / "data" / "base" / t, out / "data" / "base" / t)
        sizes["tiles"] += (repo / "data" / "base" / t).stat().st_size
    (out / "index.html").write_text(ROOT_INDEX)
    (out / ".nojekyll").write_text("")
    sizes["total"] = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    return sizes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    sizes = assemble(args.repo, args.out)
    print(", ".join(f"{k} {v / 1e6:.1f} MB" for k, v in sizes.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
