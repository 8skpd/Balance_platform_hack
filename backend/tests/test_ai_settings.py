import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import admin, ai, api, store
from test_library import isolated, register, new_run, execute, HEADERS


@pytest.mark.parametrize('model',['', '  ', 'two models', 'bad\nmodel', 'bad\x7fmodel', 'x'*201])
def test_invalid_model_name(model):
    with pytest.raises(ValueError):admin.AIConnection(base_url='http://ollama',model=model)


def test_model_selection_probe_persistence_and_all_job_snapshots(isolated,monkeypatch):
    with TestClient(api.app,headers=HEADERS) as client:
        register(client,'alice')
        old=client.put('/api/admin/ai',json={'base_url':'http://ollama','revision':0}).json()
        assert old['model']==admin.OLLAMA_MODEL
        selected=client.put('/api/admin/ai',json={'base_url':'http://ollama/api/chat','model':' other:latest ','revision':1})
        assert selected.status_code==200 and selected.json()['model']=='other:latest'
        captured=[]
        class FakeClient:
            def __init__(self,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def get(self,url):
                return httpx.Response(200,json={'models':[{'name':'other:latest'},{'name':'next:latest'}]},request=httpx.Request('GET',url))
            def post(self,url,**kwargs):
                captured.append(kwargs['json'])
                content=json.dumps({'summary':'Факты','signals':'Сигналы','next_steps':'Проверить'}) if 'format' in kwargs['json'] else 'Ответ'
                return httpx.Response(200,json={'message':{'content':content}},request=httpx.Request('POST',url))
        monkeypatch.setattr(admin.httpx,'Client',FakeClient)
        probe=client.post('/api/admin/ai/test',json={'base_url':'http://test','model':'next'}).json()
        assert probe['connected'] and probe['available'] and probe['model']=='next'
        assert client.get('/api/admin/ai').json()==selected.json()  # Test does not save.
        assert client.get('/api/ollama').json()['model']=='other:latest'
        assert client.get('/api/ollama').json()['available']
        assert not client.post('/api/admin/ai/test',json={'base_url':'http://test','model':'missing'}).json()['available']
        run=execute(new_run(client));rid=run['id']
        first=client.post(f'/api/runs/{rid}/ai').json()['job_id']
        assert store.get_job(first)['params']['model']=='other:latest'
        changed=client.put('/api/admin/ai',json={'base_url':'http://ollama','model':'next:latest','revision':2})
        assert changed.status_code==200
        assert store.get_job(first)['params']['model']=='other:latest'  # Queued job stays pinned.
        with store.engine.begin() as conn:conn.execute(update(store.jobs).where(store.jobs.c.id==first).values(status='completed'))
        chat=client.post(f'/api/runs/{rid}/ai-chat',json={'message':'Что изменилось?','request_id':str(uuid.uuid4())}).json()['job_id']
        assert store.get_job(chat)['params']['model']=='next:latest'
        with store.engine.begin() as conn:conn.execute(update(store.jobs).where(store.jobs.c.id==chat).values(status='completed'))
        store.patch_run(rid,auto_ai=True);ai.schedule_automatic(rid)
        automatic=next(j for j in store.list_jobs(run_id=rid) if j['params'].get('automatic'))
        assert automatic['params']['model']=='next:latest'
        assert ai.generate_chat(rid,message='Привет')['status']=='completed'
        assert captured[-1]['model']=='next:latest'
        assert ai.generate_ai(rid,model='other:latest')['status']=='completed'
        assert captured[-1]['model']=='other:latest'
        # Old API clients changing only the URL preserve the selected model.
        assert client.put('/api/admin/ai',json={'base_url':'http://new','revision':3}).json()['model']=='next:latest'
        assert client.put('/api/admin/ai',json={'base_url':'http://new','model':'stale','revision':3}).status_code==409
        store.init();assert admin.ai_settings()['model']=='next:latest'
        with store.engine.connect() as conn:
            payload=conn.execute(select(store.app_settings.c.payload).where(store.app_settings.c.id=='ollama')).scalar_one()
        assert payload=={'base_url':'http://new','model':'next:latest'}


def test_probe_timeout_is_visible(isolated,monkeypatch):
    class Offline:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url):raise httpx.ReadTimeout('')
    monkeypatch.setattr(admin.httpx,'Client',Offline)
    result=admin.connection_status('http://offline','test')
    assert not result['connected'] and not result['available']
    assert '5 секунд' in result['error']
