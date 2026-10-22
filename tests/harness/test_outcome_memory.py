import json
from datetime import date
from pathlib import Path

from harness.core import ROOT
from harness.memory import apply, entry_id, propose, screen
from harness.outcome import record, rubric_integrity
from harness.state import read_json


def test_rubric_matches_lock():
    ok, digest = rubric_integrity(ROOT / "rubric" / "outcome-rubric.json", ROOT / "rubric" / "rubric.lock")
    assert ok, f"rubric changed without updating rubric/rubric.lock (now {digest})"


def _result(failing: bool) -> dict:
    status = "fail" if failing else "pass"
    return {"rubric_version": "1.0", "rubric_sha256": "sha256:x", "max_repair_rounds": 3,
            "subjective": {"status": "not_run", "reason": "test", "scores": []},
            "findings": [{"id": "g1", "title": "Gate one", "kind": "pytest", "status": status, "objective": True,
                          "evidence_paths": ["g1.log"], "detail": "d", "seconds": 0.1}]}


def test_failing_gate_blocks_release_and_rounds_are_bounded(state, tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "g1.log").write_text("gate log")
    ctx = {"evidence_dir": str(evidence), "state_latest_run_id": None, "run_url": None}
    releases = [record(state, _result(True), f"{i:040x}", ctx, None) for i in range(1, 5)]
    assert all(r["status"] == "blocked" for r in releases)
    assert [r["repair_round"] for r in releases] == [1, 2, 3, 4]
    assert "exhausted" in releases[-1]["blocked_reason"]
    state.sync()
    assert read_json(state.path, "releases/current.json") is None
    ok = record(state, _result(False), "f" * 40, ctx, None)
    state.sync()
    assert ok["status"] == "released"
    assert read_json(state.path, "releases/current.json")["release_id"] == ok["release_id"]


def _lesson(topic="Strategy 'trend' across synthetic regimes", finding="Rejected in 3 of 3 completed runs."):
    return {"id": entry_id(topic, finding), "topic": topic, "finding": finding, "supporting_run_ids": ["r1"],
            "evidence_links": ["https://github.com/o/r/blob/state/runs/r1/results.json"],
            "scope": "research-finding", "confidence": "low", "date": "2026-10-21",
            "revalidate_when": "after 10 more runs", "expires_on": "2027-01-19", "supersedes": [],
            "status": "active"}


def test_guard_rejects_protected_subjects():
    assert screen(_lesson(finding="Raise the acceptance threshold so more strategies pass."))
    assert screen({**_lesson(), "scope": "security-boundary"})
    assert not screen(_lesson())


def test_propose_dedupes_and_supersedes(fake_hooks, tmp_path, monkeypatch):
    accepted = _lesson()
    monkeypatch.setattr("harness.memory.load_lessons", lambda root=None: {"entries": [accepted]})
    same = {k: accepted[k] for k in ("topic", "finding", "supporting_run_ids", "evidence_links", "scope",
                                     "confidence", "revalidate_when", "expires_on")}
    changed = {**same, "finding": "Rejected in 5 of 6 completed runs."}
    fake_hooks.memory_candidates = lambda root: [same, changed]
    proposal = propose(tmp_path, today=date(2026, 10, 21))
    assert proposal["adds"] == []
    assert len(proposal["supersedes"]) == 1 and proposal["supersedes"][0]["old_id"] == accepted["id"]
    merged = apply({"entries": [accepted]}, proposal)
    statuses = {e["id"]: e["status"] for e in merged["entries"]}
    assert statuses[accepted["id"]] == "superseded"


def test_expired_lessons_are_proposed_for_retirement(fake_hooks, tmp_path, monkeypatch):
    old = {**_lesson(), "expires_on": "2026-01-01"}
    monkeypatch.setattr("harness.memory.load_lessons", lambda root=None: {"entries": [old]})
    proposal = propose(tmp_path, today=date(2026, 10, 21))
    assert proposal["retires"][0]["id"] == old["id"]


def test_rubric_hard_gates_are_well_formed():
    rubric = json.loads((Path(ROOT) / "rubric" / "outcome-rubric.json").read_text())
    ids = [g["id"] for g in rubric["hard_gates"]]
    assert len(ids) == len(set(ids)) and rubric["max_repair_rounds"] == 3
    assert all(g["kind"] in {"pytest", "command", "live", "evidence-file"} for g in rubric["hard_gates"])
