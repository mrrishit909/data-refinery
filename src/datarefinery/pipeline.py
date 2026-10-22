"""Batch processing: transformation, deduplication, contract validation, FX, late arrivals and the run orchestrator."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from statistics import median

import pandas as pd
from jsonschema import Draft202012Validator

from . import __version__
from .analysis import AMBIGUOUS_RULES, analyse_batch, classify_label, date_candidates, profile_frame, rnd
from .contract import (
    FIXTURES_DIR,
    SCHEMAS_DIR,
    Contract,
    FxTable,
    contract_failures,
    contract_path,
    file_sha256,
    fx_path,
    load_contract,
    load_fx,
    normalize_cell,
    parse_number,
)
from .intake import IntakeError, find_source, read_batch
from .outputs import MAX_TOTAL_BYTES, render_report

LINEAGE_COLUMNS = ["_batch", "_source_file", "_source_row"]
DERIVED_COLUMNS = ["amount_usd", "restates_period"]
QUARANTINE_COLUMNS = [*LINEAGE_COLUMNS, "reason_codes", "reason_detail", "raw"]
DUPLICATE_CODES = {"DUPLICATE_CONFLICT", "DUPLICATE_PRIOR_BATCH"}
REASON_CODES = {
    "MISSING_ID": "order_id is null or blank",
    "BAD_PATTERN": "identifier does not match the contract pattern",
    "BAD_DATE": "date does not parse as ISO or under an applied date-format repair",
    "BAD_NUMBER": "not a number, or more decimal places than the contract scale",
    "NULL_REQUIRED": "required value is null",
    "INVALID_CATEGORY": "category outside the contract with no applied mapping",
    "AMBIGUOUS_CATEGORY": "category whose meaning is undetermined; never auto-mapped",
    "INVALID_ENUM": "enum value outside the contract",
    "NEGATIVE_AMOUNT": "amount below zero",
    "OUT_OF_RANGE": "number outside the contract range, or more than 15 integer digits",
    "DUPLICATE_CONFLICT": "order_id repeated with different content; every copy is quarantined",
    "DUPLICATE_PRIOR_BATCH": "order_id already accepted from an earlier batch in this run",
    "UNKNOWN_COLUMN_SET": "a required contract column is absent and no rename was applied",
    "UNRESOLVED_UNIT_SCALE": "column looks like a different unit and the conversion was not applied, or the "
                             "value is implausible after conversion",
}


class RequestError(ValueError):
    pass


class OutputBudgetError(IntakeError):
    """The request's inputs would produce more output than one run may hold."""


def request_problems(params: dict) -> list[str]:
    schema = json.loads((SCHEMAS_DIR / "request.schema.json").read_text())
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
              for e in sorted(Draft202012Validator(schema).iter_errors(params), key=lambda e: list(e.absolute_path))]
    if not errors and params["batches"] != sorted(params["batches"]):
        errors.append("batches: must be listed in ascending order")
    return errors


@dataclass
class BatchResult:
    batch: str
    source_file: str
    raw: pd.DataFrame
    canonical: pd.DataFrame
    reasons: dict[int, list[dict]]
    merged: dict[int, int]
    accepted: list[int]
    quarantined: list[int]
    changed: dict[tuple[int, str], str]
    steps: list[dict]
    applied: list[str]
    skipped: list[dict]
    column_sources: dict[str, str | None]
    metrics: dict = field(default_factory=dict)


def _message(code: str, column: str | None, value, contract: Contract) -> str:
    v = "null" if value is None else repr(value)
    col = contract.col(column) if column in contract.names else None
    text = {
        "MISSING_ID": "order_id is blank",
        "BAD_PATTERN": f"{column} {v} does not match {col.pattern if col else ''}",
        "BAD_DATE": f"{column} {v} is not an ISO date"
                    + (f" in years {col.year_range[0]}-{col.year_range[1]}" if col and col.year_range else ""),
        "BAD_NUMBER": f"{column} {v} is not a number with at most {col.scale or 0} decimals" if col else "",
        "NULL_REQUIRED": f"{column} is null",
        "INVALID_CATEGORY": f"{column} {v} is not a contract category",
        "AMBIGUOUS_CATEGORY": f"{column} {v} has an ambiguous meaning",
        "INVALID_ENUM": f"{column} {v} is not an allowed value",
        "NEGATIVE_AMOUNT": f"amount {v} is negative",
        "OUT_OF_RANGE": f"{column} {v} is out of range",
        "UNRESOLVED_UNIT_SCALE": f"{column} {v} is implausible after unit conversion; its unit is unclear",
    }.get(code, "")
    if isinstance(value, str) and value.startswith("="):
        text += " (unevaluated spreadsheet formula)"
    return text


def _trim(v):
    return None if v is None or v.strip() == "" else v.strip()


def process_batch(batch: str, raw: pd.DataFrame, source_file: str, contract: Contract, fx: FxTable,
                  repairs: list[dict], detected_units: dict[str, str], prior_ids: dict[str, str],
                  closed_periods: set[str], unmapped: dict[str, str] | None = None) -> BatchResult:
    """Transform one raw batch with the given repairs and route every row to accepted, quarantined or merged.

    repairs: repairs to apply (each checks its own guard against this batch). detected_units: column -> unit for
    detected unit shifts; unless a unit_scale repair converted that column, rows with a value there are quarantined.
    unmapped: rename candidates (source column -> contract column); if the contract column ends up unmapped, rows
    with a value in the source column are quarantined instead of silently losing that value.
    """
    n = len(raw)
    rows = list(raw.index)
    steps: list[dict] = []
    applied: list[str] = []
    skipped: list[dict] = []

    def step(name, kind, columns, rows_in=n, rows_out=n, changed=0, repair=None, **detail):
        steps.append({"order": len(steps) + 1, "name": name, "kind": kind, "repair": repair, "columns": columns,
                      "rows_in": rows_in, "rows_out": rows_out, "rows_changed": changed, "detail": detail})

    step("intake", "base", ["*"], file=source_file)
    frame = raw
    for r in (r for r in repairs if r["kind"] == "rename"):
        src, dst = r["detail"]["from"], r["detail"]["to"]
        if src in frame.columns and dst not in frame.columns:
            frame = frame.rename(columns={src: dst})
            applied.append(r["id"])
            step(f"rename {src} -> {dst}", "repair", [dst], changed=n, repair=r["id"])
        else:
            skipped.append({"repair": r["id"], "reason": f"column {src} absent or {dst} already present"})
    renames = {r["detail"]["to"]: r for r in repairs if r["id"] in applied}
    renamed_from = {to: r["detail"]["from"] for to, r in renames.items()}
    column_sources = {c: (renamed_from.get(c, c) if c in frame.columns else None) for c in contract.names}
    missing_required = [c for c in contract.names if column_sources[c] is None and contract.col(c).required]
    extra = [c for c in frame.columns if c not in contract.names]
    step("conform_columns", "base", ["*"], missing_required=missing_required,
         missing_optional=[c for c in contract.names if column_sources[c] is None and c not in missing_required],
         ignored_extra_columns=extra)

    source_values = {c: list(frame[c]) for c in contract.names if column_sources[c]}
    trimmed = {c: [_trim(v) for v in source_values[c]] if column_sources[c] else [None] * n for c in contract.names}
    changed_rows = sum(any(trimmed[c][i] != vals[i] for c, vals in source_values.items()) for i in range(n))
    step("trim_blank_to_null", "base", ["*"], changed=changed_rows)

    def active(kind):
        return [r for r in repairs if r["kind"] == kind]

    date_fmt: dict[str, tuple[str, str]] = {}
    for r in active("date_format"):
        col = r["detail"]["column"]
        if not column_sources.get(col) or col in date_fmt:
            skipped.append({"repair": r["id"], "reason": f"column {col} absent or already has a date repair"})
            continue
        dc = date_candidates([v for v in trimmed[col] if v is not None], contract.col(col).alternate_formats, batch)
        if dc["safe_format"] == r["detail"]["format"]:
            date_fmt[col] = (r["detail"]["format"], r["id"])
        elif not dc["parseable"]:
            skipped.append({"repair": r["id"], "reason": f"no non-ISO {col} values that a declared format reads"})
        else:
            skipped.append({"repair": r["id"], "reason": f"guard not met: {r['detail']['format']} is not the only "
                                                         f"declared format that reads every non-ISO {col} value "
                                                         f"into {batch}"})
    factors: dict[str, tuple[Decimal, str]] = {}
    plausible: dict[str, dict] = {}
    for r in active("unit_scale"):
        col = r["detail"]["column"]
        nums = [x for x in (parse_number(v) for v in trimmed.get(col, [])) if x is not None]
        g = r["detail"]["guard"]
        if col in factors or not column_sources.get(col):
            skipped.append({"repair": r["id"], "reason": f"column {col} absent or already converted"})
        elif nums and g["median_min"] <= median(nums) <= g["median_max"]:
            factors[col] = (Decimal(r["detail"]["factor"]), r["id"])
            plausible[col] = r["detail"].get("plausible_after", {})
        else:
            skipped.append({"repair": r["id"], "reason": f"guard not met: {col} median outside "
                                                         f"[{g['median_min']}, {g['median_max']}]"})
    cat_maps: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    for r in active("category_map"):
        cat_maps[r["detail"]["column"]][r["detail"]["from"]] = (r["detail"]["to"], r["id"])

    canonical: dict[str, list] = {}
    cell_errors: dict[tuple[int, str], str] = {}
    changed: dict[tuple[int, str], str] = {(rows[i], to): r["id"] for to, r in renames.items()
                                           for i, v in enumerate(trimmed[to]) if v is not None}
    reformatted: Counter = Counter()
    per_repair: Counter = Counter()
    for col in contract.columns:
        fmt, fmt_id = date_fmt.get(col.name, (None, None))
        factor, factor_id = factors.get(col.name, (None, None))
        cmap = {k: v[0] for k, v in cat_maps.get(col.name, {}).items()}
        out = []
        for i, v in enumerate(trimmed[col.name]):
            value, error, by_repair = normalize_cell(col, v, fmt, factor, cmap)
            band = plausible.get(col.name)
            if by_repair and factor is not None and band and not band["min"] <= float(value) <= band["max"]:
                value, error = None, "UNRESOLVED_UNIT_SCALE"
            out.append(value)
            if error:
                cell_errors[(rows[i], col.name)] = error
            elif by_repair:
                rid = fmt_id or factor_id or cat_maps[col.name][v][1]
                changed[(rows[i], col.name)] = rid
                per_repair[rid] += 1
            elif value != v:
                reformatted[col.name] += 1
        canonical[col.name] = out
    step("parse_declared_types", "base", ["*"], changed=sum(reformatted.values()),
         reformatted=dict(sorted(reformatted.items())),
         parse_errors=dict(sorted(Counter(c for _, c in cell_errors).items())))
    by_id = {r["id"]: r for r in repairs}
    for rid in [*[v[1] for v in date_fmt.values()], *[v[1] for v in factors.values()],
                *sorted({v[1] for m in cat_maps.values() for v in m.values()})]:
        r = by_id[rid]
        if r["kind"] == "category_map" and not per_repair[rid]:
            skipped.append({"repair": rid, "reason": f"no cells equal to {r['detail']['from']!r}"})
            continue
        if r["kind"] == "date_format" and not per_repair[rid]:
            skipped.append({"repair": rid, "reason": "no non-ISO dates parse under the format"})
            continue
        applied.append(rid)
        d = r["detail"]
        if r["kind"] == "date_format":
            label = f"date_format {d['format']} -> ISO"
        elif r["kind"] == "unit_scale":
            label = f"unit_scale {d['column']} x{d['factor']}"
        else:
            label = f"category_map {d['from']!r} -> {d['to']}"
        step(label, "repair", [d["column"]], changed=per_repair[rid], repair=rid)
    settled = set(applied) | {x["repair"] for x in skipped}
    for r in repairs:
        if r["id"] not in settled:
            skipped.append({"repair": r["id"], "reason": "not applicable to this batch"})
    canon = pd.DataFrame(canonical, index=raw.index, dtype=object)

    # Exact duplicates: identical raw records after the declared trim and blank-to-null rule (so copies that differ
    # only in surrounding whitespace are duplicates, not conflicts). Conflicts: same order_id, different content.
    merged: dict[int, int] = {}
    first_seen: dict[tuple, int] = {}
    for rec in raw.itertuples(index=True, name=None):
        key = tuple(_trim(v) for v in rec[1:])
        if key in first_seen:
            merged[rec[0]] = first_seen[key]
        else:
            first_seen[key] = rec[0]
    reasons: dict[int, list[dict]] = defaultdict(list)
    live = [r for r in rows if r not in merged]
    pk = contract.primary_key
    groups: dict[str, list[int]] = defaultdict(list)
    for r in live:
        if canon.at[r, pk] is not None:
            groups[canon.at[r, pk]].append(r)
    for oid, members in groups.items():
        if len(members) > 1:
            for r in members:
                reasons[r].append({"code": "DUPLICATE_CONFLICT", "column": pk,
                                   "message": f"order_id {oid!r} appears {len(members)} times with different content"})
        if oid in prior_ids:
            for r in members:
                reasons[r].append({"code": "DUPLICATE_PRIOR_BATCH", "column": pk,
                                   "message": f"order_id {oid!r} was already accepted from {prior_ids[oid]}"})
    step("deduplicate", "route", [pk], rows_out=len(live), merged=len(merged),
         conflict_rows=sum(len(m) for m in groups.values() if len(m) > 1))

    if missing_required:
        for r in live:
            reasons[r].append({"code": "UNKNOWN_COLUMN_SET", "column": None,
                               "message": f"required column(s) {', '.join(missing_required)} absent"})
    for src, target in sorted((unmapped or {}).items()):
        if src not in raw.columns or column_sources.get(target) or target in missing_required:
            continue
        for r in live:
            if _trim(raw.at[r, src]) is not None:
                reasons[r].append({"code": "UNKNOWN_COLUMN_SET", "column": src,
                                   "message": f"column {src} may hold {target} but was not mapped"})
    position = {r: i for i, r in enumerate(rows)}
    for (row, col), code in sorted(cell_errors.items()):
        if row not in merged:
            reasons[row].append({"code": code, "column": col,
                                 "message": _message(code, col, trimmed[col][position[row]], contract)})
    for row, col, code in contract_failures(contract, canon.loc[live]):
        if (row, col) in cell_errors or column_sources[col] is None:
            continue
        value = canon.at[row, col]
        if code == "INVALID_CATEGORY" and classify_label(value, contract.col(col).values)["rule"] in AMBIGUOUS_RULES:
            code = "AMBIGUOUS_CATEGORY"
        reasons[row].append({"code": code, "column": col, "message": _message(code, col, value, contract)})
    converted = {by_id[rid]["detail"]["column"] for rid in applied if by_id[rid]["kind"] == "unit_scale"}
    for col, unit in sorted(detected_units.items()):
        if col in converted:
            continue
        for r in live:
            if canon.at[r, col] is not None:
                reasons[r].append({"code": "UNRESOLVED_UNIT_SCALE", "column": col,
                                   "message": f"{col} {canon.at[r, col]!r} looks like {unit}; conversion not applied"})
    for r in reasons:
        reasons[r].sort(key=lambda x: (x["code"], x["column"] or "", x["message"]))
    accepted = [r for r in live if not reasons.get(r)]
    quarantined = [r for r in live if reasons.get(r)]
    step("validate_contract", "route", ["*"], rows_in=len(live), rows_out=len(accepted),
         quarantined=len(quarantined), engine="pandera")

    canon["amount_usd"] = None
    canon["restates_period"] = ""
    for r in accepted:
        canon.at[r, "amount_usd"] = format(fx.to_base(Decimal(canon.at[r, "amount"]), canon.at[r, "currency"]), "f")
    step("currency_normalize", "derive", ["amount_usd"], rows_in=len(accepted), rows_out=len(accepted),
         changed=len(accepted), fx_table=fx.fx_table)
    late = 0
    for r in accepted:
        period = canon.at[r, "order_date"][:7]
        if period in closed_periods and period != batch:
            canon.at[r, "restates_period"] = period
            late += 1
    step("flag_late_arrivals", "derive", ["restates_period"], rows_in=len(accepted), rows_out=len(accepted),
         changed=late, closed_periods=sorted(closed_periods))

    for s in steps:
        s["id"] = f"step:{batch}:{s['order']:02d}:{s['name'].split(' ')[0]}"
    result = BatchResult(batch, source_file, raw, canon, dict(reasons), merged, accepted, quarantined, changed,
                         steps, applied, skipped, column_sources)
    required = [c.name for c in contract.columns if c.required]
    conflict_rows = sum(len(m) for m in groups.values() if len(m) > 1)
    contract_ok = [r for r in live if not [x for x in reasons.get(r, []) if x["code"] not in DUPLICATE_CODES]]
    result.metrics = {
        "rows_in": n,
        "completeness": rnd(sum(v is not None for c in required for v in trimmed[c]) / (n * len(required)), 4)
        if n else None,
        "validity": rnd(len(contract_ok) / len(live), 4) if live else None,
        "uniqueness": rnd(1 - (len(merged) + conflict_rows) / n, 4) if n else None,
        "timeliness": rnd(1 - late / len(accepted), 4) if accepted else None,
        "late_share": rnd(late / len(accepted), 4) if accepted else None,
        "accepted_share": rnd(len(accepted) / n, 4) if n else None,
        "rows_accepted": len(accepted),
        "rows_quarantined": len(quarantined),
        "rows_merged_duplicates": len(merged),
    }
    return result


def _cleaned_rows(result: BatchResult, contract: Contract) -> list[dict]:
    cols = [*contract.names, *DERIVED_COLUMNS]
    return [{**{c: result.canonical.at[r, c] for c in cols}, "_batch": result.batch,
             "_source_file": result.source_file, "_source_row": r} for r in result.accepted]


def _raw_record(result: BatchResult, row: int) -> dict:
    return {c: result.raw.at[row, c] for c in result.raw.columns}


def _quarantine_rows(result: BatchResult) -> list[dict]:
    out = []
    for r in result.quarantined:
        rs = result.reasons[r]
        out.append({"_batch": result.batch, "_source_file": result.source_file, "_source_row": r,
                    "reason_codes": ";".join(sorted({x["code"] for x in rs})),
                    "reason_detail": "; ".join(x["message"] for x in rs if x["message"]),
                    "raw": json.dumps(_raw_record(result, r), ensure_ascii=False, separators=(",", ":"))})
    return out


def _raw_amount(result: BatchResult, row: int, contract: Contract) -> str | None:
    """The row's raw amount under the contract's numeric parse (sign kept), or None when it does not parse."""
    src = result.column_sources.get("amount")
    value, error, _ = normalize_cell(contract.col("amount"), _trim(result.raw.at[row, src]) if src else None)
    return None if error else value


def _money_by_currency(result: BatchResult, rows: list[int], contract: Contract, cleaned: bool = False) -> dict:
    """Amounts grouped by raw currency text: raw parses, or (cleaned=True) the accepted canonical amounts."""
    totals: dict[str, Decimal] = defaultdict(Decimal)
    currency_src = result.column_sources.get("currency")
    for r in rows:
        a = result.canonical.at[r, "amount"] if cleaned else _raw_amount(result, r, contract)
        if a is not None:
            cur = _trim(result.raw.at[r, currency_src]) if currency_src else None
            totals[cur or "(null)"] += Decimal(a)
    return {k: format(v, "f") for k, v in sorted(totals.items())}


def _samples(result: BatchResult, by_id: dict[str, dict], contract: Contract, limit: int = 25) -> list[dict]:
    repairs_by_row: dict[int, set[str]] = defaultdict(set)
    for (row, _), rid in result.changed.items():
        repairs_by_row[row].add(rid)
    tags: dict[int, list[str]] = {}
    for r in result.raw.index:
        if r in result.merged:
            t = ["merged_duplicate"]
        elif r in result.reasons:
            t = [f"quarantine:{c}" for c in sorted({x["code"] for x in result.reasons[r]})]
        else:
            t = sorted({f"repair:{by_id[rid]['kind']}" for rid in repairs_by_row[r]})
            if result.canonical.at[r, "amount_usd"] != result.canonical.at[r, "amount"]:
                t.append(f"fx:{result.canonical.at[r, 'currency']}")
            if result.canonical.at[r, "restates_period"]:
                t.append("late_arrival")
            t = t or ["clean"]
        tags[r] = t
    pools: dict[str, list[int]] = defaultdict(list)
    for r, ts in tags.items():
        for t in ts:
            pools[t].append(r)
    chosen: list[int] = []
    while len(chosen) < limit and any(pools.values()):
        for t in sorted(pools):
            while pools[t] and pools[t][0] in chosen:
                pools[t].pop(0)
            if pools[t] and len(chosen) < limit:
                chosen.append(pools[t].pop(0))
    cols = [*contract.names, *DERIVED_COLUMNS]
    out = []
    for r in sorted(chosen):
        accepted = r in result.accepted
        out.append({
            "_source_row": r,
            "outcome": "accepted" if accepted else "merged" if r in result.merged else "quarantined",
            "tags": tags[r],
            "raw": _raw_record(result, r),
            "cleaned": {c: result.canonical.at[r, c] for c in cols} if accepted else None,
            "reasons": result.reasons.get(r, []),
            "repairs": sorted(repairs_by_row[r]) if accepted else [],
            "duplicate_of": result.merged.get(r),
        })
    return out


def _lineage(results: list[BatchResult], manifests: list[dict], contract: Contract, fx: FxTable) -> dict:
    nodes = [{"id": "output:cleaned.csv", "type": "output"}, {"id": "output:quarantine.csv", "type": "output"},
             {"id": "output:reconciliation.json", "type": "output", "detail": "merged duplicates"}]
    edges = []
    columns: dict[str, dict] = {c: {"kind": "contract", "by_batch": {}} for c in contract.names}
    for res, man in zip(results, manifests, strict=True):
        src = f"source:{res.batch}"
        nodes.append({"id": src, "type": "source", "batch": res.batch, "file": man["file"], "sha256": man["sha256"],
                      "rows": man["rows"]})
        prev = src
        for s in res.steps:
            nodes.append({"id": s["id"], "type": "step", "batch": res.batch, "name": s["name"], "kind": s["kind"],
                          "repair": s["repair"], "rows_in": s["rows_in"], "rows_out": s["rows_out"]})
            edges.append({"from": prev, "to": s["id"], "rows": s["rows_in"]})
            prev = s["id"]
            if s["name"] == "deduplicate":
                edges.append({"from": s["id"], "to": "output:reconciliation.json", "rows": len(res.merged)})
            if s["name"] == "validate_contract":
                edges.append({"from": s["id"], "to": "output:quarantine.csv", "rows": len(res.quarantined)})
        edges.append({"from": prev, "to": "output:cleaned.csv", "rows": len(res.accepted)})
        for c in contract.names:
            touched = [s["id"] for s in res.steps if "*" in s["columns"] or c in s["columns"]]
            columns[c]["by_batch"][res.batch] = {"source_column": res.column_sources[c], "steps": touched}
    derived = contract.derived[0]
    columns["amount_usd"] = {"kind": "derived", "inputs": derived.inputs, "rule": derived.rule,
                             "fx_table": fx.fx_table, "rates": {k: str(v) for k, v in fx.rates.items()},
                             "by_batch": {r.batch: {"steps": [s["id"] for s in r.steps
                                                              if s["name"] == "currency_normalize"]} for r in results}}
    columns["restates_period"] = {"kind": "derived", "inputs": ["order_date", "_batch"],
                                  "rule": "YYYY-MM of order_date when that month was closed by an earlier batch "
                                          "in this run, else empty",
                                  "by_batch": {r.batch: {"steps": [s["id"] for s in r.steps
                                                                   if s["name"] == "flag_late_arrivals"]}
                                               for r in results}}
    for c in LINEAGE_COLUMNS:
        columns[c] = {"kind": "lineage", "rule": {"_batch": "batch id of the source file",
                                                  "_source_file": "source file name",
                                                  "_source_row": "1-based data record number in the source file"}[c]}
    return {"nodes": nodes, "edges": edges, "columns": columns,
            "row_mapping": "every cleaned.csv and quarantine.csv row carries _batch, _source_file and _source_row; "
                           "merged duplicates are listed in reconciliation.json with duplicate_of"}


def _self_checks(results: list[BatchResult], manifests: list[dict], paths: list[Path], fx: FxTable,
                 contract: Contract) -> list[dict]:
    checks = []
    for res in results:
        n = len(res.raw)
        total = len(res.accepted) + len(res.quarantined) + len(res.merged)
        covered = sorted([*res.accepted, *res.quarantined, *res.merged])
        checks.append({"id": f"accounting:{res.batch}", "passed": total == n and covered == list(res.raw.index),
                       "detail": f"rows_in {n} = accepted {len(res.accepted)} + quarantined {len(res.quarantined)} "
                                 f"+ merged {len(res.merged)}"})
    for man, path in zip(manifests, paths, strict=True):
        now = file_sha256(path)
        checks.append({"id": f"raw_unchanged:{man['file']}", "passed": now == man["sha256"],
                       "detail": "sha256 of the source file re-read after the run equals the intake manifest"})
    problems = []
    for res in results:
        for r in res.accepted:
            for c in contract.names:
                src = res.column_sources[c]
                raw_v = _trim(res.raw.at[r, src]) if src else None
                out_v = res.canonical.at[r, c]
                if (raw_v is None) != (out_v is None):
                    problems.append(f"{res.batch}:{r}:{c} null-ness changed")
                elif out_v is not None and out_v != raw_v and (r, c) not in res.changed:
                    col = contract.col(c)
                    if not (col.numeric and Decimal(out_v) == Decimal(raw_v)):
                        problems.append(f"{res.batch}:{r}:{c} changed without a declared parse or applied repair")
            expect = fx.to_base(Decimal(res.canonical.at[r, "amount"]), res.canonical.at[r, "currency"])
            if format(expect, "f") != res.canonical.at[r, "amount_usd"]:
                problems.append(f"{res.batch}:{r}:amount_usd mismatch")
    checks.append({"id": "no_invented_values", "passed": not problems,
                   "detail": "every accepted cell is the trimmed source value, a numeric re-format of it, or the "
                             "output of an applied repair; nulls stay null; amount_usd recomputed from the FX table"
                             + (f"; problems: {problems[:5]}" if problems else "")})
    accepted = pd.concat([r.canonical.loc[r.accepted, contract.names] for r in results])
    failures = contract_failures(contract, accepted.reset_index(drop=True), enforce_unique=True)
    checks.append({"id": "accepted_rows_satisfy_contract", "passed": not failures,
                   "detail": f"pandera re-validation of all accepted rows with uniqueness enforced: "
                             f"{len(failures)} failures"})
    unstable = []
    for res in results:
        if not res.accepted:
            continue
        before = res.canonical.loc[res.accepted, contract.names].reset_index(drop=True)
        again = process_batch(res.batch, before.set_axis(pd.RangeIndex(1, len(before) + 1)), res.source_file,
                              contract, fx, [], {}, {}, set())
        after = again.canonical.loc[again.accepted, contract.names].reset_index(drop=True)
        if len(again.accepted) != len(before) or not after.equals(before):
            unstable.append(res.batch)
    checks.append({"id": "idempotency", "passed": not unstable,
                   "detail": "accepted rows re-processed as a fresh batch with no repairs are all accepted again "
                             "with identical values (cleaned output is a fixed point)"
                             + (f"; unstable batches: {unstable}" if unstable else "")})
    return checks


def _period_entry() -> dict:
    return {"accepted_usd": "0.00", "rows": 0, "reported_by": [], "closed_by": None, "restatements": []}


def _update_periods(periods: dict[str, dict], res: BatchResult) -> None:
    """Add a batch's accepted USD by order month. Late rows record the restated total before and after."""
    groups: dict[tuple[str, bool], list[int]] = defaultdict(list)
    for r in res.accepted:
        groups[(res.canonical.at[r, "order_date"][:7], bool(res.canonical.at[r, "restates_period"]))].append(r)
    for (period, is_late), rows in sorted(groups.items()):
        entry = periods.setdefault(period, _period_entry())
        added = sum((Decimal(res.canonical.at[r, "amount_usd"]) for r in rows), Decimal("0.00"))
        before = Decimal(entry["accepted_usd"])
        entry["accepted_usd"] = format(before + added, "f")
        entry["rows"] += len(rows)
        if res.batch not in entry["reported_by"]:
            entry["reported_by"].append(res.batch)
        if is_late:
            entry["restatements"].append({"batch": res.batch, "rows": len(rows), "late_usd": format(added, "f"),
                                          "before_usd": format(before, "f"), "after_usd": entry["accepted_usd"]})


def _csv_bytes(columns: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for row in rows:
        w.writerow(["" if row[c] is None else row[c] for c in columns])
    return buf.getvalue().encode("utf-8")


def _json_bytes(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _resolution_code(r: dict) -> str:
    """The reason code that rows affected by a repair carry while the repair is not applied."""
    if r["kind"] == "category_map":
        if r["detail"]["column"] != "category":
            return "INVALID_ENUM"
        return "AMBIGUOUS_CATEGORY" if r["detail"]["rule"] in AMBIGUOUS_RULES else "INVALID_CATEGORY"
    return {"date_format": "BAD_DATE", "rename": "UNKNOWN_COLUMN_SET", "unit_scale": "UNRESOLVED_UNIT_SCALE"}[r["kind"]]


def _canonical_hash(obj) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                                 ensure_ascii=False).encode()).hexdigest()


def _check_outdir(outdir: Path) -> None:
    if outdir.exists() and (not outdir.is_dir() or any(outdir.iterdir())):
        raise RequestError(f"output directory {outdir.name} exists and is not empty")


def run(params: dict, outdir: Path, fixtures_dir: Path = FIXTURES_DIR) -> dict:
    """Run the full pipeline for a validated request and write every output file into outdir."""
    problems = request_problems(params)
    if problems:
        raise RequestError("; ".join(problems))
    outdir = Path(outdir)
    _check_outdir(outdir)
    contract = load_contract(params["contract"])
    fx = load_fx(params["fx_table"])
    mode = params["repair_mode"]

    manifests, paths, results, before, profiles, drift, all_repairs = [], [], [], [], {}, {}, []
    prior_ids: dict[str, str] = {}
    closed: set[str] = set()
    baseline: dict | None = None
    periods: dict[str, dict] = {}
    for batch in params["batches"]:
        path = find_source(Path(fixtures_dir), batch)
        raw, manifest = read_batch(path)
        manifests.append(manifest)
        paths.append(path)
        raw_profile = profile_frame(raw, contract)
        findings, repairs = analyse_batch(batch, raw, raw_profile, contract, baseline)
        to_apply = [r for r in repairs if r["auto_applicable"]] if mode == "apply-safe" else []
        unit_cols = {r["detail"]["column"]: r["detail"]["from_unit"] for r in repairs if r["kind"] == "unit_scale"}
        candidates = {r["detail"]["from"]: r["detail"]["to"] for r in repairs if r["kind"] == "rename"}
        pre = process_batch(batch, raw, path.name, contract, fx, [], unit_cols, prior_ids, closed, candidates)
        res = process_batch(batch, raw, path.name, contract, fx, to_apply, unit_cols, prior_ids, closed,
                            candidates) if to_apply else pre
        for r in repairs:
            r["applied"] = r["id"] in res.applied
        all_repairs += repairs
        accepted_profile = profile_frame(res.canonical.loc[res.accepted, contract.names], contract)
        profiles[batch] = {"raw": raw_profile, "accepted": accepted_profile}
        drift[batch] = {"baseline": baseline["batch"] if baseline else None, "findings": findings}
        results.append(res)
        before.append(pre)

        _update_periods(periods, res)
        if res.accepted:
            baseline = {"batch": batch, "profile": accepted_profile, "raw_profile": raw_profile}
        for r in res.accepted:
            prior_ids[res.canonical.at[r, contract.primary_key]] = batch
        closed.add(batch)
        periods.setdefault(batch, _period_entry())["closed_by"] = batch

    by_id = {r["id"]: r for r in all_repairs}
    cleaned_rows = [row for res in results for row in _cleaned_rows(res, contract)]
    quarantine_rows = [row for res in results for row in _quarantine_rows(res)]
    recon_batches = {}
    for res in results:
        m = len(res.raw)
        # accepted uses the cleaned amounts while the other parts use raw parses, so the balance fails if any
        # accepted amount differs from its source.
        money = {"input": _money_by_currency(res, list(res.raw.index), contract),
                 "accepted": _money_by_currency(res, res.accepted, contract, cleaned=True),
                 "quarantined": _money_by_currency(res, res.quarantined, contract),
                 "merged": _money_by_currency(res, sorted(res.merged), contract)}
        currencies = sorted(set().union(*money.values()))
        balanced = all(Decimal(money["input"].get(c, "0")) == sum(Decimal(money[k].get(c, "0"))
                       for k in ("accepted", "quarantined", "merged")) for c in currencies)
        mismatched = sum(_raw_amount(res, r, contract) != res.canonical.at[r, "amount"] for r in res.accepted)
        unparseable = sum(_raw_amount(res, r, contract) is None for r in res.raw.index)
        cleaned_sum: dict[str, Decimal] = defaultdict(Decimal)
        usd: dict[str, Decimal] = defaultdict(Decimal)
        for r in res.accepted:
            cleaned_sum[res.canonical.at[r, "currency"]] += Decimal(res.canonical.at[r, "amount"])
            usd[res.canonical.at[r, "order_date"][:7]] += Decimal(res.canonical.at[r, "amount_usd"])
        cleaned_fmt = {k: format(v, "f") for k, v in sorted(cleaned_sum.items())}
        recon_batches[res.batch] = {
            "rows_in": m,
            "rows_accepted": len(res.accepted),
            "rows_quarantined": len(res.quarantined),
            "rows_merged_duplicates": len(res.merged),
            "identity_holds": m == len(res.accepted) + len(res.quarantined) + len(res.merged),
            "amount_by_currency": {**money, "balanced": balanced, "accepted_cleaned": cleaned_fmt,
                                   "cleaned_matches_input": mismatched == 0,
                                   "unparseable_amount_rows": unparseable,
                                   "note": "grouped by raw currency text; input, quarantined and merged sum raw "
                                           "amounts that pass the contract parse, accepted sums cleaned amounts; "
                                           "accepted_cleaned groups cleaned amounts by contract currency"},
            "accepted_usd_by_period": {k: format(v, "f") for k, v in sorted(usd.items())},
            "accepted_by_category": dict(sorted(Counter(res.canonical.loc[res.accepted, "category"]).items())),
            "quarantine_reasons": dict(sorted(Counter(c for r in res.quarantined
                                                      for c in {x["code"] for x in res.reasons[r]}).items())),
            "merged_duplicates": [{"_source_row": r, "duplicate_of": d} for r, d in sorted(res.merged.items())],
            "restated_rows": sum(1 for r in res.accepted if res.canonical.at[r, "restates_period"]),
        }
    reconciliation = {
        "identity": "rows_in == rows_accepted + rows_quarantined + rows_merged_duplicates",
        "batches": recon_batches,
        "periods": dict(sorted(periods.items())),
        "totals": {"rows_in": sum(len(r.raw) for r in results), "rows_accepted": len(cleaned_rows),
                   "rows_quarantined": len(quarantine_rows),
                   "rows_merged_duplicates": sum(len(r.merged) for r in results),
                   "accepted_usd": format(sum((Decimal(x["amount_usd"]) for x in cleaned_rows), Decimal("0")), "f")},
    }
    ambiguities = []
    for r in all_repairs:
        if r["auto_applicable"]:
            continue
        code = _resolution_code(r)
        ambiguities.append({"id": f"A-{r['id'][2:]}", "repair": r["id"], "batch": r["batch"], "kind": r["kind"],
                            "column": r["detail"].get("column", r["detail"].get("to")),
                            "value": r["detail"].get("from"), "rows": r["detail"].get("rows_affected"),
                            "candidates": r["evidence"].get("candidates"), "reason": r["ambiguity"],
                            "resolution": f"not auto-applied in any mode; affected rows are quarantined as {code} "
                                          "until a human decides"})
    manifest = {
        "params": params,
        "sources": manifests,
        "contract_version": contract.contract,
        "contract_sha256": file_sha256(contract_path(params["contract"])),
        "fx_table": fx.fx_table,
        "fx_sha256": file_sha256(fx_path(params["fx_table"])),
        "package_version": __version__,
        "row_numbering": "1-based data record number in the source file, header excluded",
    }
    manifest["input_manifest_hash"] = _canonical_hash({
        "params": params, "sources": [m["sha256"] for m in manifests],
        "contract": manifest["contract_sha256"], "package_version": __version__})
    plan = {"repair_mode": mode, "batches": {
        res.batch: {"steps": res.steps, "applied_repairs": res.applied, "skipped_repairs": res.skipped,
                    "proposed_not_applied": [r["id"] for r in all_repairs if r["batch"] == res.batch
                                             and not r["applied"]]} for res in results}}
    quality = {
        "definitions": {
            "completeness": "present required cells / (rows x required columns), after renames and trimming",
            "validity": "share of non-merged rows with no contract reason code (duplicate codes excluded)",
            "uniqueness": "1 - (merged duplicates + rows in conflicting duplicate groups) / rows_in",
            "timeliness": "1 - late_share, where late_share = accepted rows restating a closed period / accepted",
            "before_repair": "the batch processed with no repairs applied (what propose mode produces)",
            "after_repair": "the batch processed with the repairs applied in this run's mode",
        },
        "batches": {res.batch: {"before_repair": pre.metrics, "after_repair": res.metrics}
                    for res, pre in zip(results, before, strict=True)},
    }
    samples = {"limit_per_batch": 25, "batches": {res.batch: _samples(res, by_id, contract) for res in results}}
    lineage = _lineage(results, manifests, contract, fx)
    checks = _self_checks(results, manifests, paths, fx, contract)

    files = {
        "manifest.json": _json_bytes(manifest),
        "profiles.json": _json_bytes({"batches": profiles}),
        "drift.json": _json_bytes({"batches": drift}),
        "repairs.json": _json_bytes({"repair_mode": mode, "repairs": all_repairs,
                                     "summary": {"proposed": len(all_repairs),
                                                 "auto_applicable": sum(r["auto_applicable"] for r in all_repairs),
                                                 "applied": sum(r["applied"] for r in all_repairs)}}),
        "ambiguities.json": _json_bytes({"ambiguities": ambiguities}),
        "plan.json": _json_bytes(plan),
        "cleaned.csv": _csv_bytes([*contract.names, *DERIVED_COLUMNS, *LINEAGE_COLUMNS], cleaned_rows),
        "quarantine.csv": _csv_bytes(QUARANTINE_COLUMNS, quarantine_rows),
        "reconciliation.json": _json_bytes(reconciliation),
        "lineage.json": _json_bytes(lineage),
        "quality.json": _json_bytes(quality),
        "samples.json": _json_bytes(samples),
        "checks.json": _json_bytes({"all_passed": all(c["passed"] for c in checks), "checks": checks,
                                    "reason_codes": REASON_CODES}),
        "report.md": render_report(params, reconciliation, all_repairs, ambiguities, checks).encode("utf-8"),
    }
    total = sum(len(b) for b in files.values())
    if total > MAX_TOTAL_BYTES:
        raise OutputBudgetError(f"these batches would produce {total} bytes of output, over the {MAX_TOTAL_BYTES} "
                                "byte per-run budget; request fewer or smaller batches")
    _check_outdir(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (outdir / name).write_bytes(data)
    return {"repair_mode": mode, "batches": {b: {k: v[k] for k in ("rows_in", "rows_accepted", "rows_quarantined",
                                                                     "rows_merged_duplicates")}
                                             for b, v in recon_batches.items()},
            "repairs_proposed": len(all_repairs), "repairs_applied": sum(r["applied"] for r in all_repairs),
            "ambiguities": len(ambiguities), "checks_passed": all(c["passed"] for c in checks)}

