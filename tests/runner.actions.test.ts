import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {DockerRunner,type RunInput,type RunEvent,type RunResult} from '../src/runtime/runner.js';

const exec=promisify(execFile);
const docker=async(args:string[])=>{const result=await exec('docker',args,{windowsHide:true,maxBuffer:4*1024*1024});return result.stdout.trim();};
test('Python ApiAction defaults: empty capture accepts 204/plain text; null body is absent',{skip:process.env.E2E_RUN_DOCKER_TESTS!=='1',timeout:60000},async()=>{
  const id=randomUUID();const image='e2e-runner:1.63.0';const fixture=`e2e-action-fixture-${id.slice(0,8)}`;
  const runner=new DockerRunner({image});const workDir=path.resolve('data/runner-actions-regression',id);const events:RunEvent[]=[];
  try {
    await docker(['run','-d','--rm','--name',fixture,'--label','e2e.fixture=action-regression','--network','bridge','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--mount',`type=bind,source=${path.resolve('runtime/runner/action-fixture.mjs')},target=/fixture.mjs,readonly`,'--entrypoint','node',image,'/fixture.mjs']);
    const info=JSON.parse(await docker(['inspect',fixture]));const origin=`http://${info[0].NetworkSettings.Networks.bridge.IPAddress}:8080`;
    // Keep the explicit null and empty mappings emitted by Pydantic model_dump().
    const defaults={apiBase:'main',headers:{},body:null,expectedStatus:200,capture:{}};
    const input:RunInput={id,projectId:'action-regression',workDir,versions:[{id:'v1',scenarioId:'action-defaults',checks:[],modules:{},code:`import {test,expect} from '@playwright/test';
test('actual request shapes and capture',async({platform})=>{
  expect(platform.get('getBodyBytes')).toBe(0);
  expect(platform.get('postBodyBytes')).toBe(0);
  expect(platform.get('falseBody')).toBe('false');
  expect(platform.get('orderId')).toBe('regression-order');
});`}],environment:{websites:{main:origin},apiBases:{main:origin},variables:{},secretVariables:{},roles:[],allowedOrigins:[],setup:[
      {...defaults,name:'GET with default body',method:'GET',path:'/shape',capture:{getBodyBytes:'bodyBytes'}},
      {...defaults,name:'POST with default body',method:'POST',path:'/shape',capture:{postBodyBytes:'bodyBytes'}},
      {...defaults,name:'false is an intentional body',method:'POST',path:'/shape',body:false,capture:{falseBody:'body'}},
      {...defaults,name:'plain text without capture',method:'GET',path:'/plain'},
      {...defaults,name:'create order',method:'POST',path:'/orders',body:{reference:'regression'},expectedStatus:201,capture:{orderId:'id'}}
    ],cleanup:[{...defaults,name:'DELETE empty 204 with empty capture',method:'DELETE',path:'/orders/{{orderId}}',expectedStatus:204}]},timeoutMs:20000,retries:0};
    let complete!:(result:RunResult)=>void;const completed=new Promise<RunResult>(resolve=>complete=resolve);
    await runner.start(input,{event:event=>events.push(event),complete});const result=await completed;
    await fs.writeFile(path.join(workDir,'regression.json'),JSON.stringify({verifiedAt:new Date().toISOString(),id,result,events},null,2));
    assert.equal(result.status,'passed',JSON.stringify(result));assert.equal(result.verification,'verified');
    assert(events.some(event=>event.type==='cleanup.end'&&event.data.actual===204&&event.data.status==='passed'));
    const state=JSON.parse(await docker(['exec',fixture,'node','-e',"fetch('http://localhost:8080/state').then(r=>r.json()).then(v=>console.log(JSON.stringify(v)))"]));
    assert.equal(state.orderExists,false);
    const deletion=state.requests.find((request:any)=>request.method==='DELETE');assert.equal(deletion.bodyBytes,0);assert.equal(deletion.contentType,null);
    for(const method of ['GET','POST']){const request=state.requests.find((request:any)=>request.path==='/shape'&&request.method===method);assert.equal(request.bodyBytes,0);assert.equal(request.contentType,null);}
  }finally{await runner.close();await docker(['rm','-f',fixture]).catch(()=>{});}
});
