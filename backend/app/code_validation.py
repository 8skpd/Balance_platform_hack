"""Bounded static contract checks. Never import or execute user source in the API."""
import ast
import builtins
import inspect
import io
import tokenize
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .auth import require_writer
from .pipeline_data import NORMAL_COLUMNS, COMPARE_COLUMNS, FACT_FIELDS, SEGMENT_FIELDS

Stage = Literal['normalization', 'comparison', 'aggregation', 'checks']
router = APIRouter(prefix='/api/code', dependencies=[Depends(require_writer)])

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stage: Stage
    code: str = Field(max_length=100000)

@router.post('/validate')
def validate(body: Input):
    return analyze(body.stage, body.code)

def require_valid(stage, code):
    result = analyze(stage, code)
    if not result['valid']:
        errors = [f"Строка {d['line']}: {d['message']}" for d in result['diagnostics'] if d['severity']=='error']
        raise HTTPException(422, 'Контракт Python: ' + '; '.join(errors[:8]))

def signature_fix(stage, key, function, code):
    """Offer only an unambiguous addition of missing trailing arguments.

    Never rename existing bindings, remove parameters, or rewrite the body.
    Offsets are UTF-16 code units for CodeMirror, not AST's UTF-8 columns.
    """
    if not isinstance(function, ast.FunctionDef) or function.decorator_list:
        return None
    args = function.args
    if args.posonlyargs or args.vararg or args.kwarg or args.kwonlyargs or args.defaults:
        return None
    expected = (['app_id', 'before', 'after', 'config', 'params'] if key == 'segments' else
                ['document', 'app_id', 'source', 'config', 'params'] if stage == 'normalization' else
                ['mart', 'params'] if stage == 'checks' else ['data', 'params'])
    existing = [a.arg for a in args.args]
    if len(existing) >= len(expected):
        return None
    # A known contract name in another position is ambiguous, e.g. run(params).
    if any(name in expected and expected[i] != name for i, name in enumerate(existing)):
        return None
    missing = expected[len(existing):]
    if any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id in missing
           for n in ast.walk(function)):
        return None
    tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
    start = next((i for i, t in enumerate(tokens) if t.string == 'def' and t.start[0] == function.lineno), None)
    if start is None:
        return None
    opening = next(i for i in range(start, len(tokens)) if tokens[i].string == '(')
    depth = 0
    for i in range(opening, len(tokens)):
        token = tokens[i]
        if token.type != tokenize.OP:
            continue
        if token.string in '([{': depth += 1
        if token.string in ')]}': depth -= 1
        if depth == 0:
            # Restrict the automatic edit to simple single-line headers.
            if token.start[0] != tokens[opening].start[0]:
                return None
            lines = code.splitlines(keepends=True)
            offset = sum(len(line) for line in lines[:token.start[0]-1]) + token.start[1]
            prefix = code[:offset].rstrip()
            insert = (', ' if existing and not prefix.endswith(',') else ' ' if existing else '') + ', '.join(missing)
            utf16 = len(code[:offset].encode('utf-16-le')) // 2
            return {'from': utf16, 'to': utf16, 'insert': insert, 'expected': '',
                    'title': 'Добавить параметры',
                    'preview': f"{function.name}({', '.join([*existing, *missing])})"}
    return None

@lru_cache(maxsize=128)
def analyze(stage, code):
    diagnostics = []
    def add(message, node=None, severity='error', line=None, column=None):
        diagnostics.append({'message': message, 'severity': severity,
            'line': line or getattr(node, 'lineno', 1),
            'column': column or getattr(node, 'col_offset', 0)+1})
    def result():
        return {'valid': not any(d['severity']=='error' for d in diagnostics),
                'diagnostics': diagnostics, 'scope': 'static'}
    try:
        tree = ast.parse(code)
        # compile catches errors accepted by ast.parse: return outside a function,
        # duplicate arguments, nonlocal without binding, etc. It does not run code.
        compile(tree, '<contract>', 'exec')
    except (SyntaxError, ValueError, RecursionError) as exc:
        add('Ошибка Python: '+getattr(exc, 'msg', str(exc)), line=getattr(exc, 'lineno', 1), column=getattr(exc, 'offset', 1))
        return result()
    if sum(1 for _ in ast.walk(tree)) > 20000:
        add('Слишком сложный код для предварительной проверки. Разделите процедуру.')
        return result()
    bindings = {}
    imported = set()
    for node in tree.body:
        if isinstance(node,(ast.Import,ast.ImportFrom)):
            imported.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bindings[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name): bindings[target.id] = node.value
    def resolve(node, seen=()):
        if isinstance(node, ast.Name) and node.id in bindings and node.id not in seen:
            return resolve(bindings[node.id], (*seen, node.id))
        return node
    export = 'CHECK' if stage=='checks' else 'PROCEDURE'
    if export not in bindings:
        add(f'Добавьте {export} = {{"entrypoint": run}}'+(' и экспорт segments.' if stage=='normalization' else '.'))
        return result()
    exported = resolve(bindings[export])
    if not isinstance(exported, ast.Dict):
        if isinstance(exported, (ast.Constant, ast.List, ast.Tuple, ast.Set)):
            add(f'{export} должен быть словарём.', exported)
        else:
            add(f'{export} создаётся динамически: его состав будет проверен при выполнении.', exported, 'warning')
        return result()
    entries = {k.value:v for k,v in zip(exported.keys, exported.values) if isinstance(k, ast.Constant) and isinstance(k.value,str)}
    dynamic = any(k is None or not isinstance(k, ast.Constant) for k in exported.keys)
    if 'params' in entries and isinstance(resolve(entries['params']), (ast.Constant,ast.List,ast.Tuple,ast.Set)):
        add(f'{export}["params"] должен быть словарём.',entries['params'])
    for key, argc in [('entrypoint', 5 if stage=='normalization' else 2)] + ([('segments', 5)] if stage=='normalization' else []):
        if key not in entries:
            add(f'В {export} отсутствует «{key}».', exported, 'warning' if dynamic else 'error')
            continue
        function = resolve(entries[key])
        if isinstance(function,ast.Name) and function.id not in bindings and function.id not in imported and not hasattr(builtins,function.id):
            add(f'Функция «{function.id}» не определена. Проверьте имя в {export}["{key}"].',function)
            continue
        if isinstance(function, ast.AsyncFunctionDef):
            add(f'{key} должен быть обычной функцией, без async.', function)
            continue
        if not isinstance(function, (ast.FunctionDef, ast.Lambda)):
            if isinstance(function, (ast.Constant, ast.Dict, ast.List, ast.Tuple, ast.Set)):
                add(f'{export}["{key}"] должен ссылаться на функцию.', function)
            else:
                add(f'Сигнатура {key} определяется динамически и будет проверена при выполнении.', entries[key], 'warning')
            continue
        if getattr(function, 'decorator_list', []):
            add(f'Декораторы могут изменить сигнатуру {key}; проверьте результат при выполнении.', function, 'warning')
        else:
            args = function.args
            parameters = []
            positional = [*args.posonlyargs, *args.args]
            for i,a in enumerate(positional):
                parameters.append(inspect.Parameter(a.arg, inspect.Parameter.POSITIONAL_ONLY if i<len(args.posonlyargs) else inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    default=None if i>=len(positional)-len(args.defaults) else inspect.Parameter.empty))
            if args.vararg: parameters.append(inspect.Parameter(args.vararg.arg, inspect.Parameter.VAR_POSITIONAL))
            for a,default in zip(args.kwonlyargs,args.kw_defaults):
                parameters.append(inspect.Parameter(a.arg,inspect.Parameter.KEYWORD_ONLY,default=None if default is not None else inspect.Parameter.empty))
            if args.kwarg: parameters.append(inspect.Parameter(args.kwarg.arg,inspect.Parameter.VAR_KEYWORD))
            try: inspect.Signature(parameters).bind(*[object() for _ in range(argc)])
            except TypeError:
                add(f'{key} вызывается с {argc} позиционными аргументами. Исправьте сигнатуру функции.', function)
                fix = signature_fix(stage, key, function, code)
                if fix:
                    diagnostics[-1]['fix'] = fix
        # Only inspect literal results directly returned/yielded by this entrypoint.
        # Do not infer dictionaries assembled or modified by arbitrary user code.
        outputs = []
        def visit(node):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)): return
            if isinstance(node, (ast.Return, ast.Yield)): outputs.append(node)
            for child in ast.iter_child_nodes(node): visit(child)
        if isinstance(function, ast.Lambda): outputs = [ast.Return(value=function.body)]
        else:
            for node in function.body: visit(node)
        def own_nodes(node):
            yield node
            for child in ast.iter_child_nodes(node):
                if not isinstance(child,(ast.FunctionDef,ast.AsyncFunctionDef,ast.Lambda,ast.ClassDef)):
                    yield from own_nodes(child)
        nodes=list(own_nodes(function))
        generator = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in nodes)
        if generator and (key=='segments' or stage=='checks'):
            add('segments должен возвращать словарь, без yield.' if key=='segments' else 'Проверка должна возвращать список строк через return, без yield.',function)
        if not outputs and not generator and not any(isinstance(n,ast.Raise) for n in nodes):
            add('Функция не возвращает результат. Добавьте return или yield согласно контракту.',function)
        for output in outputs:
            if generator and isinstance(output, ast.Return): continue
            value = output.value
            if value is None or isinstance(value, ast.Constant):
                add('Ожидается словарь разрезов.' if key=='segments' else 'Ожидается коллекция строк, а не скаляр или None.', value or function)
                continue
            rows = []
            if key=='segments' or isinstance(output, ast.Yield): rows=[value]
            elif isinstance(value, (ast.List,ast.Tuple)): rows=value.elts
            elif isinstance(value, ast.Dict):
                add('Верните коллекцию строк: [row] или yield row, а не отдельный словарь.',value)
            for row in rows:
                if isinstance(row, ast.Constant):
                    add('Каждая строка результата должна быть словарём.',row)
                if not isinstance(row, ast.Dict): continue
                fields={k.value:v for k,v in zip(row.keys,row.values) if isinstance(k,ast.Constant) and isinstance(k.value,str)}
                required = (['APP_ID',*SEGMENT_FIELDS,'DECISION_CHANGED'] if key=='segments' else
                    NORMAL_COLUMNS if stage=='normalization' else COMPARE_COLUMNS if stage=='comparison' else
                    [*FACT_FIELDS,'CNT','APP_IDS'] if stage=='aggregation' else
                    ['LABEL','SOURCE','ARGUMENT','CNT','TOTAL','METRIC','RESULT','CONFIDENCE','APP_IDS'])
                if all(isinstance(k,ast.Constant) for k in row.keys):
                    missing = sorted(set(required)-fields.keys())
                    if missing: add('В строке результата отсутствуют поля: '+', '.join(missing)+'.', row)
                enums={'SOURCE':('BEFORE','AFTER'),'RESULT':('PASS','FAIL'),'CONFIDENCE':('HIGH','LOW'),
                    'VALUE_TYPE':('string','number','boolean','null'),
                    'CHANGE_TYPE':('SAME','VALUE_CHANGED','ATTRIBUTE_NEW','ATTRIBUTE_LOST'),
                    'DIFF_FLAG':(0,1),'DECISION_CHANGED':(0,1)}
                for field,allowed in enums.items():
                    val=fields.get(field)
                    if isinstance(val,ast.Constant) and val.value not in allowed:
                        add(f'{field}: допустимы '+', '.join(map(str,allowed))+'.',val)
                for field,val in fields.items():
                    if not isinstance(val,ast.Constant): continue
                    scalar=val.value
                    if field in ('CNT','TOTAL','DIFF_FLAG','DECISION_CHANGED') and (type(scalar) is not int or scalar<0):
                        add(f'{field}: требуется неотрицательное целое число.',val)
                    elif field=='METRIC' and type(scalar) not in (int,float):
                        add('METRIC: требуется число.',val)
                    elif stage=='normalization' and key!='segments' and field in NORMAL_COLUMNS and not isinstance(scalar,str):
                        add(f'{field}: требуется строка.',val)
                    elif field in ('LABEL','ARGUMENT','APP_ID','PATH','ARRAY_KEY') and not isinstance(scalar,str):
                        add(f'{field}: требуется строка.',val)
                ids=fields.get('APP_IDS')
                if isinstance(ids,ast.Constant):add('APP_IDS: требуется список строк.',ids)
                if isinstance(ids,ast.List):
                    for item in ids.elts:
                        if isinstance(item,ast.Constant) and not isinstance(item.value,str):add('APP_IDS: идентификаторы должны быть строками.',item)
    return result()
