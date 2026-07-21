from __future__ import annotations

import json
import sqlite3
import stat
import tomllib
from pathlib import Path

import pytest

from open_source_ai_news_wire.config import UnsafeDataRoot, ensure_runtime_layout, resolve_runtime_paths
from open_source_ai_news_wire import __version__
from open_source_ai_news_wire.content_store import ContentStore
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.settings import InvalidConfiguration, load_settings, load_source_definitions
from open_source_ai_news_wire.storage import Database, MIGRATIONS, MigrationRequired, SCHEMA_V1, SCHEMA_VERSION


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
    assert database.schema_version() == SCHEMA_VERSION
    assert database.integrity_check() == "ok"


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


def test_settings_merge_private_local_overrides(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(tmp_path / "wire-data")
    ensure_runtime_layout(paths)
    (paths.config / "settings.local.json").write_text(
        json.dumps({"usage": {"background_units": 4}}), encoding="utf-8"
    )

    settings = load_settings(paths)

    assert settings.section("usage")["background_units"] == 4
    assert settings.section("usage")["urgent_reserve_units"] == 2
    assert load_source_definitions(paths)[0]["id"] == "openai-news"


def test_unknown_source_override_is_rejected(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(tmp_path / "wire-data")
    ensure_runtime_layout(paths)
    (paths.config / "sources.local.json").write_text(
        json.dumps({"sources": {"missing": {"enabled_by_default": False}}}),
        encoding="utf-8",
    )

    with pytest.raises(InvalidConfiguration, match="Unknown local source"):
        load_source_definitions(paths)


def test_content_store_is_deduplicated_and_private(tmp_path: Path) -> None:
    store = ContentStore(tmp_path / "content")

    first = store.put(b"bounded evidence")
    second = store.put(b"bounded evidence")

    assert first.digest == second.digest
    assert first.path == second.path
    assert store.get(first.digest) == b"bounded evidence"
    assert stat.S_IMODE(first.path.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="Invalid content digest"):
        store.get("../escape")


def test_existing_schema_requires_explicit_migration_and_cancels_staged_work(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(tmp_path / "wire-data")
    ensure_runtime_layout(paths)
    with sqlite3.connect(paths.database) as connection:
        connection.executescript(SCHEMA_V1)
        connection.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '1')"
        )
        connection.execute(
            """
            INSERT INTO work_item(kind, status, priority, payload_json, created_at, updated_at)
            VALUES('scout_scan', 'queued', 100, '{}', '2026-07-14T00:00:00Z', '2026-07-14T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO scan_run(trigger_type, started_at, result, details)
            VALUES('manual', '2026-07-14T00:00:00Z', 'queued', 'Staged dashboard request')
            """
        )
        connection.commit()
    database = Database(paths)

    with pytest.raises(MigrationRequired):
        database.initialize()

    assert database.migrate() == SCHEMA_VERSION
    assert database.initialize() is None
    assert database.one("SELECT status FROM work_item") == {"status": "cancelled"}
    scan = database.one("SELECT result, finished_at FROM scan_run")
    assert scan["result"] == "cancelled"
    assert scan["finished_at"] is not None
    assert database.one("SELECT COUNT(*) AS count FROM source_state") == {"count": 0}


def test_v3_migration_preserves_data_and_supersedes_legacy_research(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(tmp_path / "wire-data")
    ensure_runtime_layout(paths)
    with sqlite3.connect(paths.database) as connection:
        connection.executescript(SCHEMA_V1)
        for statement in MIGRATIONS[2]:
            connection.execute(statement)
        connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '2')")
        connection.execute(
            """
            INSERT INTO story_cluster(
                id, slug, headline, summary, lane, openness_class, status, priority,
                priority_score, freshness, first_public_at, detected_at, created_at, updated_at
            ) VALUES('story-one', 'story-one', 'AI event', 'Summary', 'Broader AI News',
                     'not stated', 'signal', 'Standard', 50, 'Fresh',
                     '2026-07-14T00:00:00Z', '2026-07-14T00:01:00Z',
                     '2026-07-14T00:01:00Z', '2026-07-14T00:01:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO work_item(kind, story_id, status, priority, payload_json, created_at, updated_at)
            VALUES('research', 'story-one', 'queued', 50, '{}',
                   '2026-07-14T00:02:00Z', '2026-07-14T00:02:00Z')
            """
        )
        connection.commit()

    database = Database(paths)
    assert database.migrate() == 4
    assert database.one("SELECT headline FROM story_cluster WHERE id = 'story-one'") == {
        "headline": "AI event"
    }
    assert database.one("SELECT status, last_error_class FROM work_item") == {
        "status": "cancelled",
        "last_error_class": "superseded_legacy_research",
    }
    assert database.one(
        "SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'table' AND name = 'evidence_source'"
    ) == {"count": 1}


def test_v4_migration_requires_reporting_origin_review_and_preserves_draft(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(tmp_path / "wire-data")
    ensure_runtime_layout(paths)
    now = "2026-07-21T08:00:00Z"
    with sqlite3.connect(paths.database) as connection:
        connection.executescript(SCHEMA_V1)
        for version in (2, 3):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '3')")
        connection.execute(
            """
            INSERT INTO story_cluster(
                id, slug, headline, summary, lane, openness_class, status, priority,
                priority_score, freshness, first_public_at, detected_at, created_at, updated_at
            ) VALUES('story-reporting', 'reporting', 'Syndicated report', 'Summary',
                     'Broader AI News', 'not stated', 'draft_ready', 'High', 75, 'Breaking',
                     ?, ?, ?, ?)
            """,
            (now, now, now, now),
        )
        connection.execute(
            """
            INSERT INTO candidate(story_id, evidence_gate, importance_gate, score, score_json, qualified_at)
            VALUES('story-reporting', 1, 1, 75, '{}', ?)
            """,
            (now,),
        )
        for host in ("yahoo.com", "indiatimes.com"):
            connection.execute(
                """
                INSERT INTO evidence_source(
                    story_id, requested_url, final_url, canonical_url, publisher_key,
                    acquisition_method, title, confirmed_role, status, confirmed_at,
                    created_at, updated_at
                ) VALUES('story-reporting', ?, ?, ?, ?, 'manual', 'Axios report',
                         'Reporting', 'confirmed', ?, ?, ?)
                """,
                (f"https://{host}/report", f"https://{host}/report", f"https://{host}/report", host, now, now, now),
            )
        connection.execute(
            """
            INSERT INTO draft(
                story_id, mode, status, version, headline, metadata, body, lens,
                sources_json, created_at, updated_at
            ) VALUES('story-reporting', 'Neutral News Brief', 'Current', 1,
                     'Existing draft', 'Breaking', 'Preserve me', '', '[]', ?, ?)
            """,
            (now, now),
        )
        connection.commit()

    database = Database(paths)
    assert database.migrate() == 4
    assert database.query(
        "SELECT DISTINCT origin_status FROM evidence_source ORDER BY origin_status"
    ) == [{"origin_status": "needs_review"}]
    assert database.one("SELECT evidence_gate FROM candidate") == {"evidence_gate": 0}
    assert database.one("SELECT status FROM story_cluster") == {"status": "signal"}
    assert database.one("SELECT status, body FROM draft") == {
        "status": "Needs Review",
        "body": "Preserve me",
    }


def test_storage_pressure_levels_can_be_forced(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    free = database.storage_pressure().free_bytes

    assert database.storage_pressure(warning_bytes=free + 2, critical_bytes=free + 1).level == "critical"
    assert database.storage_pressure(warning_bytes=free + 1, critical_bytes=0).level == "warning"
    assert database.storage_pressure(warning_bytes=0, critical_bytes=0).level == "normal"
