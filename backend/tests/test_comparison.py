import json
from copy import deepcopy

import duckdb
import pytest

from app.normalizer import DEFAULT_CONFIG, normalize, segments
from app.worker import build_database
from app.mart import Mart

def config(arrays=None, buckets=None):
    result = deepcopy(DEFAULT_CONFIG)
    result['arrays'].update(arrays or {})
    result['buckets'].update(buckets or {})
    return result

def compare(tmp_path, before, after, settings=None):
    settings = settings or config()
    normalized_file = tmp_path / 'normal.jsonl'
    normalized = normalize(before, 'A', 'BEFORE', settings) + normalize(after, 'A', 'AFTER', settings)
    normalized_file.write_text('\n'.join(json.dumps(row) for row in normalized))
    segments_file = tmp_path / 'segments.jsonl'
    segments_file.write_text(json.dumps(segments('A', before, after)))
    conn = duckdb.connect()
    build_database(conn, normalized_file, segments_file)
    return conn, Mart(conn)

def test_four_change_types_and_presence(tmp_path):
    conn, mart = compare(tmp_path, {'same': 1, 'v': 1, 'gone': None, 'null': None, 'empty': '', 'typed': 'false'},
                        {'same': 1.0, 'v': 2, 'new': None, 'null': False, 'empty': '', 'typed': False})
    rows = {r['PATH']: r for r in mart.rows()}
    assert rows['same']['CHANGE_TYPE'] == 'SAME'
    assert rows['v']['CHANGE_TYPE'] == 'VALUE_CHANGED'
    assert rows['gone']['CHANGE_TYPE'] == 'ATTRIBUTE_LOST'
    assert rows['new']['CHANGE_TYPE'] == 'ATTRIBUTE_NEW'
    assert rows['gone']['VALUE_BEFORE'] == 'NULL'
    assert rows['gone']['VALUE_AFTER'] is None
    assert rows['null']['DIFF_FLAG'] == 1
    assert rows['empty']['DIFF_FLAG'] == 0
    assert rows['typed']['DIFF_FLAG'] == 1
    conn.close()

def test_array_reordering_and_composite_keys(tmp_path):
    before = {'offers': [{'id': 1, 'type': 'standard', 'value': 10}, {'id': 1, 'type': 'manual', 'value': 20}]}
    after = {'offers': list(reversed(before['offers']))}
    rules = config({'offers': {'mode': 'key', 'keys': ['id', 'type']}})
    conn, mart = compare(tmp_path, before, after, rules)
    assert mart.count(diff_flag=1) == 0
    assert len(mart.rows(path='offers.value')) == 2
    conn.close()

def test_vectors_keep_position_and_sets_ignore_order(tmp_path):
    rules = config({'vector': {'mode': 'index'}, 'reasons': {'mode': 'value'}})
    conn, mart = compare(tmp_path, {'vector': [1, 2], 'reasons': ['A', 'B']},
                        {'vector': [2, 1], 'reasons': ['B', 'A', 'A']}, rules)
    assert mart.count(path='vector', diff_flag=1) == 2
    assert mart.count(path='reasons', diff_flag=1) == 0
    conn.close()

def test_bucket_does_not_hide_value_change(tmp_path):
    conn, mart = compare(tmp_path, {'score': 0.42}, {'score': 0.58}, config(buckets={'score': [0.3, 0.6, 1]}))
    row = mart.rows()[0]
    assert row['ADJUSTED_BEFORE'] == row['ADJUSTED_AFTER']
    assert row['DIFF_FLAG'] == 1
    conn.close()

def test_model_context_not_collapsed(tmp_path):
    before = {'decisionResult': {'scoring': [{'modelName': 'V1', 'features': {'age': 20}}, {'modelName': 'V2', 'features': {'age': 20}}]}}
    after = deepcopy(before)
    after['decisionResult']['scoring'][1]['features']['age'] = 21
    conn, mart = compare(tmp_path, before, after)
    rows = mart.rows(path='decisionResult.scoring.features.age')
    assert len(rows) == 2
    assert mart.count(diff_flag=1) == 1
    assert mart.app_ids(diff_flag=1) == ['A']
    conn.close()

def test_technical_fields_excluded(tmp_path):
    before = {'appData': {'request_id': 'old', 'createDateTime': 'old', 'application_id': 'A'}}
    after = {'appData': {'request_id': 'new', 'createDateTime': 'new', 'application_id': 'A'}}
    conn, mart = compare(tmp_path, before, after)
    assert mart.count() == 1
    assert mart.count(diff_flag=1) == 0
    conn.close()

def test_duplicate_array_key_rejected():
    with pytest.raises(ValueError, match='Повтор ключа'):
        normalize({'x': [{'id': 1}, {'id': 1}]}, 'A', 'BEFORE', config({'x': {'mode': 'key', 'keys': ['id']}}))

def test_unconfigured_array_rejected():
    with pytest.raises(ValueError, match='Не задан режим'):
        normalize({'unknown': [1]}, 'A', 'BEFORE', config())

def test_filters_are_parameterized_and_combined(tmp_path):
    conn, mart = compare(tmp_path, {'a': 1, 'b': 1}, {'a': 2, 'b': 3})
    assert mart.count(path=['a', 'b'], change_type=['VALUE_CHANGED'], app_ids=['A']) == 2
    assert mart.count(path="' OR 1=1 --") == 0
    assert mart.count(app_ids=[]) == 0
    assert mart.app_ids(path_prefix='a') == ['A']
    conn.close()

def test_large_integer_values_are_not_rounded(tmp_path):
    conn, mart = compare(tmp_path, {'id': 9007199254740992}, {'id': 9007199254740993})
    assert mart.count(diff_flag=1) == 1
    conn.close()
