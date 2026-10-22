from harness.intake import decide
from harness.issueform import sections

BODY = """### Batches

- [X] 2026-01 (CSV, clean baseline)
- [X] 2026-02 (CSV, renamed column, mixed dates, duplicates)
- [ ] 2026-03 (JSON, currencies, unit change, categories, nulls)
- [ ] 2026-04 (XLSX, late arrivals, missing identifiers)

### Repair mode

apply-safe
"""


def event(action="opened", author="mrrishit909", sender=None, body=BODY, labels=("type:run",), label=None):
    ev = {
        "action": action,
        "repository": {"owner": {"login": "mrrishit909"}},
        "sender": {"login": sender or author},
        "issue": {"number": 7, "state": "open", "user": {"login": author}, "body": body,
                  "labels": [{"name": n} for n in labels]},
    }
    if label:
        ev["label"] = {"name": label}
    return ev


def test_sections_handles_no_response():
    assert sections("### A\n\n_No response_\n") == {"A": None}


def test_owner_request_dispatches_exact_params():
    d = decide(event())
    assert d["action"] == "dispatch"
    assert d["params"] == {"batches": ["2026-01", "2026-02"], "repair_mode": "apply-safe",
                           "contract": "orders.v1", "fx_table": "fixed-2026q1"}


def test_non_owner_blocked_regardless_of_labels():
    assert decide(event(author="someone", labels=("type:run", "job:approved")))["action"] == "blocked"


def test_approval_only_from_owner():
    assert decide(event(action="labeled", author="x", sender="x", label="job:approved"))["action"] == "blocked"
    assert decide(event(action="labeled", author="x", sender="mrrishit909", label="job:approved"))["action"] == "dispatch"


def test_injection_in_free_text_is_rejected():
    d = decide(event(body=BODY.replace("apply-safe", "apply-safe; rm -rf /")))
    assert d["action"] == "invalid" and all("rm -rf" not in e for e in d["errors"])


def test_unknown_mode_fails_schema():
    d = decide(event(body=BODY.replace("apply-safe", "apply-everything")))
    assert d["action"] == "invalid"


def test_no_batch_selected_is_invalid():
    d = decide(event(body=BODY.replace("[X]", "[ ]")))
    assert d["action"] == "invalid"


def test_ignores_issues_without_run_label():
    assert decide(event(labels=()))["action"] == "ignore"
