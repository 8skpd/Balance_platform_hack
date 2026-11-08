import json
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, Table, create_engine, inspect, select, update

from app import api, auth, store
from app.normalizer import DEFAULT_CONFIG

HEADERS = {'X-Balance-Request': '1'}
PASSWORD = 'Test-only-password-42'

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "auth.sqlite3"}', connect_args={'check_same_thread': False, 'timeout': 30})
    monkeypatch.setattr(store, 'engine', engine)
    monkeypatch.setattr(api, 'RUNS', tmp_path)
    monkeypatch.setattr(api, 'bootstrap', lambda: None)
    auth.attempts.clear()
    store.init()
    yield engine
    engine.dispose()

def register(client, username, **extra):
    from account_helpers import account
    return account(client,username,PASSWORD,**extra)

def seed(owner, run_id='private-run'):
    store.create_run({'id': run_id, 'owner_id': owner, 'name': 'Private report', 'dataset': 'dataset',
                      'status': 'completed', 'created_at': store.now(), 'config': DEFAULT_CONFIG, 'checks': [],
                      'summary': {'total_apps': 1, 'changed_apps': 0, 'decision_changed': 0}})

def test_personal_lists_configs_and_all_run_endpoints(isolated):
    with TestClient(api.app, headers=HEADERS) as a, TestClient(api.app, headers=HEADERS) as b:
        alice = register(a, 'alice')
        bob = register(b, 'bob')
        seed(alice['id'])
        seed(bob['id'], 'bob-run')
        assert [r['id'] for r in a.get('/api/runs').json()] == ['private-run']
        assert [r['id'] for r in b.get('/api/runs').json()] == ['bob-run']
        cfg = deepcopy(DEFAULT_CONFIG)
        cfg['check_params']['max_drop_rel'] = .42
        assert a.put('/api/config', json=cfg, headers={'If-Match':a.get('/api/config').headers['etag']}).status_code == 200
        assert a.get('/api/config').json()['check_params']['max_drop_rel'] == .42
        assert b.get('/api/config').json()['check_params']['max_drop_rel'] == .1
        for suffix in ['', '/applications', '/applications/A', '/applications/A/source', '/pivot', '/report.html']:
            r = b.get('/api/runs/private-run' + suffix)
            assert r.status_code == 404, (suffix, r.text)
            assert 'Private report' not in r.text
        assert b.post('/api/runs/private-run/ai').status_code == 404
        assert a.get('/api/runs/private-run').status_code == 200
        assert a.get('/api/runs/private-run/report.html').status_code == 200

def test_authentication_cookie_expiry_logout_and_password_storage(isolated):
    with TestClient(api.app, headers=HEADERS) as client:
        for url in ['/api/runs', '/api/config', '/api/datasets', '/api/ollama', '/api/runs/anything/report.html']:
            assert client.get(url).status_code == 401
        user = register(client, 'alice')
        token = client.cookies.get(auth.COOKIE)
        with store.engine.connect() as conn:
            stored = conn.execute(select(store.users)).mappings().one()
            session = conn.execute(select(store.sessions)).mappings().one()
        assert stored['password_hash'] != PASSWORD
        assert auth.verify_password(PASSWORD, stored['password_hash'])
        assert token not in session.values()
        assert client.get('/api/auth/me').json() == user
        assert client.post('/api/auth/logout').status_code == 200
        client.cookies.set(auth.COOKIE, token)
        assert client.get('/api/auth/me').status_code == 401
        client.cookies.clear()
        wrong = client.post('/api/auth/login', json={'username': 'alice', 'password': 'wrong-password-long'})
        assert wrong.status_code == 401
        logged = client.post('/api/auth/login', json={'username': 'ALICE', 'password': PASSWORD})
        assert logged.status_code == 200
        assert 'HttpOnly' in logged.headers['set-cookie'] and 'SameSite=lax' in logged.headers['set-cookie']
        assert client.cookies.get(auth.COOKIE) != token
        with store.engine.begin() as conn:
            conn.execute(update(store.sessions).values(expires_at=int(time.time())-1))
        assert client.get('/api/runs').status_code == 401

def test_duplicate_account_validation_and_csrf(isolated):
    with TestClient(api.app, headers=HEADERS) as client:
        register(client, 'alice')
        assert client.post('/api/admin/users', json={'username':'ALICE','display_name':'Second','password':PASSWORD}).status_code == 409
        weak = client.post('/api/admin/users', json={'username':'new','display_name':'New','password':'secret'})
        assert weak.status_code == 422 and 'secret' not in weak.text
        assert client.post('/api/auth/logout', headers={'Origin': 'https://other.example'}).status_code == 403
        assert client.post('/api/runs', headers={'Sec-Fetch-Site':'cross-site'}, json={}).status_code == 403
        assert client.get('/api/auth/me').status_code == 200
        assert client.post('/api/auth/logout', headers={'Origin':'http://testserver'}).status_code == 200

def test_rate_limit(isolated):
    with TestClient(api.app, headers=HEADERS) as client:
        for _ in range(8):
            assert client.post('/api/auth/login', json={'username':'missing','password':PASSWORD}).status_code == 401
        assert client.post('/api/auth/login', json={'username':'missing','password':PASSWORD}).status_code == 429

def test_first_account_adopts_legacy_data_only_once(isolated):
    seed(None, 'legacy')
    store.save_config('default', DEFAULT_CONFIG)
    with TestClient(api.app, headers=HEADERS) as a, TestClient(api.app, headers=HEADERS) as b:
        assert a.post('/api/auth/register',json={'username':'outsider','display_name':'Outsider','password':PASSWORD}).status_code in (404,405)
        alice = register(a, 'alice')
        register(b, 'bob')
        assert store.get_run('legacy')['owner_id'] == alice['id']
        assert a.get('/api/runs/legacy').status_code == 200
        assert b.get('/api/runs/legacy').status_code == 404
        assert store.get_config(alice['id']) == DEFAULT_CONFIG
        assert store.get_config('default') is None
        with pytest.raises(ValueError):auth.bootstrap_admin(auth.Registration(username='newadmin',display_name='New admin',password=PASSWORD))

def test_local_bootstrap_race_has_one_owner(isolated):
    seed(None,'legacy')
    def create(username):
        try:return auth.bootstrap_admin(auth.Registration(username=username,display_name=username,password=PASSWORD))
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(create,['alice','bob']))
    winners=[r for r in results if r]
    assert len(winners)==1
    assert store.get_run('legacy')['owner_id']==winners[0]['id']

def test_legacy_schema_migration_is_idempotent(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "old.sqlite3"}')
    monkeypatch.setattr(store, 'engine', engine)
    old = MetaData()
    table = Table('runs', old, *(Column(c.name, c.type, primary_key=c.primary_key) for c in store.runs.columns if c.name != 'owner_id'))
    old.create_all(engine)
    with engine.begin() as conn:
        conn.execute(table.insert().values(id='old', name='Preserve me', status='completed'))
    store.init()
    store.init()
    assert 'owner_id' in {c['name'] for c in inspect(engine).get_columns('runs')}
    assert store.get_run('old')['name'] == 'Preserve me'
    assert store.get_run('old')['owner_id'] is None
    engine.dispose()
