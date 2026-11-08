"""End-to-end state/permission invariants for staff lifecycle and shared review."""
import json
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select,update
from app import api,store,auth,queueing,operations,workflow,comparisons,storage,backups,maintenance
from test_library import isolated,register,new_run,execute,HEADERS
from test_access import role

@pytest.fixture
def setup(isolated,monkeypatch):
    from app import settings,imports
    for module in (operations,storage,backups,maintenance,settings):
        if hasattr(module,'DATA'):monkeypatch.setattr(module,'DATA',isolated)
        for attr,name in [('RUNS','runs'),('JOBS','jobs'),('DATASETS','datasets'),('UPLOADS','uploads')]:
            if hasattr(module,attr):monkeypatch.setattr(module,attr,isolated/name)
    monkeypatch.setattr(comparisons,'RUNS',isolated/'runs')
    (isolated/'uploads').mkdir(exist_ok=True)
    monkeypatch.setattr(imports,'UPLOADS',isolated/'uploads')
    yield isolated

def share(client,rid,username):
    r=client.post(f'/api/runs/{rid}/shares',json={'username':username});assert r.status_code==201,r.text

def transfer(client,run,name,retain=True):
    return client.post(f"/api/runs/{run['id']}/transfer",json={'username':name,'expected_owner_id':run['owner_id'],'retain_previous':retain,'reason':'Продолжение работы'})

def test_block_revoke_unblock_last_active_admin_and_audit(setup):
    with TestClient(api.app,headers=HEADERS) as a,TestClient(api.app,headers=HEADERS) as b:
        alice=register(a,'alice');bob=register(b,'bob');run=new_run(b)
        token=b.cookies.get(auth.COOKIE)
        body={'blocked':True,'expected_blocked':False,'reason':'Увольнение'}
        assert b.put(f"/api/admin/users/{alice['id']}/state",json=body).status_code==403
        assert a.put(f"/api/admin/users/{alice['id']}/state",json=body).status_code==409
        assert a.put(f"/api/admin/users/{bob['id']}/state",json=body).status_code==200
        assert b.get('/api/auth/me').status_code==401
        assert store.list_jobs(run_id=run['id'])[0]['status']=='cancelled'
        credentials={'username':'bob','password':'Library-test-password-42'}
        assert b.post('/api/auth/login',json=credentials).status_code==401
        assert a.put(f"/api/admin/users/{bob['id']}/state",json={**body,'blocked':False,'expected_blocked':True}).status_code==200
        assert b.get('/api/auth/me').status_code==401
        assert b.post('/api/auth/login',json=credentials).status_code==200
        assert b.cookies.get(auth.COOKIE)!=token
        assert a.post(f"/api/admin/users/{bob['id']}/revoke-sessions",json={'reason':'Перевход'}).status_code==200
        assert b.get('/api/auth/me').status_code==401
        assert b.post('/api/auth/login',json=credentials).status_code==200
        events=a.get('/api/admin/audit').json()
        assert {'account.block','account.unblock','account.revoke_sessions'}<={e['action'] for e in events['items']}
        assert 'password' not in json.dumps(events) and token not in json.dumps(events)
        assert b.get('/api/admin/audit').status_code==403


def test_concurrent_blocking_keeps_active_administrator(setup):
    with TestClient(api.app,headers=HEADERS) as a,TestClient(api.app,headers=HEADERS) as b:
        alice=register(a,'alice');bob=register(b,'bob');role(a,bob,'admin');bob['role']='admin'
        body=operations.AccountState(blocked=True,expected_blocked=False,reason='test')
        def block(user):
            try:operations.account_state(user['id'],body,user);return 200
            except HTTPException as exc:return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool: assert sorted(pool.map(block,[alice,bob]))==[200,409]


def test_shared_comments_review_observer_read_only_and_revoke(setup):
    with TestClient(api.app,headers=HEADERS) as a,TestClient(api.app,headers=HEADERS) as b,TestClient(api.app,headers=HEADERS) as c:
        alice=register(a,'alice');bob=register(b,'bob');charlie=register(c,'charlie');role(a,charlie,'observer')
        run=execute(new_run(a));rid=run['id'];url=f'/api/runs/{rid}';share(a,rid,'bob');share(a,rid,'charlie')
        body={'target_kind':'application','target_id':'A','body':'Ожидается <b>новая</b> стратегия'}
        comment=b.post(url+'/comments',json=body);assert comment.status_code==201,comment.text
        cid=comment.json()['id']
        assert c.post(url+'/comments',json=body).status_code==403
        assert c.get(url+'/comments?target_kind=application&target_id=A').json()['items'][0]['body']==body['body']
        assert b.post(url+'/comments',json={**body,'target_id':'missing'}).status_code==404
        assert a.put(url+'/comments/'+cid,json={'body':'x','revision':1}).status_code==404
        assert b.put(url+'/comments/'+cid,json={'body':'Уточнение','revision':1}).status_code==200
        assert b.put(url+'/comments/'+cid,json={'body':'Потерянное изменение','revision':1}).status_code==409
        review={'status':'in_review','assignee':'bob','reason':'Проверить изменения','revision':0}
        assert b.put(url+'/review',json=review).status_code==403
        assert a.put(url+'/review',json=review).status_code==200
        resolved={**review,'revision':1,'status':'approved','reason':'Изменения ожидаемы'}
        assert a.put(url+'/review',json=resolved).status_code==403
        assert c.put(url+'/review',json=resolved).status_code==403
        assert b.put(url+'/review',json=resolved).status_code==200
        assert b.put(url+'/review',json=resolved).status_code==409
        assert c.get(url+'/review').json()['status']=='approved'
        assert c.get(url+'/activity').json()['total']>=4
        assert a.delete(url+'/shares/'+bob['id']).status_code==200
        assert b.put(url+'/review',json={**resolved,'revision':2}).status_code==404
        assert b.get(url+'/comments').status_code==404
        assert c.delete(url+'/comments/'+cid+'?revision=2').status_code==403


def test_transfer_private_source_jobs_and_recalculate(setup):
    with TestClient(api.app,headers=HEADERS) as a,TestClient(api.app,headers=HEADERS) as b:
        alice=register(a,'alice');bob=register(b,'bob');run=execute(new_run(a));rid=run['id']
        (setup/'datasets/test').rename(setup/'datasets/upload-private')
        with store.engine.begin() as conn:
            conn.execute(store.dataset_imports.insert().values(id='upload-private',owner_id=alice['id'],job_id='import-private',upload_id='00000000-0000-0000-0000-000000000001',name='Private',filename='private.zip',size=1,sha256='x',created_at=store.now()))
            conn.execute(store.jobs.insert().values(id='import-private',owner_id=alice['id'],run_id='',kind='import',dataset_id='upload-private',status='completed'))
        store.patch_run(rid,dataset='upload-private')
        result=transfer(a,run,'bob');assert result.status_code==200,result.text
        assert b.get(f'/api/runs/{rid}/applications/A/source').status_code==200
        assert a.get('/api/runs/'+rid).json()['can_manage']==False
        assert all(j['owner_id']==bob['id'] for j in store.list_jobs(run_id=rid))
        assert transfer(a,run,'bob').status_code==409
        repeat=b.post(f'/api/runs/{rid}/recalculate');assert repeat.status_code==201,repeat.text
        copied=execute(repeat.json());assert copied['status']=='completed',copied['error']
        assert copied['dataset_owner_id']==alice['id']
        assert a.post('/api/storage/cleanup',json={'kind':'dataset','id':'upload-private','confirmation':'Private'}).status_code==409
        assert b.post(f'/api/runs/{rid}/ai').status_code==202
        now=store.get_run(rid)
        assert transfer(b,now,'alice').status_code==409


def test_admin_recovery_does_not_grant_report_access(setup):
    with TestClient(api.app,headers=HEADERS) as a,TestClient(api.app,headers=HEADERS) as b,TestClient(api.app,headers=HEADERS) as c:
        register(a,'alice');bob=register(b,'bob');register(c,'charlie');run=execute(new_run(b))
        assert a.get('/api/runs/'+run['id']).status_code==404
        assert a.get(f"/api/admin/users/{bob['id']}/analyses").json()['items'][0]['id']==run['id']
        assert a.put(f"/api/admin/users/{bob['id']}/state",json={'blocked':True,'expected_blocked':False,'reason':'left'}).status_code==200
        assert transfer(a,run,'charlie',False).status_code==200
        assert c.get('/api/runs/'+run['id']).status_code==200
        assert a.get('/api/runs/'+run['id']).status_code==404


def test_compare_detects_equal_counts_different_values_and_both_acl(setup):
    with TestClient(api.app,headers=HEADERS) as a,TestClient(api.app,headers=HEADERS) as b:
        register(a,'alice');register(b,'bob');first=execute(new_run(a))
        source=setup/'datasets/test/RESULTS/AFTER_BETA/A.json';payload=json.loads(source.read_text());payload['value']=3;source.write_text(json.dumps(payload))
        second=execute(new_run(a));url=f"/api/run-comparison?left={first['id']}&right={second['id']}"
        result=a.get(url);assert result.status_code==200,result.text
        assert result.json()['counts']['changed']==1
        assert result.json()['items'][0]['left_changes']==result.json()['items'][0]['right_changes']
        share(a,first['id'],'bob');assert b.get(url).status_code==404
        share(a,second['id'],'bob');assert b.get(url).status_code==200
        assert a.get(url.replace(second['id'],first['id'])).json()['counts']=={'same':1}


def test_ai_history_snapshot_and_failed_attempts_survive_cleanup(setup):
    with TestClient(api.app,headers=HEADERS) as a,TestClient(api.app,headers=HEADERS) as b:
        register(a,'alice');bob=register(b,'bob');run=execute(new_run(a));rid=run['id'];share(a,rid,'bob');role(a,bob,'observer')
        requested=a.post(f'/api/runs/{rid}/ai');assert requested.status_code==202
        job=queueing.claim(requested.json()['job_id']);snapshot=job['params']['context']
        queueing.finish(job,'completed',result={'status':'completed','model':'snapshot-model','content':'Сохранённый ответ','created_at':store.now()})
        second=a.post(f'/api/runs/{rid}/ai');failed=queueing.claim(second.json()['job_id']);queueing.finish(failed,'failed',error='offline')
        entries=b.get(f'/api/runs/{rid}/ai-history').json()['items'];assert len(entries)==2
        assert entries[1]['context']==snapshot and entries[1]['result']['content']=='Сохранённый ответ'
        assert entries[0]['error']=='offline'
        assert b.post(f'/api/runs/{rid}/ai').status_code==403
        work=setup/'jobs'/job['id'];work.mkdir();(work/'log').write_text('log')
        assert a.post('/api/storage/cleanup',json={'kind':'job','id':job['id'],'confirmation':job['id']}).status_code==200
        assert not work.exists()
        assert b.get(f'/api/runs/{rid}/ai-history').json()['items'][1]['result']['content']=='Сохранённый ответ'


def test_quota_race_archive_pagination_cleanup(setup):
    with TestClient(api.app,headers=HEADERS) as a:
        alice=register(a,'alice');limits=a.get('/api/admin/limits').json();limits['max_active_jobs']=1
        assert a.put('/api/admin/limits',json=limits).status_code==200
        def launch(i):
            body=api.NewRun(dataset='test',name=f'Run {i}',config=api.DEFAULT_CONFIG,builtin_checks=[])
            try:return api.enqueue_run(body,alice,[])['id']
            except HTTPException as exc:return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(launch,range(2)))
        assert result.count(429)==1
        rid=next(x for x in result if isinstance(x,str));run=execute(store.get_run(rid))
        assert a.put(f'/api/runs/{rid}/archive',json={'archived':True}).status_code==200
        assert a.get('/api/run-history').json()['total']==0
        assert a.get('/api/run-history?archived=true&limit=1').json()['total']==1
        assert a.post(f'/api/runs/{rid}/recalculate').status_code==409
        assert a.post('/api/storage/cleanup',json={'kind':'run','id':rid,'confirmation':'wrong'}).status_code==422
        assert store.get_run(rid) and (setup/'runs'/rid/'result.duckdb').exists()
        assert a.post('/api/storage/cleanup',json={'kind':'run','id':rid,'confirmation':run['name']}).status_code==200
        assert not store.get_run(rid) and not (setup/'runs'/rid).exists()
        assert not store.list_jobs(run_id=rid)
        assert any(e['action']=='storage.cleanup' for e in a.get('/api/admin/audit').json()['items'])


def test_backup_roundtrip_probe_permissions_integrity_and_gate(setup):
    with TestClient(api.app,headers=HEADERS) as a,TestClient(api.app,headers=HEADERS) as b:
        register(a,'alice');register(b,'bob');run=execute(new_run(a))
        a.post(f"/api/runs/{run['id']}/comments",json={'body':'Восстановить обсуждение'})
        assert b.post('/api/admin/backups').status_code==403
        created=a.post('/api/admin/backups');assert created.status_code==202
        op=a.get('/api/admin/backups').json()[0];assert op['status']=='completed',op
        archive=backups.archive_path(op['archive_id'])
        destination=setup/'restore-test'
        restored=backups.restore_bundle(archive,destination)
        assert restored['api_probe'] and restored['reports_opened']==1
        with sqlite3.connect(destination/'app.sqlite3') as conn:
            assert conn.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
            assert conn.execute('SELECT body FROM comments').fetchone()[0]=='Восстановить обсуждение'
        with pytest.raises(ValueError,match='пустую'):backups.restore_bundle(archive,destination)
        assert b.get(f"/api/admin/backups/{op['archive_id']}/download").status_code==403
        assert a.post(f"/api/admin/backups/{op['archive_id']}/verify").status_code==202
        verified=a.get('/api/admin/backups').json()[0];assert verified['status']=='completed',verified
        corrupt=setup/'corrupt.zip'
        with zipfile.ZipFile(archive) as source,zipfile.ZipFile(corrupt,'w') as output:
            for name in source.namelist():output.writestr(name,b'broken' if name.endswith('A.json') else source.read(name))
        with pytest.raises(ValueError):backups.restore_bundle(corrupt,setup/'bad-restore')
        with maintenance.gate(exclusive=True):
            assert a.get('/api/runs').status_code==503
            assert a.get('/api/auth/me').status_code==200
            assert a.post('/api/runs',json={}).status_code==503
        assert a.get('/api/runs').status_code==200


def test_backup_rejects_active_work_and_path_traversal(setup):
    with TestClient(api.app,headers=HEADERS) as a:
        register(a,'alice');new_run(a)
        with pytest.raises(ValueError,match='активные'):backups.create_bundle()
    archive=setup/'evil.zip'
    with zipfile.ZipFile(archive,'w') as z:
        files={'app.sqlite3':{'size':0,'sha256':'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},'../outside':{'size':0,'sha256':''}}
        z.writestr('manifest.json',json.dumps({'format':1,'files':files}))
        z.writestr('app.sqlite3',b'');z.writestr('../outside',b'')
    with pytest.raises(ValueError):backups.restore_bundle(archive,setup/'evil-restore')
    assert not (setup/'outside').exists()


def test_cleanup_crash_recovery_restores_only_uncommitted_files(setup):
    import uuid
    for committed in (False,True):
        rid='crash-'+str(committed);source=setup/'runs'/rid;source.mkdir();(source/'report').write_text('important')
        folder=setup/'trash'/str(uuid.uuid4());folder.mkdir(parents=True)
        (folder/'journal.json').write_text(json.dumps({'id':rid,'kind':'run','paths':['runs/'+rid]}));source.rename(folder/'0')
        if committed:
            with store.engine.begin() as conn:
                conn.execute(store.audit_events.insert().values(created_at=store.now(),action='storage.cleanup',entity_id=rid,details={'cleanup_id':folder.name}))
        storage.recover_cleanup()
        assert source.exists()==(not committed)
        assert not folder.exists()


def test_quota_immutable_dataset_cache_still_counts_files(setup):
    folder=setup/'datasets/upload-cache';folder.mkdir()
    (folder/'data').write_bytes(b'x'*1024)
    assert operations.immutable_size(folder)==1024
    import shutil
    shutil.rmtree(folder)
    assert operations.immutable_size(folder)==0
    folder.mkdir();(folder/'data').write_bytes(b'y'*2048)
    assert operations.immutable_size(folder)==2048


def test_backup_restores_compact_sources_and_parallel_report(setup):
    from test_parallel_processing import make_dataset
    folder=setup/'datasets'/'packed';assert make_dataset(folder)=='sqlite'
    (folder/'manifest.json').write_text('{"name":"Packed fixture"}')
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'packedowner');run=execute(new_run(client,dataset='packed'))
        assert run['status']=='completed',run['error']
        bundle=backups.create_bundle();destination=setup/'restored-packed'
        result=backups.restore_bundle(backups.archive_path(bundle['archive_id']),destination)
        assert result['reports_opened']==1 and result['api_probe']
        from app.dataset_storage import read_raw
        assert json.loads(read_raw(destination/'datasets'/'packed','AFTER_BETA','A00000'))['score']==1
