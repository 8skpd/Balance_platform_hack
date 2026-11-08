import {provisionAccount} from './test-accounts';
import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
const file=(side:string,format:string)=>({name:side+'.'+format,mimeType:format==='csv'?'text/csv':'application/xml',buffer:readFileSync('public/examples/'+side+'.'+format)});
async function register(page:any){expect((await provisionAccount(page.request,{data:{username:'formats-'+Date.now(),display_name:'Форматы QA',password:'Formats-browser-password-42'}})).status()).toBe(201);}
async function upload(page:any,name:string,before:any,after:any){
 await page.goto('/?view=datasets');await page.getByLabel('Способ загрузки',{exact:true}).selectOption('pair');
 await page.getByLabel('До (MASTER)',{exact:true}).setInputFiles(before);await page.getByLabel('После (BETA)',{exact:true}).setInputFiles(after);await page.getByLabel('Название датасета',{exact:true}).fill(name);
 const received=page.waitForResponse(r=>r.url().includes('/api/dataset-imports?')&&r.request().method()==='POST');await page.getByRole('button',{name:'Загрузить и проверить',exact:true}).click();const response=await received;expect(response.status()).toBe(202);return response.json();
}
test('CSV, XML and mixed pairs upload and produce equivalent reports',async({page})=>{
 await register(page);const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 for(const [before,after] of [['csv','csv'],['xml','xml'],['csv','xml']]){
  const name=before+' → '+after;const imported=await upload(page,name,file('before',before),file('after',after));
  const card=page.locator('.dataset-card').filter({has:page.getByRole('heading',{name,exact:true})});await expect(card).toBeVisible({timeout:30000});await expect(card).toContainText('Пар заявок: 2');
  await expect(page.locator(`[data-import-id="${imported.id}"]`)).toContainText('Готов');await card.getByRole('button',{name:'Запустить сравнение',exact:true}).click();await page.getByLabel('Название запуска',{exact:true}).fill('Сравнение '+name);await page.getByRole('button',{name:'Запустить',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Сравнение '+name,exact:true})).toBeVisible();const id=new URL(page.url()).searchParams.get('run');
  await expect.poll(async()=>{const r=await(await page.request.get('/api/runs/'+id)).json();if(r.status==='failed')throw new Error(r.error);return r.status;},{timeout:45000,intervals:[1000]}).toBe('completed');
  const run=await(await page.request.get('/api/runs/'+id)).json();expect(run.summary.total_apps).toBe(2);expect(run.summary.changed_apps).toBe(1);
  const source=await(await page.request.get(`/api/runs/${id}/applications/A001/source`)).json();expect(source.BEFORE.score).toBe(100);expect(source.AFTER.score).toBe(120);expect(source.BEFORE.decisionResult.decision).toBe('APPROVE');
 }
 await page.goto('/?view=datasets');await page.getByLabel('Способ загрузки').selectOption('pair');await expect(page.locator('.import-format-help')).not.toHaveAttribute('open');await page.locator('.import-format-help summary').click();
 for(const format of ['csv','xml'])for(const side of ['before','after']){const r=await page.request.get(`/examples/${side}.${format}`);expect(r.status()).toBe(200);expect(await r.body()).toEqual(file(side,format).buffer);}
 await page.locator('.import-format-help summary').click();await page.screenshot({path:'../artifacts/import-formats-desktop.png',fullPage:true});await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:'../artifacts/import-formats-mobile.png',fullPage:true});expect(errors).toEqual([]);
});
test('invalid CSV and XML remain unpublished with useful errors',async({page})=>{
 await register(page);
 const invalid=[
  {name:'Повтор ID',format:'csv',data:'application_id,score\nA001,1\nA001,2\n',message:'повтор application_id'},
  {name:'Небезопасный XML',format:'xml',data:'<!DOCTYPE a [<!ENTITY x "EXPAND">]><a><v>&x;</v></a>',message:'DTD'},
  {name:'Нет ID',format:'xml',data:'<application><score>1</score></application>',message:'укажите application_id'},
 ];
 for(const c of invalid){const bad=await upload(page,c.name,{name:'before.'+c.format,mimeType:'text/plain',buffer:Buffer.from(c.data)},file('after',c.format));await expect(page.locator(`[data-import-id="${bad.id}"]`)).toContainText(c.message,{timeout:30000});await expect(page.locator('.dataset-card').filter({has:page.getByRole('heading',{name:c.name,exact:true})})).toHaveCount(0);}
});
test('cancelling file preparation prevents a late upload',async({page})=>{
 await register(page);await page.goto('/?view=datasets');await page.getByLabel('Способ загрузки').selectOption('pair');await page.getByLabel('До (MASTER)').setInputFiles(file('before','csv'));await page.getByLabel('После (BETA)').setInputFiles(file('after','csv'));await page.getByLabel('Название датасета').fill('Отмена');
 await page.evaluate(()=>{const original=File.prototype.arrayBuffer;const release:Function[]=[];(window as any).releaseReads=()=>Promise.all(release.map(f=>f()));File.prototype.stream=function(){const file=this;return new ReadableStream({start(controller){release.push(async()=>{controller.enqueue(new Uint8Array(await original.call(file)));controller.close();});}});};});
 const uploads:string[]=[];page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/api/dataset-imports?'))uploads.push(r.url());});await page.getByRole('button',{name:'Загрузить и проверить'}).click();await page.getByRole('button',{name:'Прервать передачу'}).click();await page.evaluate(()=>(window as any).releaseReads());await expect(page.getByRole('button',{name:'Загрузить и проверить'})).toBeEnabled();await expect(page.getByRole('status')).toContainText('Передача отменена');expect(uploads).toEqual([]);
});
