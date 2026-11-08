"""Role enforcement, shared-report isolation, migration and AI administration."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, update
from app import admin, ai, api, auth, store
from app.normalizer import DEFAULT_CONFIG
from test_library import isolated, register, new_run, execute, HEADERS, CODE


def role(client, user, value, expected='user'):
    return client.put(f"/api/admin/users/{user['id']}/role",json={'role':value,'expected_role':expected})


def test_roles_bootstrap_live_sessions_and_last_admin(isolated):
    with TestClient(api.app,headers=HEADERS) as a, TestClient(api.app,headers=HEADERS) as b:
        alice=register(a,'alice');bob=register(b,'bob')
        assert alice['role']=='admin' and bob['role']=='user'
        assert b.get('/api/admin/users').status_code==403
        assert role(b,bob,'admin').status_code==403
        assert role(a,alice,'user','admin').status_code==409
        assert role(a,bob,'observer').status_code==200
        assert b.get('/api/auth/me').json()['role']=='observer'
        assert b.get('/api/config').status_code==403
        assert role(a,bob,'admin').status_code==409
        assert role(a,bob,'admin','observer').status_code==200
        assert role(b,alice,'user','admin').status_code==200
        assert a.get('/api/admin/users').status_code==403
        assert role(b,bob,'observer','admin').status_code==409
        store.init()
        assert a.get('/api/auth/me').json()['role']=='user'
        assert b.get('/api/auth/me').json()['role']=='admin'
        assert a.post('/api/auth/register',json={'username':'evil','display_name':'Evil','password':'Long-password-42','role':'admin'}).status_code in (404,405)
        assert set(b.get('/api/admin/users').json()[0])=={'id','username','display_name','role','created_at','blocked'}


def test_concurrent_admin_demotions_leave_one(isolated):
    with TestClient(api.app,headers=HEADERS) as a, TestClient(api.app,headers=HEADERS) as b:
        alice=register(a,'alice');bob=register(b,'bob');role(a,bob,'admin');bob['role']='admin'
        def demote(user):
            try:admin.change_role(user['id'],admin.RoleChange(role='user',expected_role='admin'),user);return 200
            except HTTPException as e:return e.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(demote,[alice,bob]))
        assert sorted(results)==[200,409]
        with store.engine.connect() as conn:assert len(conn.execute(select(store.users).where(store.users.c.role=='admin')).all())==1


def test_legacy_role_migration_only_once(tmp_path,monkeypatch):
    engine=create_engine(f'sqlite:///{tmp_path / "legacy.sqlite3"}')
    monkeypatch.setattr(store,'engine',engine)
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE users (id VARCHAR PRIMARY KEY, username VARCHAR UNIQUE NOT NULL, display_name VARCHAR NOT NULL, password_hash VARCHAR NOT NULL, created_at VARCHAR NOT NULL)')
        conn.exec_driver_sql("INSERT INTO users VALUES ('first','first','First','hash','2020'), ('second','second','Second','hash','2021')")
    store.init()
    with engine.begin() as conn:
        assert dict(conn.execute(select(store.users.c.id,store.users.c.role)).all())=={'first':'admin','second':'user'}
        conn.execute(update(store.users).where(store.users.c.id=='second').values(role='admin'))
        conn.execute(update(store.users).where(store.users.c.id=='first').values(role='observer'))
    store.init()
    with engine.connect() as conn:assert dict(conn.execute(select(store.users.c.id,store.users.c.role)).all())=={'first':'observer','second':'admin'}
    engine.dispose()


def test_observer_denied_all_authoring_routes(isolated):
    with TestClient(api.app,headers=HEADERS) as a, TestClient(api.app,headers=HEADERS) as b:
        register(a,'alice');bob=register(b,'bob');owned=new_run(b);role(a,bob,'observer')
        rid=owned['id'];jid=store.list_jobs(run_id=rid)[0]['id']
        routes=[('GET','/api/config'),('PUT','/api/config'),('GET','/api/datasets'),
          ('POST','/api/datasets/test/normalization-preview'),('GET','/api/run-templates'),('POST','/api/run-templates'),
          ('GET','/api/run-templates/x'),('PUT','/api/run-templates/x'),('DELETE','/api/run-templates/x?revision=1'),
          ('POST','/api/runs'),('POST',f'/api/runs/{rid}/ai'),('POST',f'/api/runs/{rid}/python-checks'),
          ('GET','/api/runtime'),('GET','/api/ollama'),('GET','/api/jobs'),('GET',f'/api/jobs/{jid}'),
          ('POST',f'/api/jobs/{jid}/cancel'),('POST',f'/api/jobs/{jid}/retry'),
          ('GET','/api/check-library'),('POST','/api/check-library'),('GET','/api/check-library/x'),
          ('POST','/api/check-library/x/versions'),('POST','/api/check-library/x/archive'),('POST','/api/check-library/x/versions/1/test'),
          ('GET','/api/dataset-imports'),('POST','/api/dataset-imports'),('GET','/api/dataset-imports/x'),
          ('POST','/api/pivot-views'),('PUT','/api/pivot-views/x'),('DELETE','/api/pivot-views/x?expected_revision=1'),
          ('GET',f'/api/runs/{rid}/shares'),('POST',f'/api/runs/{rid}/shares'),('DELETE',f'/api/runs/{rid}/shares/x'),
          ('GET','/api/admin/users'),('GET','/api/admin/ai'),('PUT','/api/admin/ai'),('POST','/api/admin/ai/test')]
        for method,path in routes:
            r=b.request(method,path,json={} if method in ('POST','PUT') else None)
            assert r.status_code==403,(method,path,r.status_code,r.text)
        assert not b.get('/api/runs/'+rid).json()['can_manage']
        assert store.get_job(jid)['status']=='queued'


@pytest.mark.parametrize('recipient_role',['observer','user','admin'])
def test_shared_report_read_queries_private_source_and_revoke(isolated,recipient_role):
    with TestClient(api.app,headers=HEADERS) as a, TestClient(api.app,headers=HEADERS) as b, TestClient(api.app,headers=HEADERS) as c:
        alice=register(a,'alice');bob=register(b,'bob');register(c,'charlie')
        if recipient_role!='user':assert role(a,bob,recipient_role).status_code==200
        run=execute(new_run(a));assert run['status']=='completed';rid=run['id'];url='/api/runs/'+rid
        # Turn the fixture into a private imported dataset; shared raw JSON must use its owner's identity.
        (isolated/'datasets/test').rename(isolated/'datasets/upload-private')
        job=store.list_jobs(run_id=rid)[0]
        with store.engine.begin() as conn:
            conn.execute(store.dataset_imports.insert().values(id='upload-private',owner_id=alice['id'],job_id=job['id'],upload_id='x',name='Private',filename='data.zip',size=1,sha256='x',created_at=store.now(),stats={}))
        store.patch_run(rid,dataset='upload-private')
        assert b.get(url).status_code==404 # Even another admin cannot read it without a grant.
        assert a.post(url+'/shares',json={'username':'ALICE'}).status_code==422
        assert a.post(url+'/shares',json={'username':'nobody'}).status_code==404
        for _ in range(2):assert a.post(url+'/shares',json={'username':' BOB '}).status_code==201
        assert len(a.get(url+'/shares').json())==1
        visible=b.get('/api/runs').json();assert len(visible)==1 and visible[0]['access']=='shared' and not visible[0]['can_manage']
        assert b.get(url).json()['events']==[]
        for suffix in ('','/applications','/applications/A','/applications/A/source','/pivot','/report.html'):
            response=b.get(url+suffix);assert response.status_code==200,(suffix,response.text)
        assert b.get(url+'/applications/A/source').json()['AFTER']['value']==2
        assert b.get(url+'/applications/unknown/source').status_code==404
        spec={'rows':['PATH'],'columns':['CHANGE_TYPE'],'metrics':['apps'],'filters':[],'changed_only':True,'sort':'apps_desc'}
        assert b.post(url+'/pivot/query',json={'spec':spec}).json()['grand']['apps']==1
        assert b.post(url+'/pivot/applications',json={'spec':spec,'row':None,'column':None,'metric':'apps'}).json()['total']==1
        for method,suffix,payload in [('POST','/ai',{}),('POST','/python-checks',{'code':CODE}),('GET','/check-plan/anything',None),('GET','/shares',None),('POST','/shares',{'username':'charlie'}),('DELETE','/shares/'+bob['id'],None)]:
            assert b.request(method,url+suffix,json=payload).status_code==403
        assert c.get(url).status_code==404
        if recipient_role!='observer':
            assert 'upload-private' not in [d['id'] for d in b.get('/api/datasets').json()]
            assert b.post('/api/runs',json={'dataset':'upload-private','config':deepcopy(DEFAULT_CONFIG)}).status_code==404
        before=store.get_run(rid)
        assert a.delete(url+'/shares/'+bob['id']).status_code==200
        assert b.get('/api/runs').json()==[]
        for suffix in ('','/applications','/applications/A','/applications/A/source','/pivot','/report.html'):
            assert b.get(url+suffix).status_code==404
        assert b.post(url+'/pivot/query',json={'spec':spec}).status_code==404
        assert b.post(url+'/pivot/applications',json={'spec':spec,'row':None,'column':None,'metric':'apps'}).status_code==404
        assert store.get_run(rid)==before
        store.patch_run(rid,status='queued')
        assert a.post(url+'/shares',json={'username':'bob'}).status_code==409


@pytest.mark.parametrize('url',['ftp://host','http://a:b@host','http://host?query=1','http://host#x','http://host:99999','http://','http://host/has space','http://host\\other'])
def test_bad_ai_addresses(url):
    with pytest.raises(ValueError):admin.AIConnection(base_url=url)


def test_ai_settings_permissions_revision_testing_and_job_snapshot(isolated,monkeypatch):
    with TestClient(api.app,headers=HEADERS) as a, TestClient(api.app,headers=HEADERS) as b:
        register(a,'alice');register(b,'bob')
        assert b.get('/api/admin/ai').status_code==403
        assert b.put('/api/admin/ai',json={'base_url':'http://evil','revision':0}).status_code==403
        initial=a.get('/api/admin/ai').json();assert initial['revision']==0
        first=a.put('/api/admin/ai',json={'base_url':'http://127.0.0.1:12345/api/chat/','revision':0})
        assert first.status_code==200 and first.json()['base_url']=='http://127.0.0.1:12345'
        assert a.put('/api/admin/ai',json={'base_url':'http://other','revision':0}).status_code==409
        destinations=[]
        class FakeClient:
            def __init__(self,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def get(self,url):
                destinations.append(url)
                return httpx.Response(200,json={'models':[{'name':admin.OLLAMA_MODEL}]},request=httpx.Request('GET',url))
            def post(self,url,**kwargs):
                destinations.append(url)
                return httpx.Response(200,json={'message':{'content':json.dumps({'summary':'Факты','signals':'Сигналы','next_steps':'Проверить'})}},request=httpx.Request('POST',url))
        # Patch only client construction after TestClients are already alive.
        monkeypatch.setattr(admin.httpx,'Client',FakeClient)
        result=a.post('/api/admin/ai/test',json={'base_url':'http://test-only/api/chat'})
        assert result.json()['available'] and destinations==['http://test-only/api/tags']
        assert a.get('/api/admin/ai').json()==first.json()
        run=new_run(a);store.patch_run(run['id'],status='completed',summary={'total_apps':1,'changed_apps':1,'decision_changed':0,'changed_same_decision':1,'transitions':[]},checks=[])
        with store.engine.begin() as conn:conn.execute(update(store.jobs).where(store.jobs.c.run_id==run['id']).values(status='completed'))
        job=a.post(f"/api/runs/{run['id']}/ai");assert job.status_code==202,job.text
        snapshot=next(j for j in store.list_jobs(run_id=run['id']) if j['kind']=='ai')
        assert store.get_job(snapshot['id'])['params']['ollama_url']=='http://127.0.0.1:12345'
        assert a.put('/api/admin/ai',json={'base_url':'https://new-host/prefix/api/chat','revision':1}).status_code==200
        result=ai.generate_ai(run['id'],store.get_job(snapshot['id'])['params']['ollama_url'])
        assert result['status']=='completed' and destinations[-1]=='http://127.0.0.1:12345/api/chat'
        store.init();assert admin.ai_settings()['base_url']=='https://new-host/prefix'
