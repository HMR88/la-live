"""Write data/base into an orphan branch `data` without touching the checkout or the current branch.

GitHub Pages serves no Git LFS and the base layers are gitignored on main, so the unattended workflow checks
out this branch into data/base. The branch is always one commit; rerun after a prep rebuild and push with
`git push --force origin data`. Uses a temporary index, write-tree and commit-tree, so nothing in the working
tree or in main's index changes.

Run: python prep/data_branch.py [--branch data]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["SOURCE.json", "shape_index.json", "segments.gpkg", "shapes.gpkg", "stops.gpkg", "detectors.gpkg", "cameras.gpkg",
         "gtfs_metro_bus.gpkg", "gtfs_bigbluebus.gpkg", "gtfs_culvercity.gpkg", "gtfs_ladot.gpkg",
         "corridor.pmtiles", "buildings.pmtiles"]


def git(*args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, env={**os.environ, **(env or {})}, check=True, capture_output=True, text=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", default="data")
    args = ap.parse_args()
    base = ROOT / "data" / "base"
    missing = [f for f in FILES if not (base / f).exists()]
    if missing:
        raise SystemExit(f"missing under data/base: {missing}")
    source = json.loads((base / "SOURCE.json").read_text())
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git("add", "-f", "--", *[f"data/base/{f}" for f in FILES], env=env)
        tree = git("write-tree", env=env)
    msg = f"Base layers for the unattended workflow\n\nFrom qgis-mcp run {source.get('run_id', '?')}, copied by prep/copy_base.py. Files: {', '.join(FILES)}.\n"
    commit = git("commit-tree", tree, "-m", msg)
    git("update-ref", f"refs/heads/{args.branch}", commit)
    total = sum((base / f).stat().st_size for f in FILES)
    print(f"{args.branch} -> {commit[:12]} tree {tree[:12]}, {len(FILES)} files, {total / 1e6:.1f} MB. Push with: git push --force origin {args.branch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
