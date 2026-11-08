"""Owner-scoped Python procedures; every run captures its complete execution sources."""
import ast
import hashlib
import json
import uuid
from pathlib import Path
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import select,update,delete
from . import store
from .auth import require_writer
from .queueing import transaction

STAGES={'normalization':'Приведение данных','comparison':'Сравнение атрибутов','aggregation':'Подготовка сводной'}
EXAMPLES={
'normalization':'''def run(document, app_id, source, config, params):
    # Здесь можно преобразовать документ или строки результата.
    return defaults.normalize(document, app_id, source, config)

def segments(app_id, before, after, config, params):
    # Здесь задаются решение, продукты, партнёры и другие разрезы заявки.
    return defaults.segments(app_id, before, after)

PROCEDURE = {"entrypoint": run, "segments": segments}
''',
'comparison':'''def run(data, params):
    # data.normalized(), data.segments(): поток строк входной витрины.
    for row in data.default_comparison():
        yield row

PROCEDURE = {"entrypoint": run}
''',
'aggregation':'''def run(data, params):
    # Факт содержит измерения, CNT и полный список APP_IDS.
    # APP_IDS сохраняет переход от факта к заявке.
    for fact in data.default_facts():
        yield fact

PROCEDURE = {"entrypoint": run}
'''}

class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(min_length=1,max_length=100)
    revision:int=Field(ge=1)
    params:dict=Field(default_factory=dict)
    @model_validator(mode='after')
    def valid(self):
        from .library import valid_params
        valid_params(self.params);return self

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')
    stage:Literal['normalization','comparison','aggregation']
    name:str=Field(min_length=1,max_length=120)
    code:str=Field(min_length=1,max_length=100000)
    params:dict=Field(default_factory=dict)
    filename:str=Field(default='',max_length=160)
    timeout:int=Field(default=600,ge=1,le=3600)
    memory_mb:int=Field(default=2048,ge=512,le=3072)
    @model_validator(mode='after')
    def valid(self):
        from .library import valid_params
        valid_params(self.params)
        try: ast.parse(self.code)
        except (SyntaxError,ValueError,RecursionError) as exc:raise ValueError('Некорректный Python: '+str(exc)) from exc
        if self.filename and (Path(self.filename).name!=self.filename or not self.filename.lower().endswith('.py')):raise ValueError('Выберите файл .py без пути')
        return self
class Edit(Input):
    expected_revision:int=Field(ge=1)

router=APIRouter(prefix='/api/procedures',dependencies=[Depends(require_writer)])
@router.get('/catalog')
def catalog():return {'stages':STAGES,'examples':EXAMPLES,'contracts':{
 'normalization':'run(document, app_id, source, config, params) → строки нормализации; segments(app_id, before, after, config, params) → разрезы заявки. Доступен defaults.',
 'comparison':'run(data, params) → строки сравнения. Доступны data.normalized(), data.segments(), data.default_comparison(). Ключ APP_ID + PATH + ARRAY_KEY сохраняется.',
 'aggregation':'run(data, params) → факты с измерениями, CNT и APP_IDS. Доступны data.comparison(), data.default_facts(). CNT = числу уникальных APP_IDS.'}}
@router.get('')
def inventory(user:dict=Depends(require_writer)):
    with store.engine.connect() as conn:return [dict(r) for r in conn.execute(select(store.procedure_library).where(store.procedure_library.c.owner_id==user['id']).order_by(store.procedure_library.c.name)).mappings()]
@router.post('',status_code=201)
def create(body:Input,user:dict=Depends(require_writer)):
    from .code_validation import require_valid
    require_valid(body.stage,body.code)
    row={**body.model_dump(),'id':str(uuid.uuid4()),'owner_id':user['id'],'revision':1,'created_at':store.now(),'updated_at':store.now()}
    with store.engine.begin() as conn:conn.execute(store.procedure_library.insert().values(**row))
    return row
@router.put('/{procedure_id}')
def edit(procedure_id:str,body:Edit,user:dict=Depends(require_writer)):
    from .code_validation import require_valid
    require_valid(body.stage,body.code)
    with transaction() as conn:
        row=conn.execute(select(store.procedure_library).where(store.procedure_library.c.id==procedure_id,store.procedure_library.c.owner_id==user['id']).with_for_update()).mappings().first()
        if not row:raise HTTPException(404,'Процедура не найдена')
        if row['revision']!=body.expected_revision:raise HTTPException(409,'Процедура изменена. Обновите список или сохраните новый экземпляр.')
        values=body.model_dump(exclude={'expected_revision'});values.update(revision=row['revision']+1,updated_at=store.now())
        conn.execute(update(store.procedure_library).where(store.procedure_library.c.id==procedure_id).values(**values))
    return {**dict(row),**values}
@router.delete('/{procedure_id}')
def remove(procedure_id:str,user:dict=Depends(require_writer)):
    with store.engine.begin() as conn:
        count=conn.execute(delete(store.procedure_library).where(store.procedure_library.c.id==procedure_id,store.procedure_library.c.owner_id==user['id'])).rowcount
    if not count:raise HTTPException(404,'Процедура не найдена')
    return {'deleted':True}

def resolve(user,selections):
    if set(selections)-set(STAGES):raise HTTPException(422,'Неизвестный этап процедуры')
    folder=Path(__file__).parent
    helpers={name:(folder/(name+'.py')).read_text() for name in ('normalizer','pipeline_data')}
    plan={}
    with store.engine.connect() as conn:
        for stage,name in STAGES.items():
            spec={'kind':'builtin','stage':stage,'name':name,'code':EXAMPLES[stage],'params':{},'revision':1,'timeout':600,'memory_mb':2048,'helpers':helpers}
            if stage in selections:
                choice=selections[stage]
                row=conn.execute(select(store.procedure_library).where(store.procedure_library.c.id==choice.id,store.procedure_library.c.owner_id==user['id'])).mappings().first()
                if not row:raise HTTPException(404,'Выбранная процедура недоступна')
                if row['stage']!=stage:raise HTTPException(422,'Процедура предназначена для другого этапа')
                if row['revision']!=choice.revision:raise HTTPException(409,'Выбранная процедура изменилась. Выберите актуальную редакцию.')
                spec.update({k:row[k] for k in ('id','stage','name','code','params','revision','timeout','memory_mb','filename')},kind='python')
                spec['params']={**row['params'],**choice.params}
                from .code_validation import require_valid
                from .library import valid_params
                require_valid(stage,spec['code'])
                try: valid_params(spec['params'])
                except ValueError as exc: raise HTTPException(422,str(exc)) from exc
            spec['sha256']=hashlib.sha256(json.dumps({'code':spec['code'],'helpers':helpers},sort_keys=True).encode()).hexdigest()
            plan[stage]=spec
    return plan

def public(plan):
    return {k:{x:y for x,y in v.items() if x not in ('code','helpers')} for k,v in (plan or {}).items()}
