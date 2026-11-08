import json
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, Float, Integer, MetaData, String, Text, Table, UniqueConstraint, create_engine, select, update, inspect, text
from .settings import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={'timeout': 30} if DATABASE_URL.startswith('sqlite') else {})
metadata = MetaData()
runs = Table('runs', metadata,
    Column('id', String, primary_key=True), Column('name', String),
    Column('dataset', String), Column('status', String), Column('progress', Integer),
    Column('stage', String), Column('created_at', String), Column('finished_at', String),
    Column('duration', Float), Column('config', JSON), Column('summary', JSON),
    Column('checks', JSON), Column('error', String), Column('ai', JSON), Column('pid', Integer),
    Column('dataset_owner_id', String), Column('archived', Boolean, nullable=False, server_default='0'),
    Column('owner_id', String, index=True), Column('check_plan', JSON), Column('check_executions', JSON), Column('procedure_plan', JSON),
    Column('auto_ai', Boolean, nullable=False, server_default='0'), Column('report_revision', Integer, nullable=False, server_default='1'))
check_library = Table('check_library', metadata,
    Column('id', String, primary_key=True), Column('owner_id', String, nullable=False, index=True),
    Column('name', String, nullable=False), Column('description', Text, nullable=False),
    Column('latest_version', Integer, nullable=False), Column('archived', Boolean, nullable=False, default=False),
    Column('created_at', String, nullable=False), Column('updated_at', String, nullable=False))
check_versions = Table('check_versions', metadata,
    Column('id', String, primary_key=True), Column('check_id', String, nullable=False, index=True),
    Column('version', Integer, nullable=False), Column('name', String, nullable=False), Column('description', Text, nullable=False),
    Column('code', Text, nullable=False), Column('params', JSON, nullable=False), Column('timeout', Integer, nullable=False),
    Column('memory_mb', Integer, nullable=False), Column('sha256', String, nullable=False), Column('created_at', String, nullable=False),
    UniqueConstraint('check_id', 'version', name='uq_check_version'))
procedure_library = Table('procedure_library', metadata,
    Column('id', String, primary_key=True), Column('owner_id', String, nullable=False, index=True),
    Column('stage', String, nullable=False), Column('name', String, nullable=False), Column('code', Text, nullable=False),
    Column('params', JSON, nullable=False), Column('filename', String, nullable=False), Column('revision', Integer, nullable=False),
    Column('timeout', Integer, nullable=False), Column('memory_mb', Integer, nullable=False),
    Column('created_at', String, nullable=False), Column('updated_at', String, nullable=False))
configs = Table('configs', metadata, Column('id', String, primary_key=True), Column('payload', JSON),
    Column('revision', Integer, nullable=False, server_default='1'))
run_templates = Table('run_templates', metadata,
    Column('id', String, primary_key=True), Column('owner_id', String, nullable=False, index=True),
    Column('name', String, nullable=False), Column('payload', JSON, nullable=False),
    Column('revision', Integer, nullable=False), Column('created_at', String, nullable=False), Column('updated_at', String, nullable=False))
dataset_imports = Table('dataset_imports', metadata,
    Column('id', String, primary_key=True), Column('owner_id', String, nullable=False, index=True),
    Column('job_id', String, nullable=False, unique=True), Column('upload_id', String, nullable=False),
    Column('name', String, nullable=False), Column('filename', String, nullable=False),
    Column('size', Integer, nullable=False), Column('sha256', String, nullable=False),
    Column('created_at', String, nullable=False), Column('stats', JSON))
pivot_views = Table('pivot_views', metadata,
    Column('id', String, primary_key=True), Column('owner_id', String, nullable=False, index=True),
    Column('name', String, nullable=False), Column('spec', JSON, nullable=False),
    Column('revision', Integer, nullable=False), Column('created_at', String, nullable=False), Column('updated_at', String, nullable=False))
users = Table('users', metadata, Column('id', String, primary_key=True),
    Column('username', String, nullable=False, unique=True), Column('display_name', String, nullable=False),
    Column('password_hash', String, nullable=False), Column('created_at', String, nullable=False),
    Column('role', String, nullable=False, server_default='user'),
    Column('blocked', Boolean, nullable=False, server_default='0'))
run_shares = Table('run_shares', metadata,
    Column('run_id', String, primary_key=True), Column('user_id', String, primary_key=True),
    Column('created_at', String, nullable=False), Column('granted_by', String, nullable=False))
app_settings = Table('app_settings', metadata,
    Column('id', String, primary_key=True), Column('payload', JSON, nullable=False),
    Column('revision', Integer, nullable=False), Column('updated_at', String), Column('updated_by', String))
sessions = Table('sessions', metadata, Column('token_hash', String, primary_key=True),
    Column('user_id', String, nullable=False, index=True), Column('expires_at', Integer, nullable=False))
system_state = Table('system_state', metadata, Column('id', String, primary_key=True))
jobs = Table('jobs', metadata,
    Column('id', String, primary_key=True), Column('owner_id', String, nullable=False, index=True),
    Column('run_id', String, nullable=False, index=True), Column('kind', String, nullable=False),
    Column('status', String, nullable=False, index=True), Column('created_at', String),
    Column('started_at', String), Column('finished_at', String), Column('heartbeat', Float),
    Column('enqueued_at', Float), Column('lease', String), Column('attempts', Integer, default=0),
    Column('timeout', Integer), Column('memory_mb', Integer), Column('code', Text), Column('params', JSON),
    Column('result', JSON), Column('error', Text), Column('duration', Float), Column('check_ref', JSON),
    Column('dataset_id', String), Column('progress', Integer), Column('stage', String))

audit_events = Table('audit_events', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True), Column('created_at', String, nullable=False),
    Column('actor_id', String), Column('action', String, nullable=False), Column('entity_id', String),
    Column('run_id', String, index=True), Column('details', JSON, nullable=False))
comments = Table('comments', metadata,
    Column('id', String, primary_key=True), Column('run_id', String, nullable=False, index=True),
    Column('target_kind', String, nullable=False), Column('target_id', String, nullable=False),
    Column('author_id', String, nullable=False), Column('body', Text, nullable=False),
    Column('revision', Integer, nullable=False), Column('created_at', String, nullable=False), Column('updated_at', String, nullable=False))
reviews = Table('reviews', metadata,
    Column('run_id', String, primary_key=True), Column('status', String, nullable=False),
    Column('assignee_id', String, nullable=False), Column('reason', Text, nullable=False),
    Column('revision', Integer, nullable=False), Column('updated_at', String, nullable=False), Column('updated_by', String, nullable=False))
backup_operations = Table('backup_operations', metadata,
    Column('id', String, primary_key=True), Column('kind', String, nullable=False), Column('status', String, nullable=False),
    Column('created_at', String, nullable=False), Column('finished_at', String), Column('actor_id', String),
    Column('archive_id', String), Column('result', JSON), Column('error', Text))

def now():
    return datetime.now(timezone.utc).isoformat()

def init():
    # Additive v0.1 -> v0.2 migration. Existing run files and identifiers stay intact.
    inspector = inspect(engine)
    if inspector.has_table('configs') and 'revision' not in {c['name'] for c in inspector.get_columns('configs')}:
        with engine.begin() as conn:
            conn.execute(text('ALTER TABLE configs ADD COLUMN revision INTEGER NOT NULL DEFAULT 1'))
    if inspector.has_table('users') and 'role' not in {c['name'] for c in inspector.get_columns('users')}:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR NOT NULL DEFAULT 'user'"))
    if inspector.has_table('runs') and 'owner_id' not in {c['name'] for c in inspector.get_columns('runs')}:
        with engine.begin() as conn:
            conn.execute(text('ALTER TABLE runs ADD COLUMN owner_id VARCHAR'))
            conn.execute(text('CREATE INDEX IF NOT EXISTS ix_runs_owner_id ON runs (owner_id)'))
    if inspector.has_table('runs'):
        existing = {c['name'] for c in inspect(engine).get_columns('runs')}
        with engine.begin() as conn:
            for column in ('check_plan', 'check_executions', 'procedure_plan'):
                if column not in existing:
                    conn.execute(text(f'ALTER TABLE runs ADD COLUMN {column} JSON'))
    if inspector.has_table('jobs') and 'check_ref' not in {c['name'] for c in inspector.get_columns('jobs')}:
        with engine.begin() as conn:
            conn.execute(text('ALTER TABLE jobs ADD COLUMN check_ref JSON'))
    if inspector.has_table('jobs'):
        existing = {c['name'] for c in inspect(engine).get_columns('jobs')}
        with engine.begin() as conn:
            for column, kind in [('dataset_id','VARCHAR'),('progress','INTEGER'),('stage','VARCHAR')]:
                if column not in existing:
                    conn.execute(text(f'ALTER TABLE jobs ADD COLUMN {column} {kind}'))
    for table, columns in {'users': [('blocked', 'BOOLEAN NOT NULL DEFAULT FALSE')],
                           'runs': [('dataset_owner_id', 'VARCHAR'), ('archived', 'BOOLEAN NOT NULL DEFAULT FALSE'), ('auto_ai', 'BOOLEAN NOT NULL DEFAULT FALSE'), ('report_revision', 'INTEGER NOT NULL DEFAULT 1')]}.items():
        if inspector.has_table(table):
            existing = {c['name'] for c in inspect(engine).get_columns(table)}
            with engine.begin() as conn:
                for column, kind in columns:
                    if column not in existing:
                        conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {kind}'))
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.dataset_owner_id.is_(None)).values(dataset_owner_id=runs.c.owner_id))
        conn.execute(text("INSERT INTO system_state (id) VALUES ('registration') ON CONFLICT (id) DO NOTHING"))
        # One-time adoption only. Later role changes must never be undone on restart.
        adopted=conn.execute(text("INSERT INTO system_state (id) VALUES ('roles-v1') ON CONFLICT (id) DO NOTHING")).rowcount
        if adopted:
            first=conn.execute(select(users.c.id).order_by(users.c.created_at,users.c.id).limit(1)).scalar_one_or_none()
            if first:conn.execute(update(users).where(users.c.id==first).values(role='admin'))

def create_run(values):
    with engine.begin() as conn:
        conn.execute(runs.insert().values(**values))

def patch_run(run_id, **values):
    with engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.id == run_id).values(**values))

def get_run(run_id):
    with engine.connect() as conn:
        row = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
        return dict(row) if row else None

def list_runs(owner_id=None):
    with engine.connect() as conn:
        columns = [c for c in runs.c if c.name not in ('checks', 'config', 'ai', 'check_plan', 'check_executions', 'procedure_plan')]
        query = select(*columns).order_by(runs.c.created_at.desc())
        if owner_id is not None:
            query = query.where(runs.c.owner_id == owner_id)
        rows = conn.execute(query).mappings().all()
        return [{k: v for k, v in dict(r).items() if k not in ('checks', 'config', 'ai', 'check_plan', 'check_executions', 'procedure_plan')} for r in rows]

def get_config(owner_id):
    with engine.connect() as conn:
        return conn.execute(select(configs.c.payload).where(configs.c.id == owner_id)).scalar_one_or_none()

def get_config_state(owner_id):
    with engine.connect() as conn:
        row = conn.execute(select(configs).where(configs.c.id == owner_id)).mappings().first()
    return {'config':row['payload'] if row else None, 'revision':row['revision'] if row else 0}

def save_config(owner_id, payload, expected_revision=None):
    from .queueing import transaction
    with transaction() as conn:
        # Lock the account even before its first config row exists (PostgreSQL).
        conn.execute(select(users.c.id).where(users.c.id == owner_id).with_for_update()).first()
        row = conn.execute(select(configs).where(configs.c.id == owner_id).with_for_update()).mappings().first()
        revision = row['revision'] if row else 0
        if expected_revision is not None and expected_revision != revision:
            raise ValueError('Конфигурация изменена в другой вкладке. Загрузите сохранённую версию и повторите изменения.')
        values = {'payload':payload, 'revision':revision+1}
        if row:
            conn.execute(update(configs).where(configs.c.id == owner_id).values(**values))
        else:
            conn.execute(configs.insert().values(id=owner_id, **values))
        return revision+1

def get_job(job_id):
    with engine.connect() as conn:
        row = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().first()
        return dict(row) if row else None

def list_jobs(owner_id=None, run_id=None, *, active_only=False, detail=True):
    columns = list(jobs.c) if detail else [c for c in jobs.c if c.name not in ('code', 'params', 'result')]
    query = select(*columns).order_by(jobs.c.created_at.asc() if active_only else jobs.c.created_at.desc())
    if active_only:
        query = query.where(jobs.c.status.in_(['queued', 'running', 'cancelling']))
    if owner_id is not None:
        query = query.where(jobs.c.owner_id == owner_id)
    if run_id is not None:
        query = query.where(jobs.c.run_id == run_id)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(query).mappings()]
