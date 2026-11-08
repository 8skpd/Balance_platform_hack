"""Configurable parser. Array identity is explicit; value type and presence are preserved."""
import json
import math
import re
from decimal import Decimal
from urllib.parse import quote

DEFAULT_CONFIG = {
    'exclude_paths': ['appData.request_id', 'appData.createDateTime'],
    'arrays': {
        'decisionResult.available_offers': {'mode': 'key', 'keys': ['product_id', 'type']},
        'decisionResult.product_trials': {'mode': 'key', 'keys': ['product_id']},
        'decisionResult.product_trials.rules': {'mode': 'key', 'keys': ['name']},
        'decisionResult.product_trials.scoring.scoreModels': {'mode': 'key', 'keys': ['modelName']},
        'decisionResult.scoring': {'mode': 'key', 'keys': ['modelName']},
        'decisionResult.scoring.value': {'mode': 'index'},
        'ext_data': {'mode': 'key', 'keys': ['body.action']},
        'product_catalog': {'mode': 'key', 'keys': ['mfo_id', 'product_id']},
        'tariff_matrix': {'mode': 'key', 'keys': ['mfo_id', 'product_id']},
    },
    'buckets': {},
    'layout': {},
    'check_params': {'max_drop_rel': 0.1, 'low_confidence_cnt': 5, 'app_ids_limit': 100},
}

def value_text(value):
    if value is None:
        return 'NULL', 'null'
    if isinstance(value, bool):
        return ('true' if value else 'false'), 'boolean'
    if isinstance(value, int):
        return str(value), 'number'
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('Неконечное числовое значение')
        # Formatting is exact; Decimal.normalize() would round at context precision.
        text = format(Decimal(str(value)), 'f')
        if '.' in text:
            text = text.rstrip('0').rstrip('.')
        return ('0' if value == 0 else text), 'number'
    if isinstance(value, str):
        return value, 'string'
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')), 'container'

def nested(obj, path):
    for part in path.split('.'):
        if not isinstance(obj, dict) or part not in obj:
            raise ValueError(f'Нет обязательного ключа массива: {path}')
        obj = obj[part]
    if obj is None or isinstance(obj, (dict, list)):
        raise ValueError(f'Некорректный ключ массива: {path}')
    return obj

def key_text(value):
    text, kind = value_text(value)
    # Preserve documented human-readable keys, escape delimiters and type ambiguity.
    return ('s:' if kind == 'string' and (re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?', text) or text in ('true', 'false', 'NULL')) else '') + quote(text, safe='@_.-')

def normalize(document, app_id, source, config, *, max_output_chars=None):
    output = []
    unique = set()
    exclude = config['exclude_paths']
    output_chars = 0

    def walk(value, parts, keys):
        nonlocal output_chars
        path = '.'.join(parts)
        if any(path == p or path.startswith(p + '.') for p in exclude):
            return
        if isinstance(value, dict) and value:
            for key, child in value.items():
                if '.' in key:
                    raise ValueError(f'Точка в имени поля требует расширения контракта: {key}')
                walk(child, parts + [key], keys)
            return
        if isinstance(value, list) and value:
            rule = config['arrays'].get(path)
            if not rule:
                raise ValueError(f'Не задан режим сравнения массива: {path}')
            seen = set()
            for index, child in enumerate(value):
                if rule['mode'] == 'index':
                    key = str(index)
                elif rule['mode'] == 'value':
                    if isinstance(child, (dict, list)):
                        raise ValueError(f'Режим value требует скаляры: {path}')
                    key = key_text(child)
                else:
                    key = '|'.join(key_text(nested(child, p)) for p in rule['keys'])
                if key in seen:
                    if rule['mode'] == 'value':  # set semantics, explicitly documented
                        continue
                    raise ValueError(f'Повтор ключа массива {path}: {key}')
                seen.add(key)
                walk(child, parts, keys + [key])
            return
        # Empty arrays/objects are not leaf attributes; source JSON retains them.
        if isinstance(value, (dict, list)):
            return
        text, kind = value_text(value)
        adjusted = text
        boundaries = config.get('buckets', {}).get(path, [])
        if kind == 'number' and boundaries:
            low = '-∞'
            for high in boundaries:
                if value <= high:
                    adjusted = f'({low}; {high}]'
                    break
                low = high
            else:
                adjusted = f'({low}; +∞)'
        array_key = '|'.join(keys)
        if max_output_chars is not None:
            output_chars += len(path) + len(array_key) + len(text) + len(adjusted)
            if output_chars > max_output_chars:
                raise ValueError('Результат нормализации слишком велик для предпросмотра; используйте полный расчёт')
        identity = (path, array_key)
        if identity in unique:
            raise ValueError(f'Дублирующийся атрибут: {identity}')
        unique.add(identity)
        scalar_set = config['arrays'].get(path, {}).get('mode') == 'value' and bool(keys)
        layout = {'PREFIX': parts[0] if parts else '',
                  'CLASS': parts[1] if len(parts) > 2 or scalar_set and len(parts)>1 else '',
                  'SUFFIX': '.'.join(parts[2:-1]), 'KEY': '' if scalar_set else parts[-1] if parts else ''}
        layout.update(config.get('layout', {}).get(path, {}))
        output.append({'APP_ID': app_id, 'SOURCE': source, 'PATH': path, **layout,
            'ARRAY_KEY': array_key, 'VALUE': text, 'VALUE_TYPE': kind, 'ADJUSTED_VALUE': adjusted})

    walk(document, [], [])
    return output

def segments(app_id, before, after):
    row = {'APP_ID': app_id}
    products, partner_types, routes = set(), set(), set()
    contexts = {}
    for source, doc in [('BEFORE', before), ('AFTER', after)]:
        decision = doc.get('decisionResult', {})
        trials = decision.get('product_trials', [])
        row['DECISION_' + source] = decision.get('decision')
        offers = sorted(decision.get('available_offers', []), key=lambda x: x.get('rank', 999999))
        row['TOP_OFFER_' + source] = str(offers[0]['product_id']) if offers else ''
        # Stable primary product: smallest product_id, independent of array ordering.
        primary = min(trials, key=lambda t: str(t['product_id'])) if trials else {}
        row['PD_MODEL_' + source] = primary.get('scoring', {}).get('pd', {}).get('modelName', '')
        row['CUTOFF_' + source] = primary.get('cutOff', {}).get('name', '')
        row['REJECT_REASON_' + source] = primary.get('rejectReason') or ''
        score = doc.get('characteristics', {}).get('score_okb')
        row['SCORE_OKB_' + source] = value_text(score)[0]
        seen_products = set()
        for trial in trials:
            identity = key_text(trial['product_id'])
            if identity in seen_products:
                raise ValueError(f'Повтор product_id в {source}: {identity}')
            seen_products.add(identity)
            context = contexts.setdefault(identity, {
                'PRODUCT_ID':str(trial['product_id']), 'PRODUCT_TYPE':value_text(trial['product_id'])[1],
                'PRESENT_BEFORE':False, 'PRESENT_AFTER':False,
                'PD_MODEL_BEFORE':None, 'PD_MODEL_AFTER':None, 'CUTOFF_BEFORE':None, 'CUTOFF_AFTER':None})
            context['PRESENT_'+source] = True
            context['PD_MODEL_'+source] = trial.get('scoring', {}).get('pd', {}).get('modelName')
            context['CUTOFF_'+source] = trial.get('cutOff', {}).get('name')
            products.add(str(trial['product_id']))
            partner_types.add(str(trial.get('partner_type', '')))
            routes.add(str(trial.get('routing_method', '')))
    row['PRODUCT_CONTEXT'] = json.dumps([contexts[k] for k in sorted(contexts)], ensure_ascii=False)
    row.update(PRODUCTS=';'.join(sorted(products)), PARTNER_TYPE=';'.join(sorted(partner_types)),
               ROUTING_METHOD=';'.join(sorted(routes)),
               DECISION_CHANGED=int(row['DECISION_BEFORE'] != row['DECISION_AFTER']))
    return row
