from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select,func,update

from app import api,auth,admin,store
from test_library import isolated,register,HEADERS

PASSWORD='New-user-password-42'
def payload(**extra):return {'username':'new-user','display_name':'Новый пользователь','password':PASSWORD,**extra}

def test_public_registration_is_closed_even_on_empty_install(isolated):
    with TestClient(api.app,headers=HEADERS) as client:
        for url in ('/api/auth/register','/api/auth/bootstrap'):
            assert client.post(url,json=payload(role='admin')).status_code in (404,405)
        assert client.post('/api/admin/users',json=payload()).status_code==401
        with store.engine.connect() as conn:
            assert conn.execute(select(func.count()).select_from(store.users)).scalar_one()==0

def test_admin_creates_all_roles_without_replacing_session_and_records_audit(isolated):
    with TestClient(api.app,headers=HEADERS) as owner,TestClient(api.app,headers=HEADERS) as newcomer:
        actor=register(owner,'alice');token=owner.cookies.get(auth.COOKIE)
        for role in ('user','observer','admin'):
            result=owner.post('/api/admin/users',json=payload(username='new-'+role,role=role))
            assert result.status_code==201,result.text
            created=result.json();assert created['role']==role and 'password' not in result.text
            assert 'set-cookie' not in result.headers
            assert owner.cookies.get(auth.COOKIE)==token
            assert owner.get('/api/auth/me').json()['id']==actor['id']
            assert newcomer.post('/api/auth/login',json={'username':'new-'+role,'password':PASSWORD}).status_code==200
            assert newcomer.get('/api/auth/me').json()==created
            with store.engine.connect() as conn:
                stored=conn.execute(select(store.users).where(store.users.c.id==created['id'])).mappings().one()
                event=conn.execute(select(store.audit_events).where(store.audit_events.c.entity_id==created['id'],store.audit_events.c.action=='account.create')).mappings().one()
            assert auth.verify_password(PASSWORD,stored['password_hash'])
            assert event['actor_id']==actor['id'] and event['details']['role']==role
            assert PASSWORD not in str(event) and stored['password_hash'] not in str(event)
        assert owner.post('/api/admin/users',json=payload(username='NEW-USER')).status_code==409
        weak=owner.post('/api/admin/users',json=payload(password='secret'))
        assert weak.status_code==422 and 'secret' not in weak.text
        assert owner.post('/api/admin/users',json=payload(role='superadmin')).status_code==422
        assert owner.post('/api/admin/users',json=payload(),headers={'Origin':'https://other.example'}).status_code==403

def test_users_observers_blocked_and_demoted_admins_cannot_create_accounts(isolated):
    with TestClient(api.app,headers=HEADERS) as a,TestClient(api.app,headers=HEADERS) as b:
        alice=register(a,'alice');bob=register(b,'bob')
        assert b.post('/api/admin/users',json=payload(role='admin')).status_code==403
        assert a.put(f"/api/admin/users/{bob['id']}/role",json={'role':'observer','expected_role':'user'}).status_code==200
        assert b.post('/api/admin/users',json=payload()).status_code==403
        with store.engine.begin() as conn:conn.execute(update(store.users).where(store.users.c.id==alice['id']).values(blocked=True))
        assert a.post('/api/admin/users',json=payload()).status_code==401
        try:admin.create_user(admin.UserCreate(**payload()),user=alice)
        except HTTPException as exc:assert exc.status_code==403
        else:assert False,'Stale administrator accepted'

def test_concurrent_duplicate_user_creation_has_one_winner(isolated):
    with TestClient(api.app,headers=HEADERS) as client:
        actor=register(client,'alice')
        def create(_):
            try:return admin.create_user(admin.UserCreate(**payload()),user=actor)['username']
            except HTTPException as exc:return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(create,range(2)))
        assert results.count('new-user')==1 and results.count(409)==1
