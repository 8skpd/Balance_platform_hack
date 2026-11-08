#!/usr/bin/env python3
"""Trusted local administrator CLI: import a generated archive and verify a real queued run."""
import argparse
import hashlib
import json
import shutil
import sys
import time
import uuid
from pathlib import Path
from sqlalchemy import select

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app import store,imports,api,library
from app.normalizer import DEFAULT_CONFIG


def output(value):print(json.dumps(value,ensure_ascii=False),flush=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--username',required=True);parser.add_argument('--archive',required=True);parser.add_argument('--record',default='artifacts/load-500000-validation.json');args=parser.parse_args()
    source=Path(args.archive).resolve();expected=json.loads(source.with_suffix('.json').read_text())['expected'];record=Path(args.record)
    store.init()
    with store.engine.connect() as conn:
        row=conn.execute(select(store.users).where(store.users.c.username==args.username)).mappings().one()
        user={k:row[k] for k in ('id','username','display_name','role','blocked')}
    assert not user['blocked'] and user['role'] in ('admin','user')
    upload_id=str(uuid.uuid4());uploaded=imports.upload_path(upload_id);shutil.copyfile(source,uploaded)
    with source.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    try:job=imports.enqueue({'owner_id':user['id'],'upload_id':upload_id,'name':'Нагрузочный набор — 500 000 заявок','filename':source.name,'size':source.stat().st_size,'sha256':digest})
    except Exception:uploaded.unlink(missing_ok=True);raise
    result={'owner':user['username'],'dataset_id':job['dataset_id'],'import_job_id':job['id'],'archive':str(source),'expected':expected,'started_at':store.now()}
    def save():record.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save();output(result)
    last=None;last_print=0
    while True:
        current=store.get_job(job['id']);state=(current['status'],current['progress'])
        if state!=last or time.monotonic()-last_print>30:
            output({'phase':'import','status':current['status'],'progress':current['progress'],'stage':current['stage']});last=state;last_print=time.monotonic()
        if current['status'] in ('completed','failed','cancelled','timed_out'):break
        time.sleep(2)
    result['import_duration']=current['duration'];result['import_status']=current['status'];save()
    if current['status']!='completed':raise RuntimeError(current['error'])
    imported=imports.record(job['dataset_id']);assert imported['stats']['pairs']==expected['total_apps'];result['dataset_stats']=imported['stats'];save()
    body=api.NewRun(name='Нагрузочный тест — 500 000 заявок',dataset=job['dataset_id'],config=DEFAULT_CONFIG)
    plan=library.resolve_plan(user,None,[],body.config.model_dump());run=api.enqueue_run(body,user,plan)
    result['run_id']=run['id'];save();output({'phase':'run','run_id':run['id']})
    last=None;last_print=0
    while True:
        current=store.get_run(run['id']);state=(current['status'],current['stage'],current['progress'])
        if state!=last or time.monotonic()-last_print>30:
            output({'phase':'run','status':current['status'],'stage':current['stage'],'progress':current['progress']});last=state;last_print=time.monotonic()
        if current['status'] in ('completed','failed','cancelled','timed_out'):break
        time.sleep(2)
    result['run_status']=current['status'];result['run_duration']=current.get('duration');result['error']=current.get('error');save()
    if current['status']!='completed':raise RuntimeError(current['error'])
    for key in ('total_apps','changed_apps','decision_changed'):assert current['summary'][key]==expected[key],(key,current['summary'][key],expected[key])
    assert all(c['status']=='completed' for c in current['check_executions']) and len(current['check_executions'])==5
    assert current['summary']['processing']['processes']>1
    result.update(summary=current['summary'],checks=current['checks'],verified=True,finished_at=store.now());save();output(result)

if __name__=='__main__':main()
