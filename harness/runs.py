"""Run lifecycle: admit (queued -> running | blocked) and publish (running -> terminal).

Partial-write recovery rules (see docs/STATE_MODEL.md):
- Dispatch happened but admit never committed: the issue stays job:requested; the reconciler
  re-labels it job:blocked after a timeout so the owner can resubmit. No run record exists.
- Admit committed `running` but compute/publish never finished: the reconciler inspects the
  workflow run; it re-publishes from the retained artifact if one exists, else marks the run
  failed or cancelled. The original record and its evidence links are kept.
- Results committed but issue not updated: issue labels are views of state; the reconciler
  re-syncs them. State is the source of truth, Issues are the human-readable ledger.
- A publish re-run after success is a no-op (terminal records are immutable).
"""

from __future__ import annotations

import importlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .core import (
    TERMINAL,
    HarnessError,
    config,
    iso,
    parse_iso,
    request_id,
    require_valid,
    run_id,
    schema_errors,
    sha256_json,
    transition,
    utcnow,
)
from .state import (
    StateRepo,
    copy_results,
    prune_run_dirs,
    put_request,
    put_run_record,
    read_json,
    set_latest,
    tree_bytes,
    upsert_index,
    write_json,
)

MAX_ATTEMPTS = 3
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def hooks() -> Any:
    return importlib.import_module("project_hooks")


def request_errors(params: dict[str, Any]) -> list[str]:
    """Schema validation, then the project's own feasibility rules (only on schema-valid input)."""
    errors = schema_errors(params, "request.schema.json")
    if not errors and hasattr(hooks(), "request_problems"):
        errors = list(hooks().request_problems(params))
    return errors


def sanitize(text: str, limit: int = 2000) -> str:
    return _CONTROL.sub(" ", text)[:limit]


def admit(state: StateRepo, params: dict[str, Any], source: dict[str, Any], workflow_run_id: int | None,
          source_commit: str, now: datetime | None = None) -> dict[str, Any]:
    cfg = config()
    errors = request_errors(params)
    if errors:
        return {"decision": "invalid", "errors": errors}
    req_id = request_id(cfg["project"], params)
    params_hash = sha256_json(params)
    now = now or utcnow()
    limits = cfg["limits"]

    def mutate(root: Path) -> dict[str, Any]:
        req = read_json(root, f"requests/{req_id}.json")
        index = read_json(root, "index.json", {"runs": []})
        attempt, parent = 1, None
        if req:
            last = req["attempts"][-1]
            last_rec = read_json(root, f"runs/{last['run_id']}/run.json")
            if last["status"] == "completed":
                return {"decision": "duplicate", "request_id": req_id, "run_id": last["run_id"]}
            if last["status"] in ("running", "queued"):
                if last_rec and last_rec["workflow_run_id"] == workflow_run_id:
                    # A re-run of the same workflow run: the earlier attempt died before publishing.
                    dead = transition(last_rec, "failed", completed_at=iso(now),
                                      error_summary="superseded by a re-run of the same workflow before it published")
                    put_run_record(root, dead)
                    put_request(root, req_id, params_hash, dead)
                    upsert_index(root, dead)
                    last = {**last, "status": "failed"}
                else:
                    return {"decision": "duplicate", "request_id": req_id, "run_id": last["run_id"],
                            "in_progress": True}
            if last["attempt"] >= MAX_ATTEMPTS:
                return {"decision": "blocked", "request_id": req_id, "reason": "retry limit reached"}
            attempt, parent = last["attempt"] + 1, last["run_id"]

        rid = run_id(cfg["project"], now, req_id, attempt)
        record: dict[str, Any] = {
            "run_id": rid, "project": cfg["project"], "request_id": req_id,
            "workflow_run_id": workflow_run_id, "source_commit": source_commit,
            "input_manifest_hash": None, "schema_version": "1.0", "created_at": iso(now),
            "started_at": None, "completed_at": None, "status": "queued", "attempt": attempt,
            "parent_run_id": parent, "result_paths": [], "evidence_paths": [], "error_summary": None,
            "resource_usage_summary": {"wall_seconds": None, "output_bytes": None,
                                       "inference": {"used": False, "usd": None}},
            "request_source": source,
        }
        month = now.strftime("%Y-%m")
        used = sum(1 for r in index["runs"] if r["created_at"].startswith(month) and r["status"] != "blocked")
        running = sum(1 for r in index["runs"] if r["status"] == "running")
        if used >= limits["max_runs_per_month"]:
            record = transition(record, "blocked", completed_at=iso(now),
                                error_summary=f"monthly run ceiling reached ({limits['max_runs_per_month']})")
        elif running >= limits["max_open_requests"]:
            record = transition(record, "blocked", completed_at=iso(now),
                                error_summary=f"concurrent run ceiling reached ({limits['max_open_requests']})")
        else:
            record = transition(record, "running", started_at=iso(now))
        require_valid(record, "run.schema.json")
        put_run_record(root, record)
        put_request(root, req_id, params_hash, record)
        upsert_index(root, record)
        write_json(root, f"runs/{rid}/request.json", {"params": params, "request_id": req_id})
        return {"decision": "run" if record["status"] == "running" else "blocked", "run_id": rid,
                "request_id": req_id, "attempt": attempt, "reason": record["error_summary"]}

    return state.transact(mutate, f"admit {req_id}")


def validate_outputs(out_dir: Path | None) -> list[str]:
    if out_dir is None or not out_dir.is_dir() or not any(out_dir.iterdir()):
        return ["compute produced no output directory"]
    problems = list(hooks().validate_outputs(out_dir))
    size = tree_bytes(out_dir)
    if size > config()["limits"]["max_run_bytes"]:
        problems.append(f"output is {size} bytes, above the {config()['limits']['max_run_bytes']} byte ceiling")
    return problems


def publish(state: StateRepo, rid: str, conclusion: str, out_dir: Path | None, error_text: str | None,
            run_url: str | None, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    problems = validate_outputs(out_dir) if conclusion == "success" else []
    evidence = [run_url] if run_url else []

    def mutate(root: Path) -> dict[str, Any]:
        rec = read_json(root, f"runs/{rid}/run.json")
        if rec is None:
            raise HarnessError(f"no admitted run record for {rid}")
        if rec["status"] in TERMINAL:
            return rec
        wall = (now - parse_iso(rec["started_at"])).total_seconds() if rec["started_at"] else None
        usage = {**rec["resource_usage_summary"], "wall_seconds": wall}
        if conclusion == "success" and not problems:
            assert out_dir is not None
            paths = copy_results(root, rid, out_dir)
            manifest = json.loads((out_dir / "manifest.json").read_text())
            new = transition(rec, "completed", completed_at=iso(now), result_paths=paths,
                             input_manifest_hash=manifest.get("input_manifest_hash"),
                             evidence_paths=evidence,
                             resource_usage_summary={**usage, "output_bytes": tree_bytes(out_dir)})
            new["result_paths"] = paths + list(hooks().on_publish(root, new, out_dir) or [])
            headline = hooks().headline(out_dir)
        else:
            status = "cancelled" if conclusion == "cancelled" else "failed"
            detail = "; ".join(problems) if problems else (error_text or f"compute job concluded {conclusion}")
            write_json(root, f"runs/{rid}/failure.json",
                       {"conclusion": conclusion, "problems": problems, "detail": sanitize(detail)})
            new = transition(rec, status, completed_at=iso(now), error_summary=sanitize(detail),
                             evidence_paths=evidence + [f"runs/{rid}/failure.json"],
                             resource_usage_summary=usage)
            headline = None
        require_valid(new, "run.schema.json")
        put_run_record(root, new)
        put_request(root, new["request_id"], read_json(root, f"requests/{new['request_id']}.json")["params_hash"], new)
        upsert_index(root, new, headline)
        if new["status"] == "completed":
            set_latest(root, new)
            prune_run_dirs(root)
        return new

    return state.transact(mutate, f"publish {rid}")
