from __future__ import annotations

import stat
import tomllib
from pathlib import Path

import pytest

from open_source_ai_news_wire.config import UnsafeDataRoot, resolve_runtime_paths
from open_source_ai_news_wire import __version__
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.storage import Database


def test_runtime_root_refuses_git_worktree() -> None:
    with pytest.raises(UnsafeDataRoot):
        resolve_runtime_paths(Path.cwd() / ".news-wire-data")


def test_database_initializes_private_layout(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(tmp_path / "wire-data")
    database = Database(paths)
    database.initialize()

    assert paths.database.exists()
    assert stat.S_IMODE(paths.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.database.stat().st_mode) == 0o600
    assert database.get_state("missing", "fallback") == "fallback"


def test_demo_seed_is_explicit_and_idempotent(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()

    assert seed_demo_data(database) is True
    assert seed_demo_data(database) is False
    assert database.one("SELECT COUNT(*) AS count FROM story_cluster")["count"] == 5
    assert database.get_state("demo_mode") == "true"


def test_cloud_synchronized_path_is_warned() -> None:
    paths = resolve_runtime_paths("/tmp/Dropbox/wire-data")
    assert paths.cloud_sync_warning is not None


def test_application_package_version_matches_project_metadata() -> None:
    pyproject = tomllib.loads((Path.cwd() / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__
