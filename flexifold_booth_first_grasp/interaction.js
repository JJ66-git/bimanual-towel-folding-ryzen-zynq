'use strict';
const $ = id => document.getElementById(id);
let config, activeTab = 'demo', recording = false, replaying = false;
let guardStarted = false, pending = null, sending = false, debounce, revision = 0;
let rows = [], previewRows = [], duration = 0, baseline, latest, animation, downloadUrl;
let recordingStart = 0, sampleSource = '', savedClip = null;
const names = {target_bounds:'目标越界', act_bounds:'基础目标越界', joint_step:'步差超限', tracking:'跟踪偏差超限', correction_magnitude:'修正量超限', correction_slew:'修正变化率超限', gripper_ownership:'夹爪归属不符', start_pose:'起始姿态偏差', action_budget:'步数超限', feedback_stale:'反馈过期', profile_mismatch:'配置不匹配'};
const modeReady = fetch('/mode', {signal:AbortSignal.timeout(3000)}).then(async r => {
  if (!r.ok) throw Error('配置读取失败');
  config = await r.json();
  const board = config.source === 'board';
  $('mode').textContent = board ? 'FPGA 已配置 · 等待实际回包' : '软件参考预演 · 未连接 FPGA';
  $('footer-note').textContent = board ? `历史样本诊断 / FPG1 ${config.protocol} / 主臂采样独立运行` : '软件参考预演 / 主臂采样独立运行';
  $('result-source').textContent = board ? '本次 FPGA 回包' : '软件参考检查';
  $('leader-state').textContent = config.leader_error || (config.leader_enabled ? '等待完整主臂数据' : '尚未接入主臂；连接后可开始真实采集。');
  $('j2').min = config.slider_min; $('j2').max = config.slider_max; $('j2').step = config.slider_step;
  const fraction = (config.upper_millirad - config.slider_min) / (config.slider_max - config.slider_min);
  $('j2').style.setProperty('--limit', `calc(${fraction * 100}% + ${(0.5 - fraction) * 34}px)`);
  $('angle-limit').textContent = `绿色区 ≤ ${(config.upper_millirad / 1000).toFixed(3)} rad`;
  for (const id of ['j2','reset-guard','original']) $(id).disabled = false;
  drawJoint(config.slider_max);
  return config;
}).catch(e => {
  $('mode').textContent = '本地服务不可用，请刷新重试';
  guardState('error', '!', '配置读取失败', e.message);
  $('leader-state').textContent = '本地服务不可用，请刷新页面。';
  throw e;
});
modeReady.catch(() => {});

function tab(name) {
  if (recording || captureBusy) return;
  if (activeTab === 'capture') stopReplay();
  activeTab = name;
  for (const item of ['demo','guard','capture']) $(item + '-panel').hidden = item !== name;
  document.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-pressed', b.dataset.tab === name));
  $('clip').pause(); $('grasp-video').pause();
  history.replaceState(null, '', '#' + name);
  if (name === 'guard' && !guardStarted) modeReady.then(() => resetGuard()).catch(() => {});
  if (name === 'capture') { paintTrace(rows.length ? rows : [], duration); if (rows.length) pose(rows[rows.length-1], rows[0]); }
}
document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => tab(b.dataset.tab));
document.querySelectorAll('[data-open]').forEach(b => b.onclick = () => tab(b.dataset.open));
$('fullscreen').onclick = async () => {
  try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); }
  catch { $('fullscreen').textContent = '请按 F11 全屏'; }
};
function endpoint(angle, length=165) { return [255 + length * Math.cos(angle), 230 - length * Math.sin(angle)]; }
function drawJoint(millirad) {
  const angle = millirad / 1000, upper = config.upper_millirad / 1000;
  const over = millirad > config.upper_millirad;
  const end = endpoint(angle), limit = endpoint(upper, 195), arc = endpoint(angle, 83);
  $('joint-arm').setAttribute('d', `M255 230L${end[0]} ${end[1]}`);
  $('joint-arm').setAttribute('stroke', over ? '#ff847e' : '#4be1ba');
  $('joint-arc').setAttribute('d', `M338 230A83 83 0 0 0 ${arc[0]} ${arc[1]}`);
  $('joint-arc').setAttribute('stroke', over ? '#ff847e' : '#4be1ba');
  $('joint-limit').setAttribute('d', `M255 230L${limit[0]} ${limit[1]}`);
  $('joint-tip').setAttribute('transform', `translate(${end[0]} ${end[1]}) rotate(${90-angle * 180 / Math.PI})`);
  $('angle-value').value = angle.toFixed(3) + ' rad';
  $('angle-value').dataset.over = over;
  $('j2').setAttribute('aria-valuetext', angle.toFixed(3) + ' rad，' + (over ? '越界区域' : '范围内'));
  $('pose-label').textContent = over ? '目标在越界区域' : '目标已回到范围内';
}
function guardState(state, symbol, title, detail) {
  $('guard-result').dataset.state = state;
  $('result-symbol').textContent = symbol;
  $('verdict').textContent = title;
  $('guard-detail').textContent = detail;
}
function queueCheck(millirad, original=false) {
  clearTimeout(debounce);
  pending = {millirad, original, revision:++revision};
  guardState('waiting', '…', '等待本次检查', '目标已改变，正在取得对应结果。');
  $('board-meta').textContent = '';
  $('after-result').textContent = '等待本次检查';
  $('after-result').style.color = '';
  $('after-angle').textContent = original ? '真实原样本' : (millirad / 1000).toFixed(3) + ' rad';
  processChecks();
}
async function processChecks() {
  if (sending || !pending) return;
  sending = true;
  const edit = pending; pending = null;
  try {
    const response = await fetch(edit.original ? '/check/original' : '/check/angle', {
      method:'POST', headers:{'X-FlexiFold-Action':'check','Content-Type':'application/json'},
      body: edit.original ? undefined : JSON.stringify({millirad:edit.millirad}), signal:AbortSignal.timeout(4000)
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || '检查未完成');
    if (edit.millirad === config.slider_max && !edit.original) {
      $('before-result').textContent = result.mask ? '目标越界' : '未触发违规';
      $('before-result').style.color = result.mask ? 'var(--red)' : 'var(--green)';
    }
    // A result for an earlier slider position must never colour the current pose green.
    const current = edit.original || Number($('j2').value) === edit.millirad;
    if (!pending && current && edit.revision === revision) {
      const board = result.source === 'board';
      const match = !board || result.reference_match === true;
      const reason = result.names.map(n => names[n] || n).join('、');
      const state = !match ? 'error' : result.mask ? 'fail' : 'pass';
      guardState(state, state === 'pass' ? '✓' : state === 'fail' ? '!' : '?',
        !match ? '检查结果不一致' : result.mask ? '这条动作被拦下' : '这条动作检查通过',
        !match ? 'FPGA 回包与软件参考不同，请工作人员核对。' : result.mask ? `${reason}。试着把圆点拖回绿色区。` : '当前目标未触发诊断规则。');
      $('board-meta').textContent = `${board ? 'FPGA' : '软件参考'} · ${result.value.toFixed(3)} rad · SEQ ${result.seq}`;
      $('after-result').textContent = !match ? '待核对' : result.mask ? '仍有违规' : '检查通过';
      $('after-result').style.color = state === 'pass' ? 'var(--green)' : 'var(--red)';
      $('after-angle').textContent = result.value.toFixed(3) + ' rad';
      $('diagnostic').textContent = JSON.stringify(result, null, 2);
      if (board) $('mode').textContent = `FPGA 已回包 · ${config.protocol} · SEQ ${result.seq}`;
    }
  } catch (e) {
    if (!pending && edit.revision === revision && (edit.original || Number($('j2').value) === edit.millirad)) {
      guardState('error', '?', '未取得本次结果', e.message);
      $('after-result').textContent = '未收到结果';
      $('board-meta').textContent = '请确认连接后，重新挑战或再次拖动。';
    }
  } finally { sending = false; if (pending) processChecks(); }
}
function resetGuard() {
  guardStarted = true;
  $('j2').value = config.slider_max;
  drawJoint(config.slider_max);
  $('before-result').textContent = '正在检查';
  queueCheck(config.slider_max);
}
$('j2').oninput = () => {
  revision++;
  drawJoint(Number($('j2').value));
  guardState('waiting', '…', '目标已调整', '停下滑块，自动检查这个角度。');
  clearTimeout(debounce);
  debounce = setTimeout(() => queueCheck(Number($('j2').value)), 350);
};
$('j2').onchange = () => queueCheck(Number($('j2').value));
$('reset-guard').onclick = resetGuard;
$('original').onclick = () => {
  clearTimeout(debounce);
  // The archived value is between slider ticks; retain it exactly in the request.
  $('j2').value = Math.round(config.original_rad * 1000 / config.slider_step) * config.slider_step;
  drawJoint(config.original_rad * 1000);
  queueCheck(null, true);
};

for (let i=0; i<6; i++) {
  const el = document.createElement('div'); el.className = 'joint-bar';
  el.innerHTML = `J${i+1}<span id="delta-${i}">—°</span><div class="joint-track"><div class="joint-fill" id="fill-${i}"></div></div>`;
  $('joint-bars').append(el);
}
function pose(row, origin) {
  const opening = Math.max(0, Math.min(80, row.gripper_mm));
  const gap = 12 + opening * .65;
  $('jaw-left').setAttribute('d', `M${250-gap} 98V149H${260-gap}`);
  $('jaw-right').setAttribute('d', `M${250+gap} 98V149H${240+gap}`);
  $('live-grip').textContent = `夹爪 ${row.gripper_mm.toFixed(1)} mm`;
  for (let i=0; i<6; i++) {
    const delta = row.joint_deg[i] - origin.joint_deg[i];
    $('delta-'+i).textContent = (delta >= 0 ? '+' : '') + delta.toFixed(1) + '°';
    $('fill-'+i).style.width = Math.min(100, Math.abs(delta) / 30 * 100) + '%';
  }
}
pose({gripper_mm:40, joint_deg:[0,0,0,0,0,0]}, {joint_deg:[0,0,0,0,0,0]});
const jointDelta = (row, origin) => Math.max(...row.joint_deg.map((v,i) => Math.abs(v - origin.joint_deg[i])));
let episodeId=null, captureBusy=false, selectedSource='piper_leader_commands';
let cameraStream=null, cameraEnabled=false, framePending=null, frameTimer, clockTimer;
let liveReady=false, stopping=false, lastFrameCount=0, lastSampleCount=0;
const frameCanvas=document.createElement('canvas');
function paintTrace(data, elapsed=0) {
  const canvas=$('trace-plot'),box=canvas.getBoundingClientRect();if(!box.width)return;
  const dpr=window.devicePixelRatio||1;canvas.width=Math.round(box.width*dpr);canvas.height=Math.round(box.height*dpr);
  const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
  const w=box.width,h=box.height,left=34,right=w-10;
  const horizon=recording?Math.max(5000,elapsed):Math.max(1,duration);
  const x=t=>left+(right-left)*Math.min(1,t/horizon);
  ctx.font='10px system-ui';
  for(let i=0;i<4;i++){
    const t=horizon*i/3;ctx.strokeStyle='#263e54';ctx.beginPath();ctx.moveTo(x(t),4);ctx.lineTo(x(t),h-4);ctx.stroke();
    $('time-'+i).textContent=(t/1000).toFixed(1)+' 秒';
  }
  const peak=data.reduce((max,r)=>Math.max(max,jointDelta(r,data[0])),1);
  for(const [rowIndex,max,color,get] of [[0,80,'#4be1ba',r=>r.gripper_mm],[1,peak,'#ffb15c',r=>jointDelta(r,data[0])]]){
    const top=rowIndex*h/2+12,bottom=(rowIndex+1)*h/2-8;
    ctx.fillStyle=color;ctx.fillText(max.toFixed(0),2,top+4);ctx.fillText('0',8,bottom);
    ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();
    data.forEach((r,i)=>{const yy=bottom-Math.max(0,Math.min(1,get(r)/max))*(bottom-top);if(i===0)ctx.moveTo(x(r.t_ms),yy);else ctx.lineTo(x(r.t_ms),yy);});ctx.stroke();
  }
  ctx.strokeStyle='#e8f5ff';ctx.beginPath();ctx.moveTo(x(elapsed),0);ctx.lineTo(x(elapsed),h);ctx.stroke();
}
function stopReplay(){cancelAnimationFrame(animation);replaying=false;$('play-trace').textContent='回放我的动作';}
function setClock(ms,label){$('capture-count').innerHTML=(ms/1000).toFixed(1)+'<span>'+label+'</span>';}
function playTrace(){
  stopReplay();if(!rows.length)return;
  replaying=true;$('play-trace').textContent='重新回放';const start=performance.now(),speed=Math.max(1,duration/8000);
  function advance(now){
    const elapsed=Math.min(duration,(now-start)*speed),row=rows.findLast(r=>r.t_ms<=elapsed)||rows[0];
    pose(row,rows[0]);paintTrace(rows,elapsed);setClock(elapsed,'秒 / 回放 ×'+speed.toFixed(1));
    if(elapsed<duration)animation=requestAnimationFrame(advance);else{replaying=false;$('play-trace').textContent='回放我的动作';}
  }
  animation=requestAnimationFrame(advance);
}
$('play-trace').onclick=playTrace;
function controls(){
  document.querySelectorAll('[data-tab]').forEach(b=>b.disabled=recording||captureBusy);
  $('record-grasp').hidden=recording;
  $('record-grasp').disabled=captureBusy||(selectedSource==='piper_leader_commands'&&!liveReady);
  $('stop-grasp').hidden=!recording;$('stop-grasp').disabled=captureBusy;
  $('task-instruction').disabled=recording||captureBusy;
  $('rehearse').disabled=recording||captureBusy;
  $('record-dot').dataset.recording=recording;
}
async function captureRequest(path,data){
  const response=await fetch(path,{method:'POST',headers:{'X-FlexiFold-Action':'record','Content-Type':'application/json'},body:JSON.stringify(data),signal:AbortSignal.timeout(8000)});
  const value=await response.json();if(!response.ok)throw Error(value.error||'采集请求未完成');return value;
}
function startClock(){
  clearInterval(clockTimer);clockTimer=setInterval(()=>{if(recording&&!stopping)setClock(performance.now()-recordingStart,'秒 / 采集中，完成后点结束');},100);
}
function liveCounts(status){
  lastFrameCount=status.frame_count;lastSampleCount=status.sample_count;
  $('capture-live-count').textContent=`${lastSampleCount} 笔动作 · ${lastFrameCount} 张图像`;
  $('a-count').textContent=lastSampleCount+' 笔带时间戳的动作';
  $('v-count').textContent=lastFrameCount?lastFrameCount+' 张本次场景图像':cameraEnabled?'相机已接入，等待图像':'未接相机，未采图像';
}
function adoptRecording(status){
  if(episodeId!==status.episode_id||!recording){
    stopReplay();episodeId=status.episode_id;recording=true;stopping=false;previewRows=[];
    selectedSource=status.source;recordingStart=performance.now()-status.duration_ms;
    $('task-instruction').value=status.instruction;
    $('recorded-actions').hidden=true;$('capture-summary').hidden=true;$('capture-folder').hidden=true;
    $('capture-source').textContent=selectedSource==='piper_leader_commands'?'正在采集 · 真实主臂指令':'正在排练 · 合成动作';
    $('capture-status').textContent='正在采集，时长由您决定';
    $('capture-detail').textContent='请示范动作；做完后点击“结束并保存”。';
    controls();startClock();startFrames();
  }
  liveCounts(status);
  if(status.last_sample){const row=status.last_sample;if(!previewRows.length||row.t_ms>previewRows.at(-1).t_ms)previewRows.push(row);pose(row,previewRows[0]);paintTrace(previewRows,status.duration_ms);}
}
function useClip(value){
  if(!Array.isArray(value.samples)||!value.samples.length||value.samples.some(r=>!Number.isFinite(r.t_ms)||!Number.isFinite(r.gripper_mm)||!Array.isArray(r.joint_deg)||r.joint_deg.length!==6||r.joint_deg.some(v=>!Number.isFinite(v))))throw Error('已保存数据的动作内容不完整，请查看专用文件夹。');
  savedClip=value;rows=value.samples;duration=value.duration_ms;sampleSource=value.source;
  if(downloadUrl)URL.revokeObjectURL(downloadUrl);
  downloadUrl=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
  $('save-trace').href=downloadUrl;$('save-trace').download=value.episode_id+'.json';
  $('save-trace').textContent='下载数据副本';
  const real=sampleSource==='piper_leader_commands',complete=value.state==='complete';
  $('capture-source').textContent=real?'本次采集 · 真实主臂指令':'示例排练 · 合成数据';
  $('capture-status').textContent=complete?'这一条示范，已保存':'采集中断，片段已保存';
  $('capture-detail').textContent=value.error||(real?'画面、指令和动作将作为后续整理与训练的原始素材。':'这是合成排练，已保存到独立的 rehearsals 文件夹。');
  const extrema=rows.reduce((a,r)=>({low:Math.min(a.low,r.gripper_mm),high:Math.max(a.high,r.gripper_mm),peak:Math.max(a.peak,jointDelta(r,rows[0]))}),{low:Infinity,high:-Infinity,peak:0});
  $('capture-summary').textContent=`${rows.length} 笔动作 · ${value.frame_count} 张图像 · ${(duration/1000).toFixed(1)} 秒 / 1 条任务指令。夹爪 ${extrema.low.toFixed(1)}–${extrema.high.toFixed(1)} mm，最大关节变化 ${extrema.peak.toFixed(1)}°`;
  $('capture-summary').hidden=false;$('recorded-actions').hidden=false;$('capture-folder').hidden=false;
  $('saved-path').textContent=value.saved_dir;$('record-grasp').textContent='再采一条';
  setClock(duration,'秒 / 本条实际采集时长');liveCounts(value);pose(rows.at(-1),rows[0]);paintTrace(rows,duration);
}
async function refreshEpisodes(){
  try{
    const response=await fetch('/capture/episodes',{signal:AbortSignal.timeout(3000)});if(!response.ok)return;
    const value=await response.json();$('data-root').textContent=value.directory;
    $('episode-list').textContent=value.episodes.length?'':'专用文件夹尚无采集记录。';
    for(const row of value.episodes){
      const item=document.createElement('div'),title=document.createElement('strong'),detail=document.createElement('small');
      title.textContent=(row.source==='synthetic_rehearsal'?'[合成排练] ':'[真实采集] ')+row.episode_id;
      detail.textContent=`${row.instruction} / ${row.sample_count} 笔动作 / ${row.frame_count} 张图像 / ${row.state}`;
      item.append(title);item.append(detail);$('episode-list').append(item);
    }
  }catch{$('episode-list').textContent='记录列表暂时不可用；已保存的数据仍在专用文件夹。';}
}
async function finishCapture(){
  if(!episodeId||captureBusy)return;
  captureBusy=true;stopping=true;clearInterval(frameTimer);controls();
  $('capture-status').textContent='正在结束并保存';
  try{
    const value=await captureRequest('/capture/stop',{episode_id:episodeId});
    recording=false;stopping=false;clearInterval(clockTimer);
    if(value.state==='save_error'){
      $('capture-status').textContent='数据保存失败';
      $('capture-detail').textContent=(value.error||'请检查磁盘与目录权限。')+' 部分文件可能保留在：'+value.saved_dir;
      $('capture-source').textContent='采集已结束 · 保存失败';
      if(savedClip){$('recorded-actions').hidden=false;$('save-trace').textContent='下载上一条完整数据';}
    }else useClip(value);
    await refreshEpisodes();
  }catch(error){
    $('capture-status').textContent='结束保存尚未确认';$('capture-detail').textContent=error.message+' 可再次点击结束；请勿重复开始。';
  }finally{captureBusy=false;controls();}
}
$('stop-grasp').onclick=finishCapture;
$('record-grasp').onclick=async()=>{
  if(recording||captureBusy)return;captureBusy=true;stopReplay();controls();
  try{
    const value=await captureRequest('/capture/start',{instruction:$('task-instruction').value,source:selectedSource});
    adoptRecording(value);refreshEpisodes();
  }catch(error){$('capture-status').textContent='未能开始采集';$('capture-detail').textContent=error.message;}
  finally{captureBusy=false;controls();}
};
$('rehearse').onclick=()=>{
  if(recording||captureBusy)return;
  selectedSource=selectedSource==='piper_leader_commands'?'synthetic_rehearsal':'piper_leader_commands';
  const real=selectedSource==='piper_leader_commands';
  $('capture-source').textContent=real?'真实主臂采集':'示例排练 · 合成动作';
  $('rehearse').textContent=real?'无主臂？切换示例排练':'切换回真实主臂采集';
  $('capture-status').textContent=real?'准备采集一条示范':'示例模式也由您决定开始与结束';
  $('capture-detail').textContent=real?'点开始，自由示范，再点结束保存。':'点开始模拟采集，再点结束；数据会存入 rehearsals。';controls();
};
$('enable-camera').onclick=async()=>{
  if(cameraStream){
    cameraStream.getTracks().forEach(track=>track.stop());cameraStream=null;cameraEnabled=false;
    $('capture-camera').srcObject=null;$('capture-camera').hidden=true;$('camera-placeholder').hidden=false;
    $('enable-camera').textContent='接入相机';$('camera-state').textContent='相机已关闭';return;
  }
  try{
    if(!navigator.mediaDevices?.getUserMedia)throw Error('当前浏览器不支持相机，请使用 localhost 或 127.0.0.1 打开。');
    cameraStream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:640},height:{ideal:480}},audio:false});
    $('capture-camera').srcObject=cameraStream;await $('capture-camera').play();cameraEnabled=true;
    $('capture-camera').hidden=false;$('camera-placeholder').hidden=true;$('enable-camera').textContent='关闭相机';
    $('camera-state').textContent='实时相机 / 采集中约 5 帧每秒';
    cameraStream.getVideoTracks()[0].addEventListener('ended',()=>{cameraEnabled=false;$('camera-state').textContent='相机已断开';});
  }catch(error){
    if(cameraStream)cameraStream.getTracks().forEach(track=>track.stop());cameraStream=null;cameraEnabled=false;
    $('capture-camera').srcObject=null;$('capture-camera').hidden=true;$('camera-placeholder').hidden=false;
    $('enable-camera').textContent='接入相机';$('camera-state').textContent='未接入相机：'+error.message;
  }
};
async function captureFrame(){
  if(!recording||stopping||!cameraEnabled||framePending)return;
  const video=$('capture-camera');if(video.readyState<2||!video.videoWidth)return;
  const task=(async()=>{
    try{
      frameCanvas.width=Math.min(640,video.videoWidth);frameCanvas.height=Math.round(frameCanvas.width*video.videoHeight/video.videoWidth);
      frameCanvas.getContext('2d').drawImage(video,0,0,frameCanvas.width,frameCanvas.height);
      const jpeg=frameCanvas.toDataURL('image/jpeg',.7).split(',')[1];
      const value=await captureRequest('/capture/frame',{episode_id:episodeId,jpeg_base64:jpeg,client_elapsed_ms:performance.now()-recordingStart});
      lastFrameCount=value.frame_count;$('v-count').textContent=lastFrameCount+' 张本次场景图像';
    }catch(error){if(recording&&!stopping)$('camera-state').textContent='图像保存未确认：'+error.message;}
  })();
  framePending=task;
  try{await task;}finally{if(framePending===task)framePending=null;}
}
function startFrames(){clearInterval(frameTimer);frameTimer=setInterval(captureFrame,200);}
async function pollCapture(){
  if(activeTab==='capture'||recording){
    try{
      if(config?.leader_enabled&&!recording){
        const response=await fetch('/leader/status',{signal:AbortSignal.timeout(1500)});if(!response.ok)throw Error('读取主臂失败');
        const status=await response.json();liveReady=!!status.ready;
        $('leader-state').textContent=liveReady?'主臂就绪 · 可自由开始与结束':status.error||'主臂数据不完整或已过期。';
        if(liveReady&&!rows.length){latest=status.sample;baseline||=latest;pose(latest,baseline);}
      }
      const response=await fetch('/capture/status',{signal:AbortSignal.timeout(2000)});if(!response.ok)throw Error('读取采集状态失败');
      const status=await response.json();
      if(status.state==='recording')adoptRecording(status);
      else if(recording&&status.episode_id===episodeId&&!captureBusy)await finishCapture();
      controls();
    }catch{
      $('leader-state').textContent='暂时无法读取实时状态。采集中可再次点击结束确认保存。';
      if(!recording){liveReady=false;controls();}
    }
  }
  setTimeout(pollCapture,250);
}
modeReady.then(()=>{refreshEpisodes();pollCapture();}).catch(()=>{});
window.addEventListener('beforeunload',event=>{
  if(recording){event.preventDefault();event.returnValue='请先结束采集并保存。';}
});
let px=.5,py=.6;
function pin() {px=Math.max(0,Math.min(1,px));py=Math.max(0,Math.min(1,py));$('grasp-pin').style.left=px*100+'%';$('grasp-pin').style.top=py*100+'%';$('grasp-pin').hidden=false;$('point-status').textContent='抓点已标记，可与历史实机画面对照。';}
$('mark-grasp').onclick=e=>{if(e.detail){const box=$('mark-grasp').getBoundingClientRect();px=(e.clientX-box.left)/box.width;py=(e.clientY-box.top)/box.height;}pin();};
$('mark-grasp').onkeydown=e=>{const offset={ArrowLeft:[-.02,0],ArrowRight:[.02,0],ArrowUp:[0,-.02],ArrowDown:[0,.02]}[e.key];if(offset){e.preventDefault();px+=offset[0];py+=offset[1];pin();}};
$('play-grasp').onclick=()=>{$('grasp-video').hidden=false;$('grasp-video').currentTime=0;$('grasp-video').play().catch(()=>{});};
window.addEventListener('resize',()=>{if(!recording&&rows.length)paintTrace(rows,duration);});
window.addEventListener('pagehide',()=>{if(downloadUrl)URL.revokeObjectURL(downloadUrl);if(cameraStream)cameraStream.getTracks().forEach(track=>track.stop());});
const initial=location.hash.slice(1);if(['guard','capture'].includes(initial))tab(initial);
