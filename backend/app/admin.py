"""Account roles and server-wide Ollama connection. Credentials are never returned."""
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update, func
from sqlalchemy.exc import IntegrityError

from . import store, audit
from .auth import require_admin, public_user, Registration, account_record
from .queueing import transaction
from .settings import OLLAMA_URL, OLLAMA_MODEL

router=APIRouter(prefix='/api/admin',dependencies=[Depends(require_admin)])
Role=Literal['admin','user','observer']

def locked_admin(conn,user):
    # Shared lock serializes all role operations, including concurrent demotions.
    conn.execute(select(store.system_state.c.id).where(store.system_state.c.id=='roles-v1').with_for_update()).first()
    role=conn.execute(select(store.users.c.role).where(store.users.c.id==user['id'],store.users.c.blocked==False)).scalar_one_or_none()
    if role!='admin':raise HTTPException(403,'Роль администратора больше недоступна')

class RoleChange(BaseModel):
    model_config=ConfigDict(extra='forbid')
    role:Role
    expected_role:Role

class UserCreate(Registration):
    role:Role='user'

@router.post('/users',status_code=201)
def create_user(body:UserCreate,user:dict=Depends(require_admin)):
    account=account_record(body,body.role)
    try:
        with transaction() as conn:
            locked_admin(conn,user)
            conn.execute(store.users.insert().values(**account))
            audit.record(conn,user['id'],'account.create',account['id'],username=account['username'],display_name=account['display_name'],role=body.role)
    except IntegrityError:
        raise HTTPException(409,'Такой логин уже занят')
    return public_user(account)

@router.get('/users')
def users():
    with store.engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(store.users.c.id,store.users.c.username,store.users.c.display_name,
            store.users.c.role,store.users.c.created_at,store.users.c.blocked).order_by(store.users.c.created_at,store.users.c.id)).mappings()]

@router.put('/users/{user_id}/role')
def change_role(user_id:str,body:RoleChange,user:dict=Depends(require_admin)):
    with transaction() as conn:
        locked_admin(conn,user)
        target=conn.execute(select(store.users).where(store.users.c.id==user_id).with_for_update()).mappings().first()
        if not target:raise HTTPException(404,'Пользователь не найден')
        if target['role']!=body.expected_role:raise HTTPException(409,'Роль уже изменена. Обновите список пользователей.')
        if target['role']=='admin' and not target['blocked'] and body.role!='admin':
            count=conn.execute(select(func.count()).select_from(store.users).where(store.users.c.role=='admin',store.users.c.blocked==False)).scalar_one()
            if count<=1:raise HTTPException(409,'Нельзя снять роль у последнего администратора')
        conn.execute(update(store.users).where(store.users.c.id==user_id).values(role=body.role))
        audit.record(conn,user['id'],'account.role',user_id,before=target['role'],after=body.role)
        result=public_user({**target,'role':body.role})
    return result

def ai_settings():
    with store.engine.connect() as conn:
        row=conn.execute(select(store.app_settings).where(store.app_settings.c.id=='ollama')).mappings().first()
    return {'base_url':row['payload']['base_url'] if row else OLLAMA_URL,
            'revision':row['revision'] if row else 0,'model':row['payload'].get('model',OLLAMA_MODEL) if row else OLLAMA_MODEL,
            'updated_at':row['updated_at'] if row else None}

class AIConnection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    base_url:str=Field(min_length=1,max_length=2048)
    model:str|None=Field(default=None,min_length=1,max_length=200)

    @field_validator('model')
    @classmethod
    def valid_model(cls,value):
        if value is None:return value
        value=value.strip()
        if not value or any(c.isspace() or ord(c)<32 or ord(c)==127 for c in value):
            raise ValueError('Укажите имя модели Ollama без пробелов и управляющих символов')
        return value

    @field_validator('base_url')
    @classmethod
    def valid_url(cls,value):
        value=value.strip()
        if any(ord(c)<=32 for c in value) or '\\' in value:raise ValueError('Адрес не должен содержать пробелы или управляющие символы')
        try:
            url=urlsplit(value);port=url.port
        except ValueError:raise ValueError('Некорректный адрес сервера')
        if url.scheme not in ('http','https') or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment:
            raise ValueError('Укажите HTTP(S)-адрес Ollama без логина, пароля, параметров и фрагмента')
        path=url.path.rstrip('/')
        if path.endswith('/api/chat'):path=path[:-9]
        return urlunsplit((url.scheme,url.netloc,path,'',''))

class AIUpdate(AIConnection):
    revision:int=Field(ge=0)

@router.get('/ai')
def get_ai():return ai_settings()

@router.put('/ai')
def save_ai(body:AIUpdate,user:dict=Depends(require_admin)):
    with transaction() as conn:
        locked_admin(conn,user)
        row=conn.execute(select(store.app_settings).where(store.app_settings.c.id=='ollama').with_for_update()).mappings().first()
        revision=row['revision'] if row else 0
        if body.revision!=revision:raise HTTPException(409,'Настройки ИИ уже изменены. Загрузите актуальные настройки.')
        model=body.model or (row['payload'].get('model',OLLAMA_MODEL) if row else OLLAMA_MODEL)
        values={'payload':{'base_url':body.base_url,'model':model},'revision':revision+1,'updated_at':store.now(),'updated_by':user['id']}
        if row:conn.execute(update(store.app_settings).where(store.app_settings.c.id=='ollama').values(**values))
        else:conn.execute(store.app_settings.insert().values(id='ollama',**values))
        audit.record(conn,user['id'],'settings.ai','ollama',before=row['payload'] if row else None,after=values['payload'])
    return {'base_url':body.base_url,'revision':revision+1,'updated_at':values['updated_at'],'model':model}

@router.post('/ai/test')
def test_ai(body:AIConnection):
    return connection_status(body.base_url,body.model or ai_settings()['model'])

def connection_status(base_url,model):
    # The admin explicitly chooses a local/network service. Never follow redirects.
    try:
        with httpx.Client(timeout=5,follow_redirects=False) as client:
            response=client.get(base_url+'/api/tags');response.raise_for_status()
            models=sorted({m['name'] for m in response.json()['models'] if isinstance(m.get('name'),str)})
        canonical=model if ':' in model.rsplit('/',1)[-1] else model+':latest'
        return {'connected':True,'available':model in models or canonical in models,'model':model,'models':models}
    except Exception as exc:
        error='Сервер не ответил за 5 секунд.' if isinstance(exc,httpx.TimeoutException) else str(exc)[:1000]
        return {'connected':False,'available':False,'model':model,'error':error}
