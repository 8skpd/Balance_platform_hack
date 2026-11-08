import {provisionAccount} from './test-accounts';
import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';

test('personal templates pin check versions, detect conflicts and preserve reports after deletion',async({page,browser,baseURL})=>{
  page.setDefaultTimeout(15000);
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  expect((await provisionAccount(page.request,{data:{username:'templates-'+Date.now(),display_name:'Шаблоны QA',password:'Templates-test-password-42'}})).status()).toBe(201);
  const zip=execFileSync('../.venv/bin/python',['-c',`import io,json,zipfile,sys
b=io.BytesIO()
with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
 for n,side in enumerate(['BEFORE_MASTER','AFTER_BETA']):z.writestr('RESULTS/'+side+'/A.json',json.dumps({'appData':{'application_id':'A'},'value':n}))
sys.stdout.buffer.write(b.getvalue())`]);
  const uploaded=await page.request.post('/api/dataset-imports?name=Набор%20шаблона&filename=template.zip',{data:zip,headers:{'Content-Type':'application/zip'}});expect(uploaded.status()).toBe(202);const dataset=(await uploaded.json()).id;
  await expect.poll(async()=>{const r=await(await page.request.get('/api/dataset-imports/'+dataset)).json();if(r.status==='failed')throw new Error(r.error);return r.status;},{timeout:30000,intervals:[1000]}).toBe('completed');
  const code=`def run(mart, params):
    return [{'LABEL': params['label'], 'ARGUMENT':'All', 'SOURCE':'AFTER', 'CNT':1, 'TOTAL':1, 'METRIC':1, 'RESULT':'PASS', 'CONFIDENCE':'HIGH', 'APP_IDS':['A']}]
CHECK = {'entrypoint':run}`;
  const v1=await(await page.request.post('/api/check-library',{data:{name:'Проверка шаблона',code,params:{label:'Версия один'}}})).json();
  await page.goto('/');await page.getByRole('button',{name:'Новый запуск',exact:true}).click();
  await page.getByLabel('Название запуска',{exact:true}).fill('Запуск из шаблона');
  await page.getByRole('combobox',{name:'Датасет',exact:true}).selectOption(dataset);
  await expect(page.locator('.check-picker input[type=checkbox]').first()).toBeVisible();
  for(const box of await page.locator('.check-picker input[type=checkbox]').all())await box.uncheck();
  await page.getByRole('checkbox',{name:'Включить Проверка шаблона',exact:true}).check();
  await page.locator('.run-templates > summary').click();
  await page.getByLabel('Название шаблона',{exact:true}).fill('Мой сценарий');
  const creation=page.waitForResponse(r=>r.url().endsWith('/api/run-templates')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Сохранить как новый шаблон',exact:true}).click();const template=await(await creation).json();
  expect(template.spec.user_checks[0].version_id).toBe(v1.id);expect(template.spec.builtin_checks).toEqual([]);
  expect(await(await page.request.get('/api/runs')).json()).toEqual([]);
  await expect(page.locator('.template-notice')).toContainText('Шаблон сохранён');
  const v2=await page.request.post(`/api/check-library/${v1.check_id}/versions`,{data:{name:'Проверка шаблона',code,params:{label:'Версия два'},expected_version:1}});expect(v2.status()).toBe(201);
  await page.getByRole('button',{name:'Закрыть',exact:true}).click();
  await page.getByRole('button',{name:'Новый запуск',exact:true}).click();await page.locator('.run-templates > summary').click();
  await page.getByLabel('Мои шаблоны',{exact:true}).selectOption(template.id);
  await page.getByRole('button',{name:'Применить шаблон',exact:true}).click();
  await expect(page.getByLabel('Название запуска',{exact:true})).toHaveValue('Запуск из шаблона');
  await expect(page.getByLabel('Версия Проверка шаблона',{exact:true})).toHaveValue(v1.id);
  await expect(page.getByRole('combobox',{name:'Датасет',exact:true})).toHaveValue(dataset);
  // A different tab changes the saved template, while the current form remains editable.
  expect((await page.request.put('/api/run-templates/'+template.id,{data:{name:'Из другой вкладки',spec:template.spec,revision:1}})).status()).toBe(200);
  await page.getByLabel('Название запуска',{exact:true}).fill('Мой несохранённый вариант');
  await page.getByRole('button',{name:'Обновить шаблон',exact:true}).click();
  await expect(page.locator('.run-templates .error-box')).toContainText('другой вкладке');
  await expect(page.getByLabel('Название запуска',{exact:true})).toHaveValue('Мой несохранённый вариант');
  await page.getByRole('button',{name:'Применить шаблон',exact:true}).click();
  await expect(page.getByLabel('Мои шаблоны',{exact:true}).locator('option:checked')).toContainText('Из другой вкладки');
  await page.getByLabel('Название шаблона',{exact:true}).fill('Обновлённый сценарий');
  await page.getByRole('button',{name:'Обновить шаблон',exact:true}).click();await expect(page.locator('.template-notice')).toContainText('Шаблон сохранён');
  await page.locator('.template-composition > summary').click();
  await page.screenshot({path:'../artifacts/templates-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'../artifacts/templates-mobile.png',fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.setViewportSize({width:1440,height:1000});
  const other=await browser.newContext({baseURL,extraHTTPHeaders:{'X-Balance-Request':'1'}});
  await provisionAccount(other.request,{data:{username:'templates-other-'+Date.now(),display_name:'Other',password:'Templates-test-password-42'}});
  expect(await(await other.request.get('/api/run-templates')).json()).toEqual([]);
  expect((await other.request.get('/api/run-templates/'+template.id)).status()).toBe(404);await other.close();
  await page.getByRole('button',{name:'Запустить',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Запуск из шаблона',exact:true})).toBeVisible();
  const runId=new URL(page.url()).searchParams.get('run');
  await expect.poll(async()=>{const r=await(await page.request.get('/api/runs/'+runId)).json();if(r.status==='failed')throw new Error(r.error);return r.status;},{timeout:45000,intervals:[1000]}).toBe('completed');
  const report=await(await page.request.get('/api/runs/'+runId)).json();expect(report.check_plan[0].version_id).toBe(v1.id);expect(report.checks[0].LABEL).toBe('Версия один');
  await page.request.post(`/api/check-library/${v1.check_id}/archive`,{data:{archived:true}});
  await page.goto('/');
  await page.getByRole('button',{name:'Новый запуск',exact:true}).click();await page.locator('.run-templates > summary').click();
  await page.getByLabel('Мои шаблоны',{exact:true}).selectOption(template.id);await page.getByRole('button',{name:'Применить шаблон',exact:true}).click();
  await expect(page.locator('.run-templates .error-box')).toContainText('Архивная');
  await page.getByRole('button',{name:'Убрать недоступную проверку',exact:true}).click();
  await page.getByRole('button',{name:'Обновить шаблон',exact:true}).click();await expect(page.locator('.template-notice')).toContainText('Шаблон сохранён');
  await page.getByRole('button',{name:'Удалить шаблон',exact:true}).click();await page.getByRole('button',{name:'Подтвердить удаление',exact:true}).click();
  await expect(page.locator('.template-notice')).toContainText('Шаблон удалён');
  expect(await(await page.request.get('/api/run-templates')).json()).toEqual([]);
  expect((await(await page.request.get('/api/runs/'+runId)).json()).check_plan).toEqual(report.check_plan);
  expect(errors).toEqual([]);
});
