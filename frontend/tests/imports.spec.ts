import {provisionAccount} from './test-accounts';
import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';

function zip(missing=false) {
  return execFileSync('../.venv/bin/python',['-c',`
import io,json,zipfile,sys
b=io.BytesIO()
with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
 for n,side in enumerate(['BEFORE_MASTER'] if ${missing?'True':'False'} else ['BEFORE_MASTER','AFTER_BETA']):
  z.writestr('RESULTS/'+side+'/A.json',json.dumps({'appData':{'application_id':'A'},'decisionResult':{'decision':'APPROVE' if n else 'DECLINE'},'score':n}))
sys.stdout.buffer.write(b.getvalue())
`]);
}

test('upload ZIP, validate, compare, reject missing pairs and isolate ownership',async({page,browser,baseURL})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const account=await provisionAccount(page.request,{data:{username:'upload-'+Date.now(),display_name:'Импорт QA',password:'Upload-browser-password-42'}});
  expect(account.status()).toBe(201);
  await page.goto('/?view=datasets');
  await page.getByLabel('ZIP-архив').setInputFiles({name:'my-data.zip',mimeType:'application/zip',buffer:zip()});
  await page.getByLabel('Название датасета').fill('Мой датасет');
  const response=page.waitForResponse(r=>r.url().includes('/api/dataset-imports?')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Загрузить и проверить'}).click();
  const imported=await(await response).json();
  const card=page.locator('.dataset-card').filter({has:page.getByRole('heading',{name:'Мой датасет',exact:true})});
  await expect(card).toBeVisible({timeout:30000});
  await expect(page.locator(`[data-import-id="${imported.id}"]`)).toContainText('Готов');
  await expect(card).toContainText('Только мне');
  await page.reload();await expect(card).toBeVisible();
  await card.getByRole('button',{name:'Запустить сравнение'}).click();
  await expect(page.getByRole('combobox',{name:'Датасет',exact:true})).toHaveValue(imported.id);
  await page.getByLabel('Название запуска').fill('Расчёт загруженного набора');
  await page.getByRole('button',{name:'Запустить',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Расчёт загруженного набора',exact:true})).toBeVisible();
  const runId=new URL(page.url()).searchParams.get('run');
  await expect.poll(async()=>{const r=await(await page.request.get(`/api/runs/${runId}`)).json();if(r.status==='failed')throw new Error(r.error);return r.status;},{timeout:45000,intervals:[1000]}).toBe('completed');
  const run=await(await page.request.get(`/api/runs/${runId}`)).json();expect(run.summary.total_apps).toBe(1);expect(run.summary.changed_apps).toBe(1);
  const source=await page.request.get(`/api/runs/${runId}/applications/A/source`);expect(source.status()).toBe(200);expect((await source.json()).AFTER.score).toBe(1);
  const other=await browser.newContext({baseURL,extraHTTPHeaders:{'X-Balance-Request':'1'}});
  await provisionAccount(other.request,{data:{username:'upload-other-'+Date.now(),display_name:'Other',password:'Upload-browser-password-42'}});
  expect((await other.request.get('/api/dataset-imports/'+imported.id)).status()).toBe(404);
  expect((await other.request.get(`/api/runs/${runId}/applications/A/source`)).status()).toBe(404);
  const visible=await(await other.request.get('/api/datasets')).json();expect(visible.some((d:any)=>d.id===imported.id)).toBe(false);expect(visible.some((d:any)=>d.visibility==='shared')).toBe(true);await other.close();
  await page.goto('/?view=datasets');
  await page.getByLabel('ZIP-архив').setInputFiles({name:'missing.zip',mimeType:'application/zip',buffer:zip(true)});
  await page.getByLabel('Название датасета').fill('Непарный набор');
  const badResponse=page.waitForResponse(r=>r.url().includes('/api/dataset-imports?')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Загрузить и проверить'}).click();
  const bad=await(await badResponse).json();const badCard=page.locator(`[data-import-id="${bad.id}"]`);
  await expect(badCard).toContainText('Непарные заявки',{timeout:30000});
  const retryResponse=page.waitForResponse(r=>r.url().endsWith('/retry'));
  await badCard.getByRole('button',{name:'Повторить проверку'}).click();const retry=await(await retryResponse).json();expect(retry.dataset_id).not.toBe(bad.id);
  await expect(page.locator(`[data-import-id="${retry.dataset_id}"]`)).toContainText('Непарные заявки',{timeout:30000});
  await badCard.getByRole('link',{name:'Подробности задачи'}).click();
  await expect(page.locator('.job-detail')).toContainText('Импорт датасета');
  await expect(page.locator('.job-detail')).toContainText('Непарные заявки');
  await page.goto('/?view=datasets');
  await expect(card).toBeVisible();
  await page.screenshot({path:'../artifacts/datasets-import-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.locator('.upload-panel').evaluate(panel=>[...panel.querySelectorAll('input')].every(input=>input.getBoundingClientRect().right<panel.getBoundingClientRect().right))).toBe(true);
  await expect(page.locator('.import-history')).not.toContainText('raise ValueError');
  await page.screenshot({path:'../artifacts/datasets-import-mobile.png',fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});
