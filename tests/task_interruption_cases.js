const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const core = fs.readFileSync('app/static/participant.js','utf8');
const helper = core.slice(core.indexOf('function interruptibleTask('),core.indexOf('// Page navigation: "Next"',core.indexOf('function interruptibleTask(')));
function environment(kind) {
  let timers=[], docListeners={}, winListeners={}, rootListeners={};
  const doc={activeElement:null,body:{},hidden:false,addEventListener(k,fn){(docListeners[k]??=[]).push(fn);},createElement(){return node();}};
  function node() { return {hidden:false,value:'',textContent:'',dataset:{},children:[],listeners:{},classList:{add(){},remove(){},toggle(){}},
    setAttribute(){}, appendChild(n){this.children.push(n);},prepend(n){this.children.unshift(n);},remove(){},
    focus(){doc.activeElement=this;},contains(n){return n===this||this.children.includes(n);},
    addEventListener(k,fn){this.listeners[k]=fn;},closest(){return null;},querySelector(){return node();}}; }
  const nodes=new Map();
  function q(sel){if(!nodes.has(sel))nodes.set(sel,node());return nodes.get(sel);}
  const root={dataset:{required:'1',order:'1',keys:'1',cats:'["1","2"]',labels:'["Left","Right"]'},querySelector:q,closest(){return null;},contains(n){return [...nodes.values()].includes(n);},addEventListener(k,fn){rootListeners[k]=fn;},dispatchEvent(){}};
  const task=q(kind==='iat'?'[data-iat-task]':kind==='assign'?'[data-assign-task]':'[data-dd-task-box]');task.hidden=true;
  const stage=q(`[data-${kind==='discounting'?'dd':kind}-stage]`);
  let startSelector=`[data-${kind==='discounting'?'dd':kind}-start]`;
  const answer=q(`[data-${kind==='discounting'?'dd':kind}-answer]`);
  q('[data-iat-plan]').textContent=JSON.stringify([{left:['A'],right:['B'],trials:[[0,0],[1,1]]}]);
  q('[data-iat-words]').textContent='[["One","A"],["Two","B"]]';q('[data-iat-cats]').textContent='{"A":"Left","B":"Right"}';
  q('[data-assign-elements]').textContent='["a","b"]';q('[data-assign-swap]').textContent='{}';
  q('[data-dd-task]').textContent=JSON.stringify({mode:'quick',unit:'{n}',amount:100,start:0,ladder:['tomorrow'],moves:[],total:2,now:'now'});
  doc.querySelector=()=>null;
  doc.querySelectorAll=(sel)=>sel===`[data-${kind==='discounting'?'dd':kind}]`?[root]:[];
  const ctx={document:doc,window:{addEventListener(k,fn){winListeners[k]=fn;}},uiT:(de,en)=>en,performance:{now:()=>1000},
    CustomEvent:class{},setTimeout(fn){timers.push(fn);},requestAnimationFrame(fn){timers.push(fn);},roundHalfUp:(v)=>v};
  vm.runInNewContext(helper,ctx);
  let source=kind==='assign'?core.slice(core.indexOf('(function () {',core.indexOf('// Page navigation: "Next"',core.indexOf('function interruptibleTask('))),core.indexOf('// Pages without navigation buttons')):fs.readFileSync('app/static/participant-tasks.js','utf8');
  vm.runInNewContext(source,ctx);
  function click(selector){let target=q(selector);rootListeners.click({target:{closest(sel){return sel===selector?target:null;}}});}
  return {task,stage,answer,doc,q,root,click,start(){click(startSelector);},abort(){task.children.find(n=>n.textContent==='Stop task').listeners.click();},
    flush(){while(timers.length){let old=timers;timers=[];old.forEach(fn=>fn());}},
    key(k,target){doc.activeElement=target; (docListeners.keydown||[]).forEach(fn=>fn({key:k,target,repeat:false,preventDefault(){}}));},
    hidden(){doc.hidden=true;docListeners.visibilitychange.forEach(fn=>fn());},blur(){(winListeners.blur||(()=>{}))();}};
}
let count=0;
for(const kind of ['iat','assign','discounting'])for(const phase of ['start','active','between'])for(const stop of ['abort','hidden']){
  const e=environment(kind);e.start();
  if(phase!=='start'){
    if(kind==='iat')e.click('[data-iat-go]');e.flush();
    if(phase==='between'){
      if(kind==='iat'){const key=e.q('[data-iat-key]');key.dataset.iatKey='0';e.click('[data-iat-key]');}
      if(kind==='assign'){const key=e.q('[data-assign-choice]');key.dataset.assignChoice='1';e.click('[data-assign-choice]');}
      if(kind==='discounting'){const key=e.q('[data-dd-choice]');key.dataset.ddChoice='S';e.click('[data-dd-choice]');}
    }
  }
  e[stop]();e.flush();
  assert.equal(e.task.hidden,true);assert.equal(e.stage.hidden,false);assert.equal(e.answer.value,'');
  assert.match(e.stage.children[0].textContent,/will not be evaluated/);
  e.doc.hidden=false;e.start();if(kind==='iat')e.click('[data-iat-go]');e.flush();
  assert.equal(e.task.hidden,false);assert.equal(e.answer.value,'');count++;
}
for(const kind of ['assign','iat']){
  const e=environment(kind);e.start();if(kind==='iat')e.click('[data-iat-go]');e.flush();
  const outside={closest(){return null;}};e.key('e',outside);assert.equal(e.answer.value,'');
  if(kind==='assign'){e.key('e',e.task);assert.notEqual(e.answer.value,'');}
  else {e.key('e',e.task);e.flush();e.key('i',outside);assert.equal(e.answer.value,'');e.key('i',e.task);assert.notEqual(e.answer.value,'');}
  count++;
}
// a lost focus (a system notification, a click into a frame) does not stop a running task (W-A05)
for(const kind of ['iat','assign','discounting']){
  const e=environment(kind);e.start();if(kind==='iat')e.click('[data-iat-go]');e.flush();
  e.blur();e.flush();
  assert.equal(e.task.hidden,false);assert.equal(e.stage.children.some(n=>/will not be evaluated/.test(n.textContent)),false);
  count++;
}
console.log(`${count} task abort, late callback, restart and focus cases passed`);
