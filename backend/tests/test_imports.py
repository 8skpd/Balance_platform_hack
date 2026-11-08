import hashlib
import io
import json
import os
import stat
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

import duckdb
import anyio
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from starlette.requests import Request, ClientDisconnect

from app import api, auth, datasets, imports, queueing, store, tasks
from app.normalizer import DEFAULT_CONFIG

HEADERS = {'X-Balance-Request': '1'}

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "app.sqlite3"}', connect_args={'check_same_thread':False,'timeout':30})
    monkeypatch.setattr(store, 'engine', engine)
    monkeypatch.setattr(api, 'bootstrap', lambda: None)
    for folder in ('uploads','datasets','jobs','runs'):
        (tmp_path / folder).mkdir()
    for module in (imports, datasets):
        monkeypatch.setattr(module, 'DATASETS', tmp_path / 'datasets')
    for module in (api, tasks, queueing):
        monkeypatch.setattr(module, 'RUNS', tmp_path / 'runs')
    monkeypatch.setattr(imports, 'UPLOADS', tmp_path / 'uploads')
    monkeypatch.setattr(tasks, 'JOBS', tmp_path / 'jobs')
    monkeypatch.setenv('BALANCE_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "app.sqlite3"}')
    store.init();auth.attempts.clear()
    yield tmp_path
    engine.dispose()

def archive(entries=None):
    out=io.BytesIO()
    if entries is None:
        entries=[('dataset/RESULTS/'+side+'/A.json', json.dumps({'appData':{'application_id':'A'},'value':index,'items':[1,2]}))
                 for index,side in enumerate(('BEFORE_MASTER','AFTER_BETA'))]
        entries += [('dataset/manifest.json','{"source":"test"}'),('README.txt','ignored')]
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name,body in entries:z.writestr(name,body)
    return out.getvalue()

def register(client,name):
    from account_helpers import account
    return account(client,name,'Upload-test-password-42')

def upload(client, raw=None):
    return client.post('/api/dataset-imports?name=Test&filename=test.zip',content=archive() if raw is None else raw)

def pending(owner, raw=None):
    raw=archive() if raw is None else raw
    upload_id=str(uuid.uuid4());imports.upload_path(upload_id).write_bytes(raw)
    return imports.enqueue({'owner_id':owner,'name':'Test','filename':'test.zip','upload_id':upload_id,'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})

def validate(job, tmp_path):
    job=queueing.claim(job['id']);work=tmp_path/job['id'];work.mkdir()
    result=imports.execute(job['id'],work)
    return job, work, result

def test_upload_publish_privacy_and_raw_source(isolated):
    with TestClient(api.app,headers=HEADERS) as a, TestClient(api.app,headers=HEADERS) as b:
        alice=register(a,'alice');register(b,'bob')
        r=upload(a);assert r.status_code==202,r.text
        value=r.json();dataset_id=value['id']
        assert a.get('/api/datasets').json()==[]
        assert b.get('/api/dataset-imports').json()==[]
        assert b.get('/api/dataset-imports/'+dataset_id).status_code==404
        assert b.get('/api/jobs/'+value['job_id']).status_code==404
        assert b.post('/api/jobs/'+value['job_id']+'/cancel').status_code==404
        body={'dataset':dataset_id,'config':DEFAULT_CONFIG}
        assert a.post('/api/runs',json=body).status_code==404
        job,work,result=validate(store.get_job(value['job_id']),isolated)
        assert result['pairs']==1 and result['array_paths']==['items'] and result['ignored_files']==1
        queueing.finish(job,'completed',result=result,artifact=work/'dataset')
        assert a.get('/api/dataset-imports/'+dataset_id).json()['progress']==100
        assert a.get('/api/datasets').json()[0]['visibility']=='private'
        assert b.get('/api/datasets').json()==[]
        for candidate in (dataset_id,'./'+dataset_id,dataset_id+'/', 'x/../'+dataset_id):
            assert b.post('/api/runs',json={**body,'dataset':candidate}).status_code==404
        created=a.post('/api/runs',json=body);assert created.status_code==201,created.text
        run_id=created.json()['id'];store.patch_run(run_id,status='completed')
        with duckdb.connect(str(isolated/'runs'/run_id/'result.duckdb'), config={'threads':1}) as conn:
            conn.execute("CREATE TABLE applications AS SELECT 'A' APP_ID")
        assert a.get(f'/api/runs/{run_id}/applications/A/source').status_code==200
        assert b.get(f'/api/runs/{run_id}/applications/A/source').status_code==404
        assert datasets.paths(dataset_id,alice['id']).is_dir()
        with pytest.raises(ValueError):datasets.paths(dataset_id)
        # A second upload with the same name creates an independent immutable dataset.
        second=upload(a);assert second.status_code==202 and second.json()['id']!=dataset_id

@pytest.mark.parametrize('raw,match',[
    (b'not a zip','ZIP'),
    (archive([('RESULTS/BEFORE_MASTER/A.json','{}')]),'Непарные'),
    (archive([('RESULTS/BEFORE_MASTER/A.json','{}'),('RESULTS/AFTER_BETA/B.json','{}')]),'Непарные'),
    (archive([('../escape','bad')]),'Небезопасный'),
    (archive([('/escape','bad')]),'Небезопасный'),
    (archive([('x\\escape','bad')]),'Небезопасный'),
    (archive([('A','a'),('a','b')]),'Повтор пути'),
    (archive([('RESULTS/BEFORE_MASTER/A.json','{}'),('other/RESULTS/AFTER_BETA/A.json','{}')]),'ровно один'),
    (archive([('RESULTS/BEFORE_MASTER/A.txt','{}')]),'Ожидается'),
])
def test_invalid_archives_never_published(isolated,raw,match):
    job=pending('alice',raw)
    with pytest.raises(ValueError,match=match):validate(job,isolated)
    assert list((isolated/'datasets').iterdir())==[]
    assert datasets.inventory('alice')==[]

@pytest.mark.parametrize('document,match',[
    (b'[]','объектом'), (b'{"a":1,"a":2}','Повтор ключа'), (b'{"a":NaN}','число'),
    (b'{"a":1e999}','число'), (b'{"a":"\xff"}','decode'),
    (b'{"appData":{"application_id":"B"}}','не совпадает'), (b'{"appData":[]}','appData'),
    (b'{"decisionResult":[]}','decisionResult'),
    (('{"a":'*70+'1'+'}'*70).encode(),'сложность'),
])
def test_invalid_json(isolated,document,match):
    job=pending('alice',archive([('RESULTS/'+s+'/A.json',document) for s in ('BEFORE_MASTER','AFTER_BETA')]))
    with pytest.raises(ValueError,match=match):validate(job,isolated)

def test_bom_missing_ids_and_symlink_rejection(isolated):
    raw=b'\xef\xbb\xbf{"x":1}'
    job,work,result=validate(pending('alice',archive([('RESULTS/'+s+'/A.json',raw) for s in ('BEFORE_MASTER','AFTER_BETA')])),isolated)
    assert result['missing_application_ids']==2
    assert json.loads((work/'dataset/RESULTS/BEFORE_MASTER/A.json').read_text())=={'x':1}
    info=zipfile.ZipInfo('link');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
    with zipfile.ZipFile(io.BytesIO(archive([(info,'target')]))) as z:
        with pytest.raises(ValueError,match='Ссылки'):imports.safe_entries(z)

def test_archive_limits_and_compression(isolated,monkeypatch):
    raw=archive()
    for setting,value,match in [('IMPORT_FILE_BYTES',1,'Файл в ZIP превышает'),('IMPORT_MAX_BYTES',1,'1 ГБ'),('IMPORT_MAX_FILES',-99,'много записей')]:
        with monkeypatch.context() as m:
            m.setattr(imports,setting,value)
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                with pytest.raises(ValueError,match=match):imports.safe_entries(z)
    with zipfile.ZipFile(io.BytesIO(archive([('bomb','a'*1000000)]))) as z:
        with pytest.raises(ValueError,match='степень сжатия'):imports.safe_entries(z)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_BZIP2) as z:z.writestr('file','data')
    with zipfile.ZipFile(io.BytesIO(out.getvalue())) as z:
        with pytest.raises(ValueError,match='Store'):imports.safe_entries(z)

def test_upload_cleanup_limits_and_anonymous(isolated,monkeypatch):
    with TestClient(api.app,headers=HEADERS) as c:
        assert upload(c).status_code==401
        register(c,'alice')
        assert upload(c,b'').status_code==422
        assert not list(imports.UPLOADS.iterdir())
        monkeypatch.setattr(imports,'UPLOAD_MAX_BYTES',10)
        assert upload(c).status_code==413
        response=c.post('/api/dataset-imports?name=Test&filename=test.zip',content=iter([b'123456',b'123456']))
        assert response.status_code==413,response.text
        assert not list(imports.UPLOADS.iterdir())

def test_queue_limit_is_atomic_and_retry_is_new_dataset(isolated):
    with TestClient(api.app,headers=HEADERS) as c:
        owner=register(c,'alice')['id']
        def create(_):
            try:return pending(owner)
            except HTTPException as exc:return exc.status_code
        with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(create,range(6)))
        jobs=[j for j in results if isinstance(j,dict)]
        assert len(jobs)==3 and results.count(429)==3
        source=imports.record(jobs[0]['dataset_id'])
        assert c.post('/api/jobs/'+jobs[0]['id']+'/retry').status_code==409
        queueing.cancel(jobs[0]['id'])
        response=c.post('/api/jobs/'+jobs[0]['id']+'/retry');assert response.status_code==202,response.text
        new=response.json();assert new['dataset_id']!=jobs[0]['dataset_id']
        assert imports.record(new['dataset_id'])['upload_id']==source['upload_id']

def test_cancel_and_stale_lease_cannot_publish(isolated):
    job,work,result=validate(pending('alice'),isolated)
    queueing.cancel(job['id'])
    queueing.finish(job,'completed',result=result,artifact=work/'dataset')
    assert store.get_job(job['id'])['status']=='cancelled'
    assert datasets.inventory('alice')==[]
    assert not (isolated/'datasets'/job['dataset_id']).exists()
    assert not queueing.finish(job,'completed',result=result,artifact=work/'dataset')

def test_publish_recovers_rename_before_commit(isolated):
    job,work,result=validate(pending('alice'),isolated)
    imports.publish(job,work/'dataset',result)
    assert datasets.inventory('alice')==[]
    work2=isolated/'retry';work2.mkdir()
    result2=imports.execute(job['id'],work2)
    queueing.finish(job,'completed',result=result2,artifact=work2/'dataset')
    assert len(datasets.inventory('alice'))==1
    assert not (work2/'dataset').exists()

def test_real_supervisor_failure_and_success(isolated):
    good=pending('alice');tasks.execute(good['id'])
    assert store.get_job(good['id'])['status']=='completed',store.get_job(good['id'])['error']
    bad=pending('alice',b'broken');tasks.execute(bad['id'])
    assert store.get_job(bad['id'])['status']=='failed'
    assert len(datasets.inventory('alice'))==1
    assert not list((isolated/'jobs').glob('**/dataset'))
    assert imports.upload_path(imports.record(bad['dataset_id'])['upload_id']).exists()

def test_disconnect_removes_partial_upload_and_releases_slot(isolated):
    messages=iter([{'type':'http.request','body':b'partial','more_body':True},{'type':'http.disconnect'}])
    async def receive():return next(messages)
    request=Request({'type':'http','method':'POST','headers':[]},receive)
    with pytest.raises(ClientDisconnect):
        anyio.run(imports.upload,request,'Test','test.zip',{'id':'alice'})
    assert not list(imports.UPLOADS.iterdir())
    assert imports.upload_slots.acquire(blocking=False)
    assert imports.upload_slots.acquire(blocking=False)
    imports.upload_slots.release();imports.upload_slots.release()

def test_http_queue_limit_does_not_leave_unregistered_source(isolated):
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'alice')
        for _ in range(3):assert upload(client).status_code==202
        assert upload(client).status_code==429
        assert len(list(imports.UPLOADS.iterdir()))==3

def test_source_checksum_and_existing_target_are_not_overwritten(isolated):
    job=pending('alice');source=imports.upload_path(imports.record(job['dataset_id'])['upload_id'])
    source.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='Контрольная сумма'):validate(job,isolated)
    good,work,result=validate(pending('alice'),isolated)
    target=isolated/'datasets'/good['dataset_id'];target.mkdir();(target/'manifest.json').write_text('{}')
    with pytest.raises(ValueError,match='перезапись запрещена'):
        queueing.finish(good,'completed',result=result,artifact=work/'dataset')
    assert (target/'manifest.json').read_text()=='{}'
    assert datasets.inventory('alice')==[]

def test_stale_cleanup_keeps_registered_and_recent_uploads(isolated):
    job=pending('alice');registered=imports.upload_path(imports.record(job['dataset_id'])['upload_id'])
    old=imports.UPLOADS/(str(uuid.uuid4())+'.part');old.write_bytes(b'partial')
    recent=imports.UPLOADS/(str(uuid.uuid4())+'.zip');recent.write_bytes(b'recent')
    for path in (registered,old):os.utime(path,(time.time()-90000,time.time()-90000))
    imports.cleanup_stale_uploads()
    assert registered.exists() and recent.exists() and not old.exists()

def test_array_inventory_merges_different_documents_before_truncation(isolated):
    entries=[('RESULTS/BEFORE_MASTER/A.json',json.dumps({f'a{i:03d}':[] for i in range(150)})),
             ('RESULTS/AFTER_BETA/A.json',json.dumps({f'a{i:03d}':[] for i in range(50,200)}))]
    _,_,result=validate(pending('alice',archive(entries)),isolated)
    assert len(result['array_paths'])==200
    assert result['array_paths'][0]=='a000' and result['array_paths'][-1]=='a199'

# WinRAR/Windows packages can store legacy names plus the standard 0x7075 field.
def unicode_path_archive(unicode_name, legacy_name='README.txt', with_data=False):
    import struct,zlib
    raw_name=legacy_name.encode('cp866')
    class LegacyInfo(zipfile.ZipInfo):
        def _encodeFilenameFlags(self):
            return raw_name,self.flag_bits & ~0x800
    info=LegacyInfo(legacy_name)
    payload=b'\x01'+struct.pack('<I',zlib.crc32(raw_name))+unicode_name.encode('utf-8')
    info.extra=struct.pack('<HH',0x7075,len(payload))+payload
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr(info,'documentation')
        if with_data:
            for side in ('BEFORE_MASTER','AFTER_BETA'):
                z.writestr('RESULTS/'+side+'/A.json','{"value":1}')
    return out.getvalue()

def test_unicode_path_metadata_is_not_path_traversal(isolated):
    raw=unicode_path_archive('01_ПЛАН_ЗАДАНИЯ.md','01_ПЛАН_ЗАДАНИЯ.md',True)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        assert z.infolist()[0].orig_filename!=z.infolist()[0].filename
        assert len(imports.safe_entries(z))==3
        assert z.read(z.infolist()[0])==b'documentation'
    _,_,result=validate(pending('alice',raw),isolated)
    assert result['pairs']==1

@pytest.mark.parametrize('name',['../escape','/absolute','x\\escape','C:/escape','safe\x00../escape'])
def test_unicode_metadata_cannot_hide_unsafe_paths(isolated,name):
    with zipfile.ZipFile(io.BytesIO(unicode_path_archive(name))) as z:
        with pytest.raises(ValueError,match='Небезопасный путь'):imports.safe_entries(z)

def test_unicode_metadata_cannot_hide_unsafe_legacy_path(isolated):
    with zipfile.ZipFile(io.BytesIO(unicode_path_archive('safe.txt','../escape'))) as z:
        with pytest.raises(ValueError,match='Небезопасный путь'):imports.safe_entries(z)

def test_assignment_wrapper_explains_which_zip_to_upload(isolated):
    raw=archive([('01_ПЛАН_ЗАДАНИЯ.md','docs'),('ПАКЕТ/ЗАДАНИЕ.zip',archive()),('ПАКЕТ/ОРГАНИЗАТОРАМ.zip','other')])
    with pytest.raises(ValueError,match='Распакуйте его и загрузите вложенный ПАКЕТ/ЗАДАНИЕ.zip'):
        validate(pending('alice',raw),isolated)
