"""Regression cases reproduced during the v0.9 audit."""
import json
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from app import api, store, queueing, normalizer, library, pivot
from app.checks.check_target_model_changed import run as model_check
from test_comparison import compare, config
from test_library import isolated, register, new_run, execute, HEADERS


def test_no_attributes_still_counted_and_queryable(isolated):
    with TestClient(api.app,headers=HEADERS) as c:
        register(c,'alice');cfg=config();cfg['exclude_paths']=['appData','value']
        report=execute(new_run(c,config=cfg));assert report['status']=='completed',report['error']
        assert report['summary']['total_apps']==1 and report['summary']['empty_apps']==1
        assert report['summary']['changed_apps']==0 and report['summary']['decision_changed']==0
        url='/api/runs/'+report['id']
        assert c.get(url+'/applications').json()['total']==1
        assert c.get(url+'/applications?changed=true').json()['total']==0
        assert c.get(url+'/applications?path=missing').json()['total']==0
        assert c.get(url+'/applications/A').json()['total']==0
        assert c.get(url+'/applications/A/source').status_code==200
        spec={'rows':[],'columns':[],'metrics':['apps','attributes'],'changed_only':False}
        data=c.post(url+'/pivot/query',json={'spec':spec}).json()
        assert data['grand']['apps']==1 and data['grand']['attributes']==0
        assert c.post(url+'/pivot/applications',json={'spec':spec,'metric':'apps'}).json()['total']==1
        assert c.get(url+'/report.html').status_code==200


def test_exact_numbers_beyond_decimal_context(tmp_path):
    conn,mart=compare(tmp_path,{'id':10**28,'huge':10**400,'same':1,'zero':0},
                      {'id':10**28+1,'huge':10**400+1,'same':1.0,'zero':-0.0})
    assert mart.app_ids(diff_flag=1)==['A'] and mart.count(diff_flag=1)==2
    assert mart.rows(path='huge')[0]['VALUE_AFTER']==str(10**400+1)
    conn.close()


@pytest.mark.parametrize('mode',['value','key'])
def test_array_keys_distinguish_types_and_delimiters(tmp_path,mode):
    values=[-1,'-1',1.5,'1.5',None,'NULL',True,'true','s:-1','a|b','a%7Cb']
    if mode=='key':values=[v for v in values if v is not None]
    items=values if mode=='value' else [{'id':v,'result':i} for i,v in enumerate(values)]
    rules=config({'items':{'mode':mode,**({'keys':['id']} if mode=='key' else {})}})
    conn,mart=compare(tmp_path,{'items':items},{'items':list(reversed(items))},rules)
    assert len(mart.rows(path='items' if mode=='value' else 'items.id'))==len(values)
    assert mart.count(diff_flag=1)==0
    conn.close()


def product(pid,model):return {'product_id':pid,'scoring':{'pd':{'modelName':model}}}
def doc(*products):return {'decisionResult':{'product_trials':list(products)}}


def test_original_model_check_uses_primary_product_and_preserves_product_context(tmp_path):
    before=doc(product('A','same'),product('B','old'),product('removed','gone'))
    after=doc(product('new','brand-new'),product('B','new'),product('A','same'))
    conn,mart=compare(tmp_path,before,after)
    rows=model_check(mart)
    assert len(rows)==1 and rows[0]['RESULT']=='PASS' and rows[0]['CNT']==0
    assert rows[0]['ARGUMENT']==''
    assert mart.total_apps()==1 and len(mart.segments())==1 and len(mart.product_segments())==4
    assert next(c for c in library.builtin_catalog() if c['key']=='target_model_changed')['version']==1
    conn.close()


def test_original_model_check_compares_primary_models_when_product_is_removed(tmp_path):
    conn,mart=compare(tmp_path,doc(product('A','old-A'),product('B','same-B')),doc(product('B','same-B')))
    rows=model_check(mart)
    assert rows[0]['RESULT']=='FAIL' and rows[0]['CNT']==1
    assert rows[0]['ARGUMENT']=='old-A -> same-B'
    conn.close()


def test_config_preconditions_and_concurrent_saves(isolated):
    with TestClient(api.app,headers=HEADERS) as c:
        user=register(c,'alice');cfg=c.get('/api/config').json()
        assert c.get('/api/config').headers['etag']=='"0"'
        assert c.put('/api/config',json=cfg).status_code==428
        assert c.put('/api/config',json=cfg,headers={'If-Match':'0'}).headers['etag']=='"1"'
        def save(i):
            changed=deepcopy(cfg);changed['check_params']['max_drop_rel']=i/10
            try:return store.save_config(user['id'],changed,1)
            except ValueError:return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(save,[2,3]))
        assert results.count(2)==1 and results.count('conflict')==1
        assert c.put('/api/config',json=cfg,headers={'If-Match':'1'}).status_code==409
        assert c.get('/api/config/state').json()['revision']==2
        assert c.get('/api/config').json()['check_params']['max_drop_rel'] in (.2,.3)
        store.init();assert c.get('/api/config/state').json()['revision']==2


def test_config_revision_additive_migration(tmp_path,monkeypatch):
    engine=create_engine(f'sqlite:///{tmp_path / "legacy.sqlite3"}')
    monkeypatch.setattr(store,'engine',engine)
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TABLE configs (id VARCHAR PRIMARY KEY, payload JSON)')
        c.exec_driver_sql('INSERT INTO configs VALUES (?,?)',('legacy',json.dumps(config())))
    store.init();assert store.get_config_state('legacy')=={'config':config(),'revision':1}
    assert store.save_config('legacy',config(),1)==2
    store.init();assert store.get_config_state('legacy')['revision']==2
    engine.dispose()


@pytest.mark.parametrize('outcome',['cancel_queued','failed','timed_out','cancel_running','recovery'])
def test_ai_last_success_survives_unsuccessful_attempts(isolated,monkeypatch,outcome):
    answer={'status':'completed','content':'Previous verified answer','model':'qwen3','created_at':store.now()}
    store.create_run({'id':'ai-run','owner_id':'alice','status':'completed','ai':answer})
    job=queueing.submit('alice','ai-run','ai')
    assert store.get_run('ai-run')['ai']['last_success']==answer
    if outcome=='cancel_queued':queueing.cancel(job['id'])
    else:
        claimed=queueing.claim(job['id']);assert store.get_run('ai-run')['ai']['last_success']==answer
        if outcome=='recovery':
            with store.engine.begin() as c:c.execute(update(store.jobs).where(store.jobs.c.id==job['id']).values(heartbeat=time.time()-60))
            monkeypatch.setattr(queueing.celery,'send_task',lambda *a,**k:None)
            queueing.dispatch_pending();assert not queueing.finish(claimed,'completed',result={**answer,'content':'Stale'})
            claimed=queueing.claim(job['id'])
            with store.engine.begin() as c:c.execute(update(store.jobs).where(store.jobs.c.id==job['id']).values(heartbeat=time.time()-60))
            queueing.dispatch_pending()
        elif outcome=='cancel_running':
            queueing.cancel(job['id']);queueing.finish(claimed,'completed',result={**answer,'content':'Cancelled response'})
        else:queueing.finish(claimed,outcome,error='Ollama unavailable')
    state=store.get_run('ai-run')['ai'];assert state['last_success']==answer
    next_job=queueing.submit('alice','ai-run','ai');claimed=queueing.claim(next_job['id'])
    new_answer={**answer,'content':'New verified answer'}
    queueing.finish(claimed,'completed',result=new_answer)
    assert store.get_run('ai-run')['ai']=={**new_answer,'job_id':next_job['id']}
