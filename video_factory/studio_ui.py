"""创作工作台前端页面（P10）。

返回一个完整的单页应用 HTML 字符串（内联 CSS/JS，零外部资源，全中文）。视觉方向：
深色剪辑软件风——背景 #17181c、面板 #1f2127、金色主强调 #e8b84b（与 Remotion 特效
组件同色，品牌统一）。左栏生产表单、右栏任务看板；三个页签：单条生产 / 批量 / 凭据依赖。

数据契约：页面开局 fetch /api/meta 拿平台/风格/画幅/音色/凭据/依赖，动态渲染表单；
提交走 /api/jobs；2 秒轮询 /api/jobs 刷新看板；成片经 /media?path= 内嵌 <video> 预览。
"""

from __future__ import annotations

_CSS = """
:root {
  --bg: #17181c;
  --panel: #1f2127;
  --panel-2: #24262d;
  --line: #2a2c34;
  --gold: #e8b84b;
  --gold-dim: #b8912f;
  --text: #e8e8ea;
  --muted: #9a9ba3;
  --green: #3fbf7f;
  --red: #e05a52;
  --blue: #4a90d9;
  --radius: 10px;
  --shadow: inset 0 1px 0 rgba(255,255,255,0.03), 0 6px 20px rgba(0,0,0,0.28);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.6;
}
a { color: var(--gold); text-decoration: none; }
a:hover { color: #f2ca63; }
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; padding: 14px 24px;
  border-bottom: 1px solid var(--line);
  background: linear-gradient(180deg, #1b1c22, #17181c);
  position: sticky; top: 0; z-index: 20;
}
.brand { display: flex; align-items: baseline; gap: 12px; }
.brand h1 { margin: 0; font-size: 20px; font-weight: 800; letter-spacing: 1px; }
.brand .accent { color: var(--gold); }
.brand .tag { color: var(--muted); font-size: 12px; }
.top-right { display: flex; align-items: center; gap: 16px; font-size: 12px; }
.cred-dots { display: flex; align-items: center; gap: 8px; }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: var(--muted); }
.dot.on { background: var(--green); box-shadow: 0 0 6px rgba(63,191,127,0.6); }
.dot.off { background: var(--red); }
.tabs { display: flex; gap: 4px; padding: 0 24px; border-bottom: 1px solid var(--line); background: #1a1b20; }
.tab {
  padding: 12px 18px; cursor: pointer; color: var(--muted);
  border-bottom: 2px solid transparent; font-weight: 700; font-size: 13px;
}
.tab:hover { color: var(--text); }
.tab.active { color: var(--gold); border-bottom-color: var(--gold); }
.page { display: none; }
.page.active { display: block; }
.layout { display: grid; grid-template-columns: 55% 45%; gap: 18px; padding: 20px 24px; align-items: start; }
@media (max-width: 1000px) { .layout { grid-template-columns: 1fr; } }
.col-right { position: sticky; top: 96px; }
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 16px 18px; margin-bottom: 16px;
}
.card h2 { margin: 0 0 4px; font-size: 15px; }
.card .desc { color: var(--muted); font-size: 12px; margin: 0 0 12px; }
label { display: block; margin: 12px 0 6px; font-size: 12px; font-weight: 700; color: #c9cad2; }
input[type=text], input[type=number], input[type=password], textarea, select {
  width: 100%; background: var(--panel-2); border: 1px solid var(--line);
  color: var(--text); padding: 10px 11px; border-radius: 8px; font-size: 13px;
  font-family: inherit; outline: none;
}
textarea { min-height: 72px; resize: vertical; line-height: 1.5; }
input:focus, textarea:focus, select:focus { border-color: var(--gold); box-shadow: 0 0 0 2px rgba(232,184,75,0.18); }
.drop {
  border: 1.5px dashed #3a3d47; border-radius: 8px; padding: 18px; text-align: center;
  cursor: pointer; color: var(--muted); background: var(--panel-2); transition: all .16s ease;
}
.drop:hover, .drop.over { border-color: var(--gold); color: var(--text); background: #262832; }
.drop input[type=file] { display: none; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
.chip {
  padding: 8px 12px; border: 1px solid var(--line); border-radius: 20px; cursor: pointer;
  background: var(--panel-2); color: var(--muted); font-size: 12px; font-weight: 700; transition: all .14s ease;
}
.chip:hover { border-color: var(--gold-dim); color: var(--text); }
.chip.active { border-color: var(--gold); color: #1a1b20; background: var(--gold); }
.style-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-top: 6px; }
.style-card {
  border: 1px solid var(--line); border-radius: 8px; padding: 10px 11px; cursor: pointer;
  background: var(--panel-2); transition: all .14s ease;
}
.style-card:hover { border-color: var(--gold-dim); }
.style-card.active { border-color: var(--gold); background: #2b2820; }
.style-card strong { display: block; font-size: 13px; }
.style-card span { display: block; color: var(--muted); font-size: 11px; margin-top: 3px; line-height: 1.4; }
.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.range-wrap { display: flex; align-items: center; gap: 10px; }
.range-wrap input[type=range] { accent-color: var(--gold); }
.range-val { min-width: 42px; text-align: right; color: var(--gold); font-weight: 700; }
.switch-row { display: flex; align-items: center; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--line); }
.switch-row:last-child { border-bottom: none; }
.switch { position: relative; width: 42px; height: 22px; }
.switch input { display: none; }
.slider { position: absolute; inset: 0; background: #3a3d47; border-radius: 22px; cursor: pointer; transition: .18s; }
.slider::before { content: ""; position: absolute; width: 16px; height: 16px; left: 3px; top: 3px; background: #fff; border-radius: 50%; transition: .18s; }
.switch input:checked + .slider { background: var(--gold); }
.switch input:checked + .slider::before { transform: translateX(20px); }
.warn-dot { color: var(--gold); font-size: 11px; margin-left: 8px; }
.btn {
  border: 0; border-radius: 8px; padding: 12px 18px; font-size: 14px; font-weight: 800;
  cursor: pointer; background: var(--gold); color: #1a1b20; font-family: inherit;
}
.btn:hover { background: #f2ca63; }
.btn:disabled { opacity: .5; cursor: wait; }
.btn-big { width: 100%; padding: 14px; margin-top: 14px; }
.btn-ghost { background: transparent; color: var(--text); border: 1px solid var(--line); }
.btn-ghost:hover { background: var(--panel-2); border-color: var(--gold-dim); }
.errbox { color: var(--red); font-size: 12px; margin: 8px 0; white-space: pre-wrap; }
.task { border: 1px solid var(--line); border-radius: var(--radius); padding: 14px; margin-bottom: 12px; background: var(--panel); }
.task-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
.task-name { font-weight: 800; font-size: 14px; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 12px; background: var(--panel-2); color: var(--muted); border: 1px solid var(--line); }
.badge.plat { color: var(--gold); border-color: var(--gold-dim); }
.stage-chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0; }
.sc { font-size: 11px; padding: 4px 9px; border-radius: 6px; border: 1px solid var(--line); color: var(--muted); background: var(--panel-2); }
.sc.done { color: var(--green); border-color: rgba(63,191,127,.4); background: rgba(63,191,127,.08); }
.sc.run { color: #1a1b20; background: var(--gold); border-color: var(--gold); animation: pulse 1.1s ease-in-out infinite; }
.sc.fail { color: var(--red); border-color: rgba(224,90,82,.5); background: rgba(224,90,82,.1); }
.sc.skip { color: var(--muted); font-style: italic; opacity: .7; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .55; } }
.task-meta { color: var(--muted); font-size: 11px; }
.err-detail { margin-top: 8px; }
.err-detail summary { cursor: pointer; color: var(--red); font-size: 12px; font-weight: 700; }
.err-detail pre { white-space: pre-wrap; color: var(--red); font-size: 12px; background: rgba(224,90,82,.06); padding: 8px; border-radius: 6px; margin: 6px 0 0; }
.task video { width: 100%; border-radius: 8px; margin-top: 10px; background: #000; }
.path-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.path-row code { flex: 1; background: var(--panel-2); border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: Consolas, monospace; }
.copy-btn { font-size: 11px; padding: 6px 10px; border-radius: 6px; }
.links { display: flex; gap: 12px; margin-top: 8px; font-size: 12px; }
.empty { color: var(--muted); font-size: 13px; text-align: center; padding: 30px 0; }
.batch-wrap { padding: 20px 24px; max-width: 1100px; }
.batch-actions { display: flex; gap: 10px; margin: 12px 0; }
.batch-job { border: 1px solid var(--line); border-radius: var(--radius); padding: 14px 16px; margin-bottom: 14px; background: var(--panel-2); }
.bj-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.bj-title { font-weight: 800; color: var(--gold); font-size: 13px; }
.bj-ops { display: flex; gap: 8px; }
.bj-op { padding: 5px 12px; font-size: 12px; border-radius: 6px; }
.bj-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
@media (max-width: 700px) { .bj-grid { grid-template-columns: 1fr; } }
.bj-file { display: flex; gap: 8px; align-items: center; }
.bj-file input[type=text] { flex: 1; }
.bj-up { flex: 0 0 auto; padding: 9px 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); color: var(--text); cursor: pointer; font-size: 12px; font-weight: 700; white-space: nowrap; }
.bj-up:hover { border-color: var(--gold-dim); color: var(--gold); }
.bj-up input[type=file] { display: none; }
.bj-adv { margin-top: 10px; border-top: 1px dashed var(--line); padding-top: 8px; }
.bj-adv summary { cursor: pointer; color: var(--muted); font-size: 12px; font-weight: 700; padding: 4px 0; }
.bj-adv[open] summary { color: var(--text); }
.result-table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 12px; }
.result-table th, .result-table td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); }
.result-table th { color: var(--muted); }
.result-table .ok { color: var(--green); }
.result-table .bad { color: var(--red); }
.cred-wrap { padding: 20px 24px; max-width: 760px; }
.cred-row { display: grid; grid-template-columns: 30px 1fr auto auto; gap: 10px; align-items: center; margin-bottom: 12px; }
.cred-row .name { font-weight: 700; font-size: 13px; }
.dep-list { margin-top: 14px; }
.dep-item { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--line); font-size: 12px; }
.notice { background: rgba(232,184,75,.08); border: 1px solid var(--gold-dim); border-radius: 8px; padding: 12px; color: #e6cf9a; font-size: 12px; margin-top: 16px; }
"""


def _script() -> str:
    """页面 JS：拉取 meta、渲染表单、上传、提交、轮询看板、批量、凭据。"""
    return r"""
const S = { meta: null, platform: '', style: '', assetGroup: null, assets: [], batchJobs: [], visual: 'video' };

function h(sel) { return document.querySelector(sel); }
function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

async function api(url, opts) {
  const r = await fetch(url, opts);
  const t = await r.text();
  let data = {}; try { data = t ? JSON.parse(t) : {}; } catch (e) {}
  return { status: r.status, data };
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + name));
}

async function loadMeta() {
  const { data } = await api('/api/meta');
  S.meta = data;
  renderCredDots(data.credentials);
  renderPlatforms(data.platforms);
  renderStyles(data.styles);
  renderTtsProviders(data);
  renderAdvanced(data);
  renderCredPage(data);
  // meta 到位后再渲染批量清单：平台/风格/画幅等下拉需要 meta 填充选项。
  if (!S.batchJobs.length) addBatchJob(); else renderBatchJobs();
}

function renderCredDots(creds) {
  const wrap = h('#credDots'); if (!wrap) return;
  wrap.innerHTML = Object.keys(creds).map(k =>
    `<span class="dot ${creds[k] ? 'on' : 'off'}" title="${esc(k)}"></span>`).join('');
}

function renderPlatforms(platforms) {
  const wrap = h('#platformChips');
  const keys = Object.keys(platforms);
  let html = keys.map(k => `<div class="chip" data-plat="${esc(k)}">${esc(platforms[k].label)}</div>`).join('');
  html += `<div class="chip" data-plat="">自定义</div>`;
  wrap.innerHTML = html;
  wrap.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => selectPlatform(c.dataset.plat)));
}

function selectPlatform(key) {
  S.platform = key;
  document.querySelectorAll('#platformChips .chip').forEach(c => c.classList.toggle('active', c.dataset.plat === key));
  const p = S.meta.platforms[key];
  if (p) {
    h('#f_aspect').value = p.aspect;
    h('#f_fit').value = p.fit;
    h('#f_duration').value = p.duration;
    h('#f_subtitles').checked = p.subtitles;
    h('#f_effects').checked = p.effects;
  }
}

function renderStyles(styles) {
  const wrap = h('#styleGrid');
  wrap.innerHTML = styles.map(s =>
    `<div class="style-card" data-style="${esc(s.key)}"><strong>${esc(s.label)}</strong><span>${esc(s.tts_hint)}</span></div>`).join('');
  wrap.querySelectorAll('.style-card').forEach(c => c.addEventListener('click', () => {
    S.style = c.dataset.style;
    wrap.querySelectorAll('.style-card').forEach(x => x.classList.toggle('active', x === c));
  }));
}

function renderTtsProviders(meta) {
  const sel = h('#f_tts');
  sel.innerHTML = meta.tts_providers.map(p => `<option value="${esc(p)}">${esc(p)}（默认 ${esc(meta.voice_defaults[p] || '')}）</option>`).join('');
  sel.addEventListener('change', updateTtsWarn);
  updateTtsWarn();
}

function updateTtsWarn() {
  const p = h('#f_tts').value;
  const creds = S.meta.credentials;
  let warn = '';
  if (p === 'doubao' && !(creds.VOLC_TTS_APIKEY || (creds.VOLC_TTS_APPID && creds.VOLC_TTS_TOKEN))) warn = '未配置豆包凭据';
  if (p === 'openai' && !creds.OPENAI_API_KEY) warn = '未配置 OpenAI 凭据';
  h('#ttsWarn').innerHTML = warn ? `<span class="warn-dot">● ${esc(warn)}</span>` : '';
}

function renderAdvanced(meta) {
  h('#f_aspect').innerHTML = meta.aspects.map(a => `<option value="${esc(a)}">${esc(a)}</option>`).join('');
  const FIT_LABELS = { pad: '补黑边（pad·保内容全）', crop: '放大裁满（crop·可能裁掉边缘）', blur: '模糊背景填充（blur·竖屏推荐）' };
  h('#f_fit').innerHTML = meta.fits.map(f => `<option value="${esc(f)}">${esc(FIT_LABELS[f] || f)}</option>`).join('');
  const LLM_LABELS = { auto: '自动（按已配置的凭据选择）', openai: 'OpenAI（GPT）', anthropic: 'Anthropic（Claude）', deepseek: 'DeepSeek（国内直连·高性价比）' };
  h('#f_llm').innerHTML = (meta.llm_providers || ['auto']).map(p => `<option value="${esc(p)}">${esc(LLM_LABELS[p] || p)}</option>`).join('');
}

async function uploadFile(file, kind, group) {
  let url = `/api/upload?kind=${encodeURIComponent(kind)}&name=${encodeURIComponent(file.name)}`;
  if (group) url += `&group=${encodeURIComponent(group)}`;
  const r = await fetch(url, { method: 'POST', body: file });
  const data = await r.json();
  if (r.status !== 200) throw new Error(data.error || '上传失败');
  return data.path;
}

function bindUploads() {
  const sd = h('#sourceDrop'), si = h('#sourceFile');
  sd.addEventListener('click', () => si.click());
  sd.addEventListener('dragover', e => { e.preventDefault(); sd.classList.add('over'); });
  sd.addEventListener('dragleave', () => sd.classList.remove('over'));
  sd.addEventListener('drop', e => { e.preventDefault(); sd.classList.remove('over'); if (e.dataTransfer.files[0]) handleSource(e.dataTransfer.files[0]); });
  si.addEventListener('change', () => { if (si.files[0]) handleSource(si.files[0]); });

  const ad = h('#assetDrop'), ai = h('#assetFile');
  ad.addEventListener('click', () => ai.click());
  ad.addEventListener('dragover', e => { e.preventDefault(); ad.classList.add('over'); });
  ad.addEventListener('dragleave', () => ad.classList.remove('over'));
  ad.addEventListener('drop', e => { e.preventDefault(); ad.classList.remove('over'); handleAssets(e.dataTransfer.files); });
  ai.addEventListener('change', () => handleAssets(ai.files));
}

async function handleSource(file) {
  h('#sourceDropLabel').textContent = '上传中…';
  try {
    const path = await uploadFile(file, 'source');
    h('#f_source').value = path;
    h('#sourceDropLabel').textContent = '已上传：' + file.name;
  } catch (e) { h('#sourceDropLabel').textContent = '上传失败：' + e.message; }
}

async function handleAssets(files) {
  if (!files || !files.length) return;
  if (!S.assetGroup) S.assetGroup = 'g' + Date.now();
  h('#assetDropLabel').textContent = '上传中…';
  try {
    let dir = '';
    for (const f of files) { const p = await uploadFile(f, 'asset', S.assetGroup); dir = p.substring(0, p.lastIndexOf('/')) || p.substring(0, p.lastIndexOf('\\')); S.assets.push(p); }
    h('#f_assets').value = dir;
    h('#assetDropLabel').textContent = `已上传 ${S.assets.length} 个素材`;
  } catch (e) { h('#assetDropLabel').textContent = '上传失败：' + e.message; }
}

function collectForm() {
  const f = {};
  const put = (k, v) => { if (v !== '' && v != null) f[k] = v; };
  put('source', h('#f_source').value.trim());
  put('assets', h('#f_assets').value.trim());
  put('visual_source', S.visual);
  put('platform', S.platform);
  put('style', S.style);
  put('brief', h('#f_brief').value.trim());
  put('tts', h('#f_tts').value);
  put('voice', h('#f_voice').value.trim());
  put('bgm', h('#f_bgm').value.trim());
  put('bgm_volume', h('#f_bgmvol').value);
  put('duration', h('#f_duration').value.trim());
  put('aspect', h('#f_aspect').value);
  put('fit', h('#f_fit').value);
  if (h('#f_llm').value && h('#f_llm').value !== 'auto') put('llm', h('#f_llm').value);
  put('name', h('#f_name').value.trim());
  f.subtitles = h('#f_subtitles').checked;
  f.effects = h('#f_effects').checked;
  f.sfx = h('#f_sfx').checked;
  put('sfx_volume', h('#f_sfxvol').value);
  f.lower_thirds = h('#f_lower').checked;
  return f;
}

async function submitJob() {
  const btn = h('#submitBtn'); const err = h('#formErr');
  err.textContent = ''; btn.disabled = true;
  try {
    const { status, data } = await api('/api/jobs', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(collectForm()) });
    if (status !== 200) { err.textContent = (data.errors || [data.error || '提交失败']).join('\n'); return; }
    err.textContent = '';
  } finally { btn.disabled = false; }
  refreshTasks();
}

function selectVisual(key) {
  S.visual = (key === 'ai_image') ? 'ai_image' : 'video';
  document.querySelectorAll('#visualChips .chip').forEach(c => c.classList.toggle('active', c.dataset.vis === S.visual));
  const ai = S.visual === 'ai_image';
  const block = h('#assetsBlock');
  if (block) block.style.opacity = ai ? '0.4' : '1';
  const hint = h('#aiImageHint');
  if (hint) hint.style.display = ai ? 'block' : 'none';
}

const STAGE_LABELS = { rewrite: '文案改写', image_gen: 'AI 生图', assemble: '素材拼装', effects: '特效', subtitles: '字幕' };
const ALL_STAGES = ['rewrite', 'image_gen', 'assemble', 'effects', 'subtitles'];

function stageClass(task, stage) {
  if (task.stage_failed === stage) return 'fail';
  if (task.stages_done.includes(stage)) return 'done';
  if (task.current_stage === stage) return 'run';
  return '';
}

function renderTask(t) {
  // image_gen 只在 ai_image 任务出现：仅当该任务确实跑到/跑过它时才显示这枚阶段徽标，
  // 避免视频模式任务挂一枚永远灰着的"AI 生图"造成"卡住了"的错觉。
  const stages = ALL_STAGES.filter(st =>
    st !== 'image_gen' || (t.stages_done || []).includes('image_gen') || t.current_stage === 'image_gen'
  );
  const chips = stages.map(st => {
    const cls = stageClass(t, st);
    return `<span class="sc ${cls}">${esc(STAGE_LABELS[st])}</span>`;
  }).join('');
  let body = '';
  if (t.status === 'failed' && t.error) {
    body += `<details class="err-detail" open><summary>失败：${esc(t.stage_failed || '')} 阶段</summary><pre>${esc(t.error)}</pre></details>`;
  }
  if (t.status === 'ok' && t.final) {
    body += `<video controls src="/media?path=${encodeURIComponent(t.final)}"></video>`;
    body += `<div class="path-row"><code title="${esc(t.final)}">${esc(t.final)}</code><button class="btn copy-btn" data-copy="${esc(t.final)}">复制</button></div>`;
    const links = [];
    if (t.outputs.rewrite) links.push(`<a href="/media?path=${encodeURIComponent(t.outputs.rewrite)}" target="_blank">rewrite.json</a>`);
    if (t.outputs.assembly_plan) links.push(`<a href="/media?path=${encodeURIComponent(t.outputs.assembly_plan)}" target="_blank">assembly_plan.json</a>`);
    if (links.length) body += `<div class="links">${links.join('')}</div>`;
  }
  const elapsed = t.elapsed_seconds ? `${t.elapsed_seconds.toFixed(1)}s` : '';
  return `<div class="task">
    <div class="task-head"><span class="task-name">${esc(t.name)}</span>
      <span>${t.platform ? `<span class="badge plat">${esc(t.platform)}</span> ` : ''}<span class="badge">${esc(statusLabel(t.status))}</span></span>
    </div>
    <div class="stage-chips">${chips}</div>
    <div class="task-meta">${esc(t.created_at)} ${elapsed ? '· ' + elapsed : ''}</div>
    ${body}
  </div>`;
}

function statusLabel(s) { return {queued:'排队中',running:'生产中',ok:'完成',failed:'失败',invalid:'非法'}[s] || s; }

// 复制按钮走事件委托 + data 属性：严禁把动态数据插进内联 onclick
// （HTML 实体在进 JS 解析前已被解码，esc() 防不住 JS 字符串上下文注入）。
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.copy-btn');
  if (btn && btn.dataset.copy) copyText(btn.dataset.copy);
});

async function refreshTasks() {
  const { data } = await api('/api/jobs');
  const list = data.jobs || [];
  const wrap = h('#taskBoard');
  wrap.innerHTML = list.length ? list.map(renderTask).join('') : '<div class="empty">还没有任务。填好左侧表单点「开始生成」。</div>';
}

function copyText(txt) { navigator.clipboard && navigator.clipboard.writeText(txt); }

// ----- 批量：表单化任务清单（不再手写 JSON）-----
// 三态开关：空=跟随平台预设、on=开、off=关。留空的高级字段一律不下发，交后端套平台预设。
const BATCH_TOGGLE_OPTS = [['', '跟随平台预设'], ['on', '开'], ['off', '关']];

function newGroup() { return 'bg' + Date.now() + Math.random().toString(36).slice(2, 6); }

const VISUAL_OPTS = [['video', '视频素材'], ['ai_image', 'AI 生图（豆包）']];

function blankBatchJob() {
  const tts = (S.meta && S.meta.tts_providers && S.meta.tts_providers[0]) || 'doubao';
  return { name: '', platform: '', style: '', source: '', assets: '', brief: '', tts,
    voice: '', bgm: '', duration: '', aspect: '', fit: '', subtitles: '', effects: '',
    sfx: '', lower_thirds: false, visual_source: 'video', _grp: newGroup() };
}

function addBatchJob(seed) {
  const job = seed ? Object.assign({}, seed, { name: '', _grp: newGroup() }) : blankBatchJob();
  S.batchJobs.push(job);
  renderBatchJobs();
}
function removeBatchJob(i) {
  S.batchJobs.splice(i, 1);
  if (!S.batchJobs.length) S.batchJobs.push(blankBatchJob());
  renderBatchJobs();
}
function dupBatchJob(i) {
  const copy = Object.assign({}, S.batchJobs[i], { _grp: newGroup() });
  S.batchJobs.splice(i + 1, 0, copy);
  renderBatchJobs();
}

function optionsHtml(list, sel) {
  return list.map(v => {
    const val = Array.isArray(v) ? v[0] : v;
    const lab = Array.isArray(v) ? v[1] : v;
    return `<option value="${esc(val)}"${val === sel ? ' selected' : ''}>${esc(lab)}</option>`;
  }).join('');
}

function renderBatchJobs() {
  const meta = S.meta || {};
  const platforms = meta.platforms ? Object.keys(meta.platforms) : [];
  const styles = meta.styles || [];
  const tts = meta.tts_providers || [];
  const aspects = meta.aspects || [];
  const fits = meta.fits || [];
  h('#batchJobs').innerHTML = S.batchJobs.map((j, i) => `
    <div class="batch-job" data-idx="${i}">
      <div class="bj-head">
        <span class="bj-title">任务 ${i + 1}</span>
        <div class="bj-ops">
          <button type="button" class="btn btn-ghost bj-op" data-act="dup" data-idx="${i}">复制</button>
          <button type="button" class="btn btn-ghost bj-op" data-act="del" data-idx="${i}">删除</button>
        </div>
      </div>
      <div class="bj-grid">
        <div><label>任务名（可选）</label><input type="text" data-idx="${i}" data-f="name" value="${esc(j.name)}" placeholder="留空自动命名"></div>
        <div><label>平台</label><select data-idx="${i}" data-f="platform"><option value="">自定义 / 不指定</option>${platforms.map(k => `<option value="${esc(k)}"${k === j.platform ? ' selected' : ''}>${esc(meta.platforms[k].label)}</option>`).join('')}</select></div>
        <div><label>文案风格</label><select data-idx="${i}" data-f="style"><option value="">不指定</option>${styles.map(s => `<option value="${esc(s.key)}"${s.key === j.style ? ' selected' : ''}>${esc(s.label)}</option>`).join('')}</select></div>
        <div><label>配音引擎</label><select data-idx="${i}" data-f="tts">${tts.map(p => `<option value="${esc(p)}"${p === j.tts ? ' selected' : ''}>${esc(p)}</option>`).join('')}</select></div>
      </div>
      <label>视觉来源</label>
      <select data-idx="${i}" data-f="visual_source">${VISUAL_OPTS.map(([v, l]) => `<option value="${v}"${v === (j.visual_source || 'video') ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>
      <label>原片 / 字幕 / 文本（source）</label>
      <div class="bj-file"><input type="text" data-idx="${i}" data-f="source" value="${esc(j.source)}" placeholder="上传，或粘贴本机绝对路径"><label class="bj-up">上传<input type="file" data-idx="${i}" data-up="source"></label></div>
      <label>素材库（assets 目录，AI 生图时可留空）</label>
      <div class="bj-file"><input type="text" data-idx="${i}" data-f="assets" value="${esc(j.assets)}" placeholder="上传多个素材，或粘贴本机目录路径"><label class="bj-up">上传<input type="file" data-idx="${i}" data-up="assets" multiple></label></div>
      <label>创作简报（brief，可选）</label>
      <textarea data-idx="${i}" data-f="brief" placeholder="目标人群、重点、禁忌等">${esc(j.brief)}</textarea>
      <details class="bj-adv">
        <summary>高级：时长 / 音色 / BGM / 画幅 / 字幕特效开关（留空跟随平台预设）</summary>
        <div class="bj-grid">
          <div><label>时长（秒）</label><input type="number" min="1" data-idx="${i}" data-f="duration" value="${esc(j.duration)}" placeholder="平台预设"></div>
          <div><label>音色（voice）</label><input type="text" data-idx="${i}" data-f="voice" value="${esc(j.voice)}" placeholder="默认音色"></div>
          <div><label>画幅（aspect）</label><select data-idx="${i}" data-f="aspect"><option value="">跟随平台预设</option>${optionsHtml(aspects, j.aspect)}</select></div>
          <div><label>填充方式（fit）</label><select data-idx="${i}" data-f="fit"><option value="">跟随平台预设</option>${optionsHtml(fits, j.fit)}</select></div>
          <div><label>字幕</label><select data-idx="${i}" data-f="subtitles">${optionsHtml(BATCH_TOGGLE_OPTS, j.subtitles)}</select></div>
          <div><label>特效</label><select data-idx="${i}" data-f="effects">${optionsHtml(BATCH_TOGGLE_OPTS, j.effects)}</select></div>
          <div><label>特效音</label><select data-idx="${i}" data-f="sfx">${optionsHtml(BATCH_TOGGLE_OPTS, j.sfx)}</select></div>
        </div>
        <label>背景音乐（BGM 路径，可选）</label>
        <input type="text" data-idx="${i}" data-f="bgm" value="${esc(j.bgm)}" placeholder="粘贴本机 BGM 路径">
        <div class="switch-row" style="margin-top:10px"><span>底部花字条（每节小标题横条）</span><label class="switch"><input type="checkbox" data-idx="${i}" data-f="lower_thirds"${j.lower_thirds ? ' checked' : ''}><span class="slider"></span></label></div>
      </details>
    </div>`).join('');
}

function batchFieldUpdate(el) {
  const idx = Number(el.dataset.idx);
  const f = el.dataset.f;
  if (!(idx >= 0) || !f || !S.batchJobs[idx]) return;
  S.batchJobs[idx][f] = (el.type === 'checkbox') ? el.checked : el.value;
}

async function batchUpload(input) {
  const idx = Number(input.dataset.idx);
  const kind = input.dataset.up;
  const files = input.files;
  const job = S.batchJobs[idx];
  if (!job || !files || !files.length) return;
  const card = input.closest('.batch-job');
  try {
    if (kind === 'source') {
      const p = await uploadFile(files[0], 'source');
      job.source = p; card.querySelector('[data-f="source"]').value = p;
    } else {
      let dir = '';
      for (const f of files) { const p = await uploadFile(f, 'asset', job._grp); dir = p.substring(0, Math.max(p.lastIndexOf('/'), p.lastIndexOf('\\'))) || p; }
      job.assets = dir; card.querySelector('[data-f="assets"]').value = dir;
    }
  } catch (e) { alert('上传失败：' + e.message); }
}

function collectBatchJobs() {
  return S.batchJobs.map(j => {
    const o = {};
    const put = (k, v) => { if (v !== '' && v != null) o[k] = v; };
    put('name', (j.name || '').trim());
    if (j.platform) o.platform = j.platform;
    if (j.style) o.style = j.style;
    put('source', (j.source || '').trim());
    put('assets', (j.assets || '').trim());
    put('brief', (j.brief || '').trim());
    if (j.tts) o.tts = j.tts;
    put('voice', (j.voice || '').trim());
    put('bgm', (j.bgm || '').trim());
    put('duration', ('' + (j.duration || '')).trim());
    if (j.aspect) o.aspect = j.aspect;
    if (j.fit) o.fit = j.fit;
    if (j.subtitles === 'on') o.subtitles = true; else if (j.subtitles === 'off') o.subtitles = false;
    if (j.effects === 'on') o.effects = true; else if (j.effects === 'off') o.effects = false;
    if (j.sfx === 'on') o.sfx = true; else if (j.sfx === 'off') o.sfx = false;
    if (j.lower_thirds) o.lower_thirds = true;
    if (j.visual_source === 'ai_image') o.visual_source = 'ai_image';
    return o;
  });
}

async function batchValidate() {
  const jobs = collectBatchJobs();
  const { data } = await api('/api/batch/validate', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ jobs }) });
  const rows = (data.results || []).map((r, i) =>
    `<tr><td>${i + 1}. ${esc(r.name)}</td><td>${esc(r.platform || '-')}</td><td class="${r.valid?'ok':'bad'}">${r.valid?'✓ 合法':'✕ 非法'}</td><td class="bad">${esc((r.errors||[]).join('；'))}</td></tr>`).join('');
  h('#batchResult').innerHTML = `<table class="result-table"><tr><th>任务</th><th>平台</th><th>状态</th><th>错误</th></tr>${rows}</table>`;
}

async function batchRun() {
  const jobs = collectBatchJobs();
  const btn = h('#batchRunBtn'); btn.disabled = true;
  try {
    const { data } = await api('/api/batch/run', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ jobs }) });
    const payloads = data.jobs || [];
    const queued = payloads.filter(p => p.id).length;
    const rejected = payloads.filter(p => p.errors);
    let msg = `<div class="notice">已排队 <b>${queued}</b> 条任务，进度请到「单条生产」页右栏看板查看。`;
    if (rejected.length) msg += `<br>另有 <b>${rejected.length}</b> 条被拒（字段不完整）：` + rejected.map(r => esc((r.errors||[]).join('；'))).join(' ／ ');
    msg += '</div>';
    h('#batchResult').innerHTML = msg;
  } finally { btn.disabled = false; }
  refreshTasks();
}

// ----- 凭据 -----
function renderCredPage(meta) {
  const names = { OPENAI_API_KEY: 'OpenAI', ANTHROPIC_API_KEY: 'Anthropic', DEEPSEEK_API_KEY: 'DeepSeek', VOLC_TTS_APIKEY: '豆包 API Key（新版控制台·推荐）', VOLC_TTS_APPID: '豆包 AppID（旧版）', VOLC_TTS_TOKEN: '豆包 Token（旧版）', PEXELS_API_KEY: 'Pexels 素材下载', ARK_API_KEY: '豆包生图（方舟 API Key）' };
  const wrap = h('#credRows');
  wrap.innerHTML = Object.keys(meta.credentials).map(k =>
    `<div class="cred-row">
      <span class="dot ${meta.credentials[k]?'on':'off'}" id="cd_${k}"></span>
      <span class="name">${esc(names[k]||k)}</span>
      <input type="password" id="ci_${k}" placeholder="${meta.credentials[k]?'已设置（重填可覆盖）':'输入凭据值'}">
      <button class="btn copy-btn" onclick="saveCred('${k}')">保存</button>
    </div>`).join('');
  const sp = h('#stylePrompt');
  if (sp) sp.value = meta.image_style_prompt || '';
  const deps = { ffmpeg: 'ffmpeg — 缺失则无法拼装与转码', node: 'node — 缺失则特效渲染不可用', faster_whisper: 'faster-whisper — 缺失则本地字幕转写降级', edge_tts: 'edge-tts — 缺失则 edge 配音不可用' };
  h('#depList').innerHTML = Object.keys(meta.dependencies).map(k =>
    `<div class="dep-item"><span class="dot ${meta.dependencies[k]?'on':'off'}"></span><span>${esc(deps[k]||k)}</span></div>`).join('');
}

async function saveStylePrompt() {
  const value = h('#stylePrompt').value.trim();
  const { status, data } = await api('/api/settings', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name: 'IMAGE_STYLE_PROMPT', value }) });
  const msg = h('#stylePromptMsg');
  if (status === 200) {
    h('#stylePrompt').value = data.value || '';
    msg.textContent = value ? '已保存并生效' : '已恢复内置默认';
    S.meta.image_style_prompt = data.value;
  } else {
    msg.textContent = '保存失败：' + esc(data.error || status);
  }
  setTimeout(() => { msg.textContent = ''; }, 4000);
}

async function saveCred(name) {
  const val = h('#ci_' + name).value;
  if (!val) return;
  const { status } = await api('/api/credentials', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name, value: val }) });
  if (status === 200) {
    h('#cd_' + name).className = 'dot on';
    h('#ci_' + name).value = ''; h('#ci_' + name).placeholder = '已设置（重填可覆盖）';
    S.meta.credentials[name] = true; renderCredDots(S.meta.credentials); updateTtsWarn();
  }
}

// ----- bootstrap -----
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.tab)));
  h('#submitBtn').addEventListener('click', submitJob);
  document.querySelectorAll('#visualChips .chip').forEach(c => c.addEventListener('click', () => selectVisual(c.dataset.vis)));
  h('#clearBtn').addEventListener('click', () => { h('#jobForm').reset(); S.assets = []; S.assetGroup = null; h('#f_source').value=''; h('#f_assets').value=''; selectVisual('video'); });
  h('#f_bgmvol').addEventListener('input', () => h('#bgmVolVal').textContent = h('#f_bgmvol').value);
  h('#f_sfxvol').addEventListener('input', () => h('#sfxVolVal').textContent = h('#f_sfxvol').value);
  h('#batchValidateBtn').addEventListener('click', batchValidate);
  h('#batchRunBtn').addEventListener('click', batchRun);
  h('#batchAddBtn').addEventListener('click', () => addBatchJob());
  h('#stylePromptSave').addEventListener('click', saveStylePrompt);
  // 批量清单用事件委托：字段编辑只改数据不重渲染（不丢焦点/光标），增删复制才重渲染。
  // 严禁把用户数据插进内联 onclick（HTML 实体在进 JS 前已解码，esc() 防不住该上下文）。
  const bwrap = h('#batchJobs');
  bwrap.addEventListener('input', e => { if (e.target.dataset && e.target.dataset.f) batchFieldUpdate(e.target); });
  bwrap.addEventListener('change', e => {
    const t = e.target;
    if (t.dataset.f) batchFieldUpdate(t);
    if (t.dataset.up) batchUpload(t);
  });
  bwrap.addEventListener('click', e => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const i = Number(b.dataset.idx);
    if (b.dataset.act === 'dup') dupBatchJob(i);
    else if (b.dataset.act === 'del') removeBatchJob(i);
  });
  bindUploads();
  loadMeta();
  refreshTasks();
  setInterval(refreshTasks, 2000);
});
"""


def _body() -> str:
    return f"""
  <div class="topbar">
    <div class="brand"><h1>创作<span class="accent">工作台</span></h1><span class="tag">本地批量成片控制台</span></div>
    <div class="top-right">
      <a href="http://127.0.0.1:56080/" target="_blank">旧版工作台 ↗</a>
      <div class="cred-dots" id="credDots"></div>
    </div>
  </div>
  <div class="tabs">
    <div class="tab active" data-tab="single">单条生产</div>
    <div class="tab" data-tab="batch">批量生产</div>
    <div class="tab" data-tab="cred">凭据与依赖</div>
  </div>

  <div class="page active" id="page-single">
    <div class="layout">
      <form class="col-left" id="jobForm" onsubmit="return false">
        <div class="card">
          <h2>素材输入</h2>
          <p class="desc">先选视觉来源，再准备素材。</p>
          <label>视觉来源</label>
          <div class="chips" id="visualChips">
            <div class="chip active" data-vis="video">视频素材</div>
            <div class="chip" data-vis="ai_image">AI 生图（豆包）</div>
          </div>
          <label>原片 / 字幕 / 文本（source）</label>
          <div class="drop" id="sourceDrop"><input type="file" id="sourceFile"><span id="sourceDropLabel">拖入文件或点击上传</span></div>
          <input type="text" id="f_source" placeholder="或粘贴本机绝对路径" style="margin-top:8px">
          <div id="assetsBlock">
            <label>素材库（assets 目录）</label>
            <div class="drop" id="assetDrop"><input type="file" id="assetFile" multiple><span id="assetDropLabel">拖入多个素材或点击上传</span></div>
            <input type="text" id="f_assets" placeholder="或粘贴本机素材目录路径" style="margin-top:8px">
          </div>
          <div id="aiImageHint" class="notice" style="display:none">AI 生图模式：无需上传视频素材，系统按文案为每节自动生成配图并加运镜。需先在「凭据与依赖」页配置豆包生图 ARK Key；画风可在该页「生图风格」调整。</div>
        </div>

        <div class="card">
          <h2>平台预设</h2>
          <p class="desc">选平台自动联动画幅 / 填充 / 时长 / 字幕 / 特效，仍可在下方覆盖。</p>
          <div class="chips" id="platformChips"></div>
        </div>

        <div class="card">
          <h2>文案风格</h2>
          <div class="style-grid" id="styleGrid"></div>
        </div>

        <div class="card">
          <h2>配音与音乐</h2>
          <label>TTS 引擎 <span id="ttsWarn"></span></label>
          <select id="f_tts"></select>
          <label>音色（voice，留空用默认）</label>
          <input type="text" id="f_voice" placeholder="留空自动用引擎默认音色">
          <label>背景音乐（BGM 路径，可选）</label>
          <input type="text" id="f_bgm" placeholder="粘贴本机 BGM 路径">
          <label>BGM 音量</label>
          <div class="range-wrap"><input type="range" id="f_bgmvol" min="0.05" max="0.4" step="0.01" value="0.2"><span class="range-val" id="bgmVolVal">0.2</span></div>
        </div>

        <div class="card">
          <h2>高级</h2>
          <div class="row2">
            <div><label>时长（秒）</label><input type="number" id="f_duration" min="1" placeholder="90"></div>
            <div><label>任务名（可选）</label><input type="text" id="f_name" placeholder="留空自动命名"></div>
          </div>
          <label>创作简报（brief）</label>
          <textarea id="f_brief" placeholder="目标人群、重点、禁忌等"></textarea>
          <label>写稿 AI（文案改写用哪家大模型）</label>
          <select id="f_llm"></select>
          <div class="row2">
            <div><label>画幅（aspect）</label><select id="f_aspect"></select></div>
            <div><label>素材不合画幅时怎么放（fit）</label><select id="f_fit"></select></div>
          </div>
          <div style="margin-top:12px">
            <div class="switch-row"><span>字幕</span><label class="switch"><input type="checkbox" id="f_subtitles"><span class="slider"></span></label></div>
            <div class="switch-row"><span>特效</span><label class="switch"><input type="checkbox" id="f_effects" checked><span class="slider"></span></label></div>
            <div class="switch-row"><span>特效音（片头/章节卡/花字条音效）</span><label class="switch"><input type="checkbox" id="f_sfx" checked><span class="slider"></span></label></div>
            <div class="switch-row"><span>底部花字条（每节小标题横条）</span><label class="switch"><input type="checkbox" id="f_lower"><span class="slider"></span></label></div>
          </div>
          <label>特效音音量</label>
          <div class="range-wrap"><input type="range" id="f_sfxvol" min="0.1" max="0.8" step="0.05" value="0.35"><span class="range-val" id="sfxVolVal">0.35</span></div>
        </div>

        <div class="errbox" id="formErr"></div>
        <button type="button" class="btn btn-big" id="submitBtn">开始生成</button>
        <button type="button" class="btn btn-ghost btn-big" id="clearBtn">清空</button>
      </form>

      <div class="col-right">
        <div class="card"><h2>任务看板</h2><p class="desc">每 2 秒自动刷新，关闭页面不影响任务。</p></div>
        <div id="taskBoard"><div class="empty">还没有任务。</div></div>
      </div>
    </div>
  </div>

  <div class="page" id="page-batch">
    <div class="batch-wrap">
      <div class="card">
        <h2>批量生产</h2>
        <p class="desc">一次排队生产多条视频，每条就是一支独立成片（各自的素材、平台、风格）。逐条自动生产，
        某条失败不影响其余——适合一次备好几天的发布量。像填单条一样填每张卡片（可「复制」上一条只改素材），
        先「校验全部」确认无误，再「执行全部」；进度在「单条生产」页右栏看板查看。</p>
        <div id="batchJobs"></div>
        <div class="batch-actions">
          <button type="button" class="btn btn-ghost" id="batchAddBtn">＋ 添加一条任务</button>
        </div>
        <div class="batch-actions">
          <button type="button" class="btn" id="batchValidateBtn">校验全部</button>
          <button type="button" class="btn btn-ghost" id="batchRunBtn">执行全部</button>
        </div>
        <div id="batchResult"></div>
      </div>
    </div>
  </div>

  <div class="page" id="page-cred">
    <div class="cred-wrap">
      <div class="card">
        <h2>凭据</h2>
        <p class="desc">保存后只显示「已设置」，永不回显。凭据仅存于本次运行内存。</p>
        <div id="credRows"></div>
      </div>
      <div class="card">
        <h2>生图风格</h2>
        <p class="desc">AI 配图的统一画风提示词：自动追加到每条生图提示词之后，保证整个账号画风一致。
        默认为美式漫画插画风（参考爆款账号风格）；改完点保存立即生效并写入 settings.yaml。清空保存 = 恢复默认。</p>
        <textarea id="stylePrompt" style="min-height:96px" placeholder="加载中…"></textarea>
        <div class="batch-actions">
          <button type="button" class="btn" id="stylePromptSave">保存风格</button>
          <span id="stylePromptMsg" class="task-meta"></span>
        </div>
      </div>
      <div class="card">
        <h2>依赖状态</h2>
        <div class="dep-list" id="depList"></div>
      </div>
      <div class="notice">凭据保存后会写入项目根目录的 <b>credentials.yaml</b>，重启自动加载，<b>无需每次重填</b>；你也可以直接用文本编辑器改这个文件。（该文件含明文密钥，已加入 .gitignore，切勿提交或分享。）写稿 AI 三选一即可：DeepSeek 国内直连、注册即送额度，推荐没有海外账号的用户使用。豆包配音：新版控制台（快捷API接入）只需填「豆包 API Key」一项；旧版才需要 AppID+Token 两项。</div>
    </div>
  </div>
"""


def render_page() -> str:
    """返回完整 HTML 页面字符串（含内联 CSS/JS，零外部资源）。"""
    return (
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>创作工作台</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        f"{_body()}\n"
        f"<script>{_script()}</script>\n"
        "</body>\n</html>\n"
    )
