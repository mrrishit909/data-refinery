import json
from datetime import UTC, datetime, timedelta

import pytest
from harness.core import IllegalTransition, ImmutableConflict, transition
from harness.runs import admit, publish
from harness.state import read_json, set_latest, write_immutable

from .conftest import clone_state, make_outputs

PARAMS = {"batches": ["2026-01"], "contract": "orders.v1", "repair_mode": "propose", "fx_table": "fixed-2026q1"}
SRC = {"type": "manual", "issue_number": None, "actor": "mrrishit909"}
T0 = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)


def test_cas_retry_keeps_both_writers(remote, tmp_path):
    a = clone_state(remote, tmp_path / "a")
    b = clone_state(remote, tmp_path / "b")
    a.transact(lambda root: (root / "from_a.txt").write_text("a"), "a")
    # b's local tip is stale; push must be rejected once, then re-applied on the new tip.
    b.transact(lambda root: (root / "from_b.txt").write_text("b"), "b")
    b.sync()
    assert (b.path / "from_a.txt").exists() and (b.path / "from_b.txt").exists()


def test_immutable_paths_refuse_different_content(state):
    state.transact(lambda root: write_immutable(root, "runs/x/r.json", b"1"), "w1")
    state.transact(lambda root: write_immutable(root, "runs/x/r.json", b"1"), "same content is a no-op")
    with pytest.raises(ImmutableConflict):
        state.transact(lambda root: write_immutable(root, "runs/x/r.json", b"2"), "w2")


def test_state_machine_rejects_illegal_transitions():
    rec = {"run_id": "r", "status": "completed"}
    with pytest.raises(IllegalTransition):
        transition(rec, "running")
    with pytest.raises(IllegalTransition):
        transition({"run_id": "r", "status": "queued"}, "completed")


def test_admit_publish_duplicate_and_latest(state, fake_hooks, tmp_path):
    first = admit(state, PARAMS, SRC, 101, "a" * 40, now=T0)
    assert first["decision"] == "run" and first["attempt"] == 1
    again = admit(state, PARAMS, SRC, 102, "a" * 40, now=T0 + timedelta(minutes=1))
    assert again["decision"] == "duplicate" and again["in_progress"]

    out = make_outputs(tmp_path / "out")
    rec = publish(state, first["run_id"], "success", out, None, "https://example.invalid/run", now=T0 + timedelta(minutes=5))
    assert rec["status"] == "completed" and rec["completed_at"]
    assert any(p.endswith("results.json") for p in rec["result_paths"])
    state.sync()
    assert read_json(state.path, "latest.json")["run_id"] == first["run_id"]
    # Publishing the same run again is a no-op, never a second write.
    head = state.head()
    publish(state, first["run_id"], "success", out, None, None, now=T0 + timedelta(minutes=6))
    state.sync()
    assert state.head() == head
    dup = admit(state, PARAMS, SRC, 103, "a" * 40, now=T0 + timedelta(minutes=7))
    assert dup["decision"] == "duplicate" and not dup.get("in_progress")


def test_failed_attempt_is_retained_and_retry_links_parent(state, fake_hooks, tmp_path):
    first = admit(state, PARAMS, SRC, 201, "b" * 40, now=T0)
    failed = publish(state, first["run_id"], "failure", None, "boom", None, now=T0 + timedelta(minutes=2))
    assert failed["status"] == "failed" and "boom" in failed["error_summary"]
    second = admit(state, PARAMS, SRC, 202, "b" * 40, now=T0 + timedelta(minutes=3))
    assert second["decision"] == "run" and second["attempt"] == 2
    state.sync()
    rec2 = read_json(state.path, f"runs/{second['run_id']}/run.json")
    assert rec2["parent_run_id"] == first["run_id"]
    assert read_json(state.path, f"runs/{first['run_id']}/failure.json")["detail"] == "boom"
    assert read_json(state.path, "latest.json") is None  # failed runs never move the pointer


def test_invalid_outputs_fail_the_run_without_publishing_results(state, fake_hooks, tmp_path):
    first = admit(state, PARAMS, SRC, 301, "c" * 40, now=T0)
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{}")
    rec = publish(state, first["run_id"], "success", bad, None, None, now=T0 + timedelta(minutes=1))
    assert rec["status"] == "failed" and "results.json missing" in rec["error_summary"]
    state.sync()
    assert not (state.path / "runs" / first["run_id"] / "manifest.json").exists()


def test_rerun_of_same_workflow_supersedes_dead_attempt(state, fake_hooks):
    first = admit(state, PARAMS, SRC, 401, "d" * 40, now=T0)
    again = admit(state, PARAMS, SRC, 401, "d" * 40, now=T0 + timedelta(minutes=30))
    assert again["decision"] == "run" and again["attempt"] == 2
    state.sync()
    assert read_json(state.path, f"runs/{first['run_id']}/run.json")["status"] == "failed"


def test_monthly_ceiling_blocks(state, fake_hooks, monkeypatch):
    from harness import core
    cfg = json.loads(json.dumps(core.config()))
    cfg["limits"]["max_runs_per_month"] = 1
    monkeypatch.setattr("harness.runs.config", lambda: cfg)
    assert admit(state, PARAMS, SRC, 501, "e" * 40, now=T0)["decision"] == "run"
    other = {**PARAMS, "repair_mode": "apply-safe"}
    blocked = admit(state, other, SRC, 502, "e" * 40, now=T0 + timedelta(minutes=1))
    assert blocked["decision"] == "blocked" and "ceiling" in blocked["reason"]


def test_latest_pointer_requires_completed(tmp_path):
    with pytest.raises(Exception):
        set_latest(tmp_path, {"status": "failed", "completed_at": "x"})


def test_invalid_request_never_writes(state, fake_hooks):
    head = state.head()
    result = admit(state, {**PARAMS, "contract": "orders.v2"}, SRC, 601, "f" * 40, now=T0)
    assert result["decision"] == "invalid"
    assert state.head() == head
