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
    assert installer.launcher.resolve() == release.path / "launcher"
    assert "-m open_source_ai_news_wire" in (release.path / "launcher").read_text(encoding="utf-8")
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


def test_installer_labels_release_from_incoming_source_not_running_package(
    tmp_path: Path,
) -> None:
    source = tmp_path / "incoming"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname = "incoming-wire"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    (source / "uv.lock").write_text("", encoding="utf-8")
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    database.initialize()
    installer = LocalInstaller(
        source,
        database.paths,
        application_root=tmp_path / "app",
        binary_root=tmp_path / "bin",
    )

    assert installer._source_version() == "9.8.7"


def test_installer_rebuilds_incomplete_content_addressed_release(tmp_path: Path) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    database.initialize()

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
            (python.parent / "open-source-ai-news-wire").write_text("entry", encoding="utf-8")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    installer = LocalInstaller(
        source, database.paths, application_root=tmp_path / "app",
        binary_root=tmp_path / "bin", runner=fake_runner,
    )
    incomplete = installer.releases / f"0.2.0-{installer._source_digest()}"
    incomplete.mkdir(parents=True)
    (incomplete / "partial").write_text("stale", encoding="utf-8")

    release = installer.install()

    assert (release.path / "launcher").exists()
    assert (release.path / "partial").exists() is False
