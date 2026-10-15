'use strict';

const $ = id => document.getElementById(id);
const icon = (name, cls = '') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone = value => JSON.parse(JSON.stringify(value));
const normalizePath = value => String(value ?? '').trim().replace(/^"|"$/g, '').replace(/\//g, '\\').replace(/\\+$/, '');
const basename = path => normalizePath(path).split('\\').pop();
const absolutePath = path => /^(?:[a-z]:\\|\\\\[^\\]+\\[^\\]+)/i.test(String(path).trim());
const sourceRoot = path => /\.exe$/i.test(path) ? path.slice(0, path.lastIndexOf('\\')) : path;
const sameOrInside = (child, parent) => child.toLowerCase() === parent.toLowerCase() || child.toLowerCase().startsWith(parent.toLowerCase() + '\\');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const STORE = 'desktop-packager-prototype-v1';
const COMPILER = 'D:\\PCAPP\\Inno Setup 7\\ISCC.exe';
const ROLES = {C:{name:'功能软件 C',icon:'app',color:'blue',description:'核心功能程序，始终安装',folder:'FeatureApp'},A:{name:'认证软件 A',icon:'key',color:'amber',description:'独立的认证软件，按需安装',folder:'AuthApp'},B:{name:'引擎软件 B',icon:'cpu',color:'teal',description:'独立的引擎软件，按需安装',folder:'EngineApp'}};
const ROLE_ORDER = ['C','A','B'];
let saved = {};
try { saved = JSON.parse(localStorage.getItem(STORE) || '{}') || {}; } catch (_) {}
let history = Array.isArray(saved.history) ? saved.history.slice(0,12) : [];
let runtimes = [];
let compiler = typeof saved.compiler === 'string' ? saved.compiler : COMPILER;
let pickerKind = '';
let building = false;
let cancelRequested = false;
let buildLogs = [];
let installer = null;
let toastTimer;
let runtimeSerial = 0;

function persist() {
  try { localStorage.setItem(STORE, JSON.stringify({config:currentConfig(),compiler,history})); } catch (_) {}
}
function toast(message) {
  $('toast').querySelector('span').textContent = message;
  $('toast').classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.remove('visible'), 3200);
}
function runtimeFromPath(path, params) {
  path = normalizePath(path);
  const name = basename(path);
  const type = /\.msi$/i.test(name) ? 'MSI' : 'EXE';
  const isVc = /(?:vc[_-]?redist|vcredist)/i.test(name);
  const isDotnet = /(?:windowsdesktop-runtime|dotnet-runtime)/i.test(name);
  return {id:'runtime-' + (++runtimeSerial),path,type,params: typeof params === 'string' ? params : type === 'MSI' ? '/qn /norestart' : isVc || isDotnet ? '/install /quiet /norestart' : ''};
}
function currentConfig() {
  return {paths:Object.fromEntries(ROLE_ORDER.map(role => [role,normalizePath($('path'+role).value)])),output:normalizePath($('outputPath').value),name:$('bundleName').value.trim() || '桌面软件套装',version:$('bundleVersion').value.trim() || '1.0.0',runtimes:runtimes.map(({path,params})=>({path,params}))};
}
function applyConfig(config) {
  for (const role of ROLE_ORDER) $('path'+role).value = typeof config.paths?.[role] === 'string' ? config.paths[role] : '';
  $('outputPath').value = typeof config.output === 'string' ? config.output : '';
  $('bundleName').value = typeof config.name === 'string' ? config.name.slice(0,60) : '桌面软件套装';
  $('bundleVersion').value = typeof config.version === 'string' ? config.version.slice(0,24) : '1.0.0';
  runtimes = (Array.isArray(config.runtimes) ? config.runtimes : []).slice(0,100).filter(item=>item && typeof item.path === 'string').map(item=>runtimeFromPath(item.path,item.params));
  clearErrors(); renderRuntimes(); renderSummary(); persist();
}
function artifactName(config, withTime = false) {
  const safe = value => value.replace(/[<>:"/\\|?*\x00-\x1f]/g,'-').replace(/[. ]+$/,'') || 'Package';
  const stamp = new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date()).replace(/[- :]/g,'');
  return `${safe(config.name)}-${safe(config.version)}${withTime?'-'+stamp:''}-Bundle.exe`;
}
function renderSummary() {
  const config = currentConfig();
  const included = ROLE_ORDER.filter(role=>config.paths[role]);
  $('includedCount').textContent = `${included.length} / 3`;
  $('softwareSummary').innerHTML = ROLE_ORDER.map(role=>`<div class="software-summary-item ${config.paths[role]?'':'missing'}"><span class="role-letter">${role}</span><div><strong>${ROLES[role].name}</strong><small>${config.paths[role] ? role==='C'?'固定安装':'用户可勾选安装' : role==='C'?'等待配置发布目录':'本包暂不包含'}</small></div>${config.paths[role]?icon('check'):''}</div>`).join('');
  for (const role of ROLE_ORDER) {
    const hasPath = Boolean(config.paths[role]);
    $('status'+role).textContent = hasPath ? '路径已填写' : role==='C'?'未配置':'未添加';
    $('status'+role).classList.toggle('configured',hasPath);
    const clearButton = document.querySelector(`[data-clear="${role}"]`);
    if (clearButton) clearButton.hidden = !hasPath;
  }
  $('runtimeSummaryText').textContent = runtimes.length ? `${runtimes.length} 个安装包，按列表顺序执行` : '未添加环境安装包';
  $('artifactName').textContent = artifactName(config);
  const ready = Boolean(config.paths.C && config.output);
  if (!building) {
    $('readinessTitle').textContent = ready ? '配置已就绪' : '填写路径，即可开始';
    $('readinessText').textContent = ready ? `${included.length} 款软件 · ${runtimes.length} 个运行环境` : '请配置功能软件 C 与输出目录';
  }
  $('readinessIcon').classList.toggle('ready',ready);
}
function renderRuntimes() {
  $('runtimeCount').textContent = runtimes.length;
  $('runtimeEmpty').hidden = Boolean(runtimes.length);
  $('runtimeOrderNote').hidden = !runtimes.length;
  $('runtimeList').innerHTML = runtimes.map((item,index)=>`<article class="runtime-item" data-runtime="${item.id}"><div class="runtime-main"><span class="runtime-file-icon">${icon('file')}</span><div class="runtime-info"><div class="runtime-name"><strong>${escapeHtml(basename(item.path))}</strong><span class="tag tag-gray">${item.type}</span></div><div class="runtime-path" title="${escapeHtml(item.path)}">${escapeHtml(item.path)}</div></div><div class="runtime-controls"><button type="button" class="icon-btn" data-runtime-action="up" aria-label="上移 ${escapeHtml(basename(item.path))}" ${index===0?'disabled':''}>${icon('up')}</button><button type="button" class="icon-btn" data-runtime-action="down" aria-label="下移 ${escapeHtml(basename(item.path))}" ${index===runtimes.length-1?'disabled':''}>${icon('down')}</button><button type="button" class="icon-btn remove" data-runtime-action="remove" aria-label="移除 ${escapeHtml(basename(item.path))}">${icon('x')}</button></div></div><details class="runtime-params"><summary><span>${item.type==='MSI'?'MSI 安装参数':item.params?'已配置安装参数':'使用安装包自身界面'}</span>${icon('down')}</summary><input data-runtime-params="${item.id}" value="${escapeHtml(item.params)}" aria-label="${escapeHtml(basename(item.path))} 的安装参数" placeholder="可填写该安装包支持的静默参数"><p>${item.type==='MSI'?'MSI 将通过系统安装服务执行。':'仅使用厂商支持的参数；留空时显示安装包自身界面。'} 环境安装包始终先于业务软件执行。</p></details></article>`).join('');
  renderSummary();
}
function clearErrors() {
  $('errorC').textContent = '';
  $('errorOutput').textContent = '';
  document.querySelectorAll('.path-control.invalid').forEach(el=>el.classList.remove('invalid'));
}
function validate(config) {
  clearErrors();
  const errors = [];
  function error(field,message) {
    if (field==='C' || field==='output') {
      $(field==='C'?'errorC':'errorOutput').textContent = message;
      $(field==='C'?'pathC':'outputPath').closest('.path-control').classList.add('invalid');
    }
    errors.push(message);
  }
  if (!config.paths.C) error('C','请选择功能软件 C 的发布目录。');
  if (!config.output) error('output','请选择安装包输出目录。');
  for (const role of ROLE_ORDER) if (config.paths[role] && !absolutePath(config.paths[role])) error(role,`${ROLES[role].name} 需要完整的 Windows 路径，例如 D:\\Apps\\${ROLES[role].folder}。`);
  if (config.output && !absolutePath(config.output)) error('output','输出目录需要完整的 Windows 路径。');
  if (config.output && /\.(exe|msi)$/i.test(config.output)) error('output','请填写输出目录，不是安装包文件名。');
  const roots = ROLE_ORDER.filter(role=>config.paths[role]).map(role=>({role,path:sourceRoot(config.paths[role])}));
  for (const root of roots) if (config.output && sameOrInside(config.output,root.path)) error('output','输出目录不能位于软件发布目录内部。');
  for (let i=0;i<roots.length;i++) for (let j=i+1;j<roots.length;j++) if (sameOrInside(roots[i].path,roots[j].path)||sameOrInside(roots[j].path,roots[i].path)) errors.push(`${ROLES[roots[i].role].name} 与 ${ROLES[roots[j].role].name} 的发布目录重叠。`);
  for (const item of config.runtimes) if (!absolutePath(item.path)||! /\.(exe|msi)$/i.test(item.path)) errors.push('运行环境需要完整的 EXE 或 MSI 安装包路径。');
  if (errors.length) { toast(errors[0]); return false; }
  return true;
}

function openPicker(kind) {
  if (building) return;
  pickerKind = kind;
  const multiple = kind==='runtime';
  $('pickerError').textContent = '';
  $('pickerPath').hidden = multiple;
  $('pickerMultiple').hidden = !multiple;
  $('pickerMultiple').value = '';
  $('pickerTitle').textContent = multiple?'添加运行环境安装包':kind==='output'?'选择输出目录':kind==='compiler'?'选择 Inno Setup 编译器':`选择${ROLES[kind].name}发布目录`;
  $('pickerDescription').textContent = multiple?'可同时选择多个示例安装包，或粘贴自己的安装包路径。':'选择一个示例目录，或直接填写自己的完整 Windows 路径。';
  $('pickerPathLabel').textContent = multiple?'其他安装包路径（每行一个）':kind==='compiler'?'编译器路径':'目录或主程序路径';
  $('confirmPath').textContent = multiple?'添加选中安装包':kind==='compiler'?'使用此编译器':'选择此目录';
  const choices = multiple ? [
    ['VC_redist.x64.exe','Microsoft Visual C++ · x64','D:\\Prerequisites\\VC_redist.x64.exe'],
    ['VC_redist.x86.exe','Microsoft Visual C++ · x86','D:\\Prerequisites\\VC_redist.x86.exe'],
    ['windowsdesktop-runtime-x64.exe','.NET Desktop Runtime · x64','D:\\Prerequisites\\windowsdesktop-runtime-x64.exe'],
    ['CustomRuntime.msi','其他 MSI 环境安装包','D:\\Prerequisites\\CustomRuntime.msi']
  ] : kind==='output' ? [['InstallPackages','安装包输出目录','D:\\InstallPackages'],['Output','本地输出目录','D:\\Output']] : kind==='compiler' ? [['Inno Setup 7','已有编译器目录',COMPILER]] : [[ROLES[kind].folder,'完整发布目录',`D:\\DesktopApps\\${ROLES[kind].folder}`],[ROLES[kind].folder+'.exe','通过主程序确定发布目录',`D:\\DesktopApps\\${ROLES[kind].folder}\\${ROLES[kind].folder}.exe`]];
  $('pickerLocation').textContent = multiple?'此电脑 / D: / Prerequisites':kind==='output'?'此电脑 / D:':kind==='compiler'?'此电脑 / D: / PCAPP':'此电脑 / D: / DesktopApps';
  $('pickerPath').value = kind==='compiler'?$('compilerPath').value:kind==='output'?$('outputPath').value:multiple?'':$('path'+kind).value;
  $('pickerChoices').innerHTML = choices.map(([name,description,path])=>multiple?`<label class="picker-choice"><input type="checkbox" value="${escapeHtml(path)}">${icon('file')}<div><strong>${escapeHtml(name)}</strong><small>${description}</small></div><span class="tag tag-gray">${/\.msi$/i.test(path)?'MSI':'EXE'}</span></label>`:`<button type="button" class="picker-choice" data-picker-value="${escapeHtml(path)}">${icon(kind==='compiler'?'terminal':'folder')}<div><strong>${escapeHtml(name)}</strong><small>${description}</small></div>${icon('arrow')}</button>`).join('');
  $('pathDialog').showModal();
}
function confirmPicker() {
  if (pickerKind==='runtime') {
    const selected = [...$('pickerChoices').querySelectorAll('input:checked')].map(input=>input.value);
    const manual = $('pickerMultiple').value.split(/\r?\n/).map(normalizePath).filter(Boolean);
    const paths = [...selected,...manual];
    if (!paths.length) { $('pickerError').textContent='至少选择或填写一个安装包路径。'; return; }
    if (paths.some(path=>!absolutePath(path)||! /\.(exe|msi)$/i.test(path))) { $('pickerError').textContent='请填写完整的 EXE 或 MSI 路径，每行一个。'; return; }
    const keys = new Set(runtimes.map(item=>item.path.toLowerCase()));
    let added = 0;
    for (const path of paths) if (!keys.has(path.toLowerCase())) { runtimes.push(runtimeFromPath(path)); keys.add(path.toLowerCase()); added++; }
    renderRuntimes(); persist(); toast(added?`已添加 ${added} 个运行环境安装包`:'所选安装包已经在列表中');
  } else {
    const path = normalizePath($('pickerPath').value);
    if (!absolutePath(path)) { $('pickerError').textContent='请填写完整的 Windows 路径。'; return; }
    $(pickerKind==='output'?'outputPath':pickerKind==='compiler'?'compilerPath':'path'+pickerKind).value = path;
    clearErrors(); renderSummary(); persist();
  }
  $('pathDialog').close();
}
function navigate(page) {
  for (const name of ['workspace','history','settings']) $('page-'+name).hidden = name!==page;
  document.querySelectorAll('[data-page]').forEach(el=>el.classList.toggle('active',el.dataset.page===page));
  $('breadcrumbTitle').textContent = {workspace:'打包工作台',history:'构建记录',settings:'工具设置'}[page];
  $('mainScroll').scrollTop = 0;
  if (page==='history') renderHistory();
}
function lockBuild(locked) {
  building = locked;
  $('packageForm').querySelectorAll('input,button,textarea').forEach(el=>{ if (!['cancelBuild','collapseConsole'].includes(el.id)) el.disabled = locked; });
  $('loadExample').disabled = locked;
  $('previewButton').disabled = locked;
  $('cancelBuild').hidden = !locked;
  $('cancelBuild').disabled = false;
  $('cancelBuild').textContent = '取消打包';
  $('buildButton').innerHTML = locked?`${icon('clock')}正在演示打包…`:`${icon('box')}开始打包${icon('arrow')}`;
  if (!locked) renderRuntimes();
}
function addBuildLog(message,kind='') {
  const time = new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date());
  buildLogs.push({time,message,kind});
  const line = document.createElement('div'); line.className='log-line '+kind;
  const timestamp = document.createElement('time'); timestamp.textContent=time;
  line.append(timestamp,document.createTextNode(message)); $('buildLogs').append(line); $('buildLogs').scrollTop=$('buildLogs').scrollHeight;
}
async function startBuild(event) {
  event.preventDefault(); if (building) return;
  const config=currentConfig(); if (!validate(config)) return;
  cancelRequested=false; buildLogs=[]; $('buildLogs').replaceChildren(); $('buildConsole').hidden=false; $('buildLogs').hidden=false;
  $('buildStatusTag').textContent='演示中'; $('buildStatusTag').className='tag tag-blue';
  $('buildProgressFill').style.width='0%'; $('buildPercent').textContent='0%';
  lockBuild(true); persist();
  const stages=[['检查路径与配置', '演示：路径格式检查通过；未读取真实磁盘文件。'],['收集发布文件','演示：保留软件目录结构与依赖文件。']];
  for (const runtime of config.runtimes) stages.push(['封装运行环境','演示：加入环境安装包 '+basename(runtime.path)]);
  for (const role of ROLE_ORDER.filter(role=>config.paths[role])) stages.push(['生成独立子安装器','演示：生成 '+ROLES[role].name+' 子安装器，使用独立产品标识。']);
  stages.push(['生成统一安装包','演示：配置环境优先安装、C 必装与 A/B 可选。'],['完成演示构建','演示：流程完成，未生成实际 EXE 文件。']);
  addBuildLog('开始模拟构建；不会调用 ISCC 或执行安装程序。');
  let status='success';
  for (let i=0;i<stages.length;i++) {
    if (cancelRequested) { status='cancelled'; break; }
    $('buildStage').textContent=stages[i][0]; $('readinessTitle').textContent=stages[i][0]; $('readinessText').textContent='仅演示构建流程';
    await delay(550);
    if (cancelRequested) { status='cancelled'; break; }
    addBuildLog(stages[i][1],i===stages.length-1?'success':'');
    const progress=Math.round((i+1)/stages.length*100); $('buildProgressFill').style.width=progress+'%'; $('buildPercent').textContent=progress+'%';
  }
  const record={id:Date.now()+'-'+Math.random().toString(36).slice(2,6),date:new Date().toISOString(),config:clone(config),name:artifactName(config,true),status,logs:clone(buildLogs)};
  if (status==='cancelled') { addBuildLog('演示已取消；未产生真实构建文件。','error'); record.logs=clone(buildLogs); $('buildStatusTag').textContent='已取消'; $('buildStatusTag').className='tag tag-gray'; $('buildStage').textContent='演示构建已取消'; toast('已取消演示打包'); }
  else { $('buildStatusTag').textContent='演示完成'; $('buildStatusTag').className='tag tag-green'; $('buildStage').textContent='演示完成 · 未生成实际安装包'; toast('演示打包完成，可以预览安装界面'); }
  history.unshift(record); history=history.slice(0,12); lockBuild(false); renderHistory(); persist();
  if (status==='success') showRecord(record);
}
function renderHistory() {
  $('historyCount').textContent=history.length;
  if (!history.length) { $('historyList').innerHTML=`<div class="history-empty">${icon('clock')}<h3>还没有构建记录</h3><p>完成一次演示打包后，记录会保存在这里。</p><button class="btn btn-white" data-go-workspace>前往工作台${icon('arrow')}</button></div>`; return; }
  $('historyList').innerHTML=`<table class="history-table"><thead><tr><th style="width:41%">安装包</th><th style="width:23%">包含内容</th><th style="width:18%">状态</th><th style="width:18%">操作</th></tr></thead><tbody>${history.map(record=>`<tr><td class="history-name">${escapeHtml(record.name)}<small>${escapeHtml(new Date(record.date).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}))}</small></td><td>${ROLE_ORDER.filter(role=>record.config.paths[role]).join(' + ')}<small>${record.config.runtimes.length} 个运行环境</small></td><td><span class="tag ${record.status==='success'?'tag-green':'tag-gray'}">${record.status==='success'?'演示完成':'已取消'}</span></td><td><button class="btn btn-text" data-record="${escapeHtml(record.id)}">查看详情</button></td></tr>`).join('')}</tbody></table>`;
}
function showRecord(record) {
  $('detailEyebrow').textContent='构建记录 · 模拟产物'; $('detailTitle').textContent=record.status==='success'?'演示打包完成':'演示打包已取消';
  $('detailBody').innerHTML=`<p class="detail-description">本次配置与日志已保存。此为原型演示，没有生成实际安装包。</p><div class="detail-row"><span>安装包名称</span><span>${escapeHtml(record.name)}</span></div><div class="detail-row"><span>输出目录</span><span>${escapeHtml(record.config.output)}</span></div><div class="detail-row"><span>业务软件</span><span>${ROLE_ORDER.filter(role=>record.config.paths[role]).map(role=>ROLES[role].name).join('、')}</span></div><div class="detail-row"><span>环境安装包</span><span>${record.config.runtimes.map(item=>escapeHtml(basename(item.path))).join('<br>')||'无'}</span></div><div class="detail-note">安装流程：先执行运行环境，再安装 C 和用户勾选的 A/B。三个软件各自独立安装、运行与卸载。</div><div class="detail-action"><button class="btn btn-white" id="recordPreview">${icon('eye')}预览此配置的安装界面</button><button class="btn btn-text" id="reuseRecord">使用此配置</button></div>`;
  $('recordPreview').onclick=()=>{ $('detailDialog').close(); openInstaller(record.config); };
  $('reuseRecord').onclick=()=>{if(building){toast('请先等待当前演示结束');return;} applyConfig(record.config);$('detailDialog').close();navigate('workspace');toast('已恢复此构建配置');};
  $('detailDialog').showModal();
}

function openInstaller(config=currentConfig()) {
  if (!config.paths.C) { toast('先填写功能软件 C 的路径，或加载完整示例。'); return; }
  installer={stage:'select',config:clone(config),selected:{C:true,A:false,B:false},scenario:'success',tasks:[],cancelRequested:false};
  renderInstaller(); $('installerDialog').showModal();
}
function environmentBlock(config) {
  return config.runtimes.length?`<div class="installer-environments"><h3>${icon('shield')}以下运行环境将优先安装</h3><div class="environment-chips">${config.runtimes.map(item=>`<span>${escapeHtml(basename(item.path))}</span>`).join('')}</div><p>所有环境完成后，再安装所选软件；环境失败时停止。</p></div>`:'';
}
function renderInstaller() {
  const state=installer, config=state.config, stage=state.stage;
  const step=stage==='select'?1:stage==='result'?3:2;
  document.querySelectorAll('[data-installer-step]').forEach(el=>{el.classList.toggle('current',Number(el.dataset.installerStep)===step);el.classList.toggle('done',Number(el.dataset.installerStep)<step);});
  $('installerNext').disabled=stage==='install'; $('installerBack').disabled=false; $('closeInstaller').disabled=false;
  $('installerFooterNote').textContent='此预览不会执行实际安装';
  const selectedRoles=ROLE_ORDER.filter(role=>config.paths[role]&&state.selected[role]);
  if (stage==='select') {
    $('installerBack').textContent='取消'; $('installerNext').innerHTML=`下一步${icon('arrow')}`;
    $('installerBody').innerHTML=`<h2>选择需要安装的软件</h2><p class="intro">${escapeHtml(config.name)} ${escapeHtml(config.version)} · 软件可独立运行与卸载</p>${ROLE_ORDER.filter(role=>config.paths[role]).map(role=>`<label class="install-choice ${state.selected[role]?'selected':''}"><input type="checkbox" data-install-role="${role}" ${state.selected[role]?'checked':''} ${role==='C'?'disabled':''}><span class="software-icon ${ROLES[role].color}">${icon(ROLES[role].icon)}</span><div><strong>${ROLES[role].name}</strong><p>${ROLES[role].description}</p></div><span class="tag ${role==='C'?'tag-blue':'tag-gray'}">${role==='C'?'必装':'可选'}</span></label>`).join('')}${environmentBlock(config)}<div class="preview-scenario"><span>原型演示场景</span><select id="installScenario" aria-label="安装预览演示场景"><option value="success">正常安装完成</option>${config.runtimes.length?'<option value="environment-fail">首个运行环境安装失败</option>':''}<option value="software-fail">首个业务软件安装失败</option></select></div>`;
    $('installScenario').value=state.scenario;
  } else if (stage==='summary') {
    $('installerBack').textContent='上一步'; $('installerNext').innerHTML=`开始安装${icon('arrow')}`;
    $('installerBody').innerHTML=`<h2>准备好开始安装</h2><p class="intro">请确认本次内容。运行环境会在业务软件之前安装。</p>${config.runtimes.length?`<div class="install-summary-group"><h3>${icon('layers')}第一阶段 · 运行环境</h3>${config.runtimes.map((item,index)=>`<div class="install-summary-item">${icon('file')}<div>${escapeHtml(basename(item.path))}<small>${escapeHtml(item.params||'使用安装包自身界面')}</small></div><span>${index+1}</span></div>`).join('')}</div>`:''}<div class="install-summary-group"><h3>${icon('app')}${config.runtimes.length?'第二阶段':'安装内容'} · 所选软件</h3>${selectedRoles.map(role=>`<div class="install-summary-item">${icon(ROLES[role].icon)}<div>${ROLES[role].name}<small>C:\\Program Files\\${ROLES[role].folder}</small></div><span>独立安装</span></div>`).join('')}</div><div class="detail-note">未勾选的软件保持原状；不会卸载已经存在的软件。各软件使用独立目录与卸载入口。</div>`;
  } else if (stage==='install') {
    $('installerBack').textContent=state.cancelRequested?'正在停止…':'停止后续安装'; $('installerBack').disabled=state.cancelRequested;
    $('installerNext').textContent='正在安装…'; $('installerFooterNote').textContent='模拟安装中 · 不修改系统';
    const completed=state.tasks.filter(task=>task.status==='done').length;
    const active=state.tasks.find(task=>task.status==='running');
    const percent=Math.round(completed/state.tasks.length*100);
    $('installerBody').innerHTML=`<h2>${active?.type==='runtime'?'正在准备运行环境':'正在安装所选软件'}</h2><p class="intro">${state.cancelRequested?'当前任务完成后停止。':'请稍候，安装向导将按阶段完成安装。'}</p><div class="install-progress-track"><div style="width:${percent}%"></div></div><div class="install-status-line"><span>${escapeHtml(active?.name||'准备安装')}</span><strong>${percent}%</strong></div><div class="install-tasks">${state.tasks.map((task,index)=>`<div class="install-task ${task.status}"><span>${task.status==='done'?'✓':index+1}</span><span>${escapeHtml(task.name)}</span><span class="task-status">${{pending:'等待中',running:'演示安装中',done:'已完成',failed:'失败'}[task.status]}</span></div>`).join('')}</div>`;
  } else {
    const success=state.result==='success', cancelled=state.result==='cancelled';
    $('installerBack').textContent='重新预览'; $('installerNext').textContent='完成';
    $('installerBody').innerHTML=`<div class="install-result-icon ${success?'':'failed'}">${icon(success?'check':'x')}</div><h2>${success?'演示安装完成':cancelled?'已停止后续安装':'演示安装未完成'}</h2><p class="intro">${success?'运行环境与所选软件均已完成演示安装。':escapeHtml(state.reason)}<br>此原型不会实际安装软件或修改系统。</p><div class="install-result-list">${state.tasks.map(task=>`<div class="install-result-item ${task.status==='failed'?'failed':task.status==='pending'?'pending':''}">${icon(task.status==='done'?'check':task.status==='failed'?'x':'clock')}<span>${escapeHtml(task.name)} · ${{done:'完成',failed:'安装失败',pending:'未执行',running:'未完成'}[task.status]}</span></div>`).join('')}</div><div class="detail-note">${success?'三个软件各自独立运行与卸载。安装结束后通过各自快捷方式启动。':'已成功的组件保持其状态，不自动卸载。可以修复问题后重新运行安装包。'}</div>`;
  }
}
async function simulateInstall() {
  const state=installer;
  state.tasks=[...state.config.runtimes.map(item=>({name:basename(item.path),type:'runtime',status:'pending'})),...ROLE_ORDER.filter(role=>state.config.paths[role]&&state.selected[role]).map(role=>({name:ROLES[role].name,type:'software',status:'pending'}))];
  state.stage='install'; state.cancelRequested=false; renderInstaller();
  let failed=false;
  for (const task of state.tasks) {
    task.status='running'; renderInstaller(); await delay(900);
    if ((state.scenario==='environment-fail'&&task.type==='runtime')||(state.scenario==='software-fail'&&task.type==='software')) {
      task.status='failed'; state.result='failed'; state.reason=task.type==='runtime'?'运行环境安装失败，后续业务软件未执行。':'业务软件安装失败，已停止执行后续任务。'; failed=true; break;
    }
    task.status='done'; renderInstaller();
    if (state.cancelRequested) { state.result='cancelled';state.reason='当前任务已完成，剩余任务未执行。';failed=true;break; }
    await delay(120);
  }
  if (!failed) state.result='success';
  state.stage='result'; renderInstaller();
}
function closeInstaller() {
  if (installer?.stage==='install') { installer.cancelRequested=true;renderInstaller();toast('当前任务完成后停止，不强制中断安装器。');return; }
  $('installerDialog').close();
}

document.addEventListener('click',event=>{
  const target=event.target.closest('button,a'); if (!target) return;
  if (target.dataset.page) navigate(target.dataset.page);
  if (target.hasAttribute('data-go-workspace')) navigate('workspace');
  if (target.dataset.browse) openPicker(target.dataset.browse);
  if (target.dataset.clear) { $('path'+target.dataset.clear).value='';renderSummary();persist(); }
  if (target.dataset.close) $(target.dataset.close).close();
  if (target.dataset.pickerValue) { $('pickerPath').value=target.dataset.pickerValue; $('pickerChoices').querySelectorAll('.picker-choice').forEach(el=>el.classList.toggle('selected',el===target)); }
  if (target.dataset.runtimeAction) {
    const index=runtimes.findIndex(item=>item.id===target.closest('[data-runtime]').dataset.runtime);
    if (target.dataset.runtimeAction==='remove') runtimes.splice(index,1);
    else {const to=index+(target.dataset.runtimeAction==='up'?-1:1);if(to>=0&&to<runtimes.length)[runtimes[index],runtimes[to]]=[runtimes[to],runtimes[index]];}
    renderRuntimes();persist();
  }
  if (target.dataset.record) { const record=history.find(item=>item.id===target.dataset.record); if(record)showRecord(record); }
});
$('packageForm').addEventListener('input',event=>{
  if (event.target.dataset.runtimeParams) {
    const item=runtimes.find(item=>item.id===event.target.dataset.runtimeParams); if(item)item.params=event.target.value;
    const summary=event.target.closest('details').querySelector('summary span'); summary.textContent=item.type==='MSI'?'MSI 安装参数':item.params?'已配置安装参数':'使用安装包自身界面';
  } else {clearErrors();renderSummary();}
  persist();
});
$('packageForm').addEventListener('submit',startBuild);
$('confirmPath').onclick=confirmPicker;
$('addRuntime').onclick=$('addRuntimeEmpty').onclick=()=>openPicker('runtime');
$('loadExample').onclick=()=>{applyConfig({paths:{C:'D:\\DesktopApps\\FeatureApp',A:'D:\\DesktopApps\\AuthApp',B:'D:\\DesktopApps\\EngineApp'},output:'D:\\InstallPackages',name:'桌面软件套装',version:'1.0.0',runtimes:[{path:'D:\\Prerequisites\\VC_redist.x64.exe'},{path:'D:\\Prerequisites\\VC_redist.x86.exe'}]});toast('已加载 3 款软件与 2 个 VC++ 环境示例');};
$('previewButton').onclick=()=>openInstaller();
$('cancelBuild').onclick=()=>{cancelRequested=true;$('cancelBuild').disabled=true;$('cancelBuild').textContent='正在取消…';};
$('collapseConsole').onclick=()=>{ $('buildLogs').hidden=!$('buildLogs').hidden; $('collapseConsole').innerHTML=icon($('buildLogs').hidden?'down':'up'); $('collapseConsole').setAttribute('aria-label',$('buildLogs').hidden?'展开日志':'收起日志'); };
$('exportConfig').onclick=()=>{
  const config=currentConfig();const blob=new Blob([JSON.stringify({format:'desktop-packager-prototype',version:1,...config},null,2)],{type:'application/json'});
  const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='安装包工坊-配置.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1500);toast('配置已导出为 JSON 文件');
};
$('importConfig').onclick=()=>$('importFile').click();
$('importFile').onchange=async event=>{
  const file=event.target.files[0];if(!file)return;
  try {if(file.size>1024*1024)throw new Error();const data=JSON.parse(await file.text());if(data.format!=='desktop-packager-prototype'||!data.paths||!Array.isArray(data.runtimes))throw new Error();applyConfig(data);toast('配置已导入');}catch(_){toast('无法导入，请选择此原型导出的 JSON 配置。');}
  event.target.value='';
};
$('saveSettings').onclick=()=>{const path=normalizePath($('compilerPath').value);if(!absolutePath(path)||! /\\ISCC\.exe$/i.test(path)){toast('请填写指向 ISCC.exe 的完整路径。');return;}compiler=path;persist();toast('编译器路径已保存；原型不检测真实文件。');};
$('resetCompiler').onclick=()=>{$('compilerPath').value=COMPILER;toast('已恢复默认路径，点击保存设置后生效。');};
$('aboutButton').onclick=()=>{$('detailEyebrow').textContent='关于此原型';$('detailTitle').textContent='安装包工坊';$('detailBody').innerHTML='<p class="detail-description">用于确认桌面打包工具的布局与交互。路径选择使用示例数据，打包和安装均为模拟。</p><div class="detail-note">支持 C 必选、A/B 可选，以及多个已有运行环境安装包。正式流程先安装运行环境，再独立安装所选软件。<br><br>你确认原型后，再使用 Qt 严格对照此原型实现桌面应用。</div>';$('detailDialog').showModal();};
$('installerBody').addEventListener('change',event=>{
  if(event.target.dataset.installRole){installer.selected[event.target.dataset.installRole]=event.target.checked;event.target.closest('.install-choice').classList.toggle('selected',event.target.checked);}
  if(event.target.id==='installScenario')installer.scenario=event.target.value;
});
$('installerNext').onclick=()=>{if(installer.stage==='select'){installer.stage='summary';renderInstaller();}else if(installer.stage==='summary')simulateInstall();else if(installer.stage==='result')closeInstaller();};
$('installerBack').onclick=()=>{if(installer.stage==='select')closeInstaller();else if(installer.stage==='summary'||installer.stage==='result'){installer.stage='select';renderInstaller();}else closeInstaller();};
$('closeInstaller').onclick=closeInstaller;
$('installerDialog').addEventListener('cancel',event=>{if(installer?.stage==='install'){event.preventDefault();closeInstaller();}});
$('compilerPath').value=compiler;
applyConfig(saved.config&&typeof saved.config==='object'?saved.config:{paths:{},output:'',runtimes:[]});
renderHistory();
