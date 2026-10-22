"""Real hooks against real engine output (the fake-hook tests cannot catch contract drift)."""

import json
import shutil
from pathlib import Path

import project_hooks
from harness.memory import screen

REQUEST = {"batches": ["2026-01", "2026-02", "2026-03", "2026-04"], "repair_mode": "apply-safe",
           "contract": "orders.v1", "fx_table": "fixed-2026q1"}


def test_hooks_on_real_outputs(tmp_path: Path):
    out = tmp_path / "out"
    project_hooks.compute(REQUEST, out)
    assert project_hooks.validate_outputs(out) == []

    rec = json.loads((out / "reconciliation.json").read_text())
    batches = rec["batches"].values()
    rows_in = sum(b["rows_in"] for b in batches)
    accepted = sum(b["rows_accepted"] for b in batches)
    assert project_hooks.headline(out).startswith(f"{rows_in:,} rows in: {accepted:,} accepted")

    state = tmp_path / "state"
    runs = []
    for i in range(3):
        rid = f"data-refinery-2026102{i}T000000Z-abcdef12-a1"
        shutil.copytree(out, state / "runs" / rid)
        runs.append({"run_id": rid, "status": "completed", "created_at": f"2026-10-2{i}T00:00:00Z"})
    (state / "index.json").write_text(json.dumps({"runs": runs}))
    candidates = project_hooks.memory_candidates(state)
    topics = {c["topic"] for c in candidates}
    assert "Recurring quarantine reason AMBIGUOUS_CATEGORY" in topics
    assert any(t.startswith("Recurring ambiguous value category=") for t in topics)
    for c in candidates:
        entry = {**c, "id": "mem-0123456789", "status": "active", "date": "2026-10-21", "supersedes": []}
        assert screen(entry) == [], (c["topic"], screen(entry))


def test_request_problems_orders_batches():
    assert project_hooks.request_problems(REQUEST) == []
    assert project_hooks.request_problems({**REQUEST, "batches": ["2026-02", "2026-01"]})
