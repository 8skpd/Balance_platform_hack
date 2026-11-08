import json,uuid
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import update,select
from app import api,store,queueing,ai,ai_chat,tasks
from test_library import isolated,register,new_run,execute,HEADERS

def send(client,rid,message='Что изменилось?',request_id=None):
    return client.post(f'/api/runs/{rid}/ai-chat',json={'message':message,'request_id':request_id or str(uuid.uuid4())})
def finish(job_id,text='Изменился один атрибут.'):
    job=queueing.claim(job_id);assert job
    assert queueing.finish(job,'completed',result={'status':'completed','content':text,'model':'test-model','created_at':store.now()})

def test_conversation_saved_context_and_idempotency(isolated):
    with TestClient(api.app,headers=HEADERS) as c:
        register(c,'alice');run=execute(new_run(c));rid=run['id'];key=str(uuid.uuid4())
        first=send(c,rid,request_id=key);assert first.status_code==202,first.text
        jid=first.json()['job_id'];assert send(c,rid,request_id=key).json()['job_id']==jid
        assert send(c,rid,'Ещё вопрос').status_code==409
        finish(jid)
        assert send(c,rid,request_id=key).json()['job_id']==jid
        second=send(c,rid,'Поясни предыдущий ответ');assert second.status_code==202
        params=store.get_job(second.json()['job_id'])['params']
        assert params['conversation']==[{'role':'user','content':'Что изменилось?'},{'role':'assistant','content':'Изменился один атрибут.'}]
        assert params['context']['summary']['total_apps']==1 and params['chat']
        history=c.get(f'/api/runs/{rid}/ai-chat').json()
        assert [r['message'] for r in history['items']]==['Что изменилось?','Поясни предыдущий ответ']
        assert history['active']['id']==second.json()['job_id']
        assert 'ollama_url' not in str(history)
        # Reusing an id for another message must not silently change the conversation.
        assert send(c,rid,'Другой текст',key).status_code==409

def test_chat_access_observer_archive_and_validation(isolated):
    with TestClient(api.app,headers=HEADERS) as c,TestClient(api.app,headers=HEADERS) as b:
        alice=register(c,'alice');bob=register(b,'bob');run=execute(new_run(c));rid=run['id']
        assert b.get(f'/api/runs/{rid}/ai-chat').status_code==404
        assert send(b,rid).status_code==404
        assert c.post(f'/api/runs/{rid}/shares',json={'username':'bob'}).status_code==201
        assert b.get(f'/api/runs/{rid}/ai-chat').json()['can_send'] is False
        assert send(b,rid).status_code==403
        with store.engine.begin() as conn:conn.execute(update(store.users).where(store.users.c.id==bob['id']).values(role='observer'))
        assert send(b,rid).status_code==403
        for text in ['  ','x'*4001]:assert send(c,rid,text).status_code==422
        store.patch_run(rid,archived=True)
        assert send(c,rid).status_code==409
        assert c.get(f'/api/runs/{rid}/ai-chat').json()['can_send'] is False

def test_chat_revision_pagination_failed_retry_and_cancel(isolated):
    with TestClient(api.app,headers=HEADERS) as c:
        register(c,'alice');run=execute(new_run(c));rid=run['id']
        ids=[]
        for n in range(3):
            r=send(c,rid,str(n));ids.append(r.json()['job_id']);finish(ids[-1],f'Ответ {n}')
        page=c.get(f'/api/runs/{rid}/ai-chat?limit=2').json();assert [r['id'] for r in page['items']]==ids[1:]
        older=c.get(f'/api/runs/{rid}/ai-chat',params={'limit':2,'before':page['next_before']}).json();assert [r['id'] for r in older['items']]==ids[:1]
        store.patch_run(rid,report_revision=2)
        job=send(c,rid,'Новая редакция').json();params=store.get_job(job['job_id'])['params'];assert params['conversation']==[]
        claimed=queueing.claim(job['job_id']);queueing.finish(claimed,'failed',error='test unavailable')
        retry=c.post(f"/api/jobs/{job['job_id']}/retry");assert retry.status_code==202,retry.text
        assert retry.json()['params']['message']=='Новая редакция'
        c.post(f"/api/jobs/{retry.json()['id']}/cancel")
        history=c.get(f'/api/runs/{rid}/ai-chat').json();assert history['active'] is None
        assert all(x['stale'] for x in history['items'][:3])
        assert history['items'][-1]['status']=='cancelled'

def test_ollama_chat_receives_history_and_returns_text(isolated,monkeypatch):
    captured=[]
    def handler(request):
        captured.append(json.loads(request.content));return httpx.Response(200,json={'model':'qwen3-test','message':{'content':'<think>hidden</think>**Ответ** по отчёту.'},'done_reason':'stop'})
    real_client=httpx.Client;monkeypatch.setattr(ai.httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    reply=ai.generate_chat('test','http://ollama',context={'total':2},message='Почему?',conversation=[{'role':'user','content':'Привет'},{'role':'assistant','content':'Здравствуйте'}])
    assert reply['status']=='completed' and reply['content']=='**Ответ** по отчёту.'
    assert captured[0]['messages'][-3:]==[{'role':'user','content':'Привет'},{'role':'assistant','content':'Здравствуйте'},{'role':'user','content':'Почему?'}]
    assert 'format' not in captured[0] and captured[0]['think'] is False

def test_failed_model_response_and_bounded_context(isolated,monkeypatch):
    real_client=httpx.Client
    monkeypatch.setattr(ai.httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'message':{'content':''}})),**kwargs))
    assert ai.generate_chat('x','http://ollama',context={},message='test')['status']=='failed'
    with TestClient(api.app,headers=HEADERS) as c:
        register(c,'alice');run=execute(new_run(c))
        for _ in range(8):finish(send(c,run['id'],'x'*4000).json()['job_id'],'y'*10000)
        history=ai_chat.conversation(store.get_run(run['id']))
        assert sum(len(x['content']) for x in history)<=24000
        assert len(history)%2==0 and all(len(x['content'])<=8000 for x in history)
