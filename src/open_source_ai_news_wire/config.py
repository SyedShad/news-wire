"""Runtime configuration and local data-boundary validation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ENV_DATA_ROOT = "OPEN_SOURCE_AI_NEWS_WIRE_DATA"
DEFAULT_DATA_ROOT = Path.home() / "open-source-ai-news-wire-data"


class UnsafeDataRoot(ValueError):
    """Raised when runtime data could enter source control."""


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    root: Path
    database: Path
    content: Path
    editorial: Path
    operations: Path
    config: Path
    temporary: Path
    cloud_sync_warning: str | None = None


def _inside_git_worktree(path: Path) -> bool:
    current = path.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return True
    return False


def _cloud_sync_warning(path: Path) -> str | None:
    normalized = str(path.expanduser().resolve()).lower()
    markers = (
        "/library/mobile documents/",
        "/icloud drive/",
        "/dropbox/",
        "/onedrive/",
        "/google drive/",
    )
    if any(marker in normalized for marker in markers):
        return "This path appears to be cloud-synchronized; V1 is designed for local-only storage."
    return None


def resolve_runtime_paths(data_root: str | os.PathLike[str] | None = None) -> RuntimePaths:
    raw = data_root or os.environ.get(ENV_DATA_ROOT) or DEFAULT_DATA_ROOT
    root = Path(raw).expanduser().resolve()
    if _inside_git_worktree(root):
        raise UnsafeDataRoot(
            f"Runtime data root must be outside every Git worktree: {root}"
        )
    return RuntimePaths(
        root=root,
        database=root / "database" / "news-wire.sqlite3",
        content=root / "content",
        editorial=root / "editorial",
        operations=root / "operations",
        config=root / "config",
        temporary=root / "tmp",
        cloud_sync_warning=_cloud_sync_warning(root),
    )


def ensure_runtime_layout(paths: RuntimePaths) -> None:
    directories = (
        paths.root,
        paths.database.parent,
        paths.content / "evidence",
        paths.content / "primary-snapshots",
        paths.content / "translations",
        paths.editorial / "drafts",
        paths.editorial / "evidence-bundles",
        paths.editorial / "exports",
        paths.operations / "locks",
        paths.operations / "queue",
        paths.operations / "logs",
        paths.operations / "reports",
        paths.config,
        paths.temporary,
    )
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
