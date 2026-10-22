import json
import subprocess
import types
from pathlib import Path

import pytest
from harness.state import StateRepo


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    """A bare 'GitHub' remote with an initialised `state` branch."""
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "state")
    _git(seed, "config", "user.email", "t@example.invalid")
    _git(seed, "config", "user.name", "t")
    (seed / "index.json").write_text(json.dumps({"schema_version": "1.0", "runs": []}))
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "init state")
    _git(seed, "push", "-q", str(bare), "state")
    return bare


def clone_state(remote: Path, dest: Path) -> StateRepo:
    _git(dest.parent, "clone", "-q", "--branch", "state", str(remote), str(dest))
    _git(dest, "config", "user.email", "bot@example.invalid")
    _git(dest, "config", "user.name", "bot")
    return StateRepo(dest, branch="state")


@pytest.fixture
def state(remote: Path, tmp_path: Path) -> StateRepo:
    return clone_state(remote, tmp_path / "work")


@pytest.fixture
def fake_hooks(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    ns = types.SimpleNamespace(
        PUBLIC_FILES=["manifest.json", "results.json"],
        validate_outputs=lambda d: [] if (d / "results.json").exists() else ["results.json missing"],
        headline=lambda d: "test headline",
        on_publish=lambda root, rec, d: [],
        memory_candidates=lambda root: [],
    )
    import harness.memory
    import harness.runs
    import harness.snapshot

    for mod in (harness.runs, harness.memory, harness.snapshot):
        monkeypatch.setattr(mod, "hooks", lambda: ns)
    return ns


def make_outputs(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(json.dumps({"input_manifest_hash": "sha256:" + "a" * 64}))
    (path / "results.json").write_text(json.dumps({"ok": True}))
    return path
