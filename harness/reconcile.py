"""Reconciler for interrupted workflows (runs on a schedule and on demand).

Bounded: at most `limits.max_reconcile_actions` corrective actions per invocation.
"""

from __future__ import annotations

import io
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from .core import config, iso, parse_iso, transition, utcnow
from .github import GitHub
from .runs import publish
from .state import StateRepo, put_request, put_run_record, read_json, upsert_index


def _safe_extract(data: bytes, dest: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            if not str(target).startswith(str(dest.resolve())) or member.is_dir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(member))


def reconcile(state: StateRepo, gh: GitHub, now=None) -> list[dict[str, Any]]:
    cfg = config()
    limits = cfg["limits"]
    now = now or utcnow()
    state.sync()
    index = read_json(state.path, "index.json", {"runs": []})
    actions: list[dict[str, Any]] = []
    stale_cutoff = now - timedelta(minutes=limits["stale_running_minutes"])

    for entry in index["runs"]:
        if len(actions) >= limits["max_reconcile_actions"]:
            break
        if entry["status"] != "running":
            continue
        rec = read_json(state.path, f"runs/{entry['run_id']}/run.json")
        if not rec or parse_iso(rec["started_at"]) > stale_cutoff:
            continue
        wf = gh.workflow_run(rec["workflow_run_id"]) if rec["workflow_run_id"] else None
        if wf and wf["status"] != "completed":
            continue  # still genuinely running or queued on GitHub's side
        conclusion = (wf or {}).get("conclusion") or "unknown"
        recovered = False
        if wf:
            for art in gh.run_artifacts(rec["workflow_run_id"]):
                if art["name"] == f"run-{rec['run_id']}" and not art["expired"]:
                    with tempfile.TemporaryDirectory() as tmp:
                        _safe_extract(gh.download_artifact(art["id"]), Path(tmp))
                        result = publish(state, rec["run_id"], "success", Path(tmp), None,
                                         gh.repo.run_url(rec["workflow_run_id"]), now)
                    actions.append({"run_id": rec["run_id"], "action": "republished_from_artifact",
                                    "status": result["status"]})
                    recovered = True
                    break
        if recovered:
            continue
        status = "cancelled" if conclusion == "cancelled" else "failed"

        def mutate(root: Path, rec=rec, status=status, conclusion=conclusion) -> None:
            current = read_json(root, f"runs/{rec['run_id']}/run.json")
            if current["status"] != "running":
                return
            new = transition(current, status, completed_at=iso(now),
                             error_summary=f"reconciled: workflow concluded '{conclusion}' without publishing")
            put_run_record(root, new)
            req = read_json(root, f"requests/{new['request_id']}.json")
            put_request(root, new["request_id"], req["params_hash"], new)
            upsert_index(root, new)

        state.transact(mutate, f"reconcile {rec['run_id']}")
        actions.append({"run_id": rec["run_id"], "action": f"marked_{status}", "conclusion": conclusion})

    # Issue ledger: re-sync labels from state and time out lost dispatches.
    by_issue: dict[int, str] = {}
    for entry in reversed(read_json(state.path, "index.json", {"runs": []})["runs"]):
        if entry.get("issue"):
            by_issue[entry["issue"]] = entry["status"]
    for issue in gh.open_issues("type:run"):
        if len(actions) >= limits["max_reconcile_actions"]:
            break
        labels = {lab["name"] for lab in issue["labels"]}
        status = by_issue.get(issue["number"])
        if status and f"job:{status}" not in labels:
            gh.set_status(issue["number"], status)
            actions.append({"issue": issue["number"], "action": "label_synced", "status": status})
        elif not status and "job:requested" in labels and parse_iso(issue["updated_at"]) < now - timedelta(hours=2):
            gh.set_status(issue["number"], "blocked")
            gh.comment(issue["number"], "The dispatched run never started (no run record after 2 hours). "
                                        "Edit or re-open the request to try again.")
            actions.append({"issue": issue["number"], "action": "dispatch_timed_out"})
    return actions
