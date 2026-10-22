"""Adversarial probes of the pipeline's correctness claims, written by an independent reviewer.

Every crafted batch is written to a temporary fixtures directory; the committed fixtures are only read.
Tests marked xfail(strict=True) reproduce a confirmed defect: they stay xfailed while the defect exists and turn the
suite red once it is fixed (remove the marker then). Each pins the exception it currently fails with, so a change in
failure mode also shows up red. Unmarked tests probe a claim that held and remain as regression guards.
"""

import csv
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from decimal import Decimal

import openpyxl
import pandas as pd
import pytest
from conftest import FILES, FIXTURES, REPO, load_csv, load_json, request

from datarefinery.__main__ import main
from datarefinery.contract import load_contract, load_fx
from datarefinery.intake import IntakeError, read_batch
from datarefinery.outputs import validate
from datarefinery.pipeline import process_batch, run

COLS = ["order_id", "customer_id", "order_date", "amount", "currency", "category", "quantity", "weight_kg", "region"]


def _row(i, **overrides):
    r = {"order_id": f"ORD-1{i:05d}", "customer_id": f"CUS-{10000 + i}", "order_date": f"2026-01-{i % 28 + 1:02d}",
         "amount": "10.00", "currency": "USD", "category": "Home", "quantity": "1",
         "weight_kg": f"{1 + i % 3}.500", "region": "East"}
    return {**r, **overrides}


def _csv(rows, header=None):
    header = header or list(rows[0])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if r.get(c) is None else r[c] for c in header])
    return buf.getvalue().encode("utf-8")


def _xlsx(rows, cells=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(COLS)
    for r in rows:
        ws.append([r[c] for c in COLS])
    for ref, value in (cells or {}).items():
        ws[ref] = value
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _with_dimension(data, ref):
    """Rewrite the sheet's <dimension> record, as a non-Excel writer with a stale used-range would leave it."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                body, n = re.subn(rb'<dimension ref="[^"]*" ?/>', f'<dimension ref="{ref}"/>'.encode(), body)
                if n != 1:
                    raise RuntimeError("test setup: no <dimension> element to rewrite")
            dst.writestr(info, body)
    return out.getvalue()


def _run(where, files, batches, mode="apply-safe"):
    fx = where / "fx"
    fx.mkdir(parents=True)
    for name, data in files.items():
        (fx / name).write_bytes(data)
    out = where / "out"
    run(request(mode, batches), out, fixtures_dir=fx)
    return out


def _cleaned(out):
    return {r["order_id"]: r for r in load_csv(out / "cleaned.csv")}


def _quarantined(out):
    return {json.loads(r["raw"]).get("order_id"): r for r in load_csv(out / "quarantine.csv")}


def _grams_rows(n=300):
    """The first n rows of the committed 2026-01 batch with weight_kg rewritten as whole grams (same distribution)."""
    rows = load_csv(FIXTURES / FILES["2026-01"])[:n]
    return [{**r, "weight_kg": str(int(Decimal(r["weight_kg"]) * 1000)) if r["weight_kg"] else ""} for r in rows]


def _max_accepted_weight(out):
    return max(Decimal(r["weight_kg"]) for r in load_csv(out / "cleaned.csv") if r["weight_kg"])


# ---------------------------------------------------------------------------------------------------------------
# Confirmed defects: fixed ones are plain regression tests; DR-20a remains a strict xfail (documented limitation)
# ---------------------------------------------------------------------------------------------------------------

def _undecidable_dates():
    return {"orders_2026-01.csv": _csv([_row(i, order_date=f"{1 + i % 12:02d}/01/2026") for i in range(24)])}


def _two_rename_candidates():
    rows = []
    for i in range(30):
        r = _row(i)
        cid = r.pop("customer_id")
        rows.append({**r, "cust_id": cid, "customer": cid})
    return {"orders_2026-01.csv": _csv(rows)}


def _unclean_unit_scale():
    base = load_csv(FIXTURES / FILES["2026-01"])[:300]
    grams = _grams_rows()
    return {"orders_2026-01.csv": _csv([base[i] if i % 25 == 0 else grams[i] for i in range(300)])}


@pytest.mark.parametrize("case", ["undecidable-dates-propose", "undecidable-dates-apply", "two-rename-candidates",
                                  "unclean-unit-scale", "quarantine-csv-fed-back"])
def test_dr01_non_category_ambiguity_is_reported_not_crashed(case, runs, tmp_path):
    mode = "propose" if case.endswith("propose") else "apply-safe"
    if case.startswith("undecidable"):
        files = _undecidable_dates()
    elif case == "two-rename-candidates":
        files = _two_rename_candidates()
    elif case == "unclean-unit-scale":
        files = _unclean_unit_scale()
    else:
        files = {"orders_2026-01.csv": (runs["apply-safe"] / "quarantine.csv").read_bytes()}
    out = _run(tmp_path, files, ["2026-01"], mode)
    repairs = load_json(out / "repairs.json")["repairs"]
    flagged = {a["repair"] for a in load_json(out / "ambiguities.json")["ambiguities"]}
    assert [r for r in repairs if not r["auto_applicable"]], "probe must produce a non-auto repair"
    assert {r["id"] for r in repairs if not r["auto_applicable"]} <= flagged
    assert load_json(out / "checks.json")["all_passed"]


def test_dr02_xlsx_stale_dimension_does_not_drop_rows(tmp_path):
    data = _with_dimension(_xlsx([_row(i, order_date="2026-04-02") for i in range(100)]), "A1:I11")
    try:
        out = _run(tmp_path, {"orders_2026-04.xlsx": data}, ["2026-04"])
    except IntakeError:
        return  # rejecting the file loudly is an acceptable fix
    rec = load_json(out / "reconciliation.json")["batches"]["2026-04"]
    assert rec["rows_in"] == 100, f"{100 - rec['rows_in']} data rows vanished while identity_holds={rec['identity_holds']}"


def test_dr03_one_junk_weight_cell_does_not_disable_unit_detection(tmp_path):
    rows = _grams_rows()
    rows.append({**rows[0], "order_id": "ORD-999999", "weight_kg": "n/a"})
    out = _run(tmp_path, {"orders_2026-01.csv": _csv(rows)}, ["2026-01"])
    assert _max_accepted_weight(out) < 100, "gram values were accepted as kilograms"


def test_dr04_renamed_weight_column_still_gets_unit_check(tmp_path):
    rows = [{("weight_g" if k == "weight_kg" else k): v for k, v in r.items()} for r in _grams_rows()]
    out = _run(tmp_path, {"orders_2026-01.csv": _csv(rows)}, ["2026-01"])
    assert _max_accepted_weight(out) < 100, "gram values were accepted as kilograms"


def test_dr05_mixed_day_month_dates_are_not_moved_to_other_months(tmp_path):
    feb = [_row(100 + i, order_date=f"{13 + i % 15:02d}/02/2026") for i in range(20)]  # DD/MM, day > 12
    feb += [_row(200 + i, order_date=f"02/{1 + i % 9:02d}/2026") for i in range(10)]   # MM/DD, also February
    files = {"orders_2026-01.csv": _csv([_row(i) for i in range(30)]), "orders_2026-02.csv": _csv(feb)}
    out = _run(tmp_path, files, ["2026-01", "2026-02"])
    moved = sorted((r["order_id"], r["order_date"], r["restates_period"]) for r in load_csv(out / "cleaned.csv")
                   if r["_batch"] == "2026-02" and not r["order_date"].startswith("2026-02"))
    assert moved == [], f"February orders accepted in other months: {moved[:4]}"


def test_dr06_out_of_range_year_is_quarantined_not_fatal(tmp_path):
    rows = [_row(i) for i in range(20)] + [_row(20, order_date="3026-01-05")]
    out = _run(tmp_path, {"orders_2026-01.csv": _csv(rows)}, ["2026-01"])
    assert "ORD-100020" in _quarantined(out)
    assert len(_cleaned(out)) == 20


def test_dr07_tax_amount_is_not_auto_renamed_to_amount(tmp_path):
    rows = []
    for i in range(100):
        r = _row(i)
        r.pop("amount")
        rows.append({**r, "tax_amount": "7.00"})
    out = _run(tmp_path, {"orders_2026-01.csv": _csv(rows)}, ["2026-01"])
    applied = [r["detail"] for r in load_json(out / "repairs.json")["repairs"] if r["applied"] and r["kind"] == "rename"]
    assert {"from": "tax_amount", "to": "amount"} not in applied


def test_dr08_one_edit_label_with_other_meaning_is_not_auto_mapped(tmp_path):
    out = _run(tmp_path, {"orders_2026-01.csv": _csv([_row(i) for i in range(20)] + [_row(20, category="Tops")])},
               ["2026-01"])
    assert _cleaned(out).get("ORD-100020", {}).get("category") != "Toys"


@pytest.mark.parametrize("case", ["lookalike-order-id", "fullwidth-year"])
def test_dr09_unicode_digits_do_not_pass_ascii_contract_patterns(case, tmp_path):
    fullwidth = "\uff11\uff10\uff10\uff10\uff10"  # five fullwidth digits
    if case == "lookalike-order-id":  # NFKC-equal to ORD-100000, so the same order is accepted twice
        lookalike = "ORD-" + fullwidth + "0"
        rows = [_row(i) for i in range(20)] + [_row(0, order_id=lookalike)]
        out = _run(tmp_path, {"orders_2026-01.csv": _csv(rows)}, ["2026-01"])
        assert lookalike not in _cleaned(out)
    else:  # a fullwidth year is accepted verbatim, opens a phantom period and escapes the January restatement
        files = {"orders_2026-01.csv": _csv([_row(i) for i in range(3)]),
                 "orders_2026-02.csv": _csv([_row(9, order_date="\uff12\uff10\uff12\uff16-01-05")])}
        out = _run(tmp_path, files, ["2026-01", "2026-02"])
        row = _cleaned(out).get("ORD-100009")
        assert row is None or (row["order_date"], row["restates_period"]) == ("2026-01-05", "2026-01"), row


@pytest.mark.parametrize("column,value", [("quantity", "9" * 20), ("amount", "1" + "0" * 30 + ".00")])
def test_dr10_oversized_number_is_quarantined_not_fatal(column, value, tmp_path):
    rows = [_row(i) for i in range(20)] + [_row(20, **{column: value})]
    out = _run(tmp_path, {"orders_2026-01.csv": _csv(rows)}, ["2026-01"])
    assert "ORD-100020" in _quarantined(out)


def test_dr11_ambiguity_resolution_names_the_actual_reason_code(tmp_path):
    rows = [_row(i) for i in range(10)] + [_row(20, currency="US"), _row(21, region="Northeast")]
    out = _run(tmp_path, {"orders_2026-01.csv": _csv(rows)}, ["2026-01"])
    held = {json.loads(r["raw"])[col]: set(r["reason_codes"].split(";"))
            for r in load_csv(out / "quarantine.csv") for col in ("currency", "region")}
    for a in load_json(out / "ambiguities.json")["ambiguities"]:
        code = re.search(r"quarantined as (\w+)", a["resolution"]).group(1)
        assert code in held[a["value"]], (a["value"], code, held[a["value"]])


def test_dr12_whitespace_only_duplicate_is_not_reported_as_different_content(tmp_path):
    rows = [_row(i) for i in range(20)] + [_row(0, order_id="ORD-100000 ")]
    out = _run(tmp_path, {"orders_2026-01.csv": _csv(rows)}, ["2026-01"])
    kept = "ORD-100000" in _cleaned(out)
    details = [r["reason_detail"] for r in load_csv(out / "quarantine.csv")]
    assert kept or not any("different content" in d for d in details), details


def test_dr13_xlsx_formula_without_cached_value_is_not_accepted_as_null(tmp_path):
    data = _xlsx([_row(i, order_date="2026-04-02") for i in range(10)], cells={"H2": "=2.5*1"})  # H2: weight_kg
    try:
        out = _run(tmp_path, {"orders_2026-04.xlsx": data}, ["2026-04"])
    except IntakeError:
        return
    assert _cleaned(out).get("ORD-100000", {}).get("weight_kg") != "", "formula value silently became null"


def test_dr14_json_repeated_key_is_not_silently_resolved(tmp_path):
    recs = [{c: _row(i, order_date="2026-03-02")[c] for c in COLS} for i in range(3)]
    text = json.dumps(recs).replace('"order_id": "ORD-100002"', '"order_id": "ORD-100002", "order_id": "ORD-999999"')
    try:
        out = _run(tmp_path, {"orders_2026-03.json": text.encode()}, ["2026-03"])
    except IntakeError:
        return
    accepted = _cleaned(out)
    assert "ORD-999999" not in accepted and "ORD-100002" not in accepted


def test_dr15_non_utf8_csv_is_an_input_error(tmp_path):
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "orders_2026-01.csv").write_bytes(_csv([_row(0, region="North")]).decode().replace("North", "N\xf6rth")
                                            .encode("latin-1"))
    req = tmp_path / "req.json"
    req.write_text(json.dumps(request("apply-safe", ["2026-01"])))
    assert main(["run", "--request", str(req), "--out", str(tmp_path / "out"), "--fixtures", str(fx)]) == 2


def test_dr16_in_bound_input_yields_valid_outputs(tmp_path):
    files = {}
    for k, m in enumerate(["01", "02", "03", "04"], start=1):
        files[f"orders_2026-{m}.csv"] = _csv([_row(i, order_id=f"ORD-{k}{i:05d}", order_date=f"2026-{m}-{i % 28 + 1:02d}")
                                             for i in range(5000)])
    try:
        out = _run(tmp_path, files, ["2026-01", "2026-02", "2026-03", "2026-04"])
    except IntakeError:
        return  # tightening the intake bounds is an acceptable fix
    assert validate(out) == []


def test_dr17_date_repair_rechecks_its_guard_when_applied(runs):
    contract, fx = load_contract(), load_fx()
    applied = [r for r in load_json(runs["apply-safe"] / "repairs.json")["repairs"] if r["applied"]]
    us = pd.DataFrame([[_row(i, order_date=f"03/{1 + i:02d}/2026")[c] for c in COLS] for i in range(20)],
                      columns=COLS, dtype=object, index=pd.RangeIndex(1, 21))
    res = process_batch("2026-03", us, "orders_2026-03.csv", contract, fx, applied, {}, {}, set())
    dates = sorted({res.canonical.at[r, "order_date"] for r in res.accepted})
    assert all(d.startswith("2026-03") for d in dates), f"March orders accepted as {dates[:4]}"


def test_dr18_every_passed_repair_is_applied_or_skipped():
    contract, fx = load_contract(), load_fx()
    raw, _ = read_batch(FIXTURES / FILES["2026-01"])
    foreign = [{"id": "R-x-01", "kind": "date_format", "batch": "x", "detail": {"column": "ship_date",
                                                                                  "format": "%d/%m/%Y"}}]
    res = process_batch("2026-01", raw, FILES["2026-01"], contract, fx, foreign, {}, {}, set())
    assert "R-x-01" in set(res.applied) | {s["repair"] for s in res.skipped}


def test_dr19_amount_with_three_decimal_places_is_rejected(tmp_path):
    out = _run(tmp_path, {"orders_2026-01.csv": _csv([_row(i) for i in range(5)] + [_row(9, amount="10.000")])},
               ["2026-01"])
    assert "BAD_NUMBER" in _quarantined(out).get("ORD-100009", {}).get("reason_codes", "")


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="DR-20a: unit handling is batch-level only; 30% gram rows in a kg batch pass unflagged")
def test_dr20a_minority_gram_rows_are_not_accepted_as_kg(tmp_path):
    base, grams = load_csv(FIXTURES / FILES["2026-01"])[:300], _grams_rows()
    rows = [grams[i] if i % 10 < 3 else base[i] for i in range(300)]
    out = _run(tmp_path, {"orders_2026-01.csv": _csv(rows)}, ["2026-01"])
    assert _max_accepted_weight(out) < 100, "gram values were accepted as kilograms"


def test_dr20b_kg_rows_in_gram_batch_are_not_silently_rescaled(tmp_path):
    base, rows = load_csv(FIXTURES / FILES["2026-01"])[:300], _grams_rows()
    kg = {5: "15", 105: "2", 205: "8"}
    for i, v in kg.items():
        rows[i] = {**base[i], "weight_kg": v}
    out = _run(tmp_path, {"orders_2026-01.csv": _csv(rows)}, ["2026-01"])
    assert any(r["applied"] for r in load_json(out / "repairs.json")["repairs"] if r["kind"] == "unit_scale")
    cleaned = _cleaned(out)
    rescaled = {base[i]["order_id"]: cleaned[base[i]["order_id"]]["weight_kg"] for i in kg
                if base[i]["order_id"] in cleaned}
    assert rescaled == {}, f"kg rows silently divided by 1000: {rescaled}"


# ---------------------------------------------------------------------------------------------------------------
# Claims that held under attack (regression guards)
# ---------------------------------------------------------------------------------------------------------------

def test_full_run_is_byte_identical_across_hash_seeds(tmp_path):
    """Idempotency across processes: set iteration order (PYTHONHASHSEED) must not leak into any output."""
    req = tmp_path / "req.json"
    req.write_text(json.dumps(request("apply-safe")))
    outs = []
    for seed in ("1", "424242"):
        out = tmp_path / f"out-{seed}"
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run([sys.executable, "-m", "datarefinery", "run", "--request", str(req), "--out", str(out)],
                              capture_output=True, text=True, cwd=REPO, env=env)
        assert proc.returncode == 0, proc.stderr
        outs.append(out)
    names = sorted(p.name for p in outs[0].iterdir())
    assert names == sorted(p.name for p in outs[1].iterdir())
    for name in names:
        assert (outs[0] / name).read_bytes() == (outs[1] / name).read_bytes(), name


def test_numbers_are_never_coerced_or_rounded(tmp_path):
    bad = {30: "1e3", 31: "1,000.00", 32: "$10.00", 33: "10.005", 34: "+10.00", 35: "10.0.0"}
    rows = [_row(i) for i in range(10)] + [_row(n, amount=a) for n, a in bad.items()]
    rows += [_row(40, amount="-0.00")]
    out = _run(tmp_path, {"orders_2026-01.csv": _csv(rows)}, ["2026-01"])
    held, kept = _quarantined(out), _cleaned(out)
    for n in bad:
        assert held[f"ORD-1000{n}"]["reason_codes"] == "BAD_NUMBER", n
    assert kept["ORD-100040"]["amount"] == "-0.00"  # kept verbatim: not rewritten, not flagged negative
    raw = {json.loads(r["raw"])["order_id"]: json.loads(r["raw"]) for r in load_csv(out / "quarantine.csv")}
    assert {n: raw[f"ORD-1000{n}"]["amount"] for n in bad} == bad  # raw text preserved exactly


def test_fx_half_up_at_half_cent_boundaries(tmp_path):
    # 1.50 x 1.27 = 1.905 and 2.50 x 1.27 = 3.175: ROUND_HALF_UP gives 1.91 / 3.18 where HALF_EVEN gives 1.90 / 3.18.
    rows = [_row(1, amount="1.50", currency="GBP"), _row(2, amount="2.50", currency="GBP"),
            _row(3, amount="0.50", currency="GBP"), _row(4, amount="0.05", currency="EUR")]
    out = _run(tmp_path, {"orders_2026-01.csv": _csv(rows)}, ["2026-01"])
    got = {k: v["amount_usd"] for k, v in _cleaned(out).items()}
    assert got == {"ORD-100001": "1.91", "ORD-100002": "3.18", "ORD-100003": "0.64", "ORD-100004": "0.05"}


def test_late_arrival_into_first_period_restates_it(tmp_path):
    files = {"orders_2026-01.csv": _csv([_row(i) for i in range(5)]),
             "orders_2026-02.csv": _csv([_row(100 + i, order_date="2026-01-15") for i in range(3)]
                                        + [_row(200, order_date="2026-02-02")])}
    out = _run(tmp_path, files, ["2026-01", "2026-02"])
    jan = load_json(out / "reconciliation.json")["periods"]["2026-01"]
    assert jan["closed_by"] == "2026-01" and jan["accepted_usd"] == "80.00"
    assert jan["restatements"] == [{"batch": "2026-02", "rows": 3, "late_usd": "30.00", "before_usd": "50.00",
                                    "after_usd": "80.00"}]
    late = {k for k, r in _cleaned(out).items() if r["restates_period"]}
    assert late == {"ORD-100100", "ORD-100101", "ORD-100102"}


def test_cross_batch_duplicate_with_new_content_is_held(tmp_path):
    files = {"orders_2026-01.csv": _csv([_row(i) for i in range(5)]),
             "orders_2026-02.csv": _csv([_row(0, amount="99.00", order_date="2026-02-01"),
                                         _row(0, order_id=" ORD-100001 ", order_date="2026-02-01"),
                                         _row(50, order_date="2026-02-03")])}
    out = _run(tmp_path, files, ["2026-01", "2026-02"])
    q = load_csv(out / "quarantine.csv")
    assert sorted(r["reason_codes"] for r in q) == ["DUPLICATE_PRIOR_BATCH", "DUPLICATE_PRIOR_BATCH"]
    kept = _cleaned(out)
    assert kept["ORD-100000"]["amount"] == "10.00" and kept["ORD-100000"]["_batch"] == "2026-01"


def test_bom_crlf_and_quoted_newline_are_accounted_and_raw_preserved(tmp_path):
    rows = [_row(i) for i in range(5)] + [_row(9, category="Home\nGarden")]
    data = ("\ufeff" + _csv(rows).decode().replace("\n", "\r\n").replace("Home\r\nGarden", "Home\nGarden"))
    out = _run(tmp_path, {"orders_2026-01.csv": data.encode("utf-8")}, ["2026-01"])
    rec = load_json(out / "reconciliation.json")["batches"]["2026-01"]
    assert (rec["rows_in"], rec["rows_accepted"], rec["rows_quarantined"]) == (6, 5, 1)
    (held,) = load_csv(out / "quarantine.csv")
    assert held["_source_row"] == "6" and json.loads(held["raw"])["category"] == "Home\nGarden"
    assert held["reason_codes"] == "AMBIGUOUS_CATEGORY"


def test_header_only_and_empty_files(tmp_path):
    out = _run(tmp_path / "a", {"orders_2026-01.csv": (",".join(COLS) + "\n").encode()}, ["2026-01"])
    rec = load_json(out / "reconciliation.json")["batches"]["2026-01"]
    assert rec["rows_in"] == 0 and rec["identity_holds"] and validate(out) == []
    with pytest.raises(IntakeError, match="empty"):
        _run(tmp_path / "b", {"orders_2026-01.csv": b""}, ["2026-01"])


def test_gram_batch_on_reference_distribution_is_converted(tmp_path):
    """Control for DR-03/DR-04: the same gram rows, clean and under the contract name, are detected and converted."""
    out = _run(tmp_path, {"orders_2026-01.csv": _csv(_grams_rows())}, ["2026-01"])
    (unit,) = [r for r in load_json(out / "repairs.json")["repairs"] if r["kind"] == "unit_scale"]
    assert unit["applied"] and unit["detail"]["factor"] == "0.001"
    assert _max_accepted_weight(out) < 100
