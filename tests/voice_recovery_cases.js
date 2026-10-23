const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('app/static/participant-voice.js', 'utf8');
async function check(fallback, api, errorName) {
  const nodes = new Map();
  function node(key) { if (!nodes.has(key)) nodes.set(key,{hidden:true,value:'',textContent:'',addEventListener(type, fn){this[type]=fn;},getAttribute(){return null;}}); return nodes.get(key); }
  const box = {querySelector(key){return key === '.p-voice-text .p-hint' && !fallback ? null : node(key);},closest(){return {};},getAttribute(){return '60';},classList:{contains(){return false;}}};
  const doc = {querySelectorAll(){return [box];},addEventListener(){}};
  const ctx = {document:doc,window:{MediaRecorder:api,DataTransfer:api},MediaRecorder:{isTypeSupported(){return true;}},navigator:{mediaDevices:{getUserMedia(){return Promise.reject({name:errorName});}}},uiT(de,en){return en;}};
  vm.runInNewContext(source,ctx);
  if(api) { node('[data-voice-toggle]').click(); await new Promise(setImmediate); }
  const message=node('[data-voice-problem]').textContent;
  if(api) { assert.match(message,/Recording failed/); assert.equal(message.includes('Or type'),fallback); assert.equal(node('[data-voice-problem]').hidden,false); }
  else if(!fallback) { assert.match(message,/not possible in this browser/); assert.equal(node('[data-voice-problem]').hidden,false); }
  else assert.equal(node('.p-voice-text .p-hint').textContent,'Your answer:');
}
(async()=>{let n=0;for(const fallback of [false,true])for(const kind of [null,'NotAllowedError','NotFoundError','NotReadableError']){await check(fallback,kind!==null,kind);n++;}console.log(`${n} voice error/API cases passed`);})().catch(e=>{console.error(e);process.exitCode=1;});
