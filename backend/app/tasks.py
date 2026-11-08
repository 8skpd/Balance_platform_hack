"""Celery supervises disposable processes; only this trusted parent publishes results."""
import fcntl
import json
import logging
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from celery.signals import worker_ready
from . import queueing, sandbox, store
from .settings import JOBS, RUNS, ROOT, REDIS_URL, QUEUE_NAME

@worker_ready.connect
def announce_worker(sender=None, **kwargs):
    def announce():
        import redis
        client = redis.Redis.from_url(REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
        key = f'{QUEUE_NAME}:worker:{sender.hostname}:{os.getpid()}'
        while True:
            try:
                client.set(key, 'ready', ex=10)
            except Exception:
                pass
            time.sleep(3)
    threading.Thread(target=announce, daemon=True).start()

def stop(process):
    if process and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)

from .maintenance import shared_task

@queueing.celery.task(name='balance.execute')
@shared_task
def execute(job_id):
    folder = JOBS / job_id
    folder.mkdir(exist_ok=True)
    # Duplicate Redis deliveries must not run the same job concurrently.
    with (folder / 'worker.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        job = queueing.claim(job_id)
        if job:
            if job['kind'] == 'import':
                for old in folder.glob('*/dataset'):
                    shutil.rmtree(old)
            supervise(job)

class ExecutionError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def control(job, deadline):
    current = store.get_job(job['id'])
    if not current or current['lease'] != job['lease']:
        raise ExecutionError('failed', 'Попытка заменена новым исполнителем')
    if current['status'] == 'cancelling':
        raise ExecutionError('cancelled', 'Задача отменена пользователем')
    if time.monotonic() >= deadline:
        raise ExecutionError('timed_out', 'Превышен общий лимит времени расчёта')


def run_process(job, args, work, env, cwd, phase_timeout, deadline, result_limit):
    control(job, deadline)
    process = None
    phase_deadline = min(deadline, time.monotonic() + phase_timeout)
    try:
        with (work / 'process.log').open('wb') as log:
            process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
            last_heartbeat = 0
            while process.poll() is None:
                if (work/'process.log').stat().st_size>1024**2:raise ExecutionError('failed','Вывод процесса превышает 1 МиБ')
                control(job, deadline)
                now = time.monotonic()
                if now >= phase_deadline:
                    raise ExecutionError('timed_out', f'Превышен лимит времени: {phase_timeout} сек.')
                if now-last_heartbeat >= 3:
                    from .operations import directory_size,limits,usage
                    from .settings import DATA
                    with store.engine.connect() as conn:
                        quota=limits(conn)
                        used=usage(conn,job['owner_id'])
                    if directory_size(JOBS/job['id'])>quota['max_job_storage_mb']*1024**2 or used['storage_bytes']>quota['max_user_storage_mb']*1024**2 or shutil.disk_usage(DATA).free<quota['min_free_mb']*1024**2:
                        raise ExecutionError('failed','Превышена квота хранения или исчерпано место на диске')
                    progress = None
                    progress_path = work / 'progress.json'
                    if job['kind'] == 'import' and progress_path.exists() and progress_path.stat().st_size < 65536:
                        progress = json.loads(progress_path.read_text())
                    queueing.heartbeat(job, progress)
                    last_heartbeat = now
                time.sleep(.25)
        if (work/'process.log').stat().st_size>1024**2:raise ExecutionError('failed','Вывод процесса превышает 1 МиБ')
        if process.returncode != 0:
            with (work / 'process.log').open('rb') as source:
                source.seek(max(0, (work / 'process.log').stat().st_size-4000))
                detail = source.read(4000).decode(errors='replace')
            status = 'timed_out' if process.returncode == -signal.SIGXCPU else 'failed'
            raise ExecutionError(status, f'Процесс завершился с кодом {process.returncode}.\n{detail}')
        control(job, deadline)
        result_path = work / 'result.json'
        if result_path.stat().st_size > result_limit:
            raise ValueError('Результат превышает допустимый размер')
        return json.loads(result_path.read_text())
    finally:
        stop(process)


def isolated_check(job, spec, work, report, deadline):
    work.mkdir(parents=True, exist_ok=True)
    (work / 'script.py').write_text(spec['code'])
    (work / 'params.json').write_text(json.dumps(spec['params'], allow_nan=False))
    args = sandbox.command(work, report, spec['timeout'], spec['memory_mb'])
    result = run_process(job, args, work, sandbox.clean_env(), work, spec['timeout'], deadline, 64*1024**2)
    return sandbox.validate_result(result, report)


def record_checks(job, result, message, progress):
    from sqlalchemy import update
    with queueing.transaction() as conn:
        current = queueing.locked_job(conn, job['id'])
        if current['lease'] != job['lease'] or current['status'] not in ('running', 'cancelling'):
            return
        conn.execute(update(store.runs).where(store.runs.c.id == job['run_id']).values(
            check_executions=result['check_executions'], stage='user_checks', progress=progress))
    with (RUNS / job['run_id'] / 'events.jsonl').open('a') as log:
        log.write(json.dumps({'time':store.now(), 'stage':'user_checks', 'message':message, 'check_key':result['check_executions'][-1]['key']}, ensure_ascii=False)+'\n')


def run_selected_checks(job, result, work, deadline):
    run = store.get_run(job['run_id'])
    plan = run.get('check_plan') or []
    report = work / 'result.duckdb'
    result.setdefault('check_executions', [])
    for index, spec in enumerate(plan):
        if spec['kind'] != 'python':
            continue
        control(job, deadline)
        execution = {k:spec[k] for k in ('key','name','version','kind')}
        execution.update(status='running', rows=0)
        result['check_executions'].append(execution)
        progress = 92 + int(6 * index / max(len(plan), 1))
        record_checks(job, result, f"{spec['name']} · v{spec['version']}: запуск", progress)
        start = time.monotonic()
        try:
            rows = isolated_check(job, spec, work / f'check-{index}', report, deadline)['rows']
            import duckdb
            from .check_results import materialize
            with duckdb.connect(str(report),config={'threads':1}) as membership_conn:
                materialize(rows,membership_conn,len(result['checks']))
            for row in rows:
                row.update(CHECK_ID=spec['key'], CHECK_NAME=spec['name'], CHECK_VERSION=spec['version'], CHECK_KIND='python')
            result['checks'].extend(rows)
            execution.update(status='completed', rows=len(rows))
        except Exception as exc:
            execution.update(status=exc.status if isinstance(exc, ExecutionError) else 'failed', error=str(exc))
            if isinstance(exc, ExecutionError):
                raise ExecutionError(exc.status, f"{spec['name']} · v{spec['version']}: {exc}") from exc
            raise ValueError(f"{spec['name']} · v{spec['version']}: {exc}") from exc
        finally:
            execution['duration'] = round(time.monotonic()-start, 2)
            record_checks(job, result, f"{spec['name']} · v{spec['version']}: {execution['status']}", progress)
    result['summary']['checks_failed'] = sum(row['RESULT']=='FAIL' for row in result['checks'])
    result['summary']['checks_passed'] = sum(row['RESULT']=='PASS' for row in result['checks'])
    result['summary']['checks_total'] = len(result['check_executions'])


def supervise(job):
    work = JOBS / job['id'] / job['lease']
    work.mkdir(parents=True)
    started = time.monotonic()
    deadline = started + job['timeout']
    status, result, error = 'failed', None, None
    try:
        if job['kind'] == 'check':
            result = isolated_check(job, job, work, RUNS / job['run_id'] / 'result.duckdb', deadline)
            from .check_results import materialize
            import duckdb
            directory=RUNS/job['run_id']/'check-members' if (job.get('check_ref') or {}).get('publish') else work
            directory.mkdir(exist_ok=True)
            target=directory/(job['id']+'-'+job['lease']+'.parquet')
            with duckdb.connect(config={'threads':1}) as membership_conn:materialize(result['rows'],membership_conn,parquet=target)
        else:
            args = [sys.executable, '-m', 'app.process_entry', str(os.getpid()), job['kind'], job['id'] if job['kind'] == 'import' else job['run_id'],
                    str(work), str(job['timeout']), str(job['memory_mb'])]
            env = {**os.environ, 'OPENBLAS_NUM_THREADS':'1', 'OMP_NUM_THREADS':'1'}
            env.pop('BALANCE_JOB_AI_URL', None)
            if job['kind']=='ai':
                (work / 'ai-request.json').write_text(json.dumps(job.get('params') or {},ensure_ascii=False))
            if job['kind']=='ai' and (job.get('params') or {}).get('ollama_url'):
                env['BALANCE_JOB_AI_URL']=job['params']['ollama_url']
            result = run_process(job, args, work, env, ROOT / 'backend', job['timeout'], deadline, 16*1024**2)
            if job['kind'] == 'run':
                run_selected_checks(job, result, work, deadline)
        control(job, deadline)
        status = 'completed'
    except ExecutionError as exc:
        status, error = exc.status, str(exc)
    except Exception as exc:
        error = str(exc)
        logging.exception('Job %s failed', job['id'])
    artifact = (work / ('dataset' if job['kind'] == 'import' else 'result.duckdb')
                if status == 'completed' and job['kind'] in ('run', 'import') else None)
    try:
        published=queueing.finish(job, status, result=result, error=error, duration=round(time.monotonic()-started, 2), artifact=artifact)
        if published and job['kind']=='ai':
            run=store.get_run(job['run_id'])
            if run and (job.get('params') or {}).get('report_revision',1)!=run.get('report_revision',1):
                from .ai import schedule_automatic
                schedule_automatic(job['run_id'])
        if published and status=='completed' and (job['kind']=='run' or job['kind']=='check' and (job.get('check_ref') or {}).get('publish')):
            from .ai import schedule_automatic
            schedule_automatic(job['run_id'])
    except Exception as exc:
        logging.exception('Publication failed: %s', job['id'])
        queueing.finish(job, 'failed', error=str(exc), duration=round(time.monotonic()-started, 2))
    finally:
        if job['kind']=='check' and status!='completed':
            candidate=RUNS/job['run_id']/'check-members'/(job['id']+'-'+job['lease']+'.parquet')
            candidate.unlink(missing_ok=True)
        if job['kind'] == 'import' and (work / 'dataset').exists():
            shutil.rmtree(work / 'dataset')
