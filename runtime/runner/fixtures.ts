import { test as base, expect as originalExpect } from '/opt/runner/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs';
import { input, emit, loadVariables } from './state.mjs';

const proxy = {server:process.env.E2E_PROXY_URL!};
const role = input.environment.roles?.find((r:any)=>r.name===input.role);
const origins = new Set([...Object.values(input.environment.websites),...Object.values(input.environment.apiBases),...(input.environment.allowedOrigins||[])].map((url:any)=>new URL(url).origin));
function bound(base:string,path:string) {
  if(/^[a-z]+:/i.test(path)||path.startsWith('//'))throw new Error('API path must be relative to its named environment base');
  const url=new URL(path,base.endsWith('/')?base:base+'/');
  if(url.origin!==new URL(base).origin||!origins.has(url.origin))throw new Error('API target does not belong to this environment');
  return url.href;
}
function monitor(context:any) {
  const rows:any[]=[];
  const watch=(page:any)=>{
    page.on('console',(message:any)=>emit('console',{level:message.type(),text:message.text()}));
    page.on('pageerror',(error:any)=>emit('page.error',{message:error.message}));
    page.on('request',(request:any)=>{const row={phase:'request',method:request.method(),url:request.url(),resourceType:request.resourceType()};rows.push(row);emit('network',row);});
    page.on('response',(response:any)=>{const row={phase:'response',status:response.status(),url:response.url()};rows.push(row);emit('network',row);});
    page.on('requestfailed',(request:any)=>{const row={phase:'failed',url:request.url(),error:request.failure()?.errorText};rows.push(row);emit('network',row);});
  };
  context.on('page',watch);context.pages().forEach(watch);return rows;
}
export const test=base.extend({
  _platformEvidence:[async({context}:any,use:any,testInfo:any)=>{
    const rows=monitor(context);await use();
    await testInfo.attach('network',{body:Buffer.from(JSON.stringify(rows)),contentType:'application/json'});
  },{auto:true}],
  platform:async({browser,playwright}:any,use:any)=>{
    const contexts:any[]=[];
    const variables=loadVariables();
    await use({
      websites:input.environment.websites,apiBases:input.environment.apiBases,variables,
      get:(key:string)=>variables[key],
      set:(key:string,value:any)=>{variables[key]=value;fs.writeFileSync('/work/variables.json',JSON.stringify(variables));},
      api:async(name:string,path:string,options:any={})=>{
        const apiBase=input.environment.apiBases[name];if(!apiBase)throw new Error(`Unknown API base: ${name}`);
        const client=await playwright.request.newContext({proxy,extraHTTPHeaders:role?.headers,storageState:role?.storageState});contexts.push(client);
        const url=bound(apiBase,path);const response=await client.fetch(url,{...options,maxRedirects:0});emit('network',{phase:'response',source:'api',url,status:response.status()});return response;
      },
      roleContext:async(name:string)=>{
        const found=input.environment.roles?.find((r:any)=>r.name===name);if(!found)throw new Error(`Unknown role: ${name}`);
        const context=await browser.newContext({proxy,storageState:found.storageState,extraHTTPHeaders:found.headers,serviceWorkers:'block'});contexts.push(context);monitor(context);return context;
      }
    });
    for(const context of contexts)await context.dispose?.().catch(()=>{}),await context.close?.().catch(()=>{});
  }
});

async function observed(actual:any,matcher:string,args:any[]) {
  try {
    if(matcher==='toHaveText'||matcher==='toContainText')return await actual.allTextContents();
    if(matcher==='toHaveValue')return await actual.inputValue({timeout:1000});
    if(matcher==='toHaveValues')return await actual.evaluate((el:any)=>Array.from(el.selectedOptions).map((o:any)=>o.value));
    if(matcher==='toBeVisible'||matcher==='toBeHidden')return await actual.isVisible();
    if(matcher==='toBeEnabled'||matcher==='toBeDisabled')return await actual.isEnabled({timeout:1000});
    if(matcher==='toBeChecked')return await actual.isChecked({timeout:1000});
    if(matcher==='toHaveCount')return await actual.count();
    if(matcher==='toHaveURL')return actual.url();
    if(matcher==='toHaveTitle')return await actual.title();
    if(matcher==='toHaveAttribute')return await actual.getAttribute(args[0],{timeout:1000});
    if(actual===null||['number','boolean','string','undefined'].includes(typeof actual))return actual;
    return JSON.parse(JSON.stringify(actual));
  } catch {return {unavailable:true};}
}
function matchers(value:any,actual:any,negated=false):any {
  return new Proxy(value,{get(target,key,receiver){
    const child=Reflect.get(target,key,receiver);
    if(key==='not')return matchers(child,actual,!negated);
    if(key==='resolves'||key==='rejects')return matchers(child,actual,negated);
    if(typeof child!=='function')return child;
    return (...args:any[])=>{
      let output:any;
      const report=async(status:string,error?:any)=>emit('assertion',{matcher:String(key),negated,expected:args,actual:await observed(actual,String(key),args),status,error:error?.message});
      try {output=child.apply(target,args);} catch(error){void report('failed',error);throw error;}
      if(output&&typeof output.then==='function')return output.then(async(value:any)=>{await report('passed');return value;},async(error:any)=>{await report('failed',error);throw error;});
      void report('passed');return output;
    };
  }});
}
function wrapExpect(value:any):any {
  return new Proxy(value,{apply(target,thisArg,args){return matchers(Reflect.apply(target,thisArg,args),args[0]);},get(target,key,receiver){const item=Reflect.get(target,key,receiver);if(['soft','configure','poll','extend'].includes(String(key))&&typeof item==='function')return (...args:any[])=>{const next=item.apply(target,args);return typeof next==='function'?wrapExpect(next):matchers(next,args[0]);};return item;}});
}
export const expect=wrapExpect(originalExpect);
