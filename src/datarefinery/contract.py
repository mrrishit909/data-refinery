"""The orders contract: pydantic models, declared cell parses, FX conversion and the pandera schema."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Literal

import pandas as pd
import pandera.pandas as pa
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = ROOT / "contracts"
FIXTURES_DIR = ROOT / "fixtures"
SCHEMAS_DIR = ROOT / "schemas"

NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
INTEGER = re.compile(r"^-?\d+$")
ISO_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Column(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["string", "date", "decimal", "float", "integer", "enum"]
    required: bool
    unique: bool = False
    pattern: str | None = None
    format: str | None = None
    alternate_formats: list[str] = []
    scale: int | None = None
    min: Decimal | None = None
    exclusive_min: Decimal | None = None
    values: list[str] = []
    unit: str | None = None
    unit_conversions: dict[str, Decimal] = {}
    reference: dict[str, float] = {}
    note: str | None = None

    @property
    def numeric(self) -> bool:
        return self.type in ("decimal", "float", "integer")


class Derived(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["decimal"]
    scale: int
    inputs: list[str]
    fx_table: str
    rule: str


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: str
    entity: str
    description: str
    primary_key: str
    columns: list[Column]
    derived: list[Derived]

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.columns]

    def col(self, name: str) -> Column:
        return next(c for c in self.columns if c.name == name)


class FxTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fx_table: str
    synthetic: bool
    note: str
    base_currency: str
    rates: dict[str, Decimal]
    rounding: Literal["ROUND_HALF_UP", "ROUND_HALF_EVEN"]
    scale: int

    def to_base(self, amount: Decimal, currency: str) -> Decimal:
        return (amount * self.rates[currency]).quantize(Decimal(1).scaleb(-self.scale), rounding=self.rounding)


def contract_path(name: str) -> Path:
    return CONTRACTS_DIR / f"{name}.json"


def fx_path(name: str) -> Path:
    return CONTRACTS_DIR / f"fx_{name}.json"


@cache
def load_contract(name: str = "orders.v1") -> Contract:
    return Contract.model_validate_json(contract_path(name).read_bytes())


@cache
def load_fx(name: str = "fixed-2026q1") -> FxTable:
    return FxTable.model_validate_json(fx_path(name).read_bytes())


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_iso_date(value: str) -> bool:
    return bool(ISO_SHAPE.match(value)) and parse_date(value, "%Y-%m-%d") is not None


def parse_date(value: str, fmt: str) -> str | None:
    """Strict parse under one declared format; returns the canonical ISO string or None."""
    try:
        return datetime.strptime(value, fmt).date().isoformat()
    except ValueError:
        return None


def normalize_cell(col: Column, value: str | None, date_format: str | None = None, factor: Decimal | None = None,
                   category_map: dict[str, str] | None = None) -> tuple[str | None, str | None, bool]:
    """Apply the contract's declared parse to one trimmed cell.

    Returns (canonical value, parse error code, changed by a repair). Constraint checks (pattern, enum, range,
    nulls) are left to the pandera schema; this only decides whether the text can be read as the declared type.
    """
    if value is None:
        return None, None, False
    if col.type == "date":
        if is_iso_date(value):
            return value, None, False
        parsed = parse_date(value, date_format) if date_format else None
        return (parsed, None, True) if parsed else (None, "BAD_DATE", False)
    if col.type == "enum":
        if value not in col.values and category_map and value in category_map:
            return category_map[value], None, True
        return value, None, False
    if col.type == "integer":
        return (str(int(value)), None, False) if INTEGER.match(value) else (None, "BAD_NUMBER", False)
    if col.numeric:
        if not NUMBER.match(value):
            return None, "BAD_NUMBER", False
        number = Decimal(value) * factor if factor is not None else Decimal(value)
        quantum = Decimal(1).scaleb(-(col.scale or 0))
        fixed = number.quantize(quantum)
        if fixed != number:
            return None, "BAD_NUMBER", False
        return format(fixed, "f"), None, factor is not None
    return value, None, False


def typed_frame(contract: Contract, canonical: pd.DataFrame) -> pd.DataFrame:
    """Typed view of canonical string values, used only for validation."""
    out = {}
    for col in contract.columns:
        s = canonical[col.name]
        if col.type == "date":
            out[col.name] = pd.to_datetime(s, format="%Y-%m-%d")
        elif col.type == "integer":
            out[col.name] = pd.array([None if v is None else int(v) for v in s], dtype="Int64")
        elif col.numeric:
            out[col.name] = pd.array([None if v is None else float(v) for v in s], dtype="float64")
        else:
            out[col.name] = s.astype(object)
    return pd.DataFrame(out, index=canonical.index)


def pandera_schema(contract: Contract, enforce_unique: bool = False) -> pa.DataFrameSchema:
    """Checks carry their reason code as the pandera error label; nullability is mapped by column."""
    columns = {}
    for col in contract.columns:
        checks = []
        if col.pattern:
            checks.append(pa.Check.str_matches(col.pattern, error="BAD_PATTERN"))
        if col.type == "enum":
            code = "INVALID_CATEGORY" if col.name == "category" else "INVALID_ENUM"
            checks.append(pa.Check.isin(col.values, error=code))
        if col.min is not None:
            code = "NEGATIVE_AMOUNT" if col.name == "amount" else "OUT_OF_RANGE"
            checks.append(pa.Check.ge(float(col.min), error=code))
        if col.exclusive_min is not None:
            checks.append(pa.Check.gt(float(col.exclusive_min), error="OUT_OF_RANGE"))
        dtype = {"date": "datetime64[ns]", "integer": "Int64", "decimal": float, "float": float}.get(col.type, str)
        columns[col.name] = pa.Column(dtype, checks=checks, nullable=not col.required,
                                      unique=enforce_unique and col.unique)
    return pa.DataFrameSchema(columns, strict=True)


def contract_failures(contract: Contract, canonical: pd.DataFrame, enforce_unique: bool = False) -> list[tuple]:
    """Run the pandera schema lazily; return (row index, column, reason code) for every failing cell."""
    try:
        pandera_schema(contract, enforce_unique).validate(typed_frame(contract, canonical), lazy=True)
    except pa.errors.SchemaErrors as err:
        out = []
        for case in err.failure_cases.itertuples():
            check = str(case.check)
            if check == "not_nullable":
                code = "MISSING_ID" if case.column == contract.primary_key else "NULL_REQUIRED"
            elif check == "field_uniqueness":
                code = "DUPLICATE_CONFLICT"
            else:
                code = check
            idx = None if pd.isna(case.index) else int(case.index)
            out.append((idx, case.column, code))
        return out
    return []
