"""Shared primitives: config, canonical JSON, hashing, ids, schema validation, state machine."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(os.environ.get("HARNESS_ROOT", Path(__file__).resolve().parent.parent))

# Directive state machine. Terminal states never change once written.
TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "blocked"},
    "running": {"completed", "failed", "cancelled"},
}
TERMINAL = {"completed", "failed", "blocked", "cancelled"}


class HarnessError(Exception):
    """Base error for harness failures that should stop a workflow step."""


class IllegalTransition(HarnessError):
    pass


class ImmutableConflict(HarnessError):
    pass


@cache
def config() -> dict[str, Any]:
    return json.loads((ROOT / "project.json").read_text())


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def pretty(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_json(obj: Any) -> str:
    return sha256_bytes(dumps(obj).encode())


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def request_id(project: str, params: dict[str, Any]) -> str:
    """Idempotency key: identical parameters for the same project are the same request."""
    return hashlib.sha256(dumps({"project": project, "params": params}).encode()).hexdigest()[:16]


def run_id(project: str, created_at: datetime, req_id: str, attempt: int) -> str:
    return f"{project}-{created_at.strftime('%Y%m%dT%H%M%SZ')}-{req_id[:8]}-a{attempt}"


@cache
def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((ROOT / "schemas" / name).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def schema_errors(instance: Any, schema_name: str) -> list[str]:
    errors = sorted(_validator(schema_name).iter_errors(instance), key=lambda e: list(e.absolute_path))
    out = []
    for err in errors:
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        out.append(f"{where}: {err.message[:200]}")
    return out


def require_valid(instance: Any, schema_name: str) -> None:
    errors = schema_errors(instance, schema_name)
    if errors:
        raise HarnessError(f"{schema_name} validation failed: " + "; ".join(errors[:5]))


def transition(record: dict[str, Any], new_status: str, **fields: Any) -> dict[str, Any]:
    old = record["status"]
    if new_status not in TRANSITIONS.get(old, set()):
        raise IllegalTransition(f"{record['run_id']}: {old} -> {new_status} is not allowed")
    return {**record, "status": new_status, **fields}


@dataclass(frozen=True)
class Repo:
    """Identity of the GitHub repository the harness is operating on."""

    owner: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    @classmethod
    def current(cls) -> Repo:
        slug = os.environ.get("GITHUB_REPOSITORY") or f"{config()['owner']}/{config()['repo']}"
        owner, name = slug.split("/", 1)
        return cls(owner, name)

    def blob_url(self, branch: str, path: str) -> str:
        return f"https://github.com/{self.slug}/blob/{branch}/{path}"

    def run_url(self, run: int | None) -> str | None:
        return f"https://github.com/{self.slug}/actions/runs/{run}" if run else None
