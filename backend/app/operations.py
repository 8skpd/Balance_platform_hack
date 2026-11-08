"""Account lifecycle, audit, quotas and paginated analysis inventory."""
from functools import lru_cache
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update, delete, func, or_
from . import store, audit, access
from .auth import current_user, require_admin
from .admin import locked_admin
from .queueing import transaction, TERMINAL, update_ai
from .settings import DATA, RUNS, JOBS, DATASETS, UPLOADS

router = APIRouter(prefix='/api')

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class AccountState(Input):
    blocked: bool
    expected_blocked: bool
    reason: str = Field(min_length=1, max_length=1000)

@router.put('/admin/users/{user_id}/state')
def account_state(user_id: str, body: AccountState, user=Depends(require_admin)):
    with transaction() as conn:
        locked_admin(conn, user)
        target = conn.execute(select(store.users).where(store.users.c.id==user_id).with_for_update()).mappings().first()
        if not target: raise HTTPException(404, 'Аккаунт не найден')
        if target['blocked'] != body.expected_blocked: raise HTTPException(409, 'Состояние аккаунта уже изменено')
        if body.blocked and target['role']=='admin' and not target['blocked']:
            count = conn.execute(select(func.count()).select_from(store.users).where(store.users.c.role=='admin', store.users.c.blocked==False)).scalar_one()
            if count <= 1: raise HTTPException(409, 'Нельзя заблокировать последнего действующего администратора')
        conn.execute(update(store.users).where(store.users.c.id==user_id).values(blocked=body.blocked))
        revoked = conn.execute(delete(store.sessions).where(store.sessions.c.user_id==user_id)).rowcount
        if body.blocked:
            jobs = conn.execute(select(store.jobs).where(store.jobs.c.owner_id==user_id, store.jobs.c.status.not_in(TERMINAL)).with_for_update()).mappings().all()
            for job in jobs:
                status = 'cancelled' if job['status']=='queued' else 'cancelling'
                conn.execute(update(store.jobs).where(store.jobs.c.id==job['id']).values(status=status,
                    error='Аккаунт заблокирован', finished_at=store.now() if status=='cancelled' else None))
                if job['kind']=='run': conn.execute(update(store.runs).where(store.runs.c.id==job['run_id']).values(status=status,stage=status))
                if job['kind']=='ai': update_ai(conn, job['run_id'], {'status':status, 'error':'Аккаунт заблокирован', 'job_id':job['id']})
        audit.record(conn, user['id'], 'account.block' if body.blocked else 'account.unblock', user_id, reason=body.reason, revoked_sessions=revoked)
    return {'blocked':body.blocked, 'revoked_sessions':revoked}

class Reason(Input):
    reason: str = Field(min_length=1, max_length=1000)

@router.post('/admin/users/{user_id}/revoke-sessions')
def revoke_sessions(user_id: str, body: Reason, user=Depends(require_admin)):
    with transaction() as conn:
        locked_admin(conn, user)
        if not conn.execute(select(store.users.c.id).where(store.users.c.id==user_id).with_for_update()).first(): raise HTTPException(404, 'Аккаунт не найден')
        count = conn.execute(delete(store.sessions).where(store.sessions.c.user_id==user_id)).rowcount
        audit.record(conn,user['id'],'account.revoke_sessions',user_id,reason=body.reason,revoked_sessions=count)
    return {'revoked_sessions':count}

def paged(conn, query, page, limit):
    total = conn.execute(select(func.count()).select_from(query.order_by(None).subquery())).scalar_one()
    return {'items':[dict(r) for r in conn.execute(query.offset((page-1)*limit).limit(limit)).mappings()],
            'total':total, 'page':page, 'limit':limit}

def audit_query():
    return select(store.audit_events,store.users.c.username.label('actor')).outerjoin(store.users,store.users.c.id==store.audit_events.c.actor_id).order_by(store.audit_events.c.id.desc())

@router.get('/admin/audit')
def admin_audit(page:int=Query(1,ge=1),limit:int=Query(25,ge=1,le=100),action:str='',user=Depends(require_admin)):
    query=audit_query()
    if action: query=query.where(store.audit_events.c.action==action)
    with store.engine.connect() as conn: return paged(conn,query,page,limit)

@router.get('/runs/{run_id}/activity')
def activity(run_id:str,page:int=Query(1,ge=1),limit:int=Query(25,ge=1,le=100),user=Depends(current_user)):
    access.require_run(run_id,user)
    with store.engine.connect() as conn: return paged(conn,audit_query().where(store.audit_events.c.run_id==run_id),page,limit)

@router.get('/run-history')
def run_history(page:int=Query(1,ge=1),limit:int=Query(20,ge=1,le=100),archived:bool=False,search:str=Query('',max_length=120),user=Depends(current_user)):
    shared=select(store.run_shares.c.run_id).where(store.run_shares.c.user_id==user['id'])
    columns=[c for c in store.runs.c if c.name not in ('checks','config','ai','check_plan','check_executions','procedure_plan')]
    query=select(*columns).where(or_(store.runs.c.owner_id==user['id'],(store.runs.c.id.in_(shared)) & (store.runs.c.status=='completed')),
        store.runs.c.archived==archived).order_by(store.runs.c.created_at.desc(),store.runs.c.id.desc())
    if search: query=query.where(store.runs.c.name.contains(search,autoescape=True))
    with store.engine.connect() as conn: result=paged(conn,query,page,limit)
    result['items']=[{**r,'access':'owner' if r['owner_id']==user['id'] else 'shared','can_manage':access.can_manage(r,user)} for r in result['items']]
    return result

@router.get('/admin/users/{user_id}/analyses')
def employee_analyses(user_id:str,page:int=Query(1,ge=1),user=Depends(require_admin)):
    # Recovery inventory contains metadata only, never report data or source JSON.
    with store.engine.connect() as conn:
        return paged(conn,select(store.runs.c.id,store.runs.c.name,store.runs.c.status,store.runs.c.archived,store.runs.c.owner_id)
            .where(store.runs.c.owner_id==user_id).order_by(store.runs.c.created_at.desc(),store.runs.c.id),page,20)

class Limits(Input):
    max_active_jobs: int = Field(default=6,ge=1,le=100)
    max_runs: int = Field(default=200,ge=1,le=100000)
    max_user_storage_mb: int = Field(default=10240,ge=16,le=1048576)
    max_job_storage_mb: int = Field(default=4096,ge=16,le=65536)
    min_free_mb: int = Field(default=512,ge=16,le=1048576)

class LimitsUpdate(Limits):
    revision: int = Field(ge=0)

def limits(conn):
    row=conn.execute(select(store.app_settings).where(store.app_settings.c.id=='limits')).mappings().first()
    return {**Limits().model_dump(),**(row['payload'] if row else {}),'revision':row['revision'] if row else 0}

def directory_size(path):
    if path.is_symlink() or not path.exists(): return 0
    if path.is_file(): return path.stat().st_size
    total=0
    for item in path.rglob('*'):
        try:
            if item.is_file() and not item.is_symlink(): total+=item.stat().st_size
        except FileNotFoundError: pass
    return total

@lru_cache(maxsize=4096)
def _immutable_size(path, inode, modified):
    # Published imported datasets never change; replacement has a different inode.
    from pathlib import Path
    return directory_size(Path(path))

def immutable_size(path):
    if path.is_symlink() or not path.exists(): return 0
    stat=path.stat()
    return _immutable_size(str(path),stat.st_ino,stat.st_mtime_ns)

def usage(conn,owner):
    run_ids=conn.execute(select(store.runs.c.id).where(store.runs.c.owner_id==owner)).scalars().all()
    job_ids=conn.execute(select(store.jobs.c.id).where(store.jobs.c.owner_id==owner)).scalars().all()
    datasets=conn.execute(select(store.dataset_imports).where(store.dataset_imports.c.owner_id==owner)).mappings().all()
    paths=[*(RUNS / x for x in run_ids),*(JOBS / x for x in job_ids),
           *(UPLOADS / f'{x}.zip' for x in {d['upload_id'] for d in datasets})]
    return {'runs':len(run_ids),'storage_bytes':sum(directory_size(p) for p in paths)+sum(immutable_size(DATASETS/d['id']) for d in datasets),
            'active_jobs':conn.execute(select(func.count()).select_from(store.jobs).where(store.jobs.c.owner_id==owner,store.jobs.c.status.not_in(TERMINAL))).scalar_one()}

def admission(conn, owner, new_run=False, extra_bytes=0):
    import shutil
    account=conn.execute(select(store.users).where(store.users.c.id==owner).with_for_update()).mappings().first()
    if account and (account['blocked'] or account['role']=='observer'): raise HTTPException(403,'Аккаунту недоступен запуск задач')
    quota=limits(conn); used=usage(conn,owner)
    if used['active_jobs']>=quota['max_active_jobs']: raise HTTPException(429,'Достигнут лимит активных задач. Дождитесь завершения или отмените задачу.')
    if new_run and used['runs']>=quota['max_runs']: raise HTTPException(409,'Достигнут лимит анализов. Удалите ненужные анализы из архива.')
    if used['storage_bytes']+extra_bytes>=quota['max_user_storage_mb']*1024**2: raise HTTPException(409,'Достигнут лимит диска пользователя. Очистите ненужные данные.')
    if shutil.disk_usage(DATA).free-extra_bytes<quota['min_free_mb']*1024**2: raise HTTPException(409,'Недостаточно свободного места на сервере')

@router.get('/quotas')
def quota_usage(user=Depends(current_user)):
    with store.engine.connect() as conn: return {'limits':limits(conn),'usage':usage(conn,user['id'])}

@router.get('/admin/limits')
def get_limits(user=Depends(require_admin)):
    with store.engine.connect() as conn: return limits(conn)

@router.put('/admin/limits')
def save_limits(body:LimitsUpdate,user=Depends(require_admin)):
    with transaction() as conn:
        locked_admin(conn,user)
        old=limits(conn)
        if body.revision!=old['revision']: raise HTTPException(409,'Лимиты уже изменены. Обновите страницу.')
        payload=body.model_dump(exclude={'revision'})
        values={'payload':payload,'revision':body.revision+1,'updated_at':store.now(),'updated_by':user['id']}
        if old['revision']: conn.execute(update(store.app_settings).where(store.app_settings.c.id=='limits').values(**values))
        else: conn.execute(store.app_settings.insert().values(id='limits',**values))
        audit.record(conn,user['id'],'settings.limits','limits',before=old,after=payload)
    return {**payload,'revision':body.revision+1}
