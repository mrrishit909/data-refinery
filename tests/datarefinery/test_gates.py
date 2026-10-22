"""The eight hard release gates. Each test loops over modes and batches internally so `-m gate` collects exactly 8."""

import csv
import json
import re
from collections import deque
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import pytest
from conftest import (
    BATCHES,
    CONTRACTS,
    FILES,
    FIXTURES,
    MODES,
    load_csv,
    load_json,
    read_raw,
    request,
    routed,
    sha256,
)

from datarefinery.contract import contract_failures, load_contract, load_fx
from datarefinery.intake import read_batch
from datarefinery.pipeline import REASON_CODES, process_batch, run

CONTRACT_COLUMNS = ["order_id", "customer_id", "order_date", "amount", "currency", "category", "quantity",
                    "weight_kg", "region"]
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _trim(v):
    return None if v is None or str(v).strip() == "" else str(v).strip()


@pytest.mark.gate
def test_every_row_accounted_for(runs, raw_batches):
    for mode in MODES:
        out = runs[mode]
        recon = load_json(out / "reconciliation.json")["batches"]
        files = routed(out)
        for b in BATCHES:
            rec, got = recon[b], files[b]
            rows_in = len(raw_batches[b])
            assert rec["rows_in"] == rows_in, (mode, b)
            assert rec["rows_in"] == rec["rows_accepted"] + rec["rows_quarantined"] + rec["rows_merged_duplicates"]
            assert rec["identity_holds"] is True
            assert len(got["cleaned"]) == rec["rows_accepted"], (mode, b)
            assert len(got["quarantine"]) == rec["rows_quarantined"], (mode, b)
            assert len(got["merged"]) == rec["rows_merged_duplicates"], (mode, b)
            union = set(got["cleaned"]) | set(got["quarantine"]) | set(got["merged"])
            assert union == set(range(1, rows_in + 1)), (mode, b)
        assert load_json(out / "reconciliation.json")["totals"]["rows_in"] == sum(len(r) for r in raw_batches.values())
        # amounts reconcile too: input = accepted + quarantined + merged for every currency
        for b, rec in recon.items():
            money = rec["amount_by_currency"]
            assert money["balanced"] and money["cleaned_matches_input"], (mode, b)


@pytest.mark.gate
def test_idempotent(runs, tmp_path):
    for mode in MODES:
        again = tmp_path / f"again-{mode}"
        run(request(mode), again)
        first = sorted(p.name for p in runs[mode].iterdir())
        assert first == sorted(p.name for p in again.iterdir())
        for name in first:
            assert (runs[mode] / name).read_bytes() == (again / name).read_bytes(), (mode, name)

    # Feeding the cleaned output back as a clean batch changes nothing and quarantines nothing: both the file
    # verbatim (extra columns are reported as drift and ignored) and its projection onto the contract columns.
    source = runs["apply-safe"] / "cleaned.csv"
    cleaned = load_csv(source)
    for variant in ("verbatim", "contract-columns"):
        feed = tmp_path / f"feed-{variant}"
        feed.mkdir()
        target = feed / "orders_2026-01.csv"
        if variant == "verbatim":
            target.write_bytes(source.read_bytes())
        else:
            with target.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh, lineterminator="\n")
                w.writerow(CONTRACT_COLUMNS)
                for row in cleaned:
                    w.writerow([row[c] for c in CONTRACT_COLUMNS])
        out = tmp_path / f"feed-out-{variant}"
        run(request("apply-safe", ["2026-01"]), out, fixtures_dir=feed)
        assert load_csv(out / "quarantine.csv") == [], variant
        assert load_json(out / "repairs.json")["repairs"] == [], variant
        again = load_csv(out / "cleaned.csv")
        assert len(again) == len(cleaned)
        for before, after in zip(cleaned, again, strict=True):
            for c in [*CONTRACT_COLUMNS, "amount_usd"]:
                assert before[c] == after[c], (variant, c)
            assert after["restates_period"] == ""


@pytest.mark.gate
def test_original_data_preserved(runs, fixture_hashes, raw_batches):
    assert runs  # both runs have executed
    after = {p.name: sha256(p) for p in sorted(FIXTURES.iterdir()) if p.is_file()}
    assert after == fixture_hashes
    for mode in MODES:
        out = runs[mode]
        sources = {m["file"]: m for m in load_json(out / "manifest.json")["sources"]}
        for b, name in FILES.items():
            assert sources[name]["sha256"] == fixture_hashes[name]
            assert sources[name]["bytes"] == (FIXTURES / name).stat().st_size
        checks = {c["id"]: c["passed"] for c in load_json(out / "checks.json")["checks"]}
        assert all(checks[f"raw_unchanged:{name}"] for name in FILES.values())
        for row in load_csv(out / "quarantine.csv"):
            assert json.loads(row["raw"]) == raw_batches[row["_batch"]][int(row["_source_row"])], (mode, row["_source_row"])
        for b, samples in load_json(out / "samples.json")["batches"].items():
            for s in samples:
                assert s["raw"] == raw_batches[b][s["_source_row"]]


@pytest.mark.gate
def test_no_silent_row_drops(runs, raw_batches):
    for mode in MODES:
        files = routed(runs[mode])
        for b in BATCHES:
            got = files[b]
            for n in raw_batches[b]:
                places = [n in got["cleaned"], n in got["quarantine"], n in got["merged"]]
                assert sum(places) == 1, (mode, b, n, places)
            for n, row in got["quarantine"].items():
                codes = row["reason_codes"].split(";")
                assert codes and all(c in REASON_CODES for c in codes), (mode, b, n, codes)
            for n, first in got["merged"].items():
                assert first < n and raw_batches[b][first] == raw_batches[b][n], (mode, b, n)
                assert first in got["cleaned"] or first in got["quarantine"]


@pytest.mark.gate
def test_no_invented_values(runs, raw_batches):
    fx = load_json(CONTRACTS / "fx_fixed-2026q1.json")
    for mode in MODES:
        out = runs[mode]
        applied = [r for r in load_json(out / "repairs.json")["repairs"] if r["applied"]]
        cleaned = load_csv(out / "cleaned.csv")
        ids = [r["order_id"] for r in cleaned]
        assert len(ids) == len(set(ids))
        for b in BATCHES:
            mine = [r for r in applied if r["batch"] == b]
            rename = {r["detail"]["to"]: r["detail"]["from"] for r in mine if r["kind"] == "rename"}
            date_fmt = next((r["detail"]["format"] for r in mine if r["kind"] == "date_format"), None)
            factor = next((Decimal(r["detail"]["factor"]) for r in mine if r["kind"] == "unit_scale"), Decimal(1))
            cmap = {r["detail"]["from"]: r["detail"]["to"] for r in mine if r["kind"] == "category_map"}
            raw = raw_batches[b]
            raw_ids = {_trim(r.get("order_id")) for r in raw.values()}
            for row in (r for r in cleaned if r["_batch"] == b):
                src = raw[int(row["_source_row"])]
                assert row["_source_file"] == FILES[b]
                for col in CONTRACT_COLUMNS:
                    rv, ov = _trim(src.get(rename.get(col, col))), row[col] or None
                    assert (rv is None) == (ov is None), (mode, b, row["_source_row"], col)
                    if rv is None:
                        continue
                    if col == "order_date":
                        expect = rv if ISO.match(rv) else datetime.strptime(rv, date_fmt).date().isoformat()
                        assert ov == expect
                    elif col == "amount":
                        assert Decimal(ov) == Decimal(rv)
                    elif col == "weight_kg":
                        assert Decimal(ov) == Decimal(rv) * factor
                    elif col == "quantity":
                        assert int(ov) == int(rv)
                    elif col == "category":
                        assert ov == cmap.get(rv, rv)
                    else:
                        assert ov == rv, (col, rv, ov)
                rate = Decimal(fx["rates"][row["currency"]])
                assert row["amount_usd"] == str((Decimal(row["amount"]) * rate).quantize(Decimal("0.01"), ROUND_HALF_UP))
                assert row["order_id"] in raw_ids


@pytest.mark.gate
def test_ambiguity_flagged_not_applied(runs, ground_truth):
    targets = {"Elec", "Home & Garden"}
    truth_rows = {d["row"]: d["raw"] for d in ground_truth["batches"]["2026-03"]["row_defects"]
                  if d["kind"] == "category_ambiguous"}
    assert set(truth_rows.values()) == targets
    for mode in MODES:
        out = runs[mode]
        amb = load_json(out / "ambiguities.json")["ambiguities"]
        flagged = {a["value"]: a for a in amb if a["column"] == "category"}
        assert targets <= set(flagged), mode
        assert all(flagged[v]["reason"] for v in targets)
        repairs = {r["id"]: r for r in load_json(out / "repairs.json")["repairs"]}
        for v in targets:
            rep = repairs[flagged[v]["repair"]]
            assert rep["auto_applicable"] is False and rep["applied"] is False and rep["ambiguity"]
        plan = load_json(out / "plan.json")["batches"]["2026-03"]
        assert not {flagged[v]["repair"] for v in targets} & set(plan["applied_repairs"])
        files = routed(out)["2026-03"]
        for n, label in truth_rows.items():
            assert n not in files["cleaned"]
            assert "AMBIGUOUS_CATEGORY" in files["quarantine"][n]["reason_codes"].split(";"), (mode, n, label)
            assert json.loads(files["quarantine"][n]["raw"])["category"] == label


@pytest.mark.gate
def test_lineage_inspectable(runs):
    for mode in MODES:
        out = runs[mode]
        manifest = {m["file"]: m for m in load_json(out / "manifest.json")["sources"]}
        seen = set()
        for name in ("cleaned.csv", "quarantine.csv"):
            for row in load_csv(out / name):
                key = (row["_batch"], int(row["_source_row"]))
                assert key not in seen, (mode, name, key)
                seen.add(key)
                assert row["_source_file"] == FILES[row["_batch"]]
                assert 1 <= key[1] <= manifest[row["_source_file"]]["rows"]

        lineage = load_json(out / "lineage.json")
        nodes = {n["id"]: n for n in lineage["nodes"]}
        nxt = {}
        for e in lineage["edges"]:
            assert e["from"] in nodes and e["to"] in nodes
            nxt.setdefault(e["from"], []).append(e["to"])
        recon = load_json(out / "reconciliation.json")["batches"]
        for b in BATCHES:
            src = f"source:{b}"
            assert nodes[src]["sha256"] == manifest[FILES[b]]["sha256"]
            reached, queue = set(), deque([src])
            while queue:
                cur = queue.popleft()
                for t in nxt.get(cur, []):
                    if t not in reached:
                        reached.add(t)
                        queue.append(t)
            assert "output:cleaned.csv" in reached and "output:quarantine.csv" in reached
            steps = [n for n in reached if nodes[n]["type"] == "step"]
            assert steps and all(nodes[s]["batch"] == b for s in steps)
            names = {nodes[s]["name"].split(" ")[0] for s in steps}
            assert {"intake", "deduplicate", "validate_contract", "currency_normalize"} <= names
            if recon[b]["rows_merged_duplicates"]:
                assert "output:reconciliation.json" in reached
        cleaned_header = list(load_csv(out / "cleaned.csv")[0])
        for col in cleaned_header:
            spec = lineage["columns"][col]
            for b, d in spec.get("by_batch", {}).items():
                assert all(s in nodes for s in d["steps"])
                if spec["kind"] == "contract" and d["source_column"]:
                    assert d["source_column"] in manifest[FILES[b]]["columns"]
        if mode == "apply-safe":
            assert lineage["columns"]["customer_id"]["by_batch"]["2026-02"]["source_column"] == "cust_id"


@pytest.mark.gate
def test_repairs_pass_original_and_drifted(runs):
    contract, fx = load_contract(), load_fx()
    applied = [r for r in load_json(runs["apply-safe"] / "repairs.json")["repairs"] if r["applied"]]
    assert {"rename", "date_format"} <= {r["kind"] for r in applied if r["batch"] == "2026-02"}
    for mode in MODES:
        assert not [r for r in load_json(runs[mode] / "repairs.json")["repairs"] if r["batch"] == "2026-01"]

    raw01, _ = read_batch(FIXTURES / FILES["2026-01"])
    raw02, _ = read_batch(FIXTURES / FILES["2026-02"])
    plain01 = process_batch("2026-01", raw01, FILES["2026-01"], contract, fx, [], {}, {}, set())
    with01 = process_batch("2026-01", raw01, FILES["2026-01"], contract, fx, applied, {}, {}, set())
    with02 = process_batch("2026-02", raw02, FILES["2026-02"], contract, fx, applied, {}, {}, set())
    for res in (with01, with02):
        accepted = res.canonical.loc[res.accepted, contract.names].reset_index(drop=True)
        assert len(accepted) > 0
        assert contract_failures(contract, accepted, enforce_unique=True) == [], res.batch
    assert with01.applied == [], "repairs must be no-ops on the clean baseline"
    assert with01.accepted == plain01.accepted == list(raw01.index)
    assert with01.canonical.equals(plain01.canonical)
    assert {r["id"] for r in applied if r["batch"] == "2026-02"} <= set(with02.applied)

    # 2026-01 accepted values are identical across modes in the full runs as well.
    def rows01(mode):
        return [r for r in load_csv(runs[mode] / "cleaned.csv") if r["_batch"] == "2026-01"]

    assert rows01("apply-safe") == rows01("propose")
    assert len(rows01("apply-safe")) == len(read_raw(FIXTURES / FILES["2026-01"]))
