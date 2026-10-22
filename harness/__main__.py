"""Command line entry points used by the workflows: `python -m harness <command>`.

Untrusted values (issue bodies, dispatch inputs) arrive through files or environment variables
and are parsed as JSON here, never interpolated into shell commands.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core import HarnessError, Repo, config, pretty


def _out(**values: str) -> None:
    """Write step outputs for later jobs (values are simple tokens, never free text)."""
    path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{k}={v}" for k, v in values.items()]
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def _summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def cmd_labels(_: argparse.Namespace) -> None:
    from .github import GitHub
    created = GitHub().ensure_labels()
    print("created labels:", ", ".join(created) or "none")


def cmd_intake(args: argparse.Namespace) -> None:
    from .github import GitHub
    from .intake import decide

    event = json.loads(Path(args.event).read_text())
    decision = decide(event)
    _out(action=decision["action"])
    if decision["action"] == "ignore":
        return
    gh = GitHub()
    number = decision["issue"]
    if decision["action"] == "invalid":
        gh.set_status(number, "blocked")
        gh.comment(number, decision["message"] + "\n\n" + "\n".join(f"- {e}" for e in decision["errors"][:12]))
    elif decision["action"] == "blocked":
        gh.set_status(number, "blocked")
        gh.comment(number, decision["message"])
    else:
        gh.set_status(number, "requested")
        gh.dispatch("run.yml", config().get("default_branch", "main"), {
            "request": json.dumps(decision["params"], sort_keys=True),
            "source_type": "issue",
            "issue": str(number),
        })
        gh.comment(number, "Request validated and queued. The run workflow will update this issue when it starts "
                           "and when results are published.")


def _source(args: argparse.Namespace) -> dict:
    return {"type": args.source_type, "issue_number": int(args.issue) if args.issue else None,
            "actor": os.environ.get("GITHUB_ACTOR")}


def cmd_admit(args: argparse.Namespace) -> None:
    from .runs import admit
    from .state import StateRepo

    params = json.loads(os.environ["REQUEST_JSON"])
    result = admit(StateRepo(Path(args.state_dir)), params, _source(args), int(args.workflow_run_id),
                   args.source_commit)
    Path(args.result).write_text(pretty(result))
    _out(decision=result["decision"], run_id=result.get("run_id", ""), request_id=result.get("request_id", ""))
    _summary(f"### Admission\n\n```json\n{pretty(result)}```")
    if args.issue:
        from .github import GitHub
        gh, number = GitHub(), int(args.issue)
        if result["decision"] == "run":
            gh.set_status(number, "running")
            gh.comment(number, f"Run `{result['run_id']}` started (attempt {result['attempt']}). "
                               f"Workflow: {Repo.current().run_url(int(args.workflow_run_id))}")
        elif result["decision"] == "duplicate":
            state = "in progress" if result.get("in_progress") else "already completed"
            gh.comment(number, f"Duplicate request: identical parameters are {state} as run "
                               f"`{result['run_id']}`. Nothing new was launched.")
            if not result.get("in_progress"):
                gh.set_status(number, "completed")
        else:
            gh.set_status(number, "blocked")
            gh.comment(number, f"Not run: {result.get('reason') or '; '.join(result.get('errors', []))[:1500]}")


def cmd_publish(args: argparse.Namespace) -> None:
    from .runs import publish
    from .state import StateRepo

    error = Path(args.error_file).read_text()[-4000:] if args.error_file and Path(args.error_file).exists() else None
    out_dir = Path(args.out_dir) if args.out_dir else None
    rec = publish(StateRepo(Path(args.state_dir)), args.run_id, args.conclusion, out_dir, error,
                  Repo.current().run_url(int(args.workflow_run_id)))
    _out(status=rec["status"])
    _summary(f"### Publish `{rec['run_id']}`: **{rec['status']}**\n\n{rec.get('error_summary') or ''}")
    issue = rec["request_source"]["issue_number"]
    if issue:
        from .github import GitHub
        gh = GitHub()
        gh.set_status(issue, rec["status"])
        cfg = config()
        state_url = Repo.current().blob_url(cfg["state_branch"], f"runs/{rec['run_id']}/run.json")
        if rec["status"] == "completed":
            gh.comment(issue, f"Run `{rec['run_id']}` completed and was published to the state branch "
                              f"({state_url}). The site updates after the Pages deployment in the same "
                              f"workflow: {cfg['pages_url']}#/runs/{rec['run_id']}")
        else:
            gh.comment(issue, f"Run `{rec['run_id']}` {rec['status']}: {rec.get('error_summary') or 'no detail'}\n\n"
                              f"Evidence is retained: {state_url}")


def cmd_compute(args: argparse.Namespace) -> None:
    """Compute job entry (no write token, no secrets): validate, run the project hook, validate outputs."""
    from .runs import hooks, request_errors, validate_outputs

    params = json.loads(Path(args.request).read_text())
    errors = request_errors(params)
    if errors:
        raise HarnessError("invalid request: " + "; ".join(errors[:5]))
    out = Path(args.out)
    hooks().compute(params, out)
    problems = validate_outputs(out)
    if problems:
        raise HarnessError("outputs failed validation: " + "; ".join(problems[:5]))
    print(f"computed and validated {out}")


def cmd_snapshot(args: argparse.Namespace) -> None:
    from .snapshot import build

    meta = build(Path(args.state_dir), Path(args.out), args.source_commit, args.state_commit)
    print(pretty(meta))


def cmd_reconcile(args: argparse.Namespace) -> None:
    from .github import GitHub
    from .reconcile import reconcile
    from .state import StateRepo

    actions = reconcile(StateRepo(Path(args.state_dir)), GitHub())
    _summary("### Reconciler\n\n" + ("\n".join(f"- `{json.dumps(a)}`" for a in actions) or "No action needed."))
    print(pretty(actions))


def cmd_outcome(args: argparse.Namespace) -> None:
    from .github import GitHub
    from .outcome import evaluate, record
    from .state import StateRepo, read_json

    state = StateRepo(Path(args.state_dir))
    state.sync()
    latest = (read_json(state.path, "latest.json") or {}).get("run_id")
    context = {"live_url": config()["pages_url"], "state_latest_run_id": latest, "evidence_dir": args.evidence_dir,
               "run_url": Repo.current().run_url(int(args.workflow_run_id)) if args.workflow_run_id else None,
               "grader_reason": args.grader_reason}
    grader = Path(args.grader_file) if args.grader_file else None
    result = evaluate(Path("rubric/outcome-rubric.json"), Path("rubric/rubric.lock"), Path(args.evidence_dir),
                      context, grader)
    release = record(state, result, args.source_commit, context, GitHub() if args.ledger else None)
    _out(status=release["status"], release_id=release["release_id"])
    rows = "\n".join(f"| {f['id']} | {f['status']} | {f['detail']} |" for f in release["findings"])
    table = "| gate | status | detail |\n|---|---|---|\n" + rows
    _summary(f"### Outcome Verification Loop: **{release['status']}**\n\n{table}")
    if release["status"] != "released" and args.strict:
        sys.exit(1)


def cmd_memory_propose(args: argparse.Namespace) -> None:
    from .github import GitHub
    from .memory import post, propose
    from .state import StateRepo

    state = StateRepo(Path(args.state_dir))
    state.sync()
    proposal = propose(state.path)
    number = post(GitHub(), proposal) if args.ledger else None
    _summary(f"### Memory curation\n\nadds={len(proposal['adds'])} supersedes={len(proposal['supersedes'])} "
             f"retires={len(proposal['retires'])} rejected={len(proposal['rejected'])} issue={number}")
    print(pretty(proposal))


def cmd_memory_accept(args: argparse.Namespace) -> None:
    from .github import GitHub
    from .memory import accept
    from .state import StateRepo

    event = json.loads(Path(args.event).read_text())
    result = accept(event, StateRepo(Path("."), branch=config().get("default_branch", "main")))
    if result:
        gh = GitHub()
        gh.comment(event["issue"]["number"], f"Accepted. memory/lessons.json now has {result['accepted']} active "
                                             "lessons.")
        gh.update_issue(event["issue"]["number"], state="closed")
    print(pretty(result))


def cmd_verify_published(args: argparse.Namespace) -> None:
    """Hard gate: the live site's data for the latest run is byte-identical to the state branch."""
    import hashlib
    import urllib.request

    from .runs import hooks
    from .state import read_json

    state = Path(args.state_dir)
    latest = read_json(state, "latest.json")
    if not latest:
        raise HarnessError("no latest run on the state branch")
    base = config()["pages_url"].rstrip("/") + f"/data/runs/{latest['run_id']}/"
    mismatches, checked = [], 0
    for name in hooks().PUBLIC_FILES:
        local = state / "runs" / latest["run_id"] / name
        if not local.exists():
            continue
        with urllib.request.urlopen(base + name, timeout=30) as resp:  # noqa: S310 (configured Pages URL)
            remote = resp.read()
        checked += 1
        if hashlib.sha256(remote).digest() != hashlib.sha256(local.read_bytes()).digest():
            mismatches.append(name)
    print(f"checked {checked} published files for {latest['run_id']}; mismatches: {mismatches or 'none'}")
    if mismatches or checked == 0:
        sys.exit(1)


def cmd_scheduled_request(args: argparse.Namespace) -> None:
    """Deterministic weekly request: the seed is the ISO year-week, so a repeat in the same week is a duplicate."""
    import datetime as dt

    req = json.loads(json.dumps(config()["scheduled_request"]))
    year, week, _ = dt.date.today().isocalendar()
    if req.get("dataset", {}).get("seed") == "iso_week":
        req["dataset"]["seed"] = year * 100 + week
    Path(args.out).write_text(json.dumps(req, sort_keys=True))
    print(json.dumps(req, sort_keys=True))


def cmd_dispatch(args: argparse.Namespace) -> None:
    from .github import GitHub

    request = Path(args.request_file).read_text()
    GitHub().dispatch("run.yml", config().get("default_branch", "main"),
                      {"request": request, "source_type": args.source_type, "issue": ""})
    print("dispatched run.yml")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="harness")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("labels").set_defaults(fn=cmd_labels)
    p = sub.add_parser("intake")
    p.add_argument("--event", required=True)
    p.set_defaults(fn=cmd_intake)
    for name, fn in (("admit", cmd_admit),):
        p = sub.add_parser(name)
        p.add_argument("--state-dir", required=True)
        p.add_argument("--source-type", required=True, choices=["issue", "schedule", "manual", "reconcile"])
        p.add_argument("--issue", default="")
        p.add_argument("--workflow-run-id", required=True)
        p.add_argument("--source-commit", required=True)
        p.add_argument("--result", required=True)
        p.set_defaults(fn=fn)
    p = sub.add_parser("publish")
    p.add_argument("--state-dir", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--conclusion", required=True, choices=["success", "failure", "cancelled", "skipped"])
    p.add_argument("--out-dir", default="")
    p.add_argument("--error-file", default="")
    p.add_argument("--workflow-run-id", required=True)
    p.set_defaults(fn=cmd_publish)
    p = sub.add_parser("compute")
    p.add_argument("--request", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_compute)
    p = sub.add_parser("snapshot")
    p.add_argument("--state-dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--state-commit", required=True)
    p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("reconcile")
    p.add_argument("--state-dir", required=True)
    p.set_defaults(fn=cmd_reconcile)
    p = sub.add_parser("outcome")
    p.add_argument("--state-dir", required=True)
    p.add_argument("--evidence-dir", required=True)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--workflow-run-id", default="")
    p.add_argument("--grader-file", default="")
    p.add_argument("--grader-reason", default="")
    p.add_argument("--ledger", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(fn=cmd_outcome)
    p = sub.add_parser("memory-propose")
    p.add_argument("--state-dir", required=True)
    p.add_argument("--ledger", action="store_true")
    p.set_defaults(fn=cmd_memory_propose)
    p = sub.add_parser("memory-accept")
    p.add_argument("--event", required=True)
    p.set_defaults(fn=cmd_memory_accept)
    p = sub.add_parser("verify-published")
    p.add_argument("--state-dir", required=True)
    p.set_defaults(fn=cmd_verify_published)
    p = sub.add_parser("scheduled-request")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_scheduled_request)
    p = sub.add_parser("dispatch")
    p.add_argument("--request-file", required=True)
    p.add_argument("--source-type", required=True, choices=["schedule", "manual", "reconcile"])
    p.set_defaults(fn=cmd_dispatch)
    args = parser.parse_args(argv)
    try:
        args.fn(args)
    except HarnessError as exc:
        print(f"harness error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
