"""Persistent, report-scoped AI conversation built on the durable job history."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select,and_,or_
from . import store,access,queueing,ai,admin
from .auth import current_user,require_writer

router=APIRouter(prefix='/api/runs')
class Message(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    message:str=Field(min_length=1,max_length=4000)
    request_id:UUID

def conversation(run):
    """Only successful turns from the current report revision enter model context."""
    with store.engine.connect() as conn:
        rows=conn.execute(select(store.jobs.c.params,store.jobs.c.result).where(store.jobs.c.run_id==run['id'],store.jobs.c.kind=='ai',store.jobs.c.status=='completed').order_by(store.jobs.c.created_at.desc(),store.jobs.c.id.desc()).limit(30)).mappings().all()
    turns=[];size=0
    for row in rows:
        params=row['params'] or {};answer=row['result'] or {};content=answer.get('content')
        if params.get('report_revision',1)!=run.get('report_revision',1) or not content:continue
        question=params.get('message') or 'Проанализируй результаты этого отчёта.'
        turn=[{'role':'user','content':question[:4000]},{'role':'assistant','content':content[:8000]}]
        length=sum(len(m['content']) for m in turn)
        if size+length>24000:break
        turns.append(turn);size+=length
        if len(turns)==6:break
    return [message for turn in reversed(turns) for message in turn]

@router.post('/{run_id}/ai-chat',status_code=202)
def send_message(run_id:str,body:Message,user=Depends(require_writer)):
    run=access.require_run(run_id,user,complete=True,write=True)
    if run['archived']:raise HTTPException(409,'Сначала восстановите анализ из архива')
    request_id=str(body.request_id)
    with store.engine.connect() as conn:
        prior=conn.execute(select(store.jobs).where(store.jobs.c.run_id==run_id,store.jobs.c.kind=='ai',store.jobs.c.params['request_id'].as_string()==request_id)).mappings().first()
    if prior:
        if prior['params'].get('message')!=body.message:raise HTTPException(409,'Этот идентификатор уже использован для другого сообщения')
        return {'job_id':prior['id'],'status':prior['status']}
    settings=admin.ai_settings()
    params={'chat':True,'message':body.message,'request_id':request_id,'conversation':conversation(run),
            'context':ai.build_context(run),'report_revision':run.get('report_revision',1),'prompt_version':3,
            'ollama_url':settings['base_url'],'model':settings['model'],'requested_by':user['id']}
    try:job=queueing.submit(user['id'],run_id,'ai',params=params)
    except ValueError as exc:raise HTTPException(409,str(exc))
    return {'job_id':job['id'],'status':job['status']}

@router.get('/{run_id}/ai-chat')
def history(run_id:str,before:str|None=None,limit:int=Query(20,ge=1,le=50),user=Depends(current_user)):
    run=access.require_run(run_id,user,complete=True)
    query=select(store.jobs).where(store.jobs.c.run_id==run_id,store.jobs.c.kind=='ai')
    with store.engine.connect() as conn:
        if before:
            anchor=conn.execute(select(store.jobs.c.created_at,store.jobs.c.id).where(store.jobs.c.id==before,store.jobs.c.run_id==run_id,store.jobs.c.kind=='ai')).first()
            if not anchor:raise HTTPException(404,'Сообщение не найдено')
            query=query.where(or_(store.jobs.c.created_at<anchor.created_at,and_(store.jobs.c.created_at==anchor.created_at,store.jobs.c.id<anchor.id)))
        rows=conn.execute(query.order_by(store.jobs.c.created_at.desc(),store.jobs.c.id.desc()).limit(limit+1)).mappings().all()
        active=conn.execute(select(store.jobs.c.id,store.jobs.c.status).where(store.jobs.c.run_id==run_id,store.jobs.c.kind=='ai',store.jobs.c.status.not_in(queueing.TERMINAL))).mappings().first()
    has_more=len(rows)>limit;rows=rows[:limit];items=[]
    for row in reversed(rows):
        params=row['params'] or {};answer=row['result'] or {};revision=params.get('report_revision',1)
        items.append({'id':row['id'],'status':row['status'],'created_at':row['created_at'],'finished_at':row['finished_at'],
          'message':params.get('message'),'request_id':params.get('request_id'),'requested_by':params.get('requested_by'),
          'content':answer.get('content'),'model':answer.get('model') or params.get('model'),'truncated':answer.get('truncated',False),
          'error':row['error'],'report_revision':revision,'stale':revision!=run.get('report_revision',1)})
    legacy=run['ai'] or {};legacy=legacy if legacy.get('status')=='completed' else legacy.get('last_success') or {}
    return {'items':items,'next_before':rows[-1]['id'] if has_more else None,'active':dict(active) if active else None,
       'report_revision':run.get('report_revision',1),'can_send':access.can_manage(run,user) and not run['archived'],
       'legacy_answer':legacy if not rows and not before and legacy.get('content') else None}
