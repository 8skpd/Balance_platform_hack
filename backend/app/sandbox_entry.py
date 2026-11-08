"""Only this bootstrap loads user code, AFTER OS policies and hard resource limits."""
import json
import os
import resource
import sys
import traceback
from pathlib import Path

# Invoked with -I: explicitly add only trusted application modules during bootstrap.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolation import restrict_filesystem, restrict_syscalls, parent_death
from mart import Mart

parent_death(int(sys.argv[1]))
work = Path(sys.argv[2])
report = Path(sys.argv[3])
cpu, memory = int(sys.argv[4]), int(sys.argv[5])
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu+1))
resource.setrlimit(resource.RLIMIT_AS, (memory*1024*1024, memory*1024*1024))
resource.setrlimit(resource.RLIMIT_FSIZE, (64*1024*1024, 64*1024*1024))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

# Pre-open only the bounded result destination; after Landlock, paths are read-only.
output = (work / 'result.json').open('w')
try:
    code = (work / 'script.py').read_text()
    params = json.loads((work / 'params.json').read_text())
    allowed = ['/usr', '/lib', '/lib64', '/etc/ld.so.cache', sys.prefix, report,
               '/sys/devices/system/cpu/online', '/proc/stat', '/proc/sys/vm/overcommit_memory', '/proc/self/cgroup',
               '/sys/fs/cgroup']
    if Path(sys.prefix).resolve() in (Path.home().resolve(), Path(__file__).resolve().parents[2]):
        raise RuntimeError('Недопустимый путь Python runtime')
    # Install Landlock BEFORE native libraries start threads; every thread inherits it.
    restrict_filesystem(allowed)
    import duckdb
    conn = duckdb.connect(str(report), read_only=True, config={'threads': 1, 'memory_limit': '128MB', 'temp_directory': ''})
    # No automatic network extensions, filesystem writes or multi-threaded queries.
    conn.execute('SET autoinstall_known_extensions=false')
    conn.execute('SET autoload_known_extensions=false')
    mart = Mart(conn)
    sys.path.remove(str(Path(__file__).resolve().parent))
    restrict_syscalls()
    namespace = {'__name__': '__user_check__'}
    exec(compile(code, '<user-check>', 'exec'), namespace)
    check = namespace.get('CHECK')
    if not isinstance(check, dict) or not callable(check.get('entrypoint')):
        raise ValueError('Скрипт должен экспортировать CHECK с функцией entrypoint(mart, params)')
    result = check['entrypoint'](mart, {**check.get('params', {}), **params})
    json.dump({'rows': result}, output, ensure_ascii=False, allow_nan=False)
    output.flush()
except BaseException:
    traceback.print_exc()
    sys.exit(1)
finally:
    output.close()
