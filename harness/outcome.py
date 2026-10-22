"""Outcome Verification Loop (a local-harness equivalent running in GitHub Actions; not a native
Claude product feature).

1. Load the fixed rubric and verify it against rubric/rubric.lock (a grader or builder cannot
   change the rubric, tests, thresholds or baselines without the lock changing in review).
2. Execute deterministic hard gates; every result carries evidence paths.
3. Merge optional independent grader scores (subjective, never able to pass a failed hard gate).
4. Produce structured findings; assign bounded repairs (max rounds per defect batch).
5. Write the release record: `released` only when every hard gate passed.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .core import ROOT, config, iso, pretty, require_valid, utcnow
from .github import GitHub
from .state import StateRepo, read_json, write_json

# Durable evidence is a small representative set; full screenshot sets stay in Actions artifacts (14 days).
EVIDENCE_KEEP = re.compile(
    r"^(e2e/summary\.json|[^/]+\.(junit\.xml|log|json)|e2e/screens/[a-z0-9-]+-first-viewport\.jpg)$"
)
MAX_EVIDENCE_BYTES = 900_000
KEEP_SCREENSHOT_RELEASES = 3


def select_evidence(evidence_src: Path) -> list[Path]:
    """Allow-listed evidence files, smallest first, within the per-release byte cap."""
    files = sorted((f for f in evidence_src.rglob("*") if f.is_file()
                    and EVIDENCE_KEEP.match(f.relative_to(evidence_src).as_posix())), key=lambda f: f.stat().st_size)
    chosen, total = [], 0
    for f in files:
        if total + f.stat().st_size > MAX_EVIDENCE_BYTES:
            continue
        chosen.append(f)
        total += f.stat().st_size
    return sorted(chosen)


def prune_screenshots(root: Path, keep: set[str]) -> None:
    """Keep screenshots only for the newest releases and the current one; records and logs stay."""
    out = root / "outcomes"
    releases = sorted((d for d in out.iterdir() if d.is_dir()), key=lambda d: d.name) if out.exists() else []
    recent = {d.name for d in releases[-KEEP_SCREENSHOT_RELEASES:]} | keep
    for d in releases:
        if d.name not in recent:
            for jpg in d.rglob("*.jpg"):
                jpg.unlink()


def rubric_integrity(rubric_path: Path, lock_path: Path) -> tuple[bool, str]:
    actual = "sha256:" + hashlib.sha256(rubric_path.read_bytes()).hexdigest()
    expected = lock_path.read_text().strip() if lock_path.exists() else ""
    return actual == expected, actual


def _http(url: str, timeout: int = 30) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "outcome-verification-loop"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (rubric-defined https URL)
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except urllib.error.URLError:
        return 0, b""


def _run_gate(gate: dict[str, Any], evidence: Path, context: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    kind = gate["kind"]
    paths: list[str] = []
    detail = ""
    ok = False
    if kind == "pytest":
        xml = evidence / f"{gate['id']}.junit.xml"
        cmd = [sys.executable, "-m", "pytest", *shlex.split(gate["args"]), f"--junitxml={xml}", "-q"]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=gate.get("timeout", 900))
        (evidence / f"{gate['id']}.log").write_text(proc.stdout[-20000:] + proc.stderr[-5000:])
        ok = proc.returncode == 0
        detail = (proc.stdout.strip().splitlines() or ["no output"])[-1][:300]
        paths = [xml.name, f"{gate['id']}.log"]
    elif kind == "command":
        proc = subprocess.run(gate["argv"], cwd=ROOT, capture_output=True, text=True, timeout=gate.get("timeout", 600))
        (evidence / f"{gate['id']}.log").write_text(proc.stdout[-20000:] + proc.stderr[-5000:])
        ok = proc.returncode == 0
        detail = (proc.stdout.strip().splitlines() or [f"exit {proc.returncode}"])[-1][:300]
        paths = [f"{gate['id']}.log"]
    elif kind == "live":
        base = context["live_url"].rstrip("/") + "/"
        checks = []
        status, body = _http(base)
        checks.append({"url": base, "status": status, "ok": status == 200 and gate["marker"].encode() in body})
        status, body = _http(base + "data/snapshot.json")
        snap_ok = status == 200
        snapshot = json.loads(body) if snap_ok else {}
        latest = context.get("state_latest_run_id")
        checks.append({"url": base + "data/snapshot.json", "status": status, "ok": snap_ok,
                       "latest_run_id": snapshot.get("latest_run_id"), "state_latest_run_id": latest,
                       "matches_state": snapshot.get("latest_run_id") == latest})
        ok = all(c["ok"] for c in checks) and checks[1]["matches_state"]
        (evidence / f"{gate['id']}.json").write_text(pretty(checks))
        paths = [f"{gate['id']}.json"]
        detail = "live site and snapshot reachable; snapshot matches state" if ok else "live check failed"
    elif kind == "evidence-file":
        target = evidence / gate["file"]
        if target.exists():
            summary = json.loads(target.read_text())
            ok = summary.get("failed", 1) == 0 and summary.get("passed", 0) >= gate.get("min_passed", 1)
            detail = f"{summary.get('passed', 0)} passed, {summary.get('failed', '?')} failed"
        else:
            detail = f"missing evidence file {gate['file']}"
        paths = [gate["file"]]
    else:
        detail = f"unknown gate kind {kind}"
    return {"id": gate["id"], "title": gate["title"], "kind": kind, "status": "pass" if ok else "fail",
            "objective": True, "evidence_paths": paths, "detail": detail,
            "seconds": round(time.monotonic() - started, 2)}


def evaluate(rubric_path: Path, lock_path: Path, evidence: Path, context: dict[str, Any],
             grader_file: Path | None) -> dict[str, Any]:
    evidence.mkdir(parents=True, exist_ok=True)
    rubric = json.loads(rubric_path.read_text())
    intact, digest = rubric_integrity(rubric_path, lock_path)
    findings = [{"id": "rubric-integrity", "title": "Rubric matches its review lock", "kind": "integrity",
                 "status": "pass" if intact else "fail", "objective": True, "evidence_paths": [],
                 "detail": digest, "seconds": 0.0}]
    if intact:
        findings += [_run_gate(g, evidence, context) for g in rubric["hard_gates"]]
    subjective: dict[str, Any] = {"status": "not_run", "reason": "no grader output supplied", "scores": []}
    if grader_file and grader_file.exists():
        graded = json.loads(grader_file.read_text())
        require_valid(graded, "grader.schema.json")
        subjective = {"status": "graded", "grader": graded["grader"], "scores": graded["scores"]}
    elif context.get("grader_reason"):
        subjective["reason"] = context["grader_reason"]
    return {"rubric_version": rubric["rubric_version"], "rubric_sha256": digest, "findings": findings,
            "subjective": subjective, "max_repair_rounds": rubric.get("max_repair_rounds", 3)}


def record(state: StateRepo, result: dict[str, Any], source_commit: str, context: dict[str, Any],
           gh: GitHub | None) -> dict[str, Any]:
    cfg = config()
    now = utcnow()
    failed = sorted(f["id"] for f in result["findings"] if f["status"] == "fail")
    batch_id = hashlib.sha256(",".join(failed).encode()).hexdigest()[:12] if failed else None
    release_id = f"{cfg['project']}-{now.strftime('%Y%m%dT%H%M%SZ')}-{source_commit[:7]}"

    def mutate(root: Path) -> dict[str, Any]:
        repairs = read_json(root, "outcomes/repairs.json", {"batches": {}})
        rounds = 0
        if batch_id:
            batch = repairs["batches"].get(batch_id, {"gates": failed, "rounds": 0, "commits": [],
                                                      "first_seen": iso(now), "issues": {}})
            if source_commit not in batch["commits"]:
                batch["commits"].append(source_commit)
                batch["rounds"] = len(batch["commits"])
            batch["last_seen"] = iso(now)
            repairs["batches"][batch_id] = batch
            rounds = batch["rounds"]
            write_json(root, "outcomes/repairs.json", repairs)
        exhausted = rounds > result["max_repair_rounds"]
        release = {
            "release_id": release_id,
            "project": cfg["project"],
            "source_commit": source_commit,
            "run_id": context.get("state_latest_run_id"),
            "created_at": iso(now),
            "status": "released" if not failed else "blocked",
            "blocked_reason": None if not failed else (
                "repair budget exhausted; human review required" if exhausted
                else f"hard gates failing: {', '.join(failed)}"),
            "defect_batch_id": batch_id,
            "repair_round": rounds,
            "rubric_sha256": result["rubric_sha256"],
            "findings": result["findings"],
            "subjective": result["subjective"],
            "evidence_dir": f"outcomes/{release_id}/",
            "workflow_run_url": context.get("run_url"),
        }
        require_valid(release, "release.schema.json")
        write_json(root, f"outcomes/{release_id}/release.json", release)
        evidence_src = Path(context["evidence_dir"])
        for file in select_evidence(evidence_src):
            dest = root / "outcomes" / release_id / file.relative_to(evidence_src)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(file.read_bytes())
        if not failed:
            write_json(root, "releases/current.json", release)
        current = (read_json(root, "releases/current.json") or {}).get("release_id")
        prune_screenshots(root, {release_id} | ({current} if current else set()))
        history = read_json(root, "releases/history.json", {"releases": []})
        history["releases"] = ([{k: release[k] for k in ("release_id", "status", "source_commit", "created_at",
                                                           "run_id")}] + history["releases"])[:100]
        write_json(root, "releases/history.json", history)
        return release

    release = state.transact(mutate, f"outcome {release_id}")
    if gh and failed:
        _assign_repairs(gh, release, failed)
    return release


def _assign_repairs(gh: GitHub, release: dict[str, Any], failed: list[str]) -> None:
    """One open `type:repair` issue per failing gate; updated, never duplicated."""
    open_titles = {i["title"]: i["number"] for i in gh.open_issues("type:repair")}
    for gate in failed:
        finding = next(f for f in release["findings"] if f["id"] == gate)
        title = f"Repair: hard gate '{gate}' failing"
        body = (f"Release `{release['release_id']}` at `{release['source_commit'][:7]}` is **{release['status']}**.\n\n"
                f"- Gate: {finding['title']}\n- Detail: {finding['detail']}\n"
                f"- Repair round: {release['repair_round']} of max\n"
                f"- Evidence: state branch `{release['evidence_dir']}`\n\n"
                "Repairs may change implementation inside this gate's scope only. Changes to permissions, cost "
                "ceilings, trust boundaries, the rubric or release gates need explicit owner review.")
        if title in open_titles:
            gh.comment(open_titles[title], body)
        else:
            gh.create_issue(title, body, ["type:repair"])
