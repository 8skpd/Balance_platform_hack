"""Durable DB outbox + Celery delivery. DB state is authoritative, messages contain only IDs."""
import logging
import time
import uuid
from contextlib import contextmanager

from celery import Celery
from sqlalchemy import select, update, func

from . import store
from .settings import REDIS_URL, QUEUE_NAME, RUNS, RUN_TIMEOUT, RUN_MEMORY_MB

celery = Celery('balance', broker=REDIS_URL, include=['app.tasks'])
celery.conf.update(
    task_default_queue=QUEUE_NAME, task_serializer='json', accept_content=['json'],
    task_ignore_result=True, task_acks_late=True, task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, broker_connection_retry_on_startup=True,
    broker_transport_options={'visibility_timeout': 3600, 'socket_connect_timeout': 2, 'socket_timeout': 2},
    task_publish_retry=False, worker_cancel_long_running_tasks_on_connection_loss=True,
    worker_max_tasks_per_child=30,
)
TERMINAL = {'completed', 'failed', 'cancelled', 'timed_out'}

@contextmanager
def transaction():
    """SQLite write lock / PostgreSQL row locks serialize claim, cancellation, publication."""
    with store.engine.connect() as conn:
        if store.engine.dialect.name == 'sqlite':
            conn.exec_driver_sql('BEGIN IMMEDIATE')
        with conn.begin_nested() if conn.in_transaction() else conn.begin():
            yield conn
        conn.commit()

def locked_job(conn, job_id):
    row = conn.execute(select(store.jobs).where(store.jobs.c.id == job_id).with_for_update()).mappings().first()
    return dict(row) if row else None

def update_ai(conn, run_id, state):
    """Keep one successful answer while the latest attempt is pending or failed."""
    old = conn.execute(select(store.runs.c.ai).where(store.runs.c.id == run_id).with_for_update()).scalar_one_or_none() or {}
    last = old if old.get('status') == 'completed' else old.get('last_success')
    if state.get('status') != 'completed' and last:
        state = {**state, 'last_success':{k:v for k,v in last.items() if k != 'last_success'}}
    conn.execute(update(store.runs).where(store.runs.c.id == run_id).values(ai=state))


def submit(owner_id, run_id, kind, *, run_values=None, code=None, params=None, timeout=None, memory_mb=None, check_ref=None, dataset_id=None, dataset_values=None, source_run_id=None):
    job = {'id': str(uuid.uuid4()), 'owner_id': owner_id, 'run_id': run_id, 'kind': kind,
        'status': 'queued', 'created_at': store.now(), 'attempts': 0, 'dataset_id': dataset_id, 'progress': 0, 'stage': 'В очереди',
        'timeout': timeout or (RUN_TIMEOUT if kind == 'run' else 330 if kind == 'ai' else 20),
        'memory_mb': memory_mb or (RUN_MEMORY_MB if kind == 'run' else 1024), 'code': code, 'params': params or {}, 'check_ref':check_ref}
    with transaction() as conn:
        from .operations import admission
        from fastapi import HTTPException
        extra_bytes=0
        if dataset_values and not conn.execute(select(store.dataset_imports.c.id).where(store.dataset_imports.c.upload_id==dataset_values['upload_id'])).first(): extra_bytes=dataset_values['size']
        admission(conn, owner_id, new_run=bool(run_values),extra_bytes=extra_bytes)
        if source_run_id:
            source=conn.execute(select(store.runs).where(store.runs.c.id==source_run_id).with_for_update()).mappings().first()
            if not source or source['owner_id']!=owner_id or source['archived']: raise HTTPException(409,'Исходный анализ передан или архивирован')
        if not run_values and kind != 'import':
            existing = conn.execute(select(store.runs).where(store.runs.c.id==run_id).with_for_update()).mappings().first()
            if not existing or existing['owner_id']!=owner_id: raise HTTPException(409,'Владелец анализа изменился')
            if existing['archived']: raise HTTPException(409,'Сначала восстановите анализ из архива')
        if kind == 'import':
            conn.execute(select(store.users.c.id).where(store.users.c.id == owner_id).with_for_update()).first()
            active = conn.execute(select(func.count()).select_from(store.jobs).where(store.jobs.c.owner_id == owner_id,
                store.jobs.c.kind == 'import', store.jobs.c.status.not_in(TERMINAL))).scalar_one()
            if active >= 3:
                raise ValueError('Одновременно доступны не более трёх импортов. Дождитесь завершения или отмените задачу.')
            conn.execute(store.dataset_imports.insert().values(**dataset_values, job_id=job['id']))
        if kind == 'ai':
            # Lock the run as well: concurrent clicks cannot enqueue duplicate AI requests.
            conn.execute(select(store.runs.c.id).where(store.runs.c.id == run_id).with_for_update()).first()
            active = conn.execute(select(store.jobs.c.id).where(store.jobs.c.run_id == run_id,
                store.jobs.c.kind == 'ai', store.jobs.c.status.not_in(TERMINAL))).first()
            if active:
                raise ValueError('Запрос мнения ИИ уже находится в очереди')
        if run_values:
            conn.execute(store.runs.insert().values(**run_values))
        conn.execute(store.jobs.insert().values(**job))
        if kind == 'ai':
            update_ai(conn, run_id, {'status': 'queued', 'job_id': job['id']})
    # Scheduler publishes the committed outbox even if Redis is currently offline.
    return job

def claim(job_id):
    with transaction() as conn:
        job = locked_job(conn, job_id)
        if not job or job['status'] != 'queued':
            return None
        values = {'status': 'running', 'lease': str(uuid.uuid4()), 'attempts': job['attempts']+1,
                  'started_at': store.now(), 'heartbeat': time.time(), 'error': None, 'progress': 0, 'stage': 'Обработка'}
        conn.execute(update(store.jobs).where(store.jobs.c.id == job_id).values(**values))
        job.update(values)
        if job['kind'] == 'run':
            conn.execute(update(store.runs).where(store.runs.c.id == job['run_id']).values(status='running', stage='starting', progress=0, check_executions=[]))
        elif job['kind'] == 'ai':
            update_ai(conn, job['run_id'], {'status': 'running', 'job_id': job_id})
        return job

def heartbeat(job, progress=None):
    values = {'heartbeat': time.time()}
    if progress and job['kind'] == 'import':
        values.update(progress=max(0, min(99, int(progress['progress']))), stage=str(progress['stage'])[:240])
    with store.engine.begin() as conn:
        conn.execute(update(store.jobs).where(store.jobs.c.id == job['id'], store.jobs.c.lease == job['lease'],
            store.jobs.c.status.in_(['running', 'cancelling'])).values(**values))

def finish(job, status, *, result=None, error=None, duration=None, artifact=None):
    with transaction() as conn:
        current = locked_job(conn, job['id'])
        if not current or current['lease'] != job['lease'] or current['status'] in TERMINAL:
            return False
        if current['status'] == 'cancelling':
            status, result, error = 'cancelled', None, 'Задача отменена пользователем'
        if status == 'completed' and artifact:
            if job['kind'] == 'import':
                from .imports import publish
                publish(job, artifact, result)
            else:
                artifact.replace(RUNS / job['run_id'] / 'result.duckdb')
        conn.execute(update(store.jobs).where(store.jobs.c.id == job['id']).values(
            status=status, result=result, error=error, duration=duration, finished_at=store.now(), heartbeat=time.time(),
            progress=100 if status == 'completed' else current.get('progress'), stage='Готово' if status == 'completed' else status))
        if job['kind'] == 'import' and status == 'completed':
            conn.execute(update(store.dataset_imports).where(store.dataset_imports.c.id == job['dataset_id']).values(stats=result))
        if job['kind'] == 'run':
            values = {'status': status, 'stage': 'complete' if status == 'completed' else status,
                'error': error, 'finished_at': store.now(), 'duration': duration}
            if status == 'completed':
                values.update(summary=result['summary'], checks=result['checks'], progress=100)
            if result and 'check_executions' in result:
                values['check_executions'] = result['check_executions']
            conn.execute(update(store.runs).where(store.runs.c.id == job['run_id']).values(**values))
        elif job['kind']=='check' and status=='completed' and (job.get('check_ref') or {}).get('publish'):
            from .check_results import publish
            publish(conn,job,result)
        elif job['kind'] == 'ai':
            ai = result if status == 'completed' else {'status': status, 'error': error}
            revision=conn.execute(select(store.runs.c.report_revision).where(store.runs.c.id==job['run_id'])).scalar_one()
            context_revision=job['params'].get('report_revision',1)
            extra={'report_revision':context_revision,'stale':context_revision!=revision} if 'report_revision' in job['params'] or revision!=1 else {}
            update_ai(conn, job['run_id'], {**ai, 'job_id': job['id'], **extra})
    return True

def cancel(job_id):
    with transaction() as conn:
        job = locked_job(conn, job_id)
        if job['status'] in TERMINAL:
            return job
        status = 'cancelled' if job['status'] == 'queued' else 'cancelling'
        values = {'status': status}
        if status == 'cancelled':
            values.update(finished_at=store.now(), error='Отменено до начала выполнения')
        conn.execute(update(store.jobs).where(store.jobs.c.id == job_id).values(**values))
        if job['kind'] == 'run':
            conn.execute(update(store.runs).where(store.runs.c.id == job['run_id']).values(status=status, stage=status))
        elif job['kind'] == 'ai':
            update_ai(conn, job['run_id'], {'status': status, 'job_id': job_id})
        job.update(values)
        return job

from .maintenance import shared_task

@shared_task
def dispatch_pending():
    """Called by a separate scheduler, never on the API request path."""
    now = time.time()
    for job in store.list_jobs(active_only=True, detail=False):
        if job['status'] in ('running', 'cancelling') and (job['heartbeat'] or 0) < now-45:
            with transaction() as conn:
                current = locked_job(conn, job['id'])
                if current['status'] not in ('running','cancelling') or (current['heartbeat'] or 0) >= now-45:
                    continue
                status = ('cancelled' if current['status'] == 'cancelling' else
                          'queued' if current['attempts'] < 2 else 'failed')
                error = 'Исполнитель прерван. Повтор с начала.' if status == 'queued' else 'Исполнитель прерван'
                conn.execute(update(store.jobs).where(store.jobs.c.id == job['id']).values(
                    status=status, lease=None, enqueued_at=None, error=error,
                    finished_at=None if status == 'queued' else store.now()))
                if job['kind'] == 'run':
                    conn.execute(update(store.runs).where(store.runs.c.id == job['run_id']).values(status=status, stage=status, error=error, progress=0))
                elif job['kind'] == 'ai':
                    update_ai(conn, job['run_id'], {'status':status,'error':error,'job_id':job['id']})
            continue
        if job['status'] != 'queued' or (job['enqueued_at'] or 0) > now-15:
            continue
        with store.engine.begin() as conn:
            changed = conn.execute(update(store.jobs).where(store.jobs.c.id == job['id'], store.jobs.c.status == 'queued',
                (store.jobs.c.enqueued_at.is_(None) | (store.jobs.c.enqueued_at < now-15))).values(enqueued_at=now)).rowcount
        if changed:
            try:
                celery.send_task('balance.execute', args=[job['id']], task_id=job['id'], retry=False)
            except Exception:
                logging.warning('Redis недоступен; задача %s остаётся в очереди БД', job['id'])

def service_status():
    import redis
    try:
        client = redis.Redis.from_url(REDIS_URL, socket_timeout=1, socket_connect_timeout=1)
        client.ping()
        workers = client.keys(f'{QUEUE_NAME}:worker:*')
        scheduler = bool(client.exists(f'{QUEUE_NAME}:scheduler'))
        return {'broker': True, 'workers': len(workers), 'scheduler': scheduler}
    except Exception:
        return {'broker': False, 'workers': 0, 'scheduler': False}
