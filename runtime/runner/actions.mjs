import { request } from '@playwright/test';
import fs from 'node:fs';
import { input,emit } from './state.mjs';
export function interpolate(value,variables) {
  if(typeof value==='string')return value.replace(/\{\{([A-Za-z0-9_.-]+)\}\}/g,(_,key)=>{if(!(key in variables))throw new Error(`Missing variable: ${key}`);return String(variables[key]);});
  if(Array.isArray(value))return value.map(v=>interpolate(v,variables));
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,interpolate(v,variables)]));return value;
}
export async function actions(phase,variables) {
  const role=input.environment.roles?.find(r=>r.name===input.role);
  const client=await request.newContext({proxy:{server:process.env.E2E_PROXY_URL},storageState:role?.storageState,extraHTTPHeaders:role?.headers,timeout:10000});
  let firstError;
  try {
    for(const action of input.environment[phase]||[]) {
      const started=Date.now();emit(`${phase}.begin`,{name:action.name,apiBase:action.apiBase,method:action.method,path:action.path});
      try {
        const base=input.environment.apiBases[action.apiBase];if(!base)throw new Error(`Unknown API base: ${action.apiBase}`);
        const path=interpolate(action.path,variables);
        if(/^[a-z][a-z0-9+.-]*:/i.test(path)||path.startsWith('//')||path.includes('\\'))throw new Error('API action path must be relative to the named base');
        const url=new URL(path,base.endsWith('/')?base:base+'/');if(url.origin!==new URL(base).origin)throw new Error('API action target escaped its environment');
        // Pydantic serializes an omitted body as null. Passing null as Playwright's data sends
        // the literal JSON body "null", so omit data for either absent representation.
        const response=await client.fetch(url.href,{method:action.method,headers:interpolate(action.headers||{},variables),data:action.body==null?undefined:interpolate(action.body,variables),maxRedirects:0});
        const status=response.status();const expected=action.expectedStatus;
        if(expected!==undefined?status!==expected:!response.ok())throw new Error(`API ${action.name}: expected ${expected??'2xx'}, actual ${status}`);
        const captures=Object.entries(action.capture||{});
        if(captures.length>0) {
          const data=await response.json();
          for(const [variable,path] of captures) {
            let value=data;for(const piece of path.split('.')){if(['__proto__','prototype','constructor'].includes(piece))throw new Error('Invalid capture path');value=value?.[piece];}
            if(value===undefined)throw new Error(`Capture path not found: ${path}`);variables[variable]=value;
          }
          fs.writeFileSync('/work/variables.json',JSON.stringify(variables));
        }
        emit(`${phase}.end`,{name:action.name,status:'passed',targetOrigin:url.origin,expected:expected??'2xx',actual:status,captured:Object.keys(action.capture||{}),durationMs:Date.now()-started});
      } catch(error) {emit(`${phase}.end`,{name:action.name,status:'failed',error:error.message,durationMs:Date.now()-started});firstError??=error;if(phase==='setup')break;}
    }
  } finally {await client.dispose();}
  if(firstError)throw firstError;
}
