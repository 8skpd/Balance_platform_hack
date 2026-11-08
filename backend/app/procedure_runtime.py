"""Trusted invocation and validation of isolated Python stage results."""
import json,os,subprocess,sys,time
from pathlib import Path
from .pipeline_data import NORMAL_COLUMNS,COMPARE_COLUMNS,FACT_FIELDS,SEGMENT_FIELDS,quoted
from .sandbox import clean_env

def invoke(stage,spec,source,config,folder,progress):
    work=folder/('procedure-'+stage);work.mkdir(exist_ok=True)
    (work/'spec.json').write_text(json.dumps(spec,ensure_ascii=False))
    (work/'config.json').write_text(json.dumps(config,ensure_ascii=False))
    args=[sys.executable,'-I',str(Path(__file__).with_name('procedure_entry.py')),str(os.getpid()),str(work),str(source),stage]
    start=time.monotonic()
    with (work/'process.log').open('wb') as log:
        # Inherit the run's process group: cancelling the outer job kills this child too.
        child=subprocess.Popen(args,cwd=work,env=clean_env(),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,close_fds=True)
        try:
            while child.poll() is None:
                if (work/'process.log').stat().st_size>1024**2:raise ValueError('Вывод процедуры превышает 1 МиБ')
                if time.monotonic()-start>spec['timeout']:
                    raise TimeoutError('Превышено время процедуры '+spec['name'])
                progress(round(time.monotonic()-start));time.sleep(.5)
            if (work/'process.log').stat().st_size>1024**2:raise ValueError('Вывод процедуры превышает 1 МиБ')
            if child.returncode:
                with (work/'process.log').open('rb') as stream:
                    stream.seek(max(0,(work/'process.log').stat().st_size-5000));error=stream.read().decode(errors='replace')
                raise ValueError(spec['name']+': '+error)
        finally:
            if child.poll() is None:child.kill();child.wait()
    return work/'rows.jsonl',work/'segments.jsonl'

def load_comparison(conn,path):
    columns={k:('INTEGER' if k=='DIFF_FLAG' else 'VARCHAR') for k in COMPARE_COLUMNS}
    conn.execute('CREATE TABLE comparison AS SELECT * FROM read_json(?,format=\'newline_delimited\',columns='+repr(columns)+')',[str(path)])
    invalid=conn.execute("SELECT count(*) FROM comparison WHERE APP_ID IS NULL OR PATH IS NULL OR ARRAY_KEY IS NULL OR CHANGE_TYPE IS NULL OR CHANGE_TYPE NOT IN ('SAME','VALUE_CHANGED','ATTRIBUTE_NEW','ATTRIBUTE_LOST') OR DIFF_FLAG IS NULL OR DIFF_FLAG != CASE WHEN CHANGE_TYPE='SAME' THEN 0 ELSE 1 END").fetchone()[0]
    duplicate=conn.execute('SELECT count(*) FROM (SELECT APP_ID,PATH,ARRAY_KEY FROM comparison GROUP BY ALL HAVING count(*)>1)').fetchone()[0]
    mismatch=conn.execute('''SELECT count(*) FROM ((SELECT DISTINCT APP_ID,PATH,ARRAY_KEY FROM normalized EXCEPT SELECT APP_ID,PATH,ARRAY_KEY FROM comparison) UNION ALL (SELECT APP_ID,PATH,ARRAY_KEY FROM comparison EXCEPT SELECT DISTINCT APP_ID,PATH,ARRAY_KEY FROM normalized))''').fetchone()[0]
    if invalid or duplicate or mismatch:raise ValueError('Результат сравнения нарушает контракт: тип изменения, уникальность или состав атрибутов')

def load_facts(conn,path):
    columns={k:('INTEGER' if k=='DIFF_FLAG' else 'VARCHAR') for k in FACT_FIELDS};columns.update(CNT='BIGINT',APP_IDS='VARCHAR[]')
    conn.execute('CREATE TEMP TABLE imported_facts AS SELECT row_number() OVER ()::BIGINT FACT_ID,* FROM read_json(?,format=\'newline_delimited\',columns='+repr(columns)+')',[str(path)])
    invalid=conn.execute("SELECT count(*) FROM imported_facts WHERE APP_IDS IS NULL OR CNT IS NULL OR CNT<1 OR CNT!=len(list_distinct(APP_IDS)) OR len(APP_IDS)!=CNT OR PATH IS NULL OR ARRAY_KEY IS NULL OR SOURCE IS NULL OR SOURCE NOT IN ('BEFORE','AFTER') OR DIFF_FLAG IS NULL OR DIFF_FLAG NOT IN (0,1)").fetchone()[0]
    if invalid:raise ValueError('Факты должны содержать SOURCE, ключ атрибута, DIFF_FLAG, CNT и уникальные APP_IDS той же длины')
    conn.execute('CREATE TABLE fact_members AS SELECT FACT_ID,unnest(APP_IDS) APP_ID,PATH,ARRAY_KEY FROM imported_facts')
    invalid=conn.execute('''SELECT count(*) FROM fact_members m JOIN imported_facts f USING(FACT_ID)
      LEFT JOIN normalized n ON n.APP_ID=m.APP_ID AND n.PATH=m.PATH AND n.ARRAY_KEY=m.ARRAY_KEY AND n.SOURCE=f.SOURCE WHERE n.APP_ID IS NULL''').fetchone()[0]
    if invalid:raise ValueError('Факты ссылаются на неизвестную заявку или атрибут среза')
    inconsistent=conn.execute('SELECT count(*) FROM fact_members m JOIN imported_facts f USING(FACT_ID) JOIN comparison c ON c.APP_ID=m.APP_ID AND c.PATH=m.PATH AND c.ARRAY_KEY=m.ARRAY_KEY WHERE f.DIFF_FLAG IS DISTINCT FROM c.DIFF_FLAG OR f.CHANGE_TYPE IS DISTINCT FROM c.CHANGE_TYPE').fetchone()[0]
    if inconsistent:raise ValueError('Признак изменения в фактах противоречит результату сравнения')
    conn.execute('CREATE TABLE facts AS SELECT * EXCLUDE(APP_IDS) FROM imported_facts');conn.execute('DROP TABLE imported_facts')
