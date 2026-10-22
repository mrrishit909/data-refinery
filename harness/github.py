"""Minimal GitHub REST client for Issues, Actions runs and artifacts (stdlib only).

Uses GITHUB_TOKEN from the environment. Never logs the token or request headers.
Retries 5xx and rate-limit responses with bounded exponential backoff.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .core import HarnessError, Repo

STATUS_LABELS = ["job:requested", "job:running", "job:completed", "job:failed", "job:blocked", "job:cancelled"]

LABELS: dict[str, tuple[str, str]] = {
    "job:requested": ("C9A227", "Run request accepted, waiting to start"),
    "job:running": ("3F6FB5", "Run in progress"),
    "job:completed": ("4C7A3A", "Run completed and published"),
    "job:failed": ("A6422A", "Run failed; evidence retained"),
    "job:blocked": ("5A171B", "Not runnable: invalid, unauthorized, or over a ceiling"),
    "job:cancelled": ("6B6359", "Run cancelled"),
    "job:approved": ("646548", "Owner approval to run a request from another account"),
    "type:run": ("E8D7BF", "Run request"),
    "type:repair": ("A6422A", "Repair assigned by the Outcome Verification Loop"),
    "type:memory": ("646548", "Memory Curation Workflow proposal"),
    "type:release": ("29201B", "Release record"),
    "memory:accept": ("4C7A3A", "Owner accepts the memory proposals in this issue"),
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


class GitHub:
    def __init__(self, repo: Repo | None = None, token: str | None = None) -> None:
        self.repo = repo or Repo.current()
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
        if not self.token:
            raise HarnessError("GITHUB_TOKEN is not available to this step")

    def request(self, method: str, path: str, body: Any = None, *, raw: bool = False, attempts: int = 5) -> Any:
        url = path if path.startswith("http") else f"{self.api}{path}"
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(attempts):
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Authorization", f"Bearer {self.token}")
            req.add_header("Accept", "application/vnd.github+json")
            req.add_header("X-GitHub-Api-Version", "2022-11-28")
            if data is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed https API host)
                    payload = resp.read()
                    if raw:
                        return payload
                    return json.loads(payload) if payload else None
            except urllib.error.HTTPError as exc:
                retryable = exc.code >= 500 or exc.code == 429 or (
                    exc.code == 403 and exc.headers.get("x-ratelimit-remaining") == "0"
                ) or (exc.code == 403 and "secondary rate limit" in exc.read().decode(errors="ignore").lower())
                if not retryable or attempt == attempts - 1:
                    raise HarnessError(f"GitHub API {method} {path.split('?')[0]} failed: HTTP {exc.code}") from None
                wait = float(exc.headers.get("retry-after") or min(60, 2**attempt + random.random()))
                time.sleep(wait)
            except urllib.error.URLError:
                if attempt == attempts - 1:
                    raise HarnessError(f"GitHub API {method} {path.split('?')[0]} unreachable") from None
                time.sleep(min(30, 2**attempt + random.random()))
        raise HarnessError("unreachable")

    # Issues -------------------------------------------------------------------------------
    def ensure_labels(self) -> list[str]:
        existing = {lab["name"] for lab in self.request("GET", f"/repos/{self.repo.slug}/labels?per_page=100")}
        created = []
        for name, (color, description) in LABELS.items():
            if name not in existing:
                self.request("POST", f"/repos/{self.repo.slug}/labels",
                             {"name": name, "color": color, "description": description})
                created.append(name)
        return created

    def comment(self, issue: int, text: str) -> None:
        self.request("POST", f"/repos/{self.repo.slug}/issues/{issue}/comments", {"body": text[:60000]})

    def issue(self, number: int) -> dict[str, Any]:
        return self.request("GET", f"/repos/{self.repo.slug}/issues/{number}")

    def set_status(self, issue: int, status: str) -> None:
        """Replace job:* status labels with job:<status> (labels are views of state, not the state)."""
        current = {lab["name"] for lab in self.issue(issue)["labels"]}
        for label in STATUS_LABELS:
            if label in current and label != f"job:{status}":
                self.request("DELETE", f"/repos/{self.repo.slug}/issues/{issue}/labels/{label}")
        self.request("POST", f"/repos/{self.repo.slug}/issues/{issue}/labels", {"labels": [f"job:{status}"]})

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        out = self.request("POST", f"/repos/{self.repo.slug}/issues",
                           {"title": title[:250], "body": body[:60000], "labels": labels})
        return int(out["number"])

    def update_issue(self, number: int, **fields: Any) -> None:
        self.request("PATCH", f"/repos/{self.repo.slug}/issues/{number}", fields)

    def open_issues(self, label: str) -> list[dict[str, Any]]:
        items = self.request("GET", f"/repos/{self.repo.slug}/issues?state=open&labels={label}&per_page=100")
        return [i for i in items if "pull_request" not in i]

    # Actions ------------------------------------------------------------------------------
    def workflow_run(self, run_id: int) -> dict[str, Any]:
        return self.request("GET", f"/repos/{self.repo.slug}/actions/runs/{run_id}")

    def run_artifacts(self, run_id: int) -> list[dict[str, Any]]:
        return self.request("GET", f"/repos/{self.repo.slug}/actions/runs/{run_id}/artifacts")["artifacts"]

    def download_artifact(self, artifact_id: int) -> bytes:
        """The API answers with a redirect to a short-lived storage URL. urllib would forward the Authorization
        header there (storage rejects it, and the token must not leave the API host), so follow it by hand."""
        req = urllib.request.Request(f"{self.api}/repos/{self.repo.slug}/actions/artifacts/{artifact_id}/zip")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        try:
            with urllib.request.build_opener(_NoRedirect).open(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise HarnessError(f"GitHub API GET artifact {artifact_id} failed: HTTP {exc.code}") from None
            location = exc.headers.get("Location", "")
        if urllib.parse.urlparse(location).scheme != "https":
            raise HarnessError(f"artifact {artifact_id} download did not redirect to an https URL")
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(location), timeout=120) as resp:  # noqa: S310
                    return resp.read()
            except (urllib.error.HTTPError, urllib.error.URLError) as exc:
                if attempt == 2:
                    reason = getattr(exc, "code", exc)
                    raise HarnessError(f"artifact {artifact_id} storage download failed: {reason}") from None
                time.sleep(1 + attempt)
        raise HarnessError("unreachable")

    def dispatch(self, workflow: str, ref: str, inputs: dict[str, str]) -> None:
        self.request("POST", f"/repos/{self.repo.slug}/actions/workflows/{workflow}/dispatches",
                     {"ref": ref, "inputs": inputs})
