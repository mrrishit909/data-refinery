"""Ground-truth recall and precision, late-arrival restatements, CLI behaviour and output validation."""

import hashlib
import json
import shutil
import subprocess
import sys
from decimal import Decimal

from conftest import BATCHES, MODES, REPO, load_csv, load_json, request, routed

from datarefinery.__main__ import main
from datarefinery.outputs import validate
from datarefinery.pipeline import run


def _outcome(files, b, n):
    got = files[b]
    if n in got["cleaned"]:
        return "accepted", got["cleaned"][n]
    if n in got["quarantine"]:
        return "quarantined", got["quarantine"][n]
    return "merged", got["merged"].get(n)


def test_ground_truth_recall(runs, ground_truth):
    """Every injected defect is repaired to the true value, merged, flagged, or quarantined with the right reason."""
    for mode in MODES:
        files = routed(runs[mode])
        key = "expected" if mode == "apply-safe" else "expected_propose"
        for b, info in ground_truth["batches"].items():
            for d in info["row_defects"]:
                exp = d[key]
                outcome, row = _outcome(files, b, d["row"])
                assert outcome == exp["outcome"], (mode, b, d)
                if outcome == "accepted":
                    for col, value in exp.get("cleaned", {}).items():
                        assert row[col] == value, (mode, b, d)
                    assert row["restates_period"] == exp.get("restates_period", "")
                elif outcome == "quarantined":
                    assert exp["reason"] in row["reason_codes"].split(";"), (mode, b, d, row["reason_codes"])
                else:
                    assert row == exp["duplicate_of"], (mode, b, d)

    repairs = load_json(runs["apply-safe"] / "repairs.json")["repairs"]
    applied = {(r["batch"], r["kind"]): r for r in repairs if r["applied"]}
    assert applied[("2026-02", "rename")]["detail"] == {"from": "cust_id", "to": "customer_id"}
    assert applied[("2026-02", "date_format")]["detail"]["format"] == "%d/%m/%Y"
    assert applied[("2026-03", "unit_scale")]["detail"]["factor"] == "0.001"
    cleaned = load_csv(runs["apply-safe"] / "cleaned.csv")
    assert {r["currency"] for r in cleaned if r["_batch"] == "2026-03"} == {"USD", "EUR", "GBP"}
    weights = sorted(Decimal(r["weight_kg"]) for r in cleaned if r["_batch"] == "2026-03" and r["weight_kg"])
    assert Decimal("0.05") < weights[len(weights) // 2] < Decimal("10")

    propose = routed(runs["propose"])
    for n, row in propose["2026-02"]["quarantine"].items():
        assert "UNKNOWN_COLUMN_SET" in row["reason_codes"]
    held_weights = [r for r in propose["2026-03"]["quarantine"].values()
                    if json.loads(r["raw"])["weight_kg"] is not None]
    assert held_weights and all("UNRESOLVED_UNIT_SCALE" in r["reason_codes"] for r in held_weights)


def test_ground_truth_precision(runs, ground_truth):
    """In apply-safe mode, the rows held back are exactly the injected unrepairable defects; nothing else."""
    files = routed(runs["apply-safe"])
    for b, info in ground_truth["batches"].items():
        expected_q = {d["row"] for d in info["row_defects"] if d["expected"]["outcome"] == "quarantined"}
        expected_m = {d["row"] for d in info["row_defects"] if d["expected"]["outcome"] == "merged"}
        assert set(files[b]["quarantine"]) == expected_q, b
        assert set(files[b]["merged"]) == expected_m, b


def test_late_arrival_restatement_numbers(runs, ground_truth, tmp_path):
    out = runs["apply-safe"]
    cleaned = load_csv(out / "cleaned.csv")
    late_truth = [d["row"] for d in ground_truth["batches"]["2026-04"]["row_defects"] if d["kind"] == "late_arrival"]
    jan = load_json(out / "reconciliation.json")["periods"]["2026-01"]
    assert jan["closed_by"] == "2026-01" and jan["reported_by"] == ["2026-01", "2026-04"]
    (restated,) = jan["restatements"]
    late_rows = [r for r in cleaned if r["restates_period"] == "2026-01"]
    assert restated["batch"] == "2026-04" and restated["rows"] == len(late_truth) == len(late_rows)
    assert {int(r["_source_row"]) for r in late_rows} == set(late_truth)
    original = sum(Decimal(r["amount_usd"]) for r in cleaned if r["_batch"] == "2026-01")
    late_usd = sum(Decimal(r["amount_usd"]) for r in late_rows)
    assert Decimal(restated["before_usd"]) == original
    assert Decimal(restated["late_usd"]) == late_usd
    assert Decimal(restated["after_usd"]) == original + late_usd == Decimal(jan["accepted_usd"])

    # Without 2026-01 in the run, January is not closed, so nothing is a late arrival.
    only04 = tmp_path / "only04"
    run(request("apply-safe", ["2026-04"]), only04)
    assert not [r for r in load_csv(only04 / "cleaned.csv") if r["restates_period"]]
    assert load_json(only04 / "reconciliation.json")["periods"]["2026-01"]["restatements"] == []


def test_single_batch_unit_drift_uses_contract_reference(tmp_path):
    out = tmp_path / "only03"
    run(request("apply-safe", ["2026-03"]), out)
    (unit,) = [r for r in load_json(out / "repairs.json")["repairs"] if r["kind"] == "unit_scale"]
    assert unit["applied"] and unit["evidence"]["baseline"] == "contract weight_kg reference"


def test_outputs_valid_and_self_checks_pass(runs):
    for mode in MODES:
        assert validate(runs[mode]) == []
        checks = load_json(runs[mode] / "checks.json")
        assert checks["all_passed"] and len(checks["checks"]) >= len(BATCHES) * 2 + 3
        size = sum(p.stat().st_size for p in runs[mode].iterdir())
        assert size < 1_500_000, (mode, size)
        manifest = load_json(runs[mode] / "manifest.json")
        assert manifest["input_manifest_hash"].startswith("sha256:")


def test_input_manifest_hash_matches_definition(runs):
    for mode in MODES:
        m = load_json(runs[mode] / "manifest.json")
        contract_sha = hashlib.sha256((REPO / "contracts" / "orders.v1.json").read_bytes()).hexdigest()
        body = {"params": m["params"], "sources": [s["sha256"] for s in m["sources"]], "contract": contract_sha,
                "package_version": m["package_version"]}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        assert m["input_manifest_hash"] == "sha256:" + hashlib.sha256(canonical).hexdigest()
        assert m["params"] == request(mode)


def test_validate_detects_tampering(runs, tmp_path):
    bad = tmp_path / "bad"
    shutil.copytree(runs["apply-safe"], bad)
    recon = load_json(bad / "reconciliation.json")
    recon["batches"]["2026-02"]["rows_accepted"] += 1
    (bad / "reconciliation.json").write_text(json.dumps(recon))
    problems = validate(bad)
    assert any("identity fails for 2026-02" in p for p in problems)
    assert main(["validate", str(bad)]) == 1
    (bad / "lineage.json").unlink()
    assert validate(bad) == ["missing lineage.json"]


def test_cli_rejects_invalid_requests(tmp_path):
    def cli(params, out_name="out"):
        req = tmp_path / f"req-{out_name}.json"
        req.write_text(params if isinstance(params, str) else json.dumps(params))
        return main(["run", "--request", str(req), "--out", str(tmp_path / out_name)])

    assert cli(request("apply-safe", ["2026-02", "2026-01"]), "desc") == 2
    assert cli({**request("apply-safe"), "contract": "orders.v2"}, "contract") == 2
    assert cli({**request("apply-safe"), "batches": ["2026-05"]}, "unknown") == 2
    assert cli({**request("apply-safe"), "repair_mode": "apply-all"}, "mode") == 2
    assert cli("{not json", "json") == 2
    assert not (tmp_path / "desc").exists()
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "keep.txt").write_text("x")
    assert cli(request("propose", ["2026-01"]), "busy") == 2
    assert (busy / "keep.txt").read_text() == "x"


def test_cli_end_to_end(tmp_path):
    req = tmp_path / "req.json"
    req.write_text(json.dumps(request("apply-safe", ["2026-01", "2026-02"])))
    out = tmp_path / "out"
    proc = subprocess.run([sys.executable, "-m", "datarefinery", "run", "--request", str(req), "--out", str(out)],
                          capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["batches"]["2026-02"]["rows_accepted"] > 1000
    proc = subprocess.run([sys.executable, "-m", "datarefinery", "validate", str(out)], capture_output=True, cwd=REPO)
    assert proc.returncode == 0
