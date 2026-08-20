from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path

import pytest

from open_source_ai_news_wire import installer as installer_module
from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.installer import InstallationError, LocalInstaller
from open_source_ai_news_wire.storage import SCHEMA_VERSION, Database


DUMMY_COMMIT_SHA = "c" * 40


def _schema_probe_result(
    arguments: list[str], schema_version: int = SCHEMA_VERSION
) -> subprocess.CompletedProcess[str] | None:
    if len(arguments) >= 4 and arguments[1:3] == ["-I", "-c"]:
        return subprocess.CompletedProcess(
            arguments, 0, json.dumps({"schema_version": schema_version}), ""
        )
    return None


def _dummy_provenance_runner(
    arguments: list[str], _cwd: Path
) -> subprocess.CompletedProcess[str]:
    if "rev-parse" in arguments:
        return subprocess.CompletedProcess(arguments, 0, DUMMY_COMMIT_SHA + "\n", "")
    if "status" in arguments:
        return subprocess.CompletedProcess(arguments, 0, " M controlled-test\n", "")
    return subprocess.CompletedProcess(arguments, 1, "", "not merged in controlled test")


def _validation_report(_source: Path) -> dict[str, object]:
    result = {
        "passed": True,
        "command": "controlled test validation",
        "completed_at": "2026-07-23T00:00:00Z",
    }
    return {
        "commit_sha": DUMMY_COMMIT_SHA,
        "full_tests": dict(result),
        "coverage_gate": dict(result),
        "dependency_audit": dict(result),
        "sast": dict(result),
    }


def _write_valid_release(installer: LocalInstaller) -> tuple[Path, dict[str, object]]:
    source_sha = "a" * 64
    release_id = f"0.3.6-{source_sha[:12]}"
    release = installer.releases / release_id
    (release / "dist").mkdir(parents=True, exist_ok=True)
    (release / "venv" / "bin").mkdir(parents=True, exist_ok=True)
    wheel = release / "dist" / "wire.whl"
    wheel.write_bytes(b"reviewed wheel")
    python = release / "venv" / "bin" / "python"
    python.write_text("python", encoding="utf-8")
    python.chmod(0o700)
    launcher = release / "launcher"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o700)
    manifest: dict[str, object] = {
        "manifest_version": 2,
        "release_id": release_id,
        "version": "0.3.6",
        "schema_version": SCHEMA_VERSION,
        "python": "3.12.13",
        "runtime_root": str(installer.runtime_paths.root),
        "source_sha256": source_sha,
        "wheel_filename": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "commit_sha": DUMMY_COMMIT_SHA,
        "validation": _validation_report(installer.source_root),
        "git_validation": {
            "clean_worktree": True,
            "commit_reachable_from_origin_main": True,
            "audited_override": False,
        },
    }
    (release / "release.json").write_text(json.dumps(manifest), encoding="utf-8")
    return release, manifest


def test_local_installer_switches_atomically_and_preserves_runtime_data(tmp_path: Path) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    database.initialize()
    marker = database.paths.root / "keep-me"
    marker.write_text("local corpus", encoding="utf-8")

    def fake_runner(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if schema_probe := _schema_probe_result(arguments):
            return schema_probe
        if "build" in arguments:
            output = Path(arguments[arguments.index("--out-dir") + 1])
            (output / "wire.whl").write_bytes(b"wheel")
        elif "venv" in arguments:
            environment = Path(arguments[-1])
            (environment / "bin").mkdir(parents=True)
            python = environment / "bin" / "python"
            python.write_text("python", encoding="utf-8")
            python.chmod(0o700)
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
        provenance_runner=_dummy_provenance_runner,
        allow_unverified_source=True,
        validation_report=_validation_report(source),
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
    source_sha = "a" * 64
    release_id = f"0.0.1-{source_sha[:12]}"
    release = installer.releases / release_id
    (release / "dist").mkdir(parents=True)
    (release / "venv" / "bin").mkdir(parents=True)
    wheel = release / "dist" / "old.whl"
    wheel.write_bytes(b"old wheel")
    python = release / "venv" / "bin" / "python"
    python.write_text("python", encoding="utf-8")
    python.chmod(0o700)
    launcher = release / "launcher"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o700)
    (release / "release.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "release_id": release_id,
                "version": "0.0.1",
                "schema_version": 1,
                "python": "3.12.13",
                "runtime_root": str(database.paths.root),
                "source_sha256": source_sha,
                "wheel_filename": wheel.name,
                "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                "commit_sha": "b" * 40,
                "git_validation": {
                    "clean_worktree": True,
                    "commit_reachable_from_origin_main": True,
                    "audited_override": False,
                },
                "validation": {
                    **_validation_report(source),
                    "commit_sha": "b" * 40,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(InstallationError, match="not compatible"):
        installer.rollback(release_id)


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


def test_older_installer_records_schema_from_incoming_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    database.initialize()
    incoming_schema_version = SCHEMA_VERSION
    invoking_schema_version = incoming_schema_version - 1
    schema_probes = 0

    def fake_runner(arguments: list[str], _cwd: Path) -> subprocess.CompletedProcess[str]:
        nonlocal schema_probes
        if schema_probe := _schema_probe_result(arguments, incoming_schema_version):
            schema_probes += 1
            return schema_probe
        if "build" in arguments:
            output = Path(arguments[arguments.index("--out-dir") + 1])
            (output / "wire.whl").write_bytes(b"newer reviewed wheel")
        elif "venv" in arguments:
            environment = Path(arguments[-1])
            (environment / "bin").mkdir(parents=True)
            python = environment / "bin" / "python"
            python.write_text("python", encoding="utf-8")
            python.chmod(0o700)
        elif "pip" in arguments:
            python = Path(arguments[arguments.index("--python") + 1])
            executable = python.parent / "open-source-ai-news-wire"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o700)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(installer_module, "SCHEMA_VERSION", invoking_schema_version)
    installer = LocalInstaller(
        source,
        database.paths,
        application_root=tmp_path / "app",
        binary_root=tmp_path / "bin",
        runner=fake_runner,
        provenance_runner=_dummy_provenance_runner,
        allow_unverified_source=True,
        validation_report=_validation_report(source),
    )

    release = installer.install()
    manifest = json.loads((release.path / "release.json").read_text(encoding="utf-8"))

    assert schema_probes == 2
    assert invoking_schema_version != incoming_schema_version
    assert manifest["schema_version"] == incoming_schema_version
    assert release.schema_version == incoming_schema_version


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    (
        (1, ""),
        (0, "not-json"),
        (0, json.dumps({"schema_version": True})),
        (0, json.dumps({"schema_version": SCHEMA_VERSION, "extra": "field"})),
    ),
)
def test_installed_schema_probe_fails_closed(
    tmp_path: Path, returncode: int, stdout: str
) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    python = tmp_path / "release-python"
    python.write_text("python", encoding="utf-8")
    python.chmod(0o700)
    installer = LocalInstaller(
        source,
        database.paths,
        runner=lambda arguments, _cwd: subprocess.CompletedProcess(
            arguments, returncode, stdout, "controlled probe failure"
        ),
    )

    with pytest.raises(InstallationError, match="schema probe (failed|was invalid)"):
        installer._installed_schema_version(python)


def test_installer_rebuilds_incomplete_content_addressed_release(tmp_path: Path) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    database.initialize()

    def fake_runner(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if schema_probe := _schema_probe_result(arguments):
            return schema_probe
        if "build" in arguments:
            output = Path(arguments[arguments.index("--out-dir") + 1])
            (output / "wire.whl").write_bytes(b"wheel")
        elif "venv" in arguments:
            environment = Path(arguments[-1])
            (environment / "bin").mkdir(parents=True)
            python = environment / "bin" / "python"
            python.write_text("python", encoding="utf-8")
            python.chmod(0o700)
        elif "pip" in arguments:
            python = Path(arguments[arguments.index("--python") + 1])
            (python.parent / "open-source-ai-news-wire").write_text("entry", encoding="utf-8")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    installer = LocalInstaller(
        source, database.paths, application_root=tmp_path / "app",
        binary_root=tmp_path / "bin", runner=fake_runner,
        provenance_runner=_dummy_provenance_runner,
        allow_unverified_source=True,
        validation_report=_validation_report(source),
    )
    incomplete = installer.releases / (
        f"{installer._source_version()}-{installer._source_digest()[:12]}"
    )
    incomplete.mkdir(parents=True)
    (incomplete / "partial").write_text("stale", encoding="utf-8")

    release = installer.install()

    assert (release.path / "launcher").exists()
    assert (release.path / "partial").exists() is False


def test_release_provenance_uses_full_hashes_and_rejects_tampered_wheel(
    tmp_path: Path,
) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    database.initialize()

    def fake_runner(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if schema_probe := _schema_probe_result(arguments):
            return schema_probe
        if "build" in arguments:
            output = Path(arguments[arguments.index("--out-dir") + 1])
            (output / "wire.whl").write_bytes(b"reviewed wheel bytes")
        elif "venv" in arguments:
            environment = Path(arguments[-1])
            (environment / "bin").mkdir(parents=True)
            python = environment / "bin" / "python"
            python.write_text("python", encoding="utf-8")
            python.chmod(0o700)
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
        provenance_runner=_dummy_provenance_runner,
        allow_unverified_source=True,
        validation_report=_validation_report(source),
    )
    release = installer.install()
    manifest = json.loads((release.path / "release.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 2
    assert len(manifest["source_sha256"]) == 64
    assert len(manifest["wheel_sha256"]) == 64
    assert len(manifest["commit_sha"]) == 40
    assert manifest["validation"]["full_tests"]["passed"] is True
    assert manifest["validation"]["coverage_gate"]["passed"] is True
    assert manifest["validation"]["dependency_audit"]["passed"] is True
    assert manifest["validation"]["sast"]["passed"] is True
    assert release.release_id.endswith(manifest["source_sha256"][:12])

    wheel = release.path / "dist" / manifest["wheel_filename"]
    wheel.write_bytes(b"tampered wheel bytes")
    with pytest.raises(InstallationError, match="SHA-256"):
        installer.list_releases()
    with pytest.raises(InstallationError, match="SHA-256"):
        installer.rollback(release.release_id)


def test_install_materializes_uv_python_symlink_inside_release(tmp_path: Path) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    database.initialize()
    uv_python = tmp_path / "uv-managed-python"
    uv_python.write_bytes(b"pinned python executable")
    uv_python.chmod(0o700)

    def fake_runner(arguments: list[str], _cwd: Path) -> subprocess.CompletedProcess[str]:
        if schema_probe := _schema_probe_result(arguments):
            return schema_probe
        if "build" in arguments:
            output = Path(arguments[arguments.index("--out-dir") + 1])
            (output / "wire.whl").write_bytes(b"reviewed wheel bytes")
        elif "venv" in arguments:
            environment = Path(arguments[-1])
            (environment / "bin").mkdir(parents=True)
            (environment / "bin" / "python").symlink_to(uv_python)
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
        provenance_runner=_dummy_provenance_runner,
        allow_unverified_source=True,
        validation_report=_validation_report(source),
    )

    release = installer.install()
    installed_python = release.path / "venv" / "bin" / "python"
    assert installed_python.is_symlink() is False
    assert installed_python.read_bytes() == uv_python.read_bytes()
    assert installed_python.stat().st_mode & 0o777 == 0o700


def test_install_removes_new_destination_when_artifact_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    database.initialize()

    def fake_runner(arguments: list[str], _cwd: Path) -> subprocess.CompletedProcess[str]:
        if schema_probe := _schema_probe_result(arguments):
            return schema_probe
        if "build" in arguments:
            output = Path(arguments[arguments.index("--out-dir") + 1])
            (output / "wire.whl").write_bytes(b"reviewed wheel bytes")
        elif "venv" in arguments:
            environment = Path(arguments[-1])
            (environment / "bin").mkdir(parents=True)
            python = environment / "bin" / "python"
            python.write_text("python", encoding="utf-8")
            python.chmod(0o700)
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
        provenance_runner=_dummy_provenance_runner,
        allow_unverified_source=True,
        validation_report=_validation_report(source),
    )
    monkeypatch.setattr(
        installer,
        "_verified_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            InstallationError("Installed release artifact is missing or unsafe")
        ),
    )

    with pytest.raises(InstallationError, match="artifact is missing or unsafe"):
        installer.install()
    assert list(installer.releases.iterdir()) == []


def test_install_removes_new_destination_when_migration_command_fails(
    tmp_path: Path,
) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    database.initialize()

    def fake_runner(arguments: list[str], _cwd: Path) -> subprocess.CompletedProcess[str]:
        if schema_probe := _schema_probe_result(arguments):
            return schema_probe
        if "build" in arguments:
            output = Path(arguments[arguments.index("--out-dir") + 1])
            (output / "wire.whl").write_bytes(b"reviewed wheel bytes")
        elif "venv" in arguments:
            environment = Path(arguments[-1])
            (environment / "bin").mkdir(parents=True)
            python = environment / "bin" / "python"
            python.write_text("python", encoding="utf-8")
            python.chmod(0o700)
        elif "pip" in arguments:
            python = Path(arguments[arguments.index("--python") + 1])
            executable = python.parent / "open-source-ai-news-wire"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o700)
        elif arguments[0].endswith("/launcher"):
            return subprocess.CompletedProcess(arguments, 1, "", "migration failed")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    installer = LocalInstaller(
        source,
        database.paths,
        application_root=tmp_path / "app",
        binary_root=tmp_path / "bin",
        runner=fake_runner,
        provenance_runner=_dummy_provenance_runner,
        allow_unverified_source=True,
        validation_report=_validation_report(source),
    )

    with pytest.raises(InstallationError, match="migration failed"):
        installer.install()
    assert list(installer.releases.iterdir()) == []
    assert installer.current.exists() is False
    assert installer.launcher.exists() is False


@pytest.mark.parametrize(
    ("status_output", "merge_code", "message"),
    (
        (" M source.py\n", 0, "dirty worktree"),
        ("", 1, "not reachable"),
    ),
)
def test_installer_rejects_dirty_or_unmerged_source_by_default(
    tmp_path: Path, status_output: str, merge_code: int, message: str
) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))

    def provenance_runner(arguments: list[str], _cwd: Path) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in arguments:
            return subprocess.CompletedProcess(arguments, 0, DUMMY_COMMIT_SHA, "")
        if "status" in arguments:
            return subprocess.CompletedProcess(arguments, 0, status_output, "")
        return subprocess.CompletedProcess(arguments, merge_code, "", "")

    installer = LocalInstaller(
        source,
        database.paths,
        application_root=tmp_path / "app",
        binary_root=tmp_path / "bin",
        provenance_runner=provenance_runner,
        validation_report=_validation_report(source),
    )

    with pytest.raises(InstallationError, match=message):
        installer._source_provenance()


def test_validation_report_is_commit_bound_and_all_checks_must_pass(tmp_path: Path) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    report = _validation_report(source)
    installer = LocalInstaller(
        source, database.paths, validation_report=report
    )
    assert installer._validated_report(DUMMY_COMMIT_SHA)["commit_sha"] == DUMMY_COMMIT_SHA

    report["sast"] = {**report["sast"], "passed": False}  # type: ignore[arg-type]
    with pytest.raises(InstallationError, match="sast"):
        installer._validated_report(DUMMY_COMMIT_SHA)
    with pytest.raises(InstallationError, match="does not match"):
        installer._validated_report("d" * 40)


def test_legacy_release_is_listed_unverified_but_cannot_be_rolled_back(
    tmp_path: Path,
) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    installer = LocalInstaller(
        source,
        database.paths,
        application_root=tmp_path / "app",
        binary_root=tmp_path / "bin",
    )
    legacy = installer.releases / "0.3.5-legacy"
    legacy.mkdir(parents=True)
    (legacy / "release.json").write_text(
        json.dumps(
            {"release_id": legacy.name, "version": "0.3.5", "schema_version": 6}
        ),
        encoding="utf-8",
    )

    listed = installer.list_releases()
    assert [(item.release_id, item.verified, item.provenance) for item in listed] == [
        (legacy.name, False, "legacy_unverified")
    ]
    with pytest.raises(InstallationError, match="unsupported shape"):
        installer.rollback(legacy.name)


def test_validation_report_path_and_shape_fail_closed(tmp_path: Path) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    report_path = tmp_path / "validation.json"
    report_path.write_text(json.dumps(_validation_report(source)), encoding="utf-8")
    installer = LocalInstaller(source, database.paths, validation_report=report_path)
    assert installer._validated_report(DUMMY_COMMIT_SHA)["commit_sha"] == DUMMY_COMMIT_SHA

    missing = LocalInstaller(
        source, database.paths, validation_report=tmp_path / "missing.json"
    )
    with pytest.raises(InstallationError, match="missing or unsafe"):
        missing._validated_report(DUMMY_COMMIT_SHA)

    symlink = tmp_path / "validation-link.json"
    symlink.symlink_to(report_path)
    with pytest.raises(InstallationError, match="missing or unsafe"):
        LocalInstaller(source, database.paths, validation_report=symlink)._validated_report(
            DUMMY_COMMIT_SHA
        )

    report_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(InstallationError, match="report is invalid"):
        installer._validated_report(DUMMY_COMMIT_SHA)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("full_tests", []),
        ("coverage_gate", {"passed": True, "command": "", "completed_at": "now"}),
        ("dependency_audit", {"passed": True, "command": "audit", "completed_at": ""}),
        ("sast", {"passed": "yes", "command": "scan", "completed_at": "now"}),
    ),
)
def test_validation_report_rejects_malformed_check_records(
    tmp_path: Path, field: str, value: object
) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    report = _validation_report(source)
    report[field] = value
    with pytest.raises(InstallationError, match=field):
        LocalInstaller(source, database.paths, validation_report=report)._validated_report(
            DUMMY_COMMIT_SHA
        )


def test_source_provenance_version_digest_and_command_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "uv.lock").write_text("", encoding="utf-8")
    (source / "pyproject.toml").write_text("not-toml", encoding="utf-8")
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    installer = LocalInstaller(source, database.paths)

    with pytest.raises(InstallationError, match="valid project version"):
        installer._source_version()
    (source / "pyproject.toml").write_text(
        '[project]\nname = "wire"\nversion = ""\n', encoding="utf-8"
    )
    with pytest.raises(InstallationError, match="valid project version"):
        installer._source_version()

    target = tmp_path / "real-project.toml"
    target.write_text('[project]\nname="wire"\nversion="0.3.6"\n', encoding="utf-8")
    (source / "pyproject.toml").unlink()
    (source / "pyproject.toml").symlink_to(target)
    with pytest.raises(InstallationError, match="cannot contain symlinks"):
        installer._source_digest()

    invalid_head = lambda arguments, _cwd: subprocess.CompletedProcess(
        arguments, 0, "short-sha", ""
    )
    installer.provenance_runner = invalid_head
    with pytest.raises(InstallationError, match="full Git commit SHA"):
        installer._source_provenance()

    monkeypatch.setattr(installer_module.shutil, "which", lambda _name: None)
    with pytest.raises(InstallationError, match="uv is required"):
        installer.install()

    installer.runner = lambda arguments, _cwd: subprocess.CompletedProcess(
        arguments, 1, "", "controlled failure"
    )
    with pytest.raises(InstallationError, match="controlled failure"):
        installer._checked(["command"], source)

    monkeypatch.setattr(
        installer_module.subprocess,
        "run",
        lambda arguments, **_kwargs: subprocess.CompletedProcess(arguments, 0, "ok", ""),
    )
    assert installer_module._run_command(["command"], source).returncode == 0


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_field",
        "bad_source_hash",
        "unsafe_wheel_name",
        "runtime_root",
        "schema_bool",
        "bad_commit",
        "validation_shape",
        "validation_failed",
        "git_shape",
        "git_type",
        "untrusted_git",
    ),
)
def test_verified_manifest_rejects_identity_and_provenance_tampering(
    tmp_path: Path, mutation: str
) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    installer = LocalInstaller(
        source,
        database.paths,
        application_root=tmp_path / "app",
        binary_root=tmp_path / "bin",
    )
    release, manifest = _write_valid_release(installer)
    if mutation == "missing_field":
        manifest.pop("python")
    elif mutation == "bad_source_hash":
        manifest["source_sha256"] = "short"
    elif mutation == "unsafe_wheel_name":
        manifest["wheel_filename"] = "../wire.whl"
    elif mutation == "runtime_root":
        manifest["runtime_root"] = "/another/runtime"
    elif mutation == "schema_bool":
        manifest["schema_version"] = True
    elif mutation == "bad_commit":
        manifest["commit_sha"] = "short"
    elif mutation == "validation_shape":
        manifest["validation"] = {"commit_sha": DUMMY_COMMIT_SHA}
    elif mutation == "validation_failed":
        manifest["validation"]["sast"]["passed"] = False  # type: ignore[index]
    elif mutation == "git_shape":
        manifest["git_validation"] = {"clean_worktree": True}
    elif mutation == "git_type":
        manifest["git_validation"]["clean_worktree"] = "yes"  # type: ignore[index]
    elif mutation == "untrusted_git":
        manifest["git_validation"]["clean_worktree"] = False  # type: ignore[index]
    (release / "release.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(InstallationError, match="unsupported shape|provenance"):
        installer._verified_manifest(release)


def test_verified_manifest_rejects_unsafe_layout_artifacts_and_permissions(
    tmp_path: Path,
) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    installer = LocalInstaller(
        source,
        database.paths,
        application_root=tmp_path / "app",
        binary_root=tmp_path / "bin",
    )
    with pytest.raises(InstallationError, match="unsafe filesystem layout"):
        installer._verified_manifest(tmp_path / "missing-release")

    release, _manifest = _write_valid_release(installer)
    (release / "release.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(InstallationError, match="manifest is invalid"):
        installer._verified_manifest(release)

    release, _manifest = _write_valid_release(installer)
    (release / "launcher").unlink()
    with pytest.raises(InstallationError, match="artifact is missing"):
        installer._verified_manifest(release)

    release, _manifest = _write_valid_release(installer)
    (release / "venv" / "bin" / "python").chmod(0o600)
    with pytest.raises(InstallationError, match="not executable"):
        installer._verified_manifest(release)


@pytest.mark.parametrize("failure", ("command", "wheel_count", "missing_cli"))
def test_install_cleans_staging_on_build_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))

    def runner(arguments: list[str], _cwd: Path) -> subprocess.CompletedProcess[str]:
        if failure == "command" and "build" in arguments:
            return subprocess.CompletedProcess(arguments, 1, "", "build failed")
        if "build" in arguments:
            output = Path(arguments[arguments.index("--out-dir") + 1])
            count = 2 if failure == "wheel_count" else 1
            for index in range(count):
                (output / f"wire-{index}.whl").write_bytes(b"wheel")
        elif "venv" in arguments:
            environment = Path(arguments[-1])
            (environment / "bin").mkdir(parents=True)
            python = environment / "bin" / "python"
            python.write_text("python", encoding="utf-8")
            python.chmod(0o700)
        elif "pip" in arguments and failure != "missing_cli":
            python = Path(arguments[arguments.index("--python") + 1])
            (python.parent / "open-source-ai-news-wire").write_text("entry", encoding="utf-8")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(installer_module.shutil, "which", lambda _name: "/uv")
    installer = LocalInstaller(
        source,
        database.paths,
        application_root=tmp_path / "app",
        binary_root=tmp_path / "bin",
        runner=runner,
        provenance_runner=_dummy_provenance_runner,
        allow_unverified_source=True,
        validation_report=_validation_report(source),
    )
    with pytest.raises(InstallationError, match="build failed|exactly one|CLI launcher"):
        installer.install()
    assert list(installer.releases.glob(".*")) == []


def test_list_and_rollback_fail_closed_for_missing_or_invalid_releases(tmp_path: Path) -> None:
    source = Path(__file__).parents[2]
    database = Database(resolve_runtime_paths(tmp_path / "runtime"))
    installer = LocalInstaller(
        source,
        database.paths,
        application_root=tmp_path / "app",
        binary_root=tmp_path / "bin",
    )
    assert installer.list_releases() == []
    with pytest.raises(InstallationError, match="not installed"):
        installer.rollback("missing")

    invalid = installer.releases / "invalid"
    invalid.mkdir(parents=True)
    (invalid / "release.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(InstallationError, match="manifest is invalid"):
        installer.list_releases()

    (invalid / "release.json").write_text(json.dumps([]), encoding="utf-8")
    with pytest.raises(InstallationError, match="Legacy release manifest is invalid"):
        installer.list_releases()
