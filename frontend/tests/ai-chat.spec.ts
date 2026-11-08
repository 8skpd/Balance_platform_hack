import {test,expect} from '@playwright/test';
async function fixture(page:any,readonly=false){
 const state:any={items:[{id:'first',status:'completed',created_at:'2026-11-08T00:00:00Z',content:'Изменилось **502 заявки**.\n\n- Проверьте снижение одобрений.\n- Сравните пороги.',model:'qwen3',stale:false}],next_before:null,active:null,can_send:!readonly,report_revision:1,legacy_answer:null};
 const sent:any[]=[];const run={id:'chat-test',name:'Изменение стратегии',status:'completed',created_at:'2026-11-08T00:00:00Z',can_manage:!readonly,ai:{status:'idle'},summary:{},checks:[]};
 await page.route('**/api/**',async(route:any)=>{const req=route.request(),p=new URL(req.url()).pathname;let data:any={};
  if(p==='/api/auth/me')data={id:'qa',username:'qa',display_name:'QA',role:readonly?'observer':'user'};
  else if(p==='/api/run-history')data={items:[run],total:1};
  else if(p==='/api/datasets')data=[];
  else if(p==='/api/ollama')data={available:true,model:'qwen3'};
  else if(p==='/api/runs/chat-test')data=run;
  else if(p.endsWith('/ai-chat')){
   if(req.method()==='POST'){const body=req.postDataJSON();sent.push(body);const id='job-'+sent.length;state.items.push({id,status:'running',message:body.message,requested_by:'qa',created_at:new Date().toISOString()});state.active={id,status:'running'};data={job_id:id,status:'queued'};}
   else data=state;
  }else if(p.endsWith('/cancel')){state.items.at(-1).status='cancelled';state.active=null;}
  await route.fulfill({status:req.method()==='POST'&&p.endsWith('/ai-chat')?202:200,json:data});
 });
 await page.goto('/?run=chat-test&tab=ai');await expect(page.getByRole('region',{name:'Чат с ИИ'})).toBeVisible();return {state,sent};
}
test('chat sends questions, preserves history and draft, supports stop and retry',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));const {state,sent}=await fixture(page);
 await expect(page.locator('.chat-message-body strong')).toContainText('502 заявки');const input=page.getByLabel('Сообщение ИИ',{exact:true});await input.fill('Как изменилась доля одобрений?');await input.press('Enter');await expect(page.locator('.chat-message.user')).toContainText('Как изменилась доля одобрений?');await expect(input).toHaveValue('');expect(sent[0].message).toBe('Как изменилась доля одобрений?');await expect(page.getByRole('button',{name:'Остановить ответ'})).toBeVisible();
 state.items.at(-1).status='completed';state.items.at(-1).content='Доля снизилась на **8,0 п. п.**';state.items.at(-1).finished_at=new Date().toISOString();state.active=null;await expect(page.locator('.chat-scroll')).toContainText('8,0 п. п.');
 await input.fill('Поясни');await input.press('Shift+Enter');await input.press('A');await expect(input).toHaveValue('Поясни\nA');await page.reload();await expect(page.getByLabel('Сообщение ИИ',{exact:true})).toHaveValue('Поясни\nA');await expect(page.locator('.chat-scroll')).toContainText('502 заявки');await expect(page.locator('.chat-scroll')).toContainText('8,0 п. п.');
 await page.getByLabel('Сообщение ИИ',{exact:true}).fill('Что проверить дальше?');await page.getByRole('button',{name:'Отправить сообщение'}).click();await page.getByRole('button',{name:'Остановить ответ'}).click();await expect(page.locator('.chat-scroll')).toContainText('Ответ остановлен');await page.getByRole('button',{name:'Повторить',exact:true}).click();expect(sent.at(-1).message).toBe('Что проверить дальше?');
 state.items.at(-1).status='completed';state.items.at(-1).content='Сравните пороги и стоп-факторы. <img src=x onerror=alert(1)>';state.active=null;await expect(page.locator('.chat-scroll')).toContainText('Сравните пороги');await expect(page.locator('.chat-scroll img')).toHaveCount(0);await page.locator('.ai-chat').screenshot({path:'../artifacts/ai-chat-desktop.png'});
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.locator('.ai-chat').screenshot({path:'../artifacts/ai-chat-mobile.png'});expect(errors).toEqual([]);
});
test('shared report only exposes read-only conversation',async({page})=>{
 await fixture(page,true);await expect(page.locator('.chat-scroll')).toContainText('502 заявки');await expect(page.getByLabel('Сообщение ИИ')).toHaveCount(0);await expect(page.getByRole('button',{name:'Отправить сообщение'})).toHaveCount(0);await expect(page.locator('.chat-readonly')).toContainText('Вопросы задаёт владелец');
});
test('composer spans chat width and keyboard hint stays below it',async({page})=>{
 await fixture(page);for(const width of [1440,390]){await page.setViewportSize({width,height:900});const footer=await page.locator('.chat-footer').boundingBox(),composer=await page.locator('.chat-composer').boundingBox(),hint=await page.locator('.chat-composer-hint').boundingBox();expect(composer!.width).toBeGreaterThan(footer!.width-60);expect(hint!.y).toBeGreaterThanOrEqual(composer!.y+composer!.height);await expect.poll(()=>page.getByLabel('Сообщение ИИ',{exact:true}).evaluate(el=>el.scrollHeight<=el.clientHeight+1)).toBe(true);}
});
