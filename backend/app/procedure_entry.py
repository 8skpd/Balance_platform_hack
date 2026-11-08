"""Isolated procedure bootstrap. Only pre-opened output streams can be written."""
import json,os,resource,sys,traceback,types
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from isolation import parent_death,restrict_filesystem,restrict_syscalls
from dataset_storage import iter_pairs

parent_death(int(sys.argv[1]))
work=Path(sys.argv[2]);source=Path(sys.argv[3]);stage=sys.argv[4]
spec=json.loads((work/'spec.json').read_text());config=json.loads((work/'config.json').read_text())
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
resource.setrlimit(resource.RLIMIT_CPU,(spec['timeout'],spec['timeout']+1))
resource.setrlimit(resource.RLIMIT_AS,(spec['memory_mb']*1024**2,spec['memory_mb']*1024**2))
resource.setrlimit(resource.RLIMIT_FSIZE,(2*1024**3,2*1024**3))
resource.setrlimit(resource.RLIMIT_NOFILE,(96,96))
output=(work/'rows.jsonl').open('w');segment_out=(work/'segments.jsonl').open('w')
try:
    # Trusted helper snapshots were produced by the API, never accepted from user code.
    defaults=types.ModuleType('defaults');exec(compile(spec['helpers']['normalizer'],'<defaults>','exec'),defaults.__dict__)
    relational=types.ModuleType('relational');exec(compile(spec['helpers']['pipeline_data'],'<relational>','exec'),relational.__dict__)
    allowed=['/usr','/lib','/lib64','/etc/ld.so.cache',sys.prefix,source,
       '/sys/devices/system/cpu/online','/proc/stat','/proc/sys/vm/overcommit_memory','/proc/self/cgroup','/sys/fs/cgroup']
    restrict_filesystem(allowed)
    import duckdb
    conn=None
    if stage!='normalization':
        conn=duckdb.connect(str(source),read_only=True,config={'threads':1,'memory_limit':'256MB','temp_directory':''})
        conn.execute('SET autoinstall_known_extensions=false');conn.execute('SET autoload_known_extensions=false')
    sys.path.remove(str(Path(__file__).resolve().parent));restrict_syscalls()
    namespace={'__name__':'__user_procedure__','defaults':defaults}
    exec(compile(spec['code'],'<procedure:'+stage+'>','exec'),namespace)
    proc=namespace.get('PROCEDURE')
    if not isinstance(proc,dict) or not callable(proc.get('entrypoint')):raise ValueError('Экспортируйте PROCEDURE с entrypoint')
    params={**proc.get('params',{}),**spec['params']}
    def write(stream,row):
        if not isinstance(row,dict):raise ValueError('Процедура должна возвращать объекты строк')
        stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
    if stage=='normalization':
        if not callable(proc.get('segments')):raise ValueError('Нормализация должна экспортировать PROCEDURE.segments')
        for app_id,raw_b,raw_a in iter_pairs(source):
            before,after=json.loads(raw_b),json.loads(raw_a)
            for side,doc in [('BEFORE',before),('AFTER',after)]:
                size=0;seen=set()
                for row in proc['entrypoint'](doc,app_id,side,config,params):
                    if set(relational.NORMAL_COLUMNS)-set(row):raise ValueError('Отсутствуют колонки нормализации')
                    if row['APP_ID']!=app_id or row['SOURCE']!=side:raise ValueError('Процедура подменила APP_ID или SOURCE')
                    if any(not isinstance(row[k],str) for k in relational.NORMAL_COLUMNS):raise ValueError('Колонки нормализации должны быть строками')
                    if row['VALUE_TYPE'] not in ('string','number','boolean','null'):raise ValueError('Некорректный VALUE_TYPE')
                    if not row['PATH']:raise ValueError('Пустой PATH')
                    key=(row['PATH'],row['ARRAY_KEY'])
                    if key in seen:raise ValueError('Повтор ключа атрибута '+str(key))
                    seen.add(key);size+=sum(len(row[k]) for k in relational.NORMAL_COLUMNS)
                    if size>32*1024**2:raise ValueError('Результат одной заявки превышает 32 МиБ')
                    write(output,{k:row[k] for k in relational.NORMAL_COLUMNS})
            seg=proc['segments'](app_id,before,after,config,params)
            if seg.get('APP_ID')!=app_id:raise ValueError('segments подменил APP_ID')
            if set(relational.SEGMENT_FIELDS+['DECISION_CHANGED'])-set(seg):raise ValueError('Недостаточно полей в segments')
            for key in relational.SEGMENT_FIELDS:
                if seg[key] is not None and not isinstance(seg[key],(str,int,float,bool)):raise ValueError('Неверный тип разреза '+key)
                if seg[key] is not None:seg[key]=str(seg[key])
            if type(seg['DECISION_CHANGED']) is not int or seg['DECISION_CHANGED'] not in (0,1):raise ValueError('DECISION_CHANGED должен быть 0 или 1')
            if seg['DECISION_CHANGED']!=int(seg['DECISION_BEFORE']!=seg['DECISION_AFTER']):raise ValueError('DECISION_CHANGED противоречит решениям')
            seg.setdefault('PRODUCT_CONTEXT','[]');write(segment_out,seg)
    else:
        for row in proc['entrypoint'](relational.StageData(conn),params):write(output,row)
except BaseException:
    traceback.print_exc();sys.exit(1)
finally:
    output.close();segment_out.close()
