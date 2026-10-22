"""Memory Curation Workflow (a local-harness equivalent of "dreaming"-style memory; not a native
Claude product feature).

propose: deterministic candidate lessons from completed runs (project hook), deduplicated
         against accepted lessons, contradictions and expired entries flagged, posted as one
         `type:memory` issue with a machine-readable block.
accept:  only when the repository owner labels that issue `memory:accept`; entries are re-validated
         and merged into memory/lessons.json on the default branch.

Hard guard: lessons can never describe security boundaries, cost limits, acceptance thresholds,
publication permissions or user requirements (scope enum + text screen). Those live in code,
the rubric and the docs, and change only through reviewed commits.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from .core import ROOT, HarnessError, config, pretty, schema_errors
from .github import GitHub
from .runs import hooks
from .state import StateRepo

LESSONS = "memory/lessons.json"
BLOCK = re.compile(r"```json memory-proposals\n(.*?)\n```", re.S)
PROTECTED = re.compile(
    r"\b(permission|secret|token|credential|budget|ceiling|threshold|rubric|release gate|publish|"
    r"publication|security boundary|trust boundary|requirement)s?\b", re.I)


def entry_id(topic: str, finding: str) -> str:
    return "mem-" + hashlib.sha256(f"{topic}\n{finding}".encode()).hexdigest()[:10]


def load_lessons(root: Path = ROOT) -> dict[str, Any]:
    path = root / LESSONS
    return json.loads(path.read_text()) if path.exists() else {"schema_version": "1.0", "entries": []}


def screen(entry: dict[str, Any]) -> list[str]:
    problems = schema_errors(entry, "memory.schema.json")
    if PROTECTED.search(entry.get("topic", "") + " " + entry.get("finding", "")):
        problems.append("lesson touches a protected subject (security, cost, thresholds, publication, requirements)")
    return problems


def propose(state_root: Path, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    lessons = load_lessons()
    active = {e["id"]: e for e in lessons["entries"] if e["status"] == "active"}
    by_topic = {e["topic"]: e for e in active.values()}
    adds, supersedes, retires, rejected = [], [], [], []
    for cand in hooks().memory_candidates(state_root):
        cand = {**cand, "id": entry_id(cand["topic"], cand["finding"]), "status": "active",
                "date": today.isoformat(), "supersedes": []}
        problems = screen(cand)
        if problems:
            rejected.append({"topic": cand.get("topic"), "problems": problems})
            continue
        if cand["id"] in active:
            continue  # duplicate of an accepted lesson
        old = by_topic.get(cand["topic"])
        if old:
            cand["supersedes"] = [old["id"]]
            supersedes.append({"new": cand, "old_id": old["id"], "reason": "same topic, different finding"})
        else:
            adds.append(cand)
    for entry in active.values():
        if entry.get("expires_on") and entry["expires_on"] < today.isoformat():
            retires.append({"id": entry["id"], "reason": f"expired on {entry['expires_on']}; revalidate"})
    return {"adds": adds, "supersedes": supersedes, "retires": retires, "rejected": rejected}


def render_issue(proposal: dict[str, Any]) -> str:
    lines = ["Proposed operational lessons from completed runs. Generated deterministically; nothing is",
             "accepted until the repository owner adds the `memory:accept` label.", ""]
    for cand in proposal["adds"]:
        lines.append(f"- **Add** `{cand['id']}` ({cand['confidence']}): {cand['topic']}: {cand['finding']}")
    for sup in proposal["supersedes"]:
        lines.append(f"- **Supersede** `{sup['old_id']}` with `{sup['new']['id']}`: {sup['new']['finding']}")
    for ret in proposal["retires"]:
        lines.append(f"- **Retire** `{ret['id']}`: {ret['reason']}")
    for rej in proposal["rejected"]:
        lines.append(f"- Rejected by guard: {rej['topic']}: {'; '.join(rej['problems'])}")
    lines += ["", "```json memory-proposals", json.dumps(proposal, sort_keys=True, indent=1), "```"]
    return "\n".join(lines)


def post(gh: GitHub, proposal: dict[str, Any]) -> int | None:
    if not (proposal["adds"] or proposal["supersedes"] or proposal["retires"]):
        return None
    body = render_issue(proposal)
    for issue in gh.open_issues("type:memory"):
        if issue["user"]["login"] in ("github-actions[bot]",):
            gh.update_issue(issue["number"], body=body)
            return int(issue["number"])
    return gh.create_issue("Memory curation proposals", body, ["type:memory"])


def apply(lessons: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    entries = {e["id"]: e for e in lessons["entries"]}
    for cand in proposal["adds"] + [s["new"] for s in proposal["supersedes"]]:
        problems = screen(cand)
        if problems:
            raise HarnessError(f"refusing lesson {cand.get('id')}: {'; '.join(problems)}")
        entries[cand["id"]] = cand
    for sup in proposal["supersedes"]:
        if sup["old_id"] in entries:
            entries[sup["old_id"]] = {**entries[sup["old_id"]], "status": "superseded",
                                      "superseded_by": sup["new"]["id"]}
    for ret in proposal["retires"]:
        if ret["id"] in entries:
            entries[ret["id"]] = {**entries[ret["id"]], "status": "retired"}
    return {"schema_version": "1.0", "entries": sorted(entries.values(), key=lambda e: (e["topic"], e["id"]))}


def render_markdown(lessons: dict[str, Any]) -> str:
    out = ["# Accepted operational lessons", "",
           "Curated by the Memory Curation Workflow and accepted by the repository owner. Each lesson",
           "names its supporting runs and when it must be revalidated.", ""]
    for e in lessons["entries"]:
        if e["status"] != "active":
            continue
        out += [f"## {e['topic']}", "", e["finding"], "",
                f"- Scope: {e['scope']} | Confidence: {e['confidence']} | Date: {e['date']}",
                f"- Revalidate: {e['revalidate_when']}"
                + (f" (expires {e['expires_on']})" if e.get("expires_on") else ""),
                f"- Supporting runs: {', '.join(e['supporting_run_ids'])}",
                f"- Evidence: {', '.join(e['evidence_links'])}", ""]
    retired = [e for e in lessons["entries"] if e["status"] != "active"]
    if retired:
        out += ["## Superseded or retired", ""] + [f"- `{e['id']}` {e['topic']} ({e['status']})" for e in retired]
    return "\n".join(out) + "\n"


def accept(event: dict[str, Any], main: StateRepo) -> dict[str, Any] | None:
    owner = event["repository"]["owner"]["login"]
    issue = event["issue"]
    labels = {lab["name"] for lab in issue["labels"]}
    if event.get("label", {}).get("name") != "memory:accept" or "type:memory" not in labels:
        return None
    if event["sender"]["login"] != owner:
        raise HarnessError("only the repository owner can accept memory proposals")
    match = BLOCK.search(issue.get("body") or "")
    if not match:
        raise HarnessError("no machine-readable proposal block in the issue")
    proposal = json.loads(match.group(1))

    def mutate(root: Path) -> dict[str, Any]:
        lessons = apply(load_lessons(root), proposal)
        (root / "memory").mkdir(exist_ok=True)
        (root / LESSONS).write_text(pretty(lessons))
        (root / "memory" / "LESSONS.md").write_text(render_markdown(lessons))
        return lessons

    lessons = main.transact(mutate, f"memory: accept proposals from #{issue['number']}")
    return {"accepted": len([e for e in lessons["entries"] if e["status"] == "active"]),
            "project": config()["project"]}
