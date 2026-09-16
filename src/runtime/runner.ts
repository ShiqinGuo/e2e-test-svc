import {spawn,execFile} from 'node:child_process';
import {promisify} from 'node:util';
import * as fs from 'node:fs/promises';
import path from 'node:path';

const execFileAsync=promisify(execFile);
export type ApiAction={name:string;apiBase:string;method:string;path:string;headers?:Record<string,string>;body?:unknown;expectedStatus?:number;capture?:Record<string,string>};
export type RunInput={id:string;projectId:string;workDir:string;versions:Array<{id:string;scenarioId:string;code:string;checks:unknown[];modules?:Record<string,string>}>;environment:{websites:Record<string,string>;apiBases:Record<string,string>;variables?:Record<string,unknown>;secretVariables?:Record<string,string>;roles?:Array<{name:string;storageState?:any;headers?:Record<string,string>}>;setup?:ApiAction[];cleanup?:ApiAction[];allowedOrigins?:string[]};role?:string;timeoutMs:number;retries:number;platformOrigins?:string[]};
export type RunEvent={type:string;timestamp:string;data:any};
export type RunArtifact={name:string;path:string;contentType:string;kind:string};
export type RunResult={status:string;verification:string;summary:{total:number;passed:number;failed:number;skipped:number;flaky:number;unverified:number};error?:string;artifacts:RunArtifact[]};
export type RunHandlers={event:(event:RunEvent)=>void;complete:(result:RunResult)=>void};
export type NetworkPolicy={allowedOrigins:string[];platformOrigins?:string[]};
export type NetworkSandbox={network:string;proxyName:string;proxyAddress:string;close:()=>Promise<void>};
const imageDefault='e2e-runner:1.63.0';
const label='e2e.platform=true';
async function docker(args:string[],timeout=30000) {
  const result=await execFileAsync('docker',args,{timeout,maxBuffer:4*1024*1024,windowsHide:true});return result.stdout.trim();
}
function runtimeName(kind:string,id:string){return `e2e-${kind}-${id.replace(/[^a-zA-Z0-9_-]/g,'').slice(0,48)}`;}
export async function createNetworkSandbox(id:string,policy:NetworkPolicy,policyFile:string,image=imageDefault):Promise<NetworkSandbox> {
  const network=runtimeName('net',id);const proxyName=runtimeName('egress',id);
  const close=async()=>{await docker(['rm','-f',proxyName]).catch(()=>{});await docker(['network','rm',network]).catch(()=>{});};
  await fs.mkdir(path.dirname(policyFile),{recursive:true});await fs.writeFile(policyFile,JSON.stringify(policy));
  try {
    await docker(['network','create','--internal','--driver','bridge','--opt','com.docker.network.bridge.gateway_mode_ipv4=isolated','--label',label,network]);
    await docker(['run','-d','--name',proxyName,'--label',label,'--label',`e2e.run=${id}`,'--network','bridge','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','64','--memory','128m','--cpus','0.25','--user','1000:1000','--mount',`type=bind,source=${path.resolve(policyFile)},target=/policy.json,readonly`,'--entrypoint','node',image,'/opt/runner/egress.mjs']);
    await docker(['network','connect','--alias','egress',network,proxyName]);
    const inspect=JSON.parse(await docker(['inspect',proxyName]));const address=inspect[0].NetworkSettings.Networks[network].IPAddress;
    return {network,proxyName,proxyAddress:`http://${address}:3128`,close};
  }catch(error){await close();throw error;}
}
function redactFor(input:RunInput) {
  const values:string[]=Object.values(input.environment.secretVariables||{});
  for(const role of input.environment.roles||[]){values.push(...Object.values(role.headers||{}));for(const c of role.storageState?.cookies||[])values.push(c.value);for(const o of role.storageState?.origins||[])for(const v of o.localStorage||[])values.push(v.value);}
  const secrets=[...new Set(values.filter(v=>typeof v==='string'&&v.length>0))].sort((a,b)=>b.length-a.length);
  return (text:string)=>{let value=text;for(const secret of secrets)value=value.split(secret).join('[REDACTED]').split(encodeURIComponent(secret)).join('[REDACTED]');return value.replace(/\b(authorization|proxy-authorization|cookie|set-cookie)\s*[:=]\s*[^\r\n]+/gi,'$1: [REDACTED]');};
}
function normalizePolicy(input:RunInput):NetworkPolicy {
  const allowedOrigins=[...Object.values(input.environment.websites),...Object.values(input.environment.apiBases),...(input.environment.allowedOrigins||[])].map(raw=>{
    const url=new URL(raw);if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw new Error('Environment targets must be HTTP(S) URLs without embedded credentials');return url.origin;
  });
  return {allowedOrigins:[...new Set(allowedOrigins)],platformOrigins:(input.platformOrigins||['http://localhost:4100','http://localhost:5173']).map(raw=>new URL(raw).origin)};
}
export class DockerRunner {
  readonly image:string;
  private active=new Map<string,{promise:Promise<void>;cancel?:'cancelled'|'timed_out';container:string}>();
  constructor(options:{image?:string}={}) {this.image=options.image||process.env.RUNNER_IMAGE||imageDefault;}
  async available():Promise<{available:boolean;reason?:string}> {
    try{const kind=await docker(['info','--format','{{.OSType}}']);if(kind!=='linux')return {available:false,reason:'Linux Docker containers are required'};await docker(['image','inspect',this.image]);return {available:true};}
    catch{return {available:false,reason:`Docker or ${this.image} is unavailable; build with docker build -f runtime/runner/Dockerfile -t ${this.image} .`};}
  }
  async start(input:RunInput,handlers:RunHandlers):Promise<void> {
    if(this.active.has(input.id))throw new Error('Run is already active');
    const status=await this.available();if(!status.available)throw new Error(status.reason);if(this.active.has(input.id))throw new Error('Run is already active');
    if(!input.versions.length)throw new Error('Run requires at least one immutable version');
    if(input.role&&!input.environment.roles?.some(role=>role.name===input.role))throw new Error('Selected role does not exist in environment');
    const policy=normalizePolicy(input);
    const item={promise:Promise.resolve(),container:runtimeName('run',input.id)} as {promise:Promise<void>;cancel?:'cancelled'|'timed_out';container:string};
    this.active.set(input.id,item);
    item.promise=this.execute(input,handlers,item,policy).finally(()=>this.active.delete(input.id));
  }
  async cancel(runId:string):Promise<void> {const item=this.active.get(runId);if(!item)return;item.cancel='cancelled';await docker(['stop','--time','20',item.container],25000).catch(()=>{});}
  async close():Promise<void>{await Promise.all([...this.active.keys()].map(id=>this.cancel(id)));await Promise.all([...this.active.values()].map(item=>item.promise));}
  private async execute(input:RunInput,handlers:RunHandlers,item:{container:string;cancel?:'cancelled'|'timed_out'},policy:NetworkPolicy) {
    const execution=path.join(path.resolve(input.workDir),'execution');const redact=redactFor(input);const events:string[]=[];let sandbox:NetworkSandbox|undefined;let timer:NodeJS.Timeout|undefined;
    const safeValue=(value:any):any=>typeof value==='string'?redact(value):Array.isArray(value)?value.map(safeValue):value&&typeof value==='object'?Object.fromEntries(Object.entries(value).map(([key,val])=>[key,/^(authorization|proxy-authorization|cookie|set-cookie|password|access_token|refresh_token)$/i.test(key)?'[REDACTED]':safeValue(val)])):value;
    const event=(type:string,data:any,timestamp=new Date().toISOString())=>{const safe={type,timestamp,data:safeValue(data)};events.push(JSON.stringify(safe));handlers.event(safe);};
    let result:RunResult={status:'error',verification:'unverified',summary:{total:0,passed:0,failed:0,skipped:0,flaky:0,unverified:0},artifacts:[]};
    try {
      await fs.mkdir(path.join(execution,'artifacts'),{recursive:true});await fs.writeFile(path.join(execution,'input.json'),JSON.stringify(input));
      const role=input.environment.roles?.find(r=>r.name===input.role);
      for(const [index,version] of input.versions.entries()) {
        const dir=path.join(execution,'tests',String(index).padStart(6,'0'));await fs.mkdir(path.join(dir,'modules'),{recursive:true});
        // Redirect the conventional public import to extended fixtures. Network policy is enforced independently of this convenience transform.
        const instrument=(code:string)=>code.replace(/(\bfrom\s*|\bimport\s*|\brequire\s*\(\s*|\bimport\s*\(\s*)(['"])@playwright\/test\2/g,(_match,prefix,quote)=>`${prefix}${quote}/opt/runner/fixtures.ts${quote}`);
        await fs.writeFile(path.join(dir,`${version.id.replace(/[^\w-]/g,'')}.spec.ts`),instrument(version.code));
        for(const [name,code] of Object.entries(version.modules||{})) {if(!/^[a-zA-Z0-9_-]+\.ts$/.test(name))throw new Error('Invalid reusable module filename');await fs.writeFile(path.join(dir,'modules',name),instrument(code));}
      }
      sandbox=await createNetworkSandbox(input.id,policy,path.join(input.workDir,'policy.json'),this.image);
      const config={testDir:'/work/tests',outputDir:'/work/artifacts',fullyParallel:false,workers:1,retries:input.retries,timeout:Math.min(input.timeoutMs,60000),globalTimeout:input.timeoutMs,reporter:[['/opt/runner/reporter.mjs']],use:{browserName:'chromium',headless:true,baseURL:input.environment.websites.main||Object.values(input.environment.websites)[0],storageState:role?.storageState,extraHTTPHeaders:role?.headers,proxy:{server:sandbox.proxyAddress},launchOptions:{args:['--proxy-bypass-list=<-loopback>']},serviceWorkers:'block',trace:{mode:'on',sources:false},screenshot:'on',video:'off'}};
      await fs.writeFile(path.join(execution,'playwright.config.ts'),`export default ${JSON.stringify(config)};\n`);
      if(item.cancel)throw new Error('Run was cancelled before execution');
      event('run.begin',{runId:input.id,versionIds:input.versions.map(v=>v.id),allowedOrigins:policy.allowedOrigins});
      timer=setTimeout(()=>{item.cancel??='timed_out';void docker(['stop','--time','20',item.container],25000).catch(()=>{});},input.timeoutMs+1000);
      const args=['run','--name',item.container,'--label',label,'--label',`e2e.run=${input.id}`,'--init','--network',sandbox.network,'--dns','127.0.0.1','--read-only','--tmpfs','/tmp:rw,nosuid,size=256m','--shm-size','256m','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','256','--memory','1536m','--cpus','2','--user','1000:1000','--mount',`type=bind,source=${execution},target=/work`,'--env',`E2E_PROXY_URL=${sandbox.proxyAddress}`,this.image];
      const exit=await new Promise<number|null>((resolve,reject)=>{
        const process=spawn('docker',args,{windowsHide:true,stdio:['ignore','pipe','pipe']});let pending='';
        process.stdout.on('data',(chunk:Buffer)=>{pending+=chunk.toString();let end;while((end=pending.indexOf('\n'))!==-1){const line=pending.slice(0,end);pending=pending.slice(end+1);if(line.startsWith('@@E2E@@')){try{const parsed=JSON.parse(line.slice(7));event(parsed.type,parsed.data,parsed.timestamp);}catch{event('stdout',{text:line});}}else if(line.trim())event('stdout',{text:line});}});
        process.stderr.on('data',(chunk:Buffer)=>event('stderr',{text:chunk.toString()}));process.on('error',reject);process.on('close',code=>{if(pending.trim())event('stdout',{text:pending});resolve(code);});
      });
      try{result={...JSON.parse(await fs.readFile(path.join(execution,'result.json'),'utf8')),artifacts:[]};}catch{result.error=`Execution stopped without a final report (container exit ${exit})`;}
    }catch(error){result.error=redact(error instanceof Error?error.message:String(error));event('error',{message:result.error});}
    finally {
      if(timer)clearTimeout(timer);await docker(['rm','-f',item.container]).catch(()=>{});await sandbox?.close();
      if(item.cancel)result.status=item.cancel;
      const artifactDir=path.join(execution,'artifacts');
      const artifactInfo=await fs.lstat(artifactDir).catch(()=>null);
      if(artifactInfo?.isSymbolicLink()||artifactInfo&&!artifactInfo.isDirectory())await fs.unlink(artifactDir);
      await fs.mkdir(artifactDir,{recursive:true});
      let sanitized=false;
      const sanitizer=runtimeName('sanitize',input.id);
      try {
        // Recreate trusted input after the test process is dead: imported code cannot remove the redaction policy.
        await fs.rm(path.join(execution,'input.json'),{force:true});await fs.writeFile(path.join(execution,'input.json'),JSON.stringify(input));
        await docker(['run','--rm','--name',sanitizer,'--label',label,'--label',`e2e.run=${input.id}`,'--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','32','--memory','768m','--cpus','1','--user','1000:1000','--mount',`type=bind,source=${execution},target=/work`,'--entrypoint','node',this.image,'/opt/runner/sanitize.mjs'],30000);sanitized=true;
      } catch(error){result.error=[result.error,'Artifact sanitization failed; raw evidence was withheld'].filter(Boolean).join('; ');if(!item.cancel)result.status='error';event('error',{message:'Artifact sanitization failed; raw evidence was withheld'});}
      finally{await docker(['rm','-f',sanitizer]).catch(()=>{});}
      await fs.rm(path.join(artifactDir,'events.jsonl'),{force:true});await fs.writeFile(path.join(artifactDir,'events.jsonl'),events.join('\n')+'\n',{flag:'wx'});
      const root=path.join(execution,'artifacts');
      const scan=async(dir:string):Promise<void>=>{for(const file of await fs.readdir(dir,{withFileTypes:true})){if(file.isSymbolicLink()||file.name.startsWith('.'))continue;const full=path.join(dir,file.name);if(file.isDirectory())await scan(full);else if(file.isFile()){const stat=await fs.stat(full);if(stat.size>128*1024*1024)continue;const ext=path.extname(file.name).toLowerCase();const kind=file.name==='trace.zip'?'trace':['.png','.jpg','.jpeg'].includes(ext)?'screenshot':file.name.includes('network')?'network':['.jsonl','.log'].includes(ext)?'log':'attachment';result.artifacts.push({name:path.relative(root,full).replaceAll('\\','/'),path:full,kind,contentType:ext==='.zip'?'application/zip':ext==='.png'?'image/png':ext==='.jpg'||ext==='.jpeg'?'image/jpeg':ext==='.json'?'application/json':ext==='.html'?'text/html':'text/plain'});}}};
      if(sanitized)await scan(root);else result.artifacts.push({name:'events.jsonl',path:path.join(root,'events.jsonl'),kind:'log',contentType:'text/plain'});
      // Credentials are needed only while the isolated run is alive. The API owns its encrypted snapshot.
      await Promise.all(['input.json','variables.json','playwright.config.ts'].map(name=>fs.rm(path.join(execution,name),{force:true})));
      result.error=result.error?redact(result.error):undefined;handlers.complete(result);
    }
  }
}
