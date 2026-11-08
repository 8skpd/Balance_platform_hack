"""Explicit owner cleanup; referenced datasets and active work are protected."""
import json
import shutil
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select,delete
from . import store,audit
from .auth import require_writer
from .operations import Input,directory_size
from .queueing import transaction,TERMINAL
from .workflow import locked_access,idle
from .maintenance import gate
from .settings import DATA,RUNS,JOBS,DATASETS,UPLOADS

router=APIRouter(prefix='/api/storage')
class Cleanup(Input):
    kind: str=Field(pattern='^(run|job|dataset)$')
    id: str=Field(min_length=1,max_length=150,pattern=r'^[a-zA-Z0-9_-]+$')
    confirmation:str=Field(min_length=1,max_length=200)

def candidates(conn,user):
    rows=[]
    for run in conn.execute(select(store.runs).where(store.runs.c.owner_id==user['id'],store.runs.c.archived==True)).mappings():
        rows.append({'kind':'run','id':run['id'],'name':run['name'],'bytes':directory_size(RUNS/run['id']), 'note':'Удаляет отчёт, комментарии, согласование, доступ и историю ИИ. Журнал действий сохраняется.'})
    for job in conn.execute(select(store.jobs).where(store.jobs.c.owner_id==user['id'],store.jobs.c.status.in_(TERMINAL))).mappings():
        size=directory_size(JOBS/job['id'])
        if size: rows.append({'kind':'job','id':job['id'],'name':job['id'],'bytes':size,'note':'Удаляет временные файлы и логи задачи. Результат и история ИИ сохраняются.'})
    for d in conn.execute(select(store.dataset_imports).where(store.dataset_imports.c.owner_id==user['id'])).mappings():
        ref=conn.execute(select(store.runs.c.id).where(store.runs.c.dataset==d['id'])).first()
        active=conn.execute(select(store.jobs.c.id).where(store.jobs.c.dataset_id==d['id'],store.jobs.c.status.not_in(TERMINAL))).first()
        if not ref and not active: rows.append({'kind':'dataset','id':d['id'],'name':d['name'],'bytes':directory_size(DATASETS/d['id']), 'note':'Не используется ни в одном анализе, включая архив и переданные анализы.'})
    return rows

@router.get('/candidates')
def preview(user=Depends(require_writer)):
    with store.engine.connect() as conn: return candidates(conn,user)

@router.post('/cleanup')
def cleanup(body:Cleanup,user=Depends(require_writer)):
    moved=[];trash=DATA/'trash'/str(uuid.uuid4())
    try:
        with gate(exclusive=True):
            try:
                with transaction() as conn:
                    paths=[]
                    if body.kind=='run':
                        run=locked_access(conn,body.id,user,owner=True);idle(conn,body.id)
                        if not run['archived']: raise HTTPException(409,'Сначала перенесите анализ в архив')
                        name=run['name']
                        ids=conn.execute(select(store.jobs.c.id).where(store.jobs.c.run_id==body.id)).scalars().all()
                        paths=[RUNS/body.id,*(JOBS/x for x in ids)]
                        for table in (store.comments,store.reviews,store.run_shares,store.jobs): conn.execute(delete(table).where(table.c.run_id==body.id))
                        conn.execute(delete(store.runs).where(store.runs.c.id==body.id))
                    elif body.kind=='job':
                        job=conn.execute(select(store.jobs).where(store.jobs.c.id==body.id,store.jobs.c.owner_id==user['id'])).mappings().first()
                        if not job: raise HTTPException(404,'Задача не найдена')
                        if job['status'] not in TERMINAL: raise HTTPException(409,'Задача ещё выполняется')
                        name=body.id;paths=[JOBS/body.id]
                    else:
                        d=conn.execute(select(store.dataset_imports).where(store.dataset_imports.c.id==body.id,store.dataset_imports.c.owner_id==user['id'])).mappings().first()
                        if not d: raise HTTPException(404,'Приватный датасет не найден')
                        if conn.execute(select(store.runs.c.id).where(store.runs.c.dataset==body.id)).first(): raise HTTPException(409,'Датасет используется анализом, в том числе архивным или переданным')
                        jobs=conn.execute(select(store.jobs).where(store.jobs.c.dataset_id==body.id)).mappings().all()
                        if any(j['status'] not in TERMINAL for j in jobs): raise HTTPException(409,'Импорт ещё выполняется')
                        name=d['name'];paths=[DATASETS/body.id,*(JOBS/j['id'] for j in jobs)]
                        if not conn.execute(select(store.dataset_imports.c.id).where(store.dataset_imports.c.upload_id==d['upload_id'],store.dataset_imports.c.id!=body.id)).first(): paths.append(UPLOADS/(d['upload_id']+'.zip'))
                        conn.execute(delete(store.jobs).where(store.jobs.c.dataset_id==body.id))
                        conn.execute(delete(store.dataset_imports).where(store.dataset_imports.c.id==body.id))
                    if body.confirmation!=name: raise HTTPException(422,'Введите точное название для подтверждения удаления')
                    trash.mkdir(parents=True)
                    journal={'kind':body.kind,'id':body.id,'paths':[str(p.relative_to(DATA)) for p in paths]}
                    (trash/'journal.json').write_text(json.dumps(journal))
                    for i,path in enumerate(paths):
                        if path.exists():
                            destination=trash/str(i);path.rename(destination);moved.append((path,destination))
                    audit.record(conn,user['id'],'storage.cleanup',body.id,run_id=body.id if body.kind=='run' else None,kind=body.kind,name=name,cleanup_id=trash.name)
            except Exception:
                for path,destination in reversed(moved):
                    if destination.exists(): destination.rename(path)
                if trash.exists(): shutil.rmtree(trash,ignore_errors=True)
                raise
            else:
                if trash.exists(): shutil.rmtree(trash,ignore_errors=True)
    except BlockingIOError: raise HTTPException(409,'Сервер занят запросом или задачей. Повторите очистку позже.')
    return {'deleted':True}


def recover_cleanup():
    """After a crash, restore staged files if their DB deletion did not commit."""
    trash_root=DATA/'trash'
    if not trash_root.exists(): return
    folders=[p for p in trash_root.iterdir() if (p/'journal.json').is_file()]
    if not folders: return
    with gate(exclusive=True):
        for folder in folders:
            journal_path=folder/'journal.json'
            if not journal_path.is_file(): continue
            journal=json.loads(journal_path.read_text())
            with store.engine.connect() as conn:
                events=conn.execute(select(store.audit_events.c.details).where(store.audit_events.c.action=='storage.cleanup',store.audit_events.c.entity_id==journal['id'])).scalars()
                committed=any(e.get('cleanup_id')==folder.name for e in events)
            if not committed:
                for i,relative in enumerate(journal['paths']):
                    source=folder/str(i);target=DATA/relative
                    if source.exists():
                        if target.exists(): raise RuntimeError('Конфликт восстановления очистки: '+str(target))
                        target.parent.mkdir(parents=True,exist_ok=True);source.rename(target)
            shutil.rmtree(folder)
