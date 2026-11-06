"""Copy the corridor base layers from a qgis-mcp run into data/base/.

The poller depends on the layers that step 2 of docs/plan.md produced in the
QGIS repository. This script makes that dependency explicit: it copies the
files and writes data/base/SOURCE.json with the run id, each file's size and
sha256, so a rerun of the prep can be detected. The QGIS repository is only
read.

Usage: .venv/bin/python prep/copy_base.py <run directory>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

FILES = {
    "steps/segments.gpkg": "segments.gpkg",
    "steps/shapes.gpkg": "shapes.gpkg",
    "steps/stops.gpkg": "stops.gpkg",
    "steps/detectors.gpkg": "detectors.gpkg",
    "steps/cameras.gpkg": "cameras.gpkg",
    "steps/corridor.pmtiles": "corridor.pmtiles",
    "prep/gtfs_metro_bus__gtfs.gpkg": "gtfs_metro_bus.gpkg",
    "prep/gtfs_bigbluebus__gtfs.gpkg": "gtfs_bigbluebus.gpkg",
    "prep/gtfs_culvercity__gtfs.gpkg": "gtfs_culvercity.gpkg",
    "prep/gtfs_ladot__gtfs.gpkg": "gtfs_ladot.gpkg",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--out", type=Path, default=Path("data/base"))
    args = ap.parse_args()
    run_dir: Path = args.run_dir.resolve()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    record: dict[str, object] = {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "copied_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": {},
    }
    files: dict[str, dict[str, object]] = {}
    for src_rel, dst_name in FILES.items():
        src = run_dir / src_rel
        if not src.exists():
            raise SystemExit(f"missing in run: {src}")
        dst = out / dst_name
        shutil.copyfile(src, dst)
        files[dst_name] = {"source": src_rel, "bytes": dst.stat().st_size, "sha256": sha256(dst)}
        print(f"{dst_name}: {files[dst_name]['bytes']} bytes")
    record["files"] = files
    (out / "SOURCE.json").write_text(json.dumps(record, indent=1))
    print(f"wrote {out / 'SOURCE.json'} for run {run_dir.name}")


if __name__ == "__main__":
    main()
