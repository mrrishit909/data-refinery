"""Deterministic synthetic order batches with injected defects and a ground-truth ledger.

Only integer draws and fixed-probability choices are used (no transcendental functions), so the same seed yields
the same values on every platform. Row numbers in ground_truth.json are 1-based data records, header excluded.
"""

from __future__ import annotations

import calendar
import csv
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import openpyxl

SEED = 20260101
COLUMNS = ["order_id", "customer_id", "order_date", "amount", "currency", "category", "quantity", "weight_kg", "region"]
CATEGORIES = ["Electronics", "Home", "Grocery", "Apparel", "Toys"]
CATEGORY_P = [0.25, 0.2, 0.25, 0.18, 0.12]
REGIONS = ["North", "South", "East", "West"]
XLSX_STAMP = datetime(2026, 4, 30)
ZIP_STAMP = (2026, 4, 30, 0, 0, 0)

# label -> (contract category the row really belongs to, or None when the label's meaning is undetermined)
CATEGORY_VARIANTS = {
    "electronics": ("Electronics", 8), "Electronic": ("Electronics", 6), "Toy": ("Toys", 6), "HOME": ("Home", 5),
    "Apparrel": ("Apparel", 4), "Grocey": ("Grocery", 4),
    "Elec": (None, 7), "Home & Garden": (None, 6), "Gift Cards": (None, 4),
}
MALFORMED_DATES = ["2026-02-30", "not a date", "2026-02-31", "31/02/2026", "TBD"]


def _kg(grams: int | None) -> str | None:
    return None if grams is None else f"{grams // 1000}.{grams % 1000:03d}"


def _money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _base_rows(rng: np.random.Generator, batch_no: int, month: int, n: int, currency_p: dict[str, float]) -> list[dict]:
    days = calendar.monthrange(2026, month)[1]
    cust = rng.integers(10000, 100000, size=n)
    day = rng.integers(1, days + 1, size=n)
    big = rng.random(size=n) < 0.1
    cents = np.where(big, rng.integers(25000, 150000, size=n), rng.integers(500, 25000, size=n))
    currency = rng.choice(list(currency_p), size=n, p=list(currency_p.values()))
    category = rng.choice(CATEGORIES, size=n, p=CATEGORY_P)
    qty = rng.integers(1, 6, size=n)
    heavy = rng.random(size=n) < 0.25
    grams = np.where(heavy, rng.integers(3000, 25001, size=n), rng.integers(100, 3000, size=n))
    no_weight = rng.random(size=n) < 0.04
    region = rng.choice(REGIONS, size=n)
    return [{
        "order_id": f"ORD-{batch_no}{i + 1:05d}",
        "customer_id": f"CUS-{int(cust[i])}",
        "order_date": f"2026-{month:02d}-{int(day[i]):02d}",
        "amount": _money(int(cents[i])),
        "currency": str(currency[i]),
        "category": str(category[i]),
        "quantity": str(int(qty[i])),
        "grams": None if no_weight[i] else int(grams[i]),
        "region": str(region[i]),
    } for i in range(n)]


def _with_kg(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k != "grams"}
    out["weight_kg"] = _kg(row["grams"])
    return {c: out[c] for c in COLUMNS}


def _csv_bytes(header: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if row[c] is None else row[c] for c in header])
    return buf.getvalue().encode()


def _json_bytes(rows: list[dict]) -> bytes:
    numeric = {"amount", "quantity", "weight_kg"}

    def literal(key: str, value: str | None) -> str:
        if value is None:
            return "null"
        return value if key in numeric else json.dumps(value, ensure_ascii=False)

    lines = ["{" + ", ".join(f"{json.dumps(k)}: {literal(k, v)}" for k, v in row.items()) + "}" for row in rows]
    return ("[\n" + ",\n".join(lines) + "\n]\n").encode()


def _xlsx_bytes(rows: list[dict]) -> bytes:
    """All cells are written as text. Zip timestamps and core properties are pinned so content is stable."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "orders"
    ws.append(COLUMNS)
    for row in rows:
        ws.append([row[c] for c in COLUMNS])
    wb.properties.creator = "datarefinery.fixtures"
    wb.properties.created = XLSX_STAMP
    raw = io.BytesIO()
    wb.save(raw)
    stamp = XLSX_STAMP.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw.getvalue())) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                data = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>" + stamp, data)
            member = zipfile.ZipInfo(info.filename, date_time=ZIP_STAMP)
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o644 << 16
            dst.writestr(member, data)
    return out.getvalue()


def _defect(row: int, kind: str, column: str | None, raw, expected: dict, expected_propose: dict | None = None) -> dict:
    return {"row": row, "kind": kind, "column": column, "raw": raw, "expected": expected,
            "expected_propose": expected_propose or expected}


def _quarantined(reason: str) -> dict:
    return {"outcome": "quarantined", "reason": reason}


def batch_01(rng: np.random.Generator) -> tuple[bytes, dict]:
    rows = [_with_kg(r) for r in _base_rows(rng, 1, 1, 1000, {"USD": 1.0})]
    return _csv_bytes(COLUMNS, rows), {"rows": len(rows), "column_defects": [], "row_defects": []}


def batch_02(rng: np.random.Generator) -> tuple[bytes, dict]:
    base = [_with_kg(r) for r in _base_rows(rng, 2, 2, 1180, {"USD": 1.0})]
    order = [int(i) for i in rng.permutation(len(base))]
    ddmm, malformed, negative = order[:177], order[177:182], order[182:185]
    dup_src, conflict_src = order[185:205], order[205:211]
    notes: dict[int, tuple] = {}
    for i in ddmm:
        iso = base[i]["order_date"]
        y, m, d = iso.split("-")
        base[i]["order_date"] = f"{d}/{m}/{y}"
        notes[i] = ("date_ddmmyyyy", "order_date", base[i]["order_date"],
                    {"outcome": "accepted", "cleaned": {"order_date": iso}}, _quarantined("BAD_DATE"))
    for i, value in zip(malformed, MALFORMED_DATES, strict=True):
        base[i]["order_date"] = value
        notes[i] = ("malformed_date", "order_date", value, _quarantined("BAD_DATE"), None)
    for i in negative:
        base[i]["amount"] = "-" + base[i]["amount"]
        notes[i] = ("negative_amount", "amount", base[i]["amount"], _quarantined("NEGATIVE_AMOUNT"), None)

    # Copies are placed after their original at a seeded offset; (key, kind) keeps the ordering total.
    entries = [(i, 0, k, row) for k, (i, row) in enumerate(enumerate(base))]
    copies = []
    for i in dup_src:
        copies.append((i + int(rng.integers(1, 150)), 1, ("exact", i), dict(base[i])))
    for i in conflict_src:
        changed = dict(base[i])
        cents = int(changed["amount"].replace(".", "")) + int(rng.integers(100, 5000))
        changed["amount"] = _money(cents)
        changed["quantity"] = str(int(changed["quantity"]) % 5 + 1)
        copies.append((i + int(rng.integers(1, 150)), 1, ("conflict", i), changed))
    ordered = sorted(entries + copies, key=lambda e: (e[0], e[1], str(e[2])))
    row_of_base = {e[2]: n for n, e in enumerate(ordered, start=1) if e[1] == 0}

    defects = []
    for n, (_, kind, tag, _row) in enumerate(ordered, start=1):
        if kind == 0 and tag in notes:
            k, col, raw, exp, exp_p = notes[tag]
            defects.append(_defect(n, k, col, raw, exp, exp_p))
        elif kind == 1 and tag[0] == "exact":
            orig = row_of_base[tag[1]]
            defects.append(_defect(n, "exact_duplicate", None, None, {"outcome": "merged", "duplicate_of": orig}))
        elif kind == 1:
            orig = row_of_base[tag[1]]
            defects.append(_defect(n, "duplicate_conflict", "order_id", base[tag[1]]["order_id"],
                                   _quarantined("DUPLICATE_CONFLICT")) | {"conflicts_with": orig})
            defects.append(_defect(orig, "duplicate_conflict", "order_id", base[tag[1]]["order_id"],
                                   _quarantined("DUPLICATE_CONFLICT")) | {"conflicts_with": n})
    rows = [e[3] for e in ordered]
    for row in rows:
        row["cust_id"] = row.pop("customer_id")
    header = ["order_id", "cust_id", *COLUMNS[2:]]
    truth = {
        "rows": len(rows),
        "column_defects": [{"kind": "column_renamed", "column": "customer_id", "source_column": "cust_id",
                            "expected": {"repair": "rename", "applied_in": "apply-safe"},
                            "expected_propose": {"outcome": "quarantined", "reason": "UNKNOWN_COLUMN_SET"}}],
        "row_defects": sorted(defects, key=lambda d: d["row"]),
    }
    return _csv_bytes(header, rows), truth


def batch_03(rng: np.random.Generator) -> tuple[bytes, dict]:
    base = _base_rows(rng, 3, 3, 1100, {"USD": 0.5, "EUR": 0.3, "GBP": 0.2})
    order = [int(i) for i in rng.permutation(len(base))]
    defects = []
    pos = 0
    for label, (target, count) in CATEGORY_VARIANTS.items():
        for i in order[pos:pos + count]:
            base[i]["category"] = label
            if target:
                exp = {"outcome": "accepted", "cleaned": {"category": target}}
                defects.append(_defect(i + 1, "category_variant", "category", label, exp, _quarantined("INVALID_CATEGORY")))
            elif label == "Gift Cards":
                defects.append(_defect(i + 1, "category_unknown", "category", label, _quarantined("INVALID_CATEGORY")))
            else:
                defects.append(_defect(i + 1, "category_ambiguous", "category", label,
                                       _quarantined("AMBIGUOUS_CATEGORY")))
        pos += count
    for i in order[pos:pos + 12]:
        base[i]["quantity"] = None
        defects.append(_defect(i + 1, "null_quantity", "quantity", None, _quarantined("NULL_REQUIRED")))
    rows = []
    for row in base:
        out = {k: v for k, v in row.items() if k != "grams"}
        out["weight_kg"] = None if row["grams"] is None else str(row["grams"])
        rows.append({c: out[c] for c in COLUMNS})
    truth = {
        "rows": len(rows),
        "column_defects": [
            {"kind": "unit_grams", "column": "weight_kg", "factor": "0.001",
             "expected": {"repair": "unit_scale", "applied_in": "apply-safe"},
             "expected_propose": {"outcome": "quarantined", "reason": "UNRESOLVED_UNIT_SCALE"}},
            {"kind": "currency_mix", "column": "currency", "currencies": ["EUR", "GBP", "USD"],
             "expected": {"normalization": "amount_usd via fixed-2026q1"}},
        ],
        "row_defects": sorted(defects, key=lambda d: d["row"]),
    }
    return _json_bytes(rows), truth


def batch_04(rng: np.random.Generator) -> tuple[bytes, dict]:
    base = [_with_kg(r) for r in _base_rows(rng, 4, 4, 1040, {"USD": 1.0})]
    order = [int(i) for i in rng.permutation(len(base))]
    defects = []
    jan_days = rng.integers(1, 32, size=40)
    for i, day in zip(order[:40], jan_days, strict=True):
        base[i]["order_date"] = f"2026-01-{int(day):02d}"
        defects.append(_defect(i + 1, "late_arrival", "order_date", base[i]["order_date"],
                               {"outcome": "accepted", "restates_period": "2026-01",
                                "condition": "2026-01 processed earlier in the same run"}))
    for k, i in enumerate(order[40:48]):
        base[i]["order_id"] = "   " if k < 2 else None
        defects.append(_defect(i + 1, "missing_order_id", "order_id", base[i]["order_id"], _quarantined("MISSING_ID")))
    truth = {"rows": len(base), "column_defects": [], "row_defects": sorted(defects, key=lambda d: d["row"])}
    return _xlsx_bytes(base), truth


BATCHES = {
    "2026-01": ("orders_2026-01.csv", "csv", batch_01),
    "2026-02": ("orders_2026-02.csv", "csv", batch_02),
    "2026-03": ("orders_2026-03.json", "json", batch_03),
    "2026-04": ("orders_2026-04.xlsx", "xlsx", batch_04),
}


def build() -> dict[str, bytes]:
    """All fixture files as bytes, keyed by file name. One generator, drawn in batch order."""
    rng = np.random.default_rng(SEED)
    files: dict[str, bytes] = {}
    truth = {"generator": "datarefinery.fixtures", "seed": SEED, "synthetic": True,
             "row_numbering": "1-based data record index, header excluded", "batches": {}}
    for batch, (name, fmt, make) in BATCHES.items():
        data, info = make(rng)
        files[name] = data
        truth["batches"][batch] = {"file": name, "format": fmt, **info}
    files["ground_truth.json"] = (json.dumps(truth, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    return files


def generate(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in build().items():
        (out / name).write_bytes(data)
        written.append(out / name)
    return written
