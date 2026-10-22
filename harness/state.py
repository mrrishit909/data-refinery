"""Durable state on the `state` branch of the project repository.

Concurrency model: optimistic compare-and-swap on the branch ref. Every write is a pure,
idempotent mutation applied to a fresh copy of the remote tip; `git push` (never forced)
succeeds only if nobody moved the branch in between, otherwise we reset to the new tip,
re-apply the mutation and retry with backoff. Related files change in one commit.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .core import TERMINAL, HarnessError, ImmutableConflict, config, pretty, sha256_bytes

INDEX = "index.json"
LATEST = "latest.json"


def _scrub(stderr: str) -> str:
    """The end of git's stderr with any URL userinfo removed (none is expected here, but logs are public)."""
    return re.sub(r"://[^/@\s]+@", "://", stderr.strip())[-600:]


class StateRepo:
    def __init__(self, path: Path, branch: str | None = None, remote: str = "origin") -> None:
        self.path = Path(path)
        self.branch = branch or config()["state_branch"]
        self.remote = remote

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(["git", *args], cwd=self.path, capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise HarnessError(f"git {args[0]} failed with exit {proc.returncode}: {_scrub(proc.stderr)}")
        return proc

    def sync(self) -> None:
        # Fetch failures are usually transient (network, a concurrent ref update); retry briefly.
        for attempt in range(3):
            fetched = self.git("fetch", "--quiet", "--depth=1", self.remote, self.branch, check=False)
            if fetched.returncode == 0:
                break
            if attempt == 2:
                raise HarnessError(f"git fetch failed with exit {fetched.returncode}: {_scrub(fetched.stderr)}")
            time.sleep(1 + attempt)
        self.git("reset", "--quiet", "--hard", f"{self.remote}/{self.branch}")
        self.git("clean", "-qfdx")

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def transact(self, mutate: Callable[[Path], Any], message: str, attempts: int = 6) -> Any:
        for attempt in range(attempts):
            self.sync()
            result = mutate(self.path)
            if not self.git("status", "--porcelain").stdout.strip():
                return result
            self.git("add", "-A")
            self.git("commit", "--quiet", "-m", message)
            pushed = self.git("push", "--quiet", self.remote, f"HEAD:refs/heads/{self.branch}", check=False)
            if pushed.returncode == 0:
                return result
            time.sleep(min(30.0, 2**attempt + random.random()))
        raise HarnessError(f"state write '{message}' lost the race {attempts} times; nothing was written")


# Mutations: pure functions of (state root, inputs). Safe to re-apply. ---------------------------

def read_json(root: Path, rel: str, default: Any = None) -> Any:
    path = root / rel
    return json.loads(path.read_text()) if path.exists() else default


def write_json(root: Path, rel: str, obj: Any) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pretty(obj))


def write_immutable(root: Path, rel: str, data: bytes) -> None:
    path = root / rel
    if path.exists():
        if path.read_bytes() != data:
            raise ImmutableConflict(f"{rel} already exists with different content")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def put_run_record(root: Path, record: dict[str, Any]) -> None:
    rel = f"runs/{record['run_id']}/run.json"
    existing = read_json(root, rel)
    if existing and existing["status"] in TERMINAL and existing != record:
        raise ImmutableConflict(f"{rel} is terminal ({existing['status']}) and cannot change")
    write_json(root, rel, record)


def copy_results(root: Path, run_id: str, src: Path) -> list[str]:
    paths = []
    for file in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = f"runs/{run_id}/{file.relative_to(src).as_posix()}"
        if rel.endswith("/run.json"):
            raise HarnessError("result files may not be named run.json")
        write_immutable(root, rel, file.read_bytes())
        paths.append(rel)
    return paths


def upsert_index(root: Path, record: dict[str, Any], headline: str | None = None) -> None:
    limit = config()["limits"]["max_index_entries"]
    index = read_json(root, INDEX, {"schema_version": "1.0", "runs": []})
    entry = {
        "run_id": record["run_id"],
        "request_id": record["request_id"],
        "status": record["status"],
        "attempt": record["attempt"],
        "created_at": record["created_at"],
        "completed_at": record["completed_at"],
        "source": record["request_source"]["type"],
        "issue": record["request_source"]["issue_number"],
        "headline": headline,
    }
    runs = [r for r in index["runs"] if r["run_id"] != record["run_id"]] + [entry]
    runs.sort(key=lambda r: (r["created_at"], r["run_id"]), reverse=True)
    if len(runs) > limit:
        overflow, runs = runs[limit:], runs[:limit]
        archive = read_json(root, "index-archive.json", {"runs": []})
        archive["runs"] = sorted({r["run_id"]: r for r in archive["runs"] + overflow}.values(),
                                 key=lambda r: r["run_id"])
        write_json(root, "index-archive.json", archive)
    index["runs"] = runs
    write_json(root, INDEX, index)


def put_request(root: Path, req_id: str, params_hash: str, record: dict[str, Any]) -> None:
    rel = f"requests/{req_id}.json"
    req = read_json(root, rel, {"request_id": req_id, "params_hash": params_hash, "attempts": []})
    attempts = [a for a in req["attempts"] if a["run_id"] != record["run_id"]]
    attempts.append({"run_id": record["run_id"], "attempt": record["attempt"], "status": record["status"]})
    attempts.sort(key=lambda a: a["attempt"])
    req["attempts"] = attempts
    req["latest_status"] = attempts[-1]["status"]
    write_json(root, rel, req)


def set_latest(root: Path, record: dict[str, Any]) -> None:
    """Move the latest-result pointer. Callers do this only after outputs were validated."""
    if record["status"] != "completed":
        raise HarnessError("latest pointer may only reference a completed run")
    current = read_json(root, LATEST)
    if current and current["completed_at"] > record["completed_at"]:
        return  # never move the pointer backwards in time
    write_json(root, LATEST, {
        "run_id": record["run_id"],
        "completed_at": record["completed_at"],
        "result_paths": record["result_paths"],
        "input_manifest_hash": record["input_manifest_hash"],
    })


def prune_run_dirs(root: Path) -> list[str]:
    """Bound repository growth: keep result directories for the newest N runs plus pinned runs."""
    keep_n = config()["limits"]["max_retained_run_dirs"]
    index = read_json(root, INDEX, {"runs": []})
    pinned = {(read_json(root, LATEST) or {}).get("run_id"),
              (read_json(root, "releases/current.json") or {}).get("run_id")}
    keep = {r["run_id"] for r in index["runs"][:keep_n]} | pinned
    removed = []
    runs_dir = root / "runs"
    for run_dir in sorted(runs_dir.iterdir()) if runs_dir.exists() else []:
        if run_dir.name not in keep:
            for file in run_dir.iterdir():
                if file.name != "run.json":  # the record itself is tiny and kept for history
                    shutil.rmtree(file) if file.is_dir() else file.unlink()
                    removed.append(f"{run_dir.name}/{file.name}")
    return removed


def tree_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def tree_hash(path: Path) -> str:
    parts = [f"{p.relative_to(path).as_posix()}:{sha256_bytes(p.read_bytes())}"
             for p in sorted(path.rglob("*")) if p.is_file()]
    return sha256_bytes("\n".join(parts).encode())
