"""Issue intake: turn a `type:run` issue into a validated dispatch, or explain why not.

Authorization is by identity, never by labels (issue-form labels are applied for any author):
- the issue author is the repository owner, or
- the owner (event sender) applied `job:approved` to another account's request.
Anything else is labelled job:blocked with an explanation. No paid or secret-bearing work is
reachable from this path; dispatching the deterministic run workflow is the only effect.
"""

from __future__ import annotations

from typing import Any

from .core import config
from .issueform import to_params


def decide(event: dict[str, Any]) -> dict[str, Any]:
    cfg = config()
    action = event.get("action")
    issue = event.get("issue") or {}
    labels = {lab["name"] for lab in issue.get("labels", [])}
    owner = event["repository"]["owner"]["login"]
    sender = (event.get("sender") or {}).get("login")
    author = (issue.get("user") or {}).get("login")
    number = issue.get("number")

    if "pull_request" in issue or "type:run" not in labels or issue.get("state") != "open":
        return {"action": "ignore", "issue": number}
    if action == "labeled":
        if (event.get("label") or {}).get("name") != "job:approved":
            return {"action": "ignore", "issue": number}
        if sender != owner:
            return {"action": "blocked", "issue": number,
                    "message": "Only the repository owner can approve a run request."}
        authorized = True
    elif action == "edited":
        # Re-validate only requests that were blocked as invalid; never re-launch running work.
        if "job:blocked" not in labels:
            return {"action": "ignore", "issue": number}
        authorized = author == owner or "job:approved" in labels and sender == owner
    elif action == "opened":
        authorized = author == owner
    else:
        return {"action": "ignore", "issue": number}

    params, errors = to_params(issue.get("body") or "", cfg["form"], cfg.get("constants", {}))
    if not errors:
        from .runs import request_errors

        errors = request_errors(params)
    if errors:
        return {"action": "invalid", "issue": number, "errors": errors,
                "message": "The request did not validate. Edit the issue to fix these fields:"}
    if not authorized:
        return {"action": "blocked", "issue": number,
                "message": ("Runs are launched only for the repository owner. The owner can review this "
                            "request and add the `job:approved` label to run it.")}
    return {"action": "dispatch", "issue": number, "params": params,
            "actor": author if action == "opened" else sender}
