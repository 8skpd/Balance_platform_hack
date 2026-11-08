import {provisionAccount} from './test-accounts';
import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {createServer,type ServerResponse} from 'node:http';

async function account(request:any,name:string){const r=await provisionAccount(request,{data:{username:name+'-'+Date.now(),display_name:name,password:'Audit-test-password-42'}});expect(r.status()).toBe(201);return r.json();}

test('slow authentication and transient outage preserve form; concurrent config save is rejected',async({page})=>{
 await account(page.request,'config-fixes');let delayed=false;
 await page.route('**/api/auth/me',async route=>{if(!delayed){delayed=true;await new Promise(r=>setTimeout(r,6500));}await route.continue();});
 await page.goto('/?view=settings');const field=page.getByLabel('Порог падения одобрений',{exact:true});await expect(field).toBeVisible({timeout:15000});await page.unroute('**/api/auth/me');
 const second=await page.context().newPage();await second.goto('/?view=settings');const other=second.getByLabel('Порог падения одобрений',{exact:true});await expect(other).toBeVisible();
 await field.fill('0.37');await page.route('**/api/auth/me',route=>route.abort());await expect(page.locator('.connection-notice')).toBeVisible({timeout:12000});await expect(field).toHaveValue('0.37');await field.fill('0.38');
 await page.unroute('**/api/auth/me');await page.getByRole('button',{name:'Проверить соединение',exact:true}).click();await expect(page.locator('.connection-notice')).toHaveCount(0);await expect(field).toHaveValue('0.38');
 await page.getByRole('button',{name:'Сохранить',exact:true}).click();await expect(page.getByRole('button',{name:'Сохранено',exact:true})).toBeVisible();
 await other.fill('0.72');await second.getByRole('button',{name:'Сохранить',exact:true}).click();await expect(second.getByRole('alert')).toContainText('другой вкладке');await expect(other).toHaveValue('0.72');expect((await(await page.request.get('/api/config')).json()).check_params.max_drop_rel).toBe(.38);
 await second.screenshot({path:'../artifacts/fixes-config-conflict.png',fullPage:true});second.once('dialog',d=>d.accept());await second.getByRole('button',{name:'Загрузить сохранённую конфигурацию',exact:true}).click();await expect(other).toHaveValue('0.38');
 await other.fill('0.42');await second.getByRole('button',{name:'Сохранить',exact:true}).click();await expect(second.getByRole('button',{name:'Сохранено',exact:true})).toBeVisible();expect((await(await page.request.get('/api/config/state')).json()).revision).toBe(2);await second.close();
});

test('product model v2, Python draft and last successful AI answer shared with observer',async({page,browser,baseURL})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await account(page.request,'report-fixes');
 const admin=await browser.newContext({baseURL,extraHTTPHeaders:{'X-Balance-Request':'1'}});expect((await admin.request.post('/api/auth/login',{data:{username:process.env.BALANCE_TEST_ADMIN_USERNAME,password:process.env.BALANCE_TEST_ADMIN_PASSWORD}})).status()).toBe(200);
 const recipient=await browser.newContext({baseURL,extraHTTPHeaders:{'X-Balance-Request':'1'}});const user=await account(recipient.request,'view-fixes');await admin.request.put(`/api/admin/users/${user.id}/role`,{data:{role:'observer',expected_role:'user'}});const viewer=await recipient.newPage();
 const zip=execFileSync('../.venv/bin/python',['-c',`import io,json,zipfile,sys
b=io.BytesIO()
with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
 for side in ['BEFORE_MASTER','AFTER_BETA']:
  doc={'appData':{'application_id':'A'},'decisionResult':{'decision':'APPROVE','product_trials':[{'product_id':'A','scoring':{'pd':{'modelName':'same'}}},{'product_id':'B','scoring':{'pd':{'modelName':'new' if side=='AFTER_BETA' else 'old'}}}]}}
  z.writestr('RESULTS/'+side+'/A.json',json.dumps(doc))
sys.stdout.buffer.write(b.getvalue())`]);
 const upload=await page.request.post('/api/dataset-imports?name=Audit&filename=audit.zip',{data:zip,headers:{'Content-Type':'application/zip'}});expect(upload.status()).toBe(202);const dataset=(await upload.json()).id;
 await expect.poll(async()=>(await(await page.request.get('/api/dataset-imports/'+dataset)).json()).status,{timeout:30000,intervals:[1000]}).toBe('completed');const config=await(await page.request.get('/api/config')).json();
 const created=await page.request.post('/api/runs',{data:{name:'Audit corrections',dataset,config,builtin_checks:['target_model_changed']}});expect(created.status()).toBe(201);const run=await created.json(),url='/api/runs/'+run.id;
 await expect.poll(async()=>{const r=await(await page.request.get(url)).json();if(r.status==='failed')throw new Error(r.error);return r.status;},{timeout:30000,intervals:[1000]}).toBe('completed');const report=await(await page.request.get(url)).json();expect(report.checks[0].CHECK_VERSION).toBe(2);expect(report.checks[0].ARGUMENT).toBe('B (string): old -> new');expect(report.checks[0].RESULT).toBe('FAIL');
 await page.goto('/?run='+run.id+'&tab=python');const code=page.getByLabel('Код Python',{exact:true});const draft='# Unsaved diagnostic\nprint("keep this draft")';await code.fill(draft);
 await page.route('**/api/auth/me',route=>route.abort());await expect(page.locator('.connection-notice')).toBeVisible({timeout:12000});await expect(code).toContainText(draft);await page.unroute('**/api/auth/me');await page.getByRole('button',{name:'Проверить соединение',exact:true}).click();await expect(page.locator('.connection-notice')).toHaveCount(0);await expect(code).toContainText(draft);
 await page.request.post(url+'/shares',{data:{username:user.username}});
 let hold=false,content='Первый успешный ответ',pending:ServerResponse|undefined;
 const fake=createServer((req,res)=>{res.setHeader('Content-Type','application/json');if(req.url==='/api/tags')res.end(JSON.stringify({models:[{name:'qwen3-server:latest'}]}));else if(hold)pending=res;else res.end(JSON.stringify({message:{content:JSON.stringify({summary:content,signals:'Изменена модель B.',next_steps:'Проверить продукт B.'})}}));});
 await new Promise<void>(resolve=>fake.listen(0,'127.0.0.1',resolve));const original=await(await admin.request.get('/api/admin/ai')).json();
 try{
  await admin.request.put('/api/admin/ai',{data:{base_url:`http://127.0.0.1:${(fake.address() as {port:number}).port}`,revision:original.revision}});
  await page.goto('/?run='+run.id+'&tab=ai');await page.getByLabel('Сообщение ИИ',{exact:true}).fill('Проанализируй отчёт.');await page.getByRole('button',{name:'Отправить сообщение',exact:true}).click();await expect(page.locator('.chat-scroll')).toContainText(content,{timeout:20000});await viewer.goto('/?run='+run.id+'&tab=ai');await expect(viewer.locator('.chat-scroll')).toContainText(content);
  hold=true;await page.getByLabel('Сообщение ИИ',{exact:true}).fill('Обнови вывод по отчёту.');await page.getByRole('button',{name:'Отправить сообщение',exact:true}).click();await expect.poll(()=>!!pending,{timeout:15000}).toBe(true);await expect(viewer.locator('.chat-working')).toBeVisible({timeout:10000});await expect(viewer.locator('.chat-scroll')).toContainText(content);
  pending!.statusCode=503;pending!.end('{}');pending=undefined;await expect.poll(async()=>(await(await page.request.get(url)).json()).ai.status,{timeout:15000}).toBe('failed');await expect(viewer.locator('.chat-scroll')).toContainText(content);await expect(viewer.locator('.chat-failed')).toBeVisible({timeout:10000});await viewer.screenshot({path:'../artifacts/fixes-ai-retained.png',fullPage:true});
  hold=false;content='Второй успешный ответ';await page.getByLabel('Сообщение ИИ',{exact:true}).fill('Обнови вывод по отчёту.');await page.getByRole('button',{name:'Отправить сообщение',exact:true}).click();await expect(viewer.locator('.chat-scroll')).toContainText(content,{timeout:20000});await expect(viewer.locator('.chat-scroll')).toContainText('Первый успешный ответ');expect(errors).toEqual([]);
 }finally{
  if(pending){pending.statusCode=503;pending.end('{}');}const latest=await(await admin.request.get('/api/admin/ai')).json();await admin.request.put('/api/admin/ai',{data:{base_url:original.base_url,revision:latest.revision}});await new Promise<void>(resolve=>fake.close(()=>resolve()));await recipient.close();await admin.close();
 }
});
