from __future__ import annotations

import json
from pathlib import Path

import pytest

from open_source_ai_news_wire.adapters import Observation
from open_source_ai_news_wire.collector import Collector
from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.qualification import classify
from open_source_ai_news_wire.services import DashboardService
from open_source_ai_news_wire.source_registry import synchronize_sources
from open_source_ai_news_wire.storage import Database, article_id_for_url, backfill_fetched_articles

NOW = '2026-10-13T12:00:00Z'
LATER = '2026-10-13T13:00:00Z'


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(resolve_runtime_paths(tmp_path / 'wire'))
    db.initialize()
    synchronize_sources(db)
    return db


def source(db: Database) -> dict:
    return db.one("SELECT * FROM source_registry WHERE id='hacker-news-ai'")


def item(url: str, *, title: str = 'Open-source AI model releases weights',
         published: str = '2026-01-01T00:00:00Z', metadata: dict | None = None) -> Observation:
    return Observation(url, title, url, published, 'A fetched publisher excerpt.', 'en', metadata or {})


def persist(db: Database, observation: Observation, fetched: str = NOW) -> str:
    origin = source(db)
    with db.transaction() as connection:
        return Collector(db)._persist_story(connection, origin, observation,
            classify(observation, origin, observed_at=fetched), fetched, 1, 'scheduled')


def test_separate_articles_preserve_old_and_undated_sources_without_grades(database: Database) -> None:
    first = item('https://example.com/a')
    second = item('https://another.example/a', metadata={'timestamp_status': 'missing_source_fallback_detection'})
    persist(database, first)
    persist(database, second, LATER)
    feed = DashboardService(database).list_articles()
    assert feed['total'] == 2
    assert [article['canonical_url'] for article in feed['articles']] == [second.url, first.url]
    assert feed['articles'][0]['published_at'] is None
    assert feed['articles'][1]['published_at'] == first.published_at
    assert all(article['status'] == 'fetched' for article in feed['articles'])
    assert all('review_score' not in article and 'trust_label' not in article for article in feed['articles'])
    assert database.query('SELECT * FROM candidate') == []
    assert database.query("SELECT * FROM work_item WHERE kind='source_research'") == []
    story = database.one('SELECT * FROM story_cluster LIMIT 1')
    assert story['priority_floor_applied'] == 0
    assert story['importance_score'] == 0


def test_repeated_fetch_updates_excerpt_without_reordering_or_resetting_choice(database: Database) -> None:
    original = item('https://example.com/a?utm_source=x')
    story_id = persist(database, original)
    persist(database, item('https://example.com/b'), LATER)
    article_id = article_id_for_url('https://example.com/a')
    service = DashboardService(database)
    service.set_article_status(article_id, 'archived')
    persist(database, item('https://example.com/a', title='Open-source AI model releases revised weights'), '2026-10-14T12:00:00Z')
    article = service.get_article(article_id)
    assert article['first_fetched_at'] == NOW
    assert article['last_fetched_at'] == '2026-10-14T12:00:00Z'
    assert article['status'] == 'archived'
    assert 'revised' in article['title']
    assert service.list_articles()['articles'][0]['canonical_url'] == 'https://example.com/b'
    assert database.one('SELECT COUNT(*) AS n FROM fetched_article')['n'] == 2


def test_source_revision_preserves_existing_draft_and_pending_work(database: Database) -> None:
    story_id = persist(database, item('https://example.com/a'))
    database.execute("INSERT INTO draft(story_id,mode,version,status,headline,metadata,body,lens,created_at,updated_at) "
                     "VALUES(?,'Reddit Post',1,'Current','Human title','','Human draft','',?,?)", (story_id,NOW,NOW))
    database.execute("INSERT INTO work_item(kind,story_id,status,priority,payload_json,created_at,updated_at) "
                     "VALUES('draft',?,'queued',0,'{}',?,?)", (story_id,NOW,NOW))
    persist(database, item('https://example.com/a', title='Open-source AI model changes its license'), LATER)
    assert database.one('SELECT status,body FROM draft') == {'status':'Current','body':'Human draft'}
    assert database.one("SELECT status FROM work_item WHERE kind='draft'")['status'] == 'queued'
    assert not database.query("SELECT * FROM alert WHERE kind='correction'")


@pytest.mark.parametrize('timestamp_status', ['unknown_original', 'missing_source_fallback_detection'])
def test_unknown_refetch_preserves_known_publication_date(database: Database, timestamp_status: str) -> None:
    original = item('https://example.com/dated')
    persist(database, original)
    persist(database, item(original.url, published=LATER,
        metadata={'timestamp_status':timestamp_status}), LATER)
    article = DashboardService(database).get_article(article_id_for_url(original.url))
    assert article['published_at'] == original.published_at
    assert article['first_fetched_at'] == NOW and article['last_fetched_at'] == LATER


def test_backfill_replaces_legacy_opportunity_lane_with_topic_without_excluding_history(database: Database) -> None:
    observation = item('https://example.com/regulation', title='AI privacy regulator announces enforcement')
    story_id = persist(database, observation)
    article_id = article_id_for_url(observation.url)
    database.execute("UPDATE story_cluster SET lane='Open-Source Lens Opportunity' WHERE id=?", (story_id,))
    database.execute("UPDATE fetched_article SET lane='Open-Source Lens Opportunity',status='archived' WHERE id=?", (article_id,))
    with database.transaction() as connection:
        backfill_fetched_articles(connection)
    article = DashboardService(database).get_article(article_id)
    assert article['lane'] == 'Broader AI News'
    assert article['status'] == 'archived'
    assert article['first_fetched_at'] == NOW
    with database.transaction() as connection:
        connection.execute('DELETE FROM fetched_article WHERE id=?', (article_id,))
        backfill_fetched_articles(connection)
    assert DashboardService(database).get_article(article_id)['lane'] == 'Broader AI News'


def test_pagination_search_lane_and_operational_read_scope(database: Database) -> None:
    for index in range(3):
        persist(database, item(f'https://example.com/{index}', title=f'Open-source AI model {index}'))
    service = DashboardService(database)
    page = service.list_articles(page_size=2)
    second = service.list_articles(page_size=2, cursor=page['next_cursor'])
    assert page['total'] == 3 and len(second['articles']) == 1
    assert not {row['id'] for row in page['articles']} & {row['id'] for row in second['articles']}
    assert len(service.list_articles(page_size=None)['articles']) == 3
    assert service.list_articles(search='model 1')['total'] == 1
    assert service.list_articles(lane='AGI Development')['total'] == 0
    for kind in ['health','correction','catch_up','recovery','candidate','ready']:
        database.execute("INSERT INTO alert(kind,severity,title,body,created_at) VALUES(?,'standard','Title','Body',?)",(kind,NOW))
    assert service.operational_unread_count() == 4
    assert service.mark_operational_notices_read() == 4
    assert service.operational_unread_count() == 0
    assert database.one('SELECT COUNT(*) AS n FROM alert WHERE read_at IS NULL')['n'] == 2
    overview = service.article_overview()
    assert overview['counts'] == {'total':3,'fetched':3,'generated':0,'operational_unread':0}


def test_migration_recovers_raw_items_without_notifications_or_generation(database: Database) -> None:
    origin = source(database)
    legacy = item('https://example.com/existing')
    story_id = persist(database, legacy)
    database.execute("UPDATE story_cluster SET status='withdrawn' WHERE id=?",(story_id,))
    for name, title in [('relevant','Open-source AI model raw observation'),('irrelevant','Office renovations begin')]:
        database.execute("INSERT INTO raw_observation(source_id,external_id,canonical_url,title,published_at,observed_at,"
            "fingerprint,metadata_json,timestamp_status) VALUES(?,?,?,?,?,?,?,?,?)",
            (origin['id'],name,f'https://example.com/{name}',title,NOW,NOW,name,json.dumps({'summary':'Fetched source text'}),'valid'))
    before_events = database.query('SELECT * FROM relevance_notification_event')
    with database.transaction() as connection:
        connection.execute('DROP TABLE generation_job')
        connection.execute('DROP TABLE fetched_article')
        connection.execute('DROP INDEX idx_source_revision_raw_observed')
        connection.execute("UPDATE meta SET value='10' WHERE key='schema_version'")
    assert database.migrate() == 11
    rows = DashboardService(database).list_articles(page_size=None)['articles']
    assert {row['canonical_url'] for row in rows} == {legacy.url,'https://example.com/relevant'}
    assert next(row for row in rows if row['canonical_url']==legacy.url)['status'] == 'withdrawn'
    assert database.query('SELECT * FROM generation_job') == []
    assert database.query('SELECT * FROM relevance_notification_event') == before_events
    # Earliest-revision lookups run once per historical raw observation. They
    # must seek a covering index rather than rescan the full revision ledger.
    query_plan = database.query('EXPLAIN QUERY PLAN SELECT MIN(observed_at) FROM source_revision WHERE raw_observation_id=?', (1,))
    assert any('COVERING INDEX idx_source_revision_raw_observed' in row['detail'] for row in query_plan)
    assert not any('SCAN source_revision' in row['detail'] for row in query_plan)
    assert database.integrity_check() == 'ok'


def test_only_relevance_classification_is_retained() -> None:
    origin = {'family':'Aggregation','monitoring_role':'Discovery'}
    assert classify(item('https://example.com/a'),origin,observed_at=NOW).relevant
    assert not classify(item('https://example.com/b',title='Office renovations begin'),origin,observed_at=NOW).relevant
    classified = classify(item('https://example.com/c'),origin,observed_at=NOW)
    assert classified.score == classified.importance_score == classified.impact_score == 0
    assert not classified.importance_gate and not classified.priority
