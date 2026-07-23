"""Immutable local releases, stable launcher, and compatible rollback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import RuntimePaths
from .storage import SCHEMA_VERSION


class InstallationError(RuntimeError):
    """Raised when an atomic local installation cannot be completed."""


CommandRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class InstalledRelease:
    release_id: str
    path: Path
    active: bool
    version: str
    schema_version: int
    verified: bool = True
    provenance: str = "verified"


def _run_command(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )


class LocalInstaller:
    def __init__(
        self,
        source_root: Path,
        runtime_paths: RuntimePaths,
        *,
        application_root: Path | None = None,
        binary_root: Path | None = None,
        runner: CommandRunner = _run_command,
        provenance_runner: CommandRunner = _run_command,
        allow_unverified_source: bool = False,
        validation_report: Path | dict[str, object] | None = None,
    ):
        self.source_root = source_root.resolve()
        self.runtime_paths = runtime_paths
        self.application_root = (
            application_root or Path.home() / ".local" / "share" / "open-source-ai-news-wire"
        ).expanduser().resolve()
        self.binary_root = (binary_root or Path.home() / ".local" / "bin").expanduser().resolve()
        self.releases = self.application_root / "releases"
        self.current = self.application_root / "current"
        self.launcher = self.binary_root / "open-source-ai-news-wire"
        self.runner = runner
        self.provenance_runner = provenance_runner
        self.allow_unverified_source = allow_unverified_source
        self.validation_report = validation_report

    def _source_provenance(self) -> dict[str, object]:
        head = self.provenance_runner(
            ["git", "rev-parse", "--verify", "HEAD"], self.source_root
        )
        commit_sha = head.stdout.strip().lower() if head.returncode == 0 else ""
        if re.fullmatch(r"[0-9a-f]{40}", commit_sha) is None:
            raise InstallationError("Incoming source lacks a full Git commit SHA")
        status = self.provenance_runner(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            self.source_root,
        )
        clean = status.returncode == 0 and not status.stdout.strip()
        merged = self.provenance_runner(
            ["git", "merge-base", "--is-ancestor", commit_sha, "origin/main"],
            self.source_root,
        ).returncode == 0
        if not self.allow_unverified_source and (not clean or not merged):
            reason = "dirty worktree" if not clean else "commit is not reachable from origin/main"
            raise InstallationError(f"Incoming release provenance rejected: {reason}")
        return {
            "commit_sha": commit_sha,
            "git_validation": {
                "clean_worktree": clean,
                "commit_reachable_from_origin_main": merged,
                "audited_override": bool(self.allow_unverified_source and (not clean or not merged)),
            },
        }

    def _validated_report(self, commit_sha: str) -> dict[str, object]:
        value = self.validation_report
        if isinstance(value, Path):
            candidate = value.expanduser()
            if candidate.is_symlink():
                raise InstallationError("Release validation report is missing or unsafe")
            report_path = candidate.resolve()
            if not report_path.is_file():
                raise InstallationError("Release validation report is missing or unsafe")
            try:
                value = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise InstallationError("Release validation report is invalid") from error
        required_checks = {
            "full_tests",
            "coverage_gate",
            "dependency_audit",
            "sast",
        }
        if (
            not isinstance(value, dict)
            or set(value) != {"commit_sha", *required_checks}
            or value.get("commit_sha") != commit_sha
        ):
            raise InstallationError("Release validation report does not match the commit")
        validated: dict[str, object] = {"commit_sha": commit_sha}
        for name in sorted(required_checks):
            result = value.get(name)
            if (
                not isinstance(result, dict)
                or set(result) != {"passed", "command", "completed_at"}
                or result.get("passed") is not True
                or not isinstance(result.get("command"), str)
                or not str(result["command"]).strip()
                or len(str(result["command"])) > 500
                or not isinstance(result.get("completed_at"), str)
                or not str(result["completed_at"]).strip()
                or len(str(result["completed_at"])) > 100
            ):
                raise InstallationError(f"Release validation check did not pass: {name}")
            validated[name] = dict(result)
        return validated

    def _source_digest(self) -> str:
        digest = hashlib.sha256()
        candidates = [self.source_root / "pyproject.toml", self.source_root / "uv.lock"]
        candidates.extend(sorted((self.source_root / "src" / "open_source_ai_news_wire").rglob("*")))
        for path in candidates:
            if path.is_file() and "__pycache__" not in path.parts:
                if path.is_symlink():
                    raise InstallationError("Incoming release sources cannot contain symlinks")
                digest.update(str(path.relative_to(self.source_root)).encode("utf-8"))
                digest.update(path.read_bytes())
        return digest.hexdigest()

    @staticmethod
    def _file_digest(path: Path) -> str:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()

    def _verified_manifest(
        self,
        release_path: Path,
        *,
        expected_release_id: str | None = None,
        expected_source_sha256: str | None = None,
        expected_commit_sha: str | None = None,
    ) -> dict[str, object]:
        if release_path.is_symlink() or not release_path.is_dir():
            raise InstallationError("Installed release has an unsafe filesystem layout")
        manifest_path = release_path / "release.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise InstallationError("Installed release manifest is missing or unsafe")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise InstallationError("Installed release manifest is invalid") from error
        required = {
            "manifest_version",
            "release_id",
            "version",
            "schema_version",
            "python",
            "runtime_root",
            "source_sha256",
            "wheel_filename",
            "wheel_sha256",
            "commit_sha",
            "validation",
            "git_validation",
        }
        if not isinstance(manifest, dict) or set(manifest) != required:
            raise InstallationError("Installed release manifest has an unsupported shape")
        source_sha = manifest.get("source_sha256")
        wheel_sha = manifest.get("wheel_sha256")
        version = manifest.get("version")
        release_id = manifest.get("release_id")
        wheel_filename = manifest.get("wheel_filename")
        commit_sha = manifest.get("commit_sha")
        validation = manifest.get("validation")
        git_validation = manifest.get("git_validation")
        if (
            manifest.get("manifest_version") != 2
            or not isinstance(version, str)
            or not version
            or not isinstance(source_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_sha) is None
            or not isinstance(wheel_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", wheel_sha) is None
            or release_id != f"{version}-{source_sha[:12]}"
            or (expected_release_id is not None and release_id != expected_release_id)
            or (
                expected_source_sha256 is not None
                and source_sha != expected_source_sha256
            )
            or not isinstance(wheel_filename, str)
            or Path(wheel_filename).name != wheel_filename
            or not wheel_filename.endswith(".whl")
            or manifest.get("python") != "3.12.13"
            or manifest.get("runtime_root") != str(self.runtime_paths.root)
            or isinstance(manifest.get("schema_version"), bool)
            or not isinstance(manifest.get("schema_version"), int)
            or not isinstance(commit_sha, str)
            or re.fullmatch(r"[0-9a-f]{40}", commit_sha) is None
            or (expected_commit_sha is not None and commit_sha != expected_commit_sha)
            or not isinstance(validation, dict)
            or validation.get("commit_sha") != commit_sha
            or set(validation)
            != {
                "commit_sha",
                "full_tests",
                "coverage_gate",
                "dependency_audit",
                "sast",
            }
            or any(
                not isinstance(validation.get(name), dict)
                or set(validation[name]) != {"passed", "command", "completed_at"}
                or validation[name].get("passed") is not True
                or not isinstance(validation[name].get("command"), str)
                or not str(validation[name]["command"]).strip()
                or not isinstance(validation[name].get("completed_at"), str)
                or not str(validation[name]["completed_at"]).strip()
                for name in ("full_tests", "coverage_gate", "dependency_audit", "sast")
            )
            or not isinstance(git_validation, dict)
            or set(git_validation)
            != {
                "clean_worktree",
                "commit_reachable_from_origin_main",
                "audited_override",
            }
            or any(not isinstance(value, bool) for value in git_validation.values())
            or (
                not self.allow_unverified_source
                and (
                    not git_validation["clean_worktree"]
                    or not git_validation["commit_reachable_from_origin_main"]
                    or git_validation["audited_override"]
                )
            )
        ):
            raise InstallationError("Installed release provenance does not match its identity")
        wheel = release_path / "dist" / wheel_filename
        python = release_path / "venv" / "bin" / "python"
        launcher = release_path / "launcher"
        for artifact in (wheel, python, launcher):
            if artifact.is_symlink() or not artifact.is_file():
                raise InstallationError("Installed release artifact is missing or unsafe")
        if self._file_digest(wheel) != wheel_sha:
            raise InstallationError("Installed release wheel failed SHA-256 verification")
        if not os.access(python, os.X_OK) or not os.access(launcher, os.X_OK):
            raise InstallationError("Installed release executables are not executable")
        return manifest

    def _source_version(self) -> str:
        project_file = self.source_root / "pyproject.toml"
        try:
            project = tomllib.loads(project_file.read_text(encoding="utf-8"))
            version = project["project"]["version"]
        except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as error:
            raise InstallationError(
                "The incoming source does not declare a valid project version"
            ) from error
        if not isinstance(version, str) or not version.strip():
            raise InstallationError(
                "The incoming source does not declare a valid project version"
            )
        return version.strip()

    def install(self) -> InstalledRelease:
        uv = shutil.which("uv")
        if not uv:
            raise InstallationError("uv is required to build the pinned local release")
        source_version = self._source_version()
        provenance = self._source_provenance()
        validation = self._validated_report(str(provenance["commit_sha"]))
        source_sha256 = self._source_digest()
        release_id = f"{source_version}-{source_sha256[:12]}"
        destination = self.releases / release_id
        self.releases.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.binary_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        required = (
            destination / "release.json",
            destination / "venv" / "bin" / "python",
            destination / "launcher",
            destination / "dist",
        )
        if destination.exists() and not all(path.exists() for path in required):
            shutil.rmtree(destination)
        if not destination.exists():
            staging = Path(tempfile.mkdtemp(prefix=f".{release_id}-", dir=self.releases))
            try:
                distribution = staging / "dist"
                distribution.mkdir(mode=0o700)
                self._checked([uv, "build", "--wheel", "--out-dir", str(distribution)], self.source_root)
                wheels = list(distribution.glob("*.whl"))
                if len(wheels) != 1:
                    raise InstallationError("Expected exactly one application wheel")
                wheel_sha256 = self._file_digest(wheels[0])
                environment = staging / "venv"
                self._checked([uv, "venv", "--python", "3.12.13", str(environment)], self.source_root)
                self._checked(
                    [uv, "pip", "install", "--python", str(environment / "bin" / "python"), str(wheels[0])],
                    self.source_root,
                )
                executable = environment / "bin" / "open-source-ai-news-wire"
                if not executable.exists():
                    raise InstallationError("Installed release did not create its CLI launcher")
                manifest = {
                    "manifest_version": 2,
                    "release_id": release_id,
                    "version": source_version,
                    "schema_version": SCHEMA_VERSION,
                    "python": "3.12.13",
                    "runtime_root": str(self.runtime_paths.root),
                    "source_sha256": source_sha256,
                    "wheel_filename": wheels[0].name,
                    "wheel_sha256": wheel_sha256,
                    "commit_sha": provenance["commit_sha"],
                    "git_validation": provenance["git_validation"],
                    "validation": validation,
                }
                (staging / "release.json").write_text(
                    json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
                )
                os.chmod(staging / "release.json", 0o600)
                final_python = destination / "venv" / "bin" / "python"
                (staging / "launcher").write_text(
                    "#!/bin/sh\n"
                    f"exec {shlex.quote(str(final_python))} -m open_source_ai_news_wire \"$@\"\n",
                    encoding="utf-8",
                )
                os.chmod(staging / "launcher", 0o700)
                os.replace(staging, destination)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        self._verified_manifest(
            destination,
            expected_release_id=release_id,
            expected_source_sha256=source_sha256,
            expected_commit_sha=str(provenance["commit_sha"]),
        )
        installed_cli = destination / "launcher"
        self._checked(
            [str(installed_cli), "--data-root", str(self.runtime_paths.root), "migrate"],
            self.source_root,
        )
        self._switch(destination)
        trusted = bool(
            provenance["git_validation"]["clean_worktree"]
            and provenance["git_validation"]["commit_reachable_from_origin_main"]
            and not provenance["git_validation"]["audited_override"]
        )
        return InstalledRelease(
            release_id,
            destination,
            True,
            source_version,
            SCHEMA_VERSION,
            trusted,
            "verified" if trusted else "audited_override",
        )

    def list_releases(self) -> list[InstalledRelease]:
        active = self.current.resolve() if self.current.exists() else None
        result: list[InstalledRelease] = []
        if not self.releases.exists():
            return result
        for path in sorted(self.releases.iterdir(), reverse=True):
            manifest_path = path / "release.json"
            if not path.is_dir() or not manifest_path.exists():
                continue
            try:
                raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise InstallationError("Installed release manifest is invalid") from error
            if not isinstance(raw_manifest, dict) or raw_manifest.get("manifest_version") != 2:
                try:
                    result.append(
                        InstalledRelease(
                            str(raw_manifest["release_id"]), path, path.resolve() == active,
                            str(raw_manifest["version"]), int(raw_manifest["schema_version"]),
                            False, "legacy_unverified",
                        )
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise InstallationError("Legacy release manifest is invalid") from error
                continue
            manifest = self._verified_manifest(path)
            git_validation = manifest["git_validation"]
            trusted = bool(
                git_validation["clean_worktree"]
                and git_validation["commit_reachable_from_origin_main"]
                and not git_validation["audited_override"]
            )
            result.append(
                InstalledRelease(
                    str(manifest["release_id"]), path, path.resolve() == active,
                    str(manifest["version"]), int(manifest["schema_version"]),
                    trusted, "verified" if trusted else "audited_override",
                )
            )
        return result

    def rollback(self, release_id: str) -> InstalledRelease:
        destination = self.releases / release_id
        manifest_path = destination / "release.json"
        if not manifest_path.exists():
            raise InstallationError("Requested release is not installed")
        manifest = self._verified_manifest(
            destination, expected_release_id=release_id
        )
        if int(manifest["schema_version"]) != SCHEMA_VERSION:
            raise InstallationError("Requested release is not compatible with the current database schema")
        self._switch(destination)
        git_validation = manifest["git_validation"]
        trusted = bool(
            git_validation["clean_worktree"]
            and git_validation["commit_reachable_from_origin_main"]
            and not git_validation["audited_override"]
        )
        return InstalledRelease(
            release_id,
            destination,
            True,
            str(manifest["version"]),
            int(manifest["schema_version"]),
            trusted,
            "verified" if trusted else "audited_override",
        )

    def uninstall_application(self) -> None:
        self.launcher.unlink(missing_ok=True)
        self.current.unlink(missing_ok=True)
        if self.releases.exists():
            shutil.rmtree(self.releases)

    def _switch(self, destination: Path) -> None:
        self.application_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary_current = self.application_root / ".current-next"
        temporary_current.unlink(missing_ok=True)
        temporary_current.symlink_to(destination, target_is_directory=True)
        os.replace(temporary_current, self.current)
        temporary_launcher = self.binary_root / ".open-source-ai-news-wire-next"
        temporary_launcher.unlink(missing_ok=True)
        temporary_launcher.symlink_to(self.current / "launcher")
        os.replace(temporary_launcher, self.launcher)

    def _checked(self, arguments: list[str], cwd: Path) -> None:
        result = self.runner(arguments, cwd)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise InstallationError(detail or f"Command failed: {arguments[0]}")
