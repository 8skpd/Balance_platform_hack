import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,update
from app import api,store,procedures,library
from app.code_validation import analyze
from app.normalizer import DEFAULT_CONFIG
from test_library import isolated,register,HEADERS,CODE

@pytest.mark.parametrize('stage',list(procedures.STAGES))
def test_standard_procedures(stage):
    assert analyze(stage,procedures.EXAMPLES[stage])['valid']

def test_all_supplied_checks():
    for spec in library.builtin_catalog():
        assert analyze('checks',spec['code'])['valid'],spec['name']
    assert analyze('checks',CODE)['valid']

@pytest.mark.parametrize('code,message',[
    ('def broken(', 'Ошибка Python'),
    ('return []','Ошибка Python'),
    ('def run(a,a): pass','Ошибка Python'),
    ('pass','Добавьте CHECK'),
    ('CHECK=[]','должен быть словарём'),
    ('CHECK={}','отсутствует'),
    ('CHECK={"entrypoint":42}','ссылаться на функцию'),
    ('CHECK={"entrypoint":missing_function}','не определена'),
    ('def run(a): return []\nCHECK={"entrypoint":run}','2 позиционными'),
    ('def run(a,b,*,required): return []\nCHECK={"entrypoint":run}','2 позиционными'),
    ('async def run(a,b): return []\nCHECK={"entrypoint":run}','без async'),
    ('def run(a,b): pass\nCHECK={"entrypoint":run}','не возвращает'),
    ('CHECK={"entrypoint":lambda a,b:None}','скаляр'),
    ('CHECK={"entrypoint":lambda a,b:{}}','коллекцию строк'),
    ('CHECK={"entrypoint":lambda a,b:[{}]}','отсутствуют поля'),
    ('def run(a,b): yield {}\nCHECK={"entrypoint":run}','без yield'),
    ('CHECK={"entrypoint":lambda a,b:[],"params":[]}','должен быть словарём'),
])
def test_invalid_contracts(code,message):
    result=analyze('checks',code)
    assert not result['valid']
    assert any(message in d['message'] for d in result['diagnostics'])
    assert all(d['line']>=1 and d['column']>=1 for d in result['diagnostics'])

@pytest.mark.parametrize('signature',['a,b','a,b,c=1','*args','a,*args,optional=1','a,b,/,**kwargs'])
def test_compatible_signatures(signature):
    assert analyze('checks',f'def fn({signature}): return []\nCHECK={{"entrypoint":fn}}')['valid']

def test_segments_and_literal_output():
    code=procedures.EXAMPLES['normalization'].replace(', "segments": segments','')
    assert not analyze('normalization',code)['valid']
    bad=CODE.replace("'RESULT':'FAIL'","'RESULT':'BROKEN'")
    assert any('RESULT' in d['message'] for d in analyze('checks',bad)['diagnostics'])
    assert not analyze('normalization',procedures.EXAMPLES['normalization'].replace('return defaults.normalize(document, app_id, source, config)','return [{"APP_ID":app_id}]'))['valid']
    assert not analyze('checks',CODE.replace("'CNT':len(apps)","'CNT':'wrong'"))['valid']

def test_dynamic_contract_is_honestly_reported_and_source_never_runs(tmp_path):
    target=tmp_path/'executed'
    result=analyze('checks',f'open({str(target)!r},"w").write("unsafe")\nCHECK=dict(entrypoint=lambda m,p:[])')
    assert result['valid'] and result['diagnostics'][0]['severity']=='warning'
    assert not target.exists()

def test_api_and_run_gate_reject_old_invalid_sources_before_queue(isolated):
    with TestClient(api.app,headers=HEADERS) as client:
        assert client.post('/api/code/validate',json={'stage':'checks','code':CODE}).status_code==401
        register(client,'validator')
        r=client.post('/api/code/validate',json={'stage':'checks','code':'CHECK={}'})
        assert r.status_code==200 and not r.json()['valid']
        assert client.post('/api/code/validate',json={'stage':'unknown','code':CODE}).status_code==422
        assert client.post('/api/procedures',json={'stage':'comparison','name':'Bad','code':'pass'}).status_code==422
        assert client.post('/api/check-library',json={'name':'Bad','code':'CHECK={}'}).status_code==422
        row=client.post('/api/procedures',json={'stage':'comparison','name':'Old','code':procedures.EXAMPLES['comparison']}).json()
        spec={'dataset':'test','config':DEFAULT_CONFIG,'builtin_checks':[],'user_checks':[], 'procedures':{'comparison':{'id':row['id'],'revision':1}}}
        assert client.post('/api/runs/validate',json=spec).json()['valid']
        with store.engine.begin() as conn:
            assert not conn.execute(select(store.jobs)).first()
            conn.execute(update(store.procedure_library).where(store.procedure_library.c.id==row['id']).values(code='PROCEDURE={}'))
        assert client.post('/api/runs/validate',json=spec).status_code==422
        assert client.post('/api/runs',json=spec).status_code==422
        with store.engine.connect() as conn:
            assert not conn.execute(select(store.jobs)).first()
            assert not conn.execute(select(store.runs)).first()
        # Historical run snapshots are validated again on retry, even when the
        # source is supplied directly instead of resolved from the library.
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            api.enqueue_run(api.NewRun(**spec),{'id':'validator'},[],procedure_plan={'comparison':{'kind':'python','code':'PROCEDURE={}'}})
        assert exc.value.status_code==422

def test_observer_cannot_validate_or_view_sources(isolated):
    with TestClient(api.app,headers=HEADERS) as client:
        user=register(client,'viewer')
        with store.engine.begin() as conn:conn.execute(update(store.users).where(store.users.c.username=='viewer').values(role='observer'))
        assert client.post('/api/code/validate',json={'stage':'checks','code':CODE}).status_code==403
        assert client.get('/api/check-library/builtin/'+library.builtin_catalog()[0]['key']).status_code==403

@pytest.mark.parametrize('stage,code,signature',[
    ('checks','def run(mart):\n    return []\nCHECK={"entrypoint":run}', 'run(mart, params)'),
    ('checks','# Русский 😀\ndef проверить(данные,):\n    return [] # тело сохранено\nCHECK={"entrypoint":проверить}', 'проверить(данные, params)'),
    ('comparison','def run():\n    return []\nPROCEDURE={"entrypoint":run}', 'run(data, params)'),
    ('aggregation','def run(data):\n    return []\nPROCEDURE={"entrypoint":run}', 'run(data, params)'),
    ('normalization','def run(document):\n    return []\ndef segments(app_id, before, after, config, params):\n    return dict()\nPROCEDURE={"entrypoint":run,"segments":segments}', 'run(document, app_id, source, config, params)'),
    ('normalization','def run(*args):\n    return []\ndef segments(app_id, before, after):\n    return dict()\nPROCEDURE={"entrypoint":run,"segments":segments}', 'segments(app_id, before, after, config, params)'),
])
def test_signature_fix_preserves_source_and_revalidates(stage,code,signature):
    result=analyze(stage,code)
    fix=next(d['fix'] for d in result['diagnostics'] if 'fix' in d)
    assert fix['preview']==signature
    source=code.encode('utf-16-le')
    fixed=(source[:fix['from']*2]+fix['insert'].encode('utf-16-le')+source[fix['to']*2:]).decode('utf-16-le')
    assert analyze(stage,fixed)['valid']
    assert fixed.replace(fix['insert'],'',1)==code

@pytest.mark.parametrize('signature',['params','mart,params,required','mart=1','mart,*,params','mart,\n','mart: tuple[int,int],\n'])
def test_ambiguous_or_complex_signature_has_no_automatic_edit(signature):
    result=analyze('checks',f'def run({signature}):\n    return []\nCHECK={{"entrypoint":run}}')
    assert not any('fix' in d for d in result['diagnostics'])

def test_fix_does_not_overwrite_local_binding():
    assert not any('fix' in d for d in analyze('checks','def run(mart):\n    params=1\n    return []\nCHECK={"entrypoint":run}')['diagnostics'])
