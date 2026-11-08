"""Actual 20,000-pair ZIP import and report, using isolated disk and processes."""
import json
from copy import deepcopy
from fastapi.testclient import TestClient
from app import api, imports, queueing, store, tasks
from app.normalizer import DEFAULT_CONFIG
from test_imports import isolated, archive, register, pending, validate, HEADERS


def test_20000_pairs_import_and_full_report(isolated):
    with TestClient(api.app,headers=HEADERS) as client:
        user=register(client,'large')
        def documents():
            for side in ('BEFORE_MASTER','AFTER_BETA'):
                after=side=='AFTER_BETA'
                for i in range(20000):
                    app_id=f'A{i:05d}'
                    doc={'appData':{'application_id':app_id},'value':i+int(after and i%200==0),
                         'decisionResult':{'decision':'APPROVE','product_trials':[
                             {'product_id':'A','scoring':{'pd':{'modelName':'unchanged'}}},
                             {'product_id':'B','scoring':{'pd':{'modelName':'v2' if after and i%1000==0 else 'v1'}}}]}}
                    yield f'RESULTS/{side}/{app_id}.json',json.dumps(doc)
        raw=archive(documents());job=pending(user['id'],raw)
        claimed,work,stats=validate(job,isolated)
        assert stats['pairs']==20000 and stats['json_files']==40000
        assert queueing.finish(claimed,'completed',result=stats,artifact=work/'dataset')
        limits=client.get('/api/dataset-imports/limits').json()
        assert limits['max_pairs']==500000 and limits['max_files']==1000000
        response=client.post('/api/runs',json={'name':'20k validation','dataset':job['dataset_id'],
            'config':deepcopy(DEFAULT_CONFIG),'builtin_checks':['target_model_changed']})
        assert response.status_code==201,response.text
        run_id=response.json()['id'];queued=store.list_jobs(run_id=run_id)[0]
        tasks.supervise(queueing.claim(queued['id']));run=store.get_run(run_id)
        assert run['status']=='completed',run['error']
        assert run['summary']['total_apps']==20000 and run['summary']['changed_apps']==100
        assert run['summary']['empty_apps']==0 and run['checks'][0]['CNT']==0
        assert run['checks'][0]['CHECK_VERSION']==1
        assert run['checks'][0]['RESULT']=='PASS'
        assert client.get('/api/runs/'+run_id+'/applications').json()['total']==20000
