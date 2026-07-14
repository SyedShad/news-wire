from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".githooks" / "pre-push"
ZERO = "0" * 40
LOCAL = "1" * 40
REMOTE = "2" * 40


def _run_hook(tmp_path: Path, line: str, *, ancestor: bool) -> subprocess.CompletedProcess[str]:
    fake_git = tmp_path / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "[ \"$1\" = merge-base ] || exit 2\n"
        f"exit {0 if ancestor else 1}\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o700)
    environment = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    return subprocess.run(
        [str(HOOK)],
        input=line,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )


def test_guard_blocks_main_deletion_and_force_push(tmp_path: Path) -> None:
    deletion = _run_hook(
        tmp_path,
        f"(delete) {ZERO} refs/heads/main {REMOTE}\n",
        ancestor=True,
    )
    force = _run_hook(
        tmp_path,
        f"refs/heads/main {LOCAL} refs/heads/main {REMOTE}\n",
        ancestor=False,
    )

    assert deletion.returncode == 1
    assert "Blocked deletion" in deletion.stderr
    assert force.returncode == 1
    assert "Blocked non-fast-forward" in force.stderr


def test_guard_allows_fast_forward_main_and_other_branches(tmp_path: Path) -> None:
    fast_forward = _run_hook(
        tmp_path,
        f"refs/heads/main {LOCAL} refs/heads/main {REMOTE}\n",
        ancestor=True,
    )
    feature = _run_hook(
        tmp_path,
        f"refs/heads/feature {LOCAL} refs/heads/feature {REMOTE}\n",
        ancestor=False,
    )

    assert fast_forward.returncode == 0
    assert feature.returncode == 0
