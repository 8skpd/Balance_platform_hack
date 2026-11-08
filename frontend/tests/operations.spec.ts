import {provisionAccount} from './test-accounts';
import {openTool,openMode,reveal} from './ui-navigation';
import {test,expect,type APIRequestContext} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {createServer} from 'node:http';
import {writeFileSync} from 'node:fs';
const password='Operations-test-password-42';
async function register(request:APIRequestContext,name:string){const r=await provisionAccount(request,{data:{username:name,display_name:name,password}});expect(r.status()).toBe(201);return r.json();}
async function completed(request:APIRequestContext,url:string){await expect.poll(async()=>{const r=await(await request.get(url)).json();if(['failed','timed_out'].includes(r.status))throw new Error(r.error);return r.status;},{timeout:60000,intervals:[1000]}).toBe('completed');return (await request.get(url)).json();}
function archive(value:number){return execFileSync('../.venv/bin/python',['-c',`import io,json,zipfile,sys
b=io.BytesIO()
with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
 for side in ['BEFORE_MASTER','AFTER_BETA']:
  d={'appData':{'application_id':'A'},'value':${value} if side=='AFTER_BETA' else 0,'decisionResult':{'decision':'APPROVE','product_trials':[{'product_id':'X','scoring':{'pd':{'modelName':'new' if side=='AFTER_BETA' else 'old'}}}]}}
  z.writestr('RESULTS/'+side+'/A.json',json.dumps(d))
sys.stdout.buffer.write(b.getvalue())`]);}
test('shared discussion and review, comparison, AI history, transfer, blocking, archive and verified backup',async({page,browser,baseURL})=>{
 test.setTimeout(240000);page.setDefaultTimeout(15000);const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 const adminLogin=await page.request.post('/api/auth/login',{data:{username:process.env.BALANCE_TEST_ADMIN_USERNAME,password:process.env.BALANCE_TEST_ADMIN_PASSWORD}});expect(adminLogin.status()).toBe(200);const admin=await adminLogin.json();
 const writer=await browser.newContext({baseURL,extraHTTPHeaders:{'X-Balance-Request':'1'}}),reader=await browser.newContext({baseURL,extraHTTPHeaders:{'X-Balance-Request':'1'}});
 const suffix=Date.now();const bob=await register(writer.request,'ops-writer-'+suffix),viewer=await register(reader.request,'ops-reader-'+suffix);
 await page.request.put(`/api/admin/users/${viewer.id}/role`,{data:{role:'observer',expected_role:'user'}});
 const wp=await writer.newPage(),rp=await reader.newPage();wp.setDefaultTimeout(15000);rp.setDefaultTimeout(15000);wp.on('pageerror',e=>errors.push(e.message));rp.on('pageerror',e=>errors.push(e.message));
 const config=await(await page.request.get('/api/config')).json();const runs:any[]=[];
 for(let i=1;i<=2;i++){
  const upload=await page.request.post(`/api/dataset-imports?name=Operations-${suffix}-${i}&filename=ops.zip`,{data:archive(i),headers:{'Content-Type':'application/zip'}});expect(upload.status()).toBe(202);const dataset=(await upload.json()).id;
  await completed(page.request,'/api/dataset-imports/'+dataset);
  const created=await page.request.post('/api/runs',{data:{name:`Согласование ${suffix}-${i}`,dataset,config,builtin_checks:['target_model_changed']}});expect(created.status()).toBe(201);const run=await created.json();runs.push(await completed(page.request,'/api/runs/'+run.id));
  for(const name of [bob.username,viewer.username])expect((await page.request.post(`/api/runs/${run.id}/shares`,{data:{username:name}})).status()).toBe(201);
 }
 const run=runs[0],root='/api/runs/'+run.id;
 await page.goto('/?run='+run.id+'&tab=discussion');await page.getByLabel('Логин ответственного',{exact:true}).fill(bob.username);await page.getByLabel('Обоснование',{exact:true}).fill('Проверить ожидаемую смену модели');await page.getByRole('button',{name:'Сохранить рассмотрение',exact:true}).click();
 await expect.poll(async()=>(await(await page.request.get(root+'/review')).json()).status).toBe('in_review');
 await wp.goto('/?run='+run.id+'&tab=discussion');await wp.getByLabel('Комментарий',{exact:true}).fill('Изменение проверено <script>текст</script>');await wp.getByRole('button',{name:'Добавить комментарий',exact:true}).click();await expect(wp.locator('.comment')).toContainText('<script>текст</script>');
 await wp.getByLabel('Обоснование',{exact:true}).fill('Сохранить черновик рассмотрения');
 await wp.route('**/review',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Временная недоступность'})}));
 await expect(wp.getByRole('alert').filter({hasText:'Временная недоступность'})).toBeVisible({timeout:15000});
 await expect(wp.getByLabel('Обоснование',{exact:true})).toHaveValue('Сохранить черновик рассмотрения');await wp.unroute('**/review');
 await expect(wp.getByRole('alert').filter({hasText:'Временная недоступность'})).toHaveCount(0,{timeout:15000});await expect(wp.getByLabel('Обоснование',{exact:true})).toHaveValue('Сохранить черновик рассмотрения');
 await wp.getByLabel('Статус',{exact:true}).selectOption('approved');await wp.getByLabel('Обоснование',{exact:true}).fill('Новая модель соответствует заявке на изменение');await wp.getByRole('button',{name:'Сохранить рассмотрение',exact:true}).click();await expect.poll(async()=>(await(await page.request.get(root+'/review')).json()).status).toBe('approved');
 await rp.goto('/?run='+run.id+'&tab=discussion');await expect(rp.getByText('Согласован',{exact:true})).toBeVisible();await expect(rp.getByLabel('Комментарий',{exact:true})).toHaveCount(0);await expect(rp.getByRole('button',{name:'Сохранить рассмотрение',exact:true})).toHaveCount(0);await expect(rp.locator('.comment')).toContainText('Изменение проверено');
 await wp.goto('/?run='+run.id+'&tab=applications&app=A');await reveal(wp,'Комментарии к заявке');await wp.getByLabel('Комментарий',{exact:true}).fill('Пример заявки A');await wp.getByRole('button',{name:'Добавить комментарий',exact:true}).click();await expect(wp.locator('.comment')).toContainText('Пример заявки A');
 await wp.getByRole('button',{name:'Проверки',exact:true}).click();await wp.locator('.check-result-group > summary').first().click();await wp.getByRole('button',{name:'Обсудить',exact:true}).first().click();await wp.getByLabel('Комментарий',{exact:true}).fill('Сигнал ожидаем');await wp.getByRole('button',{name:'Добавить комментарий',exact:true}).click();await expect(wp.locator('.comment')).toContainText('Сигнал ожидаем');
 await openTool(rp,'Сравнение запусков');await rp.getByLabel('Первый запуск',{exact:true}).selectOption(run.id);await rp.getByLabel('Второй запуск',{exact:true}).selectOption(runs[1].id);await rp.getByRole('button',{name:'Сравнить',exact:true}).click();await expect(rp.getByText('Изменились: 1',{exact:false})).toBeVisible();await expect(rp.getByText('Разные датасеты:',{exact:false})).toBeVisible();
 const fake=createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(req.url==='/api/tags'?{models:[{name:'qwen3-server:latest'}]}:{model:'qwen3-server:latest',message:{content:JSON.stringify({summary:'Исторический ответ',signals:'Новая модель X',next_steps:'Проверить изменение'})}}));});await new Promise<void>(resolve=>fake.listen(0,'127.0.0.1',resolve));const ai=await(await page.request.get('/api/admin/ai')).json();
 try{
  await page.request.put('/api/admin/ai',{data:{base_url:`http://127.0.0.1:${(fake.address() as {port:number}).port}`,revision:ai.revision}});
  for(let i=0;i<2;i++){const r=await page.request.post(root+'/ai');expect(r.status()).toBe(202);await completed(page.request,'/api/jobs/'+(await r.json()).job_id);}
  await rp.goto('/?run='+run.id+'&tab=ai');await reveal(rp,'История мнений ИИ');await expect(rp.getByRole('heading',{name:'История мнений ИИ',exact:true})).toBeVisible();expect((await(await reader.request.get(root+'/ai-history')).json()).total).toBe(2);
 }finally{const current=await(await page.request.get('/api/admin/ai')).json();await page.request.put('/api/admin/ai',{data:{base_url:ai.base_url,revision:current.revision}});await new Promise<void>(resolve=>fake.close(()=>resolve()));}
 await page.goto('/?run='+run.id);await page.getByRole('button',{name:'Доступ',exact:true}).click();await page.getByText('Передать владение анализом',{exact:true}).click();await page.getByLabel('Новый владелец (логин)',{exact:true}).fill(bob.username);await page.getByLabel('Причина передачи',{exact:true}).fill('Продолжить сопровождение');await page.getByRole('button',{name:'Передать анализ',exact:true}).click();await expect.poll(async()=>(await(await writer.request.get(root)).json()).can_manage).toBe(true);expect((await writer.request.get(root+'/applications/A/source')).status()).toBe(200);
 await wp.goto('/?run='+run.id);await expect(wp.getByRole('button',{name:'Доступ',exact:true})).toBeVisible();
 await page.goto('/?view=admin');await reveal(page,'Доступ сотрудников и передача анализов');await page.getByLabel('Обоснование действия',{exact:true}).fill('Сотрудник завершил работу');const row=page.getByRole('row').filter({hasText:bob.username}).filter({has:page.getByRole('button',{name:'Заблокировать',exact:true})});await row.getByRole('button',{name:'Заблокировать',exact:true}).click();await expect(wp.getByRole('heading',{name:'Вход в систему',exact:true})).toBeVisible({timeout:15000});expect((await writer.request.get(root)).status()).toBe(401);
 const recovered=await page.request.post(root+'/transfer',{data:{username:admin.username,expected_owner_id:bob.id,retain_previous:false,reason:'Возврат анализа заблокированного сотрудника'}});expect(recovered.status()).toBe(200);
 await page.goto('/?run='+runs[1].id+'&tab=manage');await page.getByRole('button',{name:'В архив',exact:true}).click();await expect(page.getByRole('button',{name:'Восстановить из архива',exact:true})).toBeVisible();await page.getByRole('button',{name:/^Запуски/}).click();await page.getByLabel('Архив',{exact:true}).check();await expect(page.getByRole('button',{name:runs[1].name,exact:true})).toBeVisible();
 await openTool(page,'Хранилище');await page.getByRole('row').filter({hasText:runs[1].name}).getByRole('button',{name:'Очистить',exact:true}).click();await page.getByLabel('Для подтверждения введите: '+runs[1].name,{exact:true}).fill(runs[1].name);await page.getByRole('button',{name:'Удалить окончательно',exact:true}).click();await expect.poll(async()=>(await page.request.get('/api/runs/'+runs[1].id)).status()).toBe(404);
 await page.goto('/?view=admin');await reveal(page,'Резервные копии');await page.getByRole('button',{name:'Создать резервную копию',exact:true}).click();await expect(page.getByRole('link',{name:'Скачать ZIP',exact:true}).first()).toBeVisible({timeout:60000});await page.getByRole('button',{name:'Проверить восстановление',exact:true}).first().click();await expect(page.locator('.backup-facts > div').filter({has:page.locator('dt').filter({hasText:/^API$/})}).first()).toContainText('проверен',{timeout:60000});
 await page.screenshot({path:'../artifacts/operations-admin.png',fullPage:true});await rp.goto('/?run='+run.id+'&tab=discussion');await rp.setViewportSize({width:390,height:844});await rp.screenshot({path:'../artifacts/operations-observer-mobile.png',fullPage:true});expect(await rp.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 expect(errors).toEqual([]);writeFileSync('../artifacts/operations-browser-validation.json',JSON.stringify({passed:true,run_id:run.id,checks:['observer read only','application and check comments','assigned review','two runs with equal change counts','AI snapshots','private dataset transfer','live blocking','archive and cleanup','verified full restore'],errors},null,2));await writer.close();await reader.close();
});
