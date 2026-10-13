from pathlib import Path
import json
import pytest
from open_source_ai_news_wire.web import create_app
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.hosted_bridge import build_resource_projection, build_story_projection, _artifact_resources

class FakeGeneration:
    def __init__(self):
        self.requests=[]
        self.jobs=[]
    def catalog(self, force_reload=False):
        return {'models':[{'id':'test-model','display_name':'Test model','is_default':True}],
                'skills':[{'id':'skill-a','name':'Writer','description':'Write text'}],
                'default_model':'test-model','error':'','preferences':{'text':{},'graphics':{}}}
    def list_jobs(self, article_id):
        return [job for job in self.jobs if job['article_id']==article_id]
    def enqueue(self, article_id, task_type, model, skills, customizations, request_id):
        self.requests.append((article_id,task_type,model,skills,customizations,request_id))
        return {'id':'job-test','article_id':article_id,'status':'queued'}

@pytest.fixture
def workspace(tmp_path):
    app=create_app(data_root=tmp_path/'data',auth_required=False,test_config={'TESTING':True})
    db=app.config['DATABASE']
    seed_demo_data(db)
    manager=FakeGeneration()
    app.config['GENERATION_SERVICE']=manager
    client=app.test_client()
    article=app.config['DASHBOARD_SERVICE'].list_articles()['articles'][0]
    client.get('/')
    with client.session_transaction() as session:
        token=session['csrf_token']
    return app,client,article,token,manager

def test_plain_feed_and_workspace_remove_editorial_controls(workspace):
    app,client,article,token,manager=workspace
    feed=client.get('/inbox').get_data(as_text=True)
    page=client.get('/articles/'+article['id']).get_data(as_text=True)
    assert 'Fetched' in feed and article['canonical_url'] in feed
    assert 'Generate content' in page and 'Generate graphics' in page
    assert 'Customizations' in page and 'ChatGPT model' in page
    for retired in ['Review score','Urgent floor','Trust, research','Queue type','Review window','priority_score']:
        assert retired not in feed+page
    assert 'Operational Review' in feed
    assert 'Operational review</h2>' not in feed
    assert client.get('/operations').status_code==200

def test_generation_buttons_pass_independent_model_skills_and_directions(workspace):
    app,client,article,token,manager=workspace
    for task in ['text','graphics']:
        response=client.post('/articles/'+article['id']+'/generate',data={
            'csrf_token':token,'task_type':task,'model':'test-model','skill_ids':json.dumps(['skill-b','skill-a']),
            'customizations':'Use this angle','request_id':'request-'+task})
        assert response.status_code==303
    assert [request[1] for request in manager.requests]==['text','graphics']
    assert manager.requests[0][3]==['skill-b','skill-a']
    assert manager.requests[1][4]=='Use this angle'
    assert client.post('/articles/'+article['id']+'/generate',data={'task_type':'text'}).status_code==400

def test_operational_read_does_not_acknowledge_article_alerts(workspace):
    app,client,article,token,manager=workspace
    db=app.config['DATABASE']
    db.execute("INSERT INTO alert(kind,severity,title,body,created_at) VALUES('candidate','info','Article alert','Body','2026-10-13T00:00:00Z')")
    response=client.post('/inbox/read-alerts',data={'csrf_token':token})
    assert response.status_code==303 and response.location.endswith('/operations')
    assert db.one("SELECT read_at FROM alert WHERE title='Article alert'")['read_at'] is None
    assert client.get('/status.json').json['unread_alerts']==0

def test_hosted_projection_is_article_based_and_redacted(workspace):
    app,client,article,token,manager=workspace
    service=app.config['DASHBOARD_SERVICE']
    service.generation_service=manager
    records,digest=build_story_projection(service)
    assert len(records)==service.list_articles(page_size=None)['total']
    assert all('canonical_url' in record and 'first_fetched_at' in record for record in records)
    assert all('review_score' not in record and 'priority_score' not in record for record in records)
    resources,_=build_resource_projection(service)
    assert any(record['resource_type']=='generation_catalog' for record in resources)
    assert any(record['resource_type']=='article_detail' and record['resource_id']==article['id'] for record in resources)
    assert str(service.database.paths.root) not in json.dumps(resources)

def test_artifacts_are_chunked_and_only_public_metadata_crosses_bridge(tmp_path):
    output=tmp_path/'private-output.png'
    output.write_bytes(b'x'*(600*1024))
    class Manager:
        def artifact_path(self,*args): return output
    descriptors,resources=_artifact_resources(Manager(),{'id':'job1','artifacts':[{'id':'artifact1','name':'preview.png','mime_type':'image/png'}]})
    assert descriptors[0]['chunk_count']==2
    assert descriptors[0]['size_bytes']==600*1024
    assert len([item for item in resources if item[0]=='generation_artifact_chunk'])==2
    assert str(tmp_path) not in json.dumps(descriptors)
