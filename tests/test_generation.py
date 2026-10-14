import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.generation import GenerationError, GenerationService
from open_source_ai_news_wire.storage import Database


class Auth:
    def status(self):
        return {"ready": True, "message": "Connected"}

    def access_token(self):
        return "obvious-test-token"


class Transport:
    def __init__(self, skill_path, events=None, **kwargs):
        self.skill_path = skill_path
        self.events = events or []
        self.requests = []
        self.answers = []
        self.closed = False
        self.kwargs = kwargs

    def request(self, method, params):
        self.requests.append((method, params))
        if method == "model/list":
            return {"data": [{"model": "test-model", "displayName": "Test Model", "isDefault": True}]}
        if method == "skills/list":
            return {"data": [{"skills": [{"name": "test-skill", "description": "Test content skill", "path": str(self.skill_path), "enabled": True}]}]}
        if method == "thread/start":
            return {"thread": {"id": "test-thread"}}
        if method == "turn/start":
            return {"turn": {"id": "test-turn"}}
        raise AssertionError(method)

    def next_event(self, timeout=1):
        if not self.events:
            raise AssertionError("A test turn exhausted its events")
        return self.events.pop(0)

    def respond(self, request_id, result):
        self.answers.append((request_id, result))

    def close(self):
        self.closed = True


def finished(text="Draft with source attribution."):
    return [{"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": text}}},
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}]


@pytest.fixture
def setup(tmp_path):
    database = Database(resolve_runtime_paths(tmp_path / "runtime")); database.initialize()
    database.execute("""INSERT INTO fetched_article(id,canonical_url,title,summary,publisher,first_fetched_at,last_fetched_at,status)
        VALUES('article-test','https://example.com/news','Title','Original source excerpt','Publisher','2026-10-13','2026-10-13','fetched')""")
    skill = tmp_path / "skill" / "SKILL.md"; skill.parent.mkdir(); skill.write_text("Test skill")
    created = []
    def factory(**kwargs):
        item = Transport(skill, finished(), **kwargs); created.append(item); return item
    service = GenerationService(database, transport_factory=factory, auth=Auth())
    service._refresh_catalog()
    skill_id = service.catalog()["skills"][0]["id"]
    yield service, database, created, skill_id
    for job in list(service.runtime.sessions):
        service.cancel(job)


def test_catalog_is_real_opaque_and_does_not_preselect_skills(setup):
    service, _, transports, skill_id = setup
    catalog = service.catalog()
    assert catalog["models"][0]["id"] == "test-model"
    assert catalog["default_model"] == "test-model"
    assert catalog["preferences"]["text"]["skill_ids"] == []
    assert catalog["preferences"]["graphics"]["skill_ids"] == []
    assert "/" not in skill_id and "path" not in json.dumps(catalog)
    assert not any(method in {"thread/start", "turn/start"} for method, _ in transports[0].requests)


def test_no_editorial_gate_duplicate_clicks_and_source_snapshot(setup):
    service, db, _, skill_id = setup
    db.execute("UPDATE fetched_article SET status='archived' WHERE id='article-test'")
    first = service.enqueue("article-test", "text", "test-model", [skill_id], "Short", "request-1")
    duplicate = service.enqueue("article-test", "text", "test-model", [skill_id], "Short", "request-2")
    assert first["id"] == duplicate["id"]
    with pytest.raises(GenerationError, match="identifier"):
        service.enqueue("article-test", "graphics", "test-model", [skill_id], "Short", "request-1")
    db.execute("UPDATE fetched_article SET summary='Later excerpt' WHERE id='article-test'")
    assert service.run_pending() == 1
    result = service.get_job(first["id"])
    assert result["status"] == "completed"
    assert result["output_text"] == "Draft with source attribution."
    snapshot = json.loads(db.one("SELECT source_snapshot_json FROM generation_job WHERE id=?", (first["id"],))["source_snapshot_json"])
    assert snapshot["summary"] == "Original source excerpt"


def test_selected_skills_model_and_sandbox_reach_protocol(setup):
    service, db, transports, skill_id = setup
    job = service.enqueue("article-test", "text", "test-model", [skill_id], "No conclusion.")
    service.run_pending()
    transport = transports[-1]
    turn = next(params for method, params in transport.requests if method == "turn/start")
    assert turn["model"] == "test-model"
    assert turn["input"][1]["type"] == "skill"
    assert turn["input"][1]["name"] == "test-skill"
    assert "No conclusion." in turn["input"][0]["text"]
    assert turn['permissions'] == 'newswire_job'
    assert 'sandboxPolicy' not in turn
    assert transport.kwargs["access_token"] == "obvious-test-token"
    assert transport.closed
    thread = next(params for method, params in transport.requests if method == 'thread/start')
    assert thread['dynamicTools'][0]['name'] == 'newswire_request_input'
    assert thread['dynamicTools'][0]['type'] == 'function'
    assert thread['allowProviderModelFallback'] is False
    assert thread['permissions'] == 'newswire_job'
    assert 'sandbox' not in thread
    assert thread['approvalsReviewer'] == 'user'
    filesystem = thread['config']['permissions.newswire_job']['filesystem']
    assert filesystem[':root'] == 'deny'
    assert filesystem[':minimal'] == 'read'
    assert filesystem[str(db.paths.config)] == 'deny'
    assert filesystem[str(db.paths.database)] == 'deny'
    assert filesystem[str(service.root / job['id'])] == 'write'
    assert [path for path, permission in filesystem.items() if permission == 'write'] == [str(service.root / job['id'])]
    assert thread['config']['permissions.newswire_job']['network']['enabled'] is True


def test_question_pauses_and_resumes_same_turn(setup):
    service, _, transports, skill_id = setup
    question = {"id": 30, "method": "tool/requestUserInput", "params": {"questions": [{"id": "tone", "question": "Which tone?"}]}}
    def factory(**kwargs):
        transport = Transport(transports[0].skill_path, [question] + finished("After human input"), **kwargs)
        transports.append(transport); return transport
    service.transport_factory = factory
    job = service.enqueue("article-test", "text", "test-model", [skill_id])
    service.run_pending()
    assert service.get_job(job["id"])["status"] == "needs_input"
    assert not transports[-1].closed
    with pytest.raises(GenerationError, match="each"):
        service.respond(job["id"], {})
    service.respond(job["id"], {"tone": "Neutral"})
    service.run_pending()
    assert service.get_job(job["id"])["status"] == "completed"
    assert transports[-1].answers == [(30, {"answers": {"tone": {"answers": ["Neutral"]}}})]
    assert sum(method == "turn/start" for method, _ in transports[-1].requests) == 1


def test_cancel_retry_and_offline_manual_versions(setup):
    service, db, _, skill_id = setup
    job = service.enqueue("article-test", "text", "test-model", [skill_id])
    assert service.cancel(job["id"])["status"] == "cancelled"
    clone = service.retry(job["id"], request_id="retry-idempotent")
    assert service.retry(job["id"], request_id="retry-idempotent")["id"] == clone["id"]
    service.run_pending()
    db.set_state("generation_catalog", "{}", "2026-10-13")
    edited = service.save_text(clone["id"], "Human edited content")
    assert edited["id"] != clone["id"] and edited["status"] == "completed"
    assert service.get_job(clone["id"])["output_text"] != edited["output_text"]


def test_graphics_without_artifacts_fails_and_artifacts_cannot_escape(setup, tmp_path):
    service, db, _, skill_id = setup
    job = service.enqueue("article-test", "graphics", "test-model", [skill_id])
    service.run_pending()
    assert service.get_job(job["id"])["status"] == "failed"
    assert "no downloadable graphic" in service.get_job(job["id"])["error"]
    output = service.root / job["id"]
    (output / "valid.png").write_bytes(b"test-png")
    outside = tmp_path / "private.txt"; outside.write_text("not an artifact")
    (output / "escape.txt").symlink_to(outside)
    service._persist_outputs(job["id"], {"output": output, "text": "graphics"})
    artifacts = service.get_job(job["id"])["artifacts"]
    assert [a["name"] for a in artifacts] == ["valid.png"]
    assert "relative_path" not in artifacts[0]
    assert service.artifact_path(job["id"], artifacts[0]["id"]) == output / "valid.png"
    (output / "valid.png").unlink(); (output / "valid.png").symlink_to(outside)
    with pytest.raises(LookupError):
        service.artifact_path(job["id"], artifacts[0]["id"])


def test_crashed_worker_is_failed_without_model_replay(setup):
    service, db, transports, skill_id = setup
    job = service.enqueue("article-test", "text", "test-model", [skill_id])
    db.execute("UPDATE generation_job SET status='running',lease_owner='999999999:dead',lease_expires_at='2000-01-01' WHERE id=?", (job["id"],))
    before = len(transports)
    service.run_pending()
    assert service.get_job(job["id"])["status"] == "failed"
    assert len(transports) == before


def test_another_process_can_answer_without_stealing_the_rpc_session(setup):
    service, db, transports, skill_id = setup
    question = {"id": 88, "method": "tool/requestUserInput", "params": {"questions": [{"id": "audience", "question": "Audience?"}]}}
    def factory(**kwargs):
        item = Transport(transports[0].skill_path, [question] + finished("Resumed by the original owner"), **kwargs)
        transports.append(item); return item
    service.transport_factory = factory
    job = service.enqueue("article-test", "text", "test-model", [skill_id])
    service.run_pending()
    assert service.get_job(job["id"])["status"] == "needs_input"
    script = """import sys
from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.storage import Database
from open_source_ai_news_wire.generation import GenerationService
service=GenerationService(Database(resolve_runtime_paths(sys.argv[1])))
service.respond(sys.argv[2], {'audience':'Community'})
assert service.run_pending(limit=1)==0
"""
    result = subprocess.run([sys.executable, '-c', script, str(db.paths.root), job['id']], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and service.get_job(job['id'])['status'] != 'completed':
        time.sleep(.05)
    assert service.get_job(job['id'])['output_text'] == 'Resumed by the original owner'
    assert len([r for r in transports[-1].requests if r[0]=='turn/start']) == 1
    assert transports[-1].answers == [(88, {'answers': {'audience': {'answers': ['Community']}}})]


def test_default_mode_dynamic_input_tool_pauses_and_returns_actual_answer(setup):
    service, _, transports, skill_id = setup
    event = {'id':98,'method':'item/tool/call','params':{'tool':'newswire_request_input',
        'arguments':{'questions':[{'id':'slides','question':'How many slides?'}]}}}
    def factory(**kwargs):
        item = Transport(transports[0].skill_path, [event]+finished(), **kwargs)
        transports.append(item); return item
    service.transport_factory = factory
    job = service.enqueue('article-test','text','test-model',[skill_id])
    service.run_pending()
    assert service.get_job(job['id'])['status'] == 'needs_input'
    service.respond(job['id'], {'slides':'3'})
    service.run_pending()
    assert transports[-1].answers == [(98, {'contentItems':[{'type':'inputText','text':'{"slides": "3"}'}],'success':True})]


def test_safe_access_failure_and_rerouting_are_not_success(setup):
    service, _, transports, skill_id = setup
    def factory(**kwargs):
        item = Transport(transports[0].skill_path, [{'method':'turn/completed','params':{'turn':{'status':'failed',
            'error':{'codexErrorInfo':'UsageLimitExceeded','message':'private backend secret'}}}}], **kwargs)
        transports.append(item); return item
    service.transport_factory = factory
    job = service.enqueue('article-test','text','test-model',[skill_id])
    service.run_pending()
    error = service.get_job(job['id'])['error']
    assert 'usage limit' in error and 'test-model' in error and 'secret' not in error


@pytest.mark.parametrize("error_info", [
    "server_overloaded", "serverOverloaded",
    {"server_overloaded": {"httpStatusCode": 503}},
    {"serverOverloaded": {"httpStatusCode": 503}},
])
def test_capacity_failure_preserves_selected_model_and_partial_output_without_fallback(setup, error_info):
    service, db, transports, skill_id = setup
    def factory(**kwargs):
        item = Transport(transports[0].skill_path, [
            {"method": "item/agentMessage/delta", "params": {"delta": "Preserved partial draft."}},
            {"method": "turn/completed", "params": {"turn": {"status": "failed", "error": {
                "codexErrorInfo": error_info, "message": "Private backend message and secret-token",
            }}}},
        ], **kwargs)
        transports.append(item)
        return item
    service.transport_factory = factory
    job = service.enqueue("article-test", "text", "test-model", [skill_id], "Keep the requested angle.")
    assert service.run_pending() == 1
    result = service.get_job(job["id"])
    assert result["status"] == "failed"
    assert result["error"] == "Generation with test-model failed. The selected model is at capacity. Wait and retry, or explicitly select another model."
    assert result["model"] == "test-model"
    assert result["output_text"] == "Preserved partial draft."
    assert result["skill_ids"] == [skill_id]
    assert result["customizations"] == "Keep the requested angle."
    assert db.one("SELECT COUNT(*) AS n FROM generation_job")["n"] == 1
    generation_transports = [transport for transport in transports if any(method == "turn/start" for method, _ in transport.requests)]
    assert len(generation_transports) == 1
    transport = generation_transports[0]
    thread = next(params for method, params in transport.requests if method == "thread/start")
    turn = next(params for method, params in transport.requests if method == "turn/start")
    assert thread["model"] == turn["model"] == "test-model"
    assert thread["allowProviderModelFallback"] is False
    assert transport.closed
    assert service.run_pending() == 0


@pytest.mark.parametrize("allow", [False, True])
def test_permission_request_shows_exact_scope_and_requires_human_response(setup, allow):
    service, _, transports, skill_id = setup
    permissions = {"fileSystem": {"entries": [{"path": {"type": "path", "path": "/private/tmp/requested-output"}, "access": "write"}]}}
    event = {"id": 91, "method": "item/permissions/requestApproval", "params": {
        "threadId": "test-thread", "turnId": "test-turn", "itemId": "permission-1",
        "cwd": "/private/tmp/job", "reason": "Export the requested asset", "permissions": permissions, "startedAtMs": 1}}
    def factory(**kwargs):
        item = Transport(transports[0].skill_path, [event] + finished(), **kwargs)
        transports.append(item); return item
    service.transport_factory = factory
    job = service.enqueue("article-test", "text", "test-model", [skill_id])
    service.run_pending()
    paused = service.get_job(job["id"])
    assert paused["status"] == "needs_input"
    question = paused["question"]["questions"][0]["question"]
    assert "Export the requested asset" in question and "/private/tmp/requested-output" in question and "write" in question
    assert transports[-1].answers == []
    service.respond(job["id"], {"decision": "Allow once" if allow else "Decline"})
    service.run_pending()
    assert transports[-1].answers == [(91, {"permissions": permissions if allow else {}, "scope": "turn"})]
