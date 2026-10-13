import io
import json

import pytest

from open_source_ai_news_wire.appserver import AppServerError, StdioAppServer


class Process:
    def __init__(self, events):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(''.join(json.dumps(event)+'\n' for event in events))
        self.stopped = False

    def terminate(self):
        self.stopped = True

    def wait(self, timeout=3):
        return 0


def test_initialization_request_correlation_and_server_requests_preserved(monkeypatch, tmp_path):
    monkeypatch.setattr('open_source_ai_news_wire.appserver.codex_executable', lambda: '/test/codex')
    process = Process([{'id':1,'result':{}}, {'id':88,'method':'tool/requestUserInput','params':{'questions':[]}},
                       {'id':2,'result':{'data':[]}}])
    server = StdioAppServer(process_factory=lambda *a,**k:process, state_root=tmp_path)
    assert server.request('model/list', {}) == {'data':[]}
    assert server.next_event()['id'] == 88
    server.respond(88, {'answers':{}})
    requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert requests[0]['method'] == 'initialize'
    assert requests[1]['method'] == 'initialized'
    assert requests[-1] == {'id':88,'result':{'answers':{}}}
    server.close(); assert process.stopped


def test_plan_provider_token_only_environment_and_no_paid_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr('open_source_ai_news_wire.appserver.codex_executable', lambda: '/test/codex')
    monkeypatch.setenv('OPENAI_API_KEY', 'obvious-test-paid-key')
    captured = {}
    def factory(args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return Process([{'id':1,'result':{}}])
    server = StdioAppServer(access_token='obvious-plan-token', state_root=tmp_path, process_factory=factory)
    assert 'OPENAI_API_KEY' not in captured['kwargs']['env']
    assert captured['kwargs']['env']['NEWSWIRE_CHATGPT_ACCESS_TOKEN'] == 'obvious-plan-token'
    assert not any('obvious-plan-token' in item for item in captured['args'])
    assert 'model_providers.newswire_chatgpt_plan.base_url="https://api.openai.com/v1"' in captured['args']
    assert 'image_generation' in captured['args']
    server.close()


def test_rpc_errors_do_not_expose_backend_paths_or_tokens(monkeypatch):
    monkeypatch.setattr('open_source_ai_news_wire.appserver.codex_executable', lambda: '/test/codex')
    process = Process([{'id':1,'error':{'code':400,'message':'secret /Users/owner/token-file'}}])
    with pytest.raises(AppServerError) as error:
        StdioAppServer(process_factory=lambda *a,**k:process)
    assert 'secret' not in str(error.value)
    assert '/Users' not in str(error.value)
    assert process.stopped
