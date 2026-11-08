"""Owner-scoped library. Versions and run snapshots are immutable."""
import ast
import hashlib
import importlib.util
import json
import uuid
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update

from . import store
from .auth import current_user, require_writer
from .queueing import transaction

router = APIRouter(prefix='/api/check-library', dependencies=[Depends(require_writer)])

def valid_params(params):
    try:
        if len(json.dumps(params, ensure_ascii=False, allow_nan=False).encode()) > 16000:
            raise ValueError('Параметры не должны превышать 16 КБ')
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('Параметры должны быть конечным JSON-объектом до 16 КБ') from exc
    return params

class VersionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=2000)
    code: str = Field(min_length=1, max_length=50000)
    params: dict = Field(default_factory=dict)
    timeout: int = Field(default=20, ge=1, le=60)
    memory_mb: int = Field(default=1024, ge=512, le=2048)

    @model_validator(mode='after')
    def validate_content(self):
        valid_params(self.params)
        try:
            ast.parse(self.code)
        except (SyntaxError, ValueError, RecursionError) as exc:
            raise ValueError(f'Ошибка синтаксиса Python: {exc}') from exc
        return self

class NextVersion(VersionInput):
    expected_version: int = Field(ge=1)

class ArchiveInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    archived: bool

class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_id: str = Field(min_length=1, max_length=100)
    params: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_params(self):
        valid_params(self.params)
        return self

@lru_cache(maxsize=1)
def builtin_catalog():
    result = []
    for file in sorted((Path(__file__).parent / 'checks').glob('check_*.py')):
        spec = importlib.util.spec_from_file_location(file.stem, file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # Only bundled source files, never library code.
        code = file.read_text()
        result.append({'key': module.CHECK['id'], 'kind':'builtin', 'name':module.CHECK['label'],
                       'version':module.CHECK.get('version', 1), 'code':code, 'sha256':hashlib.sha256(code.encode()).hexdigest(),
                       'params':module.CHECK.get('params', {})})
    return result

def public_spec(spec):
    return {k:v for k,v in spec.items() if k != 'code'}

def require_definition(conn, check_id, user, *, lock=False):
    query = select(store.check_library).where(store.check_library.c.id == check_id, store.check_library.c.owner_id == user['id'])
    if lock:
        query = query.with_for_update()
    row = conn.execute(query).mappings().first()
    if not row:
        raise HTTPException(404, 'Проверка не найдена')
    return dict(row)

def require_version(conn, version_id, user):
    query = select(store.check_versions).join(store.check_library,
        store.check_versions.c.check_id == store.check_library.c.id).where(
        store.check_versions.c.id == version_id, store.check_library.c.owner_id == user['id'])
    row = conn.execute(query).mappings().first()
    if not row:
        raise HTTPException(404, 'Версия проверки не найдена')
    return dict(row)

def version_spec(version, overrides=None):
    return {'kind':'python', 'key':f"python:{version['check_id']}:{version['version']}",
            'library_id':version['check_id'], 'version_id':version['id'], 'version':version['version'],
            **{k:version[k] for k in ('name','description','code','sha256','timeout','memory_mb')},
            'params':{**version['params'], **(overrides or {})}}

def resolve_plan(user, builtin_ids, selections, config):
    catalog = {c['key']:c for c in builtin_catalog()}
    ids = list(catalog) if builtin_ids is None else builtin_ids
    if len(ids) != len(set(ids)) or set(ids)-set(catalog):
        raise HTTPException(422, 'Неизвестная или повторяющаяся штатная проверка')
    plan = [{**catalog[i], 'params':{**catalog[i]['params'], **config['check_params']}} for i in ids]
    selected = set()
    with store.engine.connect() as conn:
        for selection in selections:
            version = require_version(conn, selection.version_id, user)
            definition = require_definition(conn, version['check_id'], user)
            if definition['archived']:
                raise HTTPException(409, 'Архивная проверка недоступна для новых расчётов')
            if definition['id'] in selected:
                raise HTTPException(422, 'Выберите только одну версию каждой проверки')
            selected.add(definition['id'])
            spec = version_spec(version, selection.params)
            from .code_validation import require_valid
            require_valid('checks', spec['code'])
            try:
                valid_params(spec['params'])
            except ValueError as exc:
                raise HTTPException(422, str(exc))
            plan.append(spec)
    return plan

@router.get('/builtin')
def builtins():
    return [public_spec(c) for c in builtin_catalog()]

@router.get('/builtin/{key}')
def builtin_source(key: str):
    for spec in builtin_catalog():
        if spec['key']==key: return spec
    raise HTTPException(404,'Проверка не найдена')

@router.get('')
def list_checks(include_archived: bool = False, user: dict = Depends(current_user)):
    query = select(store.check_library).where(store.check_library.c.owner_id == user['id']).order_by(store.check_library.c.updated_at.desc())
    if not include_archived:
        query = query.where(store.check_library.c.archived == False)
    with store.engine.connect() as conn:
        return [dict(r) for r in conn.execute(query).mappings()]

def insert_version(conn, check_id, number, body):
    values = body.model_dump(exclude={'expected_version'})
    version = {**values, 'id':str(uuid.uuid4()), 'check_id':check_id, 'version':number,
               'created_at':store.now(), 'sha256':hashlib.sha256(body.code.encode()).hexdigest()}
    conn.execute(store.check_versions.insert().values(**version))
    return version

@router.post('', status_code=201)
def create_check(body: VersionInput, user: dict = Depends(current_user)):
    from .code_validation import require_valid
    require_valid('checks',body.code)
    check_id = str(uuid.uuid4())
    with transaction() as conn:
        conn.execute(store.check_library.insert().values(id=check_id, owner_id=user['id'], name=body.name,
            description=body.description, latest_version=1, archived=False, created_at=store.now(), updated_at=store.now()))
        return insert_version(conn, check_id, 1, body)

@router.get('/{check_id}')
def check_detail(check_id: str, user: dict = Depends(current_user)):
    with store.engine.connect() as conn:
        definition = require_definition(conn, check_id, user)
        columns = [c for c in store.check_versions.c if c.name != 'code']
        versions = [dict(r) for r in conn.execute(select(*columns).where(store.check_versions.c.check_id == check_id)
                    .order_by(store.check_versions.c.version.desc())).mappings()]
        return {**{k:v for k,v in definition.items() if k != 'owner_id'}, 'versions':versions}

@router.get('/{check_id}/versions/{number}')
def version_detail(check_id: str, number: int, user: dict = Depends(current_user)):
    with store.engine.connect() as conn:
        require_definition(conn, check_id, user)
        row = conn.execute(select(store.check_versions).where(store.check_versions.c.check_id == check_id,
            store.check_versions.c.version == number)).mappings().first()
        if not row:
            raise HTTPException(404, 'Версия проверки не найдена')
        return dict(row)

@router.post('/{check_id}/versions', status_code=201)
def add_version(check_id: str, body: NextVersion, user: dict = Depends(current_user)):
    from .code_validation import require_valid
    require_valid('checks',body.code)
    with transaction() as conn:
        definition = require_definition(conn, check_id, user, lock=True)
        if definition['archived']:
            raise HTTPException(409, 'Сначала восстановите проверку из архива')
        if definition['latest_version'] != body.expected_version:
            raise HTTPException(409, 'Уже сохранена новая версия. Обновите историю перед сохранением; ваш код остаётся в редакторе.')
        version = insert_version(conn, check_id, definition['latest_version']+1, body)
        conn.execute(update(store.check_library).where(store.check_library.c.id == check_id).values(
            name=body.name, description=body.description, latest_version=version['version'], updated_at=store.now()))
        return version

@router.post('/{check_id}/archive')
def archive(check_id: str, body: ArchiveInput, user: dict = Depends(current_user)):
    with transaction() as conn:
        require_definition(conn, check_id, user, lock=True)
        conn.execute(update(store.check_library).where(store.check_library.c.id == check_id).values(archived=body.archived, updated_at=store.now()))
    return {'archived':body.archived}
