import importlib.util
import json
import os
import sys
import time
import traceback
from pathlib import Path

import duckdb

from . import store
from .datasets import paths
from .mart import Mart, records
from .normalizer import normalize, segments
from .settings import RUNS, NORMALIZATION_WORKERS

def log(run_id, stage, progress, message, check_key=None):
    store.patch_run(run_id, stage=stage, progress=progress)
    with (RUNS / run_id / 'events.jsonl').open('a') as f:
        f.write(json.dumps({'time': store.now(), 'stage': stage, 'message': message, **({'check_key':check_key} if check_key else {})}, ensure_ascii=False) + '\n')

def stage_input(conn,folder,stage,tables):
    target=folder/('input-'+stage+'.duckdb')
    conn.execute("ATTACH '"+str(target).replace("'","''")+"' AS procedure_input")
    try:
        for table in tables:conn.execute('CREATE TABLE procedure_input.'+table+' AS SELECT * FROM '+table)
    finally:conn.execute('DETACH procedure_input')
    return target

def build_database(conn, normalized_file, segments_file, plan=None, config=None, folder=None, progress=None):
    from .pipeline_data import NORMAL_COLUMNS,SEGMENT_FIELDS,COMPARISON_SQL,build_facts
    from .procedure_runtime import invoke,load_comparison,load_facts
    progress=progress or (lambda stage,seconds:None)
    plan=plan or {}
    columns={k:'VARCHAR' for k in NORMAL_COLUMNS}
    conn.execute("CREATE TABLE normalized AS SELECT * FROM read_json(?, format='newline_delimited', columns="+repr(columns)+")",[normalized_file if isinstance(normalized_file,list) else str(normalized_file)])
    segment_columns={k:"VARCHAR" for k in ["APP_ID","PRODUCT_CONTEXT",*SEGMENT_FIELDS]};segment_columns["DECISION_CHANGED"]="INTEGER"
    conn.execute("CREATE TABLE segments AS SELECT * FROM read_json(?,format='newline_delimited',columns="+repr(segment_columns)+")",[segments_file if isinstance(segments_file,list) else str(segments_file)])
    for stage in ('comparison','aggregation'):
        spec=plan.get(stage)
        progress(stage,0)
        if spec and spec['kind']=='python':
            tables=['normalized','segments']+(['comparison','applications'] if stage=='aggregation' else [])
            source=stage_input(conn,folder,stage,tables)
            result,_=invoke(stage,spec,source,config,folder,lambda seconds:progress(stage,seconds))
            (load_comparison if stage=='comparison' else load_facts)(conn,result)
            source.unlink();result.unlink()
        else:
            if spec:
                namespace={};exec(compile(spec['helpers']['pipeline_data'],'<pipeline-snapshot>','exec'),namespace)
            else:namespace={'COMPARISON_SQL':COMPARISON_SQL,'build_facts':build_facts}
            if stage=='comparison':conn.execute('CREATE TABLE comparison AS '+namespace['COMPARISON_SQL'])
            else:namespace['build_facts'](conn)
        if stage=='comparison':
            conn.execute('CREATE INDEX comparison_app ON comparison(APP_ID)')
            if conn.execute('SELECT count(*) FROM segments').fetchone()[0]<=100000:
                conn.execute('CREATE INDEX comparison_path ON comparison(PATH)')
                conn.execute('CREATE INDEX comparison_change ON comparison(CHANGE_TYPE)')
            conn.execute("""CREATE TABLE applications AS
                SELECT s.* EXCLUDE (PRODUCT_CONTEXT), coalesce(c.changed_attributes,0)::INTEGER changed_attributes,
                  coalesce(c.compared_attributes,0)::INTEGER compared_attributes FROM segments s LEFT JOIN
                (SELECT APP_ID,count(*) compared_attributes,sum(DIFF_FLAG)::INTEGER changed_attributes FROM comparison GROUP BY APP_ID) c USING(APP_ID)""")


def execute(run_id, work_directory):
    store.init()
    run = store.get_run(run_id)
    directory = work_directory
    directory.mkdir(exist_ok=True)
    start = time.monotonic()
    conn = None
    try:
        dataset = paths(run['dataset'], run.get('dataset_owner_id') or run['owner_id'])
        log(run_id, 'validation', 2, 'Проверка пар BEFORE / AFTER')
        from .dataset_storage import inventory
        from .parallel_normalization import run as normalize_parallel
        counts=inventory(dataset)
        if not counts['pairs'] or counts['unpaired']:
            raise ValueError(f"Пустой набор или непарные заявки: BEFORE={counts['before']}, AFTER={counts['after']}, без пары={counts['unpaired']}")
        total=counts['pairs']
        log(run_id,'normalization',5,f'Нормализация {total} пар ответов · процессов: {NORMALIZATION_WORKERS}')
        last_update=time.monotonic()
        def normalized(done):
            nonlocal last_update
            now=time.monotonic()
            if now-last_update>=2 or done==total:
                log(run_id,'normalization',5+int(50*done/total),f'Приведено {done} из {total} заявок');last_update=now
        procedure_plan=run.get('procedure_plan') or {}
        norm_spec=procedure_plan.get('normalization')
        if norm_spec and (norm_spec['kind']=='python' or norm_spec['helpers']['normalizer']!=Path(__file__).with_name('normalizer.py').read_text()):
            from .procedure_runtime import invoke
            stamp=time.monotonic();last_stage=0
            def stage_progress(seconds):
                nonlocal last_stage
                if seconds-last_stage>=3:
                    log(run_id,'normalization',30,f'Выполняется {norm_spec["name"]} · {seconds} сек.');last_stage=seconds
            nf,sf=invoke('normalization',norm_spec,dataset,run['config'],directory,stage_progress)
            normalized_file,segments_file=[str(nf)],[str(sf)]
            processing={'workers':1,'processes':1,'pairs':total,'seconds':round(time.monotonic()-stamp,3)}
        else:
            normalized_file,segments_file,processing=normalize_parallel(dataset,run['config'],directory,total,NORMALIZATION_WORKERS,normalized)
        log(run_id, 'comparison', 60, 'Сопоставление атрибутов и подготовка фактов')
        conn = duckdb.connect(str(directory / 'building.duckdb'))
        conn.execute("SET memory_limit='1GB'")
        conn.execute(f'SET threads={NORMALIZATION_WORKERS}')
        conn.execute('SET preserve_insertion_order=false')
        conn.execute("SET max_temp_directory_size='3GB'")
        stage_clock={}
        def phase_progress(stage,seconds):
            if stage not in stage_clock or seconds-stage_clock[stage]>=3:
                title={'comparison':'Сопоставление атрибутов','aggregation':'Подготовка фактов и связей с заявками'}[stage]
                log(run_id,stage,60 if stage=='comparison' else 75,title+(f' · {seconds} сек.' if seconds else ''))
                stage_clock[stage]=seconds
        build_database(conn, normalized_file, segments_file,procedure_plan,run['config'],directory,phase_progress)
        from .library import builtin_catalog
        plan = run.get('check_plan')
        if plan is None:  # Backward compatibility for queued v0.3 runs.
            plan = [{**c, 'params':{**c['params'], **run['config']['check_params']}} for c in builtin_catalog()]
        builtins = [spec for spec in plan if spec['kind'] == 'builtin']
        log(run_id, 'checks', 85, f'Штатных проверок выбрано: {len(builtins)}')
        from .mart import StreamingMart
        mart, checks, executions = StreamingMart(conn), [], []
        conn.execute('CREATE TABLE check_members (CHECK_ROW INTEGER,APP_ID VARCHAR)')
        for spec in builtins:
            # Only API-generated bundled snapshots reach this branch. User library code
            # is always executed later by the isolated supervisor, never by this worker.
            namespace = {'__name__':'__bundled_check__'}
            exec(compile(spec['code'], f"<bundled:{spec['key']}>", 'exec'), namespace)
            result = namespace['CHECK']['entrypoint'](mart, {**spec['params'],'app_ids_limit':total})
            for offset,row in enumerate(result):
                ids=sorted(set(row['APP_IDS']))
                for begin in range(0,len(ids),10000):
                    conn.execute('INSERT INTO check_members SELECT ?,unnest(?::VARCHAR[])',[len(checks)+offset,ids[begin:begin+10000]])
                if spec['key']=='stop_factor_disappeared' and ids:
                    changes=records(conn.execute("SELECT CHANGE_TYPE,ARRAY_KEY FROM comparison WHERE APP_ID=? AND PATH='decisionResult.product_trials.rules.hit' AND DIFF_FLAG=1",[ids[0]]))
                    kinds={r['CHANGE_TYPE'] for r in changes if r['ARRAY_KEY'].split('|')[-1]==row['ARGUMENT']}
                    row['EXPLANATION']='Правило удалено из стратегии' if kinds=={'ATTRIBUTE_LOST'} else 'Правило перестало срабатывать' if kinds=={'VALUE_CHANGED'} else 'Изменилось правило; подробности в атрибутах заявки'
                row['MEMBERSHIP']={'kind':'run','row':len(checks)+offset,'count':len(ids)}
                row['APP_IDS']=ids[:spec['params'].get('app_ids_limit',100)]
                row.update(CHECK_ID=spec['key'], CHECK_NAME=spec['name'], CHECK_VERSION=spec['version'], CHECK_KIND='builtin')
            checks.extend(result)
            executions.append({'key':spec['key'], 'name':spec['name'], 'version':spec['version'], 'kind':'builtin',
                               'status':'completed', 'rows':len(result)})
            log(run_id, 'checks', 90, f"{spec['name']}: {len(result)} строк",check_key=spec['key'])
        summary = records(conn.execute('''SELECT count(*)::INTEGER total_apps,
            count(*) FILTER(WHERE changed_attributes>0)::INTEGER changed_apps,
            count(*) FILTER(WHERE compared_attributes=0)::INTEGER empty_apps,
            sum(DECISION_CHANGED)::INTEGER decision_changed,
            count(*) FILTER(WHERE changed_attributes>0 AND DECISION_CHANGED=0)::INTEGER changed_same_decision
            FROM applications'''))[0]
        summary['attributes'] = mart.count()
        summary['changed_attributes'] = mart.count(diff_flag=1)
        summary['normalized_rows'] = conn.execute('SELECT count(*) FROM normalized').fetchone()[0]
        summary['change_types'] = records(conn.execute('SELECT CHANGE_TYPE, count(*) CNT, count(DISTINCT APP_ID) APPS FROM comparison GROUP BY CHANGE_TYPE ORDER BY CHANGE_TYPE'))
        summary['transitions'] = records(conn.execute('SELECT DECISION_BEFORE, DECISION_AFTER, count(*) CNT FROM applications GROUP BY ALL ORDER BY ALL'))
        summary['top_paths'] = records(conn.execute('SELECT PATH, count(DISTINCT APP_ID) CNT FROM comparison WHERE DIFF_FLAG=1 GROUP BY PATH ORDER BY CNT DESC, PATH LIMIT 8'))
        summary['approval'] = records(conn.execute("SELECT count(*) FILTER(WHERE DECISION_BEFORE='APPROVE') before_count,count(*) FILTER(WHERE DECISION_AFTER='APPROVE') after_count FROM applications"))[0]
        summary['difference_examples']=records(conn.execute('SELECT APP_ID,PATH,ARRAY_KEY,VALUE_BEFORE,VALUE_AFTER,CHANGE_TYPE FROM comparison WHERE DIFF_FLAG=1 QUALIFY row_number() OVER (PARTITION BY PATH ORDER BY APP_ID,ARRAY_KEY)=1 ORDER BY PATH LIMIT 12'))
        summary['checks_failed'] = sum(row['RESULT'] == 'FAIL' for row in checks)
        summary['checks_passed'] = sum(row['RESULT'] == 'PASS' for row in checks)
        conn.close()
        conn = None
        (directory / 'building.duckdb').replace(directory / 'result.duckdb')
        for path in normalized_file+segments_file:Path(path).unlink()
        summary['processing']=processing
        log(run_id, 'checks', 92, 'Витрина подготовлена для пользовательских проверок.')
        return {'summary': summary, 'checks': checks, 'check_executions':executions}
    except Exception as exc:
        if conn:
            conn.close()
        log(run_id, 'error', 0, str(exc))
        (directory / 'error.txt').write_text(traceback.format_exc())
        raise
