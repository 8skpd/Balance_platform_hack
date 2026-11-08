import {openTool,openMode,reveal} from './ui-navigation';
import {test,expect} from '@playwright/test';
test('review draft survives maintenance response and recovery',async({page})=>{
 expect((await page.request.post('/api/auth/login',{data:{username:process.env.BALANCE_TEST_ADMIN_USERNAME,password:process.env.BALANCE_TEST_ADMIN_PASSWORD}})).status()).toBe(200);
 const runs=(await(await page.request.get('/api/run-history?limit=100')).json()).items;const run=runs.find((x:any)=>x.status==='completed'&&x.can_manage);expect(run).toBeTruthy();
 await page.goto('/?run='+run.id+'&tab=discussion');const field=page.getByLabel('Обоснование',{exact:true});await expect(field).toBeVisible();await field.fill('Черновик сохраняется во время резервирования');
 await page.route('**/review',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Временная недоступность'})}));
 await expect(page.getByRole('alert').filter({hasText:'Временная недоступность'})).toBeVisible({timeout:15000});await expect(field).toHaveValue('Черновик сохраняется во время резервирования');
 await page.unroute('**/review');await expect(page.getByRole('alert').filter({hasText:'Временная недоступность'})).toHaveCount(0,{timeout:15000});await expect(field).toHaveValue('Черновик сохраняется во время резервирования');
});
