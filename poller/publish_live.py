"""Publish the live snapshot files to a single-commit orphan branch, force-pushed every few minutes.

The unattended job runs this next to the poller. Each cycle builds one commit holding the files at the
root of the tree, with a temporary index so the checkout's working tree and index are never touched, and
force-pushes it to refs/heads/<branch>. The viewer reads the files from raw.githubusercontent.com.
Design and measurements: docs/log/step9.md.

    python -m poller.publish_live --remote origin --branch live --every 300 --while-pid <poller pid>
    python -m poller.publish_live --remote /path/to/bare.git --once          # rehearsal against a local remote
    python -m poller.publish_live --dry-run --once                            # build the commit, push nothing
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["status.json", "segments.json", "vehicles.geojson", "detectors.geojson", "events.geojson", "cameras.geojson"]
REQUIRED = ["status.json", "segments.json", "vehicles.geojson"]
AUTHOR = {"GIT_AUTHOR_NAME": "la-live poll", "GIT_AUTHOR_EMAIL": "poll@users.noreply.github.com",
          "GIT_COMMITTER_NAME": "la-live poll", "GIT_COMMITTER_EMAIL": "poll@users.noreply.github.com"}


@dataclass(frozen=True)
class Published:
    commit: str
    tree: str
    files: tuple[str, ...]
    bytes: int
    pushed: bool


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(["git", *args], cwd=repo, env={**os.environ, **AUTHOR, **(env or {})},
                          check=True, capture_output=True, text=True).stdout.strip()


def build_commit(repo: Path, live_dir: Path, message: str) -> tuple[str, str, tuple[str, ...], int]:
    """A commit whose tree holds the live files at its root. Returns (commit, tree, files, bytes)."""
    present = [f for f in FILES if (live_dir / f).exists()]
    missing = [f for f in REQUIRED if f not in present]
    if missing:
        raise FileNotFoundError(f"live files missing: {missing}")
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        for f in present:
            blob = git(repo, "hash-object", "-w", "--", str(live_dir / f))
            git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{f}", env=env)
        tree = git(repo, "write-tree", env=env)
    commit = git(repo, "commit-tree", tree, "-m", message)
    return commit, tree, tuple(present), sum((live_dir / f).stat().st_size for f in present)


def publish_once(repo: Path, live_dir: Path, remote: str, branch: str, dry_run: bool = False) -> Published:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit, tree, files, size = build_commit(repo, live_dir, f"live snapshot {stamp}\n\nWritten by poller/publish_live.py; one commit, replaced every cycle.\n")
    if not dry_run:
        git(repo, "push", "--force", "--quiet", remote, f"{commit}:refs/heads/{branch}")
    return Published(commit, tree, files, size, pushed=not dry_run)


def wait_for_files(live_dir: Path, timeout_s: float, pid: int | None = None, sleep: float = 2.0) -> bool:
    """Wait until the required files exist (the poller writes them after its first cycle, 20 to 40 s in).
    Returns False on timeout or when the watched process has gone."""
    deadline = time.monotonic() + timeout_s
    while True:
        if all((live_dir / f).exists() for f in REQUIRED):
            return True
        if time.monotonic() >= deadline or (pid is not None and not pid_alive(pid)):
            return False
        time.sleep(sleep)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="force-push the live snapshot files to an orphan branch")
    ap.add_argument("--repo", type=Path, default=ROOT)
    ap.add_argument("--live-dir", type=Path, default=ROOT / "live")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--branch", default="live")
    ap.add_argument("--every", type=float, default=300.0, help="seconds between publishes")
    ap.add_argument("--while-pid", type=int, default=None, help="keep publishing while this process lives, then publish once more and exit")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="build the commit, push nothing")
    args = ap.parse_args(argv)
    if not wait_for_files(args.live_dir, timeout_s=180, pid=args.while_pid):
        print(f"required files not in {args.live_dir} after 180 s: {REQUIRED}", file=sys.stderr, flush=True)
    failures = 0
    while True:
        try:
            p = publish_once(args.repo, args.live_dir, args.remote, args.branch, args.dry_run)
            print(f"{datetime.now(timezone.utc).strftime('%H:%M:%SZ')} {'built' if args.dry_run else 'pushed'} {p.commit[:12]} "
                  f"({len(p.files)} files, {p.bytes / 1e3:.0f} KB) to {args.remote} {args.branch}", flush=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            failures += 1
            detail = e.stderr.strip() if isinstance(e, subprocess.CalledProcessError) else str(e)
            print(f"publish failed ({failures}): {detail}", file=sys.stderr, flush=True)
        if args.once:
            return 0 if failures == 0 else 1
        if args.while_pid is not None and not pid_alive(args.while_pid):
            return 0
        deadline = time.monotonic() + args.every
        while time.monotonic() < deadline:
            if args.while_pid is not None and not pid_alive(args.while_pid):
                break  # one final publish follows, then the loop exits above
            time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
