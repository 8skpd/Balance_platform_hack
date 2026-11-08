import {provisionAccount} from './test-accounts';
import {openTool,openMode,reveal} from './ui-navigation';
import { test, expect, type APIRequestContext } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { createServer } from 'node:http';
import { writeFileSync } from 'node:fs';
const password='Roles-test-password-42';
async function account(request:APIRequestContext,username:string){
  if(username===process.env.BALANCE_TEST_ADMIN_USERNAME){const r=await request.post('/api/auth/login',{data:{username,password:process.env.BALANCE_TEST_ADMIN_PASSWORD}});expect(r.ok()).toBe(true);return r.json();}
  let r=await provisionAccount(request,{data:{username,display_name:username,password}});
  if(r.status()===409)r=await request.post('/api/auth/login',{data:{username,password}});
  expect(r.ok()).toBe(true);return r.json();
}
test('roles, shared private report, live revocation and admin Ollama connection',async({page,browser,baseURL})=>{
  test.setTimeout(180000);page.setDefaultTimeout(12000);
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const adminName=process.env.BALANCE_TEST_ADMIN_USERNAME!;const alice=await account(page.request,adminName);expect(alice.role).toBe('admin');
  const observer=await browser.newContext({baseURL,extraHTTPHeaders:{'X-Balance-Request':'1'}});
  const colleague=await browser.newContext({baseURL,extraHTTPHeaders:{'X-Balance-Request':'1'}});
  const viewer=await observer.newPage();viewer.on('pageerror',e=>errors.push(e.message));
  const bob=await account(observer.request,'roles-observer');await account(colleague.request,'roles-user');
  if(bob.role==='observer')await page.request.put(`/api/admin/users/${bob.id}/role`,{data:{role:'user',expected_role:'observer'}});
  await viewer.goto('/');await expect(viewer.getByRole('button',{name:'Новый запуск',exact:true})).toBeVisible({timeout:12000});
  await page.goto('/');await openTool(page,'Администрирование');
  await page.getByLabel('Роль roles-observer',{exact:true}).selectOption('observer');await page.getByRole('button',{name:'Сохранить роль roles-observer',exact:true}).click();
  await expect(viewer.getByRole('heading',{name:'Доступные анализы',exact:true})).toBeVisible({timeout:12000});
  for(const name of ['Новый запуск','Конфигурация'])await expect(viewer.getByRole('button',{name,exact:true})).toHaveCount(0);
  await page.getByLabel('Роль '+adminName,{exact:true}).selectOption('user');await page.getByRole('button',{name:'Сохранить роль '+adminName,exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('последнего администратора');await page.getByLabel('Роль '+adminName,{exact:true}).selectOption('admin');
  const hits:string[]=[];
  const fake=createServer((req,res)=>{
    hits.push(req.url||'');res.setHeader('Content-Type','application/json');
    if(req.url==='/api/tags')res.end(JSON.stringify({models:[{name:'qwen3-server:latest'}]}));
    else if(req.url==='/api/chat')res.end(JSON.stringify({message:{content:JSON.stringify({summary:'QA: одна заявка изменилась.',signals:'Изменение тестового значения.',next_steps:'Проверить правило.'})},done_reason:'stop'}));
    else {res.statusCode=404;res.end('{}');}
  });
  await new Promise<void>(resolve=>fake.listen(0,'127.0.0.1',resolve));
  const aiUrl=`http://127.0.0.1:${(fake.address() as {port:number}).port}`;
  try{
    const initial=await(await page.request.get('/api/admin/ai')).json();
    await reveal(page,'Подключение к ИИ');await page.getByLabel('Адрес Ollama',{exact:true}).fill(aiUrl+'/api/chat');await page.getByRole('button',{name:'Проверить соединение',exact:true}).click();
    await expect(page.getByRole('status')).toContainText('найдена');expect((await(await page.request.get('/api/admin/ai')).json()).base_url).toBe(initial.base_url);
    await page.getByRole('button',{name:'Сохранить настройки',exact:true}).click();await expect(page.getByLabel('Адрес Ollama',{exact:true})).toHaveValue(aiUrl);
    await page.screenshot({path:'../artifacts/roles-admin-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:'../artifacts/roles-admin-mobile.png',fullPage:true});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.setViewportSize({width:1440,height:1000});
    const zip=execFileSync('../.venv/bin/python',['-c',`import io,json,zipfile,sys
b=io.BytesIO()
with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
 for n,side in enumerate(['BEFORE_MASTER','AFTER_BETA']):z.writestr('RESULTS/'+side+'/A.json',json.dumps({'appData':{'application_id':'A'},'value':n}))
sys.stdout.buffer.write(b.getvalue())`]);
    const upload=await page.request.post('/api/dataset-imports?name=Приватный&filename=roles.zip',{data:zip,headers:{'Content-Type':'application/zip'}});expect(upload.status()).toBe(202);const dataset=(await upload.json()).id;
    await expect.poll(async()=>{const r=await(await page.request.get('/api/dataset-imports/'+dataset)).json();if(r.status==='failed')throw new Error(r.error);return r.status;},{timeout:30000,intervals:[1000]}).toBe('completed');
    const config=await(await page.request.get('/api/config')).json();
    const creation=await page.request.post('/api/runs',{data:{name:'Общий анализ '+Date.now(),dataset,config,builtin_checks:[],user_checks:[]}});expect(creation.status()).toBe(201);const run=await creation.json();const root='/api/runs/'+run.id;
    await expect.poll(async()=>{const r=await(await page.request.get(root)).json();if(r.status==='failed')throw new Error(r.error);return r.status;},{timeout:30000,intervals:[1000]}).toBe('completed');
    expect((await observer.request.get(root)).status()).toBe(404);
    await page.goto('/?run='+run.id);await page.getByRole('button',{name:'Доступ',exact:true}).click();
    for(const name of ['roles-observer','roles-user']){await page.getByLabel('Логин получателя',{exact:true}).fill(name);await page.getByRole('button',{name:'Предоставить просмотр',exact:true}).click();await expect(page.locator('.access-members')).toContainText('@'+name);}
    await page.screenshot({path:'../artifacts/roles-sharing.png',fullPage:true});await page.getByRole('button',{name:'Закрыть доступ',exact:true}).click();
    await expect(viewer.getByRole('button',{name:run.name,exact:true})).toBeVisible();await viewer.getByRole('button',{name:run.name,exact:true}).click();
    await expect(viewer.getByText('Только просмотр',{exact:true})).toBeVisible();
    for(const name of ['Python','Журнал','Совместный доступ'])await expect(viewer.getByRole('button',{name,exact:true})).toHaveCount(0);
    await viewer.getByRole('button',{name:'Сводная таблица',exact:true}).click();await expect(viewer.getByRole('heading',{name:'Результат сводной',exact:true})).toBeVisible();await expect(viewer.getByLabel('Название представления',{exact:true})).toHaveCount(0);
    await viewer.getByRole('button',{name:'Добавить фильтр',exact:true}).click();await viewer.getByLabel('Значение фильтра 1',{exact:true}).fill('value');await viewer.getByRole('button',{name:'Построить сводную',exact:true}).click();
    await expect(viewer.locator('.pivot-result')).toContainText('value');await viewer.locator('.pivot-result .number-link').first().click();await viewer.getByRole('button',{name:'A',exact:true}).click();
    await viewer.getByRole('button',{name:'Исходный JSON',exact:true}).click();await expect(viewer.getByRole('heading',{name:'Исходные ответы СПР',exact:true})).toBeVisible();
    expect((await observer.request.get(root+'/applications/A/source')).status()).toBe(200);expect((await observer.request.get(root+'/report.html')).status()).toBe(200);
    expect((await observer.request.post(root+'/ai')).status()).toBe(403);expect((await observer.request.post('/api/runs',{data:{dataset,config}})).status()).toBe(403);
    expect((await colleague.request.post(root+'/ai')).status()).toBe(403);expect((await colleague.request.post('/api/runs',{data:{dataset,config}})).status()).toBe(404);
    await viewer.getByRole('button',{name:'Мнение ИИ',exact:true}).click();await expect(viewer.getByRole('button',{name:'Получить мнение',exact:true})).toHaveCount(0);
    await page.getByRole('button',{name:'Мнение ИИ',exact:true}).click();await page.getByLabel('Сообщение ИИ',{exact:true}).fill('Проанализируй отчёт.');await page.getByRole('button',{name:'Отправить сообщение',exact:true}).click();
    await expect(viewer.locator('.chat-message-body')).toContainText('QA: одна заявка изменилась.',{timeout:20000});expect(hits).toContain('/api/chat');
    await viewer.screenshot({path:'../artifacts/roles-observer-desktop.png',fullPage:true});await viewer.setViewportSize({width:390,height:844});await viewer.screenshot({path:'../artifacts/roles-observer-mobile.png',fullPage:true});expect(await viewer.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.getByRole('button',{name:'Доступ',exact:true}).click();await page.getByRole('button',{name:'Отозвать доступ roles-observer',exact:true}).click();await page.getByRole('button',{name:'Закрыть доступ',exact:true}).click();await expect(viewer.getByRole('heading',{name:'Отчёт недоступен',exact:true})).toBeVisible();await expect(viewer.locator('.chat-message-body')).toHaveCount(0);expect((await observer.request.get(root+'/report.html')).status()).toBe(404);
    await viewer.goto('/?view=settings');await expect(viewer.getByRole('heading',{name:'Доступ ограничен',exact:true})).toBeVisible();await viewer.goto('/');
    await openTool(page,'Администрирование');await page.getByLabel('Роль roles-observer',{exact:true}).selectOption('user');await page.getByRole('button',{name:'Сохранить роль roles-observer',exact:true}).click();await expect(viewer.getByRole('button',{name:'Новый запуск',exact:true})).toBeVisible({timeout:12000});
    const settings=await(await page.request.get('/api/admin/ai')).json();await page.request.put('/api/admin/ai',{data:{base_url:initial.base_url,revision:settings.revision}});
    expect(errors).toEqual([]);writeFileSync('../artifacts/roles-validation.json',JSON.stringify({passed:true,run_id:run.id,checks:['live roles','last admin protection','Ollama save/test and queued generation','shared private dataset report','interactive pivot and raw JSON','observer and user write denial','live access revocation','mobile layout'],errors},null,2));
  }finally{await new Promise<void>((resolve,reject)=>fake.close(e=>e?reject(e):resolve()));await observer.close();await colleague.close();}
});
