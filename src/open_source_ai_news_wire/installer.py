"""Immutable local releases, stable launcher, and compatible rollback."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import __version__
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

    def _source_digest(self) -> str:
        digest = hashlib.sha256()
        candidates = [self.source_root / "pyproject.toml", self.source_root / "uv.lock"]
        candidates.extend(sorted((self.source_root / "src" / "open_source_ai_news_wire").rglob("*")))
        for path in candidates:
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(self.source_root)).encode("utf-8"))
                digest.update(path.read_bytes())
        return digest.hexdigest()[:12]

    def install(self) -> InstalledRelease:
        uv = shutil.which("uv")
        if not uv:
            raise InstallationError("uv is required to build the pinned local release")
        release_id = f"{__version__}-{self._source_digest()}"
        destination = self.releases / release_id
        self.releases.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.binary_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        required = (
            destination / "release.json",
            destination / "venv" / "bin" / "python",
            destination / "launcher",
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
                    "release_id": release_id,
                    "version": __version__,
                    "schema_version": SCHEMA_VERSION,
                    "python": "3.12.13",
                    "runtime_root": str(self.runtime_paths.root),
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
        installed_cli = destination / "launcher"
        self._checked(
            [str(installed_cli), "--data-root", str(self.runtime_paths.root), "migrate"],
            self.source_root,
        )
        self._switch(destination)
        return InstalledRelease(release_id, destination, True, __version__, SCHEMA_VERSION)

    def list_releases(self) -> list[InstalledRelease]:
        active = self.current.resolve() if self.current.exists() else None
        result: list[InstalledRelease] = []
        if not self.releases.exists():
            return result
        for path in sorted(self.releases.iterdir(), reverse=True):
            manifest_path = path / "release.json"
            if not path.is_dir() or not manifest_path.exists():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            result.append(
                InstalledRelease(
                    str(manifest["release_id"]), path, path.resolve() == active,
                    str(manifest["version"]), int(manifest["schema_version"]),
                )
            )
        return result

    def rollback(self, release_id: str) -> InstalledRelease:
        destination = self.releases / release_id
        manifest_path = destination / "release.json"
        if not manifest_path.exists():
            raise InstallationError("Requested release is not installed")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if int(manifest["schema_version"]) != SCHEMA_VERSION:
            raise InstallationError("Requested release is not compatible with the current database schema")
        self._switch(destination)
        return InstalledRelease(
            release_id, destination, True, str(manifest["version"]), int(manifest["schema_version"])
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
