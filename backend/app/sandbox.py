"""Parent-side validation and an actual, cached OS-policy probe."""
import json
import math
import os
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

def clean_env():
    return {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8',
            'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'PYTHONDONTWRITEBYTECODE': '1'}

def command(work, report, timeout, memory):
    return [sys.executable, '-I', str(Path(__file__).with_name('sandbox_entry.py')),
            str(os.getpid()), str(work), str(report), str(timeout), str(memory)]

@lru_cache(maxsize=1)
def availability():
    import duckdb
    try:
        with tempfile.TemporaryDirectory(prefix='balance-probe-') as folder:
            work = Path(folder)
            report = work / 'probe.duckdb'
            duckdb.connect(str(report), config={'threads': 1}).close()
            (work / 'script.py').write_text("CHECK = {'entrypoint': lambda mart, params: []}")
            (work / 'params.json').write_text('{}')
            process = subprocess.run(command(work, report, 10, 1024), cwd=work, env=clean_env(),
                                     capture_output=True, timeout=15)
            if process.returncode != 0:
                raise RuntimeError(process.stderr.decode(errors='replace')[-2000:])
        return {'available': True, 'engine': 'Landlock + seccomp', 'max_timeout': 60, 'max_memory_mb': 2048}
    except Exception as exc:
        return {'available': False, 'error': 'Изоляция Python недоступна: ' + str(exc)}

def validate_result(value, report):
    import duckdb
    rows = value.get('rows') if isinstance(value, dict) else None
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError('Результат должен содержать список из не более 1000 строк')
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Каждая строка результата должна быть объектом')
        for key in ('LABEL', 'ARGUMENT'):
            if not isinstance(row.get(key), str) or len(row[key]) > 2000:
                raise ValueError(f'{key}: требуется строка до 2000 символов')
        for key, values in [('SOURCE', ('BEFORE', 'AFTER')), ('RESULT', ('PASS', 'FAIL')), ('CONFIDENCE', ('LOW', 'HIGH'))]:
            if row.get(key) not in values:
                raise ValueError(f'Недопустимое значение {key}')
        for key in ('CNT', 'TOTAL'):
            if type(row.get(key)) is not int or not 0 <= row[key] <= 10**12:
                raise ValueError(f'{key}: требуется неотрицательное целое число')
        if row['CNT'] > row['TOTAL']:
            raise ValueError('CNT не может превышать TOTAL')
        if type(row.get('METRIC')) not in (float, int) or not math.isfinite(row['METRIC']):
            raise ValueError('METRIC: требуется конечное число')
        app_ids = row.get('APP_IDS')
        if not isinstance(app_ids, list) or len(app_ids) > 500000 or any(not isinstance(i, str) or len(i) > 200 for i in app_ids):
            raise ValueError('APP_IDS: требуется список до 500000 идентификаторов')
        if len(set(app_ids))!=len(app_ids) or len(app_ids)>row['CNT']:raise ValueError('APP_IDS должен содержать уникальные заявки и не превышать CNT')
        ids.update(app_ids)
        # Ignore arbitrary extra fields; persist only the documented result contract.
        for key in set(row) - {'LABEL','ARGUMENT','SOURCE','RESULT','CONFIDENCE','CNT','TOTAL','METRIC','APP_IDS'}:
            del row[key]
    if ids:
        with duckdb.connect(str(report), read_only=True, config={'threads': 1}) as conn:
            known = {r[0] for r in conn.execute('SELECT APP_ID FROM applications').fetchall()}
        if ids - known:
            raise ValueError('APP_IDS содержит заявки, которых нет в этом отчёте')
    return {'rows': rows}
