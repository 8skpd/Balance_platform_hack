import {test,expect} from '@playwright/test';
test('backup cards wrap on mobile and keep actions separate',async({page})=>{
 await page.request.post('/api/auth/login',{data:{username:process.env.BALANCE_TEST_ADMIN_USERNAME,password:process.env.BALANCE_TEST_ADMIN_PASSWORD}});
 await page.route('**/api/admin/backups',route=>route.fulfill({json:[{id:'verify',kind:'verify',created_at:'2026-11-07T16:30:24',status:'completed',result:{files_verified:4034,reports_opened:2,integrity_check:'ok',api_probe:true}},{id:'create',kind:'create',archive_id:'copy',created_at:'2026-11-07T16:30:17',status:'completed',result:{bytes:23173529,files:4034}}]}));
 await page.goto('/?view=admin');await page.locator('summary').filter({hasText:/^Резервные копии$/}).click();await expect(page.locator('.backup-record')).toHaveCount(2);await expect(page.getByRole('link',{name:'Скачать ZIP',exact:true})).toBeVisible();await page.screenshot({path:'../artifacts/backup-layout-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.locator('.backup-help > summary').click();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:'../artifacts/backup-layout-mobile.png',fullPage:true});
});
