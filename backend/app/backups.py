"""Consistent SQLite + artifact bundles, verified by isolated API restoration."""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select,update
from . import store,audit
from .auth import require_admin
from .maintenance import gate
from .queueing import transaction,TERMINAL
from .settings import DATA,ROOT

router=APIRouter(prefix='/api/admin/backups',dependencies=[Depends(require_admin)])

def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024**2),b''): digest.update(block)
    return digest.hexdigest()

def backup_dir():
    path=DATA/'backups';path.mkdir(exist_ok=True);return path

def archive_path(archive_id):
    try: uuid.UUID(archive_id)
    except (ValueError,TypeError): raise ValueError('Некорректный идентификатор копии')
    return backup_dir()/(archive_id+'.zip')

def create_bundle(archive_id=None):
    archive_id=archive_id or str(uuid.uuid4());destination=archive_path(archive_id)
    if destination.exists(): raise ValueError('Копия уже существует')
    if store.engine.dialect.name!='sqlite': raise ValueError('Этот способ резервирования поддерживает SQLite. Для PostgreSQL требуется согласованная копия БД через pg_dump и хранилища файлов.')
    with gate(exclusive=True):
        with store.engine.connect() as conn:
            if conn.execute(select(store.jobs.c.id).where(store.jobs.c.status.not_in(TERMINAL))).first(): raise ValueError('Завершите или отмените активные задачи перед резервированием')
        files=[]
        for folder in ('datasets','runs','jobs','uploads'):
            for path in sorted((DATA/folder).rglob('*')):
                if path.is_symlink(): raise ValueError('Символические ссылки не поддерживаются в резервной копии: '+str(path))
                if path.is_file(): files.append(path)
        if shutil.disk_usage(DATA).free<sum(p.stat().st_size for p in files)+512*1024**2: raise ValueError('Недостаточно места для резервной копии')
        with tempfile.TemporaryDirectory(prefix='balance-backup-',dir=backup_dir()) as temp:
            database=Path(temp)/'app.sqlite3'
            with sqlite3.connect(store.engine.url.database) as source,sqlite3.connect(database) as target: source.backup(target)
            entries=[(database,'app.sqlite3'),*((p,p.relative_to(DATA).as_posix()) for p in files)]
            manifest={'format':1,'version':'0.11.0','created_at':store.now(),'files':{name:{'size':p.stat().st_size,'sha256':sha(p)} for p,name in entries}}
            staging=Path(temp)/'bundle.zip'
            with zipfile.ZipFile(staging,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as archive:
                archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False))
                for path,name in entries: archive.write(path,name)
            staging.rename(destination)
    return {'archive_id':archive_id,'bytes':destination.stat().st_size,'files':len(entries),'sha256':sha(destination)}

PROBE = r'''
import json,sqlite3
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import store,api,auth
from app.settings import DATA
store.init()
with store.engine.connect() as c:
    rows=[dict(r) for r in c.execute(select(store.runs)).mappings()]
    datasets=[dict(r) for r in c.execute(select(store.dataset_imports)).mappings()]
    jobs={r['id']:dict(r) for r in c.execute(select(store.jobs)).mappings()}
checked=0
client=TestClient(api.app)
for d in datasets:
    if jobs.get(d['job_id'],{}).get('status')=='completed':
        assert (DATA/'datasets'/d['id']/'manifest.json').is_file(), 'Missing dataset '+d['id']
for run in rows:
    if run['status']!='completed': continue
    identity={'id':run['owner_id'],'role':'user','username':'restore-probe','display_name':'Restore probe'}
    api.app.dependency_overrides[auth.current_user]=lambda identity=identity:identity
    prefix='/api/runs/'+run['id']
    detail=client.get(prefix);assert detail.status_code==200,detail.text[:200]
    apps=client.get(prefix+'/applications');assert apps.status_code==200,apps.text[:200]
    assert apps.json()['total']==run['summary']['total_apps'],'Application count mismatch'
    from app.datasets import paths
    dataset=paths(run['dataset'],run.get('dataset_owner_id') or run['owner_id'])
    with api.database(run['id'],identity) as mart:
        ids=mart.execute('SELECT APP_ID FROM applications').fetchall()
    from app.dataset_storage import iter_ids
    available=set(iter_ids(dataset))
    for (app_id,) in ids:
        assert Path(app_id).name==app_id, 'Invalid application identifier'
        assert app_id in available, 'Missing source '+app_id
    if apps.json()['items']:
        from urllib.parse import quote
        first=apps.json()['items'][0]['APP_ID']
        raw=client.get(prefix+'/applications/'+quote(first,safe='')+'/source');assert raw.status_code==200,raw.text[:200]
        attributes=client.get(prefix+'/applications/'+quote(first,safe=''));assert attributes.status_code==200,attributes.text[:200]
    exported=client.get(prefix+'/report.html');assert exported.status_code==200,exported.text[:200]
    checked+=1
api.app.dependency_overrides.clear()
print(json.dumps({'reports_opened':checked,'runs':len(rows),'datasets':len(datasets),'api_probe':True}))
'''

def restore_bundle(archive,destination,probe=True):
    destination=Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()): raise ValueError('Восстановление разрешено только в новую или пустую директорию')
    destination.mkdir(parents=True,exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as z:
            infos=z.infolist();names=[i.filename for i in infos]
            if len(names)!=len(set(names)) or 'manifest.json' not in names: raise ValueError('Повторяющиеся файлы или нет манифеста')
            if z.getinfo('manifest.json').file_size>32*1024**2: raise ValueError('Слишком большой манифест')
            manifest=json.loads(z.read('manifest.json'))
            if manifest.get('format')!=1 or set(names)!=set(manifest['files'])|{'manifest.json'}: raise ValueError('Состав копии не соответствует манифесту')
            if 'app.sqlite3' not in manifest['files']: raise ValueError('Нет базы данных')
            total=sum(i.file_size for i in infos)
            if shutil.disk_usage(destination).free<total+128*1024**2: raise ValueError('Недостаточно места для восстановления')
            for name,expected in manifest['files'].items():
                path=PurePosixPath(name)
                if path.is_absolute() or '..' in path.parts or '\\' in name or (name!='app.sqlite3' and (len(path.parts)<2 or path.parts[0] not in ('datasets','runs','jobs','uploads'))): raise ValueError('Недопустимый путь в копии')
                info=z.getinfo(name)
                if info.file_size!=expected['size'] or (info.external_attr>>16)&0o170000==0o120000: raise ValueError('Некорректный размер или ссылка в копии')
                target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
                digest=hashlib.sha256()
                with z.open(name) as source,target.open('xb') as output:
                    for block in iter(lambda:source.read(1024**2),b''): output.write(block);digest.update(block)
                if digest.hexdigest()!=expected['sha256']: raise ValueError('Контрольная сумма не совпадает: '+name)
        with sqlite3.connect(destination/'app.sqlite3') as conn:
            if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Повреждение базы данных')
            conn.execute('DELETE FROM sessions')
            conn.execute("UPDATE backup_operations SET status='failed',error='Операция прервана восстановлением' WHERE status='running'")
        result={'files_verified':len(manifest['files']),'integrity_check':'ok','sessions_revoked':True,'destination':str(destination)}
        if probe:
            env={**os.environ,'BALANCE_DATA_DIR':str(destination),'DATABASE_URL':f'sqlite:///{destination / "app.sqlite3"}', 'PYTHONPATH':str(ROOT/'backend')}
            completed=subprocess.run([sys.executable,'-c',PROBE],cwd=ROOT,env=env,capture_output=True,text=True,timeout=300)
            if completed.returncode: raise ValueError('Восстановленные отчёты не прошли проверку API: '+completed.stderr[-2000:])
            result.update(json.loads(completed.stdout.strip().splitlines()[-1]))
        return result
    except Exception:
        # This directory was verified empty before any files were written.
        shutil.rmtree(destination)
        raise

def verify_bundle(archive):
    with tempfile.TemporaryDirectory(prefix='balance-restore-',dir=backup_dir()) as temp:
        result=restore_bundle(archive,Path(temp)/'restored')
        result.pop('destination',None)
        return result

def operation(operation_id,kind,archive_id,actor):
    try:
        result=create_bundle(archive_id) if kind=='create' else verify_bundle(archive_path(archive_id))
        status,error='completed',None
    except Exception as exc:
        result=None;status='failed';error='Система занята запросом или задачей. Повторите позже.' if isinstance(exc,BlockingIOError) else str(exc)
    with transaction() as conn:
        conn.execute(update(store.backup_operations).where(store.backup_operations.c.id==operation_id).values(status=status,result=result,error=error,finished_at=store.now()))
        audit.record(conn,actor,'backup.'+kind,archive_id,status=status,result=result,error=error)

def start(kind,archive_id,background,user):
    op=str(uuid.uuid4())
    with transaction() as conn:
        if conn.execute(select(store.backup_operations.c.id).where(store.backup_operations.c.status=='running')).first(): raise HTTPException(409,'Дождитесь текущей операции резервирования')
        conn.execute(store.backup_operations.insert().values(id=op,kind=kind,status='running',archive_id=archive_id,created_at=store.now(),actor_id=user['id']))
    background.add_task(operation,op,kind,archive_id,user['id'])
    return {'id':op,'status':'running'}

@router.get('')
def listing():
    with store.engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(store.backup_operations).order_by(store.backup_operations.c.created_at.desc()).limit(50)).mappings()]

@router.post('',status_code=202)
def create(background:BackgroundTasks,user=Depends(require_admin)):
    return start('create',str(uuid.uuid4()),background,user)

@router.post('/{archive_id}/verify',status_code=202)
def verify(archive_id:str,background:BackgroundTasks,user=Depends(require_admin)):
    try: path=archive_path(archive_id)
    except ValueError as exc: raise HTTPException(422,str(exc))
    if not path.exists(): raise HTTPException(404,'Копия не найдена')
    return start('verify',archive_id,background,user)

@router.get('/{archive_id}/download')
def download(archive_id:str):
    try: path=archive_path(archive_id)
    except ValueError as exc: raise HTTPException(422,str(exc))
    if not path.exists(): raise HTTPException(404,'Копия не найдена')
    return FileResponse(path,filename='balance-'+archive_id+'.zip',media_type='application/zip')
