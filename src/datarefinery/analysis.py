"""Profiling, drift detection and repair proposals. Everything here is a pure function of frames and profiles."""

from __future__ import annotations

import math
import re
from collections import Counter
from difflib import SequenceMatcher

import numpy as np
import pandas as pd

from .contract import (
    INTEGER,
    ISO_SHAPE,
    NUMBER,
    Column,
    Contract,
    is_iso_date,
    normalize_cell,
    parse_date,
    parse_number,
)

SLASH_SHAPE = re.compile(r"^[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}$")
RENAME_MIN_VALUE_MATCH = 0.99
RENAME_MIN_NAME_SIMILARITY = 0.5
UNIT_MEDIAN_TOLERANCE = 0.1  # log10 units, about +/-26%
UNIT_TAIL_TOLERANCE = 0.2
SCALE_WARN_RATIO = 2.0
CATEGORY_RULE_CONFIDENCE = {"case": 0.99, "plural": 0.97, "typo": 0.9, "substitution": 0.5, "abbreviation": 0.4,
                            "composite": 0.3, "typo_multiple": 0.3, "unmapped": 0.0}
SAFE_CATEGORY_RULES = {"case", "plural", "typo"}
AMBIGUOUS_RULES = {"substitution", "abbreviation", "composite", "typo_multiple"}
RENAME_KINDS = {"missing_column", "extra_column", "rename_candidate"}


def rnd(x: float, places: int = 6) -> float:
    return float(round(float(x), places))


def present(series: pd.Series) -> list[str]:
    """Non-null values after trimming; blank text counts as null."""
    return [v.strip() for v in series if v is not None and v.strip() != ""]


def profile_column(series: pd.Series, date_formats: list[str]) -> dict:
    values = present(series)
    n = len(series)
    counts = Counter(values)
    shares = Counter()
    for v in values:
        if INTEGER.match(v):
            shares["integer"] += 1
        elif NUMBER.match(v):
            shares["decimal"] += 1
        elif ISO_SHAPE.match(v) or SLASH_SHAPE.match(v):
            shares["date"] += 1
        else:
            shares["string"] += 1
    k = len(values)
    if not k:
        inferred = "empty"
    elif shares["integer"] == k:
        inferred = "integer"
    elif shares["integer"] + shares["decimal"] == k:
        inferred = "decimal"
    elif shares["date"] >= 0.95 * k:
        inferred = "date"
    elif shares["string"] == k:
        inferred = "string"
    else:
        inferred = "mixed"
    out = {
        "rows": n,
        "null_count": n - k,
        "null_rate": rnd((n - k) / n if n else 0, 4),
        "distinct_count": len(counts),
        "inferred_type": inferred,
        "type_shares": {t: rnd(c / k, 4) for t, c in sorted(shares.items())} if k else {},
        "top_values": [[v, c] for v, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]],
    }
    # The scale summary uses every value that reads as a number, so one junk cell cannot hide a unit change.
    nums = np.array([x for x in (parse_number(v) for v in values) if x is not None])
    if len(nums):
        p05, med, p95 = np.percentile(nums, [5, 50, 95])
        out.update(numeric_values=len(nums), scale={"p05": rnd(p05), "median": rnd(med), "p95": rnd(p95)})
        if inferred in ("integer", "decimal"):
            out.update(min=rnd(nums.min()), max=rnd(nums.max()))
    if shares["date"]:
        fmts = {"%Y-%m-%d": 0, **{f: 0 for f in date_formats}}
        unparsed = 0
        iso = []
        for v in values:
            hit = False
            if is_iso_date(v):
                fmts["%Y-%m-%d"] += 1
                iso.append(v)
                hit = True
            elif not ISO_SHAPE.match(v):
                for f in date_formats:
                    if parse_date(v, f):
                        fmts[f] += 1
                        hit = True
            unparsed += not hit
        out["date_formats"] = {**fmts, "unparsed": unparsed}
        if iso:
            out.update(min=min(iso), max=max(iso))
    return out


def profile_frame(frame: pd.DataFrame, contract: Contract) -> dict:
    date_formats = contract.col("order_date").alternate_formats
    profiles = {c: profile_column(frame[c], date_formats) for c in frame.columns}
    if "currency" in profiles:
        profiles["currency"]["currencies"] = dict(sorted(Counter(present(frame["currency"])).items()))
    return profiles


def value_match_share(col: Column, values: list[str]) -> float:
    """Share of values that already satisfy a contract column's declared parse and its pattern or enum."""
    if not values:
        return 0.0
    ok = 0
    for v in values:
        canonical, error, _ = normalize_cell(col, v)
        if error or canonical is None:
            continue
        if col.pattern and not re.match(col.pattern, canonical):
            continue
        if col.type == "enum" and canonical not in col.values:
            continue
        ok += 1
    return ok / len(values)


def name_tokens(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", name.lower())


def one_insert_or_delete(a: str, b: str) -> bool:
    short, long = sorted((a, b), key=len)
    return len(long) - len(short) == 1 and any(long[:i] + long[i + 1:] == short for i in range(len(long)))


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def classify_label(value: str, targets: list[str]) -> dict:
    """Rule ladder for an enum label outside the contract.

    Safe: case, plural, and a unique one-letter insertion or deletion (a doubled or dropped letter). A one-letter
    substitution is not safe because it readily forms a different word (Tops is not Toys).
    """
    low = value.casefold()
    folded = {t.casefold(): t for t in targets}
    tokens = re.findall(r"[a-z0-9]+", low)

    def result(rule: str, target: str | None, candidates: list[str], reason: str | None = None) -> dict:
        return {"rule": rule, "target": target, "candidates": candidates, "ambiguity": reason,
                "confidence": CATEGORY_RULE_CONFIDENCE[rule]}

    if low in folded:
        return result("case", folded[low], [folded[low]])
    plural = [t for f, t in folded.items() if low in (f + "s", f + "es") or f in (low + "s", low + "es")]
    if len(plural) == 1:
        return result("plural", plural[0], plural)
    dists = sorted((levenshtein(low, f), t) for f, t in folded.items())
    close = [t for d, t in dists if d == 1]
    if len(low) >= 4 and len(close) == 1 and dists[1][0] >= 3:
        if one_insert_or_delete(low, close[0].casefold()):
            return result("typo", close[0], close)
        return result("substitution", None, close,
                      f"'{value}' differs from {close[0]} by one substituted letter; a substitution can form a "
                      "different word, so the meaning is not certain.")
    if len(close) > 1:
        return result("typo_multiple", None, close,
                      f"'{value}' is one edit away from several contract categories ({', '.join(close)}).")
    hits = [t for f, t in folded.items() if f in tokens]
    if len(tokens) > 1 and hits:
        extra = [w for w in tokens if w not in folded]
        return result("composite", None, hits,
                      f"'{value}' combines {', '.join(hits)} with other terms ({', '.join(extra)}) that have no "
                      f"contract category; it is broader than {hits[0]} and may belong elsewhere.")
    prefixes = [t for f, t in folded.items() if f.startswith(low) or low.startswith(f)]
    if prefixes:
        return result("abbreviation", None, prefixes,
                      f"'{value}' is only a prefix of {', '.join(prefixes)}; a truncated label can also abbreviate a "
                      "category outside the contract, so the data does not determine its meaning.")
    return result("unmapped", None, [],
                  f"'{value}' matches no contract category by case, plural, single-edit typo, prefix or token.")


def _finding(batch: str, kind: str, column: str | None, severity: str, detail: str, evidence: dict) -> dict:
    return {"batch": batch, "kind": kind, "column": column, "severity": severity, "detail": detail,
            "evidence": evidence}


def _scale_baseline(col: Column, baseline: dict | None) -> tuple[dict | None, str | None]:
    if baseline and col.name in baseline["profile"] and "scale" in baseline["profile"][col.name]:
        return baseline["profile"][col.name]["scale"], f"accepted rows of {baseline['batch']}"
    if col.reference:
        return col.reference, f"contract {col.name} reference"
    return None, None


def detect_drift(batch: str, frame: pd.DataFrame, profile: dict, contract: Contract,
                 baseline: dict | None, first: int = 1) -> list[dict]:
    """baseline: {"batch", "profile"} built from the previous processed batch's accepted rows, or None."""
    findings = []
    names = contract.names
    missing = [c for c in names if c not in frame.columns]
    extra = [c for c in frame.columns if c not in names]
    for name in missing:
        sev = "error" if contract.col(name).required else "warning"
        findings.append(_finding(batch, "missing_column", name, sev, f"contract column {name} is absent", {}))
    for name in extra:
        findings.append(_finding(batch, "extra_column", name, "warning", f"column {name} is not in the contract",
                                 {"null_rate": profile[name]["null_rate"]}))
    for target in missing:
        for source in extra:
            sim = SequenceMatcher(None, target.lower(), source.lower()).ratio()
            match = value_match_share(contract.col(target), present(frame[source]))
            if match >= 0.5:
                findings.append(_finding(batch, "rename_candidate", target, "warning",
                                         f"{source} looks like {target}",
                                         {"source_column": source, "name_similarity": rnd(sim, 4),
                                          "value_match_share": rnd(match, 4)}))
    for col in contract.columns:
        if col.name not in frame.columns:
            continue
        p = profile[col.name]
        if col.type == "date" and "date_formats" in p:
            non_canonical = p["date_formats"]["unparsed"] + sum(v for f, v in p["date_formats"].items()
                                                                if f not in ("%Y-%m-%d", "unparsed"))
            if non_canonical:
                prior = baseline["raw_profile"].get(col.name, {}).get("date_formats") if baseline else None
                findings.append(_finding(batch, "date_format_change", col.name, "warning",
                                         f"{non_canonical} values are not ISO {col.format}",
                                         {"format_counts": p["date_formats"], "baseline_format_counts": prior}))
        if col.numeric and "scale" in p:
            ref, source = _scale_baseline(col, baseline)
            ratios = {k: float(f"{p['scale'][k] / ref[k]:.6g}") for k in ("p05", "median", "p95")
                      if ref and ref.get(k, 0) > 0 and p["scale"][k] > 0}
            if "median" in ratios:
                conversions = {u: rnd(1 / float(f), 6) for u, f in col.unit_conversions.items()}
                matched = [u for u, r in conversions.items()
                           if abs(math.log10(ratios["median"]) - math.log10(r)) <= UNIT_MEDIAN_TOLERANCE]
                if matched or not (1 / SCALE_WARN_RATIO <= ratios["median"] <= SCALE_WARN_RATIO):
                    findings.append(_finding(batch, "scale_shift", col.name, "warning",
                                             f"median is {ratios['median']}x the baseline",
                                             {"baseline": source, "baseline_scale": ref, "scale": p["scale"],
                                              "ratios": ratios, "matches_unit": matched[0] if matched else None,
                                              "declared_conversions": conversions}))
        if col.type == "enum":
            unknown = Counter(v for v in present(frame[col.name]) if v not in col.values)
            if unknown:
                findings.append(_finding(batch, "unknown_enum_values", col.name, "warning",
                                         f"{len(unknown)} labels outside the contract",
                                         {"values": [[v, n] for v, n in sorted(unknown.items())]}))
        if col.required and p["null_count"]:
            prior = baseline["raw_profile"].get(col.name, {}).get("null_count") if baseline else None
            findings.append(_finding(batch, "nulls_in_required", col.name, "error",
                                     f"{p['null_count']} nulls in required column",
                                     {"null_count": p["null_count"], "baseline_null_count": prior}))
    if "currency" in profile:
        current = sorted(profile["currency"].get("currencies", {}))
        prior = sorted(baseline["raw_profile"].get("currency", {}).get("currencies", {})) if baseline else None
        if len(current) > 1 or (prior is not None and current != prior):
            findings.append(_finding(batch, "currency_mix", "currency", "info",
                                     f"currencies present: {', '.join(current)}",
                                     {"currencies": profile["currency"]["currencies"], "baseline_currencies": prior}))
    for n, f in enumerate(findings, start=first):
        f["id"] = f"D-{batch}-{n:02d}"
    return findings


def _repair(batch: str, kind: str, detail: dict, evidence: dict, confidence: float, auto: bool,
            ambiguity: str | None, finding: dict, basis: str) -> dict:
    return {"batch": batch, "kind": kind, "detail": detail, "evidence": evidence, "confidence": rnd(confidence, 3),
            "confidence_basis": basis, "auto_applicable": auto, "ambiguity": ambiguity, "finding": finding["id"]}


def date_candidates(values: list[str], formats: list[str], period: str) -> dict:
    """How each declared alternate format reads the non-ISO values, and where the results land.

    safe_format is set only when exactly one format parses every value that any format parses AND every result
    falls in the batch month; otherwise day-first and month-first readings cannot be told apart safely.
    """
    vals = [v for v in values if not is_iso_date(v)]
    parsed = {f: [parse_date(v, f) for v in vals] for f in formats}
    parseable = [i for i in range(len(vals)) if any(parsed[f][i] for f in formats)]
    candidates = {}
    for f in formats:
        hits = [parsed[f][i] for i in parseable if parsed[f][i]]
        in_month = sum(h[:7] == period for h in hits)
        candidates[f] = {"parsed": len(hits), "coverage": rnd(len(hits) / len(parseable), 4) if parseable else 0.0,
                         "in_batch_month": in_month,
                         "in_batch_month_share": rnd(in_month / len(hits), 4) if hits else 0.0}
    full = [f for f in formats if parseable and candidates[f]["parsed"] == len(parseable)]
    safe = full[0] if len(full) == 1 and candidates[full[0]]["in_batch_month"] == len(parseable) else None
    keep = set(parseable)
    return {"values": vals, "parseable": len(parseable), "full": full, "safe_format": safe,
            "malformed": [v for i, v in enumerate(vals) if i not in keep], "candidates": candidates}


def _date_repair(batch: str, finding: dict, frame: pd.DataFrame, col: Column) -> dict | None:
    dc = date_candidates(present(frame[col.name]), col.alternate_formats, batch)
    if not dc["parseable"]:
        return None
    candidates = dc["candidates"]
    evidence = {"non_canonical_values": len(dc["values"]), "parseable_by_some_format": dc["parseable"],
                "malformed_values": len(dc["malformed"]), "malformed_examples": sorted(set(dc["malformed"]))[:5],
                "candidates": candidates}
    best = dc["safe_format"] or max(candidates, key=lambda f: (candidates[f]["coverage"], f))
    detail = {"column": col.name, "format": best, "to_format": col.format,
              "rows_affected": candidates[best]["parsed"],
              "guard": "applied only when this is the only declared format that parses every parseable non-ISO "
                       "value and every result falls in the batch month"}
    if dc["safe_format"]:
        runner_up = max((c["coverage"] for f, c in candidates.items() if f != best), default=0.0)
        return _repair(batch, "date_format", detail, evidence, 0.5 + 0.5 * (1 - runner_up), True, None, finding,
                       "0.5 + 0.5 x (1 - coverage of the best competing format)")
    if len(dc["full"]) > 1:
        reason = f"formats {', '.join(dc['full'])} each parse every parseable value; day and month cannot be told apart"
    elif dc["full"]:
        outside = candidates[best]["parsed"] - candidates[best]["in_batch_month"]
        reason = (f"{best} parses every value but puts {outside} of {candidates[best]['parsed']} dates outside "
                  f"{batch}; the batch likely mixes day-first and month-first dates")
    else:
        reason = "no declared format parses every parseable value"
    return _repair(batch, "date_format", detail, evidence, 0.5 * candidates[best]["coverage"], False, reason, finding,
                   "0.5 x coverage of the best format (ambiguous)")


def propose_repairs(batch: str, frame: pd.DataFrame, findings: list[dict], contract: Contract,
                    first: int = 1) -> list[dict]:
    repairs = []
    renames = [f for f in findings if f["kind"] == "rename_candidate"]
    for f in renames:
        ev = f["evidence"]
        rivals = [g for g in renames if g is not f and (g["column"] == f["column"]
                                                        or g["evidence"]["source_column"] == ev["source_column"])
                  and g["evidence"]["value_match_share"] >= RENAME_MIN_VALUE_MATCH]
        strong = (ev["value_match_share"] >= RENAME_MIN_VALUE_MATCH
                  and ev["name_similarity"] >= RENAME_MIN_NAME_SIMILARITY)
        src_tokens, dst_tokens = name_tokens(ev["source_column"]), name_tokens(f["column"])
        qualifiers = [t for t in src_tokens if t not in dst_tokens] if set(dst_tokens) < set(src_tokens) else []
        implied = [u for u in contract.col(f["column"]).unit_conversions if u in src_tokens]
        auto = strong and not rivals and not qualifiers
        if auto:
            reason = None
        elif rivals:
            reason = "more than one column matches"
        elif qualifiers:
            reason = (f"{ev['source_column']} adds {', '.join(qualifiers)} to {f['column']}; a qualified column "
                      f"usually holds a different quantity, so it is not assumed to be {f['column']}")
        else:
            reason = (f"value match {ev['value_match_share']} or name similarity {ev['name_similarity']} "
                      "is below the safe threshold")
        repairs.append(_repair(batch, "rename", {"from": ev["source_column"], "to": f["column"]},
                               {**ev, "competing_candidates": len(rivals), "qualifier_tokens": qualifiers,
                                "implied_unit": implied[0] if implied else None},
                               ev["value_match_share"] * (0.5 + 0.5 * ev["name_similarity"]), auto, reason, f,
                               "value match share x (0.5 + 0.5 x name similarity)"))
    for f in findings:
        if f["kind"] == "date_format_change":
            r = _date_repair(batch, f, frame, contract.col(f["column"]))
            if r:
                repairs.append(r)
        elif f["kind"] == "scale_shift" and f["evidence"]["matches_unit"]:
            ev = f["evidence"]
            unit = ev["matches_unit"]
            col = contract.col(f["column"])
            expected = math.log10(ev["declared_conversions"][unit])
            dev = {k: rnd(abs(math.log10(r) - expected), 4) for k, r in ev["ratios"].items()}
            tails = [dev[k] for k in ("p05", "p95") if k in dev]
            auto = (dev["median"] <= UNIT_MEDIAN_TOLERANCE and len(tails) == 2
                    and all(d <= UNIT_TAIL_TOLERANCE for d in tails))
            ref = ev["baseline_scale"]
            base_median = ref["median"] * ev["declared_conversions"][unit]
            guard = {"median_min": rnd(base_median * 10 ** -UNIT_MEDIAN_TOLERANCE, 4),
                     "median_max": rnd(base_median * 10 ** UNIT_MEDIAN_TOLERANCE, 4)}
            # A converted value far outside the baseline distribution suggests the row was already in the target
            # unit (mixed units), so such rows are held instead of being rescaled.
            plausible = {"min": rnd(ref.get("p05", ref["median"]) / 10, 6),
                         "max": rnd(ref.get("p95", ref["median"]) * 10, 6)}
            repairs.append(_repair(batch, "unit_scale",
                                   {"column": col.name, "from_unit": unit, "to_unit": col.unit,
                                    "factor": str(col.unit_conversions[unit]), "guard": guard,
                                    "plausible_after": plausible},
                                   {**ev, "log10_deviation": dev}, 1 - min(1.0, max(dev.values()) / 0.3), auto,
                                   None if auto else "the scale ratio is not a clean match for the declared unit",
                                   f, "1 - max log10 deviation from the declared factor / 0.3"))
        elif f["kind"] == "unknown_enum_values":
            col = contract.col(f["column"])
            for value, rows in f["evidence"]["values"]:
                c = classify_label(value, col.values)
                auto = c["rule"] in SAFE_CATEGORY_RULES
                repairs.append(_repair(batch, "category_map",
                                       {"column": col.name, "from": value, "to": c["target"], "rule": c["rule"],
                                        "rows_affected": rows},
                                       {"rows": rows, "candidates": c["candidates"], "rule": c["rule"],
                                        "edit_distances": {t: levenshtein(value.casefold(), t.casefold())
                                                           for t in col.values}},
                                       c["confidence"], auto, c["ambiguity"], f,
                                       "fixed confidence per matching rule (case, plural, typo, abbreviation, ...)"))
    for n, r in enumerate(repairs, start=first):
        r["id"] = f"R-{batch}-{n:02d}"
    return repairs


def analyse_batch(batch: str, frame: pd.DataFrame, profile: dict, contract: Contract,
                  baseline: dict | None) -> tuple[list[dict], list[dict]]:
    """Drift findings and repair proposals, with column checks re-run on columns that a safe rename would map.

    A rename whose source name implies a declared unit (weight_g) stays safe only if the unit check on the mapped
    column independently proposes a safe conversion from that unit.
    """
    findings = detect_drift(batch, frame, profile, contract, baseline)
    repairs = propose_repairs(batch, frame, findings, contract)
    safe = {r["detail"]["from"]: r["detail"]["to"] for r in repairs if r["kind"] == "rename" and r["auto_applicable"]}
    if safe:
        renamed = frame.rename(columns=safe)
        more = [f for f in detect_drift(batch, renamed, profile_frame(renamed, contract), contract, baseline,
                                        first=len(findings) + 1)
                if f["column"] in safe.values() and f["kind"] not in RENAME_KINDS]
        findings += more
        repairs += propose_repairs(batch, renamed, more, contract, first=len(repairs) + 1)
    for r in repairs:
        unit = r["evidence"].get("implied_unit") if r["kind"] == "rename" else None
        if unit and r["auto_applicable"]:
            agrees = any(u["kind"] == "unit_scale" and u["auto_applicable"] and u["detail"]["from_unit"] == unit
                         and u["detail"]["column"] == r["detail"]["to"] for u in repairs)
            if not agrees:
                r["auto_applicable"] = False
                r["ambiguity"] = (f"the name {r['detail']['from']} implies {unit}, but the values do not show a clean "
                                  f"{unit} scale; mapping it to {r['detail']['to']} needs a human")
    return findings, repairs
