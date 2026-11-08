import type {Page} from '@playwright/test';
export async function openTool(page:Page,name:string){
 const button=page.getByRole('button',{name,exact:true});
 if(!await button.isVisible())await page.locator('.nav-tools summary').click();
 await button.click();
}
export async function openMode(page:Page,name:string){
 await page.getByLabel('Дополнительные действия анализа').click();
 await page.getByRole('button',{name,exact:true}).click();
}
export async function reveal(page:Page,title:string){
 const summary=page.locator('summary').filter({hasText:title}).first();
 if(!await summary.evaluate(el=>(el.parentElement as HTMLDetailsElement).open))await summary.click();
}
