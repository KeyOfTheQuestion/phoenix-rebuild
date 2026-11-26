'use strict';
const zh = {
  recordFilters:'筛选实验记录',outcomeFilter:'按结果筛选',allOutcomes:'全部结果',
  contractFilter:'按契约筛选',allContracts:'全部契约',contract001:'契约 001',contract002:'契约 002',
  passedOutcome:'通过',failedOutcome:'验收未通过',generationFailedOutcome:'生成失败',verificationErrorOutcome:'验收环境错误',
  maintenanceNote:'2026-11-26 现在可显式为已有投递日志建索引，并保留原数据和回执。今天的 AI 重建通过 32/32，昨日到今日的数据交接通过 8/8。<a href="?run=20261126T023550Z-37f2ed">直达今天的证据 ↗</a>',
  navNotebook:'实验记录',navContract:'公开规格',eyebrow:'一场公开的软件实验 · 编号 001',
  headline:'同一份规格。<br><em>重新诞生。</em>',intro:'保留规格，从没有实现代码的新目录开始，让 AI 再造一次后端。然后验证：原来的数据，还能不能用。',
  inspect:'查看验收证据',run:'复制复现命令 ⌘',footnote:'每次尝试都会留下记录。失败的重建，同样公开。',
  subjectLabel:'本期重建对象',flow1:'一份公开规格',flow1sub:'接口行为 + 稳定的 SQLite 数据结构',flow2:'一次全新实现',flow2sub:'生成目录中不放入参考实现',flow3:'独立程序验收',flow3sub:'真实 HTTP、并发请求与旧数据兼容',cardBottom:'可使用的软件，可核验的结论。',
  aiRuns:'AI 重建尝试',aiCountNote:'不计入参考实现的验证',successful:'成功的 AI 重建',successNote:'对应契约的全部验收通过',compat:'旧数据兼容',compatNote:'上一实现通过接口写入的数据',principle:'实验原则',principleText:'通过只是结论。<br>交付证据，才可核验。',
  labNotes:'实验记录簿',recordTitle:'每一次，都有据可查。',recordIntro:'打开一条记录，查看验收项、生成源码与使用的规格。',all:'全部记录',aiOnly:'仅 AI 重建',colAttempt:'尝试编号 / UTC',colOrigin:'来源',colVerdict:'结果',colChecks:'验收项',colDuration:'验收耗时',loading:'正在读取公开记录…',
  ledgerNote:'仅在生成工具提供时公开 Token 用量。实际费用未知。不编造节省比例、连续成功天数或评分。',whatProves:'到底在验证什么',methodTitle:'代码重新生成。<br>承诺保持一致。',methodIntro:'第一个重建对象是一个能持久化的 Webhook 收件服务。它需要处理真实的接口边界。',feature1:'保存原始 UTF-8 请求正文',feature2:'并发重试只生成一条逻辑事件',feature3:'凭据不进入查询结果与访问日志',feature4:'进程重启或实现替换后，旧数据仍可读',readContract:'阅读完整规格 ↗',replicate:'亲手复现一次。',setup:'环境准备、执行边界与实验方法 ↗',
  honestTitle:'实验的边界',honestBody:'通过只代表这份代码通过了当前规格的验收，不是生产认证，也不是模型排行榜。生成上下文通过流程分开；本地执行不是恶意代码沙箱。所有公开请求数据均为合成示例。',footer:'由 KeyOfTheQuestion 公开构建。用证据表达能力。',challenge:'提出下一轮需求 ↗'
};
const original = new Map();
document.querySelectorAll('[data-i18n]').forEach(e=>original.set(e.dataset.i18n,e.innerHTML));
let language = localStorage.getItem('phoenix-language') === 'zh' ? 'zh' : 'en';
let filter = 'all';
let records = [];
const outcomeFilter = document.getElementById('outcome-filter');
const contractFilter = document.getElementById('contract-filter');
function restoreFilters(){
  const parameters=new URLSearchParams(location.search);
  filter=parameters.get('kind')==='ai-rebuild'?'ai-rebuild':'all';
  const status=parameters.get('status');
  if(outcomeFilter){
    outcomeFilter.value=['passed','failed','generation-failed','verification-error'].includes(status)?status:'all';
  }
  const contract=parameters.get('contract');
  if(contractFilter){
    contractFilter.value=['001','002'].includes(contract)?contract:'all';
  }
}
restoreFilters();
const escapeHtml = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const text = (en, cn) => language === 'zh' ? cn : en;

function translate(){
  document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
  document.querySelectorAll('[data-i18n]').forEach(e=>{e.innerHTML=language==='zh' ? (zh[e.dataset.i18n]||original.get(e.dataset.i18n)) : original.get(e.dataset.i18n);});
  document.getElementById('language').textContent=language==='zh'?'EN':'中文';
  render();
  renderLoadError();
}

function render(){
  document.querySelectorAll('[data-filter]').forEach(button=>{
    const selected=button.dataset.filter===filter;
    button.classList.toggle('active',selected);
    button.setAttribute('aria-pressed',String(selected));
  });
  const ai=records.filter(r=>r.kind==='ai-rebuild');
  document.getElementById('ai-count').textContent=String(ai.length);
  document.getElementById('success-count').textContent=String(ai.filter(r=>r.status==='passed').length);
  const latestPass=ai.find(r=>r.status==='passed'&&r.predecessor_sha256);
  document.getElementById('compat-status').textContent=latestPass?text('Verified','已验证'):text('Pending','待验证');
  document.getElementById('compat-status').style.color=latestPass?'var(--green)':'var(--muted)';
  const outcome=outcomeFilter?.value??'all';
  const contract=contractFilter?.value??'all';
  const shown=records.filter(r=>(filter==='all'||r.kind===filter)
    &&(outcome==='all'||r.status===outcome)
    &&(contract==='all'||(r.contract_id||'001')===contract));
  document.getElementById('runs').innerHTML=shown.length ? shown.map(r=>{
    const isAI=r.kind==='ai-rebuild';
    const date=new Date(r.started_at);
    const dateText=Number.isNaN(date.getTime())?r.started_at:date.toISOString().replace('T',' ').slice(0,19);
    return `<tr><td><span class="run-name">${escapeHtml(r.id.slice(-6).toUpperCase())}</span><small>${escapeHtml(dateText)}</small></td><td><span class="badge ${isAI?'ai':''}">${isAI?text('AI REBUILD','AI 重建'):text('REFERENCE','参考实现')}</span><small>${text('Contract','契约')} ${escapeHtml(r.contract_id||'001')}</small></td><td><span class="verdict ${r.status==='passed'?'pass':'fail'}">${r.status==='passed'?text('PASSED','通过'):r.status==='generation-failed'?text('GENERATION FAILED','生成失败'):r.status==='verification-error'?text('VERIFICATION ERROR','验收环境错误'):text('FAILED','未通过')}</span></td><td><span style="font-family:var(--mono)">${Number(r.passed)} / ${Number(r.total)}</span></td><td>${['generation-failed','verification-error'].includes(r.status)?'—':Number(r.duration_seconds).toFixed(2)+'s'}</td><td><button class="evidence-button" data-run="${escapeHtml(r.id)}">${text('Open evidence ↗','查看证据 ↗')}</button></td></tr>`;
  }).join('') : `<tr><td class="empty" colspan="6">${text('No records in this category yet.','当前分类还没有记录。')}</td></tr>`;
}

document.getElementById('language').addEventListener('click',()=>{language=language==='en'?'zh':'en';localStorage.setItem('phoenix-language',language);translate();});
function saveFilters(){
  const url=new URL(location.href);
  url.searchParams.delete('kind');
  url.searchParams.delete('status');
  url.searchParams.delete('contract');
  if(filter!=='all'){
    url.searchParams.set('kind',filter);
  }
  const status=outcomeFilter?.value??'all';
  if(status!=='all'){
    url.searchParams.set('status',status);
  }
  const contract=contractFilter?.value??'all';
  if(contract!=='all'){
    url.searchParams.set('contract',contract);
  }
  history.replaceState(history.state,'',url);
  render();
}

document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{
  filter=button.dataset.filter;
  saveFilters();
}));
outcomeFilter?.addEventListener('change',saveFilters);
contractFilter?.addEventListener('change',saveFilters);
window.addEventListener('popstate',()=>{
  restoreFilters();
  render();
});
document.getElementById('copy-command').addEventListener('click',async()=>{
  const command='git clone https://github.com/KeyOfTheQuestion/phoenix-rebuild\ncd phoenix-rebuild\npython3 -m phoenix verify';
  try{await navigator.clipboard.writeText(command);document.getElementById('copy-command').textContent=text('Copied ✓','已复制 ✓');}
  catch{document.getElementById('copy-command').textContent=text('See the commands below ↓','请查看下方复现命令 ↓');document.querySelector('.terminal-card').scrollIntoView({behavior:'smooth'});}
});
const dialog=document.getElementById('evidence-dialog');
const loadError=document.getElementById('load-error');
const loadErrorMessages={
  unknown:['This evidence link does not identify a published run.','此证据链接未指向已公开的实验记录。'],
  report:['Could not load this report. Please retry or open the GitHub evidence directory.','报告读取失败，请重试或到 GitHub 的证据目录查看。'],
  ledger:['The ledger could not be loaded. Serve this folder over HTTP, or inspect docs/runs.json on GitHub.','记录加载失败。请通过 HTTP 打开页面，或直接在 GitHub 查看 docs/runs.json。']
};
let loadErrorKind=null;
let evidenceRequest=0;

function renderLoadError(){
  loadError.hidden=loadErrorKind===null;
  loadError.textContent=loadErrorKind===null?'':text(...loadErrorMessages[loadErrorKind]);
}

function setLoadError(kind){
  loadErrorKind=kind;
  renderLoadError();
}
document.getElementById('close-dialog').addEventListener('click',()=>dialog.close());
dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});

async function openEvidence(id){
  const request=++evidenceRequest;
  if(!records.some(record=>record.id===id)){
    setLoadError('unknown');
    return;
  }
  try{
    const response=await fetch(`evidence/${encodeURIComponent(id)}/report.json`);
    if(!response.ok){
      throw new Error('Report unavailable');
    }
    const r=await response.json();
    if(request!==evidenceRequest){
      return;
    }
    if(r.id!==id){
      throw new Error('Report identifies a different run');
    }
    document.getElementById('dialog-run-id').textContent=id;
    const replay=document.getElementById('replay-instructions');
    if(replay){
      const canReplay=/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(id)
        && /^[0-9a-f]{64}$/.test(r.verifier_sha256||'')
        && ['passed','failed','verification-error'].includes(r.status);
      replay.hidden=!canReplay;
      document.getElementById('replay-heading').textContent=text('Replay this record','重放此记录');
      document.getElementById('replay-command').textContent=canReplay?`python3 -m phoenix replay ${id}`:'';
      document.getElementById('replay-notice').textContent=text(
        'Run from a current checkout of this repository with Python 3.9+. This executes trusted archived code locally and measures your current runtime.',
        '在本仓库最新检出目录中使用 Python 3.9+ 运行。此命令在本机执行可信的归档代码，并记录当前运行时的结果。');
    }
    const permalink=document.getElementById('evidence-permalink');
    permalink.href=`?run=${encodeURIComponent(id)}`;
    permalink.textContent=text('Permalink ↗','永久链接 ↗');
    document.getElementById('dialog-title').textContent=text('An inspectable result.','一份可核验的结果。');
    const generation=r.provenance?.generation_seconds;
    document.getElementById('dialog-summary').textContent=`${r.passed}/${r.total} ${text('checks passed','项验收通过')} · ${r.kind} · ${text('Contract','契约')} ${r.contract_id||'001'} · ${text('Verification','验收')} ${r.duration_seconds}s${generation!=null?' · '+text('Generation','生成')+' '+generation+'s':''}`;
    document.getElementById('artifact-links').innerHTML=[['server.py',text('Source code','源码')],['contract.md',text('Contract','规格')],['verifier.py',text('Verifier','验收器')],['report.json',text('Raw report','原始报告')],['manifest.json',text('SHA-256 manifest','文件指纹')],...(r.predecessor_sha256?[['predecessor.py',text('Previous implementation','上一实现')]]:[]),...(r.kind==='ai-rebuild'?[['prompt.md',text('Generation prompt','生成输入')]]:[]),...(['retention-composed-v1','retention-ai-v1'].includes(r.verifier_profile)?[['baseline.py',text('Frozen baseline','归档基础验收')],['retention.py',text('Frozen retention checks','归档保留策略验收')],['records.py',text('Frozen publisher support','归档发布依赖')],['audit.py',text('Frozen audit support','归档审计依赖')]]:[])].map(([file,label])=>`<a href="evidence/${encodeURIComponent(id)}/${file}" target="_blank" rel="noopener">${label} ↗</a>`).join('');
    document.getElementById('checks-list').innerHTML=r.checks.length?r.checks.map(c=>`<div class="check-row ${c.passed?'':'failed'}"><span class="check-id">${escapeHtml(c.id)}</span><div>${escapeHtml(c.title)}${c.error?`<div class="check-error">${escapeHtml(c.error)}</div>`:''}</div><span class="result">${c.passed?'PASS':'FAIL'}</span></div>`).join(''):`<p class="notice">${escapeHtml(r.error||'No candidate was executed.')}</p>`;
    const tokens=r.usage ? JSON.stringify(r.usage) : text('Not reported','未提供');
    const runtime=r.execution_runtime ? `${r.execution_runtime.python_implementation} ${r.execution_runtime.python_version}; SQLite ${r.execution_runtime.sqlite_version}` : text('Not recorded','未记录');
    const execution=r.execution==='docker-container'?text('Docker container; cooperative evaluator.','Docker 容器；协作式验收。'):r.execution==='docker-container-attempted'?text('Container verification error; no acceptance verdict.','容器验收环境错误；未产生验收结论。'):r.execution==='local-subprocess-attempted'?text('Local verifier runtime error; no acceptance verdict.','本地验收器运行错误；未产生验收结论。'):r.execution==='not-executed'?text('Candidate not executed.','未执行候选代码。'):text('Local subprocess.','本地子进程。');
    document.getElementById('dialog-provenance').textContent=`SHA-256: ${r.candidate_sha256} · ${text('Runtime: ','运行时：')}${runtime} · ${text('Usage','用量')}: ${tokens} · ${text('Cost: unknown.','费用未知。')} ${execution} ${r.kind==='ai-rebuild'?text('Procedural generation context separation.','生成上下文通过流程分开。'):text('Reference verification; no model call.','参考验证；未调用模型。')}`;
    setLoadError(null);
    dialog.showModal();
  }catch{
    if(request===evidenceRequest){
      setLoadError('report');
    }
  }
}

document.getElementById('runs').addEventListener('click',async e=>{
  const button=e.target.closest('[data-run]');
  if(!button){
    return;
  }
  button.disabled=true;
  try{
    await openEvidence(button.dataset.run);
  }finally{
    button.disabled=false;
  }
});

async function load(){
  try{
    const response=await fetch('runs.json',{cache:'no-cache'});
    if(!response.ok){
      throw new Error('Ledger unavailable');
    }
    records=await response.json();
    translate();
  }catch{
    document.getElementById('runs').innerHTML='';
    loadError.className='notice';
    setLoadError('ledger');
    return;
  }
  const run=new URLSearchParams(location.search).get('run');
  if(run!==null){
    await openEvidence(run);
  }
}
translate();load();
