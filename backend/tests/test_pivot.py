import json
from copy import deepcopy

import duckdb
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from pydantic import ValidationError

from app import api, auth, pivot, store
from app.normalizer import DEFAULT_CONFIG, normalize, segments
from app.worker import build_database

@pytest.fixture
def report(tmp_path):
    docs={
      'A':({'x':1,'y':1,'decisionResult':{'decision':'APPROVE'}},{'x':2,'y':2,'decisionResult':{'decision':'DECLINE'}}),
      'B':({'x':1,'decisionResult':{'decision':'APPROVE'}},{'x':2,'added':None,'decisionResult':{'decision':'APPROVE'}}),
      'C':({'y':None,'z':'NULL','empty':''},{'y':None,'z':'NULL','empty':''}),
    }
    normal,seg=tmp_path/'normal.jsonl',tmp_path/'segments.jsonl'
    rows=[];cuts=[]
    for app,(before,after) in docs.items():
        rows+=normalize(before,app,'BEFORE',DEFAULT_CONFIG)+normalize(after,app,'AFTER',DEFAULT_CONFIG)
        cuts.append(segments(app,before,after))
    normal.write_text('\n'.join(json.dumps(r) for r in rows));seg.write_text('\n'.join(json.dumps(r) for r in cuts))
    path=tmp_path/'result.duckdb'
    with duckdb.connect(str(path)) as conn:build_database(conn,normal,seg)
    return path

def query(report,**kwargs):
    with duckdb.connect(str(report),read_only=True) as conn:
        return pivot.matrix(conn,pivot.PivotQuery(**kwargs))

def test_unique_totals_not_sum_of_cells(report):
    result=query(report,spec={'rows':['PATH'],'columns':['DECISION_AFTER']})
    assert result['grand']['apps']==2 and result['grand']['attributes']==5
    assert sum(r['apps'] for r in result['rows']['items'])==5
    x=next(r for r in result['rows']['items'] if r['key']==['x'])
    assert x['apps']==2
    assert result['grand']['decision_apps']==1
    for c in result['cells']:
        with duckdb.connect(str(report),read_only=True) as conn:
            drill=pivot.drill(conn,pivot.DrillQuery(spec=result['spec'],row=c['row'],column=c['column']))
        assert drill['total']==c['values']['apps']

def test_pages_keep_global_totals(report):
    result=query(report,spec={'rows':['PATH'],'columns':['DECISION_AFTER']},row_limit=1,column_limit=1)
    assert len(result['rows']['items'])==1 and len(result['columns']['items'])==1
    assert result['rows']['items'][0]['key']==['x'] and result['rows']['items'][0]['apps']==2
    assert result['cells'][0]['values']['apps']==1 and result['grand']['apps']==2
    assert result['columns']['items'][0]['attributes']==2  # B: x and added, including row on another page.
    assert result['rows']['total']==4 and result['columns']['total']==2
    last=query(report,row_page=999,column_page=999,row_limit=1,column_limit=1)
    assert last['rows']['page']==last['rows']['pages']

def test_null_empty_literal_null_and_safe_filters(report):
    result=query(report,spec={'rows':['VALUE_BEFORE'],'columns':[],'changed_only':False})
    keys=[r['key'][0] for r in result['rows']['items']]
    assert None in keys and '' in keys and 'NULL' in keys
    for op,values,apps in [('is_null',[],1),('eq',['NULL'],1),('eq',[''],1),('eq',["' OR 1=1 --"],0)]:
        r=query(report,spec={'rows':['PATH'],'columns':[],'changed_only':False,'filters':[{'field':'VALUE_BEFORE','op':op,'values':values}]})
        assert r['grand']['apps']==apps
    r=query(report,spec={'rows':['PATH'],'columns':[],'filters':[{'field':'PATH','op':'in','values':['x','y']},{'field':'DECISION_AFTER','op':'eq','values':['DECLINE']}]})
    assert r['grand']['apps']==1 and r['grand']['attributes']==2

def test_multiple_axes_metric_scope_and_empty_axes(report):
    spec={'rows':['PREFIX','PATH'],'columns':['DECISION_BEFORE','DECISION_AFTER'],'changed_only':False,'metrics':['apps','changed_apps','decision_apps']}
    result=query(report,spec=spec)
    assert all(len(r['key'])==2 for r in result['rows']['items'])
    with duckdb.connect(str(report),read_only=True) as conn:
        for metric,count in [('apps',3),('changed_apps',2),('decision_apps',1)]:
            assert pivot.drill(conn,pivot.DrillQuery(spec=spec,metric=metric))['total']==count
    flat=query(report,spec={'rows':[],'columns':[],'changed_only':False})
    assert len(flat['cells'])==1 and flat['cells'][0]['values']==flat['grand']
    empty=query(report,spec={'filters':[{'field':'PATH','op':'contains','values':['not-present']}]})
    assert empty['rows']['total']==0 and empty['columns']['total']==0 and empty['grand']['apps']==0

@pytest.mark.parametrize('spec',[
    {'rows':['PATH; DROP TABLE comparison']}, {'rows':['PATH'],'columns':['PATH']},
    {'metrics':['sum(CNT)']}, {'metrics':[]}, {'filters':[{'field':'PATH','op':'contains','values':['']}]},
    {'filters':[{'field':'PATH','op':'eq','values':['x','y']}]},
])
def test_invalid_spec_rejected(spec):
    with pytest.raises(ValidationError):pivot.Spec(**spec)

def test_saved_views_and_queries_are_private_and_updates_conflict(report,tmp_path,monkeypatch):
    engine=create_engine(f'sqlite:///{tmp_path / "meta.sqlite3"}',connect_args={'check_same_thread':False})
    monkeypatch.setattr(store,'engine',engine);monkeypatch.setattr(api,'bootstrap',lambda:None)
    monkeypatch.setattr(api,'RUNS',tmp_path);auth.attempts.clear();store.init()
    (tmp_path/'r').mkdir();report.rename(tmp_path/'r/result.duckdb')
    headers={'X-Balance-Request':'1'}
    with TestClient(api.app,headers=headers) as a,TestClient(api.app,headers=headers) as b:
        users=[]
        for client,name in [(a,'alice'),(b,'bob')]:
            from account_helpers import account
            users.append(account(client,name,'Pivot-test-password-42'))
        store.create_run({'id':'r','owner_id':users[0]['id'],'status':'completed'})
        saved=a.post('/api/pivot-views',json={'name':'My matrix','spec':{'rows':['PATH'],'columns':['CHANGE_TYPE']}})
        assert saved.status_code==201;saved=saved.json()
        assert b.get('/api/pivot-views').json()==[]
        url='/api/pivot-views/'+saved['id']
        body={'name':'Updated','spec':saved['spec'],'expected_revision':1}
        assert b.put(url,json=body).status_code==404
        assert b.delete(url+'?expected_revision=1').status_code==404
        assert a.put(url,json=body).json()['revision']==2
        assert a.put(url,json=body).status_code==409
        assert a.delete(url+'?expected_revision=1').status_code==409
        for suffix in ('query','applications'):
            assert b.post('/api/runs/r/pivot/'+suffix,json={'spec':saved['spec']}).status_code==404
            assert a.post('/api/runs/r/pivot/'+suffix,json={'spec':saved['spec']}).status_code==200
        assert a.post('/api/runs/r/pivot/applications',json={'spec':saved['spec'],'row':[]}).status_code==422
        store.patch_run('r',status='running')
        assert a.post('/api/runs/r/pivot/query',json={'spec':saved['spec']}).status_code==409
        assert a.delete(url+'?expected_revision=2').status_code==200
        assert a.get('/api/pivot-views').json()==[]
    engine.dispose()

def test_source_columns_keep_before_after_order_across_pages(report):
    spec={'rows':['PATH'],'columns':['DIFF_FLAG','SOURCE'],'changed_only':False}
    result=query(report,spec=spec)
    assert [c['key'] for c in result['columns']['items']]==[['0','BEFORE'],['0','AFTER'],['1','BEFORE'],['1','AFTER']]
    pages=[query(report,spec=spec,column_limit=1,column_page=n) for n in range(1,5)]
    assert [p['columns']['items'][0]['key'] for p in pages]==[c['key'] for c in result['columns']['items']]
    assert all(p['grand']==result['grand'] for p in pages)
