"""Unit tests: fixture determinism, profiling, drift detection, repair rules, intake bounds."""

import io
import zipfile

import pandas as pd
import pytest
from conftest import FILES, FIXTURES

from datarefinery import fixtures
from datarefinery.analysis import classify_label, detect_drift, profile_frame, propose_repairs
from datarefinery.contract import load_contract
from datarefinery.intake import MAX_ROWS, IntakeError, read_batch

CONTRACT = load_contract()
CATEGORIES = ["Electronics", "Home", "Grocery", "Apparel", "Toys"]


def frame(columns: dict[str, list]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    return pd.DataFrame(columns, dtype=object, index=pd.RangeIndex(1, n + 1))


def clean_rows(n: int, **overrides) -> dict[str, list]:
    cols = {
        "order_id": [f"ORD-1{i:05d}" for i in range(n)],
        "customer_id": [f"CUS-{10000 + i}" for i in range(n)],
        "order_date": [f"2026-02-{i % 28 + 1:02d}" for i in range(n)],
        "amount": ["10.00"] * n,
        "currency": ["USD"] * n,
        "category": ["Home"] * n,
        "quantity": ["1"] * n,
        "weight_kg": [f"{1 + i % 3}.500" for i in range(n)],
        "region": ["East"] * n,
    }
    cols.update(overrides)
    return cols


def analyse(cols, batch="2026-02", baseline=None):
    f = frame(cols)
    p = profile_frame(f, CONTRACT)
    findings = detect_drift(batch, f, p, CONTRACT, baseline)
    return p, findings, propose_repairs(batch, f, findings, CONTRACT)


def test_generator_reproduces_committed_fixtures():
    """CSV, JSON and ground truth are byte-identical. XLSX is compared by zip member names and decompressed member
    bytes, because deflate output can differ between zlib builds even when the workbook content is identical."""
    built = fixtures.build()
    assert sorted(built) == sorted([*FILES.values(), "ground_truth.json"])
    for name, data in built.items():
        committed = (FIXTURES / name).read_bytes()
        if name.endswith(".xlsx"):
            with zipfile.ZipFile(io.BytesIO(data)) as a, zipfile.ZipFile(io.BytesIO(committed)) as b:
                assert a.namelist() == b.namelist()
                for member in a.namelist():
                    assert a.read(member) == b.read(member), member
        else:
            assert data == committed, name
    assert fixtures.build()["orders_2026-04.xlsx"] == built["orders_2026-04.xlsx"]


def test_fixture_sizes_and_shapes():
    for batch, name in FILES.items():
        f, manifest = read_batch(FIXTURES / name)
        assert 1000 <= manifest["rows"] <= 1300, batch
        assert f.index[0] == 1 and f.index[-1] == manifest["rows"]
    assert read_batch(FIXTURES / FILES["2026-02"])[1]["columns"][1] == "cust_id"


def test_profile_column_statistics():
    p = profile_frame(frame({"amount": ["1.00", "2.00", "3.00", None, " "], "order_date": ["2026-01-02", "02/01/2026",
                                                                                         "x", None, None]}), CONTRACT)
    a = p["amount"]
    assert (a["null_count"], a["null_rate"], a["distinct_count"], a["inferred_type"]) == (2, 0.4, 3, "decimal")
    assert (a["min"], a["max"]) == (1.0, 3.0)
    assert a["scale"] == {"p05": 1.1, "median": 2.0, "p95": 2.9}
    d = p["order_date"]["date_formats"]
    assert d == {"%Y-%m-%d": 1, "%d/%m/%Y": 1, "%m/%d/%Y": 1, "unparsed": 1}


def test_profile_detects_currencies():
    p = profile_frame(frame({"currency": ["USD", "EUR", "EUR", None]}), CONTRACT)
    assert p["currency"]["currencies"] == {"EUR": 2, "USD": 1}
    assert p["currency"]["inferred_type"] == "string"


def test_clean_batch_has_no_drift_or_repairs():
    _, findings, repairs = analyse(clean_rows(60))
    assert findings == [] and repairs == []


def test_drift_rename_candidate_is_safe_when_unique():
    cols = clean_rows(50)
    cols["cust_id"] = cols.pop("customer_id")
    _, findings, repairs = analyse(cols)
    kinds = {f["kind"] for f in findings}
    assert {"missing_column", "extra_column", "rename_candidate"} <= kinds
    (rename,) = [r for r in repairs if r["kind"] == "rename"]
    assert rename["detail"] == {"from": "cust_id", "to": "customer_id"}
    assert rename["auto_applicable"] and rename["evidence"]["value_match_share"] == 1.0


def test_drift_rename_with_two_matching_columns_is_ambiguous():
    cols = clean_rows(50)
    cols["cust_id"] = cols.pop("customer_id")
    cols["customer"] = list(cols["cust_id"])
    _, _, repairs = analyse(cols)
    renames = [r for r in repairs if r["kind"] == "rename"]
    assert len(renames) == 2 and not any(r["auto_applicable"] for r in renames)
    assert all("more than one" in r["ambiguity"] for r in renames)


def test_drift_rename_requires_value_pattern_match():
    cols = clean_rows(50)
    cols.pop("customer_id")
    cols["cust_id"] = ["unknown"] * 50
    _, findings, repairs = analyse(cols)
    assert not [f for f in findings if f["kind"] == "rename_candidate"]
    assert not repairs


def test_drift_date_format_unique_parse_is_safe():
    dates = [f"{d:02d}/02/2026" for d in range(1, 29)] + ["2026-02-30"]
    cols = clean_rows(29, order_date=dates)
    _, findings, repairs = analyse(cols)
    assert "date_format_change" in {f["kind"] for f in findings}
    (r,) = [r for r in repairs if r["kind"] == "date_format"]
    assert r["auto_applicable"] and r["detail"]["format"] == "%d/%m/%Y"
    assert r["evidence"]["malformed_values"] == 1
    assert r["evidence"]["candidates"]["%m/%d/%Y"]["coverage"] < 0.5


def test_drift_date_format_undecidable_is_not_applied():
    dates = [f"{d:02d}/{m:02d}/2026" for d, m in zip(range(1, 13), [2] * 12, strict=True)]
    _, _, repairs = analyse(clean_rows(12, order_date=dates))
    (r,) = [r for r in repairs if r["kind"] == "date_format"]
    assert not r["auto_applicable"] and "cannot be told apart" in r["ambiguity"]


def test_drift_unit_scale_thousandfold_is_safe():
    base = clean_rows(200)
    baseline_frame = frame(base)
    baseline = {"batch": "2026-01", "profile": profile_frame(baseline_frame, CONTRACT),
                "raw_profile": profile_frame(baseline_frame, CONTRACT)}
    grams = clean_rows(200, weight_kg=[str(int(float(w) * 1000)) for w in base["weight_kg"]])
    _, findings, repairs = analyse(grams, baseline=baseline)
    (shift,) = [f for f in findings if f["kind"] == "scale_shift"]
    assert shift["evidence"]["matches_unit"] == "g" and shift["evidence"]["ratios"]["median"] == 1000.0
    (r,) = [r for r in repairs if r["kind"] == "unit_scale"]
    assert r["auto_applicable"] and r["detail"]["factor"] == "0.001"

    tripled = clean_rows(200, weight_kg=[f"{float(w) * 3:.3f}" for w in base["weight_kg"]])
    _, findings, repairs = analyse(tripled, baseline=baseline)
    assert [f["evidence"]["matches_unit"] for f in findings if f["kind"] == "scale_shift"] == [None]
    assert not [r for r in repairs if r["kind"] == "unit_scale"]


@pytest.mark.parametrize("label,rule,target", [
    ("electronics", "case", "Electronics"), ("HOME", "case", "Home"), ("Toy", "plural", "Toys"),
    ("Electronic", "plural", "Electronics"), ("Apparrel", "typo", "Apparel"), ("Grocey", "typo", "Grocery"),
    ("Elec", "abbreviation", None), ("Home & Garden", "composite", None), ("Gift Cards", "unmapped", None),
])
def test_category_rules(label, rule, target):
    c = classify_label(label, CATEGORIES)
    assert (c["rule"], c["target"]) == (rule, target)
    assert (c["ambiguity"] is None) == (target is not None)


def test_category_typo_needs_a_unique_target():
    c = classify_label("Bat", ["Cat", "Hat", "Mat"])
    assert c["rule"] == "typo_multiple" and c["target"] is None


def test_intake_bounds(tmp_path):
    big = tmp_path / "orders_big.csv"
    big.write_text("order_id\n" + "\n".join(f"ORD-{i:06d}" for i in range(MAX_ROWS + 1)) + "\n")
    with pytest.raises(IntakeError, match="row bound"):
        read_batch(big)
    ragged = tmp_path / "orders_ragged.csv"
    ragged.write_text("a,b\n1,2\n3\n")
    with pytest.raises(IntakeError, match="fields"):
        read_batch(ragged)
    nested = tmp_path / "orders_nested.json"
    nested.write_text('[{"a": {"b": 1}}]')
    with pytest.raises(IntakeError, match="scalars"):
        read_batch(nested)


def test_intake_keeps_raw_text():
    f, _ = read_batch(FIXTURES / FILES["2026-03"])
    assert f.at[1, "weight_kg"] == "12463" and f.at[1, "amount"] == "39.94"
    f, _ = read_batch(FIXTURES / FILES["2026-04"])
    blanks = [v for v in f["order_id"] if v is None or not v.strip()]
    assert len(blanks) == 8 and "   " in blanks
