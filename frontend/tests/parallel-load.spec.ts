import {provisionAccount} from './test-accounts';
import {test,expect} from '@playwright/test';
import {readFileSync,writeFileSync} from 'node:fs';
async function register(page:any){expect((await provisionAccount(page.request,{data:{username:'parallel-'+Date.now(),display_name:'Параллельная обработка QA',password:'Parallel-browser-password-42'}})).status()).toBe(201);}
async function ready(page:any,url:string){await expect.poll(async()=>{const r=await(await page.request.get(url)).json();if(['failed','timed_out'].includes(r.status))throw new Error(r.error);return r.status;},{timeout:90000,intervals:[1000]}).toBe('completed');return (await page.request.get(url)).json();}
test('packed 5000-pair dataset runs all checks in multiple processes and exposes source and preview',async({page})=>{
 await register(page);const expected=JSON.parse(readFileSync('../artifacts/load-5000-qa.json','utf-8')).expected;
 const uploaded=await page.request.post('/api/dataset-imports?name=Parallel-5000&filename=5000.zip',{data:readFileSync('../artifacts/load-5000-qa.zip'),headers:{'Content-Type':'application/zip'}});expect(uploaded.status()).toBe(202);const dataset=(await uploaded.json()).id;
 const imported=await ready(page,'/api/dataset-imports/'+dataset);expect(imported.stats.storage_format).toBe('sqlite');expect(imported.stats.pairs).toBe(5000);
 const config=await(await page.request.get('/api/config')).json();const preview=await page.request.post(`/api/datasets/${dataset}/normalization-preview`,{data:{config,app_id:'LOAD0005000'}});expect(preview.status()).toBe(200);expect((await preview.json()).pairs).toBe(5000);
 const created=await page.request.post('/api/runs',{data:{name:'Параллельные 5000',dataset,config}});expect(created.status()).toBe(201);const id=(await created.json()).id;const run=await ready(page,'/api/runs/'+id);
 for(const key of ['total_apps','changed_apps','decision_changed'])expect(run.summary[key]).toBe(expected[key]);expect(run.summary.processing.workers).toBeGreaterThan(1);expect(run.summary.processing.processes).toBeGreaterThan(1);expect(run.check_executions).toHaveLength(5);expect(run.check_executions.every((c:any)=>c.status==='completed')).toBe(true);
 await page.goto('/?run='+id);await expect(page.getByRole('heading',{name:'Параллельные 5000',exact:true})).toBeVisible();const source=await(await page.request.get(`/api/runs/${id}/applications/LOAD0005000/source`)).json();expect(source.BEFORE.appData.application_id).toBe('LOAD0005000');
 writeFileSync('../artifacts/parallel-5000-qa-validation.json',JSON.stringify({run_id:id,dataset_id:dataset,summary:run.summary,checks:run.checks},null,2));
});
test('one combined CSV or XML uploads through the interface',async({page})=>{
 await register(page);
 for(const extension of ['csv','xml']){
  await page.goto('/?view=datasets');await page.getByLabel('Способ загрузки').selectOption('single');await page.getByLabel('Общий CSV / XML').setInputFiles('public/examples/dataset.'+extension);await page.getByLabel('Название датасета').fill('Общий '+extension);
  const response=page.waitForResponse(r=>r.url().includes('/api/dataset-imports?')&&r.request().method()==='POST');await page.getByRole('button',{name:'Загрузить и проверить'}).click();const res=await response;expect(res.status()).toBe(202);const imported=await res.json();await ready(page,'/api/dataset-imports/'+imported.id);await expect(page.locator('.dataset-card').filter({has:page.getByRole('heading',{name:'Общий '+extension,exact:true})})).toBeVisible();
 }
 await page.screenshot({path:'../artifacts/parallel-upload-options.png',fullPage:true});
});
