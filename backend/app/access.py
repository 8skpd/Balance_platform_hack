"""Report sharing grants read access only, independently of account roles."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, delete, or_
from . import store, audit
from .auth import current_user, require_writer
from .queueing import transaction

router=APIRouter(prefix='/api/runs',dependencies=[Depends(current_user)])

def can_manage(run,user):
    return run['owner_id']==user['id'] and user.get('role','user') in ('user','admin')

def require_run(run_id,user,complete=False,write=False):
    run=store.get_run(run_id)
    if not run:raise HTTPException(404,'Запуск не найден')
    if run['owner_id']!=user['id']:
        with store.engine.connect() as conn:
            shared=conn.execute(select(store.run_shares.c.user_id).where(store.run_shares.c.run_id==run_id,store.run_shares.c.user_id==user['id'])).first()
        if not shared or run['status']!='completed':raise HTTPException(404,'Запуск не найден')
    if write and not can_manage(run,user):raise HTTPException(403,'Управлять анализом может только его владелец с ролью пользователя или администратора')
    if complete and run['status']!='completed':raise HTTPException(409,'Отчёт доступен только после завершения обработки')
    return run

def list_runs(user):
    shared=select(store.run_shares.c.run_id).where(store.run_shares.c.user_id==user['id'])
    columns=[c for c in store.runs.c if c.name not in ('checks','config','ai','check_plan','check_executions','procedure_plan')]
    with store.engine.connect() as conn:
        rows=conn.execute(select(*columns).where(or_(store.runs.c.owner_id==user['id'],
            (store.runs.c.id.in_(shared)) & (store.runs.c.status=='completed'))).where(store.runs.c.archived==False).order_by(store.runs.c.created_at.desc(),store.runs.c.id.desc()).limit(100)).mappings().all()
    return [{**dict(r),'access':'owner' if r['owner_id']==user['id'] else 'shared','can_manage':can_manage(r,user)} for r in rows]

class Grant(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    username:str=Field(min_length=3,max_length=40)

@router.get('/{run_id}/shares')
def list_shares(run_id:str,user:dict=Depends(require_writer)):
    require_run(run_id,user,write=True)
    with store.engine.connect() as conn:
        rows=conn.execute(select(store.users.c.id,store.users.c.username,store.users.c.display_name,store.users.c.role,store.run_shares.c.created_at)
            .join(store.run_shares,store.run_shares.c.user_id==store.users.c.id).where(store.run_shares.c.run_id==run_id)
            .order_by(store.users.c.username)).mappings().all()
    return [dict(r) for r in rows]

@router.post('/{run_id}/shares',status_code=201)
def grant(run_id:str,body:Grant,user:dict=Depends(require_writer)):
    require_run(run_id,user,complete=True,write=True)
    with transaction() as conn:
        locked=conn.execute(select(store.runs).where(store.runs.c.id==run_id).with_for_update()).mappings().first()
        actor=conn.execute(select(store.users).where(store.users.c.id==user['id'])).mappings().first()
        if not actor or actor['blocked'] or actor['role']=='observer': raise HTTPException(403,'Управление доступом больше недоступно')
        if not locked or not can_manage(locked,actor): raise HTTPException(409,'Владелец анализа изменился')
        target=conn.execute(select(store.users.c.id).where(store.users.c.username==body.username.lower(),store.users.c.blocked==False)).scalar_one_or_none()
        if not target:raise HTTPException(404,'Пользователь с таким логином не найден')
        if target==user['id']:raise HTTPException(422,'Вы уже владелец этого анализа')
        existing=conn.execute(select(store.run_shares.c.user_id).where(store.run_shares.c.run_id==run_id,store.run_shares.c.user_id==target)).first()
        if not existing:conn.execute(store.run_shares.insert().values(run_id=run_id,user_id=target,created_at=store.now(),granted_by=user['id']))
        audit.record(conn,user['id'],'access.grant',target,run_id=run_id)
    return {'user_id':target,'access':'view'}

@router.delete('/{run_id}/shares/{user_id}')
def revoke(run_id:str,user_id:str,user:dict=Depends(require_writer)):
    require_run(run_id,user,write=True)
    with transaction() as conn:
        locked=conn.execute(select(store.runs).where(store.runs.c.id==run_id).with_for_update()).mappings().first()
        actor=conn.execute(select(store.users).where(store.users.c.id==user['id'])).mappings().first()
        if not actor or actor['blocked'] or actor['role']=='observer': raise HTTPException(403,'Управление доступом больше недоступно')
        if not locked or not can_manage(locked,actor): raise HTTPException(409,'Владелец анализа изменился')
        conn.execute(delete(store.run_shares).where(store.run_shares.c.run_id==run_id,store.run_shares.c.user_id==user_id))
        # An assignee cannot retain approval rights after losing access.
        audit.record(conn,user['id'],'access.revoke',user_id,run_id=run_id)
    return {'revoked':True}
