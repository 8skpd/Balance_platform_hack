"""Complete check membership and append-only publication of additional checks."""
import hashlib,json
from pathlib import Path
import duckdb
from sqlalchemy import select,update
from . import store,audit

def materialize(rows,conn,start=0,parquet=None):
    conn.execute('CREATE TABLE IF NOT EXISTS check_members(CHECK_ROW INTEGER,APP_ID VARCHAR)')
    for index,row in enumerate(rows,start):
        ids=sorted(set(row['APP_IDS']))
        for begin in range(0,len(ids),10000):conn.execute('INSERT INTO check_members SELECT ?,unnest(?::VARCHAR[])',[index,ids[begin:begin+10000]])
        row['MEMBERSHIP']={'kind':'file' if parquet else 'run','row':index,'count':len(ids),**({'file':parquet.name} if parquet else {})}
        row['APP_IDS']=ids[:100]
    if parquet:conn.execute('COPY check_members TO ? (FORMAT PARQUET)',[str(parquet)])

def publish(conn,job,result):
    run=conn.execute(select(store.runs).where(store.runs.c.id==job['run_id']).with_for_update()).mappings().first()
    if not run or run['owner_id']!=job['owner_id'] or run['archived']:raise ValueError('Анализ недоступен для добавления проверки')
    key='added-'+job['id'];name=(job.get('check_ref') or {}).get('name') or 'Пользовательская проверка'
    rows=[{**r,'CHECK_ID':key,'CHECK_NAME':name,'CHECK_KIND':'python','CHECK_VERSION':1} for r in result['rows']]
    checks=list(run['checks'] or [])+rows
    spec={'key':key,'kind':'python','name':name,'version':1,'code':job['code'],'params':job['params'],'timeout':job['timeout'],'memory_mb':job['memory_mb'],'sha256':hashlib.sha256(job['code'].encode()).hexdigest(),'added_at':store.now(),'job_id':job['id']}
    plan=list(run['check_plan'] or [])+[spec]
    executions=list(run['check_executions'] or [])+[{'key':key,'name':name,'kind':'python','version':1,'status':'completed','rows':len(rows)}]
    summary={**run['summary'],'checks_total':run['summary'].get('checks_total',len({r.get('CHECK_ID',r['LABEL']) for r in run['checks'] or []}))+1,'checks_failed':sum(r['RESULT']=='FAIL' for r in checks),'checks_passed':sum(r['RESULT']=='PASS' for r in checks)}
    revision=(run['report_revision'] or 1)+1
    ai={**(run['ai'] or {}),'stale':True} if run['ai'] else {'status':'idle'}
    conn.execute(update(store.runs).where(store.runs.c.id==run['id']).values(checks=checks,check_plan=plan,check_executions=executions,summary=summary,report_revision=revision,ai=ai))
    # An old approval must never silently certify the expanded set of checks.
    review=conn.execute(select(store.reviews).where(store.reviews.c.run_id==run['id'])).mappings().first()
    if review:
        conn.execute(update(store.reviews).where(store.reviews.c.run_id==run['id']).values(status='in_review',reason='Добавлена проверка; требуется повторное рассмотрение.',revision=review['revision']+1,updated_at=store.now(),updated_by=job['owner_id']))
    audit.record(conn,job['owner_id'],'analysis.check_added',key,run_id=run['id'],report_revision=revision,rows=len(rows))

def membership_sql(row,run_dir):
    ref=row.get('MEMBERSHIP')
    if not ref:return None,[]
    if ref['kind']=='run':return 'SELECT APP_ID FROM check_members WHERE CHECK_ROW=?',[ref['row']]
    name=ref.get('file','')
    if Path(name).name!=name or not name.endswith('.parquet'):raise ValueError('Некорректная ссылка детализации')
    path=run_dir/'check-members'/name
    if not path.is_file():raise ValueError('Файл детализации проверки недоступен')
    return 'SELECT APP_ID FROM read_parquet(?) WHERE CHECK_ROW=?',[str(path),ref['row']]
