"""Pair analysis runs on APP_ID; keep population and check-version changes explicit."""
import json
import duckdb
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from . import store, access
from .auth import current_user
from .operations import paged
from .settings import RUNS

router=APIRouter(prefix='/api')

@router.get('/runs/{run_id}/ai-history')
def ai_history(run_id:str,page:int=Query(1,ge=1),limit:int=Query(10,ge=1,le=50),user=Depends(current_user)):
    access.require_run(run_id,user,complete=True)
    query=select(store.jobs.c.id,store.jobs.c.status,store.jobs.c.created_at,store.jobs.c.finished_at,
        store.jobs.c.params,store.jobs.c.result,store.jobs.c.error).where(store.jobs.c.run_id==run_id,store.jobs.c.kind=='ai').order_by(store.jobs.c.created_at.desc(),store.jobs.c.id.desc())
    with store.engine.connect() as conn: result=paged(conn,query,page,limit)
    for row in result['items']:
        params=row.pop('params') or {};answer=row['result'] or {}
        row.update(model=params.get('model') or answer.get('model'),context=params.get('context'),prompt_version=params.get('prompt_version'),requested_by=params.get('requested_by'),message=params.get('message'))
    return result

def check_groups(run):
    result={}
    for row in run['checks'] or []:
        key=(row['CHECK_ID'],row.get('SOURCE',''),row.get('ARGUMENT',''))
        result.setdefault(key,[]).append({k:row.get(k) for k in ('CHECK_NAME','LABEL','CHECK_VERSION','RESULT','CNT','TOTAL','METRIC','CONFIDENCE')})
    return result

@router.get('/run-comparison')
def compare(left:str,right:str,page:int=Query(1,ge=1),limit:int=Query(50,ge=1,le=100),search:str=Query('',max_length=200),user=Depends(current_user)):
    a=access.require_run(left,user,complete=True);b=access.require_run(right,user,complete=True)
    conn=duckdb.connect(':memory:')
    try:
        for alias,run_id in [('a',left),('b',right)]:
            path=str(RUNS/run_id/'result.duckdb').replace("'","''")
            conn.execute(f"ATTACH '{path}' AS {alias} (READ_ONLY)")
        base='''WITH attribute_changes AS (
            SELECT coalesce(x.APP_ID,y.APP_ID) APP_ID, count(*) attribute_differences
            FROM a.comparison x FULL JOIN b.comparison y USING(APP_ID,PATH,ARRAY_KEY)
            WHERE x.APP_ID IS NULL OR y.APP_ID IS NULL OR x.VALUE_BEFORE IS DISTINCT FROM y.VALUE_BEFORE
                OR x.VALUE_AFTER IS DISTINCT FROM y.VALUE_AFTER OR x.TYPE_BEFORE IS DISTINCT FROM y.TYPE_BEFORE
                OR x.TYPE_AFTER IS DISTINCT FROM y.TYPE_AFTER
            GROUP BY 1), pairs AS (SELECT coalesce(a.APP_ID,b.APP_ID) APP_ID,
            CASE WHEN a.APP_ID IS NULL THEN 'added' WHEN b.APP_ID IS NULL THEN 'removed'
                 WHEN coalesce(c.attribute_differences,0)>0 OR a.changed_attributes IS DISTINCT FROM b.changed_attributes OR a.DECISION_BEFORE IS DISTINCT FROM b.DECISION_BEFORE OR a.DECISION_AFTER IS DISTINCT FROM b.DECISION_AFTER
                 THEN 'changed' ELSE 'same' END category,
            a.changed_attributes left_changes,b.changed_attributes right_changes,coalesce(c.attribute_differences,0) attribute_differences,
            a.DECISION_BEFORE left_before,a.DECISION_AFTER left_after,b.DECISION_BEFORE right_before,b.DECISION_AFTER right_after
            FROM a.applications a FULL JOIN b.applications b USING(APP_ID) LEFT JOIN attribute_changes c ON c.APP_ID=coalesce(a.APP_ID,b.APP_ID)) '''
        counts=dict(conn.execute(base+'SELECT category,count(*) FROM pairs GROUP BY category').fetchall())
        where=" WHERE category!='same' AND contains(APP_ID, ?)"
        total=conn.execute(base+'SELECT count(*) FROM pairs'+where,[search]).fetchone()[0]
        cursor=conn.execute(base+'SELECT * FROM pairs'+where+' ORDER BY APP_ID LIMIT ? OFFSET ?',[search,limit,(page-1)*limit])
        items=[dict(zip([d[0] for d in cursor.description],r)) for r in cursor.fetchall()]
    finally: conn.close()
    ca,cb=check_groups(a),check_groups(b)
    checks=[]
    for key in sorted(ca.keys()|cb.keys()):
        before,after=ca.get(key,[]),cb.get(key,[])
        if before!=after: checks.append({'key':key[0],'source':key[1],'argument':key[2],'left':before,'right':after})
    warnings=[]
    if a['dataset']!=b['dataset']: warnings.append('Разные датасеты: сравнивайте общие APP_ID; добавленные и удалённые заявки показаны отдельно.')
    if a['config']!=b['config']: warnings.append('Различаются настройки нормализации: изменения могут быть вызваны правилами обработки.')
    if a.get('procedure_plan')!=b.get('procedure_plan'): warnings.append('Различаются процедуры обработки. Сравните исполненный код этапов.')
    if a['check_plan']!=b['check_plan']: warnings.append('Различаются состав, версии или параметры проверок. Отсутствие строки не означает PASS.')
    return {'left':{'id':left,'name':a['name'],'summary':a['summary']},'right':{'id':right,'name':b['name'],'summary':b['summary']},
        'counts':counts,'items':items,'total':total,'page':page,'limit':limit,'checks':checks,'warnings':warnings,
        'note':'Сравниваются значения и типы нормализованных атрибутов, число различий и решения по APP_ID. Меньше различий не означает более правильную стратегию.'}
