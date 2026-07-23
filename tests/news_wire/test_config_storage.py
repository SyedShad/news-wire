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
    assert database.migrate() == SCHEMA_VERSION
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
    assert database.migrate() == SCHEMA_VERSION
    assert database.query(
        "SELECT DISTINCT origin_status FROM evidence_source ORDER BY origin_status"
    ) == [{"origin_status": "needs_review"}]
    assert database.one("SELECT evidence_gate FROM candidate") == {"evidence_gate": 0}
    assert database.one("SELECT status FROM story_cluster") == {"status": "signal"}
    assert database.one("SELECT status, body FROM draft") == {
        "status": "Needs Review",
        "body": "Preserve me",
    }


def test_v5_migration_adds_manual_override_audit_without_changing_existing_candidates(
    tmp_path: Path,
) -> None:
    paths = resolve_runtime_paths(tmp_path / "wire-data")
    ensure_runtime_layout(paths)
    now = "2026-07-23T08:00:00Z"
    with sqlite3.connect(paths.database) as connection:
        connection.executescript(SCHEMA_V1)
        for version in (2, 3, 4):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '4')"
        )
        connection.execute(
            """
            INSERT INTO story_cluster(
                id, slug, headline, summary, lane, openness_class, status, priority,
                priority_score, freshness, first_public_at, detected_at, created_at, updated_at
            ) VALUES('story-existing', 'existing', 'Existing story', 'Summary',
                     'Broader AI News', 'not stated', 'candidate', 'High', 72, 'Fresh',
                     ?, ?, ?, ?)
            """,
            (now, now, now, now),
        )
        connection.execute(
            """
            INSERT INTO candidate(
                story_id, evidence_gate, importance_gate, score, score_json, qualified_at
            ) VALUES('story-existing', 1, 1, 72, '{}', ?)
            """,
            (now,),
        )
        connection.commit()

    database = Database(paths)
    assert database.migrate() == SCHEMA_VERSION

    candidate = database.one(
        """
        SELECT manual_override, manual_override_at, manual_override_action_id,
               manual_override_snapshot_json
        FROM candidate WHERE story_id = 'story-existing'
        """
    )

    assert candidate == {
        "manual_override": 0,
        "manual_override_at": None,
        "manual_override_action_id": None,
        "manual_override_snapshot_json": "{}",
    }


def test_v6_migration_adds_dynamic_ranking_state_without_reusing_generic_update_time(
    tmp_path: Path,
) -> None:
    paths = resolve_runtime_paths(tmp_path / "wire-data")
    ensure_runtime_layout(paths)
    old = "2026-07-13T08:00:00Z"
    recent_generic_update = "2026-07-23T08:00:00Z"
    with sqlite3.connect(paths.database) as connection:
        connection.executescript(SCHEMA_V1)
        for version in (2, 3, 4, 5):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '5')")
        connection.execute(
            """
            INSERT INTO story_cluster(
                id, slug, headline, summary, lane, openness_class, status, priority,
                priority_score, freshness, first_public_at, detected_at,
                watch_status, material_update, created_at, updated_at
            ) VALUES('old-watch', 'old-watch', 'AI model security release', 'AI regulation update',
                     'Broader AI News', 'not stated', 'signal', 'High potential', 88,
                     'Breaking', ?, ?, 'Expired', 1, ?, ?)
            """,
            (old, old, old, recent_generic_update),
        )
        connection.execute(
            """
            INSERT INTO source_item(
                story_id, source_name, source_role, title, url, published_at
            ) VALUES('old-watch', 'Old discovery feed', 'Discovery',
                     'AI model security release', 'https://example.com/story', ?)
            """,
            (old,),
        )
        connection.commit()

    database = Database(paths)
    assert database.migrate() == SCHEMA_VERSION
    story = database.one(
        """
        SELECT importance_score, material_updated_at, ingestion_context, priority,
               priority_score, freshness FROM story_cluster WHERE id = 'old-watch'
        """
    )
    assert story["importance_score"] > 0
    assert story["material_updated_at"] is None
    assert story["ingestion_context"] == "legacy"
    assert story["priority"] == "Standard"
    assert story["priority_score"] == 88
    assert story["freshness"] == "Breaking"
    assert database.one("SELECT COUNT(*) AS count FROM discovery_lead") == {"count": 0}
    assert database.one("SELECT COUNT(*) AS count FROM momentum_snapshot") == {"count": 0}


def test_v7_migration_versions_revisions_disables_assistance_and_repairs_only_exact_updates(
    tmp_path: Path,
) -> None:
    paths = resolve_runtime_paths(tmp_path / "wire-data")
    ensure_runtime_layout(paths)
    false_update = "2026-07-23T11:13:29Z"
    other_update = "2026-07-23T11:13:30Z"
    with sqlite3.connect(paths.database) as connection:
        connection.executescript(SCHEMA_V1)
        for version in (2, 3, 4, 5, 6):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '6')")
        connection.execute(
            """
            INSERT INTO source_registry(id, name, family, monitoring_role, url)
            VALUES('source-one', 'Source One', 'Official', 'Event', 'https://example.com/feed')
            """
        )
        for story_id, updated in (
            ("story-debeeae2b4fa78a37fda", false_update),
            ("story-670cb7d55f310b539bbc", other_update),
        ):
            connection.execute(
                """
                INSERT INTO story_cluster(
                    id, slug, headline, summary, lane, openness_class, status,
                    priority, priority_score, freshness, first_public_at, detected_at,
                    material_update, created_at, updated_at, importance_score,
                    material_updated_at
                ) VALUES(?, ?, 'AI model release', 'Summary', 'Broader AI News',
                         'not stated', 'signal', 'High', 70, 'Updated',
                         '2026-07-13T08:00:00Z', '2026-07-13T08:05:00Z', 1,
                         '2026-07-13T08:05:00Z', '2026-07-23T11:14:00Z', 40, ?)
                """,
                (story_id, story_id, updated),
            )
        connection.execute(
            """
            INSERT INTO raw_observation(
                source_id, external_id, canonical_url, title, published_at,
                observed_at, fingerprint, content_hash
            ) VALUES('source-one', 'external-one', 'https://example.com/story',
                     'AI model release', '2026-07-13T08:00:00Z',
                     '2026-07-13T08:05:00Z', 'fingerprint', 'legacy-hash')
            """
        )
        connection.execute(
            """
            INSERT INTO source_item(
                story_id, source_name, source_role, title, url, published_at,
                source_registry_id, content_hash
            ) VALUES('story-debeeae2b4fa78a37fda', 'Source One', 'Event',
                     'AI model release', 'https://example.com/story',
                     '2026-07-13T08:00:00Z', 'source-one', 'legacy-hash')
            """
        )
        connection.execute(
            "INSERT INTO app_state(key, value, updated_at) VALUES('assistance_enabled', 'true', '2026-07-23T11:00:00Z')"
        )
        action_id = connection.execute(
            """
            INSERT INTO review_action(story_id, action, draft_mode, created_at)
            VALUES('story-debeeae2b4fa78a37fda', 'approve_neutral',
                   'Neutral News Brief', '2026-07-23T11:00:00Z')
            """
        ).lastrowid
        exact_payload = {
            "schema_version": 3,
            "story_id": "story-debeeae2b4fa78a37fda",
            "story_revision": 1,
            "review_action_id": int(action_id),
            "claim_signatures": [{"id": 1, "signature": "a" * 64}],
            "source_signatures": [{"id": "registry:1", "signature": "b" * 64}],
        }
        connection.executemany(
            """
            INSERT INTO work_item(
                kind, story_id, status, priority, payload_json, created_at, updated_at
            ) VALUES('draft', 'story-debeeae2b4fa78a37fda', ?, 1, ?,
                     '2026-07-23T11:00:00Z', '2026-07-23T11:00:00Z')
            """,
            [
                ("queued", json.dumps({"schema_version": 2})),
                ("generating", json.dumps({**exact_payload, "source_signatures": None})),
                ("waiting", "{malformed"),
                ("pending", json.dumps({**exact_payload, "claim_signatures": []})),
                ("queued", json.dumps({**exact_payload, "source_signatures": []})),
                ("pending", json.dumps(exact_payload)),
                ("completed", json.dumps({"schema_version": 2})),
                ("cancelled", json.dumps({"schema_version": 2})),
                ("needs_reapproval", json.dumps({"schema_version": 2})),
            ],
        )
        connection.commit()

    database = Database(paths)
    assert database.migrate() == SCHEMA_VERSION
    repaired = database.one(
        "SELECT story_revision, material_revision, material_update, material_updated_at FROM story_cluster WHERE id = 'story-debeeae2b4fa78a37fda'"
    )
    assert repaired == {
        "story_revision": 1,
        "material_revision": 1,
        "material_update": 0,
        "material_updated_at": None,
    }
    assert database.one(
        "SELECT material_updated_at FROM story_cluster WHERE id = 'story-670cb7d55f310b539bbc'"
    ) == {"material_updated_at": other_update}
    assert database.one(
        """
        SELECT observation_hash_version, observation_hash, source_reported_at,
               effective_published_at, timestamp_status
        FROM source_revision
        """
    ) == {
        "observation_hash_version": 1,
        "observation_hash": "legacy-hash",
        "source_reported_at": "2026-07-13T08:00:00Z",
        "effective_published_at": "2026-07-13T08:00:00Z",
        "timestamp_status": "legacy_assumed",
    }
    assert database.get_state("assistance_enabled") == "false"
    assert database.get_state("assistance_isolation_gate") == "requires_0.3.6_revalidation"
    repair = database.one(
        "SELECT detail_json FROM diagnostic_event WHERE event_type = 'false_material_update_repair'"
    )
    assert json.loads(repair["detail_json"])["repaired_story_ids"] == [
        "story-debeeae2b4fa78a37fda"
    ]
    work = database.query(
        "SELECT id, status, last_error_class FROM work_item ORDER BY id"
    )
    assert [row["status"] for row in work] == [
        "needs_reapproval",
        "needs_reapproval",
        "needs_reapproval",
        "needs_reapproval",
        "needs_reapproval",
        "pending",
        "completed",
        "cancelled",
        "needs_reapproval",
    ]
    assert all(
        row["last_error_class"] == "legacy_approval_snapshot_incomplete"
        for row in work[:5]
    )
    assert all(row["last_error_class"] is None for row in work[5:])
    quarantine = database.one(
        "SELECT detail_json FROM diagnostic_event WHERE event_type = 'legacy_draft_snapshot_quarantine'"
    )
    detail = json.loads(quarantine["detail_json"])
    assert detail == {
        "count": 5,
        "error_class": "legacy_approval_snapshot_incomplete",
        "truncated_count": 0,
        "work_item_ids": [1, 2, 3, 4, 5],
    }
    action = database.one(
        """
        SELECT story_revision, claim_signatures_json, source_signatures_json
        FROM review_action WHERE id = ?
        """,
        (action_id,),
    )
    assert action["story_revision"] == 1
    assert json.loads(action["claim_signatures_json"]) == exact_payload["claim_signatures"]
    assert json.loads(action["source_signatures_json"]) == exact_payload["source_signatures"]


def test_storage_pressure_levels_can_be_forced(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    free = database.storage_pressure().free_bytes

    assert database.storage_pressure(warning_bytes=free + 2, critical_bytes=free + 1).level == "critical"
    assert database.storage_pressure(warning_bytes=free + 1, critical_bytes=0).level == "warning"
    assert database.storage_pressure(warning_bytes=0, critical_bytes=0).level == "normal"


def test_initialize_repairs_orphaned_evidence_without_deleting_history(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    now = "2026-07-23T16:00:00Z"
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO story_cluster(
                id, slug, headline, summary, lane, openness_class, status,
                priority, priority_score, freshness, first_public_at, detected_at,
                material_update, created_at, updated_at
            ) VALUES('story-repair', 'story-repair', 'AI report', 'Summary',
                     'Broader AI News', 'not stated', 'signal', 'Standard', 40,
                     'Fresh', ?, ?, 0, ?, ?)
            """,
            (now, now, now, now),
        )
        fetched_id = int(
            connection.execute(
                """
                INSERT INTO evidence_source(
                    story_id, requested_url, final_url, canonical_url, publisher_key,
                    acquisition_method, status, created_at, updated_at
                ) VALUES('story-repair', 'https://example.com/report?utm_source=x',
                         'https://example.com/report', 'https://example.com/report',
                         'example.com', 'discovery_enrichment', 'fetched', ?, ?)
                """,
                (now, now),
            ).lastrowid
        )
        orphan_id = int(
            connection.execute(
                """
                INSERT INTO evidence_source(
                    story_id, requested_url, canonical_url, publisher_key,
                    acquisition_method, status, created_at, updated_at
                ) VALUES('story-repair', 'https://example.com/report?utm_source=x',
                         'https://example.com/report?utm_source=x', 'example.com',
                         'discovery_enrichment', 'queued', ?, ?)
                """,
                (now, now),
            ).lastrowid
        )
        connection.execute(
            "DELETE FROM app_state WHERE key = 'repair_0_3_7_orphaned_evidence'"
        )

    database.initialize()

    repaired = database.one(
        "SELECT status, error_class, confirmation_reason FROM evidence_source WHERE id = ?",
        (orphan_id,),
    )
    assert repaired == {
        "status": "excluded",
        "error_class": "superseded_duplicate",
        "confirmation_reason": f"Superseded by preserved evidence proposal {fetched_id}.",
    }
    assert database.one(
        "SELECT status FROM evidence_source WHERE id = ?", (fetched_id,)
    ) == {"status": "fetched"}
    assert database.one(
        "SELECT COUNT(*) AS count FROM work_item WHERE kind = 'evidence_enrichment'"
    ) == {"count": 0}
    diagnostic = database.one(
        "SELECT detail_json FROM diagnostic_event WHERE event_type = 'orphaned_evidence_repair'"
    )
    assert json.loads(diagnostic["detail_json"])["superseded"] == [
        {"evidence_source_id": orphan_id, "superseded_by": fetched_id}
    ]
