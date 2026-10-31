"""The timeline page served by editor.py (one self-contained HTML file, no external requests)."""

PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>funkbox timeline</title>
<style>
:root{--bg:#0f1014;--panel:#171920;--line:#2a2d38;--text:#e8e9ee;--dim:#9096a6;--acc:#ff4d5e;--ok:#3ccf8e;--warn:#f5b84a;--sel:#6aa8ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.4 system-ui,Segoe UI,Roboto,sans-serif}
header{display:flex;gap:12px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:5;flex-wrap:wrap}
header h1{font-size:15px;margin:0 8px 0 0;font-weight:600}.pill{padding:2px 8px;border-radius:99px;background:var(--panel);color:var(--dim);font-size:12px}
.pill.dirty{background:#3a2a10;color:var(--warn)}.pill.ok{color:var(--ok)}.pill.err{background:#3a1218;color:var(--acc)}
button{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:5px 10px;cursor:pointer;font:inherit}
button:hover{border-color:#4a4f60}button.primary{background:var(--acc);border-color:var(--acc);color:#fff}button:disabled{opacity:.45;cursor:default}
.spacer{flex:1}label.zoom{display:flex;gap:6px;align-items:center;color:var(--dim);font-size:12px}
#banner{display:none;padding:8px 16px;background:#22304a;border-bottom:1px solid var(--line)}#banner button{margin-left:8px}
main{display:grid;grid-template-columns:1fr 330px;gap:0;min-height:calc(100vh - 50px)}
#trackwrap{overflow-x:auto;padding:18px 16px 30px;border-right:1px solid var(--line)}
#ruler{position:relative;height:18px;margin-bottom:4px;color:var(--dim);font-size:11px}#ruler span{position:absolute;top:0;border-left:1px solid var(--line);padding-left:3px;height:18px}
#track{display:flex;gap:2px;align-items:stretch;position:relative;min-height:150px}
.shot{position:relative;flex:none;background:var(--panel);border:1px solid var(--line);border-radius:6px;overflow:hidden;cursor:grab;user-select:none;touch-action:none}
.shot.sel{outline:2px solid var(--sel)}.shot.drag{opacity:.75;z-index:3;cursor:grabbing;box-shadow:0 8px 20px #0008}.shot.new{border-style:dashed}
.shot img{display:block;width:100%;height:150px;object-fit:cover;background:#000;pointer-events:none}
.shot .meta{padding:4px 6px;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.shot .meta b{font-size:12px}
.shot .dur{color:var(--dim)}.badge{display:inline-block;padding:0 4px;border-radius:3px;background:#2b3040;margin-right:3px;font-size:10px}
.badge.vo{background:#33244a}.handle{position:absolute;right:0;top:0;bottom:0;width:9px;cursor:ew-resize;background:linear-gradient(90deg,transparent,#ffffff22)}
.handle:hover{background:#ff4d5e88}.endcard{opacity:.7;cursor:default;background:repeating-linear-gradient(45deg,#1b1d25,#1b1d25 8px,#20232c 8px,#20232c 16px)}
#marker{position:absolute;top:0;bottom:0;width:3px;background:var(--sel);display:none;border-radius:2px}
aside{padding:14px 16px;overflow-y:auto;max-height:calc(100vh - 50px)}aside h2{font-size:13px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;margin:16px 0 8px}
.row{display:flex;gap:8px;align-items:center;margin:6px 0}.row>span{width:80px;color:var(--dim);font-size:12px}
input,select{background:#0c0d11;color:var(--text);border:1px solid var(--line);border-radius:5px;padding:4px 6px;font:inherit;width:100%}
input[type=range]{padding:0}.frames{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:6px}.frames img{width:100%;border-radius:4px;background:#000;min-height:40px}
.frames div{font-size:11px;color:var(--dim)}ul.msgs{margin:0;padding-left:18px;font-size:12px}ul.msgs li.e{color:var(--acc)}ul.msgs li.w{color:var(--warn)}
.log{font-size:12px;color:var(--dim)}.log b{color:var(--text);font-weight:500}.log div{margin-bottom:6px}
video{width:100%;border-radius:6px;margin-top:8px;background:#000}.hint{color:var(--dim);font-size:12px}
@media (max-width:800px){main{grid-template-columns:1fr}#trackwrap{border-right:0}}
</style></head><body>
<header><h1 id="name">timeline</h1><span id="status" class="pill">loading</span><span id="total" class="pill"></span>
<span class="spacer"></span><label class="zoom">zoom<input id="zoom" type="range" min="30" max="260" value="90" style="width:110px"></label>
<button id="undo" title="Ctrl+Z">Undo</button><button id="revert">Revert</button><button id="render">Render preview</button><button id="save" class="primary" title="Ctrl+S">Save</button></header>
<div id="banner"></div>
<main><div id="trackwrap"><div id="ruler"></div><div id="track"><div id="marker"></div></div>
<p class="hint">Drag a shot to move it. Drag its right edge to change its length (whole and half beats when the reel has a beat grid). Click a shot to edit it. Delete removes the selected shot. Nothing is written until you Save, and an invalid reel is never saved.</p>
<div id="preview"></div></div>
<aside><div id="inspector"><p class="hint">Select a shot.</p></div><h2>Checks</h2><ul id="msgs" class="msgs"></ul><h2>Edit log</h2><div id="log" class="log"></div></aside></main>
<script>
let S=null,W=null,dirty=false,sel=null,undo=[],pps=90,probes={};
const $=id=>document.getElementById(id),esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function api(p,o){const r=await fetch(p,o);let b={};try{b=await r.json()}catch(e){}return [r.status,b]}
async function load(){const [c,b]=await api('/api/state');if(c!==200){status('error: '+(b.error||c),'err');return}S=b;W=structuredClone(b.reel);dirty=false;undo=[];draw()}
function status(t,k){const e=$('status');e.textContent=t;e.className='pill '+(k||'')}
function push(){undo.push(structuredClone(W));if(undo.length>60)undo.shift();dirty=true}
function spanOf(id){return S.spans.find(s=>s.id===id)}
function dur(sh){const sp=spanOf(sh.id),saved=(S.reel.shots||[]).find(x=>x.id===sh.id);
 if(sp&&saved&&JSON.stringify(saved.duration)===JSON.stringify(sh.duration)&&JSON.stringify(saved.source)===JSON.stringify(sh.source))return sp.duration;
 const d=sh.duration;if(typeof d==='number')return d;if(typeof d==='string'&&d.startsWith('beats:'))return parseFloat(d.slice(6))*(S.beat||0.5);return sp?sp.duration:2}
function fmtDur(sh){const d=sh.duration;return (typeof d==='string'?d+' ':'')+dur(sh).toFixed(2)+'s'}
function draw(){
 $('name').textContent=S.name;status(dirty?'unsaved changes':'saved',dirty?'dirty':'ok');
 const shots=W.shots||[];let t=0;shots.forEach(s=>t+=dur(s));const end=S.spans.find(s=>s.kind==='end');
 $('total').textContent=(t+(end?end.duration:0)).toFixed(1)+'s · '+shots.length+' shots'+(S.beat?' · beat '+S.beat.toFixed(3)+'s':'');
 const tr=$('track');tr.querySelectorAll('.shot').forEach(e=>e.remove());
 shots.forEach((sh,i)=>{const d=dur(sh),el=document.createElement('div');el.className='shot'+(sel===sh.id?' sel':'')+(spanOf(sh.id)?'':' new');
  el.style.width=Math.max(46,d*pps)+'px';el.dataset.i=i;const cap=sh.caption&&sh.caption.copy_id;
  el.innerHTML=(spanOf(sh.id)?`<img src="/api/thumb?id=${encodeURIComponent(sh.id)}&m=${S.mtime}" loading="lazy" alt="">`:`<img alt="">`)+
   `<div class="meta"><b>${esc(sh.id)}</b><br><span class="dur">${esc(fmtDur(sh))}</span><br>${cap?`<span class="badge">${esc(cap)}</span>`:''}${sh.vo?`<span class="badge vo">vo ${esc(sh.vo)}</span>`:''}</div><div class="handle"></div>`;
  wire(el,sh);tr.appendChild(el)});
 if(end){const el=document.createElement('div');el.className='shot endcard';el.style.width=Math.max(46,end.duration*pps)+'px';
  el.innerHTML=`<img src="/api/thumb?id=${encodeURIComponent(end.id)}&m=${S.mtime}" alt=""><div class="meta"><b>end card</b><br><span class="dur">${end.duration.toFixed(2)}s</span></div>`;tr.appendChild(el)}
 const ru=$('ruler');ru.innerHTML='';const step=pps>120?0.5:pps>60?1:2;for(let x=0;x<=t+(end?end.duration:0);x+=step){const s=document.createElement('span');s.style.left=(x*pps)+'px';s.textContent=x%1?'':x+'s';ru.appendChild(s)}
 const m=$('msgs');m.innerHTML=dirty?'<li class="hint">Checks run when you save.</li>':(S.errors.map(e=>`<li class="e">${esc(e)}</li>`).join('')+S.warnings.map(w=>`<li class="w">${esc(w)}</li>`).join('')||'<li class="hint">No problems.</li>');
 $('log').innerHTML=(S.edits||[]).slice().reverse().map(r=>`<div><b>${r.by==='timeline'?'you':r.by==='file'?'file (Claude or an editor)':esc(r.by)}</b> ${esc(r.time.replace('T',' ').slice(0,16))}<br>${r.changes.map(esc).join('<br>')}</div>`).join('')||'<span class="hint">Nothing yet.</span>';
 $('undo').disabled=!undo.length;$('save').disabled=!dirty;$('revert').disabled=!dirty;inspector();renderState(S.render)}
function wire(el,sh){
 el.addEventListener('pointerdown',ev=>{if(ev.button!==0)return;const handle=ev.target.classList.contains('handle');el.setPointerCapture(ev.pointerId);
  const x0=ev.clientX,d0=dur(sh);let moved=false;const blocks=[...$('track').querySelectorAll('.shot:not(.endcard)')];
  const mv=e=>{const dx=e.clientX-x0;if(!moved&&Math.abs(dx)<5)return;if(!moved){moved=true;push();if(!handle)el.classList.add('drag')}
   if(handle){let nd=Math.max(0.1,d0+dx/pps);if(S.beat){const n=Math.max(0.5,Math.round(nd/S.beat*2)/2);sh.duration='beats:'+n;nd=n*S.beat}else{nd=Math.round(nd*20)/20;sh.duration=nd}
    el.style.width=Math.max(46,nd*pps)+'px';el.querySelector('.dur').textContent=fmtDur(sh)}
   else{el.style.transform=`translateX(${dx}px)`;const idx=target(e.clientX,blocks,el);const mk=$('marker');mk.style.display='block';
    const ref=blocks.filter(b=>b!==el)[idx];const r0=$('track').getBoundingClientRect();
    mk.style.left=((ref?ref.getBoundingClientRect().left:blocks[blocks.length-1].getBoundingClientRect().right)-r0.left-2)+'px'}};
  const up=e=>{el.releasePointerCapture(ev.pointerId);el.removeEventListener('pointermove',mv);el.removeEventListener('pointerup',up);$('marker').style.display='none';
   if(!moved){sel=sh.id;draw();return}
   if(!handle){const from=+el.dataset.i,to=target(e.clientX,blocks,el);const [s]=W.shots.splice(from,1);W.shots.splice(to,0,s)}
   sel=sh.id;draw()};
  el.addEventListener('pointermove',mv);el.addEventListener('pointerup',up)})}
function target(x,blocks,self){const others=blocks.filter(b=>b!==self);for(let i=0;i<others.length;i++){const r=others[i].getBoundingClientRect();if(x<r.left+r.width/2)return i}return others.length}
function field(label,html){return `<div class="row"><span>${label}</span>${html}</div>`}
function opts(list,cur,none){return (none?`<option value="">${none}</option>`:'')+list.map(v=>`<option${v===cur?' selected':''}>${esc(v)}</option>`).join('')}
function inspector(){const box=$('inspector'),sh=(W.shots||[]).find(s=>s.id===sel);if(!sh){box.innerHTML='<p class="hint">Select a shot.</p>';return}
 const src=sh.source||{},cap=sh.caption||{};let h=`<h2 style="margin-top:0">${esc(sh.id)}</h2>`;
 h+=field('length',`<input id="f-dur" value="${esc(typeof sh.duration==='string'?sh.duration:String(sh.duration))}" title="auto, beats:N or seconds">`);
 h+=field('source',`<input value="${esc(src.video||src.image||src.color||src.funk2||'')}" disabled>`);
 if(src.video){const tr=src.trim||[0,null];h+=field('in',`<input id="f-in" type="number" step="0.05" min="0" value="${tr[0]}">`)+field('out',`<input id="f-out" type="number" step="0.05" min="0" value="${tr[1]??''}" placeholder="end">`)+
  `<input id="f-range" type="range" min="0" max="100" step="0.05" value="${tr[0]}" title="scrub the in point">`+
  `<div class="frames"><div>in<img id="fr-in" alt=""></div><div>out<img id="fr-out" alt=""></div></div>`+field('speed',`<input id="f-speed" value="${esc(JSON.stringify(src.speed??1))}" title="2, or [1, 3] to ramp">`)}
 if(src.video||src.image)h+=field('focus x',`<input id="f-fx" type="range" min="0" max="1" step="0.01" value="${(src.focus||[0.5,0.5])[0]}">`);
 h+=field('caption',`<select id="f-cap">${opts(S.copy_ids,cap.copy_id,'(none)')}</select>`);
 if(cap.copy_id)h+=field('reveal',`<select id="f-rev">${opts(['none','typewriter','words','pop'],cap.reveal||'none')}</select>`)+field('anchor',`<select id="f-anc">${opts(['top','center','bottom'],cap.anchor||'bottom')}</select>`);
 h+=field('voice line',`<select id="f-vo">${opts(S.copy_ids,sh.vo,'(none)')}</select>`);
 h+=`<div class="row"><button id="b-dup">Duplicate</button><button id="b-del">Delete</button></div>`;box.innerHTML=h;
 const set=(fn)=>{push();fn();draw()};
 $('f-dur').onchange=e=>set(()=>{const v=e.target.value.trim();sh.duration=/^[0-9.]+$/.test(v)?parseFloat(v):v});
 if(src.video){const upd=()=>{const tr=sh.source.trim||[0,null];$('fr-in').src=`/api/frame?src=${encodeURIComponent(src.video)}&t=${tr[0]}`;$('fr-out').src=`/api/frame?src=${encodeURIComponent(src.video)}&t=${Math.max(0,(tr[1]??tr[0]+1)-0.04)}`};upd();
  probe(src.video).then(p=>{if(p){$('f-range').max=p.duration;$('f-out').max=p.duration;$('f-in').max=p.duration}});
  const trim=(a,b)=>set(()=>{sh.source.trim=[Math.max(0,+a),b===''||b==null?null:+b].filter((v,i)=>i===0||v!==null);if(sh.source.trim.length===1)sh.source.trim.push((probes[src.video]||{}).duration||+a+1)});
  $('f-in').onchange=e=>trim(e.target.value,$('f-out').value);$('f-out').onchange=e=>trim($('f-in').value,e.target.value);
  $('f-range').oninput=e=>{$('fr-in').src=`/api/frame?src=${encodeURIComponent(src.video)}&t=${e.target.value}`};
  $('f-range').onchange=e=>{const len=((sh.source.trim||[0,1])[1]||0)-((sh.source.trim||[0,0])[0]);trim(e.target.value,(+e.target.value+Math.max(0.2,len)).toFixed(2))};
  $('f-speed').onchange=e=>set(()=>{let v;try{v=JSON.parse(e.target.value)}catch(_){return}if(v===1)delete sh.source.speed;else sh.source.speed=v})}
 if($('f-fx'))$('f-fx').onchange=e=>set(()=>{sh.source.focus=[+e.target.value,(sh.source.focus||[0.5,0.5])[1]]});
 $('f-cap').onchange=e=>set(()=>{if(!e.target.value)delete sh.caption;else sh.caption=Object.assign({style:'red-box',reveal:'none',anchor:'top'},sh.caption||{},{copy_id:e.target.value})});
 if($('f-rev'))$('f-rev').onchange=e=>set(()=>sh.caption.reveal=e.target.value);if($('f-anc'))$('f-anc').onchange=e=>set(()=>sh.caption.anchor=e.target.value);
 $('f-vo').onchange=e=>set(()=>{if(e.target.value){sh.vo=e.target.value;sh.duration='auto'}else delete sh.vo});
 $('b-dup').onclick=()=>set(()=>{const c=structuredClone(sh);let n=2;while(W.shots.some(s=>s.id===sh.id+'-'+n))n++;c.id=sh.id+'-'+n;delete c.vo;W.shots.splice(W.shots.indexOf(sh)+1,0,c);sel=c.id});
 $('b-del').onclick=()=>set(()=>{W.shots.splice(W.shots.indexOf(sh),1);sel=null})}
async function probe(src){if(!(src in probes)){const [c,b]=await api('/api/probe?src='+encodeURIComponent(src));probes[src]=c===200?b:null}return probes[src]}
async function save(){if(!dirty)return;status('checking…');const [c,b]=await api('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({base_mtime:S.mtime,reel:W})});
 if(c===200){S=b;W=structuredClone(b.reel);dirty=false;undo=[];draw();return}
 if(c===409){banner('The reel file changed since you loaded it (Claude or another editor). Your edits are kept here but not saved.',true);status('not saved','err');return}
 status('not saved','err');$('msgs').innerHTML=(b.errors||[b.error]).map(e=>`<li class="e">${esc(e)}</li>`).join('')}
function banner(t,reload){const e=$('banner');e.style.display=t?'block':'none';e.innerHTML=t?esc(t)+(reload?' <button id="b-load">Load the file (drop my edits)</button>':''):'';if(reload)$('b-load').onclick=()=>{banner('');load()}}
function renderState(r){const p=$('preview');if(!r||r.status==='idle'){p.innerHTML='';$('render').disabled=false;return}
 $('render').disabled=r.status==='rendering';if(r.status==='rendering')p.innerHTML='<p class="hint">Rendering a draft…</p>';
 else if(r.status==='error')p.innerHTML=`<p class="hint" style="color:var(--acc)">Render failed: ${esc(r.error)}</p>`;
 else if(r.status==='done'&&!p.querySelector('video'))p.innerHTML=`<p class="hint">Draft rendered in ${r.seconds}s (saved version).</p><video controls src="/media/${encodeURIComponent(r.file)}?t=${Date.now()}"></video>`}
$('render').onclick=async()=>{if(dirty&&!confirm('The preview renders the SAVED reel. Render it anyway?'))return;$('preview').innerHTML='';const [c,b]=await api('/api/render',{method:'POST',body:'{}'});renderState(b);
 const t=setInterval(async()=>{const [c2,b2]=await api('/api/render');S.render=b2;renderState(b2);if(b2.status!=='rendering')clearInterval(t)},1500)};
$('save').onclick=save;$('revert').onclick=()=>{W=structuredClone(S.reel);dirty=false;undo=[];draw()};
$('undo').onclick=()=>{if(!undo.length)return;W=undo.pop();dirty=undo.length>0||JSON.stringify(W)!==JSON.stringify(S.reel);draw()};
$('zoom').oninput=e=>{pps=+e.target.value;draw()};
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();save()}else if((e.ctrlKey||e.metaKey)&&e.key==='z'){e.preventDefault();$('undo').click()}
 else if(e.key==='Delete'&&sel&&!/INPUT|SELECT/.test(document.activeElement.tagName)){const i=W.shots.findIndex(s=>s.id===sel);if(i>=0){push();W.shots.splice(i,1);sel=null;draw()}}});
setInterval(async()=>{if(!S)return;const [c,b]=await api('/api/changed');if(c!==200||b.mtime===S.mtime)return;
 if(!dirty){await load();banner('Reloaded: the reel was changed outside the timeline (see the edit log).');setTimeout(()=>banner(''),6000)}
 else banner('The reel file changed outside the timeline (Claude or another editor).',true)},2000);
load();
</script></body></html>
"""
