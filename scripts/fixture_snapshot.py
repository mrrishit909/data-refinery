"""Build a real-but-labelled fixture snapshot for CI browser tests.

Runs the actual engine on a fixed request, wraps it in a run record exactly as the harness
would, and builds the Pages snapshot with mode="fixture". The site shows a "fixture data"
notice in this mode. Pages deployments never use this script; they snapshot the state branch.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.core import config, iso, pretty, request_id, run_id  # noqa: E402
from harness.snapshot import build  # noqa: E402
from harness.state import tree_bytes  # noqa: E402

REQUEST = {"batches": ["2026-01", "2026-02", "2026-03", "2026-04"], "contract": "orders.v1",
           "repair_mode": "apply-safe", "fx_table": "fixed-2026q1"}
FIXED_TIME = datetime(2026, 10, 21, 0, 0, 0, tzinfo=UTC)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    cfg = config()
    req_id = request_id(cfg["project"], REQUEST)
    rid = run_id(cfg["project"], FIXED_TIME, req_id, 1)
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        run_dir = state / "runs" / rid
        (state / "request.json").write_text(json.dumps(REQUEST))
        import project_hooks

        project_hooks.compute(REQUEST, run_dir)
        manifest = json.loads((run_dir / "manifest.json").read_text())
        headline = project_hooks.headline(run_dir)
        paths = sorted(f"runs/{rid}/{p.name}" for p in run_dir.iterdir())
        record = {
            "run_id": rid, "project": cfg["project"], "request_id": req_id, "workflow_run_id": None,
            "source_commit": "0" * 40, "input_manifest_hash": manifest["input_manifest_hash"],
            "schema_version": "1.0", "created_at": iso(FIXED_TIME), "started_at": iso(FIXED_TIME),
            "completed_at": iso(FIXED_TIME), "status": "completed", "attempt": 1, "parent_run_id": None,
            "result_paths": paths, "evidence_paths": [], "error_summary": None,
            "resource_usage_summary": {"wall_seconds": None, "output_bytes": tree_bytes(run_dir),
                                       "inference": {"used": False, "usd": None}},
            "request_source": {"type": "manual", "issue_number": None, "actor": "fixture"},
        }
        (run_dir / "run.json").write_text(pretty(record))
        (run_dir / "request.json").write_text(pretty({"params": REQUEST, "request_id": req_id}))
        index = {"schema_version": "1.0", "runs": [{
            "run_id": rid, "request_id": req_id, "status": "completed", "attempt": 1,
            "created_at": record["created_at"], "completed_at": record["completed_at"], "source": "manual",
            "issue": None, "headline": headline}]}
        (state / "index.json").write_text(pretty(index))
        (state / "latest.json").write_text(pretty({"run_id": rid, "completed_at": record["completed_at"],
                                                   "result_paths": paths,
                                                   "input_manifest_hash": record["input_manifest_hash"]}))
        meta = build(state, Path(args.out), "fixture", "fixture", mode="fixture")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
