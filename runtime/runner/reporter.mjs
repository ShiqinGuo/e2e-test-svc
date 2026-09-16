import fs from 'node:fs';
import {emit,redact,input} from './state.mjs';
export default class Reporter {
  assertions = new Map();
  steps = new WeakMap();nextStep=0;
  onBegin(config,suite) {this.suite=suite;emit('suite.begin',{total:suite.allTests().length,workers:config.workers});}
  onTestBegin(test,result) {this.assertions.set(result,0);const file=test.location.file.replace('/work/tests/','');const version=input.versions[Number(file.split('/')[0])];emit('test.begin',{testId:test.id,versionId:version?.id,scenarioId:version?.scenarioId,title:test.titlePath().slice(1).join(' > '),file,attempt:result.retry,status:'running'});}
  onStepBegin(test,result,step) {this.steps.set(step,++this.nextStep);emit('step.begin',{testId:test.id,attempt:result.retry,stepId:this.steps.get(step),parentStepId:step.parent?this.steps.get(step.parent):undefined,title:step.title,category:step.category,location:step.location});}
  onStepEnd(test,result,step) {
    if(step.category==='expect')this.assertions.set(result,(this.assertions.get(result)||0)+1);
    emit('step.end',{testId:test.id,attempt:result.retry,stepId:this.steps.get(step),parentStepId:step.parent?this.steps.get(step.parent):undefined,title:step.title,category:step.category,durationMs:step.duration,status:step.error?'failed':'passed',error:step.error?.message,expected:step.error?.matcherResult?.expected,actual:step.error?.matcherResult?.actual});
  }
  onStdOut(chunk,test,result) {this.output('stdout',chunk,test,result);}
  onStdErr(chunk,test,result) {this.output('stderr',chunk,test,result);}
  output(type,chunk,test,result) {
    const value=String(chunk);
    // Fixture assertions/network are emitted as structured worker events, keeping the normal Playwright reporter API.
    for(const line of value.split('\n')) {
      if(!line)continue;
      if(line.startsWith('@@E2E@@')) {try {const event=JSON.parse(line.slice(7));emit(event.type,{...event.data,testId:test?.id,attempt:result?.retry});}catch{emit(type,{text:line,testId:test?.id});}}
      else emit(type,{text:line,testId:test?.id,attempt:result?.retry});
    }
  }
  onError(error) {emit('error',{message:error.message,stack:error.stack});}
  onTestEnd(test,result) {emit('test.end',{testId:test.id,title:test.title,attempt:result.retry,status:result.status,expectedStatus:test.expectedStatus,durationMs:result.duration,assertions:this.assertions.get(result)||0,errors:result.errors.map(e=>({message:e.message,stack:e.stack})),attachments:result.attachments.map(a=>({name:a.name,contentType:a.contentType,path:a.path?.replace('/work/','')}))});}
  onEnd(result) {
    const summary={total:0,passed:0,failed:0,skipped:0,flaky:0,unverified:0};
    for(const test of this.suite?.allTests()||[]) {
      summary.total++; const last=test.results.at(-1);
      if(last&&last.status!=='skipped'&&!(this.assertions.get(last)||0))summary.unverified++;
      if(!last || last.status==='skipped')summary.skipped++;
      else if(last.status==='passed') {summary.passed++;if(test.results.some(r=>r.status==='failed'||r.status==='timedOut'))summary.flaky++;}
      else summary.failed++;
    }
    // Empty suites are explicit errors. Metadata checks alone are never verification.
    const status=summary.total===0?'error':result.status==='timedout'?'timed_out':result.status==='interrupted'?'cancelled':summary.failed>0?'failed':result.status==='passed'?'passed':'failed';
    const verification=summary.total===0||summary.unverified+summary.skipped===summary.total?'unverified':summary.unverified+summary.skipped>0?'partial':'verified';
    const final={status,verification,summary,error:summary.total===0?'No tests were collected':undefined};
    fs.writeFileSync('/work/report-result.json',JSON.stringify(redact.object(final)));emit('suite.end',final);
  }
}
