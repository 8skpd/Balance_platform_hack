"""Discussion, review and ownership of an immutable analysis result."""
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select, update, delete
from . import store, audit, access
from .auth import current_user, require_writer
from .operations import Input, paged, admission
from .queueing import transaction, TERMINAL

router=APIRouter(prefix='/api/runs')

def locked_access(conn, run_id, user, *, owner=False):
    run=conn.execute(select(store.runs).where(store.runs.c.id==run_id).with_for_update()).mappings().first()
    account=conn.execute(select(store.users).where(store.users.c.id==user['id'])).mappings().first()
    if not account or account['blocked'] or account['role']=='observer': raise HTTPException(403,'Действие недоступно аккаунту')
    shared=conn.execute(select(store.run_shares.c.user_id).where(store.run_shares.c.run_id==run_id,store.run_shares.c.user_id==user['id'])).first()
    if not run or (run['owner_id']!=user['id'] and (owner or not shared or run['status']!='completed')):
        raise HTTPException(404,'Анализ недоступен')
    return run

def idle(conn,run_id):
    if conn.execute(select(store.jobs.c.id).where(store.jobs.c.run_id==run_id,store.jobs.c.status.not_in(TERMINAL))).first():
        raise HTTPException(409,'Сначала завершите или отмените активные задачи анализа')

class Transfer(Input):
    username:str=Field(min_length=3,max_length=40)
    expected_owner_id:str
    retain_previous:bool=True
    reason:str=Field(min_length=1,max_length=1000)

@router.post('/{run_id}/transfer')
def transfer(run_id:str,body:Transfer,user=Depends(require_writer)):
    with transaction() as conn:
        if user['role']=='admin':
            from .admin import locked_admin
            locked_admin(conn,user)
            run=conn.execute(select(store.runs).where(store.runs.c.id==run_id).with_for_update()).mappings().first()
            if not run: raise HTTPException(404,'Анализ не найден')
        else: run=locked_access(conn,run_id,user,owner=True)
        if run['owner_id']!=body.expected_owner_id: raise HTTPException(409,'Владелец уже изменён')
        idle(conn,run_id)
        target=conn.execute(select(store.users).where(store.users.c.username==body.username.lower()).with_for_update()).mappings().first()
        if not target or target['blocked'] or target['role']=='observer': raise HTTPException(422,'Нужен действующий пользователь или администратор')
        if target['id']==run['owner_id']: raise HTTPException(422,'Этот пользователь уже владелец')
        from .operations import directory_size
        from .settings import RUNS,JOBS
        job_ids=conn.execute(select(store.jobs.c.id).where(store.jobs.c.run_id==run_id)).scalars().all()
        admission(conn,target['id'],new_run=True,extra_bytes=directory_size(RUNS/run_id)+sum(directory_size(JOBS/x) for x in job_ids))
        previous=run['owner_id']
        conn.execute(update(store.runs).where(store.runs.c.id==run_id).values(owner_id=target['id'],dataset_owner_id=run['dataset_owner_id'] or previous))
        conn.execute(update(store.jobs).where(store.jobs.c.run_id==run_id).values(owner_id=target['id']))
        conn.execute(delete(store.run_shares).where(store.run_shares.c.run_id==run_id,store.run_shares.c.user_id.in_([previous,target['id']])))
        if body.retain_previous and previous:
            conn.execute(store.run_shares.insert().values(run_id=run_id,user_id=previous,created_at=store.now(),granted_by=user['id']))
        audit.record(conn,user['id'],'analysis.transfer',run_id,run_id=run_id,before=previous,after=target['id'],retain_previous=body.retain_previous,reason=body.reason)
    return {'owner_id':target['id']}

class Archive(Input):
    archived:bool

@router.put('/{run_id}/archive')
def archive(run_id:str,body:Archive,user=Depends(require_writer)):
    with transaction() as conn:
        run=locked_access(conn,run_id,user,owner=True)
        idle(conn,run_id)
        conn.execute(update(store.runs).where(store.runs.c.id==run_id).values(archived=body.archived))
        audit.record(conn,user['id'],'analysis.archive' if body.archived else 'analysis.restore',run_id,run_id=run_id)
    return {'archived':body.archived}

@router.post('/{run_id}/recalculate',status_code=201)
def recalculate(run_id:str,user=Depends(require_writer)):
    from .api import enqueue_run,NewRun,library
    run=access.require_run(run_id,user,write=True)
    if run['archived']: raise HTTPException(409,'Сначала восстановите анализ из архива')
    from .datasets import paths
    paths(run['dataset'],run.get('dataset_owner_id') or run['owner_id'])
    body=NewRun(dataset=run['dataset'],name=run['name'][:109]+' · повтор',config=run['config'],auto_ai=run.get('auto_ai',False))
    plan=run.get('check_plan')
    if plan is None: plan=library.resolve_plan(user,None,[],body.config.model_dump())
    return enqueue_run(body,user,plan,run.get('dataset_owner_id') or run['owner_id'],run['id'],run.get('procedure_plan'))

Target=Literal['run','application','check']
class Comment(Input):
    target_kind:Target='run'
    target_id:str=Field(default='',max_length=300)
    body:str=Field(min_length=1,max_length=4000)
class CommentEdit(Input):
    body:str=Field(min_length=1,max_length=4000)
    revision:int=Field(ge=1)

def validate_target(run,user,kind,target):
    if kind=='run':
        if target: raise HTTPException(422,'Обсуждение анализа не требует идентификатора заявки')
    elif kind=='check':
        if not target.isdecimal() or str(int(target))!=target or int(target)>=len(run['checks'] or []): raise HTTPException(404,'Строка проверки не найдена')
    else:
        from .api import database
        with database(run['id'],user) as conn:
            if not conn.execute('SELECT 1 FROM applications WHERE APP_ID = ? LIMIT 1',[target]).fetchone(): raise HTTPException(404,'Заявка не найдена')

@router.get('/{run_id}/comments')
def comments(run_id:str,target_kind:Target='run',target_id:str=Query('',max_length=300),page:int=Query(1,ge=1),limit:int=Query(20,ge=1,le=100),user=Depends(current_user)):
    run=access.require_run(run_id,user,complete=True);validate_target(run,user,target_kind,target_id)
    query=select(store.comments,store.users.c.username.label('author')).outerjoin(store.users,store.users.c.id==store.comments.c.author_id).where(
        store.comments.c.run_id==run_id,store.comments.c.target_kind==target_kind,store.comments.c.target_id==target_id).order_by(store.comments.c.created_at.desc(),store.comments.c.id)
    with store.engine.connect() as conn: return paged(conn,query,page,limit)

@router.post('/{run_id}/comments',status_code=201)
def add_comment(run_id:str,body:Comment,user=Depends(require_writer)):
    run=access.require_run(run_id,user,complete=True);validate_target(run,user,body.target_kind,body.target_id)
    values={'id':str(uuid.uuid4()),'run_id':run_id,'author_id':user['id'],'revision':1,'created_at':store.now(),'updated_at':store.now(),**body.model_dump()}
    with transaction() as conn:
        locked_access(conn,run_id,user)
        conn.execute(store.comments.insert().values(**values))
        audit.record(conn,user['id'],'comment.create',values['id'],run_id=run_id,target_kind=body.target_kind,target_id=body.target_id)
    return values

@router.put('/{run_id}/comments/{comment_id}')
def edit_comment(run_id:str,comment_id:str,body:CommentEdit,user=Depends(require_writer)):
    with transaction() as conn:
        locked_access(conn,run_id,user)
        row=conn.execute(select(store.comments).where(store.comments.c.id==comment_id,store.comments.c.run_id==run_id)).mappings().first()
        if not row or row['author_id']!=user['id']: raise HTTPException(404,'Свой комментарий не найден')
        if row['revision']!=body.revision: raise HTTPException(409,'Комментарий уже изменён')
        conn.execute(update(store.comments).where(store.comments.c.id==comment_id).values(body=body.body,revision=body.revision+1,updated_at=store.now()))
        audit.record(conn,user['id'],'comment.edit',comment_id,run_id=run_id,revision=body.revision+1)
    return {'revision':body.revision+1}

@router.delete('/{run_id}/comments/{comment_id}')
def delete_comment(run_id:str,comment_id:str,revision:int=Query(ge=1),user=Depends(require_writer)):
    with transaction() as conn:
        locked_access(conn,run_id,user)
        row=conn.execute(select(store.comments).where(store.comments.c.id==comment_id,store.comments.c.run_id==run_id)).mappings().first()
        if not row or row['author_id']!=user['id']: raise HTTPException(404,'Свой комментарий не найден')
        if row['revision']!=revision: raise HTTPException(409,'Комментарий уже изменён')
        conn.execute(delete(store.comments).where(store.comments.c.id==comment_id))
        audit.record(conn,user['id'],'comment.delete',comment_id,run_id=run_id)
    return {'deleted':True}

class Review(Input):
    status:Literal['in_review','needs_changes','approved']
    assignee:str=Field(min_length=3,max_length=40)
    reason:str=Field(min_length=1,max_length=4000)
    revision:int=Field(ge=0)

@router.get('/{run_id}/review')
def get_review(run_id:str,user=Depends(current_user)):
    access.require_run(run_id,user,complete=True)
    with store.engine.connect() as conn:
        row=conn.execute(select(store.reviews,store.users.c.username.label('assignee'),store.users.c.blocked.label('assignee_blocked'))
            .outerjoin(store.users,store.users.c.id==store.reviews.c.assignee_id).where(store.reviews.c.run_id==run_id)).mappings().first()
    return dict(row) if row else {'status':'draft','revision':0}

@router.put('/{run_id}/review')
def review(run_id:str,body:Review,user=Depends(require_writer)):
    access.require_run(run_id,user,complete=True)
    with transaction() as conn:
        run=locked_access(conn,run_id,user)
        old=conn.execute(select(store.reviews).where(store.reviews.c.run_id==run_id)).mappings().first()
        if body.revision!=(old['revision'] if old else 0): raise HTTPException(409,'Рассмотрение уже изменено. Обновите статус.')
        target=conn.execute(select(store.users).where(store.users.c.username==body.assignee.lower())).mappings().first()
        if not target or target['blocked'] or target['role']=='observer': raise HTTPException(422,'Ответственным должен быть действующий пользователь')
        shared=conn.execute(select(store.run_shares.c.user_id).where(store.run_shares.c.run_id==run_id,store.run_shares.c.user_id==target['id'])).first()
        if target['id']!=run['owner_id'] and not shared: raise HTTPException(422,'Сначала предоставьте ответственному доступ к анализу')
        is_owner=run['owner_id']==user['id']
        if not old or body.status=='in_review' or old['assignee_id']!=target['id']:
            if not is_owner or body.status!='in_review': raise HTTPException(403,'Владелец назначает ответственного и отправляет анализ на проверку')
        elif user['id']!=old['assignee_id']: raise HTTPException(403,'Результат рассмотрения сохраняет ответственный')
        values={'status':body.status,'assignee_id':target['id'],'reason':body.reason,'revision':body.revision+1,'updated_at':store.now(),'updated_by':user['id']}
        if old: conn.execute(update(store.reviews).where(store.reviews.c.run_id==run_id).values(**values))
        else: conn.execute(store.reviews.insert().values(run_id=run_id,**values))
        audit.record(conn,user['id'],'review.change',run_id,run_id=run_id,**values)
    return values
