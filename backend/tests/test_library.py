import json
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from app import api, auth, datasets, library, queueing, store, tasks
from app.normalizer import DEFAULT_CONFIG

HEADERS = {'X-Balance-Request':'1'}
CODE = '''def run(mart, params):
    apps = mart.app_ids(diff_flag=1)
    return [{'LABEL': params.get('label', 'Version one'), 'ARGUMENT':'All', 'SOURCE':'AFTER',
      'CNT':len(apps), 'TOTAL':mart.total_apps(), 'METRIC':len(apps), 'RESULT':'FAIL',
      'CONFIDENCE':'HIGH', 'APP_IDS':apps}]
CHECK = {'entrypoint':run}
'''

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    db_url = f'sqlite:///{tmp_path / "state.sqlite3"}'
    engine = create_engine(db_url, connect_args={'check_same_thread':False,'timeout':30})
    monkeypatch.setattr(store,'engine',engine)
    monkeypatch.setenv('DATABASE_URL',db_url)
    monkeypatch.setenv('BALANCE_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(api,'bootstrap',lambda:None)
    for module in (api, queueing, tasks):
        monkeypatch.setattr(module,'RUNS',tmp_path/'runs')
    monkeypatch.setattr(tasks,'JOBS',tmp_path/'jobs')
    monkeypatch.setattr(datasets,'DATASETS',tmp_path/'datasets')
    for name in ('runs','jobs'):(tmp_path/name).mkdir()
    dataset=tmp_path/'datasets/test'
    for side,value in [('BEFORE_MASTER',1),('AFTER_BETA',2)]:
        folder=dataset/'RESULTS'/side;folder.mkdir(parents=True)
        (folder/'A.json').write_text(json.dumps({'appData':{'application_id':'A'},'value':value}))
    (dataset/'manifest.json').write_text('{}')
    store.init();auth.attempts.clear()
    yield tmp_path
    engine.dispose()

def register(client,name):
    from account_helpers import account
    return account(client,name,'Library-test-password-42')

def create(client,code=CODE,**kwargs):
    r=client.post('/api/check-library',json={'name':'User rule','code':code,'params':{'label':'Version one'},**kwargs})
    assert r.status_code==201,r.text
    return r.json()

def new_run(client,version=None,**kwargs):
    payload={'name':'Test run','dataset':'test','config':deepcopy(DEFAULT_CONFIG),'builtin_checks':[],
             'user_checks':[{'version_id':version['id']}] if version else [],**kwargs}
    r=client.post('/api/runs',json=payload)
    assert r.status_code==201,r.text
    return r.json()

def execute(run):
    job=store.list_jobs(run_id=run['id'])[0]
    tasks.supervise(queueing.claim(job['id']))
    return store.get_run(run['id'])

def test_versions_history_archive_and_ownership(isolated):
    with TestClient(api.app,headers=HEADERS) as a, TestClient(api.app,headers=HEADERS) as b:
        register(a,'alice');register(b,'bob')
        v1=create(a)
        payload={k:v1[k] for k in ('name','description','code','params','timeout','memory_mb')}
        payload.update(expected_version=1,code=CODE.replace('Version one','Version two'),params={'label':'Version two'})
        v2=a.post(f"/api/check-library/{v1['check_id']}/versions",json=payload)
        assert v2.status_code==201 and v2.json()['version']==2
        original=a.get(f"/api/check-library/{v1['check_id']}/versions/1").json()
        assert original==v1
        assert a.post(f"/api/check-library/{v1['check_id']}/versions",json=payload).status_code==409
        assert b.get('/api/check-library').json()==[]
        for suffix in ['', '/versions/1']:
            assert b.get('/api/check-library/'+v1['check_id']+suffix).status_code==404
        assert b.post('/api/check-library/'+v1['check_id']+'/versions',json=payload).status_code==404
        assert b.post('/api/check-library/'+v1['check_id']+'/archive',json={'archived':True}).status_code==404
        a.post('/api/check-library/'+v1['check_id']+'/archive',json={'archived':True})
        assert a.get('/api/check-library').json()==[]
        assert a.get('/api/check-library?include_archived=true').json()[0]['archived']
        assert a.get(f"/api/check-library/{v1['check_id']}/versions/1").json()==v1
        assert a.post('/api/check-library',json={'name':'Bad','code':'def broken('}).status_code==422
        assert a.post('/api/check-library',json={'name':'Bad','code':'pass','params':{'x':'a'*16001}}).status_code==422

def test_concurrent_versions_have_one_winner(isolated):
    user={'id':'owner'}
    version=library.create_check(library.VersionInput(name='Rule',code=CODE),user)
    body=library.NextVersion(name='Rule',code=CODE,expected_version=1)
    def publish(_):
        try:return library.add_version(version['check_id'],body,user)['version']
        except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(publish,range(2)))==[2,409]
    with store.engine.connect() as conn:
        assert conn.execute(select(store.check_versions.c.version).order_by(store.check_versions.c.version)).scalars().all()==[1,2]

def test_saving_source_never_executes_it(isolated):
    target=isolated/'should-not-exist'
    code=f"open({str(target)!r}, 'w').write('executed')\nCHECK={{'entrypoint':lambda m,p:[]}}"
    version=library.create_check(library.VersionInput(name='Untrusted',code=code),{'id':'owner'})
    assert not target.exists()
    assert version['code']==code

def test_selected_versions_are_frozen_and_cannot_be_forged(isolated):
    with TestClient(api.app,headers=HEADERS) as a, TestClient(api.app,headers=HEADERS) as b:
        register(a,'alice');register(b,'bob');v1=create(a)
        run=new_run(a,v1,user_checks=[{'version_id':v1['id'],'params':{'label':'Override'}}])
        plan=store.get_run(run['id'])['check_plan']
        assert plan[0]['code']==v1['code'] and plan[0]['params']=={'label':'Override'}
        assert 'code' not in run['check_plan'][0]
        a.post(f"/api/check-library/{v1['check_id']}/versions",json={'name':'Renamed','code':'raise Exception("new")','params':{},'expected_version':1})
        a.post('/api/check-library/'+v1['check_id']+'/archive',json={'archived':True})
        assert store.get_run(run['id'])['check_plan']==plan
        payload={'dataset':'test','config':DEFAULT_CONFIG,'user_checks':[{'version_id':v1['id']}]}
        assert a.post('/api/runs',json=payload).status_code==409
        assert b.post('/api/runs',json=payload).status_code==404
        assert b.get(f"/api/runs/{run['id']}/check-plan/{plan[0]['key']}").status_code==404
        assert a.post('/api/runs',json={**payload,'user_checks':[{'version_id':v1['id'],'kind':'builtin'}]}).status_code==422
        assert a.post('/api/runs',json={**payload,'user_checks':[],'builtin_checks':['../../evil']}).status_code==422
        assert a.post('/api/runs',json={**payload,'user_checks':[],'check_plan':plan}).status_code==422
        result=execute(run)
        assert result['status']=='completed',result['error']
        assert result['checks'][0]['LABEL']=='Override'

def test_pipeline_report_export_drilldown_and_saved_trial(isolated):
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'alice');version=create(client,params={'label':'<script>user</script>'})
        builtin=library.builtin_catalog()[0]['key']
        run=new_run(client,version,builtin_checks=[builtin])
        result=execute(run)
        assert result['status']=='completed',result['error']
        assert result['summary']['checks_total']==2
        assert result['summary']['checks_failed']==1
        assert len(result['checks'])==1  # Selected builtin returns no rows for a productless example.
        row=result['checks'][0]
        assert row['CHECK_VERSION']==1 and row['CHECK_KIND']=='python' and row['APP_IDS']==['A']
        apps=client.get(f"/api/runs/{run['id']}/applications?check=0").json()
        assert apps['total']==1
        exported=client.get(f"/api/runs/{run['id']}/report.html").text
        assert '&lt;script&gt;user&lt;/script&gt;' in exported and '<script>user</script>' not in exported
        assert version['sha256'] in exported and 'User rule · v1' in exported
        snapshot=client.get(f"/api/runs/{run['id']}/check-plan/{row['CHECK_ID']}").json()
        assert snapshot['code']==version['code'] and snapshot['execution']['status']=='completed'
        trial=client.post(f"/api/check-library/{version['check_id']}/versions/1/test",json={'run_id':run['id']})
        assert trial.status_code==202
        assert trial.json()['check_ref']['version_id']==version['id']
        tasks.supervise(queueing.claim(trial.json()['id']))
        assert store.get_job(trial.json()['id'])['status']=='completed'

@pytest.mark.parametrize('code,expected', [
    ('CHECK={"entrypoint":lambda m,p:[]}\nraise RuntimeError("Deliberate failure")','failed'),
    ('CHECK={"entrypoint":lambda m,p:[]}\nwhile True: pass','timed_out'),
    ("CHECK={'entrypoint':lambda m,p:[dict()]}",'failed'),
])
def test_failure_never_publishes_partial_report_and_retry_preserves_version(isolated,code,expected):
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'alice');v1=create(client,code=code,timeout=1 if expected=='timed_out' else 10)
        run=new_run(client,v1);result=execute(run)
        assert result['status']==expected,result['error']
        assert result['check_executions'][-1]['status']==expected
        assert not (isolated/'runs'/run['id']/'result.duckdb').exists()
        assert client.get(f"/api/runs/{run['id']}/report.html").status_code==409
        client.post(f"/api/check-library/{v1['check_id']}/versions",json={'name':'Fixed','code':CODE,'expected_version':1})
        client.post('/api/check-library/'+v1['check_id']+'/archive',json={'archived':True})
        job=store.list_jobs(run_id=run['id'])[0]
        retry=client.post('/api/jobs/'+job['id']+'/retry')
        assert retry.status_code==202,retry.text
        assert store.get_run(retry.json()['run_id'])['check_plan']==result['check_plan']

def test_custom_phase_cancellation_and_isolation(isolated):
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'alice');v=create(client,code='CHECK={"entrypoint":lambda m,p:[]}\nwhile True: pass',timeout=20);run=new_run(client,v)
        job=store.list_jobs(run_id=run['id'])[0]
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(tasks.supervise,queueing.claim(job['id']))
            for _ in range(100):
                if store.get_run(run['id'])['stage']=='user_checks':break
                time.sleep(.1)
            else:pytest.fail('custom phase did not start')
            queueing.cancel(job['id']);future.result(timeout=5)
        assert store.get_run(run['id'])['status']=='cancelled'
        assert not (isolated/'runs'/run['id']/'result.duckdb').exists()
        secret=isolated/'private.txt';secret.write_text('private')
        v=create(client,code=f"open({str(secret)!r}).read()\nCHECK={{'entrypoint':lambda m,p:[]}}")
        result=execute(new_run(client,v))
        assert result['status']=='failed' and 'Permission denied' in result['error']

def test_run_without_checks_and_legacy_default(isolated):
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'alice')
        result=execute(new_run(client))
        assert result['status']=='completed' and result['checks']==[] and result['summary']['checks_total']==0
        legacy=client.post('/api/runs',json={'dataset':'test','config':DEFAULT_CONFIG})
        assert legacy.status_code==201
        assert len(legacy.json()['check_plan'])==5

def test_ai_receives_custom_check_version_without_source(isolated, monkeypatch):
    import httpx
    from app import ai
    import app.settings
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'alice');version=create(client);run=new_run(client,version)
        result=execute(run)
        assert result['status']=='completed'
        captured=[]
        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200,json={'message':{'content':json.dumps({'summary':'Факт','signals':'Сигнал','next_steps':'Проверить'})}})
        real_client=httpx.Client
        monkeypatch.setattr(ai.httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
        response=ai.generate_ai(run['id'])
        assert response['status']=='completed'
        context=json.loads(captured[0]['messages'][1]['content'].removesuffix('\n/no_think'))
        assert context['checks'][0]['name']=='User rule' and context['checks'][0]['version']==1
        assert context['checks'][0]['fail_rows']==1
        assert 'def run(' not in captured[0]['messages'][1]['content']
