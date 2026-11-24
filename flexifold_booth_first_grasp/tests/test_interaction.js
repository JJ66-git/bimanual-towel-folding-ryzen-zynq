/* Exercise the actual browser controller with a small DOM fixture; no network. */
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const elements = new Map(), timers = new Set();
class Element {
  constructor(id='') { this.id=id;this.dataset={};this.style={setProperty(){}};this.hidden=false;this.disabled=false;this.value='';this.attrs={};this.textContent=''; }
  set innerHTML(html) { this.html=html;for(const match of html.matchAll(/id="([^"]+)"/g))elements.set(match[1],new Element(match[1])); }
  get innerHTML(){return this.html||'';}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getBoundingClientRect(){return {width:600,height:150,left:0,top:0};}
  getContext(){return new Proxy({}, {get:()=>()=>{},set:()=>true});}
  toDataURL(){return 'data:image/jpeg;base64,/9j/2Q==';}
  append(el){}
  pause(){}
  play(){return Promise.resolve();}
  click(){if(!this.disabled)return this.onclick?.({detail:0});}
  dispatchEvent(e){return this['on'+e.type]?.(e);}
}
const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
for(const match of html.matchAll(/id="([^"]+)"/g))elements.set(match[1],new Element(match[1]));
elements.get('record-grasp').disabled=true;
const tabs=['demo','guard','capture'].map(tab=>{const b=new Element();b.dataset.tab=tab;return b;});
const opens=['guard','capture'].map(tab=>{const b=new Element();b.dataset.open=tab;return b;});
let qaDrop=false,qaSlow=false,qaResolve,sequence=0,captureSequence=0,captureState={state:'idle'},episodes=[];
const live={joint_deg:[0,0,0,0,0,0],gripper_mm:40};
const recorded={source:'piper_leader_commands',duration_ms:3000,nominal_hz:25,samples:Array.from({length:75},(_,i)=>({t_ms:(i+1)*40,joint_deg:[0,i*.2,0,0,0,0],gripper_mm:55-i*.5}))};
const fetched=[];
async function fetchFixture(url, options={}){
 fetched.push(url);let value;
 if(url==='/mode')value={source:'reference',protocol:'v3',slider_min:1750,slider_max:2650,slider_step:10,upper_millirad:2540,original_rad:1.798876,leader_enabled:true};
 else if(url==='/leader/status')value={ready:true,sample:live};
 else if(url==='/capture/start'){
   const data=JSON.parse(options.body);captureSequence++;
   captureState={state:'recording',episode_id:'ep_test_'+captureSequence,source:data.source,instruction:data.instruction,
     sample_count:1,frame_count:0,duration_ms:0,saved_dir:'/dedicated/'+(data.source==='synthetic_rehearsal'?'rehearsals/':'episodes/')+'ep_test_'+captureSequence,last_sample:recorded.samples[0]};
   value={...captureState};
 }else if(url==='/capture/status')value={...captureState};
 else if(url==='/capture/episodes')value={directory:'/dedicated',episodes};
 else if(url==='/capture/frame'){captureState.frame_count++;value={frame_count:captureState.frame_count};}
 else if(url==='/capture/stop'){
   if(qaDrop)return {ok:false,json:async()=>({error:'测试保存超时'})};
   captureState={...captureState,state:'complete',duration_ms:recorded.duration_ms,sample_count:recorded.samples.length,samples:recorded.samples,frames:[]};
   episodes.push({...captureState});value={...captureState};
 }else{
   const angle=url==='/check/original'?1.798876:JSON.parse(options.body).millirad/1000;
   if(qaSlow){qaSlow=false;await new Promise(r=>qaResolve=r);}
   const mask=angle>2.540001?1:0;
   value={source:'reference',seq:sequence++,value:angle,mask,names:mask?['target_bounds']:[],reference_match:null};
 }
 return {ok:true,json:async()=>value};
}
function timeout(fn,ms){const t=setTimeout(()=>{timers.delete(t);fn(performance.now());},ms);timers.add(t);return t;}
const sandbox={console,AbortSignal,Blob,URL,performance,fetch:fetchFixture,
 setTimeout:timeout,clearTimeout:t=>{clearTimeout(t);timers.delete(t);},
 setInterval:(fn,ms)=>{const t=setInterval(fn,ms);timers.add(t);return t;},clearInterval:t=>{clearInterval(t);timers.delete(t);},
 requestAnimationFrame:fn=>timeout(fn,16),cancelAnimationFrame:t=>{clearTimeout(t);timers.delete(t);},
 document:{getElementById:id=>{assert(elements.has(id),'unknown DOM id '+id);return elements.get(id);},createElement:()=>new Element(),querySelectorAll:q=>q==='[data-tab]'?tabs:opens},
 history:{replaceState(){}},location:{hash:''},Event:class{constructor(type){this.type=type;}},
 navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}],getVideoTracks:()=>[{addEventListener(){}}]})}},
 assert,window:{devicePixelRatio:1,addEventListener(){}}};
const context=vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(root,'interaction.js'),'utf8'),context);
const $=id=>elements.get(id);
async function wait(condition){for(let i=0;i<100;i++){if(condition())return;await new Promise(r=>setTimeout(r,10));}throw Error('condition timeout');}
async function run(code){return vm.runInContext(code,context);}
(async()=>{
 try{
 await run('modeReady');tabs[1].click();await wait(()=>$('guard-result').dataset.state==='fail');
 assert.equal($('before-result').textContent,'目标越界');
 $('j2').value=2540;$('j2').dispatchEvent({type:'input'});$('j2').dispatchEvent({type:'change'});
 await wait(()=>$('guard-result').dataset.state==='pass');
 assert.equal($('after-result').textContent,'检查通过');
 qaSlow=true;await run('queueCheck(null,true)');await wait(()=>qaResolve);
 $('j2').value=2650;$('j2').dispatchEvent({type:'input'});qaResolve();await new Promise(r=>setTimeout(r,20));
 assert.equal($('guard-result').dataset.state,'waiting','stale original reply must not mark current target passed');
 $('j2').value=2540;$('j2').dispatchEvent({type:'change'});await wait(()=>$('guard-result').dataset.state==='pass');
 tabs[2].click();await wait(()=>!$('record-grasp').disabled);
 $('task-instruction').value='抓住毛巾，合爪后轻抬';
 await $('enable-camera').click();
 $('capture-camera').readyState=2;$('capture-camera').videoWidth=640;$('capture-camera').videoHeight=480;
 await $('record-grasp').click();
 assert.equal($('stop-grasp').hidden,false);
 assert.equal($('recorded-actions').hidden,true);
 assert.equal(captureState.state,'recording');
 assert.equal(captureState.instruction,'抓住毛巾，合爪后轻抬');
 await run('captureFrame()');
 assert.equal(captureState.frame_count,1);
 await $('stop-grasp').click();
 assert.equal($('capture-source').textContent,'本次采集 · 真实主臂指令');
 assert.equal($('recorded-actions').hidden,false);
 assert.match($('capture-summary').textContent,/75 笔动作/);
 assert.match($('saved-path').textContent,/dedicated\/episodes/);
 assert.equal($('capture-folder').hidden,false);
 const href=$('save-trace').href;
 const saved=await(await fetch(href)).json();assert.deepEqual(saved.samples,recorded.samples);
 assert.equal(saved.instruction,'抓住毛巾，合爪后轻抬');
 await $('record-grasp').click();qaDrop=true;await $('stop-grasp').click();
 assert.equal($('capture-status').textContent,'结束保存尚未确认');
 assert.equal($('save-trace').href,href,'previous valid download preserved on stop failure');
 assert.equal($('stop-grasp').hidden,false,'stop remains available for retry');
 qaDrop=false;await $('stop-grasp').click();
 $('rehearse').click();assert.equal($('capture-source').textContent,'示例排练 · 合成动作');
 await $('record-grasp').click();assert.equal(captureState.state,'recording');
 assert.equal(captureState.source,'synthetic_rehearsal');
 await $('stop-grasp').click();
 assert.match($('saved-path').textContent,/dedicated\/rehearsals/);
 const example=await(await fetch($('save-trace').href)).json();assert.equal(example.source,'synthetic_rehearsal');
 console.log('PASS: guard regression, manual start/stop, instruction, camera upload, saved folder, JSON copy, stop retry, separate rehearsal storage');
 }finally{for(const t of timers){clearTimeout(t);clearInterval(t);}timers.clear();}
})().catch(error=>{console.error(error);process.exitCode=1;});
