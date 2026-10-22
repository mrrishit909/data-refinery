"""Output contract: structural validation of a run directory, and the plain-text report."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

from .contract import ROOT, load_contract

# One run's outputs, enforced by pipeline.run before anything is written. The harness caps a whole run directory at
# 2,000,000 bytes (project.json max_run_bytes); the remainder is left for files the harness adds.
MAX_TOTAL_BYTES = 1_900_000
JSON_KEYS = {
    "manifest.json": ["params", "sources", "contract_version", "fx_table", "package_version", "input_manifest_hash"],
    "profiles.json": ["batches"],
    "drift.json": ["batches"],
    "repairs.json": ["repair_mode", "repairs", "summary"],
    "ambiguities.json": ["ambiguities"],
    "plan.json": ["repair_mode", "batches"],
    "reconciliation.json": ["identity", "batches", "periods", "totals"],
    "lineage.json": ["nodes", "edges", "columns"],
    "quality.json": ["definitions", "batches"],
    "samples.json": ["batches"],
    "checks.json": ["all_passed", "checks"],
}
REPAIR_KEYS = ["id", "batch", "kind", "detail", "evidence", "confidence", "auto_applicable", "ambiguity", "applied"]
QUARANTINE_HEADER = ["_batch", "_source_file", "_source_row", "reason_codes", "reason_detail", "raw"]
FILES = [*JSON_KEYS, "cleaned.csv", "quarantine.csv", "report.md"]


def _csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def validate(outdir: Path) -> list[str]:
    """Problems with a run directory; an empty list means the outputs satisfy the output contract."""
    outdir = Path(outdir)
    problems = [f"missing {name}" for name in FILES if not (outdir / name).is_file()]
    if problems:
        return problems
    data = {}
    for name, keys in JSON_KEYS.items():
        try:
            data[name] = json.loads((outdir / name).read_text(encoding="utf-8"))
        except ValueError as err:
            problems.append(f"{name}: invalid JSON ({err})")
            continue
        problems += [f"{name}: missing key {k}" for k in keys if k not in data[name]]
    if problems:
        return problems
    manifest = data["manifest.json"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(manifest["input_manifest_hash"])):
        problems.append("manifest.json: input_manifest_hash is not sha256:<hex>")
    for r in data["repairs.json"]["repairs"]:
        missing = [k for k in REPAIR_KEYS if k not in r]
        if missing:
            problems.append(f"repairs.json: {r.get('id')} missing {missing}")
        elif not 0 <= r["confidence"] <= 1 or (r["applied"] and not r["auto_applicable"]):
            problems.append(f"repairs.json: {r['id']} has invalid confidence or applied a non-safe repair")
    contract = load_contract(manifest["contract_version"])
    expected = [*contract.names, "amount_usd", "restates_period", "_batch", "_source_file", "_source_row"]
    header, cleaned = _csv(outdir / "cleaned.csv")
    if header != expected:
        problems.append(f"cleaned.csv: header {header} != {expected}")
    qheader, quarantine = _csv(outdir / "quarantine.csv")
    if qheader != QUARANTINE_HEADER:
        problems.append(f"quarantine.csv: header {qheader} != {QUARANTINE_HEADER}")
    accepted = Counter(r["_batch"] for r in cleaned)
    held = Counter(r["_batch"] for r in quarantine)
    batches = data["reconciliation.json"]["batches"]
    if sorted(batches) != sorted(manifest["params"]["batches"]):
        problems.append("reconciliation.json: batches differ from the request")
    for b, rec in batches.items():
        if rec["rows_in"] != rec["rows_accepted"] + rec["rows_quarantined"] + rec["rows_merged_duplicates"]:
            problems.append(f"reconciliation.json: identity fails for {b}")
        if rec["rows_accepted"] != accepted[b] or rec["rows_quarantined"] != held[b]:
            problems.append(f"reconciliation.json: {b} counts disagree with cleaned.csv/quarantine.csv")
        if rec["rows_merged_duplicates"] != len(rec["merged_duplicates"]):
            problems.append(f"reconciliation.json: {b} merged list length disagrees")
    if any(not r["reason_codes"] for r in quarantine):
        problems.append("quarantine.csv: a row has no reason code")
    if not data["checks.json"]["all_passed"]:
        failed = [c["id"] for c in data["checks.json"]["checks"] if not c["passed"]]
        problems.append(f"checks.json: self-checks failed: {failed}")
    total = sum((outdir / n).stat().st_size for n in FILES)
    if total > MAX_TOTAL_BYTES:
        problems.append(f"outputs total {total} bytes exceeds {MAX_TOTAL_BYTES}")
    for name in FILES:
        if str(ROOT) in (outdir / name).read_text(encoding="utf-8"):
            problems.append(f"{name}: contains an absolute path")
    return problems


def _describe(r: dict) -> str:
    d = r["detail"]
    return {
        "rename": lambda: f"rename column {d['from']} to {d['to']}",
        "date_format": lambda: f"read {d['column']} as {d['format']} ({d['rows_affected']} values)",
        "unit_scale": lambda: f"convert {d['column']} from {d['from_unit']} to {d['to_unit']} (x{d['factor']})",
        "category_map": lambda: f"map {d['column']} {d['from']!r} to {d['to'] or '?'} ({d['rule']}, "
                                f"{d['rows_affected']} rows)",
    }[r["kind"]]()


def render_report(params: dict, recon: dict, repairs: list[dict], ambiguities: list[dict], checks: list[dict]) -> str:
    lines = [
        "# Data Refinery run report",
        "",
        f"Batches {', '.join(params['batches'])}; repair mode `{params['repair_mode']}`; contract "
        f"`{params['contract']}`; FX table `{params['fx_table']}` (synthetic fixed rates).",
        "",
        "## Accounting",
        "",
        "| Batch | Rows in | Accepted | Quarantined | Merged duplicates | Identity holds |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for b, r in recon["batches"].items():
        lines.append(f"| {b} | {r['rows_in']} | {r['rows_accepted']} | {r['rows_quarantined']} | "
                     f"{r['rows_merged_duplicates']} | {'yes' if r['identity_holds'] else 'NO'} |")
    lines += ["", "## What was found", ""]
    for b, r in recon["batches"].items():
        reasons = ", ".join(f"{k} {v}" for k, v in r["quarantine_reasons"].items()) or "none"
        lines.append(f"- {b}: quarantine reasons: {reasons}; merged exact duplicates: {r['rows_merged_duplicates']}.")
    applied = [r for r in repairs if r["applied"]]
    proposed = [r for r in repairs if not r["applied"] and r["auto_applicable"]]
    lines += ["", "## What was repaired", ""]
    lines += [f"- {r['id']}: {_describe(r)}, confidence {r['confidence']}." for r in applied] or ["- Nothing."]
    if proposed:
        lines += ["", "## Safe repairs proposed but not applied (propose mode)", ""]
        lines += [f"- {r['id']}: {_describe(r)}, confidence {r['confidence']}." for r in proposed]
    lines += ["", "## What needs a human", ""]
    lines += [f"- {a['repair']} ({a['batch']}): {a['reason']}" for a in ambiguities] or ["- Nothing."]
    lines += ["", "## Late arrivals and restatements", ""]
    restated = [(p, x) for p, e in recon["periods"].items() for x in e["restatements"]]
    lines += [f"- {p} restated by {x['batch']}: {x['rows']} late rows, {x['late_usd']} USD; period total "
              f"{x['before_usd']} -> {x['after_usd']} USD." for p, x in restated] or ["- None."]
    failed = [c["id"] for c in checks if not c["passed"]]
    lines += ["", "## Self-checks", "",
              f"- {len(checks) - len(failed)} of {len(checks)} passed" + (f"; failed: {', '.join(failed)}." if failed
                                                                        else ".")]
    lines += [
        "", "## Limitations", "",
        "- All data is synthetic, generated deterministically by `datarefinery.fixtures`; nothing here describes real "
        "orders or customers.",
        "- `amount_usd` uses one fixed, synthetic FX table for every date; it is not market data.",
        "- One contract (`orders.v1`); there is no schema evolution or contract negotiation.",
        "- Category repairs are lexical rules (case, plural, single-edit typo). There is no semantic model, so "
        "anything else is left to a human.",
        "- Unit-scale drift is judged per batch against the previous accepted batch, or the contract's declared "
        "reference when no earlier batch is in the request. A minority of rows in another unit inside an otherwise "
        "consistent batch is not detected; converted values far outside the baseline are held as "
        "UNRESOLVED_UNIT_SCALE.",
        "- Day-first or month-first dates are converted only when exactly one declared format reads every non-ISO "
        "value and every result falls in the batch month; otherwise those rows are held as BAD_DATE, including "
        "genuine late arrivals written that way.",
        "- Spreadsheet formulas are not evaluated (their text is the raw value); JSON objects that repeat a key, "
        "files that are not UTF-8, and runs whose outputs would exceed the per-run byte budget are rejected.",
        "- Duplicates are detected within a batch and against ids accepted earlier in the same run only.",
        "",
    ]
    return "\n".join(lines)
