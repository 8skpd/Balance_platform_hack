import {test,expect} from '@playwright/test';
const base={total_apps:500000,changed_apps:132860,decision_changed:33761,changed_attributes:201431,changed_same_decision:99099,checks_total:5,checks_failed:9,checks_passed:1,top_paths:[{PATH:'decisionResult.businessDecision',CNT:33761},{PATH:'models.pdModel.score',CNT:31820},{PATH:'products.availableProducts.cutOff',CNT:28194},{PATH:'strategy.stopFactors.hit',CNT:19960},{PATH:'creditHistory.externalScore',CNT:16312}],transitions:[{DECISION_BEFORE:'APPROVE',DECISION_AFTER:'APPROVE',CNT:244017},{DECISION_BEFORE:'APPROVE',DECISION_AFTER:'DECLINE',CNT:33761},{DECISION_BEFORE:'DECLINE',DECISION_AFTER:'DECLINE',CNT:222222}]};
async function setup(page:any,summary:any){
 const run={id:'infographic',name:'Сравнение стратегий',status:'completed',owner_id:'qa',can_manage:true,can_edit:true,created_at:'2026-11-08T00:00:00',summary,checks:[],check_plan:[],config:{},dataset:'qa',events:[],progress:100};
 await page.route('**/api/**',async(route:any)=>{const p=new URL(route.request().url()).pathname;let body:any={};
 if(p==='/api/auth/me')body={id:'qa',username:'qa',display_name:'Аналитик',role:'user'};
 else if(p==='/api/run-history')body={items:[run],total:1};
 else if(p==='/api/runs/infographic')body=run;
 else if(p.endsWith('/applications'))body={items:[],total:0,page:1,limit:30};
 else if(p==='/api/datasets')body=[];
 else if(p.endsWith('/layout'))body={fields:[],filters:[]};
 await route.fulfill({json:body});});
 await page.goto('/?run=infographic');await expect(page.locator('.run-infographic')).toBeVisible();
}
test('legacy approval counts, infographic desktop/mobile and metric navigation',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await setup(page,base);
 await expect(page.getByTestId('approval-before')).toHaveText('55,6%');await expect(page.getByTestId('approval-after')).toHaveText('48,8%');await expect(page.getByTestId('approval-points')).toHaveText('−6,8 п. п.');await expect(page.getByTestId('approval-relative')).toContainText('−12,2%');
 await expect(page.locator('.infographic-transition')).toHaveCount(3);await page.screenshot({path:'../artifacts/overview-infographic-desktop.png',fullPage:true});
 for(const width of [768,390,360]){await page.setViewportSize({width,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);}
 await page.screenshot({path:'../artifacts/overview-infographic-mobile.png',fullPage:true});
 await page.getByRole('button',{name:/Сменили решение/}).click();await expect(page).toHaveURL(/decision=1/);expect(errors).toEqual([]);
});
test('new reports: percentage points and relative growth are different',async({page})=>{
 await setup(page,{...base,total_apps:100,approval:{before_count:20,after_count:30}});await expect(page.getByTestId('approval-points')).toHaveText('+10,0 п. п.');await expect(page.getByTestId('approval-relative')).toContainText('+50,0%');
});
test('zero baseline and empty dataset do not show infinity or NaN',async({page})=>{
 await setup(page,{...base,total_apps:100,approval:{before_count:0,after_count:10}});await expect(page.getByTestId('approval-relative')).toContainText('Ранее одобрений не было');await expect(page.locator('.approval-card')).not.toContainText(/NaN|Infinity/);
 await page.unroute('**/api/**');await setup(page,{...base,total_apps:0,changed_apps:0,decision_changed:0,approval:{before_count:0,after_count:0},top_paths:[],transitions:[]});await expect(page.getByTestId('approval-points')).toHaveText('Нет данных');await expect(page.locator('.run-infographic')).not.toContainText(/NaN|Infinity/);
});

test('full approval rate fits a narrow screen',async({page})=>{await page.setViewportSize({width:360,height:900});await setup(page,{...base,total_apps:100,approval:{before_count:100,after_count:100}});await expect(page.getByTestId('approval-after')).toHaveText('100,0%');expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);const before=await page.getByTestId('approval-before').boundingBox(),after=await page.getByTestId('approval-after').boundingBox();expect(before!.x+before!.width).toBeLessThan(after!.x);});
