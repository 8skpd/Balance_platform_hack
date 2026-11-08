from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from fastapi.testclient import TestClient
from app import api,store,tasks,queueing,library
from app.normalizer import DEFAULT_CONFIG
from test_library import isolated,register,new_run,execute,HEADERS,CODE

def complete_job(response):
    assert response.status_code==202,response.text
    job=response.json();tasks.supervise(queueing.claim(job['id']));value=store.get_job(job['id'])
    assert value['status']=='completed',value['error']
    return value

def test_added_check_published_and_approval_reset(isolated):
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'alice');run=execute(new_run(client));rid=run['id'];url='/api/runs/'+rid
        assert client.put(url+'/review',json={'status':'in_review','assignee':'alice','reason':'Ready','revision':0}).status_code==200
        assert client.put(url+'/review',json={'status':'approved','assignee':'alice','reason':'OK','revision':1}).status_code==200
        trial=complete_job(client.post(url+'/python-checks',json={'code':CODE,'publish':False}))
        assert store.get_run(rid)['checks']==[]
        added=complete_job(client.post(url+'/python-checks',json={'code':CODE,'publish':True,'name':'New check'}))
        result=client.get(url).json();assert result['report_revision']==2
        assert len(result['checks'])==1 and result['checks'][0]['CNT']==1
        assert result['summary']['checks_total']==1
        assert client.get(url+'/applications?check_index=0').json()['total']==1
        assert client.get(url+'/review').json()['status']=='in_review'
        assert client.get(url+'/check-plan/'+result['checks'][0]['CHECK_ID']).json()['code']==CODE
        html=client.get(url+'/report.html').text
        assert 'tab=pivot' in html and 'New check' in html
        assert 'added-' not in client.get('/api/run-history').text

def test_complete_check_membership_beyond_preview_limit(isolated):
    dataset=isolated/'datasets/test'
    for side,old in [('BEFORE_MASTER',True),('AFTER_BETA',False)]:
        for i in range(205):
            app_id=f'B{i:03d}'
            (dataset/'RESULTS'/side/(app_id+'.json')).write_text(json.dumps({'value':1 if old else 2}))
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'alice');run=execute(new_run(client));url='/api/runs/'+run['id']
        complete_job(client.post(url+'/python-checks',json={'code':CODE,'publish':True}))
        r=client.get(url).json();assert len(r['checks'][0]['APP_IDS'])==100 and r['checks'][0]['MEMBERSHIP']['count']==206
        all_ids=[]
        for page in range(1,4):
            response=client.get(url+'/applications',params={'check_index':0,'page':page,'limit':100}).json()
            assert response['total']==206
            all_ids.extend(x['APP_ID'] for x in response['items'])
        assert len(set(all_ids))==206

def test_auto_ai_enqueued_after_full_report(isolated):
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'alice');run=execute(new_run(client,auto_ai=True))
        assert run['status']=='completed'
        jobs=[j for j in store.list_jobs(run_id=run['id']) if j['kind']=='ai']
        assert len(jobs)==1 and jobs[0]['status']=='queued'
        assert jobs[0]['params']['report_revision']==1
        assert jobs[0]['params']['context']['difference_examples'][0]['PATH']=='value'

def test_concurrent_checks_preserve_both_additions(isolated):
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'alice');run=execute(new_run(client));url='/api/runs/'+run['id']
        jobs=[client.post(url+'/python-checks',json={'code':CODE,'publish':True,'name':str(i)}).json() for i in range(2)]
        claimed=[queueing.claim(j['id']) for j in jobs]
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(tasks.supervise,claimed))
        result=store.get_run(run['id']);assert result['report_revision']==3 and len(result['checks'])==2
        assert result['checks'][0]['CHECK_ID']!=result['checks'][1]['CHECK_ID']
