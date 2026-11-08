import {provisionAccount} from './test-accounts';
import {test,expect} from '@playwright/test';
test('stage documentation and guarded insertion preserve user code until confirmation',async({page})=>{
 const r=await provisionAccount(page.request,{data:{username:'contract-'+Date.now(),display_name:'Контракт QA',password:'Contract-password-42'}});expect(r.status()).toBe(201);
 await page.goto('/?view=library');
 for(const title of ['Разбор и нормализация','Сравнение','Подготовка отчёта']){
  await page.getByRole('group',{name:'Раздел библиотеки'}).getByRole('button',{name:title,exact:true}).click();const editor=page.locator('.procedure-editor:visible');await expect(editor.getByLabel('Код процедуры')).toBeEnabled();await editor.locator('.procedure-contract > summary').click();
  for(const heading of ['Точка входа','Входные данные','Результат','Стандартная реализация'])await expect(editor.getByRole('heading',{name:heading,exact:true})).toBeVisible();await expect(editor.locator('.contract-fields').first()).toBeVisible();await expect(editor.locator('.contract-document')).not.toContainText('Сеть, запуск процессов');
  const original=await editor.getByLabel('Код процедуры').innerText(),custom=original+'\n# пользовательская логика';await editor.getByLabel('Код процедуры').fill(custom);await editor.getByRole('button',{name:'Вставить стандартный пример',exact:true}).click();const dialog=page.getByRole('dialog',{name:'Заменить текущий код?'});await expect(dialog).toContainText('Несохранённые изменения будут потеряны');await dialog.getByRole('button',{name:'Отмена',exact:true}).click();await expect(editor.getByLabel('Код процедуры')).toContainText('пользовательская логика');
  await editor.getByRole('button',{name:'Вставить стандартный пример',exact:true}).click();await page.keyboard.press('Escape');await expect(editor.getByLabel('Код процедуры')).toContainText('пользовательская логика');
  await editor.getByRole('button',{name:'Вставить стандартный пример',exact:true}).click();await dialog.getByRole('button',{name:'Заменить код',exact:true}).click();await expect(editor.getByLabel('Код процедуры')).not.toContainText('пользовательская логика');await editor.getByRole('button',{name:'Вставить стандартный пример',exact:true}).click();await expect(dialog).toHaveCount(0);
 }
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:'../artifacts/procedure-contract-mobile.png',fullPage:true});
});
