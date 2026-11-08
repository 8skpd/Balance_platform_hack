"""Personal reusable inputs; updates/deletion use optimistic revision checks."""
import uuid

from fastapi import HTTPException
from sqlalchemy import select, update, delete

from . import store, library, datasets, procedures
from .queueing import transaction

def require(conn, template_id, user, lock=False):
    query=select(store.run_templates).where(store.run_templates.c.id==template_id,store.run_templates.c.owner_id==user['id'])
    if lock:query=query.with_for_update()
    row=conn.execute(query).mappings().first()
    if not row:raise HTTPException(404,'Шаблон не найден')
    return dict(row)

def public(row):
    return {**{k:v for k,v in row.items() if k not in ('owner_id','payload')},**row['payload']}

def inventory(user):
    columns=[c for c in store.run_templates.c if c.name not in ('payload','owner_id')]
    with store.engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(*columns).where(store.run_templates.c.owner_id==user['id'])
            .order_by(store.run_templates.c.updated_at.desc(),store.run_templates.c.id)).mappings()]

def detail(template_id,user):
    with store.engine.connect() as conn:row=require(conn,template_id,user)
    result=public(row);issues=[]
    try:datasets.paths(result['spec']['dataset'],user['id'])
    except ValueError:issues.append('Датасет недоступен. Выберите другой набор в форме запуска.')
    try:
        library.resolve_plan(user,result['spec']['builtin_checks'],
            [library.Selection(**s) for s in result['spec']['user_checks']],result['spec']['config'])
    except HTTPException as exc:issues.append(str(exc.detail)+'. Исправьте выбор проверок перед запуском.')
    try:procedures.resolve(user,{stage:procedures.Selection(**value) for stage,value in result['spec'].get('procedures',{}).items()})
    except HTTPException as exc:issues.append(str(exc.detail))
    return {**result,'issues':issues}

def prepare(spec,user):
    try:datasets.paths(spec.dataset,user['id'])
    except ValueError as exc:raise HTTPException(404,str(exc))
    plan=library.resolve_plan(user,spec.builtin_checks,spec.user_checks,spec.config.model_dump())
    procedures.resolve(user,spec.procedures)
    saved=spec.model_dump()
    # Explicit IDs and complete version parameters; never switch to "latest" on apply.
    saved['builtin_checks']=[c['key'] for c in plan if c['kind']=='builtin']
    saved['user_checks']=[{'version_id':c['version_id'],'params':c['params']} for c in plan if c['kind']=='python']
    return {'spec':saved,'checks':[{'name':c['name'],'kind':c['kind'],'version':c['version'],
        'key':c['key'],'sha256':c['sha256']} for c in plan]}

def save(name,spec,user,template_id=None,revision=None):
    # Check ownership before inspecting submitted references on an update.
    if template_id:
        with store.engine.connect() as conn:require(conn,template_id,user)
    payload=prepare(spec,user)
    with transaction() as conn:
        if template_id:
            row=require(conn,template_id,user,lock=True)
            if row['revision']!=revision:raise HTTPException(409,'Шаблон изменён в другой вкладке. Примените актуальную редакцию или сохраните свои настройки как новый шаблон.')
            row.update(name=name,payload=payload,revision=revision+1,updated_at=store.now())
            conn.execute(update(store.run_templates).where(store.run_templates.c.id==template_id).values(
                name=name,payload=payload,revision=row['revision'],updated_at=row['updated_at']))
        else:
            row={'id':str(uuid.uuid4()),'owner_id':user['id'],'name':name,'payload':payload,
                'revision':1,'created_at':store.now(),'updated_at':store.now()}
            conn.execute(store.run_templates.insert().values(**row))
    return {**public(row),'issues':[]}

def remove(template_id,revision,user):
    with transaction() as conn:
        row=require(conn,template_id,user,lock=True)
        if row['revision']!=revision:raise HTTPException(409,'Шаблон изменён в другой вкладке. Обновите список перед удалением.')
        conn.execute(delete(store.run_templates).where(store.run_templates.c.id==template_id))
    return {'deleted':True}
