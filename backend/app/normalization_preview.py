"""Bounded, read-only preview using the same normalizer as a full report."""
from fastapi import HTTPException

from .imports import strict_json, APP_ID
from .normalizer import normalize

MAX_BYTES = 1024 ** 2
MAX_NODES = 20000
DISPLAY_ROWS = 100

def preview(folder, config, app_id=None):
    from .dataset_storage import inventory,sample_ids,read_raw
    info=inventory(folder);ids=sample_ids(folder)
    if not ids:raise HTTPException(422, 'В датасете нет пар заявок')
    selected=app_id or ids[0]
    if not APP_ID.fullmatch(selected):raise HTTPException(404, 'Пара заявок не найдена')
    rows = {}; errors = []; arrays = set()
    for source, side in [('BEFORE','BEFORE_MASTER'),('AFTER','AFTER_BETA')]:
        try:raw=read_raw(folder,side,selected,MAX_BYTES)
        except FileNotFoundError:raise HTTPException(404,'Пара заявок не найдена')
        except ValueError as exc:raise HTTPException(422,str(exc))
        except OverflowError:raise HTTPException(413,'Для предпросмотра выберите пару с JSON до 1 МиБ на файл')
        try:
            doc, found = strict_json(raw, source)
            stack = [doc]; count = 0
            while stack:
                item = stack.pop(); count += 1
                if count > MAX_NODES:
                    raise HTTPException(413, 'Предпросмотр ограничен 20 000 узлами JSON на файл; используйте полный расчёт')
                if isinstance(item, dict): stack.extend(item.values())
                elif isinstance(item, list): stack.extend(item)
            arrays.update(found)
            rows[source] = normalize(doc, selected, source, config, max_output_chars=4 * 1024**2)
        except (ValueError, RecursionError) as exc:
            errors.append({'source': source, 'message': str(exc)[:2000]})
    result = {'app_id': selected, 'sample_ids': ids[:20], 'pairs': info['pairs'], 'array_paths': sorted(arrays)[:200],
              'errors': errors, 'valid': not errors, 'items': [], 'row_limit': DISPLAY_ROWS}
    if errors:
        return result
    maps = {side: {(r['PATH'], r['ARRAY_KEY']):r for r in values} for side, values in rows.items()}
    items=[]; changed=0
    for key in sorted(maps['BEFORE'].keys() | maps['AFTER'].keys()):
        b, a = maps['BEFORE'].get(key), maps['AFTER'].get(key)
        kind = ('ATTRIBUTE_NEW' if b is None else 'ATTRIBUTE_LOST' if a is None else
                'SAME' if (b['VALUE_TYPE'], b['VALUE']) == (a['VALUE_TYPE'], a['VALUE']) else 'VALUE_CHANGED')
        changed += kind != 'SAME'
        def value(row):
            if row is None: return None
            return {'value': row['VALUE'][:240], 'type': row['VALUE_TYPE'], 'adjusted': row['ADJUSTED_VALUE'][:240],
                    'truncated': len(row['VALUE']) > 240 or len(row['ADJUSTED_VALUE']) > 240}
        items.append({'path': key[0], 'array_key': key[1], 'change': kind, 'before': value(b), 'after': value(a)})
    items.sort(key=lambda item: item['change'] == 'SAME')
    result.update(items=items[:DISPLAY_ROWS], total=len(items), changed=changed,
                  before_count=len(rows['BEFORE']), after_count=len(rows['AFTER']), truncated=len(items)>DISPLAY_ROWS)
    return result
