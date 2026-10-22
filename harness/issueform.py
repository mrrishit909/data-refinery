"""Parse GitHub issue-form bodies into validated request parameters.

Issue text is untrusted. It is parsed as data, coerced through strict per-field rules from
project.json, and never echoed back verbatim or passed to a shell.
"""

from __future__ import annotations

import re
from typing import Any

MAX_BODY = 20_000
_HEADING = re.compile(r"^### +(.+?)\s*$", re.M)
_INT = re.compile(r"-?\d{1,10}")
_FLOAT = re.compile(r"-?\d{1,6}(\.\d{1,6})?")
_STR = re.compile(r"[A-Za-z0-9 _.-]{1,64}")
_CHECKED = re.compile(r"^- \[[xX]\] +(.+?)\s*$", re.M)


def sections(body: str) -> dict[str, str | None]:
    """Split a rendered issue form ("### Label" then the answer) into {label: answer}."""
    parts = _HEADING.split(body)
    out: dict[str, str | None] = {}
    for label, content in zip(parts[1::2], parts[2::2], strict=True):
        value = content.strip()
        out[label.strip()] = None if value in ("", "_No response_", "None") else value
    return out


def _set(params: dict[str, Any], dotted: str, value: Any) -> None:
    node = params
    keys = dotted.split(".")
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value


def _coerce(field: dict[str, Any], raw: str) -> Any:
    kind = field["type"]
    if kind == "int":
        if not _INT.fullmatch(raw):
            raise ValueError("expected a whole number")
        return int(raw)
    if kind == "float":
        if not _FLOAT.fullmatch(raw):
            raise ValueError("expected a number")
        return float(raw)
    if kind == "str":
        if not _STR.fullmatch(raw):
            raise ValueError("expected a short plain value")
        return raw
    if kind == "checkbox_list":
        choices: dict[str, str] = field["choices"]
        picked = []
        for label in _CHECKED.findall(raw):
            if label not in choices:
                raise ValueError("unknown option selected")
            picked.append(choices[label])
        return picked
    raise ValueError(f"unsupported field type {kind}")


def to_params(body: str, form: list[dict[str, Any]], constants: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return (params, errors). Error messages name fields, never quote the submitted text."""
    if len(body) > MAX_BODY:
        return {}, ["issue body is too long"]
    answers = sections(body)
    params: dict[str, Any] = {}
    errors: list[str] = []
    for field in form:
        raw = answers.get(field["label"])
        if raw is None:
            errors.append(f"{field['label']}: missing")
            continue
        try:
            _set(params, field["param"], _coerce(field, raw))
        except ValueError as exc:
            errors.append(f"{field['label']}: {exc}")
    for dotted, value in constants.items():
        _set(params, dotted, value)
    return params, errors
