"""Dependency-free credential scan of the repository tree and (with --live) the shipped site assets.

Exit 1 on any finding. Prints file and pattern name only, never the matched text.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = {
    "anthropic-key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "openai-key": re.compile(r"sk-(proj-)?[A-Za-z0-9]{32,}"),
    "github-token": re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "aws-access-key": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "private-key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    "slack-token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    "generic-assignment": re.compile(r"(?i)\b(api[_-]?key|secret|password)\s*[:=]\s*['\"][A-Za-z0-9/+_=-]{16,}['\"]"),
}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".gz", ".zip", ".lock"}


def scan_text(name: str, text: str) -> list[str]:
    return [f"{name}: {pid}" for pid, pat in PATTERNS.items() if pat.search(text)]


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.splitlines() if line]


def live_assets(base: str) -> dict[str, str]:
    def get(url: str) -> str:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (configured Pages URL)
            return resp.read().decode("utf-8", errors="ignore")

    html = get(base)
    assets = {base: html}
    for ref in re.findall(r"(?:src|href)=\"([^\"]+\.(?:js|css))\"", html):
        url = urljoin(base, ref)
        assets[url] = get(url)
    return assets


def main() -> int:
    findings: list[str] = []
    for path in tracked_files():
        if path.suffix in SKIP_SUFFIXES or not path.is_file() or path.name == "secret_scan.py":
            continue
        findings += scan_text(str(path.relative_to(ROOT)), path.read_text(errors="ignore"))
    if "--live" in sys.argv:
        base = json.loads((ROOT / "project.json").read_text())["pages_url"]
        for url, text in live_assets(base).items():
            findings += scan_text(url, text)
    for finding in findings:
        print("FINDING", finding)
    print(f"secret scan: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
