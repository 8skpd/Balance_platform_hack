import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine

from app import api, auth, datasets, store
from app.normalizer import DEFAULT_CONFIG, normalize

@pytest.fixture
def client(tmp_path, monkeypatch):
    engine=create_engine(f'sqlite:///{tmp_path / "db.sqlite3"}',connect_args={'check_same_thread':False})
    monkeypatch.setattr(store,'engine',engine)
    monkeypatch.setattr(api,'bootstrap',lambda:None)
    monkeypatch.setattr(datasets,'DATASETS',tmp_path/'datasets')
    for side in ('BEFORE_MASTER','AFTER_BETA'):(tmp_path/'datasets/sample/RESULTS'/side).mkdir(parents=True)
    (tmp_path/'datasets/sample/manifest.json').write_text('{}')
    auth.attempts.clear()
    with TestClient(api.app,headers={'X-Balance-Request':'1'}) as c:
        from account_helpers import account
        account(c,'alice','Preview-test-password-42',display_name='Alice')
        yield c,tmp_path
    engine.dispose()

def docs(folder,before,after):
    for side,doc in [('BEFORE_MASTER',before),('AFTER_BETA',after)]:
        (folder/'datasets/sample/RESULTS'/side/'A.json').write_text(json.dumps(doc))

def call(client,config=None,app_id=None,dataset='sample'):
    return client.post(f'/api/datasets/{dataset}/normalization-preview',json={'config':config or deepcopy(DEFAULT_CONFIG),'app_id':app_id})

def test_preview_uses_types_keys_exclusions_and_original_values(client):
    c,folder=client
    before={'items':[{'id':'x','v':1},{'id':'y','v':2}],'score':.42,'gone':None,'typed':False,'ignored':1}
    after={'items':[{'id':'y','v':2},{'id':'x','v':1}],'score':.58,'new':None,'typed':'false','ignored':2}
    docs(folder,before,after)
    cfg={'exclude_paths':['ignored'],'arrays':{'items':{'mode':'key','keys':['id']}},'buckets':{'score':[.3,.6,1]}}
    response=call(c,cfg);assert response.status_code==200,response.text
    result=response.json();assert result['valid'] and result['changed']==4 and result['app_id']=='A'
    rows={r['path']:r for r in result['items']}
    assert rows['score']['before']['adjusted']==rows['score']['after']['adjusted']=='(0.3; 0.6]'
    assert rows['score']['change']=='VALUE_CHANGED'
    assert rows['gone']['after'] is None and rows['gone']['before']['type']=='null'
    assert rows['typed']['change']=='VALUE_CHANGED'
    assert 'ignored' not in rows and rows['items.v']['change']=='SAME'
    assert c.get('/api/config').json()==api.RunConfig(**DEFAULT_CONFIG).model_dump() or c.get('/api/config').json()==DEFAULT_CONFIG
    assert store.list_runs()==[] and store.list_jobs()==[]

def test_missing_rules_duplicate_keys_and_value_mode(client):
    c,folder=client;docs(folder,{'items':[{'id':1},{'id':1}]},{'items':[{'id':1}]})
    cfg={'exclude_paths':[],'arrays':{}}
    result=call(c,cfg).json();assert not result['valid'] and result['array_paths']==['items']
    assert 'Не задан режим' in result['errors'][0]['message']
    cfg['arrays']={'items':{'mode':'key','keys':['id']}}
    result=call(c,cfg).json();assert 'Повтор ключа' in result['errors'][0]['message']
    cfg['arrays']['items']={'mode':'value'}
    assert 'скаляры' in call(c,cfg).json()['errors'][0]['message']
    docs(folder,{'items':['a','b','b']},{'items':['b','a']})
    result=call(c,cfg).json();assert result['valid'] and result['changed']==0
    cfg['arrays']['items']={'mode':'index'}
    assert call(c,cfg).json()['changed']==3

def test_private_dataset_and_invalid_id_are_not_readable(client):
    c,folder=client;docs(folder,{'v':1},{'v':2})
    # A guessed upload directory never becomes shared, even without registry metadata.
    (folder/'datasets/sample').rename(folder/'datasets/upload-secret')
    assert call(c,dataset='upload-secret').status_code==404
    (folder/'datasets/upload-secret').rename(folder/'datasets/sample')
    assert call(c,app_id='../A').status_code==404
    assert call(c,app_id='missing').status_code==404
    c.post('/api/auth/logout')
    assert call(c).status_code==401

def test_preview_caps_input_nodes_rows_and_values(client):
    c,folder=client
    docs(folder,{'huge':'x'*(1024**2)},{'v':1})
    assert call(c).status_code==413
    docs(folder,{'items':list(range(20001))},{'v':1})
    assert call(c).status_code==413
    docs(folder,{f'p{i}':'x'*300 for i in range(130)},{f'p{i}':'y'*300 for i in range(130)})
    result=call(c).json();assert result['valid'] and result['total']==130 and result['changed']==130
    assert len(result['items'])==100 and result['truncated']
    assert result['items'][0]['before']['truncated'] and len(result['items'][0]['before']['value'])==240

def test_preview_limits_expansion_from_large_repeated_array_keys(client):
    c,folder=client;doc={'items':[{'id':'x'*10000,'values':list(range(1000))}]};docs(folder,doc,doc)
    cfg={'exclude_paths':[],'arrays':{'items':{'mode':'key','keys':['id']},'items.values':{'mode':'index'}}}
    result=call(c,cfg).json();assert not result['valid']
    assert 'слишком велик' in result['errors'][0]['message']

@pytest.mark.parametrize('patch',[
    {'exclude_paths':['','a']}, {'exclude_paths':['a','a']}, {'exclude_paths':['a..b']},
    {'arrays':{'a':{'mode':'key','keys':['id','id']}}}, {'arrays':{'a':{'mode':'key','keys':[' ']}}},
    {'arrays':{' a':{'mode':'index'}}}, {'buckets':{'a':[2,1]}}, {'buckets':{'a':[1,1]}},
    {'buckets':{'a.':[1,2]}}, {'buckets':{'a':list(range(101))}},
    {'check_params':{'max_drop_rel':2}},
])
def test_invalid_configuration_rejected(patch):
    with pytest.raises(ValidationError):api.RunConfig(**{**deepcopy(DEFAULT_CONFIG),**patch})

def test_invalid_config_cannot_replace_saved_settings(client):
    c,_=client;original=c.get('/api/config').json()
    bad={**original,'buckets':{'score':[.6,.3]}}
    assert c.put('/api/config',json=bad).status_code==422
    assert c.get('/api/config').json()==original
    good={**original,'buckets':{'score':[.3,.6,1]}}
    assert c.put('/api/config',json=good,headers={'If-Match':c.get('/api/config').headers['etag']}).status_code==200
    assert c.get('/api/config').json()['buckets']==good['buckets']
