import {provisionAccount} from './test-accounts';
import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';

async function account(page:any){
 const r=await provisionAccount(page.request,{data:{username:'setup-'+Date.now()+Math.floor(Math.random()*1000),display_name:'Настройка QA',password:'Setup-test-password-42'}});expect(r.status()).toBe(201);
 const zip=execFileSync('../.venv/bin/python',['-c',`import io,json,zipfile,sys
b=io.BytesIO()
with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
 for n,side in enumerate(['BEFORE_MASTER','AFTER_BETA']):z.writestr('RESULTS/'+side+'/A.json',json.dumps({'appData':{'application_id':'A'},'value':n}))
sys.stdout.buffer.write(b.getvalue())`]);
 const upload=await page.request.post('/api/dataset-imports?name=Малый%20набор&filename=setup.zip',{data:zip,headers:{'Content-Type':'application/zip'}});expect(upload.status()).toBe(202);const id=(await upload.json()).id;
 await expect.poll(async()=>{const r=await(await page.request.get('/api/dataset-imports/'+id)).json();if(r.status==='failed')throw Error(r.error);return r.status;},{timeout:30000}).toBe('completed');
 await page.goto('/');await page.getByRole('button',{name:'Настройка анализа',exact:true}).click();await page.getByLabel('Датасет',{exact:true}).selectOption(id);return id;
}

test('unified setup edits all stages, checks contracts, saves template and runs real Python',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 const id=await account(page);const modal=page.getByRole('dialog',{name:'Настройка анализа'}),launch=modal.getByRole('button',{name:'Запустить',exact:true});
 await expect(launch).toBeEnabled();await expect(modal.locator('.setup-pane')).toHaveCount(5);
 const catalog=await(await page.request.get('/api/procedures/catalog')).json();
 for(const [i,stage] of ['normalization','comparison','aggregation'].entries()){
  const card=modal.locator('.setup-pane').nth(i+1);await modal.getByRole('tab').nth(i+1).click();await card.getByRole('button',{name:'Открыть код',exact:true}).click();
  const editor=card.getByRole('textbox',{name:'Код Python',exact:true});await expect(editor).toBeVisible();await expect(launch).toBeDisabled();
  if(stage==='comparison'){
   await editor.fill('def run(data):\n    return []\nPROCEDURE = {"entrypoint": run}');
   await expect(card.locator('.code-diagnostics')).toContainText('2 позиционными');await expect(card.getByRole('button',{name:'Сохранить и применить',exact:true})).toBeDisabled();
   await modal.getByRole('tab').nth(3).click();await modal.getByRole('tab').nth(2).click();await expect(editor).toContainText('def run(data):');await expect(card.locator('.code-diagnostics')).toContainText('2 позиционными');await page.screenshot({path:'../artifacts/run-setup-contract-error.png',fullPage:true});await card.locator('.procedure-contract > summary').click();await card.getByRole('button',{name:'Вставить стандартный пример',exact:true}).click();await expect(page.getByRole('dialog',{name:'Заменить текущий код?'})).toBeVisible();await page.keyboard.press('Escape');await expect(page.getByRole('dialog',{name:'Заменить текущий код?'})).toHaveCount(0);await expect(modal).toBeVisible();await expect(editor).toContainText('def run(data):');await card.locator('.procedure-contract > summary').click();
  }
  await editor.fill(catalog.examples[stage]);await card.getByLabel('Название обработчика').fill('Моя '+stage);
  await expect(card.getByRole('button',{name:'Сохранить и применить',exact:true})).toBeEnabled();await card.getByRole('button',{name:'Сохранить и применить',exact:true}).click();await expect(card.locator('.inline-procedure')).toHaveCount(0);

 }
 const checks=modal.locator('.setup-pane').nth(4);await modal.getByRole('tab').nth(4).click();
 await checks.getByRole('button',{name:'Свой Python',exact:true}).click();await checks.getByLabel('Название обработчика').fill('Проверка в форме');await expect(checks.getByRole('button',{name:'Сохранить и применить',exact:true})).toBeEnabled();await checks.getByRole('button',{name:'Сохранить и применить',exact:true}).click();
 await expect(checks.getByRole('checkbox',{name:'Включить Проверка в форме',exact:true})).toBeChecked();
 // A copied bundled check can be edited in place; cancelling preserves selection.
 await checks.getByRole('button',{name:'Стандартные',exact:true}).click();await checks.getByRole('button',{name:'Код',exact:true}).first().click();await expect(checks.getByRole('textbox',{name:'Код Python'})).toBeVisible();await checks.getByRole('button',{name:'Отмена',exact:true}).click();
 await modal.getByRole('tab').first().click();await modal.locator('.run-templates > summary').click();await modal.getByLabel('Название шаблона').fill('Четыре этапа');await modal.getByRole('button',{name:'Сохранить как новый шаблон',exact:true}).click();await expect(modal.locator('.template-notice')).toContainText('Шаблон сохранён');
 const templates=await(await page.request.get('/api/run-templates')).json();const saved=await(await page.request.get('/api/run-templates/'+templates[0].id)).json();expect(Object.keys(saved.spec.procedures)).toHaveLength(3);expect(saved.spec.user_checks).toHaveLength(1);
 await modal.getByRole('button',{name:'Закрыть',exact:true}).click();await page.getByRole('button',{name:'Настройка анализа',exact:true}).click();await modal.locator('.run-templates > summary').click();await modal.getByLabel('Мои шаблоны').selectOption(templates[0].id);await modal.getByRole('button',{name:'Применить шаблон',exact:true}).click();await expect(modal.getByLabel('Датасет',{exact:true})).toHaveValue(id);await expect(launch).toBeEnabled();
 const response=page.waitForResponse(r=>r.url().endsWith('/api/runs')&&r.request().method()==='POST');await launch.click();const r=await response;expect(r.status()).toBe(201);const run=await r.json();
 await expect.poll(async()=>{const r=await(await page.request.get('/api/runs/'+run.id)).json();if(r.status==='failed')throw Error(r.error);return r.status;},{timeout:60000}).toBe('completed');
 const completed=await(await page.request.get('/api/runs/'+run.id)).json();expect(completed.summary.changed_apps).toBe(1);expect(completed.checks.some((c:any)=>c.CHECK_NAME==='Проверка в форме')).toBe(true);
 await page.reload();await expect(page.locator('body')).not.toContainText('Internal Server Error');expect(errors).toEqual([]);
});

test('mobile editor, invalid config and stale validation never enable launch',async({page})=>{
 await account(page);const modal=page.getByRole('dialog',{name:'Настройка анализа'}),launch=modal.getByRole('button',{name:'Запустить',exact:true});await expect(launch).toBeEnabled();
 const card=modal.locator('.setup-pane').nth(1);await modal.getByRole('tab').nth(1).click();await card.locator('.stage-options > summary').click();await card.getByRole('button',{name:'Добавить исключение',exact:true}).click();await expect(launch).toBeDisabled();await expect(card.getByLabel(/Путь исключения/).last()).toBeVisible();await card.getByLabel(/Путь исключения/).last().fill('debug');await expect(launch).toBeEnabled();
 await card.getByRole('button',{name:'Открыть код',exact:true}).click();const editor=card.getByRole('textbox',{name:'Код Python'});await editor.fill('PROCEDURE = {}');await expect(card.locator('.code-diagnostics')).toContainText('entrypoint');await expect(launch).toBeDisabled();
 await page.setViewportSize({width:390,height:844});await editor.scrollIntoViewIfNeeded();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:'../artifacts/run-setup-mobile.png',fullPage:true});
 page.once('dialog',dialog=>dialog.dismiss());await modal.getByRole('button',{name:'Закрыть',exact:true}).click();await expect(modal).toBeVisible();
 page.once('dialog',dialog=>dialog.dismiss());await page.keyboard.press('Escape');await expect(modal).toBeVisible();await card.getByRole('button',{name:'Отмена',exact:true}).click();await expect(launch).toBeEnabled();await card.getByRole('button',{name:'Из библиотеки',exact:true}).click();await expect(launch).toBeDisabled();await card.getByRole('button',{name:'Стандартная',exact:true}).click();await expect(launch).toBeEnabled();
 await page.route('**/api/runs/validate',r=>r.fulfill({status:503,json:{detail:'Проверка временно недоступна'}}));await modal.getByRole('tab').first().click();await modal.getByLabel('Название анализа').fill('Изменённая конфигурация');await expect(modal.locator('.setup-feedback')).toContainText('Проверка временно недоступна');await expect(launch).toBeDisabled();await page.unroute('**/api/runs/validate');await modal.getByRole('button',{name:'Проверить ещё раз',exact:true}).click();await expect(launch).toBeEnabled();
});
