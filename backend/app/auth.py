"""Password authentication and revocable opaque sessions. No credentials in browser storage."""
import hashlib
import hmac
import secrets
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select, text, update

from . import store
from .settings import SESSION_COOKIE_SECURE

router = APIRouter(prefix='/api/auth', tags=['Authentication'])
COOKIE = 'balance_session'
SESSION_SECONDS = 8 * 60 * 60
throttle_lock = threading.Lock()
attempts = {}

class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=10, max_length=128)

    @field_validator('username')
    @classmethod
    def canonical_username(cls, value):
        value = value.strip().lower()
        if len(value) < 3 or not all(c in 'abcdefghijklmnopqrstuvwxyz0123456789_.-' for c in value):
            raise ValueError('Логин: 3–40 латинских букв, цифр, точек, дефисов или подчёркиваний')
        return value

class Registration(Credentials):
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator('display_name')
    @classmethod
    def valid_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Укажите имя')
        return value

def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    # scrypt: N=2^17, r=8, p=1; permit its ~128 MiB memory requirement.
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**17, r=8, p=1, maxmem=256*1024*1024)
    return f'scrypt${salt.hex()}${digest.hex()}'

def verify_password(password, encoded):
    try:
        algorithm, salt, digest = encoded.split('$')
        if algorithm != 'scrypt':
            return False
        actual = password_hash(password, bytes.fromhex(salt)).split('$')[-1]
        return hmac.compare_digest(actual, digest)
    except (ValueError, TypeError):
        return False

def public_user(user):
    return {k: user[k] for k in ('id', 'username', 'display_name', 'role')}

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

def current_user(request: Request):
    token = request.cookies.get(COOKIE)
    if not token or len(token) > 256:
        raise HTTPException(401, 'Войдите в аккаунт')
    with store.engine.connect() as conn:
        row = conn.execute(select(store.users).join(store.sessions, store.users.c.id == store.sessions.c.user_id)
            .where(store.sessions.c.token_hash == token_hash(token), store.sessions.c.expires_at > int(time.time()))).mappings().first()
    if not row or row['blocked']:
        raise HTTPException(401, 'Сессия завершилась. Войдите снова')
    return public_user(row)

def require_writer(user: dict = Depends(current_user)):
    if user['role'] not in ('admin','user'):
        raise HTTPException(403, 'Наблюдателю доступен только просмотр отчётов')
    return user

def require_admin(user: dict = Depends(current_user)):
    if user['role'] != 'admin':
        raise HTTPException(403, 'Доступно только администратору')
    return user

def issue_session(response, user_id, old_token=None):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    from .queueing import transaction
    with transaction() as conn:
        account = conn.execute(select(store.users).where(store.users.c.id == user_id).with_for_update()).mappings().first()
        if not account or account['blocked']:
            raise HTTPException(401, 'Аккаунт заблокирован')
        conn.execute(delete(store.sessions).where(store.sessions.c.expires_at <= now))
        if old_token:
            conn.execute(delete(store.sessions).where(store.sessions.c.token_hash == token_hash(old_token)))
        conn.execute(store.sessions.insert().values(token_hash=token_hash(token), user_id=user_id, expires_at=now+SESSION_SECONDS))
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=SESSION_COOKIE_SECURE, samesite='lax', path='/')

def rate_limit(request, username):
    # Both per-client and per-login limits. Expired entries do not accumulate.
    now = time.monotonic()
    keys = [('ip', request.client.host if request.client else 'unknown'), ('user', username)]
    with throttle_lock:
        for key in list(attempts):
            attempts[key] = [t for t in attempts[key] if now-t < 60]
            if not attempts[key]:
                del attempts[key]
        if any(len(attempts.get(k, [])) >= (20 if k[0] == 'ip' else 8) for k in keys):
            raise HTTPException(429, 'Слишком много попыток. Повторите через минуту', headers={'Retry-After': '60'})
        for key in keys:
            attempts.setdefault(key, []).append(now)

@router.get('/me')
def me(user: dict = Depends(current_user)):
    return user

def account_record(body: Registration, role: str):
    return {'id':str(uuid.uuid4()),'username':body.username,'display_name':body.display_name,
            'password_hash':password_hash(body.password),'created_at':store.now(),'role':role}

def bootstrap_admin(body: Registration):
    """Local installation only. There is deliberately no HTTP bootstrap route."""
    from .queueing import transaction
    from . import audit
    user=account_record(body,'admin')
    with transaction() as conn:
        conn.execute(select(store.system_state).where(store.system_state.c.id=='registration').with_for_update()).first()
        if conn.execute(select(func.count()).select_from(store.users)).scalar_one():
            raise ValueError('Первый аккаунт уже создан. Новых пользователей создаёт администратор в приложении.')
        conn.execute(store.users.insert().values(**user))
        conn.execute(update(store.runs).where(store.runs.c.owner_id.is_(None)).values(owner_id=user['id']))
        legacy=conn.execute(select(store.configs.c.payload).where(store.configs.c.id=='default')).scalar_one_or_none()
        if legacy is not None:
            conn.execute(store.configs.insert().values(id=user['id'],payload=legacy))
            conn.execute(delete(store.configs).where(store.configs.c.id=='default'))
        audit.record(conn,user['id'],'account.bootstrap',user['id'],username=user['username'],role='admin')
    return public_user(user)

@router.post('/login')
def login(body: Credentials, response: Response, request: Request):
    rate_limit(request, body.username)
    with store.engine.connect() as conn:
        user = conn.execute(select(store.users).where(store.users.c.username == body.username)).mappings().first()
    if not user:
        password_hash(body.password, b'\0' * 16)  # Similar work for unknown usernames.
    if not user or not verify_password(body.password, user['password_hash']) or user['blocked']:
        raise HTTPException(401, 'Неверный логин или пароль')
    issue_session(response, user['id'], request.cookies.get(COOKIE))
    return public_user(user)

@router.post('/logout')
def logout(response: Response, request: Request):
    token = request.cookies.get(COOKIE)
    if token:
        with store.engine.begin() as conn:
            conn.execute(delete(store.sessions).where(store.sessions.c.token_hash == token_hash(token)))
    response.delete_cookie(COOKIE, path='/', secure=SESSION_COOKIE_SECURE, httponly=True, samesite='lax')
    return {'status': 'ok'}


class ProfileUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    display_name:str=Field(min_length=1,max_length=80)
    expected_display_name:str=Field(min_length=1,max_length=80)

@router.patch('/profile')
def update_profile(body:ProfileUpdate,user:dict=Depends(current_user)):
    from .queueing import transaction
    with transaction() as conn:
        account=conn.execute(select(store.users).where(store.users.c.id==user['id']).with_for_update()).mappings().first()
        if not account or account['blocked']:raise HTTPException(401,'Сессия завершилась. Войдите снова')
        if account['display_name']!=body.expected_display_name:raise HTTPException(409,'Профиль изменился в другом окне. Откройте настройки заново.')
        conn.execute(update(store.users).where(store.users.c.id==user['id']).values(display_name=body.display_name))
        return {**public_user(account),'display_name':body.display_name}

class PasswordUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    current_password:str=Field(min_length=1,max_length=128)
    new_password:str=Field(min_length=10,max_length=128)

@router.post('/password')
def change_password(body:PasswordUpdate,request:Request,response:Response,user:dict=Depends(current_user)):
    from .queueing import transaction
    rate_limit(request,user['username'])
    with transaction() as conn:
        account=conn.execute(select(store.users).where(store.users.c.id==user['id']).with_for_update()).mappings().first()
        if not account or account['blocked']:raise HTTPException(401,'Сессия завершилась. Войдите снова')
        if not verify_password(body.current_password,account['password_hash']):raise HTTPException(400,'Текущий пароль указан неверно')
        if body.current_password==body.new_password:raise HTTPException(422,'Новый пароль должен отличаться от текущего')
        conn.execute(update(store.users).where(store.users.c.id==user['id']).values(password_hash=password_hash(body.new_password)))
        conn.execute(delete(store.sessions).where(store.sessions.c.user_id==user['id']))
        token=secrets.token_urlsafe(32)
        conn.execute(store.sessions.insert().values(token_hash=token_hash(token),user_id=user['id'],expires_at=int(time.time())+SESSION_SECONDS))
    response.set_cookie(COOKIE,token,max_age=SESSION_SECONDS,httponly=True,secure=SESSION_COOKIE_SECURE,samesite='lax',path='/')
    return {'status':'ok'}
