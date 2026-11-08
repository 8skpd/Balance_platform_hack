import {provisionAccount} from './test-accounts';
import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';

test('visual rules, live preview, JSON roundtrip and per-run configuration snapshot',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const r=await provisionAccount(page.request,{data:{username:'normalization-'+Date.now(),display_name:'Правила QA',password:'Normalization-test-password-42'}});expect(r.status()).toBe(201);
  const archive=execFileSync('../.venv/bin/python',['-c',`
import io,json,zipfile,sys
b=io.BytesIO()
with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
 for n,side in enumerate(['BEFORE_MASTER','AFTER_BETA']):
  items=[{'id':'p1','value':1},{'id':'p2','value':2}]
  doc={'appData':{'application_id':'A'},'decisionResult':{'decision':'APPROVE'},'items':items[::-1] if n else items,'score':.58 if n else .42,'ignore':n}
  z.writestr('RESULTS/'+side+'/A.json',json.dumps(doc))
sys.stdout.buffer.write(b.getvalue())
`]);
  const upload=await page.request.post('/api/dataset-imports?name=Правила%20массивов&filename=rules.zip',{data:archive,headers:{'Content-Type':'application/zip'}});expect(upload.status()).toBe(202);
  const dataset=(await upload.json()).id;
  await expect.poll(async()=>{const d=await(await page.request.get('/api/dataset-imports/'+dataset)).json();if(d.status==='failed')throw new Error(d.error);return d.status;},{timeout:30000,intervals:[1000]}).toBe('completed');
  expect((await page.request.put('/api/config',{headers:{'If-Match':(await page.request.get('/api/config')).headers()['etag']},data:{exclude_paths:[],arrays:{},buckets:{}}})).status()).toBe(200);
  await page.goto('/?view=settings');
  await page.getByLabel('Датасет для предпросмотра',{exact:true}).selectOption(dataset);
  await page.getByRole('button',{name:'Проверить правила',exact:true}).click();
  await expect(page.locator('.normalization-preview-result')).toContainText('Не задан режим сравнения массива: items');
  await page.getByRole('button',{name:'Добавить массив',exact:true}).click();
  await page.getByLabel('Путь массива 1',{exact:true}).fill('items');
  await page.getByLabel('Ключи массива 1',{exact:true}).fill('id');
  await page.getByRole('button',{name:'Добавить исключение'}).click();await page.getByLabel('Путь исключения 1',{exact:true}).fill('ignore');
  await page.getByRole('button',{name:'Добавить интервалы'}).click();await page.getByLabel('Путь интервала 1',{exact:true}).fill('score');
  await page.getByLabel('Границы интервала 1',{exact:true}).fill('0.6, 0.3');
  await expect(page.getByRole('button',{name:'Сохранить',exact:true})).toBeDisabled();
  await page.getByLabel('Границы интервала 1',{exact:true}).fill('0.3, 0.6, 1');
  await page.getByLabel('Порог падения одобрений',{exact:true}).fill('0.12');
  await page.getByRole('button',{name:'Проверить правила',exact:true}).click();
  await expect(page.locator('.normalization-preview-result')).toContainText('изменилось: 1');
  await expect(page.locator('.normalization-preview-result')).toContainText('Интервал: (0.3; 0.6]');
  await page.getByRole('button',{name:'JSON',exact:true}).click();
  const raw=await page.getByLabel('JSON конфигурации').inputValue();expect(JSON.parse(raw).arrays.items.keys).toEqual(['id']);
  await page.getByLabel('JSON конфигурации').fill('{broken');
  await page.getByRole('button',{name:'Визуальный редактор',exact:true}).click();
  await expect(page.getByLabel('JSON конфигурации')).toBeVisible();
  await page.getByLabel('JSON конфигурации').fill(raw);
  await page.getByRole('button',{name:'Визуальный редактор',exact:true}).click();
  await expect(page.getByLabel('Ключи массива 1',{exact:true})).toHaveValue('id');
  await page.getByRole('button',{name:'Сохранить',exact:true}).click();
  await expect(page.getByRole('button',{name:'Сохранено',exact:true})).toBeVisible();
  await page.reload();await expect(page.getByLabel('Границы интервала 1',{exact:true})).toHaveValue('0.3, 0.6, 1');
  await page.screenshot({path:'../artifacts/normalization-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'../artifacts/normalization-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1000});await page.goto('/?view=datasets');
  await page.locator('.dataset-card').filter({has:page.getByRole('heading',{name:'Правила массивов',exact:true})}).getByRole('button',{name:'Запустить сравнение'}).click();
  await page.getByLabel('Название запуска').fill('Личные правила запуска');
  await page.getByRole('button',{name:'Параметры этого запуска'}).click();
  await page.getByLabel('Режим массива 1',{exact:true}).selectOption('index');
  await page.getByRole('button',{name:'Проверить правила',exact:true}).click();
  await expect(page.locator('.normalization-preview-result')).toContainText('изменилось: 5');
  await page.getByRole('button',{name:'Запустить',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Личные правила запуска',exact:true})).toBeVisible();
  const runId=new URL(page.url()).searchParams.get('run');
  await expect.poll(async()=>{const run=await(await page.request.get(`/api/runs/${runId}`)).json();if(run.status==='failed')throw new Error(run.error);return run.status;},{timeout:45000,intervals:[1000]}).toBe('completed');
  const run=await(await page.request.get(`/api/runs/${runId}`)).json();
  expect(run.config.arrays.items.mode).toBe('index');expect(run.summary.changed_attributes).toBe(5);
  const personal=await(await page.request.get('/api/config')).json();expect(personal.arrays.items.mode).toBe('key');
  await page.request.put('/api/config',{headers:{'If-Match':(await page.request.get('/api/config')).headers()['etag']},data:{...personal,exclude_paths:['score']}});
  expect((await(await page.request.get(`/api/runs/${runId}`)).json()).config.exclude_paths).toEqual(['ignore']);
  expect(errors).toEqual([]);
});
