"""Cross-process gate for consistent snapshots and file removal (Linux deployment)."""
import fcntl
from contextlib import contextmanager
from functools import wraps
from fastapi.responses import JSONResponse
from .settings import DATA

@contextmanager
def gate(exclusive=False):
    with (DATA/'maintenance.lock').open('a') as lock:
        fcntl.flock(lock, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        try: yield
        finally: fcntl.flock(lock,fcntl.LOCK_UN)

class MaintenanceMiddleware:
    def __init__(self, app): self.app=app
    async def __call__(self,scope,receive,send):
        path=scope.get('path','')
        exempt=path.startswith('/api/admin/backups') or path.startswith('/api/storage/') or path in ('/api/health','/api/auth/me')
        if scope['type']!='http' or not path.startswith('/api/') or exempt:
            return await self.app(scope,receive,send)
        try:
            with gate(): await self.app(scope,receive,send)
        except BlockingIOError:
            await JSONResponse({'detail':'Система создаёт резервную копию или очищает файлы. Повторите запрос после завершения.'},status_code=503)(scope,receive,send)

def shared_task(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        try:
            with gate(): return fn(*args,**kwargs)
        except BlockingIOError: return None  # Durable dispatcher redelivers after maintenance.
    return wrapped
