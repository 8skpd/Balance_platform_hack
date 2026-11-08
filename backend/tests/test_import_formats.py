import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api, imports, queueing, store, tasks
from app.import_formats import csv_documents, xml_documents
from app.normalizer import DEFAULT_CONFIG
from test_imports import isolated, archive, register, pending, validate, HEADERS


def test_csv_types_paths_bom_delimiters_and_identifiers():
    for delimiter in (',',';','\t'):
        raw=('\ufeffapplication_id'+delimiter+'decisionResult.decision'+delimiter+'score'+delimiter+'enabled'+delimiter+'empty'+delimiter+'missing\n001'+delimiter+'APPROVE'+delimiter+'12.5'+delimiter+'true'+delimiter+delimiter+'null\n').encode()
        [(doc,_,_)]=list(csv_documents(raw,'test.csv'))
        assert doc=={'appData':{'application_id':'001'},'decisionResult':{'decision':'APPROVE'},'score':12.5,'enabled':True,'empty':'','missing':None}
    import csv,io
    out=io.StringIO(newline='');writer=csv.writer(out);writer.writerow(['APP_ID','items','text']);writer.writerow(['1e3','[{"id":"x","amount":3}]','"123"'])
    doc=list(csv_documents(out.getvalue().encode(),'test.csv'))[0][0]
    assert doc['appData']['application_id']=='1e3' and doc['items']==[{'id':'x','amount':3}] and doc['text']=='123'


@pytest.mark.parametrize('raw,match',[
    (b'score\n1\n','application_id'),
    (b'application_id,application_id\nA,A\n','Повтор'),
    (b'APP_ID,application_id\nA,A\n','одну колонку'),
    (b'application_id,a,a.b\nA,1,2\n','Конфликт'),
    (b'application_id,a..b\nA,1\n','путь'),
    (b'application_id,value\nA,1,2\n','число значений'),
    (b'application_id,value\nA\n','число значений'),
    (b'application_id\n','нет заявок'),
    (b'application_id,value\nA,1e999\n','число'),
    (b'application_id,value\nA,\xff\n','UTF-8'),
    (b'application_id,value\nA,"broken\n','CSV'),
    (b'application_id,value\nA,"{""a"":1,""a"":2}"\n','Повтор ключа'),
])
def test_csv_rejects_ambiguous_or_invalid_data(raw,match):
    with pytest.raises(ValueError,match=match):list(csv_documents(raw,'test.csv'))


def test_xml_types_arrays_namespaces_ids_and_empty_values():
    raw=b'''<applications xmlns="urn:example"><application application_id="001">
      <decisionResult><decision>APPROVE</decision><product_trials><item><product_id>X</product_id></item></product_trials></decisionResult>
      <score>12.5</score><enabled>true</enabled><nothing type="null"/><empty/><array type="array"/><obj type="object"/><text type="string">123</text>
    </application><application><appData><application_id>1e3</application_id></appData><score>0</score></application></applications>'''
    records=list(xml_documents(raw,'test.xml','data'))
    assert len(records)==2
    doc=records[0][0]
    assert doc['appData']['application_id']=='001'
    assert doc['decisionResult']['product_trials']==[{'product_id':'X'}]
    assert {k:doc[k] for k in ('score','enabled','nothing','empty','array','obj','text')}=={'score':12.5,'enabled':True,'nothing':None,'empty':'','array':[],'obj':{},'text':'123'}
    assert records[1][0]['appData']['application_id']=='1e3'
    assert list(xml_documents(b'<response><appData><application_id>0001</application_id></appData></response>','test.xml','data'))[0][0]['appData']['application_id']=='0001'


@pytest.mark.parametrize('raw,match',[
    (b'<!DOCTYPE a [<!ENTITY x "EXPAND">]><a><v>&x;</v></a>','DTD'),
    (b'<!DOCTYPE a SYSTEM "file:///etc/passwd"><a><v>1</v></a>','DTD'),
    ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE a [<!ENTITY x "EXPAND">]><a>&x;</a>'.encode('utf-16'),'DTD'),
    (b'<applications/>','от 1'),
    (b'<applications><strange/></applications>','ожидаются'),
    (b'<response><v>1</response>','test.xml'),
    (b'<a><v>1e999</v></a>','число'),
    (b'<a><v type="number">text</v></a>','число'),
    (b'<a><v type="array">lost text</v></a>','Массив'),
    (b'<a><v type="null">lost text</v></a>','содержит данные'),
    (b'<a>before<v>child</v>after</a>','Смешанный'),
    (b'<a xmlns:x="urn:x" xmlns:y="urn:y"><x:v>1</x:v><y:v>2</y:v></a>','пространств'),
    (b'<application id="A"><application_id>B</application_id></application>','Противоречивые'),
    (b'<a>'*67+b'text'+b'</a>'*67,'сложность'),
])
def test_xml_rejects_unsafe_and_ambiguous_data(raw,match):
    with pytest.raises(ValueError,match=match):list(xml_documents(raw,'test.xml','A'))


def test_csv_xml_publish_compare_and_privacy(isolated):
    with TestClient(api.app,headers=HEADERS) as client,TestClient(api.app,headers=HEADERS) as other:
        owner=register(client,'formats')['id'];register(other,'other')
        before=b'application_id,decisionResult.decision,score\n001,APPROVE,10\n002,DECLINE,5\n'
        after=b'<applications><application application_id="002"><decisionResult><decision>DECLINE</decision></decisionResult><score>5</score></application><application application_id="001"><decisionResult><decision>APPROVE</decision></decisionResult><score>12</score></application></applications>'
        response=client.post('/api/dataset-imports?name=Mixed&filename=mixed.zip',content=archive([('BEFORE.csv',before),('AFTER.xml',after)]))
        assert response.status_code==202,response.text
        value=response.json();job,work,result=validate(store.get_job(value['job_id']),isolated)
        assert result['pairs']==2 and result['json_files']==4 and result['source_formats']==['csv','xml']
        assert queueing.finish(job,'completed',result=result,artifact=work/'dataset')
        assert other.get('/api/dataset-imports/'+value['id']).status_code==404
        response=client.post('/api/runs',json={'name':'Mixed formats','dataset':value['id'],'config':DEFAULT_CONFIG,'builtin_checks':[]})
        assert response.status_code==201,response.text
        run_id=response.json()['id'];tasks.execute(store.list_jobs(run_id=run_id)[0]['id']);run=store.get_run(run_id)
        assert run['status']=='completed',run['error']
        assert run['summary']['total_apps']==2 and run['summary']['changed_apps']==1
        source=client.get(f'/api/runs/{run_id}/applications/001/source').json()
        assert source['BEFORE']['score']==10 and source['AFTER']['score']==12
        assert source['BEFORE']['appData']['application_id']=='001'
        assert other.get(f'/api/runs/{run_id}/applications/001/source').status_code==404


@pytest.mark.parametrize('entries,match',[
    ([('BEFORE.csv','application_id,value\nA,1\nA,2\n'),('AFTER.csv','application_id,value\nA,2\n')],'повтор application_id'),
    ([('BEFORE.csv','application_id,value\nA,1\n'),('AFTER.csv','application_id,value\nB,2\n')],'Непарные'),
    ([('BEFORE.csv','application_id,value\n../A,1\n'),('AFTER.csv','application_id,value\n../A,2\n')],'корректный application_id'),
    ([('BEFORE.xml','<applications><application><v>1</v></application></applications>'),('AFTER.xml','<applications><application><v>2</v></application></applications>')],'application_id'),
    ([('RESULTS/BEFORE_MASTER/A.json','{"value":1}'),('RESULTS/BEFORE_MASTER/table.csv','application_id,value\nA,1\n'),('RESULTS/AFTER_BETA/A.json','{"value":2}')],'повтор application_id'),
])
def test_invalid_new_formats_not_published(isolated,entries,match):
    with pytest.raises(ValueError,match=match):validate(pending('owner',archive(entries)),isolated)
    assert not list((isolated/'datasets').iterdir())


def test_new_formats_obey_pair_and_expansion_limits(isolated,monkeypatch):
    entries=[(side+'.csv','application_id,value\nA,1\nB,2\n') for side in ('BEFORE','AFTER')]
    monkeypatch.setattr(imports,'IMPORT_MAX_PAIRS',1)
    with pytest.raises(ValueError,match='Больше 1'):validate(pending('owner',archive(entries)),isolated)
    monkeypatch.setattr(imports,'IMPORT_MAX_PAIRS',20000)
    entries=[(side+'.csv','application_id,'+'a'*100+'\nA,1\nB,2\n') for side in ('BEFORE','AFTER')]
    # The source fits but expansion of long column names across rows does not.
    monkeypatch.setattr(imports,'IMPORT_MAX_BYTES',350)
    with pytest.raises(ValueError,match='преобразованных'):validate(pending('owner',archive(entries)),isolated)


def test_downloadable_examples_are_equivalent():
    folder=Path(__file__).resolve().parents[2]/'frontend/public/examples'
    for side in ('before','after'):
        csv=[x[0] for x in csv_documents((folder/(side+'.csv')).read_bytes(),side+'.csv')]
        xml=[x[0] for x in xml_documents((folder/(side+'.xml')).read_bytes(),side+'.xml','data')]
        assert csv==xml


def test_array_conversion_preserves_builtin_model_check(isolated):
    import csv,io
    with TestClient(api.app,headers=HEADERS) as client:
        user=register(client,'models')
        out=io.StringIO(newline='');writer=csv.writer(out)
        writer.writerow(['application_id','decisionResult.decision','decisionResult.product_trials'])
        writer.writerow(['A','APPROVE',json.dumps([{'product_id':'X','scoring':{'pd':{'modelName':'old'}}}])])
        xml='<applications><application application_id="A"><decisionResult><decision>APPROVE</decision><product_trials><item><product_id>X</product_id><scoring><pd><modelName>new</modelName></pd></scoring></item></product_trials></decisionResult></application></applications>'
        job,work,result=validate(pending(user['id'],archive([('BEFORE.csv',out.getvalue()),('AFTER.xml',xml)])),isolated)
        assert result['array_paths']==['decisionResult.product_trials']
        queueing.finish(job,'completed',result=result,artifact=work/'dataset')
        r=client.post('/api/runs',json={'dataset':job['dataset_id'],'config':DEFAULT_CONFIG,'builtin_checks':['target_model_changed']})
        assert r.status_code==201,r.text
        run_id=r.json()['id'];tasks.execute(store.list_jobs(run_id=run_id)[0]['id']);run=store.get_run(run_id)
        assert run['status']=='completed',run['error']
        assert len(run['checks'])==1 and run['checks'][0]['RESULT']=='FAIL'
        assert run['checks'][0]['CNT']==1 and run['checks'][0]['APP_IDS']==['A']


@pytest.mark.parametrize('extension,raw',[
 ('csv',b'SOURCE,application_id,value\nBEFORE,A,1\nAFTER,A,2\n'),
 ('xml',b'<dataset><BEFORE><application application_id="A"><value>1</value></application></BEFORE><AFTER><application application_id="A"><value>2</value></application></AFTER></dataset>'),
])
def test_combined_file_import(isolated,extension,raw):
    job,work,stats=validate(pending('owner',archive([('dataset.'+extension,raw)])),isolated)
    assert stats['pairs']==1
    from app.dataset_storage import read_raw
    assert json.loads(read_raw(work/'dataset','BEFORE_MASTER','A'))['value']==1
    assert json.loads(read_raw(work/'dataset','AFTER_BETA','A'))['value']==2


@pytest.mark.parametrize('extension,raw,match',[
 ('csv',b'application_id,value\nA,1\n','SOURCE'),
 ('csv',b'SOURCE,application_id,value\nunknown,A,1\n','BEFORE'),
 ('csv',b'SOURCE,application_id,value\nBEFORE,A,1\n','Непарные'),
 ('xml',b'<dataset><BEFORE><application application_id="A"><v>1</v></application></BEFORE></dataset>','BEFORE'),
 ('xml',b'<dataset><BEFORE/><BEFORE/><AFTER/></dataset>','Повтор'),
])
def test_combined_file_rejects_invalid_sides(isolated,extension,raw,match):
    with pytest.raises(ValueError,match=match):validate(pending('owner',archive([('dataset.'+extension,raw)])),isolated)


def test_combined_large_file_reports_byte_progress(isolated,monkeypatch):
    lines=['SOURCE,application_id,value']
    for side in ('BEFORE','AFTER'):
        lines.extend(f'{side},A{i:05d},{i}' for i in range(1100))
    events=[]
    monkeypatch.setattr(imports,'progress',lambda work,value,stage:events.append((value,stage)))
    _,_,stats=validate(pending('owner',archive([('dataset.csv','\n'.join(lines)+'\n')])),isolated)
    assert stats['pairs']==1100 and stats['storage_format']=='sqlite'
    values=[value for value,stage in events if stage.startswith('Проверено ответов')]
    assert values==sorted(values) and values[-1]>50 and max(values)<=95
