from __future__ import annotations

import json
from pathlib import Path

import pytest

from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.content_store import ContentStore
from open_source_ai_news_wire.operations import (
    create_purge_plan,
    diagnostic_payload,
    execute_purge_plan,
    export_diagnostics,
)
from open_source_ai_news_wire.scheduler import SchedulerStatus
from open_source_ai_news_wire.source_registry import synchronize_sources
from open_source_ai_news_wire.storage import Database


def _database(tmp_path: Path) -> Database:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    synchronize_sources(database)
    return database


def test_diagnostics_are_redacted_and_private(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.execute(
        """
        INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
        VALUES('warning', 'source', 'Bounded public error', '2026-07-14T00:00:00Z', '{}')
        """
    )
    destination = export_diagnostics(database, tmp_path / "exports" / "diagnostics.json")
    payload = json.loads(destination.read_text(encoding="utf-8"))

    assert payload["integrity"] == "ok"
    assert payload["source_health"]
    rendered = destination.read_text(encoding="utf-8")
    assert "https://" not in rendered
    assert '"source_url"' not in rendered
    assert oct(destination.stat().st_mode & 0o777) == "0o600"


def test_purge_requires_preview_and_separate_protected_selection(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with pytest.raises(ValueError, match="Protected editorial"):
        create_purge_plan(database, ["drafts"])
    with pytest.raises(ValueError, match="missing"):
        execute_purge_plan(database, "purge-missing")


def test_purge_deletes_only_selected_categories_and_has_no_replay(tmp_path: Path) -> None:
    database = _database(tmp_path)
    stored = ContentStore(database.paths.content).put(b"snapshot")
    database.execute(
        """
        INSERT INTO retained_content(digest, category, byte_count, created_at)
        VALUES(?, 'evidence', ?, '2026-07-14T00:00:00Z')
        """,
        (stored.digest, stored.size),
    )
    database.execute(
        """
        INSERT INTO raw_observation(
          source_id, external_id, canonical_url, title, published_at, observed_at,
          language, fingerprint, content_hash, metadata_json
        ) VALUES('openai-news', 'one', 'https://openai.com/news/one', 'AI release',
          '2026-07-14T00:00:00Z', '2026-07-14T00:01:00Z', 'en', 'fp', 'hash', '{}')
        """
    )
    plan = create_purge_plan(database, ["raw_observations", "retained_content"])

    deleted = execute_purge_plan(database, plan.plan_id)

    assert deleted == {"raw_observations": 1, "retained_content": 1}
    assert stored.path.exists() is False
    assert database.one("SELECT COUNT(*) AS count FROM source_registry")["count"] > 0
    with pytest.raises(ValueError, match="already executed"):
        execute_purge_plan(database, plan.plan_id)


def test_diagnostic_payload_reports_queue_without_exposing_payloads(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.execute(
        """
        INSERT INTO work_item(kind, status, priority, payload_json, created_at, updated_at)
        VALUES('research', 'queued', 1, '{"secret":"not-exported"}', '2026-07-14T00:00:00Z', '2026-07-14T00:00:00Z')
        """
    )

    database.set_state("schedule_installed", "false", "2026-07-14T00:00:00Z")
    database.set_state("schedule_status", "not_installed", "2026-07-14T00:00:00Z")
    payload = diagnostic_payload(
        database,
        scheduler_status=lambda: SchedulerStatus(
            True,
            True,
            "active",
            Path("/tmp/com.opensourceainewswire.scanner.plist"),
        ),
    )

    assert payload["queue"]["count"] == 1
    assert payload["schedule"] == {
        "installed": True,
        "loaded": True,
        "status": "active",
        "error": None,
        "last_scan_at": "Never",
    }
    assert "not-exported" not in json.dumps(payload)
