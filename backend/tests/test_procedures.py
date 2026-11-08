from copy import deepcopy
import duckdb
from fastapi.testclient import TestClient
from app import api,store,queueing,tasks,procedures
from app.normalizer import DEFAULT_CONFIG
from test_library import isolated,register,new_run,execute,HEADERS

def saved(client,stage,code=None):
    response=client.post('/api/procedures',json={'stage':stage,'name':'Procedure '+stage,'code':code or procedures.EXAMPLES[stage],'filename':stage+'.py'})
    assert response.status_code==201,response.text
    row=response.json();return {'id':row['id'],'revision':row['revision']}

def test_all_three_python_stages_and_exact_fact_drill(isolated):
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'alice')
        selected={s:saved(client,s) for s in procedures.STAGES}
        run=execute(new_run(client,procedures=selected))
        assert run['status']=='completed',run['error']
        assert run['summary']['changed_apps']==1
        response=client.post('/api/runs/'+run['id']+'/pivot/query',json={'spec':{'rows':['PATH'],'columns':['SOURCE'],'metrics':['apps','attributes']}})
        assert response.status_code==200,response.text
        pivot=response.json();assert pivot['grand']['apps']==1 and pivot['grand']['attributes']==1
        assert {tuple(r['key']) for r in pivot['columns']['items']}=={('BEFORE',),('AFTER',)}
        scope={'spec':pivot['spec'],'row':['value'],'column':['AFTER'],'metric':'apps','app_id':'A'}
        detail=client.post('/api/runs/'+run['id']+'/pivot/attributes',json=scope)
        assert detail.status_code==200,detail.text
        assert [r['PATH'] for r in detail.json()['items']]==['value']
        for stage in selected:
            snap=client.get('/api/runs/'+run['id']+'/procedure-plan/'+stage).json()
            assert snap['kind']=='python' and snap['code']==procedures.EXAMPLES[stage]

def test_custom_aggregation_changes_report_buckets(isolated):
    code=procedures.EXAMPLES['aggregation'].replace('yield fact',"fact['ADJUSTED_VALUE']='custom bucket'\n        yield fact")
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'alice');choice=saved(client,'aggregation',code)
        run=execute(new_run(client,procedures={'aggregation':choice}))
        assert run['status']=='completed',run['error']
        r=client.post('/api/runs/'+run['id']+'/pivot/query',json={'spec':{'rows':['ADJUSTED_VALUE'],'columns':['SOURCE'],'metrics':['apps']}})
        assert r.status_code==200,r.text
        assert r.json()['rows']['items'][0]['key']==['custom bucket']

def test_procedure_isolation_and_invalid_stage_result(isolated):
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'alice')
        choice=saved(client,'normalization',procedures.EXAMPLES['normalization'].replace('return defaults.normalize',"open('/etc/passwd').read()\n    return defaults.normalize"))
        run=execute(new_run(client,procedures={'normalization':choice}))
        assert run['status']=='failed' and 'PermissionError' in run['error']
        choice=saved(client,'comparison',procedures.EXAMPLES['comparison'].replace('yield row',"row['APP_ID']='foreign'\n        yield row"))
        run=execute(new_run(client,procedures={'comparison':choice}))
        assert run['status']=='failed' and 'контракт' in run['error']

def test_procedure_ownership_revision_and_snapshots(isolated):
    with TestClient(api.app,headers=HEADERS) as a,TestClient(api.app,headers=HEADERS) as b:
        register(a,'alice');register(b,'bob');choice=saved(a,'normalization')
        assert b.get('/api/procedures').json()==[]
        r=b.post('/api/runs',json={'dataset':'test','config':DEFAULT_CONFIG,'procedures':{'normalization':choice}})
        assert r.status_code==404
        run=new_run(a,procedures={'normalization':choice})
        edit={'stage':'normalization','name':'Changed','code':procedures.EXAMPLES['normalization'].replace('return defaults.normalize','raise ValueError("changed")\n    return defaults.normalize'),'expected_revision':1}
        assert a.put('/api/procedures/'+choice['id'],json=edit).status_code==200
        assert a.put('/api/procedures/'+choice['id'],json=edit).status_code==409
        assert execute(run)['status']=='completed'
        assert a.post('/api/runs',json={'dataset':'test','config':DEFAULT_CONFIG,'procedures':{'normalization':choice}}).status_code==409
