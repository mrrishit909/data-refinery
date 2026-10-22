"""Build the sanitized, read-only data snapshot that the Pages site serves.

Only allow-listed files from completed runs are copied. Nothing here is authoritative: the
site states the snapshot time and links back to the state branch for provenance.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .core import ROOT, config, iso, utcnow
from .runs import hooks
from .state import read_json, write_json

INDEX_FIELDS = ["run_id", "request_id", "status", "attempt", "created_at", "completed_at", "source", "issue",
                "headline"]


def build(state_root: Path, out: Path, source_commit: str, state_commit: str,
          mode: str = "state-snapshot") -> dict[str, Any]:
    cfg = config()
    public_files: list[str] = hooks().PUBLIC_FILES
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    index = read_json(state_root, "index.json", {"runs": []})
    latest = read_json(state_root, "latest.json")
    completed = [r["run_id"] for r in index["runs"] if r["status"] == "completed"]
    chosen = ([latest["run_id"]] if latest else []) + completed
    chosen = list(dict.fromkeys(chosen))[: cfg["limits"]["max_snapshot_runs"]]
    for rid in chosen:
        src = state_root / "runs" / rid
        for name in public_files + ["run.json", "request.json"]:
            if (src / name).is_file():
                dest = out / "runs" / rid / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src / name, dest)
    write_json(out, "index.json", {"runs": [{k: r.get(k) for k in INDEX_FIELDS} for r in index["runs"]]})
    release = read_json(state_root, "releases/current.json")
    if release:
        write_json(out, "release.json", release)
    lessons = ROOT / "memory" / "lessons.json"
    if lessons.exists():
        write_json(out, "memory.json", json.loads(lessons.read_text()))
    meta = {
        "project": cfg["project"],
        "mode": mode,
        "generated_at": iso(utcnow()),
        "source_commit": source_commit,
        "state_commit": state_commit,
        "latest_run_id": latest["run_id"] if latest else None,
        "runs_included": chosen,
        "stale_after_hours": 24 * 14,
        "files": {"release": bool(release), "memory": lessons.exists()},
        "state_branch_url": f"https://github.com/{cfg['owner']}/{cfg['repo']}/tree/{cfg['state_branch']}",
    }
    write_json(out, "snapshot.json", meta)
    return meta
