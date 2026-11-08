import {provisionAccount} from './test-accounts';
import {test,expect} from '@playwright/test';

async function register(page:any){const r=await provisionAccount(page.request,{data:{username:'checks-ui-'+Date.now()+Math.floor(Math.random()*1000),display_name:'Проверки QA',password:'Checks-layout-test-42'}});expect(r.status()).toBe(201);}

test('compact check library preserves versions, uploads, replacement guard and archive',async({page})=>{
 await register(page);await page.goto('/?view=library');await page.getByRole('group',{name:'Раздел библиотеки'}).getByRole('button',{name:'Проверки',exact:true}).click();
 const library=page.locator('.check-library-page');await expect(library.locator('.library-list')).toHaveCount(0);await expect(library).not.toContainText('Сохраните первую проверку');
 await library.getByLabel('Название проверки',{exact:true}).fill('Моя проверка');const editor=library.getByRole('textbox',{name:'Код библиотеки'});await expect(editor).toBeVisible();await expect(library.getByRole('button',{name:'Сохранить проверку',exact:true})).toBeEnabled();
 await page.screenshot({path:'../artifacts/check-library-desktop.png',fullPage:true});await library.getByRole('button',{name:'Сохранить проверку',exact:true}).click();await expect(library.locator('.check-library-notice')).toContainText('Сохранена версия 1.');
 const code='def run(mart, params):\n    return []\n\nCHECK = {"entrypoint": run}\n# Загруженный код';
 await library.getByLabel('Файл проверки библиотеки').setInputFiles({name:'my_check.py',mimeType:'text/x-python',buffer:Buffer.from(code)});await expect(editor).toContainText('Загруженный код');
 await library.getByRole('button',{name:'Вставить пример',exact:true}).click();await page.getByRole('dialog',{name:'Заменить текущий код?'}).getByRole('button',{name:'Отмена',exact:true}).click();await expect(editor).toContainText('Загруженный код');
 await library.getByRole('button',{name:'Сохранить новую версию',exact:true}).click();await expect(library.locator('.check-library-notice')).toContainText('Сохранена версия 2.');
 await library.locator('details > summary').filter({hasText:'Версии и архив'}).click();await expect(library.getByLabel('Версия проверки')).toHaveValue('2');await library.getByLabel('Версия проверки').selectOption('1');await expect(editor).not.toContainText('Загруженный код');
 await library.getByRole('button',{name:'В архив',exact:true}).click();await expect(library.locator('.check-library-archived')).toBeVisible();await expect(library.getByRole('button',{name:'Сохранить новую версию',exact:true})).toBeDisabled();await library.locator('.check-library-archived').getByRole('button',{name:'Восстановить',exact:true}).click();await expect(library.locator('.check-library-archived')).toHaveCount(0);
 await library.locator('details > summary').filter({hasText:'Версии и архив'}).click();await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:'../artifacts/check-library-mobile.png',fullPage:true});
});

test('completed checks show one list including empty checks and source in its own group',async({page})=>{
 await register(page);let canManage=true;
 const run={id:'layout-report',name:'Отчёт проверок',status:'completed',stage:'complete',progress:100,created_at:'2026-11-08T04:00:00Z',dataset:'demo',summary:{total_apps:1,changed_apps:1},events:[],check_plan:[{key:'check-one',name:'Проверка изменений',kind:'builtin',version:1},{key:'empty',name:'Проверка без срабатываний',kind:'python',version:1}],checks:[{CHECK_ID:'check-one',CHECK_NAME:'Проверка изменений',LABEL:'Проверка изменений',ARGUMENT:'Все заявки',SOURCE:'AFTER',CNT:1,TOTAL:1,METRIC:1,RESULT:'FAIL',CONFIDENCE:'HIGH',APP_IDS:['A']}],ai:{status:'idle'}};
 await page.route('**/api/runs/layout-report',r=>r.fulfill({json:{...run,can_manage:canManage}}));await page.route('**/api/runs/layout-report/check-plan/*',r=>r.fulfill({json:{name:'Проверка изменений',kind:'builtin',version:1,code:'CHECK = {"entrypoint": run}',params:{max_share:.3}}}));
 await page.goto('/?run=layout-report&tab=checks');await expect(page.locator('.check-result-group')).toHaveCount(2);await expect(page.getByText('Состав проверок',{exact:false})).toHaveCount(0);
 const group=page.locator('.check-result-group').first();await group.locator(':scope > summary').click();await group.locator('.check-source > summary').click();await expect(group.locator('.check-source .job-log').first()).toContainText('CHECK');
 const empty=page.locator('.check-result-group').nth(1);await empty.locator(':scope > summary').click();await expect(empty).toContainText('Проверка не вернула результатов.');await expect(empty.locator(':scope > summary')).not.toContainText('PASS');
 await page.getByRole('checkbox',{name:'Только FAIL',exact:true}).check();await expect(page.locator('.check-result-group')).toHaveCount(1);await page.getByRole('checkbox',{name:'Только FAIL',exact:true}).uncheck();
 await page.screenshot({path:'../artifacts/check-results-single-list.png',fullPage:true});canManage=false;await page.reload();await page.locator('.check-result-group').first().locator(':scope > summary').click();await expect(page.locator('.check-source')).toHaveCount(0);
});

test('contract quick fix preserves code, supports undo and mouse selection',async({page})=>{
 await register(page);await page.goto('/?view=library');await page.getByRole('group',{name:'Раздел библиотеки'}).getByRole('button',{name:'Проверки',exact:true}).click();
 const library=page.locator('.check-library-page'),editor=library.getByRole('textbox',{name:'Код библиотеки'});
 const code='def run(mart):\n    # Сохранить этот комментарий\n    return []\n\nCHECK = {"entrypoint": run}';
 await library.getByLabel('Файл проверки библиотеки').setInputFiles({name:'fix_check.py',mimeType:'text/x-python',buffer:Buffer.from(code)});
 const fix=library.getByRole('button',{name:'Добавить параметры',exact:true});await expect(fix).toBeVisible();await expect(library.getByRole('button',{name:'Сохранить проверку',exact:true})).toBeDisabled();
 await expect(library.locator('.code-quick-fix')).toContainText('run(mart, params)');await fix.click();await expect(editor).toContainText('def run(mart, params):');await expect(editor).toContainText('Сохранить этот комментарий');await expect(library.getByRole('button',{name:'Сохранить проверку',exact:true})).toBeEnabled();
 await editor.press('Control+z');await expect(editor).toContainText('def run(mart):');await expect(fix).toBeVisible();await fix.click();await expect(fix).toHaveCount(0);
 await editor.scrollIntoViewIfNeeded();
 const points=await editor.locator('.cm-line').first().evaluate(el=>{const range=document.createRange();range.selectNodeContents(el);const r=range.getBoundingClientRect();return {x:r.x,y:r.y+r.height/2,end:r.right};});
 await page.mouse.move(points.x+1,points.y);await page.mouse.down();await page.mouse.move(points.end-1,points.y,{steps:20});await page.mouse.up();
 await expect.poll(()=>page.evaluate(()=>window.getSelection()?.toString())).toContain('run(mart, params)');
 await expect(editor.locator('.cm-activeLine')).toHaveCSS('background-color','rgba(100, 110, 120, 0.035)');
 await library.getByRole('button',{name:'Выделить всё',exact:true}).click();await expect.poll(()=>page.evaluate(()=>window.getSelection()?.toString())).toContain('CHECK');
 await page.screenshot({path:'../artifacts/code-mouse-selection.png',fullPage:true});
});
