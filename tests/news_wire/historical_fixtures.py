"""SQL fixtures for preserved pre-article drafting history, never live approvals."""
from open_source_ai_news_wire.evidence import qualification_state
from open_source_ai_news_wire.revisions import approval_signature_sets
from open_source_ai_news_wire.services import utc_now
from open_source_ai_news_wire.storage import Database


def seed_historical_draft_request(service, story_id, action='approve_neutral', reason='', confirmation_version=''):
    database = service.database
    story = database.one('SELECT * FROM story_cluster WHERE id=?', (story_id,))
    claims = database.query('SELECT id,text,status,volatility FROM claim WHERE story_id=? ORDER BY id', (story_id,))
    manual = action.startswith('manual_')
    sources = service._draft_sources(story_id, allow_discovery=manual)
    claim_signatures, source_signatures = approval_signature_sets(claims, sources)
    state = qualification_state(database, story_id)
    mode = 'Open-Source Lens Brief' if action.endswith('lens') else 'Neutral News Brief'
    timestamp = utc_now()
    gates = {'evidence_gate': bool(state['evidence_gate']),
             'importance_gate': bool(state['effective_importance']),
             'automated_importance': bool(state['automated_importance']),
             'effective_importance': bool(state['effective_importance']),
             'lens_gate': story.get('opportunity_strength') in {'Strong','Moderate'},
             'lens_eligible': story.get('opportunity_strength') in {'Strong','Moderate'},
             'candidate_basis': state['candidate_basis']}
    payload = {'schema_version':3,'story_id':story_id,'mode':mode,'reason':reason,
               'approval_basis':'manual_override' if manual else 'verified','manual_override':manual,
               'confirmation_version':confirmation_version,'qualification_snapshot':gates,
               'approval_snapshot_at':timestamp,'story_revision':int(story['story_revision'] or 1),
               'claim_signatures':claim_signatures,'source_signatures':source_signatures,
               'story':dict(story),'claims':claims,'sources':sources}
    with database.transaction() as connection:
        action_id = int(connection.execute(
            "INSERT INTO review_action(story_id,action,reason,draft_mode,created_at,story_revision,claim_signatures_json,source_signatures_json) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (story_id,action,reason,mode,timestamp,payload['story_revision'],Database.json(claim_signatures),Database.json(source_signatures))
        ).lastrowid)
        payload['review_action_id'] = action_id
        connection.execute('UPDATE review_action SET approval_snapshot_json=? WHERE id=?', (Database.json(payload),action_id))
        connection.execute("UPDATE story_cluster SET status='approved',updated_at=? WHERE id=?", (timestamp,story_id))
        if manual:
            connection.execute("UPDATE candidate SET manual_override=1,manual_override_at=?,manual_override_action_id=?,manual_override_snapshot_json=? WHERE story_id=?",
                               (timestamp,action_id,Database.json(gates),story_id))
        work_id = int(connection.execute(
            "INSERT INTO work_item(kind,story_id,status,priority,payload_json,created_at,updated_at,idempotency_key,available_at) "
            "VALUES('draft',?,'queued',0,?,?,?,?,?)",
            (story_id,Database.json(payload),timestamp,timestamp,f'draft-approval:{action_id}',timestamp)
        ).lastrowid)
    service._create_editable_draft_shell(work_id,payload)
    service._dispatch_draft(work_id)
    return 'approved'
