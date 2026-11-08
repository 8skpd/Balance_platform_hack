import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update

from app import api, auth, queueing, sandbox, store, tasks

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "state.sqlite3"}', connect_args={'check_same_thread':False,'timeout':30})
    monkeypatch.setattr(store, 'engine', engine)
    monkeypatch.setattr(api, 'bootstrap', lambda:None)
    for module in (api, queueing, tasks):
        monkeypatch.setattr(module, 'RUNS', tmp_path / 'runs')
    monkeypatch.setattr(tasks, 'JOBS', tmp_path / 'jobs')
    (tmp_path / 'runs/r').mkdir(parents=True)
    (tmp_path / 'jobs').mkdir()
    report = tmp_path / 'runs/r/result.duckdb'
    with duckdb.connect(str(report), config={'threads':1}) as conn:
        conn.execute("CREATE TABLE applications AS SELECT 'A' APP_ID")
        conn.execute("CREATE TABLE segments AS SELECT 'A' APP_ID")
        conn.execute("CREATE TABLE comparison AS SELECT 'A' APP_ID, 1 DIFF_FLAG")
    store.init()
    store.create_run({'id':'r','owner_id':'alice','status':'completed','name':'Report','checks':[]})
    auth.attempts.clear()
    yield tmp_path
    engine.dispose()

def test_claim_once_and_cancellation_wins_publication(isolated):
    job = queueing.submit('alice', 'r', 'check', code='pass')
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(queueing.claim, [job['id']]*4))
    assert sum(c is not None for c in claims) == 1
    claimed = next(c for c in claims if c)
    assert queueing.cancel(job['id'])['status'] == 'cancelling'
    assert queueing.finish(claimed, 'completed', result={'rows':[]})
    assert store.get_job(job['id'])['status'] == 'cancelled'
    assert not queueing.finish(claimed, 'completed', result={'rows':[]})

def test_outbox_broker_failure_and_recovery(isolated, monkeypatch):
    job = queueing.submit('alice','r','check',code='pass')
    monkeypatch.setattr(queueing.celery, 'send_task', lambda *a,**k: (_ for _ in ()).throw(ConnectionError()))
    queueing.dispatch_pending()
    assert store.get_job(job['id'])['status'] == 'queued'
    with store.engine.begin() as conn:
        conn.execute(update(store.jobs).values(enqueued_at=0))
    sent = []
    monkeypatch.setattr(queueing.celery, 'send_task', lambda *a,**k:sent.append(k))
    queueing.dispatch_pending()
    assert sent[0]['args'] == [job['id']]

def test_dead_worker_retries_once_and_stale_result_rejected(isolated):
    job = queueing.submit('alice','r','check',code='pass')
    first = queueing.claim(job['id'])
    with store.engine.begin() as conn:
        conn.execute(update(store.jobs).values(heartbeat=time.time()-60))
    queueing.dispatch_pending()
    assert store.get_job(job['id'])['status'] == 'queued'
    assert not queueing.finish(first, 'completed', result={'rows':[]})
    second = queueing.claim(job['id'])
    assert second['attempts'] == 2
    with store.engine.begin() as conn:
        conn.execute(update(store.jobs).values(heartbeat=time.time()-60))
    queueing.dispatch_pending()
    assert store.get_job(job['id'])['status'] == 'failed'

def test_cancel_queued_never_executes(isolated):
    job = queueing.submit('alice','r','check',code='pass')
    queueing.cancel(job['id'])
    assert queueing.claim(job['id']) is None

def run_check(code, timeout=10, memory=1024):
    job = queueing.submit('alice','r','check',code=code,timeout=timeout,memory_mb=memory)
    tasks.supervise(queueing.claim(job['id']))
    return store.get_job(job['id'])

def test_real_sandbox_valid_result_and_clean_environment(isolated, monkeypatch):
    monkeypatch.setenv('BALANCE_TEST_SECRET', 'must-not-leak')
    result = run_check('''import os
assert 'BALANCE_TEST_SECRET' not in os.environ
def run(mart, params):
    assert mart.total_apps() == 1
    return [{'LABEL':'Example','ARGUMENT':'All','SOURCE':'AFTER','CNT':1,'TOTAL':1,'METRIC':1.0,
             'RESULT':'PASS','CONFIDENCE':'HIGH','APP_IDS':['A']}]
CHECK = {'entrypoint':run}
''')
    assert result['status'] == 'completed', result['error']
    assert result['result']['rows'][0]['APP_IDS'] == ['A']

@pytest.mark.parametrize('attack', [
    "open({secret}, 'r').read()",
    "open({secret}, 'w').write('changed')",
    "__import__('os').chmod({secret}, 0o777)",
    "__import__('os').setxattr({secret}, 'user.test', b'changed')",
    "__import__('socket').socket()",
    "__import__('os').fork()",
    "__import__('os').pipe()",
    "__import__('os').setuid(__import__('os').getuid())",
    "__import__('fcntl').fcntl(1, __import__('fcntl').F_SETOWN, __import__('os').getppid())",
    "__import__('os').execv('/usr/bin/true', ['true'])",
    "__import__('ctypes').CDLL(None).kill(__import__('os').getppid(), 9) == 0 or (_ for _ in ()).throw(PermissionError())",
    "mart.conn.execute(\"SELECT * FROM read_text(\" + {secret}.__repr__() + \")\").fetchall()",
])
def test_sandbox_denies_escape(isolated, attack):
    secret = isolated / 'private.txt'
    secret.write_text('private')
    code = "def run(mart, params):\n    " + attack.format(secret=repr(str(secret))) + "\n    return []\nCHECK={'entrypoint':run}"
    result = run_check(code)
    assert result['status'] == 'failed', result
    assert secret.read_text() == 'private'
    assert secret.stat().st_mode & 0o777 != 0o777

def test_timeout_memory_and_invalid_results(isolated):
    assert run_check('while True: pass', timeout=1)['status'] == 'timed_out'
    assert run_check("x = bytearray(3 * 1024**3)")['status'] == 'failed'
    assert run_check("CHECK={'entrypoint':lambda m,p:[{'LABEL':'bad'}]}")['status'] == 'failed'
    assert run_check("print('x' * (2*1024**2))\nCHECK={'entrypoint':lambda m,p:[]}")['status'] == 'failed'

def test_running_cancel_kills_child(isolated):
    job = queueing.submit('alice','r','check',code='while True: pass',timeout=20)
    claimed = queueing.claim(job['id'])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(tasks.supervise, claimed)
        time.sleep(1)
        queueing.cancel(job['id'])
        future.result(timeout=5)
    assert store.get_job(job['id'])['status'] == 'cancelled'

def test_job_access_and_api_restart(isolated, monkeypatch):
    monkeypatch.setattr(sandbox, 'availability', lambda:{'available':True})
    with TestClient(api.app, headers={'X-Balance-Request':'1'}) as alice, TestClient(api.app, headers={'X-Balance-Request':'1'}) as bob:
        users = []
        for client,name in [(alice,'alice'),(bob,'bob')]:
            from account_helpers import account
            users.append(account(client,name,'Queue-test-password-42'))
        store.patch_run('r',owner_id=users[0]['id'])
        response = alice.post('/api/runs/r/python-checks',json={'code':"CHECK={'entrypoint':lambda m,p:[]}"})
        assert response.status_code == 202
        job_id = response.json()['id']
        assert bob.get('/api/jobs').json() == []
        assert bob.get('/api/jobs/'+job_id).status_code == 404
        for action in ('cancel','retry'):
            assert bob.post('/api/jobs/'+job_id+'/'+action).status_code == 404
        assert bob.post('/api/runs/r/python-checks',json={'code':'pass'}).status_code == 404
        with TestClient(api.app) as restarted:
            assert store.get_job(job_id)['status'] == 'queued'
        assert alice.post('/api/jobs/'+job_id+'/cancel').json()['status'] == 'cancelled'
        retry = alice.post('/api/jobs/'+job_id+'/retry')
        assert retry.status_code == 202 and retry.json()['id'] != job_id
        assert alice.post('/api/runs/r/python-checks',json={'code':'pass','timeout':61}).status_code == 422
