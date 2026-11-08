import {request as apiRequest,type APIRequestContext} from '@playwright/test';

function credentials(){
 const username=process.env.BALANCE_TEST_ADMIN_USERNAME,password=process.env.BALANCE_TEST_ADMIN_PASSWORD;
 if(!process.env.BASE_URL||!username||!password)throw Error('Set BASE_URL and BALANCE_TEST_ADMIN_USERNAME / BALANCE_TEST_ADMIN_PASSWORD for an isolated installation');
 return {username,password};
}
export async function loginTestAdmin(request:APIRequestContext){
 return request.post('/api/auth/login',{data:credentials()});
}
let manager:Promise<APIRequestContext>|undefined;
export async function provisionAccount(request:APIRequestContext,options:{data:{username:string;display_name:string;password:string;role?:string}}){
 const admin=credentials();
 manager??=(async()=>{const context=await apiRequest.newContext({baseURL:process.env.BASE_URL,extraHTTPHeaders:{'X-Balance-Request':'1'}});const login=await context.post('/api/auth/login',{data:admin});if(!login.ok())throw Error('Test administrator login failed: '+login.status());return context;})();
 const response=await(await manager).post('/api/admin/users',options);
 if(response.status()===201){
  const login=await request.post('/api/auth/login',{data:{username:options.data.username,password:options.data.password}});
  if(!login.ok())throw Error('New test account cannot log in: '+login.status());
 }
 return response;
}
