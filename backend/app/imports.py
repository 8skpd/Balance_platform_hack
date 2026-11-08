"""Streamed uploads and bounded validation. Archive paths never choose destination paths."""
import hashlib
import json
import math
import re
import shutil
import stat
import struct
import threading
import time
import uuid
import zipfile
from pathlib import PurePosixPath

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from . import store, queueing
from .auth import current_user, require_writer
from .settings import UPLOADS, DATASETS, UPLOAD_MAX_BYTES, IMPORT_MAX_BYTES, IMPORT_FILE_BYTES, IMPORT_MAX_FILES, IMPORT_MAX_PAIRS, IMPORT_CSV_BYTES, IMPORT_XML_BYTES

router=APIRouter(prefix='/api/dataset-imports',dependencies=[Depends(require_writer)])
upload_slots=threading.BoundedSemaphore(2)
APP_ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z')

def upload_path(upload_id):
    return UPLOADS/(str(uuid.UUID(upload_id))+'.zip')

def record(dataset_id,owner_id=None):
    query=select(store.dataset_imports).where(store.dataset_imports.c.id==dataset_id)
    if owner_id is not None:query=query.where(store.dataset_imports.c.owner_id==owner_id)
    with store.engine.connect() as conn:
        r=conn.execute(query).mappings().first()
    return dict(r) if r else None

def enqueue(values):
    dataset_id='upload-'+str(uuid.uuid4())
    values={**values,'id':dataset_id,'created_at':store.now(),'stats':None}
    try:
        return queueing.submit(values['owner_id'],'','import',dataset_id=dataset_id,dataset_values=values,timeout=1800,memory_mb=2048)
    except ValueError as exc:
        raise HTTPException(429,str(exc))

def public_import(value):
    job=store.get_job(value['job_id'])
    return {k:v for k,v in {**value,**{key:job[key] for key in ('status','progress','stage','error','duration','attempts')}}.items()
            if k not in ('owner_id','upload_id')}

@router.get('/limits')
def limits():
    return {'max_upload_bytes':UPLOAD_MAX_BYTES,'max_unpacked_bytes':IMPORT_MAX_BYTES,
            'max_file_bytes':IMPORT_FILE_BYTES,'max_files':IMPORT_MAX_FILES,'max_pairs':IMPORT_MAX_PAIRS,'max_active':3,'formats':['json','csv','xml'],'max_csv_bytes':IMPORT_CSV_BYTES,'max_xml_bytes':IMPORT_XML_BYTES}

@router.get('')
def list_imports(user:dict=Depends(current_user)):
    with store.engine.connect() as conn:
        rows=conn.execute(select(store.dataset_imports).where(store.dataset_imports.c.owner_id==user['id'])
                         .order_by(store.dataset_imports.c.created_at.desc())).mappings().all()
    return [public_import(dict(r)) for r in rows]

@router.get('/{dataset_id}')
def detail(dataset_id:str,user:dict=Depends(current_user)):
    value=record(dataset_id,user['id'])
    if not value:raise HTTPException(404,'Импорт не найден')
    return public_import(value)

@router.post('',status_code=202)
async def upload(request:Request,name:str=Query(min_length=1,max_length=120),filename:str=Query(min_length=1,max_length=255),user:dict=Depends(current_user)):
    name=name.strip()
    if not name or any(ord(c)<32 for c in name+filename) or not filename.lower().endswith('.zip'):
        raise HTTPException(422,'Укажите название и файл с расширением .zip')
    declared=request.headers.get('content-length')
    if declared and (not declared.isascii() or not declared.isdigit() or len(declared)>12 or int(declared)>UPLOAD_MAX_BYTES):
        raise HTTPException(413,f'ZIP превышает лимит {UPLOAD_MAX_BYTES//1024**2} МБ')
    from .operations import admission
    with store.engine.connect() as conn: admission(conn,user['id'],extra_bytes=int(declared) if declared else 0)
    if not upload_slots.acquire(blocking=False):raise HTTPException(429,'Сейчас принимаются две загрузки. Повторите позже.')
    upload_id=str(uuid.uuid4());path=upload_path(upload_id);part=path.with_suffix('.part')
    accepted=False;size=0;digest=hashlib.sha256()
    try:
        try:
            with anyio.fail_after(180):
                async with await anyio.open_file(part,'xb') as output:
                    async for chunk in request.stream():
                        size+=len(chunk)
                        if size>UPLOAD_MAX_BYTES:raise HTTPException(413,f'ZIP превышает лимит {UPLOAD_MAX_BYTES//1024**2} МБ')
                        digest.update(chunk);await output.write(chunk)
        except TimeoutError:
            raise HTTPException(408,'Передача ZIP не завершилась за 180 секунд. Повторите загрузку.')
        if not size:raise HTTPException(422,'Пустой файл')
        part.replace(path)
        job=await anyio.to_thread.run_sync(lambda:enqueue({'owner_id':user['id'],'upload_id':upload_id,'name':name,
                         'filename':PurePosixPath(filename.replace('\\','/')).name,'size':size,'sha256':digest.hexdigest()}))
        accepted=True
        return {'id':job['dataset_id'],'job_id':job['id'],'status':'queued'}
    finally:
        try:
            if not accepted:
                part.unlink(missing_ok=True)
                # A disconnect can arrive after the DB commit but before the response.
                with store.engine.connect() as conn:
                    committed=conn.execute(select(store.dataset_imports.c.id).where(store.dataset_imports.c.upload_id==upload_id)).first()
                if not committed:path.unlink(missing_ok=True)
        finally:
            upload_slots.release()

def retry(job,user):
    old=record(job['dataset_id'],user['id'])
    if not old or not upload_path(old['upload_id']).is_file():raise HTTPException(409,'Исходный архив недоступен. Загрузите его заново.')
    return enqueue({k:old[k] for k in ('owner_id','upload_id','name','filename','size','sha256')})

def strict_json(raw,location):
    def pairs(values):
        out={}
        for key,value in values:
            if key in out:raise ValueError(f'Повтор ключа JSON: {key[:100]}')
            out[key]=value
        return out
    def constant(value):raise ValueError(f'Недопустимое число {value}')
    try:
        doc=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=pairs,parse_constant=constant)
        if not isinstance(doc,dict):raise ValueError('Корень JSON должен быть объектом')
        stack=[(doc,'',0)];nodes=0;arrays=set()
        while stack:
            value,path,depth=stack.pop();nodes+=1
            if nodes>200000 or depth>64 or len(path)>2048:raise ValueError('Превышена сложность JSON (200000 узлов / 64 уровня / 2048 символов пути)')
            if isinstance(value,float) and not math.isfinite(value):raise ValueError('Неконечное число JSON')
            if isinstance(value,dict):
                for key,item in value.items():
                    if len(key)>256:raise ValueError('Ключ JSON длиннее 256 символов')
                    stack.append((item,f'{path}.{key}' if path else key,depth+1))
            elif isinstance(value,list):
                if len(arrays)<200:arrays.add(path)
                stack.extend((item,path,depth+1) for item in value)
        return doc,arrays
    except (UnicodeError,ValueError,RecursionError) as exc:
        raise ValueError(f'{location}: {exc}') from exc

def safe_entries(archive):
    entries=archive.infolist()
    if len(entries)>IMPORT_MAX_FILES+100:raise ValueError(f'В ZIP слишком много записей (максимум {IMPORT_MAX_FILES+100})')
    seen=set();size=0
    for item in entries:
        name=item.filename
        # Unicode Path metadata legitimately replaces the legacy filename.
        # Check both names and the untruncated Unicode value (zipfile strips NUL).
        paths=[item.orig_filename,name];extra=item.extra
        while len(extra)>=4:
            field,length=struct.unpack('<HH',extra[:4]);payload=extra[4:4+length];extra=extra[4+length:]
            if field==0x7075 and len(payload)>=5 and payload[0]==1:
                paths.append(payload[5:].decode('utf-8'))
        for path in paths:
            parts=path.rstrip('/').split('/')
            if not path or path.startswith('/') or '\\' in path or any(p in ('','.', '..') or ':' in p or any(ord(c)<32 for c in p) for p in parts):
                raise ValueError('Небезопасный путь в ZIP: '+repr(path[:200]))
        folded=name.rstrip('/').casefold()
        if folded in seen:raise ValueError('Повтор пути в ZIP: '+name[:200])
        seen.add(folded)
        mode=stat.S_IFMT(item.external_attr>>16)
        if mode not in (0,stat.S_IFREG,stat.S_IFDIR) or (mode==stat.S_IFDIR and not item.is_dir()):
            raise ValueError('Ссылки и специальные файлы в ZIP запрещены: '+name[:200])
        if item.flag_bits&1:raise ValueError('ZIP с паролем не поддерживается')
        if item.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):raise ValueError('Поддерживается ZIP с методом Store или Deflate')
        size+=item.file_size
        if size>IMPORT_MAX_BYTES:raise ValueError('Распакованный ZIP превышает 1 ГБ')
        file_limit=IMPORT_CSV_BYTES if name.lower().endswith('.csv') else IMPORT_XML_BYTES if name.lower().endswith('.xml') else IMPORT_FILE_BYTES
        if item.file_size>file_limit:raise ValueError(f'Файл в ZIP превышает {file_limit//1024**2} МБ: '+name[:200])
        if item.file_size>max(1,item.compress_size)*500:raise ValueError('Слишком высокая степень сжатия ZIP: '+name[:200])
    return entries

def progress(work,value,stage):
    part=work/'progress.tmp';part.write_text(json.dumps({'progress':value,'stage':stage}));part.replace(work/'progress.json')

def execute(job_id,work):
    job=store.get_job(job_id);info=record(job['dataset_id'])
    source=upload_path(info['upload_id']);progress(work,2,'Проверка архива')
    digest=hashlib.sha256()
    with source.open('rb') as f:
        for chunk in iter(lambda:f.read(1024**2),b''):digest.update(chunk)
    if digest.hexdigest()!=info['sha256']:raise ValueError('Контрольная сумма исходного ZIP не совпадает')
    target=work/'dataset';target.mkdir()
    try:
        from .dataset_storage import DatasetWriter
        with zipfile.ZipFile(source) as archive, DatasetWriter(target) as writer:
            from .import_formats import csv_documents, xml_documents
            entries=safe_entries(archive);candidates=[];roots=set();ignored=0
            side_names={'before':'BEFORE_MASTER','master':'BEFORE_MASTER','before_master':'BEFORE_MASTER',
                        'after':'AFTER_BETA','beta':'AFTER_BETA','after_beta':'AFTER_BETA'}
            for item in entries:
                if item.is_dir():continue
                parts=item.filename.split('/')
                if '__MACOSX' in parts or parts[-1]=='.DS_Store' or parts[-1].startswith('._'):
                    ignored+=1;continue
                suffix=PurePosixPath(parts[-1]).suffix.lower()
                stem=PurePosixPath(parts[-1]).stem
                if 'RESULTS' in parts:
                    if len(parts)<3 or parts[-3]!='RESULTS' or parts[-2] not in ('BEFORE_MASTER','AFTER_BETA') or suffix not in ('.json','.csv','.xml'):
                        raise ValueError('Ожидается RESULTS/BEFORE_MASTER/ или RESULTS/AFTER_BETA/ с файлами JSON, CSV или XML: '+item.filename[:200])
                    side=parts[-2];root='/'.join(parts[:-3])
                elif stem.lower() in side_names and suffix in ('.csv','.xml'):
                    side=side_names[stem.lower()];root='/'.join(parts[:-1])
                elif suffix in ('.csv','.xml') and stem.lower() in ('dataset','comparison','data'):
                    side=None;root='/'.join(parts[:-1])
                else:
                    ignored+=1;continue
                if suffix=='.json' and not APP_ID.fullmatch(stem):
                    raise ValueError('Недопустимый ID в имени файла: '+parts[-1][:150])
                roots.add(root);candidates.append((item,side,stem,suffix))
            if not candidates:
                nested=[item.filename for item in entries if not item.is_dir() and item.filename.lower().endswith('.zip')]
                assignment=next((name for name in nested if PurePosixPath(name).name.casefold()=='задание.zip'),None)
                if assignment:
                    raise ValueError('Загружен архив с материалами, а не датасет. Распакуйте его и загрузите вложенный '+assignment+'.')
                if nested:
                    raise ValueError('В архиве найдены вложенные ZIP, но нет данных BEFORE / AFTER. Распакуйте пакет и загрузите архив с датасетом.')
            if len(roots)!=1:raise ValueError('ZIP должен содержать ровно один набор данных RESULTS или пару BEFORE / AFTER')
            if not candidates or len(candidates)>IMPORT_MAX_FILES:
                raise ValueError(f'Набор пуст или содержит более {IMPORT_MAX_FILES} файлов')
            root=next(iter(roots));manifest_name=(root+'/' if root else '')+'manifest.json';manifest={}
            if manifest_name in archive.namelist():
                manifest_item=archive.getinfo(manifest_name)
                if manifest_item.file_size>65536:raise ValueError('manifest.json превышает 64 КБ')
                manifest,_=strict_json(archive.read(manifest_item),manifest_name)
                ignored-=1
            arrays=set();missing_ids=0;unpacked=0;written=0;documents=0
            total_source_bytes=sum(c[0].file_size for c in candidates)
            identifiers={'BEFORE_MASTER':set(),'AFTER_BETA':set()}
            formats=set()
            for index,entry in enumerate(candidates):
                item,side,stem,suffix=entry
                candidates[index]=None  # release metadata as large archives are consumed
                raw=b''
                if suffix=='.json':
                    with archive.open(item) as f:raw=f.read(IMPORT_FILE_BYTES+1)
                unpacked+=item.file_size if suffix in ('.csv','.xml') else len(raw)
                if len(raw)>IMPORT_FILE_BYTES or unpacked>IMPORT_MAX_BYTES:
                    raise ValueError('Превышен лимит распакованных данных')
                formats.add(suffix[1:]);source_stream=None
                if suffix=='.csv':
                    source_stream=archive.open(item)
                    records=csv_documents(source_stream,item.filename,combined=side is None)
                elif suffix=='.xml':
                    source_stream=archive.open(item)
                    records=xml_documents(source_stream,item.filename,stem,combined=side is None)
                else:
                    records=[(strict_json(raw,item.filename)[0],stem,item.filename)]
                for entry in records:
                    doc,fallback_id,location=entry[:3]
                    target_side=side or entry[3]
                    app_data=doc.get('appData',{})
                    if not isinstance(app_data,dict):raise ValueError(location+': appData должен быть объектом')
                    actual=app_data.get('application_id')
                    if actual is not None and isinstance(actual,(bool,dict,list)):
                        raise ValueError(location+': некорректный application_id')
                    if suffix=='.json' and actual is not None and str(actual)!=fallback_id:
                        raise ValueError(location+': application_id не совпадает с именем файла')
                    app_id=str(actual) if actual is not None else fallback_id
                    if not app_id or not APP_ID.fullmatch(app_id):
                        raise ValueError(location+': укажите корректный application_id (буквы, цифры, _, - и точка)')
                    if app_id in identifiers[target_side]:raise ValueError(location+': повтор application_id '+app_id)
                    identifiers[target_side].add(app_id)
                    if len(identifiers[target_side])>IMPORT_MAX_PAIRS:
                        raise ValueError(f'Больше {IMPORT_MAX_PAIRS} заявок в одной версии')
                    if actual is None:
                        if manifest.get('require_application_ids') is True:
                            raise ValueError(location+': укажите application_id для сопоставления файлов')
                        missing_ids+=1
                    if 'decisionResult' in doc and not isinstance(doc['decisionResult'],dict):
                        raise ValueError(location+': decisionResult должен быть объектом')
                    encoded=json.dumps(doc,ensure_ascii=False,allow_nan=False).encode('utf-8')
                    written+=len(encoded)
                    if len(encoded)>IMPORT_FILE_BYTES or written>IMPORT_MAX_BYTES:
                        raise ValueError('Превышен лимит преобразованных данных')
                    _,found=strict_json(encoded,location)
                    arrays.update(sorted(found-arrays)[:max(0,200-len(arrays))])
                    writer.write(target_side,app_id,encoded);documents+=1
                    if documents%1000==0:
                        consumed=unpacked-item.file_size+(source_stream.tell() if source_stream else len(raw))
                        progress(work,5+min(90,int(90*consumed/max(1,total_source_bytes))),f'Проверено ответов: {documents}')
            before=identifiers['BEFORE_MASTER'];after=identifiers['AFTER_BETA']
            if not before or before!=after:
                raise ValueError(f'Непарные заявки: BEFORE={len(before)}, AFTER={len(after)}; без AFTER: {", ".join(sorted(before-after)[:10]) or "—"}; без BEFORE: {", ".join(sorted(after-before)[:10]) or "—"}')
            storage_format=writer.finish()
            stats={'storage_format':storage_format,'before':len(before),'after':len(after),'pairs':len(before),'unpaired':0,'json_files':documents,
                   'unpacked_bytes':unpacked,'ignored_files':ignored,'missing_application_ids':missing_ids,
                   'array_paths':sorted(arrays),'array_paths_limit':200,'source_formats':sorted(formats)}
            (target/'manifest.json').write_text(json.dumps({'run_id':info['id'],'name':info['name'],'import_job_id':job_id,
                'upload_sha256':info['sha256'],'source_manifest':manifest,'source_formats':sorted(formats),'storage_format':storage_format},ensure_ascii=False,allow_nan=False))
            progress(work,98,'Проверка завершена. Публикация датасета')
            return stats
    except zipfile.BadZipFile as exc:
        raise ValueError('Повреждённый или неподдерживаемый ZIP: '+str(exc)) from exc

def publish(job,artifact,result):
    target=DATASETS/job['dataset_id']
    if target.exists():
        # A previous attempt may have renamed its complete directory before DB commit.
        manifest=json.loads((target/'manifest.json').read_text())
        expected=record(job['dataset_id'])
        if manifest.get('import_job_id')!=job['id'] or manifest.get('upload_sha256')!=expected['sha256']:
            raise ValueError('Датасет с таким ID уже существует; перезапись запрещена')
        shutil.rmtree(artifact)
    else:artifact.rename(target)

def cleanup_stale_uploads():
    with store.engine.connect() as conn:known=set(conn.execute(select(store.dataset_imports.c.upload_id)).scalars())
    for path in UPLOADS.iterdir():
        if path.suffix in ('.part','.zip') and path.stat().st_mtime<time.time()-86400 and path.stem not in known:
            path.unlink(missing_ok=True)
