import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.getenv('BALANCE_DATA_DIR', str(ROOT / 'var'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DATASETS = DATA / 'datasets'
RUNS = DATA / 'runs'
for directory in (DATASETS, RUNS):
    directory.mkdir(exist_ok=True)
DATABASE_URL = os.getenv('DATABASE_URL', f'sqlite:///{DATA / "app.sqlite3"}')
OLLAMA_URL = os.getenv('OLLAMA_BASE_URL', 'http://192.168.1.76:11434').rstrip('/')
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'qwen3-server:latest')
OLLAMA_TIMEOUT = float(os.getenv('OLLAMA_TIMEOUT_SECONDS', '300'))
OLLAMA_MAX_TOKENS = int(os.getenv('OLLAMA_MAX_TOKENS', '512'))
SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', '0') == '1'
REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:6380/0')
QUEUE_NAME = os.getenv('BALANCE_QUEUE', 'balance')
RUN_TIMEOUT = int(os.getenv('RUN_TIMEOUT_SECONDS', '3600'))
RUN_MEMORY_MB = int(os.getenv('RUN_MEMORY_MB', '4096'))
JOBS = DATA / 'jobs'
JOBS.mkdir(exist_ok=True)
UPLOADS = DATA / 'uploads'
UPLOADS.mkdir(exist_ok=True)
UPLOAD_MAX_BYTES = 512 * 1024**2
IMPORT_MAX_BYTES = 1024**3
IMPORT_FILE_BYTES = 16 * 1024**2
IMPORT_MAX_PAIRS = 500000
IMPORT_MAX_FILES = IMPORT_MAX_PAIRS * 2
FRONTEND_DIST = Path(os.getenv('BALANCE_FRONTEND_DIST', str(ROOT / 'frontend/dist')))

IMPORT_CSV_BYTES = 512 * 1024**2
NORMALIZATION_WORKERS = max(1, min(8, int(os.getenv("BALANCE_NORMALIZATION_WORKERS", str(min(4, max(1, (os.cpu_count() or 2)//2)))))))
IMPORT_XML_BYTES = 512 * 1024**2
