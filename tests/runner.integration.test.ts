import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {DockerRunner,type RunInput,type RunEvent,type RunResult} from '../src/runtime/runner.js';
const exec=promisify(execFile);
const enabled=process.env.E2E_RUN_DOCKER_TESTS==='1';
const root=path.resolve('data/runner-acceptance');
const cmd=async(args:string[])=>{const result=await exec('docker',args,{windowsHide:true,maxBuffer:4*1024*1024});return result.stdout.trim();};

test('isolated Playwright runner: real UI/API, retries, evidence, target binding, cancel and timeout',{skip:!enabled,timeout:240000},async()=>{
  const image='e2e-runner:1.63.0';const fixture=`e2e-runner-fixture-${randomUUID().slice(0,8)}`;const runner=new DockerRunner({image});
  const evidence:any[]=[];
  await fs.mkdir(root,{recursive:true});
  try {
    assert.equal((await runner.available()).available,true);
    await cmd(['run','-d','--rm','--name',fixture,'--label','e2e.fixture=true','--network','bridge','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--mount',`type=bind,source=${path.resolve('runtime/runner/fixture-server.mjs')},target=/fixture.mjs,readonly`,'--entrypoint','node',image,'/fixture.mjs']);
    const info=JSON.parse(await cmd(['inspect',fixture]));const ip=info[0].NetworkSettings.Networks.bridge.IPAddress;
    const a=`http://${ip}:8080`;const b=`http://${ip}:8081`;
    const make=(code:string,options:Partial<RunInput>={}):RunInput=>{const id=randomUUID();return {id,projectId:'acceptance',workDir:path.join(root,id),versions:[{id:'v1',scenarioId:'scenario',code,checks:[{id:'metadata-only',title:'not execution evidence'}],modules:{}}],environment:{websites:{main:a},apiBases:{main:a},variables:{label:'Fixture A'},secretVariables:{credential:'runner-secret-DO-NOT-LEAK'},roles:[{name:'buyer',headers:{'X-Test-Role':'buyer','Authorization':'Bearer role-secret-DO-NOT-LEAK'}}],setup:[],cleanup:[],allowedOrigins:[]},role:'buyer',timeoutMs:30000,retries:0,...options};};
    const run=async(input:RunInput,cancel=false)=>{const events:RunEvent[]=[];let resolve!:(result:RunResult)=>void;const done=new Promise<RunResult>(r=>resolve=r);await runner.start(input,{event:event=>events.push(event),complete:resolve});if(cancel){await new Promise<void>(r=>{const timer=setInterval(()=>{if(events.some(e=>e.type==='test.begin')){clearInterval(timer);r();}},100);setTimeout(()=>{clearInterval(timer);r();},12000).unref();});await runner.cancel(input.id);}const result=await done;evidence.push({id:input.id,result,events});return {result,events};};
    const main=make(`import {test,expect} from '@playwright/test';import {label} from './modules/labels';
test('verified UI + API',async({page,platform})=>{await page.goto(process.env.E2E_WEBSITE_main!);await expect(page.getByRole('heading')).toHaveText(label('A'));await page.getByRole('textbox',{name:'Order'}).fill(platform.get('orderId'));await page.getByRole('button',{name:'Submit'}).click();await expect(page.locator('#result')).toHaveText(platform.get('orderId'));platform.set('uiOrderId',await page.locator('#order-id').textContent());const response=await platform.api('main','/orders/'+platform.get('uiOrderId'));expect((await response.json()).reference).toBe(platform.get('orderId'));const state=await platform.api('main','/state');expect((await state.json()).role).toBe('buyer');const admin=await platform.roleContext('admin');const adminState=await admin.request.get(process.env.E2E_API_main+'/state');expect((await adminState.json()).role).toBe('admin');expect(process.env.E2E_VAR_credential).toBe(platform.get('credential'));console.log(platform.get('credential'));});
test('only operations',async({page})=>{await page.goto(process.env.E2E_WEBSITE_main!);});
test.skip('explicitly skipped',async()=>{});
test('retry evidence',async({},info)=>{expect(info.retry).toBe(1);});`,{retries:1});
    main.versions[0]!.modules={'labels.ts':"export const label=(suffix:string)=>'Fixture '+suffix;"};
    main.environment.roles!.push({name:'admin',headers:{'X-Test-Role':'admin'}});
    main.environment.setup=[{name:'create order',apiBase:'main',method:'POST',path:'/orders',body:{from:'setup'},expectedStatus:201,capture:{orderId:'id'}}];
    main.environment.cleanup=[{name:'delete prepared order',apiBase:'main',method:'DELETE',path:'/orders/{{orderId}}',expectedStatus:204},{name:'delete UI order',apiBase:'main',method:'DELETE',path:'/orders/{{uiOrderId}}',expectedStatus:204}];
    const first=await run(main);
    assert.equal(first.result.status,'passed',JSON.stringify(first.result));assert.deepEqual(first.result.summary,{total:4,passed:3,failed:0,skipped:1,flaky:1,unverified:1});assert.equal(first.result.verification,'partial');
    assert(first.events.some(e=>e.type==='test.end'&&e.data.status==='failed'&&e.data.attempt===0));assert(first.events.some(e=>e.type==='assertion'&&e.data.matcher==='toHaveText'&&e.data.actual?.includes('Fixture A')));
    assert(first.events.some(e=>e.type==='cleanup.end'&&e.data.status==='passed'));assert(first.result.artifacts.some(a=>a.kind==='trace'));assert(first.result.artifacts.some(a=>a.kind==='screenshot'));
    assert(!JSON.stringify(first.events).includes('DO-NOT-LEAK'));assert.equal(await fs.access(path.join(main.workDir,'execution','input.json')).then(()=>true,()=>false),false);
    const artifactCheck=JSON.parse(await cmd(['run','--rm','--network','none','--read-only','--mount',`type=bind,source=${path.join(main.workDir,'execution','artifacts')},target=/evidence,readonly`,'--entrypoint','node',image,'/opt/runner/verify-evidence.mjs']));assert(artifactCheck.archives>=4);assert(artifactCheck.records>0);
    const state=JSON.parse(await cmd(['exec',fixture,'node','-e',"fetch('http://localhost:8080/state').then(r=>r.json()).then(v=>console.log(JSON.stringify(v)))"]));assert.equal(state.orders.length,0);
    // Exact same recorded-style code binds to the selected environment, with independently named fixture endpoints.
    const code=`import {test,expect} from '@playwright/test';test('switch environment',async({page})=>{await page.goto(new URL('/',process.env.E2E_WEBSITE_main!).href);await expect(page.getByRole('heading')).toHaveText('Fixture B');});`;
    const switched=make(code);switched.environment.websites.main=b;switched.environment.apiBases.main=b;
    assert.equal((await run(switched)).result.status,'passed');
    const recorded=await fs.readFile('runtime/recorder/evidence/bound-recording.spec.ts','utf8');
    for(const origin of [a,b]) {
      const replay=make(recorded);replay.environment.websites.main=origin;replay.environment.apiBases.main=origin;
      const result=await run(replay);assert.equal(result.result.status,'passed');assert.equal(result.result.verification,'verified');
      const requests=result.events.filter(e=>e.type==='network'&&e.data.phase==='request');assert(requests.length>0);assert(requests.every(e=>new URL(e.data.url).origin===origin));
    }
    const denied=make(`import {test,expect} from '@playwright/test';import net from 'node:net';test('old target + raw network denied',async({request})=>{const reply=await request.get('${a}/health');expect(reply.status()).toBe(403);const raw=await new Promise(r=>{const s=net.connect({host:'${ip}',port:8080},()=>{s.destroy();r('connected');});s.setTimeout(700,()=>{s.destroy();r('blocked');});s.on('error',()=>r('blocked'));});expect(raw).toBe('blocked');const control=await request.get('http://host.docker.internal:4100/api/health');expect(control.status()).toBe(403);});`);denied.environment.websites.main=b;denied.environment.apiBases.main=b;
    assert.equal((await run(denied)).result.status,'passed');
    const noChecks=await run(make(`import {test} from '@playwright/test';test('no assertions',async({page})=>{await page.goto(process.env.E2E_WEBSITE_main!);});`));assert.equal(noChecks.result.verification,'unverified');
    const empty=await run(make(`export const nothing=true;`));assert.equal(empty.result.status,'error');assert.equal(empty.result.summary.total,0);
    const cancelledInput=make(`import {test} from '@playwright/test';test('cancel',async({page})=>{await page.goto(process.env.E2E_WEBSITE_main!);await page.waitForTimeout(60000);});`);
    cancelledInput.environment.setup=main.environment.setup;cancelledInput.environment.cleanup=[main.environment.cleanup[0]!];
    const cancelled=await run(cancelledInput,true);assert.equal(cancelled.result.status,'cancelled');assert(cancelled.events.some(e=>e.type==='cleanup.end'&&e.data.status==='passed'));
    const timedInput=make(`import {test} from '@playwright/test';test('timeout',async({page})=>{await page.waitForTimeout(60000);});`,{timeoutMs:3000});
    timedInput.environment.setup=main.environment.setup;timedInput.environment.cleanup=[main.environment.cleanup[0]!];
    const timed=await run(timedInput);assert.equal(timed.result.status,'timed_out');assert(timed.events.some(e=>e.type==='cleanup.end'&&e.data.status==='passed'));
    await fs.writeFile(path.join(root,'acceptance.json'),JSON.stringify({verifiedAt:new Date().toISOString(),evidence},null,2));
  }finally{await runner.close();await cmd(['rm','-f',fixture]).catch(()=>{});}
});
