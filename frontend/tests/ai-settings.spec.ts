import {test,expect} from '@playwright/test';
import {createServer} from 'node:http';

test('AI settings show progress and local results, discover models and persist selection',async({page})=>{
 const username=process.env.BALANCE_TEST_ADMIN_USERNAME,password=process.env.BALANCE_TEST_ADMIN_PASSWORD;
 if(!username||!password)throw Error('Set administrator credentials for an isolated test installation');
 const auth=await page.request.post('/api/auth/login',{data:{username,password}});
 expect(auth.ok()).toBeTruthy();expect((await auth.json()).role).toBe('admin');
 let mode:'ok'|'timeout'='ok';
 const server=createServer((req,res)=>{const captured=mode;setTimeout(()=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({models:[{name:'qwen3-server:latest'},{name:'alternative:latest'}]}));},captured==='timeout'?6000:700);});
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
 const url=`http://127.0.0.1:${(server.address() as {port:number}).port}`;
 const original=await(await page.request.get('/api/admin/ai')).json();
 try{
  await page.goto('/?view=admin');await page.locator('summary').filter({hasText:/^Подключение к ИИ$/}).click();
  const panel=page.getByRole('region',{name:'Подключение к ИИ',exact:true});
  await panel.getByLabel('Адрес Ollama',{exact:true}).fill(url+'/api/chat');await panel.getByLabel('Модель Ollama',{exact:true}).fill('alternative:latest');
  await panel.getByRole('button',{name:'Проверить соединение',exact:true}).click();
  await expect(panel.getByRole('button',{name:'Проверяем соединение…',exact:true})).toBeDisabled();
  await expect(panel.getByRole('status')).toContainText('Проверяем сервер');await expect(panel.getByLabel('Модель Ollama')).toBeDisabled();
  await expect(panel.getByRole('status')).toContainText('Модель alternative:latest найдена.');
  expect(await panel.locator('datalist option').evaluateAll(options=>options.map(o=>o.getAttribute('value')))).toEqual(['alternative:latest','qwen3-server:latest']);
  expect(await(await page.request.get('/api/admin/ai')).json()).toEqual(original);
  await panel.getByRole('button',{name:'Сохранить настройки',exact:true}).click();await expect(panel.getByRole('status')).toContainText('Настройки сохранены');
  await page.reload();await page.locator('summary').filter({hasText:/^Подключение к ИИ$/}).click();await expect(panel.getByLabel('Модель Ollama')).toHaveValue('alternative:latest');await expect(panel.getByLabel('Адрес Ollama')).toHaveValue(url);
  expect((await(await page.request.get('/api/ollama')).json()).model).toBe('alternative:latest');
  await panel.getByLabel('Модель Ollama').fill('missing');await panel.getByRole('button',{name:'Проверить соединение',exact:true}).click();await expect(panel.getByRole('status')).toContainText('не найдена');
  await panel.screenshot({path:'../artifacts/ai-settings-desktop.png'});
  mode='timeout';await panel.getByRole('button',{name:'Проверить соединение',exact:true}).click();await expect(panel.getByRole('alert')).toContainText('5 секунд',{timeout:10000});await expect(panel.getByRole('button',{name:'Проверить соединение',exact:true})).toBeEnabled();
  await panel.getByLabel('Модель Ollama').fill('alternative:latest');await expect(panel.getByRole('alert')).toHaveCount(0);mode='ok';
  await page.setViewportSize({width:390,height:844});await panel.getByRole('button',{name:'Проверить соединение',exact:true}).click();await expect(panel.getByRole('status')).toContainText('найдена');expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();await panel.screenshot({path:'../artifacts/ai-settings-mobile.png'});
 }finally{
  const current=await(await page.request.get('/api/admin/ai')).json();await page.request.put('/api/admin/ai',{data:{base_url:original.base_url,model:original.model,revision:current.revision}});
  server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));
 }
});
