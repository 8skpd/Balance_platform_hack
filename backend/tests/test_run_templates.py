from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import api, store, run_templates
from app.normalizer import DEFAULT_CONFIG
from test_library import isolated, register, create, CODE, HEADERS

def spec(**patch):
    return {'name':'Saved run','dataset':'test','config':deepcopy(DEFAULT_CONFIG),'builtin_checks':[], 'user_checks':[],**patch}

def save(client, **patch):
    response=client.post('/api/run-templates',json={'name':' My template ','spec':spec(**patch)})
    assert response.status_code==201,response.text
    return response.json()

def test_crud_personal_defaults_and_metadata_lists(isolated):
    with TestClient(api.app,headers=HEADERS) as c:
        register(c,'alice');original=c.get('/api/config').json()
        template=save(c,builtin_checks=None)
        assert template['name']=='My template' and template['revision']==1
        assert len(template['spec']['builtin_checks'])==5
        assert 'owner_id' not in template and all('code' not in row for row in template['checks'])
        listed=c.get('/api/run-templates').json();assert len(listed)==1 and 'spec' not in listed[0]
        assert c.get('/api/run-templates/'+template['id']).json()==template
        changed=c.put('/api/run-templates/'+template['id'],json={'name':'Renamed','spec':spec(name='New run title'),'revision':1})
        assert changed.status_code==200 and changed.json()['revision']==2
        assert changed.json()['spec']['name']=='New run title'
        assert c.get('/api/config').json()==original and store.list_jobs()==[] and store.list_runs()==[]
        assert c.delete('/api/run-templates/'+template['id']+'?revision=1').status_code==409
        assert c.delete('/api/run-templates/'+template['id']+'?revision=2').status_code==200
        assert c.get('/api/run-templates').json()==[]
        assert c.get('/api/run-templates/'+template['id']).status_code==404

def test_no_access_to_foreign_template_or_check(isolated):
    with TestClient(api.app,headers=HEADERS) as a,TestClient(api.app,headers=HEADERS) as b:
        register(a,'alice');register(b,'bob');v1=create(a);template=save(a,user_checks=[{'version_id':v1['id']}])
        assert b.get('/api/run-templates').json()==[]
        for method,kwargs in [('get',{}),('put',{'json':{'name':'Steal','spec':spec(),'revision':1}}),('delete',{'params':{'revision':1}})]:
            assert getattr(b,method)('/api/run-templates/'+template['id'],**kwargs).status_code==404
        assert b.post('/api/run-templates',json={'name':'Foreign','spec':spec(user_checks=[{'version_id':v1['id']}])}).status_code==404
        assert b.post('/api/run-templates',json={'name':'Forged','owner_id':template['id'],'spec':spec()}).status_code==422
        a.post('/api/auth/logout');assert a.get('/api/run-templates').status_code==401
        assert a.post('/api/run-templates',json={'name':'No login','spec':spec()}).status_code==401

def test_exact_version_parameters_and_created_run_snapshot(isolated):
    with TestClient(api.app,headers=HEADERS) as c:
        register(c,'alice');v1=create(c,params={'label':'Frozen','threshold':.1})
        template=save(c,user_checks=[{'version_id':v1['id'],'params':{'threshold':.8}}])
        assert template['spec']['user_checks']==[{'version_id':v1['id'],'params':{'label':'Frozen','threshold':.8}}]
        v2=c.post(f"/api/check-library/{v1['check_id']}/versions",json={'name':'New version','code':CODE,'params':{'label':'Changed'},'expected_version':1})
        assert v2.status_code==201
        loaded=c.get('/api/run-templates/'+template['id']).json();assert loaded['spec']==template['spec'] and loaded['issues']==[]
        response=c.post('/api/runs',json=loaded['spec']);assert response.status_code==201,response.text
        run=store.get_run(response.json()['id']);plan=deepcopy(run['check_plan'])
        assert plan[0]['version_id']==v1['id'] and plan[0]['params']['threshold']==.8
        assert c.put('/api/run-templates/'+template['id'],json={'name':'Other','spec':spec(),'revision':1}).status_code==200
        assert c.delete('/api/run-templates/'+template['id']+'?revision=2').status_code==200
        assert store.get_run(run['id'])['check_plan']==plan and store.get_run(run['id'])['config']==loaded['spec']['config']

def test_archived_reference_and_missing_dataset_can_be_repaired(isolated):
    with TestClient(api.app,headers=HEADERS) as c:
        register(c,'alice');v=create(c);template=save(c,user_checks=[{'version_id':v['id']}])
        assert c.post('/api/check-library/'+v['check_id']+'/archive',json={'archived':True}).status_code==200
        detail=c.get('/api/run-templates/'+template['id']).json();assert detail['issues'] and detail['spec']['user_checks']
        assert c.post('/api/runs',json=detail['spec']).status_code==409
        assert c.put('/api/run-templates/'+template['id'],json={'name':'Repair','spec':spec(),'revision':1}).status_code==200
        (isolated/'datasets/test').rename(isolated/'datasets/replacement')
        detail=c.get('/api/run-templates/'+template['id']).json();assert any('Датасет' in s for s in detail['issues'])
        response=c.put('/api/run-templates/'+template['id'],json={'name':'Repaired','spec':spec(dataset='replacement'),'revision':2})
        assert response.status_code==200 and response.json()['issues']==[]

def test_concurrent_update_does_not_lose_changes(isolated):
    with TestClient(api.app,headers=HEADERS) as c:
        user=register(c,'alice');template=save(c)
        def update(name):
            try:return run_templates.save(name,api.NewRun(**spec()),user,template['id'],1)['revision']
            except HTTPException as exc:return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(update,['First','Second']))
        assert sorted(results)==[2,409]
        assert c.get('/api/run-templates/'+template['id']).json()['revision']==2

@pytest.mark.parametrize('patch,status',[
    ({'dataset':'upload-not-owned'},404),({'builtin_checks':['unknown']},422),
    ({'config':{**DEFAULT_CONFIG,'buckets':{'score':[2,1]}}},422),
])
def test_invalid_references_and_configuration_are_rejected(isolated,patch,status):
    with TestClient(api.app,headers=HEADERS) as c:
        register(c,'alice')
        assert c.post('/api/run-templates',json={'name':'Invalid','spec':spec(**patch)}).status_code==status
        assert c.get('/api/run-templates').json()==[]

def test_empty_names_duplicate_checks_and_stale_update(isolated):
    with TestClient(api.app,headers=HEADERS) as c:
        register(c,'alice');v=create(c)
        assert c.post('/api/run-templates',json={'name':'   ','spec':spec()}).status_code==422
        assert c.post('/api/run-templates',json={'name':'Duplicate','spec':spec(user_checks=[{'version_id':v['id']},{'version_id':v['id']}])}).status_code==422
        t=save(c);payload={'name':'Second','spec':spec(),'revision':1}
        assert c.put('/api/run-templates/'+t['id'],json=payload).status_code==200
        assert c.put('/api/run-templates/'+t['id'],json=payload).status_code==409
        assert c.get('/api/run-templates/'+t['id']).json()['name']=='Second'

def test_additive_initialization_keeps_existing_records(isolated):
    with TestClient(api.app,headers=HEADERS) as c:
        user=register(c,'alice');t=save(c);store.init();store.init()
        assert c.get('/api/run-templates/'+t['id']).json()['revision']==1
        with store.engine.connect() as conn:assert conn.execute(select(store.users.c.id)).scalar_one()==user['id']
