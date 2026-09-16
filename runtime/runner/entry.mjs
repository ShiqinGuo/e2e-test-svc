import fs from 'node:fs';
import {spawn} from 'node:child_process';
import {input,emit,redact} from './state.mjs';
import {actions} from './actions.mjs';

const variables={...(input.environment.variables||{}),...(input.environment.secretVariables||{})};
if(!fs.existsSync('/work/node_modules'))fs.symlinkSync('/opt/runner/node_modules','/work/node_modules','dir');
fs.writeFileSync('/work/package.json',JSON.stringify({type:'module'}));
fs.mkdirSync('/work/artifacts',{recursive:true});fs.writeFileSync('/work/variables.json',JSON.stringify(variables));
for(const [key,value] of Object.entries(input.environment.websites||{}))process.env[`E2E_WEBSITE_${key}`]=String(value);
for(const [key,value] of Object.entries(input.environment.apiBases||{}))process.env[`E2E_API_${key}`]=String(value);
for(const [key,value] of Object.entries(variables))process.env[`E2E_VAR_${key}`]=typeof value==='string'?value:JSON.stringify(value);
let child;let interrupted=false;let setupError;let cleanupError;
process.on('SIGTERM',()=>{interrupted=true;child?.kill('SIGINT');});process.on('SIGINT',()=>{interrupted=true;child?.kill('SIGINT');});
try {
  await actions('setup',variables);
  if(!interrupted) {
    const code=await new Promise((resolve,reject)=>{
      child=spawn('/opt/runner/node_modules/.bin/playwright',['test','--config=/work/playwright.config.ts'],{cwd:'/work',env:process.env,stdio:['ignore','pipe','pipe']});
      child.stdout.on('data',chunk=>process.stdout.write(chunk));child.stderr.on('data',chunk=>emit('stderr',{text:String(chunk)}));child.on('error',reject);child.on('close',resolve);
    });
    if(code!==0&&!fs.existsSync('/work/report-result.json'))setupError=`Playwright process exited with code ${code}`;
  }
} catch(error){setupError=error.message;emit('error',{message:error.message});}
finally {
  try {await actions('cleanup',JSON.parse(fs.readFileSync('/work/variables.json','utf8')));}catch(error){cleanupError=error.message;}
}

// Host always invokes the trusted, networkless sanitizer after this process/container is gone.
let result={status:'error',verification:'unverified',summary:{total:0,passed:0,failed:0,skipped:0,flaky:0,unverified:0}};
try{result=JSON.parse(fs.readFileSync('/work/report-result.json','utf8'));}catch{}
if(setupError||cleanupError){result.status='error';result.error=[setupError,cleanupError&&`Cleanup failed: ${cleanupError}`].filter(Boolean).join('; ');}
if(interrupted)result.status='cancelled';
fs.writeFileSync('/work/result.json',JSON.stringify(redact.object(result)));emit('run.complete',result);
