"""End-to-end acceptance check for a disposable Compose project only."""
import io
import json
import os
import secrets
import sys
import time
import zipfile
from pathlib import Path
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app import auth,store,procedures
from app.settings import DATA

CHECK='''def run(mart, params):
    ids=mart.app_ids(diff_flag=1)
    return [{'LABEL':'Docker Python check','ARGUMENT':'Changed applications','SOURCE':'AFTER',
      'CNT':len(ids),'TOTAL':mart.total_apps(),'METRIC':len(ids),'RESULT':'FAIL',
      'CONFIDENCE':'HIGH','APP_IDS':ids}]
CHECK={'entrypoint':run}
'''

def response(client,method,url,**kwargs):
    result=client.request(method,url,**kwargs)
    if not result.is_success:raise RuntimeError(f'{method} {url}: {result.status_code} {result.text[:500]}')
    return result

def wait_for(client,url,timeout=120):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        row=response(client,'GET',url).json()
        if row['status']=='completed':return row
        if row['status'] in ('failed','timed_out','cancelled'):raise RuntimeError(str(row.get('error') or row['status']))
        time.sleep(.5)
    raise RuntimeError('Operation timed out: '+url)

def prepare(client,state_file):
    password=secrets.token_urlsafe(32)
    # Refuses any non-empty installation; never resets or replaces an existing account.
    user=auth.bootstrap_admin(auth.Registration(username='docker-smoke-admin',display_name='Docker smoke',password=password))
    response(client,'POST','/api/auth/login',json={'username':user['username'],'password':password})
    runtime=response(client,'GET','/api/runtime').json()
    assert runtime['broker'] and runtime['workers'] and runtime['scheduler'] and runtime['sandbox']['available'],runtime
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        for side in ('BEFORE_MASTER','AFTER_BETA'):
            for app_id in ('A','B'):
                doc={'appData':{'application_id':app_id},'value':2 if side=='AFTER_BETA' and app_id=='A' else 1}
                archive.writestr(f'RESULTS/{side}/{app_id}.json',json.dumps(doc))
    dataset=response(client,'POST','/api/dataset-imports?name=Docker%20smoke&filename=smoke.zip',content=buffer.getvalue()).json()
    wait_for(client,'/api/jobs/'+dataset['job_id'])
    selected={}
    for stage,code in procedures.EXAMPLES.items():
        saved=response(client,'POST','/api/procedures',json={'stage':stage,'name':stage,'code':code,'filename':stage+'.py'}).json()
        selected[stage]={'id':saved['id'],'revision':saved['revision']}
    config=response(client,'GET','/api/config').json()
    run=response(client,'POST','/api/runs',json={'name':'Docker acceptance','dataset':dataset['id'],'config':config,'builtin_checks':[],'procedures':selected}).json()
    run=wait_for(client,'/api/runs/'+run['id'])
    assert run['summary']['total_apps']==2 and run['summary']['changed_apps']==1,run['summary']
    job=response(client,'POST',f"/api/runs/{run['id']}/python-checks",json={'code':CHECK,'name':'Docker Python check','publish':True}).json()
    wait_for(client,'/api/jobs/'+job['id'])
    state={'user_id':user['id'],'session':client.cookies.get(auth.COOKIE),'run_id':run['id'],'dataset_id':dataset['id']}
    with open(state_file,'x',opener=lambda path,flags:os.open(path,flags,0o600)) as file:json.dump(state,file)
    verify(client,state_file)
    print('PASS: import, queue, three Python procedures, Python check, pivot, report and backup',flush=True)

def verify(client,state_file):
    state=json.loads(state_file.read_text());client.cookies.set(auth.COOKIE,state['session'])
    assert response(client,'GET','/api/auth/me').json()['id']==state['user_id']
    root='/api/runs/'+state['run_id'];run=response(client,'GET',root).json()
    assert run['status']=='completed' and run['summary']['changed_apps']==1
    assert any(check['LABEL']=='Docker Python check' for check in run['checks'])
    pivot=response(client,'POST',root+'/pivot/query',json={'spec':{'rows':['PATH'],'columns':['SOURCE'],'metrics':['apps'],'changed_only':True}}).json()
    assert pivot['grand']['apps']==1
    response(client,'GET',root+'/applications/A/source')
    assert 'Docker Python check' in response(client,'GET',root+'/report.html').text
    assert response(client,'GET','/api/dataset-imports/'+state['dataset_id']).json()['status']=='completed'
    from app.backups import create_bundle,verify_bundle,archive_path
    for attempt in range(10):
        try:bundle=create_bundle();break
        except BlockingIOError:
            if attempt==9:raise
            time.sleep(.2)
    verified=verify_bundle(archive_path(bundle['archive_id']))
    assert verified['reports_opened']==1 and verified['api_probe'],verified
    print('PASS: persistent account/session, dataset, report and verified restore',flush=True)

if __name__=='__main__':
    if os.environ.get('BALANCE_DOCKER_SMOKE')!='1':raise SystemExit('Use scripts/docker-smoke.sh: this test creates data in a disposable installation.')
    state=DATA/'.docker-smoke-state.json'
    with httpx.Client(base_url=os.environ.get('BALANCE_SMOKE_URL','http://127.0.0.1:8000'),headers={'X-Balance-Request':'1'},timeout=20) as client:
        if sys.argv[1]=='prepare':prepare(client,state)
        elif sys.argv[1]=='verify':verify(client,state)
        else:raise SystemExit('Expected prepare or verify')
