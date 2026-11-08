"""Bounded process pool; workers write compressed shards rather than returning row arrays."""
import gzip
import json
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from .dataset_storage import iter_pairs
from .normalizer import normalize, segments

_config=None
_folder=None

def initialize(config,folder,parent):
    global _config,_folder
    from .isolation import parent_death
    parent_death(parent)
    _config=config;_folder=Path(folder)

def process_batch(index,items,config,folder):
    normal=Path(folder)/f'normalized-{index:06d}.jsonl.gz'
    segment=Path(folder)/f'segments-{index:06d}.jsonl.gz'
    rows=0
    with gzip.open(normal,'wt',encoding='utf-8',compresslevel=1) as nf,gzip.open(segment,'wt',encoding='utf-8',compresslevel=1) as sf:
        for app_id,before,after in items:
            docs=[]
            for source,raw in [('BEFORE',before),('AFTER',after)]:
                try:
                    doc=json.loads(raw)
                    if not isinstance(doc,dict):raise ValueError('Корень ответа должен быть объектом')
                    actual=doc.get('appData',{}).get('application_id')
                    if actual is not None and str(actual)!=app_id:raise ValueError('Идентификатор внутри ответа не совпадает с именем файла')
                    docs.append(doc)
                    for row in normalize(doc,app_id,source,config,max_output_chars=32*1024**2):
                        nf.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n');rows+=1
                except Exception as exc:raise ValueError(f'{source}/{app_id}: {exc}') from exc
            sf.write(json.dumps(segments(app_id,*docs),ensure_ascii=False,separators=(',',':'))+'\n')
    return {'normal':str(normal),'segments':str(segment),'pairs':len(items),'rows':rows,'pid':os.getpid(),'index':index}

def task(index,items):return process_batch(index,items,_config,_folder)

def batches(dataset,batch_size=500,max_bytes=8*1024**2):
    batch=[];size=0;index=0
    for record in iter_pairs(dataset):
        length=len(record[1])+len(record[2])
        if batch and (len(batch)>=batch_size or size+length>max_bytes):
            yield index,batch;index+=1;batch=[];size=0
        batch.append(record);size+=length
    if batch:yield index,batch

def run(dataset,config,folder,total,workers,progress):
    started=time.monotonic();completed=0;results=[];pids=set()
    workers=min(workers,max(1,(total+499)//500))
    source=iter(batches(dataset))
    if workers==1:
        for index,items in source:
            result=process_batch(index,items,config,folder);results.append(result)
            completed+=result['pairs'];pids.add(result['pid']);progress(completed)
    else:
        with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn'),initializer=initialize,initargs=(config,str(folder),os.getpid())) as pool:
            pending=set()
            def fill():
                while len(pending)<workers*2:
                    try:index,items=next(source)
                    except StopIteration:return
                    pending.add(pool.submit(task,index,items))
            fill()
            while pending:
                done,pending=wait(pending,return_when=FIRST_COMPLETED)
                for future in done:
                    result=future.result();results.append(result);completed+=result['pairs'];pids.add(result['pid']);progress(completed)
                fill()
    results.sort(key=lambda r:r['index'])
    return [r['normal'] for r in results],[r['segments'] for r in results],{'workers':workers,'processes':len(pids),'batches':len(results),'pairs':completed,'normalized_rows':sum(r['rows'] for r in results),'seconds':round(time.monotonic()-started,3)}
