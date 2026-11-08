from datetime import datetime
import json
import math
import uuid
from contextlib import asynccontextmanager, contextmanager
from html import escape
from typing import Literal

import duckdb
import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, Header
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import store, queueing, library, imports, run_templates, access, admin, operations, workflow, comparisons, storage, backups, procedures, ai_chat
from . import pivot as pivot_engine
from .procedures import Selection as ProcedureSelection
from .auth import router as auth_router, current_user, require_writer
from .datasets import bootstrap, inventory, paths
from .mart import records, where
from .normalizer import DEFAULT_CONFIG
from .normalization_preview import preview
from .settings import ROOT, RUNS, FRONTEND_DIST


def valid_path(path):
    return (bool(path) and len(path) <= 2048 and path == path.strip()
            and all(part and part == part.strip() for part in path.split('.'))
            and not any(ord(c) < 32 for c in path))

class ArrayRule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['key', 'index', 'value']
    keys: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def validate_keys(self):
        if self.mode == 'key' and (not self.keys or any(not valid_path(k) for k in self.keys)):
            raise ValueError('Режим key требует корректные пути ключей')
        if len(set(self.keys)) != len(self.keys):
            raise ValueError('Ключи массива не должны повторяться')
        return self

class CheckParams(BaseModel):
    model_config = ConfigDict(extra='forbid')
    max_drop_rel: float = Field(default=0.1, ge=0, le=1)
    low_confidence_cnt: int = Field(default=5, ge=0, le=10000)
    app_ids_limit: int = Field(default=100, ge=1, le=20000)

class RunConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    exclude_paths: list[str] = Field(max_length=100)
    arrays: dict[str, ArrayRule]
    buckets: dict[str, list[float]] = Field(default_factory=dict)
    check_params: CheckParams = Field(default_factory=CheckParams)
    layout: dict[str, dict[str,str]] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_buckets(self):
        if len(self.layout)>200 or any(not valid_path(p) or set(v)-{'PREFIX','CLASS','SUFFIX','KEY'} or any(len(x)>2048 for x in v.values()) for p,v in self.layout.items()):
            raise ValueError('Некорректные правила структурных колонок')
        if any(not valid_path(p) for p in [*self.exclude_paths, *self.arrays, *self.buckets]):
            raise ValueError('Путь должен содержать непустые части через точку, без пробелов по краям (до 2048 символов)')
        if len(set(self.exclude_paths)) != len(self.exclude_paths):
            raise ValueError('Исключения не должны повторяться')
        if len(self.arrays) > 200 or len(self.buckets) > 100:
            raise ValueError('Допускается до 200 правил массивов и 100 числовых интервалов')
        for boundaries in self.buckets.values():
            if len(boundaries) > 100 or any(not math.isfinite(v) for v in boundaries) or boundaries != sorted(set(boundaries)):
                raise ValueError('Границы бакетов должны быть конечными, уникальными и возрастающими')
        return self

class NewRun(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset: str = Field(min_length=1, max_length=100)
    name: str = Field(default='Сравнение стратегии', min_length=1, max_length=120)
    config: RunConfig
    builtin_checks: list[str] | None = Field(default=None, max_length=5)
    user_checks: list[library.Selection] = Field(default_factory=list, max_length=20)
    procedures: dict[str,ProcedureSelection] = Field(default_factory=dict)
    auto_ai: bool = False

@asynccontextmanager
async def lifespan(app):
    store.init()
    with store.engine.begin() as conn:
        conn.execute(store.backup_operations.update().where(store.backup_operations.c.status=='running').values(status='failed',error='Операция прервана перезапуском API',finished_at=store.now()))
    storage.recover_cleanup()
    bootstrap()
    imports.cleanup_stale_uploads()
    # Jobs live outside the API lifecycle. Only legacy interrupted work needs migration.
    for run in store.list_runs():
        if run['status'] in ('queued', 'running') and not store.list_jobs(run_id=run['id']):
            store.patch_run(run['id'], status='failed', error='Старый процесс прерван. Создайте новый запуск.')
    yield

app = FastAPI(title='Баланс · Анализ изменений', version='0.11.0', lifespan=lifespan)

secured = APIRouter(dependencies=[Depends(current_user)])
app.include_router(auth_router)
app.include_router(library.router)
app.include_router(procedures.router)
from . import code_validation
app.include_router(code_validation.router)
app.include_router(ai_chat.router)
app.include_router(pivot_engine.router)
app.include_router(imports.router)
app.include_router(access.router)
app.include_router(admin.router)
app.include_router(operations.router)
app.include_router(workflow.router)
app.include_router(comparisons.router)
app.include_router(storage.router)
app.include_router(backups.router)
from .maintenance import MaintenanceMiddleware
app.add_middleware(MaintenanceMiddleware)

@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic's default error payload can echo the password in its `input` field.
    return JSONResponse({'detail': [{k: e[k] for k in ('loc', 'msg', 'type')} for e in exc.errors()]}, status_code=422)

@app.middleware('http')
async def protect_browser_requests(request: Request, call_next):
    if request.url.path.startswith('/api/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        same_origin = str(request.base_url).rstrip('/')
        if (request.headers.get('sec-fetch-site') == 'cross-site'
                or (origin is not None and origin != same_origin)
                or (origin is None and request.headers.get('x-balance-request') != '1')):
            return JSONResponse({'detail': 'Запрос отклонён: недопустимый источник'}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

@app.get('/api/health')
def health():
    return {'status': 'ok', 'version': '0.11.0', 'mode': 'multi-user'}

require_run = access.require_run

@contextmanager
def database(run_id, user):
    require_run(run_id, user, complete=True)
    conn = duckdb.connect(str(RUNS / run_id / 'result.duckdb'), read_only=True)
    try:
        yield conn
    finally:
        conn.close()

@secured.get('/api/datasets')
def datasets(user: dict = Depends(require_writer)):
    return inventory(user['id'])

@secured.get('/api/config')
def config(response: Response, user: dict = Depends(require_writer)):
    state = store.get_config_state(user['id'])
    response.headers['ETag'] = '"'+str(state['revision'])+'"'
    return state['config'] or DEFAULT_CONFIG

@secured.get('/api/config/state')
def config_state(user: dict = Depends(require_writer)):
    state = store.get_config_state(user['id'])
    return {**state, 'config':state['config'] or DEFAULT_CONFIG}

@secured.put('/api/config')
def update_config(body: RunConfig, response: Response, if_match: str | None = Header(default=None), user: dict = Depends(require_writer)):
    if if_match is None:
        raise HTTPException(428, 'Обновите страницу конфигурации. Для сохранения нужна её редакция (If-Match).')
    value = if_match.strip('"')
    if not value.isascii() or not value.isdigit() or len(value)>18:
        raise HTTPException(422, 'Некорректная редакция конфигурации')
    try:
        revision = store.save_config(user['id'], body.model_dump(), int(value))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    response.headers['ETag'] = '"'+str(revision)+'"'
    return body

class TemplateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=120)
    spec: NewRun

    @model_validator(mode='after')
    def clean_name(self):
        self.name=self.name.strip()
        if not self.name or any(ord(c)<32 for c in self.name):
            raise ValueError('Укажите непустое название шаблона без управляющих символов')
        return self

class TemplateUpdate(TemplateInput):
    revision: int = Field(ge=1)

@secured.get('/api/run-templates')
def list_templates(user: dict = Depends(require_writer)):
    return run_templates.inventory(user)

@secured.post('/api/run-templates',status_code=201)
def create_template(body: TemplateInput,user: dict = Depends(require_writer)):
    return run_templates.save(body.name,body.spec,user)

@secured.get('/api/run-templates/{template_id}')
def get_template(template_id: str,user: dict = Depends(require_writer)):
    return run_templates.detail(template_id,user)

@secured.put('/api/run-templates/{template_id}')
def update_template(template_id: str,body: TemplateUpdate,user: dict = Depends(require_writer)):
    return run_templates.save(body.name,body.spec,user,template_id,body.revision)

@secured.delete('/api/run-templates/{template_id}')
def delete_template(template_id: str,revision: int = Query(ge=1),user: dict = Depends(require_writer)):
    return run_templates.remove(template_id,revision,user)

class PreviewConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    config: RunConfig
    app_id: str | None = Field(default=None, min_length=1, max_length=100)

@secured.post('/api/datasets/{dataset_id}/normalization-preview')
def preview_config(dataset_id: str, body: PreviewConfig, user: dict = Depends(require_writer)):
    try:
        folder = paths(dataset_id, user['id'])
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return preview(folder, body.config.model_dump(), body.app_id)

@secured.get('/api/runs')
def runs(user: dict = Depends(current_user)):
    return access.list_runs(user)

@secured.post('/api/runs', status_code=201)
def create_run(body: NewRun, user: dict = Depends(require_writer)):
    try:
        paths(body.dataset, user['id'])
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    plan = library.resolve_plan(user, body.builtin_checks, body.user_checks, body.config.model_dump())
    return enqueue_run(body, user, plan)

@secured.post('/api/runs/validate')
def validate_run(body: NewRun, user: dict = Depends(require_writer)):
    try: paths(body.dataset, user['id'])
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc
    plan = library.resolve_plan(user,body.builtin_checks,body.user_checks,body.config.model_dump())
    procedure_plan = procedures.resolve(user,body.procedures)
    diagnostics=[]
    for stage,spec in procedure_plan.items():
        diagnostics.extend({**d,'stage':stage,'name':spec['name']} for d in code_validation.analyze(stage,spec['code'])['diagnostics'])
    for spec in plan:
        diagnostics.extend({**d,'stage':'checks','name':spec['name']} for d in code_validation.analyze('checks',spec['code'])['diagnostics'])
    return {'valid':not any(d['severity']=='error' for d in diagnostics),'diagnostics':diagnostics,'scope':'static'}

def enqueue_run(body, user, plan, dataset_owner_id=None, source_run_id=None, procedure_plan=None):
    procedure_plan=procedure_plan if procedure_plan is not None else procedures.resolve(user,body.procedures)
    for stage,spec in procedure_plan.items():
        if spec['kind']=='python': code_validation.require_valid(stage,spec['code'])
    for spec in plan:
        if spec['kind']=='python': code_validation.require_valid('checks',spec['code'])
    if any(p["kind"]=="python" for p in procedure_plan.values()):require_sandbox()
    if any(spec['kind'] == 'python' for spec in plan):
        require_sandbox()
    run_id = str(uuid.uuid4())
    (RUNS / run_id).mkdir()
    queueing.submit(user['id'], run_id, 'run', run_values={
        'id': run_id, 'owner_id': user['id'], 'dataset_owner_id':dataset_owner_id or user['id'], 'name': body.name, 'dataset': body.dataset,
        'status': 'queued', 'progress': 0, 'stage': 'queued', 'created_at': store.now(),
        'config': body.config.model_dump(), 'checks': [], 'summary': None, 'ai': {'status': 'idle'},
        'check_plan':plan, 'check_executions':[], 'procedure_plan':procedure_plan, 'auto_ai':body.auto_ai, 'report_revision':1},source_run_id=source_run_id)
    return public_run(require_run(run_id, user))

def public_run(run):
    return {**run, 'procedure_plan':procedures.public(run.get('procedure_plan')), 'check_plan': [library.public_spec(c) for c in run['check_plan']] if run.get('check_plan') is not None else None}

@secured.get('/api/runs/{run_id}')
def run_detail(run_id: str, user: dict = Depends(current_user)):
    run = require_run(run_id, user)
    log = RUNS / run_id / 'events.jsonl'
    events = []
    if log.exists():
        for line in log.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # Writer may still be appending the last line.
    return {**public_run(run), 'events': events[-100:] if access.can_manage(run,user) else [],
            'can_manage':access.can_manage(run,user),'access':'owner' if run['owner_id']==user['id'] else 'shared'}

@secured.get('/api/runs/{run_id}/procedure-plan/{stage}')
def procedure_snapshot(run_id:str,stage:str,user:dict=Depends(require_writer)):
    run=require_run(run_id,user,write=True)
    spec=(run.get('procedure_plan') or {}).get(stage)
    if not spec:raise HTTPException(404,'Код этапа не сохранён в этом старом расчёте')
    return spec

@secured.get('/api/runs/{run_id}/check-plan/{key}')
def check_snapshot(run_id: str, key: str, user: dict = Depends(current_user)):
    run = require_run(run_id, user, write=True)
    for index, spec in enumerate(run.get('check_plan') or []):
        if spec['key'] == key:
            from .settings import JOBS
            execution = next((e for e in run.get('check_executions') or [] if e['key'] == key), None)
            jobs = [j for j in store.list_jobs(run_id=run_id, detail=False) if j['kind'] == 'run']
            logs = ''
            if jobs and jobs[0].get('lease') and spec['kind'] == 'python':
                path = JOBS / jobs[0]['id'] / jobs[0]['lease'] / f'check-{index}' / 'process.log'
                if path.exists():
                    with path.open('rb') as f:
                        f.seek(max(0, path.stat().st_size-16000))
                        logs = f.read(16000).decode(errors='replace')
            return {**spec, 'execution':execution, 'logs':logs}
    raise HTTPException(404, 'Снимок проверки не найден')

def comparison_filters(path, change_type, changed, app_ids=None):
    values = {}
    if path:
        values['path'] = path
    if change_type:
        values['change_type'] = change_type
    if changed:
        values['diff_flag'] = 1
    if app_ids is not None:
        values['app_ids'] = app_ids
    return where(values)

@secured.get('/api/runs/{run_id}/applications')
def applications(run_id: str, user: dict = Depends(current_user), search: str = '', path: str = '', change_type: str = '',
                 changed: bool = False, decision_changed: bool = False, check_index: int | None = None,
                 page: int = Query(1, ge=1), limit: int = Query(30, ge=1, le=100)):
    run = require_run(run_id, user, True)
    app_ids = None
    membership=None;membership_args=[]
    if check_index is not None:
        if check_index < 0 or check_index >= len(run['checks']):
            raise HTTPException(404, 'Проверка не найдена')
        from .check_results import membership_sql
        membership,membership_args=membership_sql(run['checks'][check_index],RUNS/run_id)
        if not membership:app_ids = run['checks'][check_index]['APP_IDS']
    sql, args = comparison_filters(path, change_type, changed, app_ids)
    condition = f' WHERE APP_ID IN (SELECT DISTINCT APP_ID FROM comparison{sql})' if sql else ' WHERE TRUE'
    if membership:
        condition+=' AND APP_ID IN ('+membership+')';args+=membership_args
    if search:
        condition += ' AND contains(lower(APP_ID), lower(?))'
        args.append(search)
    if decision_changed:
        condition += ' AND DECISION_CHANGED=1'
    with database(run_id, user) as conn:
        total = conn.execute('SELECT count(*) FROM applications' + condition, args).fetchone()[0]
        rows = records(conn.execute('SELECT * FROM applications' + condition + ' ORDER BY changed_attributes DESC, APP_ID LIMIT ? OFFSET ?', args + [limit, (page - 1) * limit]))
    return {'items': rows, 'total': total, 'page': page, 'limit': limit}

@secured.get('/api/runs/{run_id}/applications/{app_id}')
def application(run_id: str, app_id: str, user: dict = Depends(current_user), changed: bool = False, path: str = '', search: str = '',
                page: int = Query(1, ge=1), limit: int = Query(100, ge=1, le=300)):
    run = require_run(run_id, user, True)
    with database(run_id, user) as conn:
        segments = records(conn.execute('SELECT * FROM applications WHERE APP_ID=?', [app_id]))
        if not segments:
            raise HTTPException(404, 'Заявка не найдена')
        sql, args = comparison_filters(path, '', changed, [app_id])
        if search:
            sql += ' AND (contains(lower(PATH), lower(?)) OR contains(lower(ARRAY_KEY), lower(?)))'
            args.extend([search, search])
        total = conn.execute('SELECT count(*) FROM comparison' + sql, args).fetchone()[0]
        rows = records(conn.execute('SELECT * FROM comparison' + sql + ' ORDER BY DIFF_FLAG DESC, PATH, ARRAY_KEY LIMIT ? OFFSET ?', args + [limit, (page - 1) * limit]))
    return {'segment': segments[0], 'items': rows, 'total': total, 'page': page}

@secured.get('/api/runs/{run_id}/applications/{app_id}/source')
def application_source(run_id: str, app_id: str, user: dict = Depends(current_user)):
    run = require_run(run_id, user, True)
    # Validate against the mart before using an ID as a file name.
    with database(run_id, user) as conn:
        if not conn.execute('SELECT 1 FROM applications WHERE APP_ID=?', [app_id]).fetchone():
            raise HTTPException(404, 'Заявка не найдена')
    folder = paths(run['dataset'], run.get('dataset_owner_id') or run['owner_id'])
    from .dataset_storage import read_raw
    return {source: json.loads(read_raw(folder,sub,app_id))
            for source, sub in [('BEFORE', 'BEFORE_MASTER'), ('AFTER', 'AFTER_BETA')]}

@secured.get('/api/runs/{run_id}/pivot')
def pivot(run_id: str, user: dict = Depends(current_user), group: Literal['PATH', 'PREFIX', 'CLASS', 'KEY', 'CHANGE_TYPE'] = 'PATH',
          changed: bool = True, search: str = '', page: int = Query(1, ge=1), limit: int = Query(30, ge=1, le=100)):
    dimension = '"' + group + '"'
    clauses, args = [], []
    if changed:
        clauses.append('DIFF_FLAG=1')
    if search:
        clauses.append(f'contains(lower({dimension}), lower(?))')
        args.append(search)
    predicate = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    # Counts are computed per unique application; summing facts across attributes would double-count.
    with database(run_id, user) as conn:
        total = conn.execute(f'SELECT count(DISTINCT {dimension}) FROM comparison' + predicate, args).fetchone()[0]
        rows = records(conn.execute(f'''SELECT {dimension} dimension, count(DISTINCT APP_ID)::INTEGER apps,
            count(*)::INTEGER attributes,
            count(*) FILTER(WHERE CHANGE_TYPE='VALUE_CHANGED')::INTEGER value_changed,
            count(*) FILTER(WHERE CHANGE_TYPE='ATTRIBUTE_NEW')::INTEGER attribute_new,
            count(*) FILTER(WHERE CHANGE_TYPE='ATTRIBUTE_LOST')::INTEGER attribute_lost,
            count(*) FILTER(WHERE CHANGE_TYPE='SAME')::INTEGER same
            FROM comparison{predicate} GROUP BY {dimension} ORDER BY apps DESC, dimension LIMIT ? OFFSET ?''', args + [limit, (page - 1) * limit]))
    return {'items': rows, 'total': total, 'page': page, 'group': group}

@secured.get('/api/pivot-schema')
def pivot_schema():
    return {'fields':pivot_engine.FIELDS,'metrics':pivot_engine.METRICS}

@secured.post('/api/runs/{run_id}/pivot/query')
def pivot_matrix(run_id:str,body:pivot_engine.PivotQuery,user:dict=Depends(current_user)):
    with database(run_id,user) as conn:
        return pivot_engine.matrix(conn,body)

@secured.post('/api/runs/{run_id}/pivot/applications')
def pivot_applications(run_id:str,body:pivot_engine.DrillQuery,user:dict=Depends(current_user)):
    with database(run_id,user) as conn:
        return pivot_engine.drill(conn,body)

@secured.post('/api/runs/{run_id}/pivot/attributes')
def pivot_attributes(run_id:str,body:pivot_engine.AttributeQuery,user:dict=Depends(current_user)):
    with database(run_id,user) as conn:return pivot_engine.attributes(conn,body)

@secured.get('/api/runs/{run_id}/report.html', response_class=HTMLResponse)
def export_report(run_id: str, request: Request, user: dict = Depends(current_user)):
    run = require_run(run_id, user, True)
    origin = str(request.base_url).rstrip('/')
    link = f'{origin}/?run={run_id}&tab=pivot'
    rows = ''
    for i, row in enumerate(run['checks']):
        url = f'{origin}/?run={run_id}&tab=applications&check={i}'
        cells = ''
        for key in ['LABEL', 'SOURCE', 'ARGUMENT', 'CNT', 'TOTAL', 'METRIC', 'RESULT', 'CONFIDENCE']:
            value = escape(str(row.get(key, '')))
            if key == 'RESULT':
                tone = 'pass' if row.get(key) == 'PASS' else 'fail'
                value = f'<span class="verdict {tone}">{value}</span>'
            cells += f'<td>{value}</td>'
        version = f"{row.get('CHECK_NAME', '')} · v{row['CHECK_VERSION']}" if row.get('CHECK_VERSION') else 'Историческая'
        rows += f'<tr class="{"low-confidence" if row.get("CONFIDENCE")=="LOW" else ""}">{cells}<td>{escape(version)}</td><td><a href="{escape(url)}">{row.get("MEMBERSHIP",{}).get("count",len(row["APP_IDS"]))} из {row["CNT"]}</a></td></tr>'
    manifest = ''
    for spec in run.get('check_plan') or []:
        from urllib.parse import quote
        snapshot_url = f"{origin}/api/runs/{run_id}/check-plan/{quote(spec['key'], safe='')}"
        manifest += (f"<li><b>{escape(spec['name'])} · v{spec['version']}</b> · {escape(spec['kind'])}"
                     f"<br>SHA-256: {escape(spec['sha256'])}<pre>{escape(json.dumps(spec['params'], ensure_ascii=False, indent=2))}</pre>"
                     + (f'<a href="{escape(snapshot_url)}">Снимок кода и параметры</a></li>' if access.can_manage(run,user) else '</li>'))
    manifest = ('<details><summary>Код и параметры проверок</summary><ul>'+manifest+'</ul></details>') if manifest else '<p>Сохранённых снимков проверок нет.</p>'
    summary = run['summary']
    created = datetime.fromisoformat(run['created_at']).strftime('%d.%m.%Y · %H:%M')
    body = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(run['name'])}</title>
    <style>
    *{{box-sizing:border-box}}body{{font:15px/1.6 system-ui,sans-serif;color:#26313b;margin:0;background:#f7f8f9}}
    main{{max-width:1440px;margin:auto;padding:40px 28px}}header{{margin-bottom:28px}}.brand{{color:#f34b28;font-weight:700;font-size:20px}}
    h1{{font-size:30px;line-height:1.25;margin:12px 0;overflow-wrap:anywhere}}h2{{font-size:19px;margin:0;padding:20px 24px;border-bottom:1px solid #e7ebef}}
    .muted,footer{{color:#667483;font-size:13px}}.metrics{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:24px 0}}
    .metric,.panel,details{{background:#fff;border:1px solid #e2e7eb;border-radius:12px}}.metric{{padding:20px 24px}}.metric span{{display:block;color:#667483;font-size:13px}}.metric strong{{font-size:30px;font-weight:600}}
    a{{color:#c93c1a;text-decoration:none}}a:hover{{text-decoration:underline}}a:focus-visible,summary:focus-visible{{outline:3px solid #ffad97;outline-offset:3px}}
    .action{{display:inline-block;background:#ff5028;color:white;border-radius:8px;padding:12px 18px;font-weight:600}}
    .panel{{overflow:hidden;margin:28px 0}}.table-scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}
    td,th{{padding:14px 18px;border-bottom:1px solid #e8edf0;text-align:left;vertical-align:top}}th{{background:#fafbfc;color:#667483;font-size:12px;font-weight:500}}
    td:first-child{{min-width:200px}}td:nth-child(3){{min-width:160px;overflow-wrap:anywhere}}tbody tr:last-child td{{border-bottom:0}}
    .verdict{{display:inline-block;padding:3px 8px;border-radius:5px;font-size:12px;font-weight:600}}.verdict.pass{{color:#247759;background:#edf7f1}}.verdict.fail{{color:#c64b39;background:#fff0ec}}
    .low-confidence{{color:#657586;background:#fafbfc}}details{{margin:20px 0}}summary{{padding:16px 20px;cursor:pointer;font-weight:500}}
    details[open]>summary{{border-bottom:1px solid #e7ebef}}details ul{{padding:0 24px;list-style:none}}details li{{padding:16px 0;border-bottom:1px solid #e8edf0;overflow-wrap:anywhere}}details li:last-child{{border:0}}
    pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.7 ui-monospace,monospace;background:#f7f9fa;padding:12px;border-radius:8px}}
    footer{{margin-top:24px}}.empty{{padding:24px;color:#667483}}
    @media(max-width:650px){{main{{padding:24px 16px}}h1{{font-size:25px}}.metrics{{grid-template-columns:1fr;gap:10px}}.metric{{padding:14px 18px;display:flex;align-items:center;justify-content:space-between;gap:16px}}.metric strong{{font-size:25px}}}}
    @media print{{body{{background:#fff}}main{{padding:0;max-width:none}}.action{{background:none;color:#26313b;padding:0}}.metrics{{gap:8px}}.metric{{padding:12px}}table{{font-size:10px}}td,th{{padding:7px}}td:first-child,td:nth-child(3){{min-width:0}}.table-scroll{{overflow:visible}}.panel{{border-radius:0}}}}
    </style></head><body><main>
    <header><div class="brand">баланс</div><h1>{escape(run['name'])}</h1><div class="muted">{escape(created)}</div></header>
    <div class="metrics"><div class="metric"><span>Всего заявок</span><strong>{format(summary['total_apps'], ',').replace(',', ' ')}</strong></div><div class="metric"><span>С различиями</span><strong>{format(summary['changed_apps'], ',').replace(',', ' ')}</strong></div><div class="metric"><span>Смена решения</span><strong>{format(summary['decision_changed'], ',').replace(',', ' ')}</strong></div></div>
    <a class="action" href="{escape(link)}">Открыть интерактивный отчёт →</a>
    <section class="panel"><h2>Результаты проверок</h2><div class="table-scroll"><table><thead><tr><th>Проверка</th><th>Срез</th><th>Аргумент</th><th>Количество</th><th>Всего</th><th>Метрика</th><th>Результат</th><th>Уверенность</th><th>Версия</th><th>Заявки</th></tr></thead><tbody>{rows}</tbody></table></div>{'<p class="empty">Нет результатов проверок.</p>' if not rows else ''}</section>
    {manifest}
    <footer>Для перехода к сводной таблице и заявкам войдите в аккаунт с доступом к анализу.</footer></main></body></html>'''
    return HTMLResponse(body, headers={'Content-Disposition': f'attachment; filename="report-{run_id}.html"'})

@secured.get('/api/ollama')
def ollama_status(user: dict = Depends(require_writer)):
    settings=admin.ai_settings()
    return {**admin.connection_status(settings['base_url'],settings['model']),'url':settings['base_url']}

@secured.post('/api/runs/{run_id}/ai', status_code=202)
def request_ai(run_id: str, user: dict = Depends(require_writer)):
    run=require_run(run_id, user, True, write=True)
    from .ai import build_context
    settings=admin.ai_settings()
    try:
        job = queueing.submit(user['id'], run_id, 'ai', params={'ollama_url':settings['base_url'], 'model':settings['model'], 'context':build_context(run), 'prompt_version':2, 'report_revision':run.get('report_revision',1), 'requested_by':user['id']})
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {'status': 'queued', 'job_id': job['id']}

class PythonCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(min_length=1, max_length=50000)
    publish: bool = False
    name: str = Field(default='Пользовательская проверка',min_length=1,max_length=120)
    params: dict = Field(default_factory=dict)
    timeout: int = Field(default=20, ge=1, le=60)
    memory_mb: int = Field(default=1024, ge=512, le=2048)

@secured.get('/api/runtime')
def runtime(user: dict = Depends(require_writer)):
    from .sandbox import availability
    return {**queueing.service_status(), 'sandbox': availability()}

@secured.post('/api/runs/{run_id}/python-checks', status_code=202)
def python_check(run_id: str, body: PythonCheck, user: dict = Depends(require_writer)):
    return enqueue_check(run_id, body, user)

def require_sandbox():
    from .sandbox import availability
    ready = availability()
    if not ready['available']:
        raise HTTPException(503, ready['error'])

def enqueue_check(run_id, body, user, check_ref=None):
    require_run(run_id, user, True, write=True)
    code_validation.require_valid('checks',body.code)
    try:
        library.valid_params(body.params)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    require_sandbox()
    return public_job(queueing.submit(user['id'], run_id, 'check', **body.model_dump(exclude={'publish','name'}), check_ref={**(check_ref or {}), **({'publish':True,'name':body.name} if body.publish else {})}))

class TestVersion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str = Field(min_length=1, max_length=100)

@secured.post('/api/check-library/{check_id}/versions/{number}/test', status_code=202)
def test_version(check_id: str, number: int, body: TestVersion, user: dict = Depends(require_writer)):
    version = library.version_detail(check_id, number, user)
    return enqueue_check(body.run_id, PythonCheck(**{k:version[k] for k in ('code','params','timeout','memory_mb')}),
                         user, library.public_spec(library.version_spec(version)))

def public_job(job, detail=True):
    hidden = {'owner_id', 'lease', 'heartbeat', 'enqueued_at'}
    if not detail:
        hidden |= {'code', 'params', 'result'}
    return {k: v for k, v in job.items() if k not in hidden}

def require_job(job_id, user):
    job = store.get_job(job_id)
    if not job or job['owner_id'] != user['id']:
        raise HTTPException(404, 'Задача не найдена')
    return job

@secured.get('/api/jobs')
def jobs(run_id: str | None = None, user: dict = Depends(require_writer)):
    return [public_job(j, False) for j in store.list_jobs(owner_id=user['id'], run_id=run_id, detail=False)]

@secured.get('/api/jobs/{job_id}')
def job_detail(job_id: str, user: dict = Depends(require_writer)):
    from .settings import JOBS
    job = require_job(job_id, user)
    logs = ''
    if job.get('lease'):
        path = JOBS / job_id / job['lease'] / 'process.log'
        if path.exists():
            with path.open('rb') as f:
                f.seek(max(0, path.stat().st_size - 16000))
                logs = f.read(16000).decode('utf-8', errors='replace')
    return {**public_job(job), 'logs': logs}

@secured.post('/api/jobs/{job_id}/cancel')
def cancel_job(job_id: str, user: dict = Depends(require_writer)):
    require_job(job_id, user)
    return public_job(queueing.cancel(job_id))

@secured.post('/api/jobs/{job_id}/retry', status_code=202)
def retry_job(job_id: str, user: dict = Depends(require_writer)):
    job = require_job(job_id, user)
    if job['status'] not in ('failed', 'timed_out', 'cancelled'):
        raise HTTPException(409, 'Повтор доступен для отменённых и неуспешных задач')
    if job['kind'] == 'import':
        return public_job(imports.retry(job, user))
    run = require_run(job['run_id'], user, write=True)
    if job['kind'] == 'run':
        body = NewRun(dataset=run['dataset'], name=run['name'], config=run['config'], auto_ai=run.get('auto_ai',False))
        try:
            paths(body.dataset, run.get('dataset_owner_id') or user['id'])
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        plan = run.get('check_plan')
        if plan is None:
            plan = library.resolve_plan(user, None, [], body.config.model_dump())
        new = enqueue_run(body, user, plan, run.get('dataset_owner_id'),run['id'],run.get('procedure_plan'))
        return public_job(store.list_jobs(run_id=new['id'])[0])
    if job['kind'] == 'check':
        return enqueue_check(run['id'], PythonCheck(code=job['code'], params=job['params'], timeout=job['timeout'], memory_mb=job['memory_mb']), user, job.get('check_ref'))
    if job['params'].get('chat'):
        response = ai_chat.send_message(run['id'],ai_chat.Message(message=job['params']['message'],request_id=uuid.uuid4()),user)
        return public_job(store.get_job(response['job_id']))
    response = request_ai(run['id'], user)
    return public_job(store.get_job(response['job_id']))

app.include_router(secured)

# A production build is served from the same origin as the API.
dist = FRONTEND_DIST
if (dist / 'assets').is_dir():
    app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')
if (dist / 'examples').is_dir():
    app.mount('/examples', StaticFiles(directory=dist / 'examples'), name='examples')
if (dist / 'fonts').is_dir():
    app.mount('/fonts', StaticFiles(directory=dist / 'fonts'), name='fonts')

@app.get('/', include_in_schema=False)
def index():
    if (dist / 'index.html').exists():
        return FileResponse(dist / 'index.html')
    return HTMLResponse('<p>Frontend ещё не собран. Выполните scripts/setup.sh или откройте dev-сервер на порту 5173.</p>')
