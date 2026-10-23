const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('app/static/participant-crowd.js','utf8');
const fetchSource=source.slice(source.indexOf('      function fetchFigures('),source.indexOf('      if (fixBox) {',source.indexOf('      function fetchFigures(')));
const tick=()=>new Promise(setImmediate);
async function crowd(kind){
 let timeout,problem,locked=0,added=0;
 const input={name:'i2',value:'retained',type:'text',disabled:false};
 const question={inert:false,querySelectorAll(){return [input];}};
 const button={disabled:false};
 const box={querySelector(){return problem||null;},appendChild(n){problem=n;}};
 const ctx={box,question,form:{querySelector(){return null;}},data:{fetch:'/same-lock'},document:{createElement(){return {dataset:{},setAttribute(k,v){this[k]=v;}};}},
  FormData:class{append(){}},AbortController,uiT:(de,en)=>en,liveOwn:()=>1,fixAnswer(){locked++;input.disabled=true;},addFigures(){added++;},
  setTimeout(fn){timeout=fn;return 1;},clearTimeout(){},fetch(url,options){
   if(kind==='network')return Promise.reject(new TypeError('network'));
   if(kind==='timeout')return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject({name:'AbortError'})));
   return Promise.resolve({ok:kind==='success',status:kind==='validation'?400:kind==='success'?200:503,json(){return Promise.resolve(kind==='success'?{avatars:[]}:{error:kind==='validation'?'Please choose a value.':'page'});}});
  }};
 vm.runInNewContext(fetchSource+'\nfetchFigures(button);',Object.assign(ctx,{button}));
 assert.equal(question.inert,true);assert.equal(button.disabled,true);
 if(kind==='timeout')timeout();
 await tick();await tick();
 assert.equal(question.inert,false);assert.equal(input.value,'retained');
 if(kind==='success'){assert.equal(locked,1);assert.equal(added,1);}
 else {assert.equal(locked,0);assert.equal(input.disabled,false);assert.equal(button.disabled,false);assert.equal(problem.role,'alert');assert.match(problem.textContent,/No confirmation received/);if(kind==='validation')assert.match(problem.textContent,/Please choose a value/);}
}
async function report(){
 let timers=[],calls=[],state='network',click;
 const status={textContent:''},checked={textContent:''},heading={textContent:''};
 const check={dataset:{},addEventListener(k,fn){click=fn;},click(){}};
 const panel={dataset:{url:'/same-job?job=one',download:'1'},querySelector(s){return s==='[role="status"]'?status:s==='[data-report-checked]'?checked:s==='[data-report-check]'?check:heading;}};
 const ctx={document:{querySelector(){return panel;}},window:{location:{replace(){}}},AbortController,de_en:(de,en)=>en,Date,
 setTimeout(fn,ms){const timer={fn,ms};timers.push(timer);return timer;},clearTimeout(t){timers=timers.filter(x=>x!==t);},
 fetch(url){calls.push(url);if(state==='network')return Promise.reject(new TypeError('offline'));return Promise.resolve({status:state==='waiting'?202:204,headers:{get(){return state==='ready'?'1':null;}}});}};
 vm.runInNewContext(fs.readFileSync('app/static/report_wait.js','utf8'),ctx);
 timers.shift().fn();await tick();assert.match(status.textContent,/could not be retrieved/);
 state='waiting';click({preventDefault(){}});await tick();assert.match(status.textContent,/being prepared/);assert.match(checked.textContent,/Last confirmed/);
 state='ready';click({preventDefault(){}});await tick();assert.equal(check.dataset.ready,'1');assert.equal(check.href,panel.dataset.url);assert.equal(new Set(calls).size,1);
}
(async()=>{for(const kind of ['network','timeout','http','validation','success'])await crowd(kind);await report();console.log('5 feedback acknowledgement/error cases and report reconnect/same-job case passed');})().catch(e=>{console.error(e);process.exitCode=1;});
