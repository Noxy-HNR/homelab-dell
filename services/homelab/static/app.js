'use strict';
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const gib = (v) => `${(Number(v || 0) / 2 ** 30).toFixed(1)} GiB`;
const when = (v) => v ? new Date(typeof v === 'number' ? v * 1000 : v).toLocaleString() : '—';
let state = null, page = 'overview', task = null;
function notice(text, error=false) { $('message').textContent = text; $('message').className = `message${error?' error':''}`; }
async function api(path, method='GET', body) {
  const response = await fetch(`/api${path}`, {method, headers:{'Content-Type':'application/json','X-Homelab-Request':'1'}, body:body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) { $('login').classList.remove('hidden'); $('content').classList.add('hidden'); }
    throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
  }
  return data;
}
function btn(label, action, id='', secondary=true) {return `<button class="${secondary?'secondary':''}" data-action="${esc(action)}" data-id="${esc(id)}">${esc(label)}</button>`;}
function badge(value) {return `<span class="tag ${['failed','interrupted','cancelled'].includes(value)?'bad':''}">${esc(value)}</span>`;}
function table(headers, rows) {return `<div class="table-wrap"><table><thead><tr>${headers.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;}
function empty(text) {return `<p class="empty">${esc(text)}</p>`;}
function link(url, label) {try {let parsed = new URL(url); if (!['http:','https:'].includes(parsed.protocol)) return ''; return `<a class="button secondary" href="${esc(url)}" target="_blank" rel="noreferrer">${esc(label)} ↗</a>`;} catch {return '';}}
const titles = {overview:['Your machines, at a glance.','Live measurements from this server.'], models:['A library without the laptop clutter.','Download to the HP. Choose what to run from your Windows control app.'], projects:['Make room for your next project.','Create a workspace, run a job, and keep its output here.'], search:['Find the file you need.','Search indexed text in your registered projects.'], backups:['Keep the files you cannot replace.','Run backups, inspect snapshots, and restore into a separate folder.']};
async function openPage(next) {
  page = next; task = null;
  $('message').textContent=''; $('message').classList.add('hidden');
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active', b.dataset.page===page));
  $('title').textContent=titles[page][0]; $('subtitle').textContent=titles[page][1];
  $('content').innerHTML=empty('Loading…');
  await ({overview:overview,models:models,projects:projects,search:search,backups:backups}[page])();
}
async function connected() {
  state = await api('/status');
  $('login').classList.add('hidden'); $('content').classList.remove('hidden'); $('logout').classList.remove('hidden');
  $('machine').textContent=`${state.role.toUpperCase()} · ${state.hostname}`;
  $('context').textContent=state.role==='hp'?'HP · MODEL LIBRARY & STORAGE':'DELL · DEVELOPMENT WORKBENCH';
  const entries=state.role==='hp'?[['overview','Overview'],['models','Model library'],['backups','Backups']]:[['overview','Overview'],['projects','Projects & jobs'],['search','Search files'],['backups','Backups']];
  $('nav').innerHTML=entries.map(([id,label])=>`<button data-page="${id}">${label}</button>`).join('');
  await openPage('overview');
}
async function overview() {
  const data=await api('/metrics'), c=data.current;
  // SVG-free progress elements also work with the strict stylesheet policy.
  let html=`<div class="grid"><div class="card"><p class="eyebrow">PROCESSOR</p><div class="stat">${Number(c.cpu_percent).toFixed(0)}<span class="muted"> %</span></div><progress max="100" value="${Number(c.cpu_percent)}"></progress><p class="muted">Current CPU use</p></div><div class="card"><p class="eyebrow">MEMORY IN USE</p><div class="stat">${gib(c.memory.used)}</div><progress max="100" value="${Number(c.memory.percent)}"></progress><p class="muted">${gib(c.memory.total)} installed</p></div><div class="card"><p class="eyebrow">SERVER</p><div class="stat">${esc(state.role.toUpperCase())}</div><p class="muted">${esc(state.hostname)} · measured ${esc(when(c.timestamp))}</p></div></div>`;
  html+=`<div class="grid two"><section class="panel"><div class="row"><h2>Storage</h2>${btn('Refresh','refresh')}</div>${table(['Location','Used / total'],c.disks.map(d=>`<tr><td><code>${esc(d.path)}</code></td><td>${gib(d.used)} / ${gib(d.total)}<span class="muted">${gib(d.free)} free</span></td></tr>`))}</section><section class="panel"><h2>Network links</h2>${table(['Interface','Link','Received'],c.network.filter(n=>!['lo','Loopback Pseudo-Interface 1'].includes(n.name)).map(n=>`<tr><td>${esc(n.name)}</td><td>${n.is_up ? (n.speed_mbps ? `${n.speed_mbps/1000} Gb/s`:'Up') : 'Down'}</td><td>${gib(n.bytes_recv)}<span class="muted">${Number(n.errin)+Number(n.errout)} errors</span></td></tr>`))}<p class="muted">Counters are cumulative since boot/interface reset. Link speed is not measured file throughput.</p></section></div>`;
  const hp=state.hp_url, dell=state.dell_url;
  html+=`<section class="panel"><h2>Open a workspace</h2><p class="muted">Each server has its own login. The Dell workbench is available while the Dell is on.</p><div class="actions">${link(hp,'HP model library')}${link(dell,'Dell workbench')}${link(`https://${location.hostname}:9090`,'System controls (Cockpit)')}${state.role==='dell'?link(`https://${location.hostname}:9443`,'Containers (Portainer)'):''}</div></section>`;
  if(state.role==='hp') {const steam=await api('/steam');html+=`<section class="panel"><h2>Shared Steam drive</h2><p class="muted">Attach and release from the Windows control app on the PC using the drive.</p>${steam.available?`<pre>${esc(JSON.stringify(steam.status,null,2))}</pre>`:`<p>${esc(steam.message)}</p>`}</section>`;}
  else html+=`<section class="panel"><h2>Tools for your AI</h2><p>Your MCP server can list projects, search files, read text, and run named jobs. Open the SSH tunnel from your PC, then use this address in your MCP client:</p><code>http://127.0.0.1:8765/mcp</code><p class="muted">The setup guide includes the tunnel command and a connection check.</p></section>`;
  $('content').innerHTML=html;
}
async function models() {
  const [catalog,downloads]=await Promise.all([api('/models'),api('/downloads')]);
  $('content').innerHTML=`<section class="panel"><div class="row"><div><h2>Your models</h2><p class="muted">${catalog.items.length} complete files · ${gib(catalog.total_bytes)}</p></div>${btn('Scan library','scan-models')}</div>${catalog.items.length?table(['Model','Size','Details',''],catalog.items.map(m=>`<tr><td>${esc(m.name)}<span class="muted">${esc(m.id)}</span></td><td>${gib(m.size_bytes)}</td><td>${esc(m.metadata?.architecture || 'GGUF')}<span class="muted">${esc(m.metadata?.quantization || 'Quantization not recorded')}</span></td><td>${btn('Copy file name','copy',m.id)}</td></tr>`)):empty('Your library is empty. Add a GGUF below, or copy a complete model into the Models share, then scan.')}<p class="muted">Use the Windows control app to link or load a model on your laptop. File size alone does not determine memory fit.</p>${catalog.warnings?.length?`<pre>${esc(JSON.stringify(catalog.warnings,null,2))}</pre>`:''}</section>
  <section class="panel"><h2>Download a model to the HP</h2><p class="muted">Use the repository and exact GGUF filename from Hugging Face. The file becomes available only after it is complete and verified.</p><form id="download-form"><div class="grid two"><label>Repository<input name="repo_id" placeholder="owner/model-GGUF" required></label><label>Exact file path in the repository<input name="filename" placeholder="model-Q4_K_M.gguf" required></label><label>Revision<input name="revision" value="main" required></label><label>Save as (optional, one GGUF filename)<input name="local_name" placeholder="my-model-Q4_K_M.gguf"></label></div><button>Queue download</button></form></section>
  <section class="panel"><div class="row"><h2>Download queue</h2>${btn('Refresh','refresh')}</div>${downloads.items.length?table(['File','Progress','State',''],downloads.items.map(d=>`<tr><td>${esc(d.local_name||d.filename)}${d.error?`<span class="muted">${esc(d.error)}</span>`:''}</td><td>${gib(d.downloaded_bytes)} / ${gib(d.total_bytes)}<progress max="100" value="${Number(d.progress||0)}"></progress></td><td>${badge(d.state)}</td><td>${['queued','resolving','downloading','validating'].includes(d.state)?btn('Cancel','cancel-download',d.id):''}</td></tr>`)):empty('No downloads yet.')}</section>`;
}
async function projects() {
  const [p,j]=await Promise.all([api('/projects'),api('/jobs')]);
  $('content').innerHTML=`<section class="panel"><div class="row"><h2>Your workspaces</h2>${btn('Refresh','refresh')}</div>${p.items.length?table(['Project','Source','Run a job'],p.items.map(x=>`<tr><td>${esc(x.name)}<span class="muted">${esc(x.id)}</span></td><td>${esc(x.source)}</td><td><div class="actions">${x.presets.map(preset=>btn(preset,'run-job',JSON.stringify({project_id:x.id,preset}))).join('')}</div></td></tr>`)):empty('Create a Python project below. It includes a small example and a real test you can run immediately.')}</section>
  <section class="panel"><h2>New workspace</h2><form id="project-form"><div class="grid two"><label>Project name<input name="name" pattern="[a-z0-9][a-z0-9-]*" placeholder="my-project" required></label><label>Public GitHub repository (optional)<input name="git_url" placeholder="https://github.com/owner/repo.git"></label></div><p class="muted">Leave the repository empty to create a Python template. Imported code runs only when you start a job. Run projects you trust.</p><button>Create project</button></form></section>
  <section class="panel"><h2>Job history</h2>${j.items.length?table(['Project / job','Started','State',''],j.items.map(x=>`<tr><td>${esc(x.project_id)} / ${esc(x.preset)}</td><td>${esc(when(x.created_at))}</td><td>${badge(x.state)}</td><td>${btn('Open log','job-log',x.id)} ${['queued','running'].includes(x.state)?btn('Cancel','cancel-job',x.id):''}</td></tr>`)):empty('No jobs yet. Run the template test to check the full workflow.')}</section><section id="task-panel" class="panel hidden"></section>`;
}
async function search() {
  const p=await api('/projects');
  $('content').innerHTML=`<section class="panel"><h2>Search project text</h2><p class="muted">Index a project after editing or importing files. Search results include the original file path.</p><form id="search-form" class="inline"><label>Words to find<input name="q" placeholder="function, setting, or phrase" required></label><label>Project<select name="project_id"><option value="">All projects</option>${p.items.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('')}</select></label><button>Search</button></form><div class="actions">${btn('Rebuild search index','reindex')}</div></section><section id="search-results" class="panel">${empty('Type a search above.')}</section><section id="file-panel" class="panel hidden"></section>`;
}
async function backups() {
  const b=await api('/backups');
  $('content').innerHTML=`<section class="panel"><div class="row"><h2>Backup plans</h2>${btn('Refresh','refresh')}</div>${!b.available?'<p class="info">Restic is unavailable. Re-run the application installer on this server.</p>':''}${b.plans.length?table(['Plan','Sources','Destination','Last backup','Actions'],b.plans.map(p=>`<tr><td>${esc(p.name)}${p.error?`<span class="muted">${esc(p.error)}</span>`:''}</td><td>${p.sources.map(esc).join('<br>')}</td><td>${esc(p.destination_type)}</td><td>${p.last_backup?`${badge(p.last_backup.state)}<span class="muted">${esc(when(p.last_backup.finished_at))}</span>`:badge('never run')}</td><td>${p.configured?`${btn('Back up now','backup-run',p.id)} ${btn('Check','backup-check',p.id)} ${btn('Snapshots','snapshots',p.id)}`:badge('not configured')}</td></tr>`)):empty('No backup plan configured. Follow the guide’s Backup step to choose a separate destination and save your recovery password. Nothing is protected by this page until a backup succeeds.')}</section>
  <section class="panel"><h2>Recent backup tasks</h2>${b.tasks.length?table(['Plan / action','When','State',''],b.tasks.map(t=>`<tr><td>${esc(t.plan_id)} / ${esc(t.action)}</td><td>${esc(when(t.created_at))}</td><td>${badge(t.state)}</td><td>${btn('Open log','backup-log',t.id)}</td></tr>`)):empty('No backup tasks yet.')}</section><section id="snapshots-panel" class="panel hidden"></section><section id="task-panel" class="panel hidden"></section>`;
}
async function showTask(kind,id) {
  task={kind,id};
  const t=await api(kind==='job'?`/jobs/${encodeURIComponent(id)}`:`/backups/tasks/${encodeURIComponent(id)}`);
  $('task-panel').classList.remove('hidden');
  $('task-panel').innerHTML=`<div class="row"><h2>${kind==='job'?'Job output':'Backup output'}</h2>${badge(t.state)}</div>${t.restore_path?`<p>Restored files: <code>${esc(t.restore_path)}</code></p>`:''}<pre>${esc(t.log||t.error||'Waiting for output…')}</pre>`;
}
async function action(name,id) {
  if(name==='refresh') return openPage(page);
  if(name==='copy') {try {await navigator.clipboard.writeText(id);notice('Copied the model file name.');} catch {notice(`File name: ${id}`);} return;}
  if(name==='scan-models') {await api('/models/scan','POST',{});await models();notice('Library scanned.');}
  if(name==='cancel-download') {await api(`/downloads/${encodeURIComponent(id)}/cancel`,'POST',{});await models();}
  if(name==='run-job') {const j=await api('/jobs','POST',JSON.parse(id));await projects();await showTask('job',j.id);notice('Job queued.');}
  if(name==='cancel-job') {await api(`/jobs/${encodeURIComponent(id)}/cancel`,'POST',{});await projects();}
  if(name==='job-log') await showTask('job',id);
  if(name==='reindex') {const r=await api('/search/reindex','POST',{});notice(`Indexed ${r.indexed_files} files; skipped ${r.skipped_files}.`);}
  if(name==='file') {const p=JSON.parse(id), f=await api(`/files?${new URLSearchParams(p)}`);$('file-panel').classList.remove('hidden');$('file-panel').innerHTML=`<h2>${esc(f.path)}</h2><pre>${esc(f.content)}</pre>`;}
  if(name==='backup-run'||name==='backup-check') {const t=await api(name==='backup-run'?'/backups/run':'/backups/check','POST',{plan_id:id});await backups();await showTask('backup',t.id);}
  if(name==='backup-log') await showTask('backup',id);
  if(name==='snapshots') {const s=await api(`/backups/${encodeURIComponent(id)}/snapshots`);$('snapshots-panel').classList.remove('hidden');$('snapshots-panel').innerHTML=`<h2>Restore from a snapshot</h2><p class="muted">Restores are written to a new server folder. They do not overwrite your source files.</p>${s.items.length?table(['Snapshot','Date',''],s.items.map(x=>`<tr><td><code>${esc(x.short_id||x.id)}</code></td><td>${esc(when(x.time))}</td><td>${btn('Restore a copy','restore',JSON.stringify({plan_id:id,snapshot_id:x.id}))}</td></tr>`)):empty('No snapshots yet.')}`;}
  if(name==='restore') {if(!confirm('Restore this snapshot into a new server folder?')) return;const t=await api('/backups/restore','POST',JSON.parse(id));await showTask('backup',t.id);}
}
document.addEventListener('click',async(e)=>{const b=e.target.closest('button');if(!b) return;try {if(b.dataset.page) await openPage(b.dataset.page);if(b.dataset.action){b.disabled=true;await action(b.dataset.action,b.dataset.id);}}catch(err){notice(err.message,true);}finally{b.disabled=false;}});
document.addEventListener('submit',async(e)=>{e.preventDefault();const f=e.target, button=f.querySelector('button');button.disabled=true;try {
  const data=Object.fromEntries(new FormData(f));
  if(f.id==='login-form'){await api('/login','POST',{token:$('token').value});$('token').value='';$('message').classList.add('hidden');await connected();}
  if(f.id==='download-form'){if(!data.local_name) delete data.local_name;await api('/downloads','POST',data);await models();notice('Download queued on the HP.');}
  if(f.id==='project-form'){if(!data.git_url){delete data.git_url;data.template='python';}await api('/projects','POST',data);await projects();notice('Project created. Run its test job to check it.');}
  if(f.id==='search-form'){if(!data.project_id) delete data.project_id;const r=await api(`/search?${new URLSearchParams(data)}`);$('search-results').innerHTML=`<h2>${r.items.length} results</h2>${r.items.map(x=>`<article class="panel"><div class="row"><h3>${esc(x.project_id)} / ${esc(x.path)}</h3>${btn('Read file','file',JSON.stringify({project_id:x.project_id,path:x.path}))}</div><p>${esc(x.snippet)}</p></article>`).join('')||empty('No matches. Try fewer words or rebuild the index.')}`;}
}catch(err){notice(err.message,true);}finally{button.disabled=false;}});
$('logout').addEventListener('click',async()=>{try{await api('/logout','POST',{});}finally{location.reload();}});
setInterval(async()=>{if(task && $('task-panel')) {try {await showTask(task.kind,task.id);} catch {}}},3000);
connected().catch(()=>{});
