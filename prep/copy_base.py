"""Copy the corridor base layers from a qgis-mcp run into data/base/.

The poller depends on the layers that step 2 of docs/plan.md produced in the
QGIS repository. This script makes that dependency explicit: it copies the
files and writes data/base/SOURCE.json with the run id, each file's size and
sha256, so a rerun of the prep can be detected. The QGIS repository is only
read.

It also writes data/base/buildings.pmtiles from the run's OSM buildings
layer with the GDAL bundled in QGIS.app, adding a numeric height_m (height
in metres, feet converted; else levels times 3.5; else 8) so the viewer can
extrude without client logic.

Usage: .venv/bin/python prep/copy_base.py <run directory> [--only-buildings]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
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


QGIS = Path("/Applications/QGIS.app/Contents")
OGR2OGR = QGIS / "MacOS" / "ogr2ogr"
GDAL_ENV = {"PROJ_DATA": str(QGIS / "Resources/qgis/proj"), "GDAL_DATA": str(QGIS / "Resources/qgis/gdal")}

# OSM height strings seen in the corridor: "4.4" (metres), "18 m", "24.46'" and
# "241 ft" (feet), "74.3;241 ft" (two values, the first wins through CAST).
BUILDINGS_SQL = """
SELECT geom, height, levels,
  ROUND(CASE
    WHEN height IS NOT NULL AND CAST(height AS REAL) > 0 THEN
      CASE WHEN height LIKE '%ft%' OR height LIKE '%''%' THEN CAST(height AS REAL) * 0.3048
           ELSE CAST(height AS REAL) END
    WHEN levels IS NOT NULL AND CAST(levels AS REAL) > 0 THEN CAST(levels AS REAL) * 3.5
    ELSE 8 END, 1) AS height_m
FROM buildings
"""


def build_buildings(run_dir: Path, out: Path) -> dict[str, object]:
    src = run_dir / "prep" / "corridor_osm__osm.gpkg"
    dst = out / "buildings.pmtiles"
    if dst.exists():
        dst.unlink()
    cmd = [str(OGR2OGR), "-f", "PMTiles", str(dst), str(src), "-dialect", "SQLite", "-sql", BUILDINGS_SQL,
           "-nln", "buildings", "-dsco", "MINZOOM=13", "-dsco", "MAXZOOM=15", "-dsco", "NAME=buildings"]
    t0 = time.time()
    subprocess.run(cmd, check=True, env={**os.environ, **GDAL_ENV})
    info = {"source": "prep/corridor_osm__osm.gpkg:buildings", "bytes": dst.stat().st_size, "sha256": sha256(dst),
            "seconds": round(time.time() - t0, 1), "sql": " ".join(BUILDINGS_SQL.split())}
    print(f"buildings.pmtiles: {info['bytes']} bytes in {info['seconds']} s")
    return info


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
    ap.add_argument("--only-buildings", action="store_true", help="rebuild buildings.pmtiles, keep the copied files")
    args = ap.parse_args()
    run_dir: Path = args.run_dir.resolve()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    source_path = out / "SOURCE.json"
    if args.only_buildings:
        record_old = json.loads(source_path.read_text())
        files_old = dict(record_old["files"])
        files_old["buildings.pmtiles"] = build_buildings(run_dir, out)
        record_old["files"] = files_old
        source_path.write_text(json.dumps(record_old, indent=1))
        return
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
    files["buildings.pmtiles"] = build_buildings(run_dir, out)
    record["files"] = files
    source_path.write_text(json.dumps(record, indent=1))
    print(f"wrote {out / 'SOURCE.json'} for run {run_dir.name}")


if __name__ == "__main__":
    main()
