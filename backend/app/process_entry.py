"""Resource-constrained trusted pipeline entry; user code never enters this process."""
import json
import os
import resource
import sys
from pathlib import Path
from .isolation import parent_death

def main():
    parent_death(int(sys.argv[1]))
    kind, run_id, folder = sys.argv[2:5]
    cpu, memory = int(sys.argv[5]), int(sys.argv[6])
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu+1))
    resource.setrlimit(resource.RLIMIT_AS, (memory*1024*1024, memory*1024*1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (2*1024**3, 2*1024**3))
    if kind == 'run':
        from .worker import execute
        result = execute(run_id, Path(folder))
    elif kind == 'import':
        from .imports import execute
        result = execute(run_id, Path(folder))
    else:
        from .ai import generate_ai,generate_chat
        snapshot_path=Path(folder) / 'ai-request.json'
        snapshot=json.loads(snapshot_path.read_text()) if snapshot_path.exists() else {}
        if snapshot.get('chat'):
            result = generate_chat(run_id, os.environ.get('BALANCE_JOB_AI_URL'), context=snapshot.get('context'), model=snapshot.get('model'), message=snapshot['message'], conversation=snapshot.get('conversation'))
        else:
            result = generate_ai(run_id, os.environ.get('BALANCE_JOB_AI_URL'), context=snapshot.get('context'), model=snapshot.get('model'))
        if result['status'] != 'completed':
            raise RuntimeError(result.get('error', 'Ошибка ИИ'))
    (Path(folder) / 'result.json').write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
