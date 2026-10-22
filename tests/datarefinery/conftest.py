"""Session fixtures: one full apply-safe run and one full propose run, plus an independent raw-file reader.

The raw reader deliberately does not use datarefinery.intake, so tests compare outputs against the files on disk
through a second code path.
"""

import csv
import hashlib
import io
import json
from pathlib import Path

import openpyxl
import pytest

from datarefinery.pipeline import run

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"
CONTRACTS = REPO / "contracts"
BATCHES = ["2026-01", "2026-02", "2026-03", "2026-04"]
FILES = {"2026-01": "orders_2026-01.csv", "2026-02": "orders_2026-02.csv", "2026-03": "orders_2026-03.json",
         "2026-04": "orders_2026-04.xlsx"}
MODES = ["apply-safe", "propose"]


def request(mode, batches=BATCHES):
    return {"batches": list(batches), "contract": "orders.v1", "repair_mode": mode, "fx_table": "fixed-2026q1"}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_raw(path):
    """{1-based record number: {column: raw value}} straight from the file."""
    path = Path(path)
    if path.suffix == ".csv":
        rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))
        header, body = rows[0], rows[1:]
    elif path.suffix == ".json":
        records = json.loads(path.read_text(encoding="utf-8"), parse_float=str, parse_int=str)
        header = list(records[0])
        body = [[r.get(k) for k in header] for r in records]
    else:
        wb = openpyxl.load_workbook(path, read_only=True)
        rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
        wb.close()
        header, body = rows[0], rows[1:]
    return {n: dict(zip(header, row)) for n, row in enumerate(body, start=1)}


def load_csv(path):
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def fixture_hashes():
    """Taken before any pipeline run in the session."""
    return {p.name: sha256(p) for p in sorted(FIXTURES.iterdir()) if p.is_file()}


@pytest.fixture(scope="session")
def runs(tmp_path_factory, fixture_hashes):
    out = {}
    for mode in MODES:
        target = tmp_path_factory.mktemp(f"run-{mode}") / "out"
        run(request(mode), target)
        out[mode] = target
    return out


@pytest.fixture(scope="session")
def raw_batches():
    return {b: read_raw(FIXTURES / f) for b, f in FILES.items()}


@pytest.fixture(scope="session")
def ground_truth():
    return load_json(FIXTURES / "ground_truth.json")


def routed(outdir):
    """Per batch: cleaned rows, quarantine rows and merged duplicates, keyed by source row."""
    out = {b: {"cleaned": {}, "quarantine": {}, "merged": {}} for b in BATCHES}
    for row in load_csv(outdir / "cleaned.csv"):
        out[row["_batch"]]["cleaned"][int(row["_source_row"])] = row
    for row in load_csv(outdir / "quarantine.csv"):
        out[row["_batch"]]["quarantine"][int(row["_source_row"])] = row
    for b, rec in load_json(outdir / "reconciliation.json")["batches"].items():
        out[b]["merged"] = {m["_source_row"]: m["duplicate_of"] for m in rec["merged_duplicates"]}
    return out
