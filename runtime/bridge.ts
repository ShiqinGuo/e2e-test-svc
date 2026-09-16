// Trusted tool controller. Imported scenario code executes only in DockerRunner containers.
import readline from 'node:readline';
import { DockerRunner } from '../src/runtime/runner.js';
import { DockerRecorder } from '../src/runtime/recorder.js';
const runner = new DockerRunner();
const recorder = new DockerRecorder();
const send=(message:any)=>process.stdout.write(JSON.stringify(message)+'\n');
async function dispatch(message:any) {
  const {id,op,input}=message;
  try {
    let result:any;
    switch(op) {
      case 'available': result={runner:await runner.available(),recorder:await recorder.available()};break;
      case 'run': result=await new Promise(async(resolve,reject)=>{
        try {await runner.start(input,{event:event=>send({id,event}),complete:resolve});} catch(error) {reject(error);}
      });break;
      case 'cancel': await runner.cancel(input.id);result={};break;
      case 'recording_start': result=await new Promise(async(resolve,reject)=>{
        try {await recorder.start(input,{ready:()=>resolve(recorder.target(input.id)),error:message=>reject(Error(message))});}catch(error){reject(error);}
      });break;
      case 'recording_read': result=await recorder.read(input.id);break;
      case 'recording_stop': result=await recorder.stop(input.id);break;
      case 'recording_target': result=recorder.target(input.id);break;
      case 'close': await Promise.allSettled([runner.close(),recorder.close()]);result={};break;
      default: throw Error('Unknown runtime operation');
    }
    send({id,result});
  } catch(error) {send({id,error:error instanceof Error?error.message:'Runtime error'});}
}
readline.createInterface({input:process.stdin}).on('line',line=>{
  try {void dispatch(JSON.parse(line));} catch {send({error:'Invalid controller message'});}
}).on('close',()=>{void Promise.allSettled([runner.close(),recorder.close()]).finally(()=>process.exit(0));});
