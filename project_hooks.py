"""Data Refinery hooks for the generic harness (compute, feasibility, validation, headline, memory)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from harness.core import config
from harness.state import read_json

PUBLIC_FILES = [
    "manifest.json", "profiles.json", "drift.json", "repairs.json", "ambiguities.json", "plan.json",
    "cleaned.csv", "quarantine.csv", "reconciliation.json", "lineage.json", "quality.json", "samples.json",
    "checks.json", "report.md", "contract.json",
]
BATCH_ORDER = ["2026-01", "2026-02", "2026-03", "2026-04"]


def compute(params: dict[str, Any], out_dir: Path) -> None:
    """Deterministic pipeline run (called by `python -m harness compute` on the read-only compute job)."""
    with tempfile.TemporaryDirectory() as tmp:
        req = Path(tmp) / "request.json"
        req.write_text(json.dumps(params))
        subprocess.run([sys.executable, "-m", "datarefinery", "run", "--request", str(req), "--out", str(out_dir)],
                       check=True)
    # Record the exact contract and FX table this run used (also feeds the site's in-browser CSV preview).
    root = Path(__file__).resolve().parent / "contracts"
    snapshot = {
        "contract": json.loads((root / f"{params['contract']}.json").read_text()),
        "fx": json.loads((root / f"fx_{params['fx_table']}.json").read_text()),
    }
    (out_dir / "contract.json").write_text(json.dumps(snapshot, sort_keys=True, indent=2) + "\n")


def request_problems(params: dict[str, Any]) -> list[str]:
    """Batches must be listed in date order, so one set of batches is one request (one idempotency key)."""
    batches = params["batches"]
    if batches != sorted(batches, key=BATCH_ORDER.index):
        return ["batches: list batches in date order (for example 2026-01 before 2026-02)"]
    return []


def validate_outputs(out_dir: Path) -> list[str]:
    from datarefinery.outputs import validate

    return validate(out_dir)


def headline(out_dir: Path) -> str | None:
    rec = json.loads((out_dir / "reconciliation.json").read_text())
    totals = Counter()
    for entry in _batches(rec):
        for key in ("rows_in", "rows_accepted", "rows_quarantined", "rows_merged_duplicates"):
            totals[key] += int(entry.get(key, 0))
    repairs = json.loads((out_dir / "repairs.json").read_text())
    items = repairs if isinstance(repairs, list) else repairs.get("repairs", [])
    applied = sum(1 for r in items if r.get("applied"))
    amb = json.loads((out_dir / "ambiguities.json").read_text())
    n_amb = len(amb if isinstance(amb, list) else amb.get("ambiguities", []))
    return (f"{totals['rows_in']:,} rows in: {totals['rows_accepted']:,} accepted, {totals['rows_quarantined']:,} "
            f"quarantined, {totals['rows_merged_duplicates']:,} merged; {applied} repairs applied, {n_amb} need a human.")


def _batches(rec: Any) -> list[dict[str, Any]]:
    if isinstance(rec, dict) and isinstance(rec.get("batches"), list):
        return rec["batches"]
    if isinstance(rec, dict) and isinstance(rec.get("batches"), dict):
        return [{"batch": k, **v} for k, v in rec["batches"].items()]
    return []


def on_publish(root: Path, record: dict[str, Any], out_dir: Path) -> list[str]:
    return []


def memory_candidates(root: Path) -> list[dict[str, Any]]:
    """Lessons backed by at least three completed runs: recurring ambiguity and quarantine patterns."""
    cfg = config()
    index = read_json(root, "index.json", {"runs": []})
    reasons: Counter[str] = Counter()
    reason_runs: dict[str, list[str]] = {}
    ambiguous: Counter[str] = Counter()
    amb_runs: dict[str, list[str]] = {}
    for entry in index["runs"]:
        if entry["status"] != "completed":
            continue
        rid = entry["run_id"]
        rec = read_json(root, f"runs/{rid}/reconciliation.json")
        if rec:
            seen = set()
            for b in _batches(rec):
                for code, n in (b.get("quarantined_by_reason") or {}).items():
                    if n and code not in seen:
                        seen.add(code)
                        reasons[code] += 1
                        reason_runs.setdefault(code, []).append(rid)
        amb = read_json(root, f"runs/{rid}/ambiguities.json")
        for a in (amb if isinstance(amb, list) else (amb or {}).get("ambiguities", [])):
            key = f"{a.get('column', '?')}={a.get('value', '?')}"
            ambiguous[key] += 1
            amb_runs.setdefault(key, []).append(rid)
    base = f"https://github.com/{cfg['owner']}/{cfg['repo']}/blob/{cfg['state_branch']}"
    expires = (date.today() + timedelta(days=90)).isoformat()
    out = []
    for key, count in sorted(ambiguous.items()):
        if count >= 3:
            runs = sorted(set(amb_runs[key]))[:20]
            out.append({
                "topic": f"Recurring ambiguous value {key}",
                "finding": f"The value {key} was left for a human decision in {count} completed runs; it has never been "
                           "auto-mapped because its business meaning is ambiguous.",
                "supporting_run_ids": runs,
                "evidence_links": [f"{base}/runs/{r}/ambiguities.json" for r in runs[:5]],
                "scope": "data-quality",
                "confidence": "medium" if count >= 6 else "low",
                "revalidate_when": "after a contract change or a new mapping decision recorded by the owner",
                "expires_on": expires,
            })
    for code, count in sorted(reasons.items()):
        if count >= 3:
            runs = sorted(set(reason_runs[code]))[:20]
            out.append({
                "topic": f"Recurring quarantine reason {code}",
                "finding": f"Rows were quarantined with reason {code} in {count} completed runs.",
                "supporting_run_ids": runs,
                "evidence_links": [f"{base}/runs/{r}/reconciliation.json" for r in runs[:5]],
                "scope": "data-quality",
                "confidence": "medium" if count >= 6 else "low",
                "revalidate_when": "after a change to the fixtures, the contract or the repair rules",
                "expires_on": expires,
            })
    return out
