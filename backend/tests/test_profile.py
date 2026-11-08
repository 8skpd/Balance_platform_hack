from fastapi.testclient import TestClient
from sqlalchemy import select,update
from app import api,auth,store
from test_auth import isolated,register,PASSWORD,HEADERS

def test_profile_name_only_and_conflict(isolated):
    with TestClient(api.app,headers=HEADERS) as c:
        user=register(c,'alice')
        r=c.patch('/api/auth/profile',json={'display_name':' Новое имя ','expected_display_name':'alice'});assert r.status_code==200,r.text
        assert r.json()['display_name']=='Новое имя' and r.json()['role']=='admin'
        assert c.get('/api/auth/me').json()['display_name']=='Новое имя'
        assert c.patch('/api/auth/profile',json={'display_name':'Другой','expected_display_name':'alice'}).status_code==409
        assert c.patch('/api/auth/profile',json={'display_name':' ','expected_display_name':'Новое имя'}).status_code==422
        assert c.patch('/api/auth/profile',json={'display_name':'Другой','expected_display_name':'Новое имя','role':'admin'}).status_code==422

def test_password_change_rotates_sessions_and_rejects_old_password(isolated):
    with TestClient(api.app,headers=HEADERS) as c,TestClient(api.app,headers=HEADERS) as other:
        register(c,'alice');other.post('/api/auth/login',json={'username':'alice','password':PASSWORD})
        old_cookie=c.cookies.get(auth.COOKIE)
        r=c.post('/api/auth/password',json={'current_password':'wrong','new_password':'New-test-password-42'});assert r.status_code==400
        assert other.get('/api/auth/me').status_code==200
        r=c.post('/api/auth/password',json={'current_password':PASSWORD,'new_password':'New-test-password-42'});assert r.status_code==200,r.text
        assert c.cookies.get(auth.COOKIE)!=old_cookie and c.get('/api/auth/me').status_code==200
        assert other.get('/api/auth/me').status_code==401
        assert other.post('/api/auth/login',json={'username':'alice','password':PASSWORD}).status_code==401
        assert other.post('/api/auth/login',json={'username':'alice','password':'New-test-password-42'}).status_code==200

def test_observer_can_edit_own_profile_but_blocked_user_cannot(isolated):
    with TestClient(api.app,headers=HEADERS) as c:
        user=register(c,'alice')
        with store.engine.begin() as conn:conn.execute(update(store.users).where(store.users.c.id==user['id']).values(role='observer'))
        assert c.patch('/api/auth/profile',json={'display_name':'Наблюдатель','expected_display_name':'alice'}).status_code==200
        with store.engine.begin() as conn:conn.execute(update(store.users).where(store.users.c.id==user['id']).values(blocked=True))
        assert c.patch('/api/auth/profile',json={'display_name':'X','expected_display_name':'Наблюдатель'}).status_code==401
        assert c.post('/api/auth/password',json={'current_password':PASSWORD,'new_password':'New-test-password-42'}).status_code==401
