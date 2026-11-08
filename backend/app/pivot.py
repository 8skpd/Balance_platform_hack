"""Bounded cross-tab queries. Every distinct-app total is computed from underlying rows."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update, delete

from . import store
from .auth import current_user, require_writer
from .mart import records
from .queueing import transaction

FIELDS = {
    'PRODUCT_ID':'Продукт', 'SOURCE':'Срез', 'DIFF_FLAG':'Признак различия', 'SUFFIX':'Продолжение пути', 'ADJUSTED_VALUE':'Значение / интервал',
    'ROUTING_METHOD':'Маршрутизация', 'TOP_OFFER_BEFORE':'Первый оффер до', 'TOP_OFFER_AFTER':'Первый оффер после',
    'CUTOFF_BEFORE':'Порог до', 'CUTOFF_AFTER':'Порог после', 'SCORE_OKB_BEFORE':'Внешний скор до', 'SCORE_OKB_AFTER':'Внешний скор после',
    'REJECT_REASON_BEFORE':'Причина отказа до', 'REJECT_REASON_AFTER':'Причина отказа после',
    'PATH':'Путь атрибута', 'PREFIX':'Блок ответа', 'CLASS':'Класс', 'KEY':'Имя атрибута',
    'ARRAY_KEY':'Ключ массива', 'CHANGE_TYPE':'Тип изменения',
    'VALUE_BEFORE':'Значение BEFORE', 'VALUE_AFTER':'Значение AFTER',
    'TYPE_BEFORE':'Тип значения BEFORE', 'TYPE_AFTER':'Тип значения AFTER',
    'ADJUSTED_BEFORE':'Бакет BEFORE', 'ADJUSTED_AFTER':'Бакет AFTER',
    'DECISION_BEFORE':'Решение BEFORE', 'DECISION_AFTER':'Решение AFTER',
    'PD_MODEL_BEFORE':'Модель BEFORE', 'PD_MODEL_AFTER':'Модель AFTER',
    'PRODUCTS':'Набор продуктов', 'PARTNER_TYPE':'Набор типов партнёра',
}
from .pipeline_data import SEGMENT_FIELDS, FACT_FIELDS
APP_FIELDS=set(SEGMENT_FIELDS)
METRICS = {'apps':'Уникальных заявок', 'attributes':'Атрибутов', 'changed_apps':'Заявок с различиями',
           'changed_attributes':'Изменённых атрибутов', 'decision_apps':'Заявок со сменой решения'}
AGG = {'apps':'count(DISTINCT APP_ID)', 'attributes':'count(DISTINCT (APP_ID,PATH,ARRAY_KEY)) FILTER(WHERE PATH IS NOT NULL)',
       'changed_apps':'count(DISTINCT APP_ID) FILTER(WHERE DIFF_FLAG=1)',
       'changed_attributes':'count(DISTINCT (APP_ID,PATH,ARRAY_KEY)) FILTER(WHERE DIFF_FLAG=1)',
       'decision_apps':'count(DISTINCT APP_ID) FILTER(WHERE DECISION_CHANGED=1)'}
AGG_SQL = ', '.join(f'{expr} AS {name}' for name,expr in AGG.items())

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Filter(StrictModel):
    field: str
    op: Literal['eq','in','contains','starts_with','is_null','not_null'] = 'eq'
    values: list[str | None] = Field(default_factory=list, max_length=50)

    @model_validator(mode='after')
    def validate_filter(self):
        if self.field not in FIELDS:
            raise ValueError('Неизвестное поле фильтра')
        if any(v is not None and len(v)>1000 for v in self.values):
            raise ValueError('Значение фильтра длиннее 1000 символов')
        if self.op in ('is_null','not_null'):
            if self.values: raise ValueError('Для проверки NULL значения не нужны')
        elif self.op == 'in':
            if not self.values: raise ValueError('Укажите хотя бы одно значение')
        elif len(self.values)!=1:
            raise ValueError('Укажите одно значение')
        if self.op in ('contains','starts_with') and not self.values[0]:
            raise ValueError('Укажите непустую строку поиска')
        return self

class Spec(StrictModel):
    rows: list[str] = Field(default_factory=lambda:['PATH'], max_length=6)
    columns: list[str] = Field(default_factory=lambda:['CHANGE_TYPE'], max_length=3)
    metrics: list[str] = Field(default_factory=lambda:['apps'], min_length=1, max_length=3)
    filters: list[Filter] = Field(default_factory=list, max_length=12)
    changed_only: bool = True
    sort: Literal['apps_desc','attributes_desc','key_asc'] = 'apps_desc'

    @model_validator(mode='after')
    def validate_spec(self):
        dimensions=self.rows+self.columns
        if len(set(dimensions))!=len(dimensions) or set(dimensions)-set(FIELDS):
            raise ValueError('Поля строк и колонок должны быть известными и не повторяться')
        if len(set(self.metrics))!=len(self.metrics) or set(self.metrics)-set(METRICS):
            raise ValueError('Выберите разные показатели из списка')
        return self

class PivotQuery(StrictModel):
    spec: Spec = Field(default_factory=Spec)
    row_page: int = Field(default=1, ge=1, le=1000000)
    column_page: int = Field(default=1, ge=1, le=1000000)
    row_limit: int = Field(default=25, ge=1, le=50)
    column_limit: int = Field(default=8, ge=1, le=12)

class DrillQuery(StrictModel):
    spec: Spec
    row: list[str | None] | None = Field(default=None, max_length=6)
    column: list[str | None] | None = Field(default=None, max_length=3)
    metric: str = 'apps'
    page: int = Field(default=1, ge=1, le=1000000)
    limit: int = Field(default=30, ge=1, le=100)

    @model_validator(mode='after')
    def validate_cell(self):
        if self.metric not in METRICS: raise ValueError('Неизвестный показатель')
        for fields,values in [(self.spec.rows,self.row),(self.spec.columns,self.column)]:
            if values is not None and len(fields)!=len(values): raise ValueError('Ключ ячейки не соответствует полям')
            if values and any(v is not None and len(v)>50000 for v in values): raise ValueError('Слишком длинный ключ')
        return self

def base_sql(spec,conn=None):
    modern=conn is not None and bool(conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='fact_members'").fetchone()[0])
    product_axis='PRODUCT_ID' in spec.rows+spec.columns or any(f.field=='PRODUCT_ID' for f in spec.filters)
    fields=[]
    for f in FIELDS:
        if f=='PRODUCT_ID':
            fields.append('products.PRODUCT_ID AS PRODUCT_ID' if product_axis else 'NULL::VARCHAR AS PRODUCT_ID');continue
        if f=='DIFF_FLAG':continue
        if modern:alias='a' if f in APP_FIELDS else 'f' if f in FACT_FIELDS else 'c'
        else:alias='a' if f in APP_FIELDS else 'n' if f in ('SOURCE','ADJUSTED_VALUE') else 'c'
        fields.append(f'CAST({alias}."{f}" AS VARCHAR) AS "{f}"')
    if modern:
        source='SELECT a.APP_ID,coalesce(f.DIFF_FLAG,0) DIFF_FLAG,a.DECISION_CHANGED,'+', '.join(fields)+          ' FROM applications a LEFT JOIN (fact_members m JOIN facts f USING(FACT_ID)) ON m.APP_ID=a.APP_ID LEFT JOIN comparison c ON c.APP_ID=m.APP_ID AND c.PATH=m.PATH AND c.ARRAY_KEY=m.ARRAY_KEY'
    else:
        source='SELECT a.APP_ID,coalesce(c.DIFF_FLAG,0) DIFF_FLAG,a.DECISION_CHANGED,'+', '.join(fields)+          ' FROM applications a LEFT JOIN comparison c USING(APP_ID) LEFT JOIN normalized n USING(APP_ID,PATH,ARRAY_KEY)'
    if product_axis:source+=" LEFT JOIN LATERAL unnest(string_split("+'a'+".PRODUCTS,';')) products(PRODUCT_ID) ON TRUE"
    if modern:source+=' WHERE m.APP_ID IS NOT NULL OR a.compared_attributes=0'
    clauses=['DIFF_FLAG=1'] if spec.changed_only else []
    args=[]
    for f in spec.filters:
        col=f'CAST("{f.field}" AS VARCHAR)' if f.field=='DIFF_FLAG' else f'"{f.field}"'
        if f.op in ('is_null','not_null'):
            clauses.append(col+(' IS NULL' if f.op=='is_null' else ' IS NOT NULL'))
        elif f.op in ('eq','in'):
            clauses.append('('+' OR '.join(f'{col} IS NOT DISTINCT FROM ?' for _ in f.values)+')');args.extend(f.values)
        else:
            clauses.append(f'{"contains" if f.op=="contains" else "starts_with"}(lower({col}), lower(?))');args.append(f.values[0])
    return 'WITH base AS (SELECT * FROM ('+source+') source'+(' WHERE '+' AND '.join(clauses) if clauses else '')+') ',args

def axis(conn, base, args, fields, page, limit, sort):
    if not fields:
        return {'items':[{'key':[], **records(conn.execute(base+'SELECT '+AGG_SQL+' FROM base',args))[0]}], 'total':1, 'page':1, 'pages':1}
    cols=', '.join(f'"{f}"' for f in fields)
    count=conn.execute(base+f'SELECT count(*) FROM (SELECT DISTINCT {cols} FROM base)',args).fetchone()[0]
    pages=max(1,(count+limit-1)//limit);page=min(page,pages)
    order=(', '.join(("CASE \"SOURCE\" WHEN 'BEFORE' THEN 0 WHEN 'AFTER' THEN 1 ELSE 2 END, "
                     if f=='SOURCE' else '')+f'"{f}" NULLS FIRST' for f in fields))
    if sort!='key_asc': order=('apps' if sort=='apps_desc' else 'attributes')+' DESC, '+order
    query=base+f'SELECT {cols}, {AGG_SQL} FROM base GROUP BY {cols} ORDER BY {order} LIMIT ? OFFSET ?'
    result=records(conn.execute(query,args+[limit,(page-1)*limit]))
    return {'items':[{'key':[str(r[f]) if r[f] is not None else None for f in fields], **{k:r[k] for k in METRICS}} for r in result], 'total':count,'page':page,'pages':pages}

def keys_predicate(fields, keys):
    if not fields: return 'TRUE',[]
    if not keys: return 'FALSE',[]
    return '('+' OR '.join('('+' AND '.join(f'"{f}" IS NOT DISTINCT FROM ?' for f in fields)+')' for _ in keys)+')',[v for key in keys for v in key]

def matrix(conn, query):
    spec=query.spec;base,args=base_sql(spec,conn)
    rows=axis(conn,base,args,spec.rows,query.row_page,query.row_limit,spec.sort)
    columns=axis(conn,base,args,spec.columns,query.column_page,query.column_limit,'key_asc')
    grand=records(conn.execute(base+'SELECT '+AGG_SQL+' FROM base',args))[0]
    rwhere,rargs=keys_predicate(spec.rows,[r['key'] for r in rows['items']])
    cwhere,cargs=keys_predicate(spec.columns,[c['key'] for c in columns['items']])
    dims=spec.rows+spec.columns;fields=', '.join(f'"{f}"' for f in dims)
    sql=base+'SELECT '+(fields+', ' if fields else '')+AGG_SQL+' FROM base WHERE '+rwhere+' AND '+cwhere
    if fields: sql+=' GROUP BY '+fields
    values=records(conn.execute(sql,args+rargs+cargs))
    cells=[{'row':[str(v[f]) if v[f] is not None else None for f in spec.rows], 'column':[str(v[f]) if v[f] is not None else None for f in spec.columns], 'values':{k:v[k] for k in METRICS}} for v in values]
    return {'spec':spec.model_dump(),'rows':rows,'columns':columns,'cells':cells,'grand':grand}

def drill(conn, query):
    base,args=base_sql(query.spec,conn);clauses=[]
    for fields,key in [(query.spec.rows,query.row),(query.spec.columns,query.column)]:
        if key is not None:
            sql,params=keys_predicate(fields,[key]);clauses.append(sql);args+=params
    if query.metric in ('changed_apps','changed_attributes'):clauses.append('DIFF_FLAG=1')
    if query.metric=='decision_apps':clauses.append('DECISION_CHANGED=1')
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    scoped='SELECT DISTINCT APP_ID FROM base'+where
    total=conn.execute(base+'SELECT count(*) FROM ('+scoped+')',args).fetchone()[0]
    pages=max(1,(total+query.limit-1)//query.limit);page=min(query.page,pages)
    items=records(conn.execute(base+'SELECT a.* FROM applications a JOIN ('+scoped+') matched USING(APP_ID) ORDER BY a.APP_ID LIMIT ? OFFSET ?',args+[query.limit,(page-1)*query.limit]))
    return {'items':items,'total':total,'page':page,'pages':pages,'limit':query.limit}


class AttributeQuery(DrillQuery):
    app_id:str=Field(min_length=1,max_length=200)
    changed:bool=False
    search:str=Field(default='',max_length=2000)

def attributes(conn,query):
    base,args=base_sql(query.spec,conn);clauses=['APP_ID=?'];args.append(query.app_id)
    for fields,key in [(query.spec.rows,query.row),(query.spec.columns,query.column)]:
        if key is not None:
            sql,params=keys_predicate(fields,[key]);clauses.append(sql);args+=params
    if query.metric in ('changed_apps','changed_attributes'):clauses.append('DIFF_FLAG=1')
    if query.metric=='decision_apps':clauses.append('DECISION_CHANGED=1')
    if query.changed:clauses.append('DIFF_FLAG=1')
    if query.search:
        clauses.append('(contains(lower(PATH),lower(?)) OR contains(lower(ARRAY_KEY),lower(?)))');args.extend([query.search,query.search])
    scoped='SELECT DISTINCT APP_ID,PATH,ARRAY_KEY FROM base WHERE '+' AND '.join(clauses)
    match=' FROM comparison c JOIN ('+scoped+') chosen USING(APP_ID,PATH,ARRAY_KEY)'
    total=conn.execute(base+'SELECT count(*)'+match,args).fetchone()[0]
    pages=max(1,(total+query.limit-1)//query.limit);page=min(query.page,pages)
    items=records(conn.execute(base+'SELECT c.*'+match+' ORDER BY c.PATH,c.ARRAY_KEY LIMIT ? OFFSET ?',args+[query.limit,(page-1)*query.limit]))
    apps=records(conn.execute('SELECT * FROM applications WHERE APP_ID=?',[query.app_id]))
    if not apps:raise HTTPException(404,'Заявка не найдена')
    return {'items':items,'total':total,'page':page,'limit':query.limit,'segment':apps[0]}

router=APIRouter(prefix='/api/pivot-views',dependencies=[Depends(current_user)])
class ViewInput(StrictModel):
    name: str = Field(min_length=1,max_length=120)
    spec: Spec
    @model_validator(mode='after')
    def trim_name(self):
        self.name=self.name.strip()
        if not self.name:raise ValueError('Название не может быть пустым')
        return self
class ViewUpdate(ViewInput):
    expected_revision: int = Field(ge=1)

@router.get('')
def list_views(user:dict=Depends(current_user)):
    with store.engine.connect() as conn:
        return [{k:v for k,v in dict(r).items() if k!='owner_id'} for r in conn.execute(select(store.pivot_views).where(store.pivot_views.c.owner_id==user['id']).order_by(store.pivot_views.c.updated_at.desc())).mappings()]

@router.post('',status_code=201)
def create_view(body:ViewInput,user:dict=Depends(require_writer)):
    value={'id':str(uuid.uuid4()),'name':body.name,'spec':body.spec.model_dump(),'revision':1,'created_at':store.now(),'updated_at':store.now()}
    with store.engine.begin() as conn:conn.execute(store.pivot_views.insert().values(**value,owner_id=user['id']))
    return value

def locked_view(conn,view_id,user,revision):
    value=conn.execute(select(store.pivot_views).where(store.pivot_views.c.id==view_id,store.pivot_views.c.owner_id==user['id']).with_for_update()).mappings().first()
    if not value:raise HTTPException(404,'Представление не найдено')
    if value['revision']!=revision:raise HTTPException(409,'Представление изменено в другой вкладке. Обновите список и загрузите актуальную версию.')
    return value

@router.put('/{view_id}')
def update_view(view_id:str,body:ViewUpdate,user:dict=Depends(require_writer)):
    with transaction() as conn:
        value=locked_view(conn,view_id,user,body.expected_revision)
        new={'name':body.name,'spec':body.spec.model_dump(),'revision':value['revision']+1,'updated_at':store.now()}
        conn.execute(update(store.pivot_views).where(store.pivot_views.c.id==view_id).values(**new))
        return {**{k:v for k,v in value.items() if k!='owner_id'},**new}

@router.delete('/{view_id}')
def delete_view(view_id:str,expected_revision:int,user:dict=Depends(require_writer)):
    with transaction() as conn:
        locked_view(conn,view_id,user,expected_revision)
        conn.execute(delete(store.pivot_views).where(store.pivot_views.c.id==view_id))
    return {'deleted':True}
