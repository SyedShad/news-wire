from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.installer import InstallationError, LocalInstaller
from open_source_ai_news_wire.storage import Database


def test_local_installer_switches_atomically_and_preserves_runtime_data(tmp_path: Path) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    database.initialize()
    marker = database.paths.root / "keep-me"
    marker.write_text("local corpus", encoding="utf-8")

    def fake_runner(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if "build" in arguments:
            output = Path(arguments[arguments.index("--out-dir") + 1])
            (output / "wire.whl").write_bytes(b"wheel")
        elif "venv" in arguments:
            environment = Path(arguments[-1])
            (environment / "bin").mkdir(parents=True)
            (environment / "bin" / "python").write_text("python", encoding="utf-8")
        elif "pip" in arguments:
            python = Path(arguments[arguments.index("--python") + 1])
            executable = python.parent / "open-source-ai-news-wire"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o700)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    installer = LocalInstaller(
        source,
        database.paths,
        application_root=tmp_path / "app",
        binary_root=tmp_path / "bin",
        runner=fake_runner,
    )
    release = installer.install()

    assert installer.current.resolve() == release.path.resolve()
    assert installer.launcher.resolve() == release.path / "venv" / "bin" / "open-source-ai-news-wire"
    assert installer.list_releases()[0].active is True
    assert installer.rollback(release.release_id).active is True

    installer.uninstall_application()
    assert installer.launcher.exists() is False
    assert marker.read_text(encoding="utf-8") == "local corpus"


def test_rollback_rejects_schema_incompatible_release(tmp_path: Path) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    database.initialize()
    installer = LocalInstaller(
        source,
        database.paths,
        application_root=tmp_path / "app",
        binary_root=tmp_path / "bin",
    )
    release = installer.releases / "old"
    release.mkdir(parents=True)
    (release / "release.json").write_text(
        json.dumps({"release_id": "old", "version": "0.0.1", "schema_version": 1}),
        encoding="utf-8",
    )

    with pytest.raises(InstallationError, match="not compatible"):
        installer.rollback("old")
