"""Idempotent local service launcher. PID files and logs belong to BALANCE_DATA_DIR."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app import store
from app.settings import DATA, REDIS_URL, QUEUE_NAME
import redis

def alive(pidfile, signature):
    try:
        pid = int(pidfile.read_text())
        command = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode()
        return signature in command
    except (OSError, ValueError):
        return False

def start(name, args, signature, env=None):
    pidfile = DATA / f'{name}.pid'
    if alive(pidfile, signature):
        return
    with (DATA / f'{name}.log').open('ab') as log:
        process = subprocess.Popen(args, cwd=ROOT / 'backend', env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    pidfile.write_text(str(process.pid))

def ping(client):
    try:
        return client.ping()
    except redis.RedisError:
        return False

if __name__ == '__main__':
    store.init()
    client = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    if not ping(client):
        url = urlparse(REDIS_URL)
        if url.hostname not in ('127.0.0.1', 'localhost') or url.port != 6380:
            raise SystemExit('Redis недоступен: проверьте REDIS_URL')
        binary = shutil.which('redis-server') or str(ROOT / 'var/tools/redis/usr/bin/redis-server')
        if not Path(binary).exists():
            raise SystemExit('Выполните ./scripts/install-redis.sh')
        redis_data = ROOT / 'var/redis'
        redis_data.mkdir(exist_ok=True)
        env = {**os.environ, 'LD_LIBRARY_PATH': str(ROOT / 'var/tools/redis/usr/lib/x86_64-linux-gnu')}
        start('redis', [binary, '--bind','127.0.0.1','--port','6380','--appendonly','yes','--dir',str(redis_data),
              '--save','','--maxmemory','256mb','--maxmemory-policy','noeviction'], 'redis-server', env)
        for _ in range(30):
            if ping(client): break
            time.sleep(.2)
        else: raise SystemExit('Redis не запустился. Проверьте redis.log')
    start('worker', [sys.executable,'-m','celery','-A','app.queueing:celery','worker',
          '--concurrency','2','--queues',QUEUE_NAME,'--hostname',f'{QUEUE_NAME}@%h','--loglevel','INFO'], 'app.queueing:celery')
    start('dispatcher', [sys.executable,'-m','app.dispatcher'], 'app.dispatcher')
    for _ in range(50):
        if client.keys(f'{QUEUE_NAME}:worker:*') and client.exists(f'{QUEUE_NAME}:scheduler'):
            print('Redis, Celery и планировщик готовы.')
            break
        time.sleep(.2)
    else: raise SystemExit(f'Сервисы не готовы. Проверьте {DATA}/worker.log и dispatcher.log')
