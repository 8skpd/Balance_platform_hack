"""Probe the API/SQLite or the current container's worker heartbeat."""
import json
import socket
import sys
import urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

def check(service):
    if service=='app':
        from sqlalchemy import text
        from app import store
        with store.engine.connect() as conn:conn.execute(text('SELECT count(*) FROM users')).scalar_one()
        with urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=2) as response:
            if json.load(response).get('status')!='ok':raise RuntimeError('API is not ready')
    else:
        import redis
        from app.settings import REDIS_URL,QUEUE_NAME
        client=redis.Redis.from_url(REDIS_URL,socket_timeout=2,socket_connect_timeout=2)
        if service=='worker':
            ready=next(client.scan_iter(match=f'{QUEUE_NAME}:worker:balance@{socket.gethostname()}:*'),None)
        elif service=='dispatcher':ready=client.exists(f'{QUEUE_NAME}:scheduler')
        else:raise ValueError('Unknown service')
        if not ready:raise RuntimeError(service+' heartbeat is missing')

if __name__=='__main__':
    try:check(sys.argv[1])
    except Exception as exc:print(str(exc),file=sys.stderr);sys.exit(1)
