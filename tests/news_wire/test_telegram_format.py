import html
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from open_source_ai_news_wire.adapters import Observation
from open_source_ai_news_wire.collector import Collector
from open_source_ai_news_wire.network import FetchResult
from open_source_ai_news_wire.telegram_format import build_prompt, buttons, copy_prompt_button, message_content, page_excerpt, source_excerpt, valid_source


@pytest.mark.parametrize('text', [
    'HuggingNews discovery topics: AI, Models.',
    'Article URL: https://example.com/story Comments URL: https://news.ycombinator.com/item?id=1 Points: 2.',
    'We’re on a journey to advance and democratize artificial intelligence through open source and open science.',
    'Publisher published “Open model release.” Only limited context was available from the monitored item.',
    'An incomplete sentence', 'A truncated description…', 'A truncated description….',
    'x' * 651 + '.',
])
def test_non_article_and_incomplete_excerpts_are_omitted(text):
    assert source_excerpt(text) == ''


def test_excerpt_preserves_complete_source_sentences_and_bounds():
    assert source_excerpt('<p>Complete first sentence.</p> Incomplete tail…') == 'Complete first sentence.'
    assert source_excerpt('One. Two. Three. Four.') == 'One. Two. Three.'
    assert source_excerpt('One. ' + 'x' * 650 + '.') == 'One.'
    assert source_excerpt('Context. We propose a new model. It has open weights. Further details.') == 'We propose a new model. It has open weights. Further details.'
    assert source_excerpt('Publisher reports: A new model was released. This context comes from the monitored publisher excerpt.', publisher='Publisher') == 'A new model was released.'


@pytest.mark.parametrize('markup', [
    '<meta name="description" content="Generic"><meta property="og:description" content="The project released a new model.">',
    '<meta name="description" content="The project released a new model.">',
    '<script type="application/ld+json">{"@graph":[{"@type":"NewsArticle","description":"The project released a new model."}]}</script>',
    '<script type="application/ld+json">invalid</script><meta name="twitter:description" content="The project released a new model.">',
])
def test_metadata_fallback_selects_article_description(markup):
    assert page_excerpt(markup.encode()) == 'The project released a new model.'


def test_site_description_is_not_substituted_for_an_article():
    assert page_excerpt(b'<script type="application/ld+json">{"@type":"WebSite","description":"Generic site slogan."}</script>') == ''


def test_explicit_underlying_link_and_label_replace_discovery_context():
    event = {'title':'Model launch', 'publisher':'Mastodon AI Tag','canonical_url':'https://mastodon.social/@feed/1',
             'context':'Mastodon AI Tag reports: A new model was released. 📰 Source: The Publisher 🔗 Link: https://example.com/story # AI', 'source_type':'discovery'}
    assert message_content(event) == ('Model launch','The Publisher','https://example.com/story','A new model was released.')
    event['context'] = 'Article URL: http://127.0.0.1/secret Comments URL: https://example.com/comment'
    assert message_content(event)[2] == event['canonical_url']


@pytest.mark.parametrize('url', ['https://user:pass@example.com/a','http://127.0.0.1/a','https://localhost/a','https://host.local/a','https://example.com:444/a','javascript:alert(1)','https://[bad/a'])
def test_unsafe_source_links_are_rejected(url):
    assert not valid_source(url)


@pytest.mark.parametrize('length', [20, 160, 210, 236, 260, 900])
def test_single_copy_link_button_preserves_complete_urls(length):
    url = 'https://example.com/' + 'a' * length + '?x=1&y=2'
    row = buttons(url, 'Headline')['inline_keyboard'][0]
    assert len(row) == 3
    for button in row:
        if 'copy_text' in button:
            assert 1 <= len(button['copy_text']['text']) <= 256
    if len(url) <= 256:
        assert row[0]['copy_text']['text'] == url
    else:
        assert row[0] == {'text':'Open source','url':url}


def test_shared_prompt_exact_template_and_missing_description():
    expected = '''Use the skill selected below to create a draft about this news item.

Title: 模型 🤖 café & agents
Description: Source-provided description.
Source: https://example.com/?x=1&y=2

Skill: [CHOOSE AN INSTALLED SKILL]'''
    assert build_prompt('模型 🤖 café & agents','Source-provided description.','https://example.com/?x=1&y=2') == expected
    missing = build_prompt('Title','','https://example.com/')
    assert 'Description:' not in missing
    assert 'Skill: [CHOOSE AN INSTALLED SKILL]' in missing
    assert missing.endswith('Skill: [CHOOSE AN INSTALLED SKILL]')
    assert 'If no skill' not in missing and 'Do not publish' not in missing


def test_approved_wordpress_example_copies_directly_without_browser():
    title = 'Show HN: WP Replai – AI chatbot built right into WordPress'
    source = 'https://wpreplai.com/'
    expected = ('Use the skill selected below to create a draft about this news item.\n\n'
                f'Title: {title}\nSource: {source}\n\nSkill: [CHOOSE AN INSTALLED SKILL]')
    assert buttons(source,title)['inline_keyboard'][0][1] == {
        'text':'Copy prompt','copy_text':{'text':expected}}


@pytest.mark.parametrize('length',[1,255,256,257,30000])
def test_copy_prompt_boundaries_never_truncate_or_normalize(length):
    prompt = ('é\n🤖'*10000)[:length]
    button = copy_prompt_button(prompt)
    assert button['text']=='Copy prompt'
    if length<=256:
        assert button['copy_text']['text']==prompt
    else:
        assert parse_qs(urlsplit(button['url']).fragment)['prompt']==[prompt]


@pytest.mark.parametrize('repeat',[1,4])
def test_three_actions_share_one_row_and_preserve_full_prompt_and_google_search(repeat):
    title = '模型 🤖 "Café" & agents + tools'
    description = 'Source text with Unicode: नमस्ते.\nAnother line.' * repeat
    source = 'https://example.com/article?one=1&two=é'
    keyboard=buttons(source,title,description)['inline_keyboard']
    assert [[button['text'] for button in row] for row in keyboard] == [
        ['Copy link','Copy prompt','Search on Google']]
    assert keyboard[0][0]['copy_text']['text']==source
    expected=build_prompt(title,description,source)
    if len(expected)<=256:
        assert keyboard[0][1]['copy_text']['text']==expected
    else:
        url=urlsplit(keyboard[0][1]['url'])
        assert url.scheme=='https' and not url.query
        assert parse_qs(url.fragment)=={'v':['1'],'action':['copy'],'prompt':[expected]}
    search=urlsplit(keyboard[0][2]['url'])
    assert search.scheme=='https' and search.netloc=='www.google.com' and search.path=='/search'
    assert parse_qs(search.query)=={'q':[title]}
    assert 'Source text' not in keyboard[0][2]['url']


def test_google_search_removes_display_markup_and_only_normalizes_whitespace():
    url=buttons('https://example.com/',' <b>New model</b>\n\tadds reasoning & tools ')['inline_keyboard'][0][2]['url']
    assert parse_qs(urlsplit(url).query)=={'q':['New model adds reasoning & tools']}


def test_formatter_escapes_untrusted_html_and_uses_one_source(tmp_path):
    from open_source_ai_news_wire.config import resolve_runtime_paths
    from open_source_ai_news_wire.storage import Database
    from open_source_ai_news_wire.telegram import TelegramDispatcher
    db = Database(resolve_runtime_paths(tmp_path/'wire'));db.initialize()
    event = {'title':'<Model> & "agents" 🤖','publisher':'Publisher & Co','canonical_url':'https://example.com/a?x=1&y=2',
             'context':'Publisher & Co reports: <p>Weights &amp; code were released.</p> This context comes from the monitored publisher excerpt.'}
    text = TelegramDispatcher(db,token_provider=lambda:'unused')._message(event)
    assert '<b>&lt;Model&gt; &amp; &quot;agents&quot; 🤖</b>' in text
    assert 'Weights &amp; code were released.' in text
    assert text.count('https://example.com/') == 1
    assert 'reports:' not in text and 'This context' not in text
    assert 'Fetched:' not in text and 'Published:' not in text


@pytest.mark.parametrize('mode', ['metadata','blocked','deadline','feed'])
def test_collection_fallback_keeps_source_data_and_does_not_require_inference(mode):
    calls = []
    class Client:
        def fetch(self,url,**kwargs):
            calls.append(url)
            if mode == 'blocked':
                raise RuntimeError('blocked')
            return FetchResult(url,200,{'content-type':'text/html'},b'<meta property="og:description" content="The project released a new model.">')
    source = {'name':'Publisher','family':'Official AI organizations','monitoring_role':'Event'}
    item = Observation('one','Open-source AI model released','https://example.com/a','2026-10-16T00:00:00Z',
                       'Existing source sentence.' if mode=='feed' else '')
    collector = object.__new__(Collector);collector.now=lambda:'2026-10-16T00:00:01Z'
    deadline = datetime.now(UTC)-timedelta(seconds=1) if mode=='deadline' else None
    result = collector._hydrate_notification_context(Client(),source,[item],deadline=deadline)[0]
    assert result.summary == item.summary and result.fingerprint == item.fingerprint
    if mode=='metadata':
        assert result.metadata['notification_context']=='The project released a new model.'
    elif mode in {'blocked','deadline'}:
        assert result.metadata['notification_provenance']=='limited_context'
    if mode in {'deadline','feed'}:
        assert calls == []
