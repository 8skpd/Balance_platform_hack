import {test,expect} from '@playwright/test';
async function login(page:any){expect((await page.request.post('/api/auth/login',{data:{username:process.env.BALANCE_TEST_ADMIN_USERNAME,password:process.env.BALANCE_TEST_ADMIN_PASSWORD}})).status()).toBe(200);}
async function report(page:any){
 const list=(await(await page.request.get('/api/run-history?limit=100')).json()).items;
 for(const r of list){if(r.status==='completed'&&r.can_manage){const full=await(await page.request.get('/api/runs/'+r.id)).json();if(full.check_plan?.length&&full.checks?.some((c:any)=>c.APP_IDS.length))return full;}}
 throw new Error('QA report with check examples required');
}
test('ordinary screens omit machine metadata and decorative captions',async({page})=>{
 await page.goto('/');await expect(page.getByRole('heading',{name:'Вход в систему'})).toBeVisible();
 await expect(page.locator('body')).not.toContainText('ЛИЧНОЕ ПРОСТРАНСТВО');await expect(page.locator('body')).not.toContainText('Вы управляете доступом');await expect(page.locator('body')).not.toContainText('0.9.1');
 await page.screenshot({path:'../artifacts/plain-login.png',fullPage:true});
 await login(page);const run=await report(page);
 await page.goto('/?run='+run.id);await expect(page.getByRole('heading',{name:run.name,exact:true})).toBeVisible();
 await expect(page.locator('.page-heading')).not.toContainText(run.dataset);await expect(page.locator('.page-heading')).not.toContainText('сек.');await expect(page.locator('footer')).toHaveCount(0);
 await page.goto('/?view=datasets');await expect(page.locator('.dataset-card').first()).toBeVisible();await expect(page.locator('.dataset-id')).toHaveCount(0);
 await page.goto('/?view=jobs');await expect(page.locator('[data-job-id]').first()).toBeVisible();const row=page.locator('[data-job-id]').first();const id=(await row.getAttribute('data-job-id'))!;await expect(row).not.toContainText(id.slice(0,8));await expect(page.getByRole('columnheader',{name:'Лимиты'})).toHaveCount(0);
});
test('grouped results retain real counts, application drill-down and mobile layout',async({page})=>{
 await login(page);const run=await report(page);await page.goto(`/?run=${run.id}&tab=checks`);
 const groups=new Set(run.checks.map((c:any)=>c.CHECK_ID||c.CHECK_NAME||c.LABEL));await expect(page.locator('.check-result-group')).toHaveCount(groups.size);await expect(page.locator('.check-result-group tbody tr')).toHaveCount(0);
 await expect(page.locator('body')).not.toContainText('app_ids_limit');await page.screenshot({path:'../artifacts/plain-checks-desktop.png',fullPage:true});
 const index=run.checks.findIndex((c:any)=>c.APP_IDS.length);const c=run.checks[index];const name=c.CHECK_NAME||c.LABEL;
 const group=page.locator('.check-result-group').filter({has:page.locator('.check-group-name',{hasText:name})}).first();await group.locator('summary').click();
 const row=group.locator(`[data-check-index="${index}"]`);await expect(row).toContainText(c.ARGUMENT||'Все заявки');await row.getByRole('button',{name:/примера|примеров/}).click();
 await expect(page).toHaveURL(new RegExp('check='+index));await expect(page.locator('tbody .row-title').first()).toBeVisible();
 await page.locator('.report-tabs').getByRole('button',{name:'Проверки',exact:true}).click();await page.setViewportSize({width:390,height:844});await expect(page.locator('.check-result-group').first()).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:'../artifacts/plain-checks-mobile.png',fullPage:true});
});
test('pagination and FAIL filter preserve original discussion indices',async({page})=>{
 await login(page);const run=await report(page);const checks=Array.from({length:45},(_,i)=>({...run.checks[0],CHECK_ID:'group-one',CHECK_NAME:'Проверка атрибутов',LABEL:'Проверка атрибутов',ARGUMENT:'Атрибут '+i,RESULT:i%2?'FAIL':'PASS'}));
 checks.push({...checks[0],CHECK_ID:'group-two',CHECK_NAME:'Проверка продуктов',LABEL:'Проверка продуктов',ARGUMENT:'Продукт'});
 await page.route('**/api/runs/'+run.id,route=>route.fulfill({json:{...run,checks}}));
 await page.goto(`/?run=${run.id}&tab=checks`);const group=page.locator('.check-result-group').first();await group.locator('summary').click();await expect(group.locator('tbody tr')).toHaveCount(20);
 await group.getByRole('button',{name:'Следующая страница',exact:true}).click();await expect(group.locator('tbody tr').first()).toHaveAttribute('data-check-index','20');
 await page.getByRole('checkbox',{name:'Только FAIL',exact:true}).check();await expect(page.locator('.check-result-group')).toHaveCount(1);await expect(group.locator('tbody tr').first()).toHaveAttribute('data-check-index','1');
 await group.getByRole('button',{name:'Следующая страница',exact:true}).click();await expect(group.locator('tbody tr')).toHaveCount(2);await expect(group.locator('tbody tr').first()).toHaveAttribute('data-check-index','41');
 await page.route('**/comments?**',route=>route.fulfill({json:{items:[],total:0,page:1,limit:20}}));const comment=page.waitForRequest(r=>r.url().includes('/comments?')&&r.url().includes('target_id=41'));
 await group.locator('tbody tr').first().getByRole('button',{name:'Обсудить',exact:true}).click();await comment;await expect(page.getByRole('heading',{name:'Обсуждение проверки',exact:true})).toBeVisible();
 await page.getByRole('checkbox',{name:'Только FAIL',exact:true}).uncheck();await expect(page.locator('.check-result-group')).toHaveCount(2);await expect(group.locator('tbody tr').first()).toHaveAttribute('data-check-index','0');
});
test('cleanup shows a readable task name and confirms the original server target',async({page})=>{
 await login(page);const id='09d12844-34b0-46e9-80ec-88763fe8d375';
 await page.route('**/api/storage/candidates',route=>route.fulfill({json:[{kind:'job',id,name:id,bytes:1024,note:'Удаляет файлы задачи.'}]}));
 await page.route('**/api/jobs',route=>route.fulfill({json:[{id,kind:'check',created_at:'2026-11-07T12:00:00Z'}]}));
 let sent:any;await page.route('**/api/storage/cleanup',route=>{sent=route.request().postDataJSON();return route.fulfill({json:{ok:true}});});
 await page.goto('/?view=storage');await expect(page.getByRole('cell',{name:/Проверка от/})).toBeVisible();await expect(page.locator('body')).not.toContainText(id);await page.getByRole('button',{name:'Очистить',exact:true}).click();
 const form=page.locator('.cleanup-confirm');await expect(form.getByRole('button',{name:'Удалить окончательно'})).toBeDisabled();const name=(await form.locator('h3').innerText()).replace('Удаление: ','');await form.locator('input').fill(name);await form.getByRole('button',{name:'Удалить окончательно'}).click();await expect(form).toHaveCount(0);expect(sent).toEqual({kind:'job',id,confirmation:id});
});
