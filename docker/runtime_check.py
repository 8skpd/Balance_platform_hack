"""Fail startup clearly when the container cannot execute the required Python sandbox."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.settings import DATA
from app.sandbox import availability

def check():
    with tempfile.TemporaryFile(dir=DATA) as probe:
        probe.write(b'write probe')
    result=availability()
    if not result['available']:
        raise RuntimeError(result['error']+' Проверьте поддержку Landlock ABI >= 3 ядром Linux и профиль docker/seccomp.json. Изоляция не отключается.')
    print('Runtime ready: writable data volume; Python sandbox available.',flush=True)

if __name__=='__main__':
    try:check()
    except Exception as exc:
        print('Container startup failed: '+str(exc),file=sys.stderr);sys.exit(1)
