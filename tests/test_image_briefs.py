import pytest
from open_source_ai_news_wire.web import create_app
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.images import ImageBriefs, layout_preview

@pytest.fixture
def app(tmp_path):
    app = create_app(data_root=tmp_path/'data', auth_required=False, test_config={'TESTING': True, 'SECRET_KEY': 'test'})
    seed_demo_data(app.config['DATABASE'])
    return app


def prepare(app):
    return ImageBriefs(app.config['DATABASE']).prepare('story-demo-runtime-001', 'A factual headline', 'AI NEWS', 'SENTIENT')


def test_approval_is_specific_explicit_and_blocked(app):
    store = ImageBriefs(app.config['DATABASE'])
    first, second = prepare(app), prepare(app)
    with pytest.raises(ValueError):
        store.approve(first['id'], None)
    approved = store.approve(first['id'], 'yes')
    assert approved['status'] == 'blocked_provider_unavailable'
    assert approved['generated_asset'] is None
    assert store.get(second['id'])['status'] == 'awaiting_approval'
    assert store.approve(first['id'], 'yes')['approved_at'] == approved['approved_at']


def test_story_change_and_archive_invalidate_approval(app):
    store = ImageBriefs(app.config['DATABASE'])
    brief = prepare(app)
    app.config['DATABASE'].execute("UPDATE story_cluster SET headline='changed' WHERE id=?", (brief['story_id'],))
    with pytest.raises(ValueError, match='Story changed'):
        store.approve(brief['id'], 'yes')
    app.config['DATABASE'].execute("UPDATE story_cluster SET status='archived' WHERE id=?", (brief['story_id'],))
    with pytest.raises(ValueError, match='Archived'):
        prepare(app)


def test_routes_require_csrf_and_preview_escapes(app):
    client = app.test_client()
    brief = prepare(app)
    assert client.post('/images/'+brief['id']+'/approve', data={'confirmed': 'yes'}).status_code == 400
    client.get('/')
    with client.session_transaction() as session:
        token = session['csrf_token']
    assert client.get('/images/'+brief['id']).status_code == 200
    assert client.post('/images/'+brief['id']+'/approve', data={'confirmed': 'yes', 'csrf_token': token}).status_code == 410
    assert ImageBriefs(app.config['DATABASE']).get(brief['id'])['status'] == 'awaiting_approval'
    brief['headline'] = '<script>alert(1)</script>'
    preview = layout_preview(brief)
    assert '<script>' not in preview
    assert '&lt;script&gt;' in preview
    assert 'placeholder background' in preview


def test_image_entry_preserves_text_flow(app):
    page = app.test_client().get('/stories/story-demo-runtime-001', follow_redirects=True)
    assert b'Generate content' in page.data
    assert b'Generate graphics' in page.data
    assert b'Prepare image brief' not in page.data
