import {test,expect} from '@playwright/test';

test('login has no registration; administrator creates users without changing their own session',async({page,browser,baseURL})=>{
 page.setDefaultTimeout(15000);
 const username=process.env.BALANCE_TEST_ADMIN_USERNAME,password=process.env.BALANCE_TEST_ADMIN_PASSWORD;
 if(!username||!password)throw Error('Set BALANCE_TEST_ADMIN_USERNAME and BALANCE_TEST_ADMIN_PASSWORD for an isolated test installation');
 await page.goto('/?register=true');await expect(page.getByRole('heading',{name:'Вход в систему'})).toBeVisible();
 await expect(page.getByRole('button',{name:'Создать аккаунт'})).toHaveCount(0);await expect(page.getByLabel('Повторите пароль')).toHaveCount(0);
 expect([404,405]).toContain((await page.request.post('/api/auth/register',{data:{username:'public-attempt',display_name:'Public',password}})).status());
 await page.screenshot({path:'../artifacts/admin-only-login.png'});
 await page.getByLabel('Логин',{exact:true}).fill(username);await page.getByLabel('Пароль',{exact:true}).fill(password);await page.getByRole('button',{name:'Войти',exact:true}).click();
 await expect(page.locator('.auth-card')).toHaveCount(0);const admin=await(await page.request.get('/api/auth/me')).json();expect(admin.role).toBe('admin');
 await page.goto('/?view=admin');await page.getByRole('button',{name:'Новый пользователь',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Новый пользователь',exact:true});const login='staff-'+Date.now(),secret='Staff-browser-password-42';
 await dialog.getByLabel('Имя',{exact:true}).fill('Новый сотрудник');await dialog.getByLabel('Логин',{exact:true}).fill(login);await dialog.getByLabel('Роль',{exact:true}).selectOption('observer');await dialog.getByLabel('Пароль',{exact:true}).fill(secret);await dialog.getByLabel('Повторите пароль',{exact:true}).fill('Incorrect-password-42');await dialog.getByRole('button',{name:'Создать пользователя',exact:true}).click();await expect(dialog.getByRole('alert')).toContainText('Пароли не совпадают');
 await dialog.getByLabel('Повторите пароль',{exact:true}).fill(secret);await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await dialog.screenshot({path:'../artifacts/admin-create-user-mobile.png'});await page.setViewportSize({width:1440,height:1000});await dialog.screenshot({path:'../artifacts/admin-create-user.png'});
 const created=page.waitForResponse(r=>r.url().endsWith('/api/admin/users')&&r.request().method()==='POST');await dialog.getByRole('button',{name:'Создать пользователя',exact:true}).click();const response=await created;expect(response.status()).toBe(201);const account=await response.json();await expect(dialog).toHaveCount(0);await expect(page.getByLabel('Роль '+login,{exact:true})).toHaveValue('observer');expect(await(await page.request.get('/api/auth/me')).json()).toEqual(admin);
 await page.getByRole('button',{name:'Новый пользователь',exact:true}).click();await dialog.getByLabel('Имя',{exact:true}).fill('Дубликат');await dialog.getByLabel('Логин',{exact:true}).fill(login.toUpperCase());await dialog.getByLabel('Пароль',{exact:true}).fill(secret);await dialog.getByLabel('Повторите пароль',{exact:true}).fill(secret);await dialog.getByRole('button',{name:'Создать пользователя',exact:true}).click();await expect(dialog.getByRole('alert')).toContainText('логин уже занят');await dialog.getByRole('button',{name:'Отмена',exact:true}).click();
 const staff=await browser.newContext({baseURL,extraHTTPHeaders:{'X-Balance-Request':'1'}});try{
  const result=await staff.request.post('/api/auth/login',{data:{username:login,password:secret}});expect(result.status()).toBe(200);expect(await result.json()).toEqual(account);
  expect((await staff.request.post('/api/admin/users',{data:{username:'forbidden',display_name:'Denied',password:secret,role:'admin'}})).status()).toBe(403);
  const view=await staff.newPage();await view.goto('/?view=admin');await expect(view.getByRole('button',{name:'Новый пользователь',exact:true})).toHaveCount(0);
 }finally{await staff.close();}
});
