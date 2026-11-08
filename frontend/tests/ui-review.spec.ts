import {provisionAccount} from './test-accounts';
import {test,expect} from '@playwright/test';
import {zipSync,strToU8} from 'fflate';

async function register(page:any){const r=await provisionAccount(page.request,{data:{username:'ui-review-'+Date.now(),display_name:'Проверка интерфейса',password:'Ui-review-password-42'}});expect(r.status()).toBe(201);return r.json();}
async function dataset(page:any){const files:Record<string,Uint8Array>={};for(const side of ['BEFORE_MASTER','AFTER_BETA'])files[`RESULTS/${side}/A.json`]=strToU8(JSON.stringify({appData:{application_id:'A'},value:side==='BEFORE_MASTER'?1:2,stable:true}));const r=await page.request.post('/api/dataset-imports?name=Проверка%20интерфейса&filename=ui.zip',{data:Buffer.from(zipSync(files))});expect(r.status()).toBe(202);const upload=await r.json();await expect.poll(async()=>(await(await page.request.get('/api/jobs/'+upload.job_id)).json()).status,{timeout:30000}).toBe('completed');return upload.id;}

test('same readable check contract in library, setup and report editor',async({page})=>{
 await register(page);await page.goto('/?view=library');await page.getByRole('group',{name:'Раздел библиотеки'}).getByRole('button',{name:'Проверки',exact:true}).click();
 const library=page.locator('.check-library-page');await library.locator('.procedure-contract > summary').click();const contract=library.locator('.contract-document');await expect(contract.getByRole('heading',{name:'Входные данные',exact:true})).toBeVisible();await expect(contract.getByRole('cell',{name:'mart.count(**filters)',exact:true})).toBeVisible();await expect(contract).toContainText('Количество строк сравнения');const libraryText=(await contract.textContent())!;
 await contract.screenshot({path:'../artifacts/contract-check-desktop.png'});await page.setViewportSize({width:390,height:844});await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await contract.screenshot({path:'../artifacts/contract-check-mobile.png'});await page.setViewportSize({width:1440,height:1000});
 await page.goto('/');await page.getByRole('button',{name:'Настройка анализа',exact:true}).click();const modal=page.getByRole('dialog',{name:'Настройка анализа'});await modal.getByRole('tab').last().click();await modal.getByRole('button',{name:'Свой Python',exact:true}).click();await modal.locator('.setup-pane:visible .procedure-contract > summary').click();const setup=modal.locator('.setup-pane:visible .contract-document');await expect(setup).toContainText(libraryText);await setup.screenshot({path:'../artifacts/contract-check-setup.png'});
 // Opening documentation must never replace the draft; example insertion is guarded.
 const editor=modal.getByRole('textbox',{name:'Код Python',exact:true});await editor.fill('def run(mart, params):\n    return []\nCHECK={"entrypoint":run}\n# Мой код');await setup.getByRole('button',{name:'Вставить стандартный пример'}).click();await page.getByRole('dialog',{name:'Заменить текущий код?'}).getByRole('button',{name:'Отмена',exact:true}).click();await expect(editor).toContainText('Мой код');
 const run={id:'doc-report',name:'Документация проверки',status:'completed',stage:'complete',progress:100,created_at:'2026-11-08T04:00:00Z',dataset:'demo',summary:{total_apps:1,changed_apps:1},events:[],check_plan:[],checks:[],ai:{status:'idle'},can_manage:true};await page.route('**/api/runs/doc-report',r=>r.fulfill({json:run}));await page.route('**/api/jobs?run_id=doc-report',r=>r.fulfill({json:[]}));await page.goto('/?run=doc-report&tab=python');await page.locator('.python-panel .procedure-contract > summary').click();await expect(page.locator('.python-panel .contract-document')).toContainText(libraryText);await expect(page.getByRole('button',{name:'Загрузить .py',exact:true})).toBeVisible();
});

test('all main screens and report modes fit desktop and mobile',async({page})=>{
 const user=await register(page),id=await dataset(page);const config=await(await page.request.get('/api/config')).json();
 const r=await page.request.post('/api/runs',{data:{dataset:id,name:'Проверка экранов',config,builtin_checks:[]}});expect(r.status()).toBe(201);const run=await r.json();await expect.poll(async()=>(await(await page.request.get('/api/runs/'+run.id)).json()).status,{timeout:60000}).toBe('completed');
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 const routes=['?view=overview','?view=runs','?view=datasets','?view=library','?view=jobs','?view=compare','?view=storage',...['overview','pivot','applications','checks','python','journal','discussion','ai','manage'].map(tab=>`?run=${run.id}&tab=${tab}`)];
 for(const width of [1440,390]){await page.setViewportSize({width,height:1000});for(const [i,route] of routes.entries()){
  await page.goto('/'+route);await expect(page.locator('main')).toBeVisible();await page.waitForTimeout(500);await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),{message:route+' at '+width}).toBe(true);await expect(page.locator('body')).not.toContainText('Internal Server Error');for(const field of await page.locator('.operations-panel .field input,.operations-panel .field select').all()){const bounds=await field.evaluate(el=>{const panel=el.closest('.operations-panel')!.getBoundingClientRect(),r=el.getBoundingClientRect();return {visible:r.width>0,inside:r.left>=panel.left&&r.right<=panel.right};});expect(!bounds.visible||bounds.inside,route+' field fits its card').toBe(true);}await page.screenshot({path:`../artifacts/ui-review/${width}-${i}.png`,fullPage:true});
 }}
 expect(errors).toEqual([]);
});

test('administration panels fit narrow screens',async({page})=>{
 const user=await register(page);await page.route('**/api/auth/me',r=>r.fulfill({json:{...user,role:'admin'}}));
 await page.route('**/api/admin/users',r=>r.fulfill({json:[{...user,role:'admin',blocked:false}]}));
 await page.route('**/api/admin/ai',r=>r.fulfill({json:{base_url:'http://192.168.1.76:11434',model:'qwen3-server:latest',revision:1}}));
 await page.route('**/api/admin/limits',r=>r.fulfill({json:{max_active_jobs:3,max_runs:100,max_user_storage_mb:10240,max_job_storage_mb:4096,min_free_mb:1024}}));
 await page.route('**/api/admin/audit?*',r=>r.fulfill({json:{items:[],total:0,limit:20}}));
 await page.route('**/api/admin/backups',r=>r.fulfill({json:[{id:'create',kind:'create',archive_id:'copy',created_at:'2026-11-08T05:00:00',status:'completed',result:{bytes:23173529,files:4034}}]}));
 await page.goto('/?view=admin');await expect(page.getByRole('heading',{name:'Пользователи и роли'})).toBeVisible();
 for(const title of ['Подключение к ИИ','Доступ сотрудников и передача анализов','Квоты хранения','Резервные копии','Журнал действий'])await page.locator('summary').filter({hasText:new RegExp('^'+title+'$')}).click();
 for(const width of [1440,390]){await page.setViewportSize({width,height:1000});await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:`../artifacts/ui-review/admin-${width}.png`,fullPage:true});}
});
