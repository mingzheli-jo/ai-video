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
  display: flex; flex-direction: column; align-items: flex-start;
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
.bgm-warn { color: var(--red); font-size: 12px; font-weight: 700; margin-top: 6px; }
#bgmPreview { width: 100%; margin-top: 8px; height: 34px; }
.covers { display: flex; gap: 10px; margin-top: 10px; align-items: flex-start; }
.cover-thumb { display: block; border-radius: 8px; border: 1px solid var(--line); background: #000; }
.cover-thumb.wide { width: 240px; }
.cover-thumb.tall { width: 100px; }
.btn-kit { margin-top: 10px; font-size: 12px; }
.kit-pre { white-space: pre-wrap; font-size: 12px; background: var(--panel-2); border: 1px solid var(--line); border-radius: 6px; padding: 10px; margin: 8px 0 0; }
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
.task-actions { margin-top: 10px; }
.btn-compare { font-size: 12px; padding: 7px 14px; }
.modal-overlay { position: fixed; inset: 0; z-index: 100; display: flex; align-items: center; justify-content: center; padding: 24px; background: rgba(0,0,0,.62); }
.modal-box { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); width: 100%; max-width: 920px; max-height: 86vh; display: flex; flex-direction: column; }
.modal-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 14px 18px; border-bottom: 1px solid var(--line); }
.modal-title { font-weight: 800; font-size: 15px; }
.modal-close { background: transparent; border: 0; color: var(--muted); font-size: 24px; line-height: 1; cursor: pointer; padding: 0 4px; }
.modal-close:hover { color: var(--text); }
.compare-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px 18px; overflow: hidden; }
@media (max-width: 760px) { .compare-grid { grid-template-columns: 1fr; } }
.compare-col { display: flex; flex-direction: column; min-width: 0; }
.compare-col h3 { margin: 0 0 8px; font-size: 13px; color: var(--gold); }
.compare-content { background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; padding: 12px 13px; max-height: 60vh; overflow-y: auto; font-size: 13px; line-height: 1.75; white-space: pre-wrap; word-break: break-word; color: var(--text); }
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
  renderStyles(data.styles);
  renderTtsProviders(data);
  renderAdvanced(data);
  renderAspectChips();  // 依赖 renderAdvanced 先填好 f_aspect/f_fit 选项
  renderCredPage(data);
  // meta 到位后再渲染批量清单：平台/风格/画幅等下拉需要 meta 填充选项。
  if (!S.batchJobs.length) addBatchJob(); else renderBatchJobs();
}

function renderCredDots(creds) {
  const wrap = h('#credDots'); if (!wrap) return;
  wrap.innerHTML = Object.keys(creds).map(k =>
    `<span class="dot ${creds[k] ? 'on' : 'off'}" title="${esc(k)}"></span>`).join('');
}

// 画幅二选（2026-07-16 用户定案：平台预设退役——实际只有竖/横两种格式，
// 字幕/特效等开关已全默认开，预设的其余联动价值归零）。
const ASPECT_CHIPS = [
  ['9:16', '9:16 竖屏', 'blur'],
  ['16:9', '16:9 横屏', 'pad'],
];

function renderAspectChips() {
  const wrap = h('#aspectChips');
  wrap.innerHTML = ASPECT_CHIPS.map(([v, label]) =>
    `<div class="chip" data-aspect="${esc(v)}">${esc(label)}</div>`).join('');
  wrap.querySelectorAll('.chip').forEach(c =>
    c.addEventListener('click', () => selectAspect(c.dataset.aspect)));
  selectAspect('9:16');  // 默认竖屏（主战场）
}

function selectAspect(value) {
  document.querySelectorAll('#aspectChips .chip').forEach(c =>
    c.classList.toggle('active', c.dataset.aspect === value));
  const spec = ASPECT_CHIPS.find(([v]) => v === value);
  h('#f_aspect').value = value;
  if (spec) h('#f_fit').value = spec[2];  // 竖屏配模糊填充，横屏配补边
}

function renderStyles(styles) {
  const wrap = h('#styleGrid');
  wrap.innerHTML = styles.map(s =>
    `<div class="style-card" data-style="${esc(s.key)}"><strong>${esc(s.label)}</strong><span>${esc(s.tts_hint)}</span>` +
    `<button type="button" class="btn btn-ghost btn-style-prompt" data-prompt-style="${esc(s.key)}" style="margin-top:8px;align-self:flex-start;font-size:11px;padding:2px 10px">提示词</button></div>`).join('');
  wrap.querySelectorAll('.style-card').forEach(c => c.addEventListener('click', () => {
    S.style = c.dataset.style;
    wrap.querySelectorAll('.style-card').forEach(x => x.classList.toggle('active', x === c));
  }));
  // 提示词按钮（2026-07-17 用户点名去黑盒）：查看该风格真实下发给 LLM 的完整提示词。
  // stopPropagation 防止点按钮误选中风格卡。
  wrap.querySelectorAll('.btn-style-prompt').forEach(b => b.addEventListener('click', (e) => {
    e.stopPropagation();
    openStylePrompt(b.dataset.promptStyle);
  }));
}

function openStylePrompt(key) {
  const s = (S.meta.styles || []).find(x => x.key === key);
  if (!s) return;
  h('#promptModalTitle').textContent = `「${s.label}」内置提示词（实际下发给写稿 AI 的完整 system prompt）`;
  h('#promptModalBody').textContent = s.prompt || '（该风格未下发提示词）';
  h('#promptModal').style.display = 'flex';
}
function closeStylePrompt() { h('#promptModal').style.display = 'none'; }

function renderTtsProviders(meta) {
  const sel = h('#f_tts');
  sel.innerHTML = meta.tts_providers.map(p => `<option value="${esc(p)}">${esc(p)}（默认 ${esc(meta.voice_defaults[p] || '')}）</option>`).join('');
  sel.addEventListener('change', updateTtsWarn);
  updateTtsWarn();
}

// BGM 试听（2026-07-16）：选了歌 → 隐藏红字警示、显示可拉进度条的播放器；
// 未选 → 红字常驻提醒"成片将没有 BGM"。音频经 /media 流式下发（已支持 Range 拖动）。
function updateBgmPreview() {
  const value = h('#f_bgm').value;
  const warn = h('#bgmWarn');
  const audio = h('#bgmPreview');
  if (value) {
    warn.style.display = 'none';
    const url = '/media?path=' + encodeURIComponent(value);
    if (audio.dataset.src !== url) { audio.src = url; audio.dataset.src = url; }
    audio.style.display = 'block';
  } else {
    warn.style.display = 'block';
    audio.pause();
    audio.style.display = 'none';
  }
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
  // BGM 下拉：项目根 music/ 目录下的音频（2026-07-16 用户定案，替代手填路径）。
  const bgmSel = h('#f_bgm');
  const musicFiles = meta.music_files || [];
  bgmSel.innerHTML = '<option value="">无背景音乐</option>' +
    musicFiles.map(f => `<option value="${esc(f.path)}">${esc(f.name)}</option>`).join('');
  if (!musicFiles.length) {
    bgmSel.innerHTML += '<option value="" disabled>（music/ 目录为空，放入 mp3/wav 后刷新）</option>';
  }
  bgmSel.addEventListener('change', updateBgmPreview);
  updateBgmPreview();
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
  // 平台预设已退役（2026-07-16）：画幅直接下发 aspect/fit，不再下发 platform。
  put('style', S.style);
  put('brief', h('#f_brief').value.trim());
  put('tts', h('#f_tts').value);
  put('voice', h('#f_voice').value.trim());
  put('voice_speed', h('#f_voice_speed').value.trim());  // 留空不下发 → 后端用默认语速
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
  f.ambient_particles = h('#f_ambient').checked;
  f.dual_aspect = h('#f_dual').checked;
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

const STAGE_LABELS = { rewrite: '文案改写', voice: '配音', image_gen: 'AI 生图', assemble: '素材拼装', effects: '特效', subtitles: '字幕', publish: '发布物料' };
const ALL_STAGES = ['rewrite', 'image_gen', 'assemble', 'effects', 'subtitles', 'publish'];

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
    // 双画幅（2026-07-16）：final 是主画幅成片，另一画幅从 outputs 的 _9x16/_16x9
    // 后缀键里找，两支并排回显、各带画幅角标与"打开文件夹"。
    const vids = [{ src: t.final, label: '' }];
    const o = t.outputs || {};
    [['_9x16', '9:16'], ['_16x9', '16:9']].forEach(([sfx, label]) => {
      const v = o['subtitled' + sfx] || o['effects' + sfx] || o['release' + sfx];
      if (v && v !== t.final) vids.push({ src: v, label });
    });
    vids.forEach(v => {
      if (v.label) body += `<div class="task-meta">画幅 ${esc(v.label)}</div>`;
      body += `<video controls src="/media?path=${encodeURIComponent(v.src)}"></video>`;
      body += `<div class="path-row"><code title="${esc(v.src)}">${esc(v.src)}</code><button class="btn copy-btn" data-open="${esc(v.src)}">打开文件夹</button></div>`;
    });
    const links = [];
    if (t.outputs.rewrite) links.push(`<a href="/media?path=${encodeURIComponent(t.outputs.rewrite)}" target="_blank">rewrite.json</a>`);
    if (t.outputs.assembly_plan) links.push(`<a href="/media?path=${encodeURIComponent(t.outputs.assembly_plan)}" target="_blank">assembly_plan.json</a>`);
    if (links.length) body += `<div class="links">${links.join('')}</div>`;
    // 发布物料（2026-07-16）：双封面缩略图（点开大图）+ 标题/简介/标签折叠面板。
    const covers = [];
    if (t.outputs.cover_16x9) covers.push(`<a href="/media?path=${encodeURIComponent(t.outputs.cover_16x9)}" target="_blank"><img class="cover-thumb wide" src="/media?path=${encodeURIComponent(t.outputs.cover_16x9)}" alt="封面 16:9"></a>`);
    if (t.outputs.cover_9x16) covers.push(`<a href="/media?path=${encodeURIComponent(t.outputs.cover_9x16)}" target="_blank"><img class="cover-thumb tall" src="/media?path=${encodeURIComponent(t.outputs.cover_9x16)}" alt="封面 9:16"></a>`);
    if (t.outputs.cover_3x4) covers.push(`<a href="/media?path=${encodeURIComponent(t.outputs.cover_3x4)}" target="_blank" title="抖音横版视频专用（主页作品位是竖向，16:9 会被裁边）"><img class="cover-thumb tall" src="/media?path=${encodeURIComponent(t.outputs.cover_3x4)}" alt="封面 3:4（抖音横版用）"></a>`);
    if (covers.length) body += `<div class="covers">${covers.join('')}</div>`;
    if (t.outputs.publish_kit) {
      body += `<div><button class="btn btn-ghost btn-kit" data-task="${esc(t.id)}" data-kit-path="${esc(t.outputs.publish_kit)}">发布物料（标题/简介/标签）</button>`
        + `<button class="btn btn-ghost copy-btn" data-open="${esc(t.outputs.publish_kit)}" style="margin-left:8px">打开发布文件夹</button>`
        + `<pre class="kit-pre" id="kit_${esc(t.id)}" style="display:none"></pre></div>`;
    }
  }
  const elapsed = t.elapsed_seconds ? `${t.elapsed_seconds.toFixed(1)}s` : '';
  // 文案对比：ok/failed 任务都给一个入口（原文案 vs AI 改写稿）。走事件委托 + data 属性，
  // 不用内联 onclick（HTML 实体进 JS 前已解码，esc() 防不住该上下文）。
  const canCompare = t.status === 'ok' || t.status === 'failed';
  const actions = canCompare
    ? `<div class="task-actions"><button class="btn btn-ghost btn-compare" data-compare="${esc(t.id)}">文案对比</button></div>`
    : '';
  return `<div class="task" data-task-id="${esc(t.id)}" data-sig="${esc(taskSig(t))}">
    <div class="task-head"><span class="task-name">${esc(t.name)}</span>
      <span>${t.platform ? `<span class="badge plat">${esc(t.platform)}</span> ` : ''}<span class="badge">${esc(statusLabel(t.status))}</span></span>
    </div>
    <div class="stage-chips">${chips}</div>
    <div class="task-meta">${esc(t.created_at)} ${elapsed ? '· ' + elapsed : ''}</div>
    ${body}
    ${actions}
  </div>`;
}

function statusLabel(s) { return {queued:'排队中',running:'生产中',ok:'完成',failed:'失败',invalid:'非法'}[s] || s; }

// 复制/文案对比按钮走事件委托 + data 属性：严禁把动态数据插进内联 onclick
// （HTML 实体在进 JS 解析前已被解码，esc() 防不住 JS 字符串上下文注入）。
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.copy-btn');
  if (btn && btn.dataset.copy) copyText(btn.dataset.copy);
  if (btn && btn.dataset.open) openFolder(btn.dataset.open);
  const cmp = e.target.closest('[data-compare]');
  if (cmp && cmp.dataset.compare) openCompare(cmp.dataset.compare);
  const kit = e.target.closest('.btn-kit');
  if (kit && kit.dataset.kitPath) toggleKit(kit.dataset.task, kit.dataset.kitPath);
});

// 打开成片所在文件夹（2026-07-16 用户点名替代复制路径）：服务端唤起资源管理器并选中文件。
async function openFolder(path) {
  try {
    const { status, data } = await api('/api/open-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    if (status !== 200) alert((data && data.error) || '打开文件夹失败');
  } catch (err) {
    alert('打开文件夹失败：' + err);
  }
}

// 发布物料折叠面板：优先读人类可读的 发布物料.txt（同目录），失败回落 kit JSON。
// 内容一律 textContent 注入（物料含任意字符，绝不进 innerHTML）。
async function toggleKit(taskId, kitPath) {
  const pre = document.getElementById('kit_' + taskId);
  if (!pre) return;
  if (pre.style.display !== 'none') { pre.style.display = 'none'; return; }
  pre.style.display = 'block';
  pre.textContent = '加载中…';
  const txtPath = kitPath.replace(/publish_kit\.json$/, '发布物料.txt');
  try {
    pre.textContent = await fetchMediaText(txtPath);
  } catch (err) {
    try { pre.textContent = await fetchMediaText(kitPath); }
    catch (err2) { pre.textContent = '发布物料读取失败。'; }
  }
}

// ----- 文案对比弹窗 -----
// 数据流：/api/jobs/<id> → outputs.rewrite（rewrite.json 绝对路径）；把末段
// rewrite.json 换成 source_transcript.txt 推出原文案路径；两文件都经 /media?path= 拉取。
// 内容一律用 textContent 注入（原文案/改写稿可能含任意字符，textContent 从不解析 HTML，
// 比 esc()+innerHTML 更硬），拉不到则显示中文兜底提示。
function showCompareModal() {
  h('#compareSource').textContent = '加载中…';
  h('#compareRewrite').textContent = '加载中…';
  h('#compareModal').style.display = 'flex';
}
function closeCompare() { h('#compareModal').style.display = 'none'; }

async function fetchMediaText(path) {
  const r = await fetch('/media?path=' + encodeURIComponent(path));
  if (!r.ok) throw new Error('读取失败');
  return r.text();
}

async function openCompare(taskId) {
  showCompareModal();
  let detail = {};
  try { const { data } = await api('/api/jobs/' + encodeURIComponent(taskId)); detail = data || {}; } catch (e) {}
  const rewritePath = detail.outputs && detail.outputs.rewrite;
  if (!rewritePath) {
    h('#compareSource').textContent = '本任务未记录原文案（旧版本生成）';
    h('#compareRewrite').textContent = '未找到 rewrite.json（该任务未完成文案改写）。';
    return;
  }
  const sourcePath = String(rewritePath).replace(/rewrite\.json$/, 'source_transcript.txt');
  fetchMediaText(sourcePath)
    .then(txt => { h('#compareSource').textContent = txt.trim() || '本任务未记录原文案（旧版本生成）'; })
    .catch(() => { h('#compareSource').textContent = '本任务未记录原文案（旧版本生成）'; });
  fetchMediaText(rewritePath)
    .then(txt => {
      let vo = '';
      try { vo = String(JSON.parse(txt).full_voiceover || '').trim(); } catch (e) {}
      h('#compareRewrite').textContent = vo || '未能解析 rewrite.json 的 full_voiceover 字段。';
    })
    .catch(() => { h('#compareRewrite').textContent = '未能读取 rewrite.json。'; });
}

// 任务卡签名：只含"会变化"的字段。已完成(ok)任务这些字段全稳定 → 签名不变 → 键控
// 更新时该卡永不重建，其内嵌 <video> 得以在每 2s 刷新中存活（2026-07-15 修复"点播放
// 播 2 秒就被刷新重建、归零循环"——根因是旧实现每次刷新整体重写 innerHTML 销毁了视频）。
function taskSig(t) {
  return [t.status, t.current_stage || '', (t.stages_done || []).join('.'),
          t.stage_failed || '', t.error || '', t.final || '',
          Object.keys(t.outputs || {}).length,  // 双画幅第二支成片落盘时触发重渲
          (t.elapsed_seconds || 0)].join('|');
}

async function refreshTasks() {
  const { data } = await api('/api/jobs');
  const list = data.jobs || [];
  const wrap = h('#taskBoard');
  const emptyEl = wrap.querySelector('.empty');
  if (!list.length) {
    if (!emptyEl) wrap.innerHTML = '<div class="empty">还没有任务。填好左侧表单点「开始生成」。</div>';
    return;
  }
  if (emptyEl) emptyEl.remove();
  // 现有卡片按 task id 索引
  const existing = {};
  wrap.querySelectorAll('.task[data-task-id]').forEach(el => { existing[el.dataset.taskId] = el; });
  const seen = {};
  let prev = null;  // 按 list 顺序摆放；移动 DOM 节点不会重置正在播放的 <video>
  list.forEach(t => {
    seen[t.id] = true;
    let el = existing[t.id];
    if (!el || el.dataset.sig !== taskSig(t)) {
      // 新卡或签名变了才重建这一张（完成态签名稳定，永不走到这里 → 视频存活）
      const tmp = document.createElement('div');
      tmp.innerHTML = renderTask(t);
      const fresh = tmp.firstElementChild;
      if (el && el.parentNode) el.parentNode.replaceChild(fresh, el);
      el = fresh;
    }
    if (prev) {
      if (prev.nextElementSibling !== el) wrap.insertBefore(el, prev.nextElementSibling);
    } else if (wrap.firstElementChild !== el) {
      wrap.insertBefore(el, wrap.firstElementChild);
    }
    prev = el;
  });
  // 删除已消失的任务卡
  Object.keys(existing).forEach(id => { if (!seen[id]) existing[id].remove(); });
}

function copyText(txt) { navigator.clipboard && navigator.clipboard.writeText(txt); }

// ----- 批量：表单化任务清单（不再手写 JSON）-----
// 三态开关：空=默认（后端全默认开，2026-07-16 定案）、on=开、off=关。
// 平台预设已退役（2026-07-17 清理历史残留）：批量行与单任务表单口径统一。
const BATCH_TOGGLE_OPTS = [['', '默认（开）'], ['on', '开'], ['off', '关']];
// 画幅二选（与单任务表单同款）：值 → 自动联动的填充方式。
const BATCH_ASPECT_OPTS = [['9:16', '9:16 竖屏'], ['16:9', '16:9 横屏']];

function newGroup() { return 'bg' + Date.now() + Math.random().toString(36).slice(2, 6); }

const VISUAL_OPTS = [['video', '视频素材'], ['ai_image', 'AI 生图（豆包）']];

function blankBatchJob() {
  const tts = (S.meta && S.meta.tts_providers && S.meta.tts_providers[0]) || 'doubao';
  return { name: '', style: '', source: '', assets: '', brief: '', tts,
    voice: '', bgm: '', duration: '', aspect: '9:16', dual_aspect: false,
    subtitles: '', effects: '', sfx: '', ambient: '',
    lower_thirds: false, visual_source: 'video', _grp: newGroup() };
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
  const styles = meta.styles || [];
  const tts = meta.tts_providers || [];
  const musicFiles = meta.music_files || [];
  const bgmOptions = (sel) => '<option value="">无背景音乐</option>' +
    musicFiles.map(f => `<option value="${esc(f.path)}"${f.path === sel ? ' selected' : ''}>${esc(f.name)}</option>`).join('');
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
        <div><label>画幅</label><select data-idx="${i}" data-f="aspect">${optionsHtml(BATCH_ASPECT_OPTS, j.aspect || '9:16')}</select></div>
        <div><label>文案风格</label><select data-idx="${i}" data-f="style"><option value="">不指定</option>${styles.map(s => `<option value="${esc(s.key)}"${s.key === j.style ? ' selected' : ''}>${esc(s.label)}</option>`).join('')}</select></div>
        <div><label>配音引擎</label><select data-idx="${i}" data-f="tts">${tts.map(p => `<option value="${esc(p)}"${p === j.tts ? ' selected' : ''}>${esc(p)}</option>`).join('')}</select></div>
      </div>
      <div class="switch-row" style="margin-top:8px"><span>双画幅都出（9:16 + 16:9 各自原生配图，耗时/生图费约 ×2）</span><label class="switch"><input type="checkbox" data-idx="${i}" data-f="dual_aspect"${j.dual_aspect ? ' checked' : ''}><span class="slider"></span></label></div>
      <label>视觉来源</label>
      <select data-idx="${i}" data-f="visual_source">${VISUAL_OPTS.map(([v, l]) => `<option value="${v}"${v === (j.visual_source || 'video') ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>
      <label>原片 / 字幕 / 文本（source）</label>
      <div class="bj-file"><input type="text" data-idx="${i}" data-f="source" value="${esc(j.source)}" placeholder="上传，或粘贴本机绝对路径"><label class="bj-up">上传<input type="file" data-idx="${i}" data-up="source"></label></div>
      <label>素材库（assets 目录，AI 生图时可留空）</label>
      <div class="bj-file"><input type="text" data-idx="${i}" data-f="assets" value="${esc(j.assets)}" placeholder="上传多个素材，或粘贴本机目录路径"><label class="bj-up">上传<input type="file" data-idx="${i}" data-up="assets" multiple></label></div>
      <label>创作简报（brief，可选）</label>
      <textarea data-idx="${i}" data-f="brief" placeholder="目标人群、重点、禁忌等">${esc(j.brief)}</textarea>
      <details class="bj-adv">
        <summary>高级：时长 / 音色 / BGM / 字幕特效开关（留空即默认，与单任务口径一致）</summary>
        <div class="bj-grid">
          <div><label>时长（秒）</label><input type="number" min="1" data-idx="${i}" data-f="duration" value="${esc(j.duration)}" placeholder="默认 90"></div>
          <div><label>音色（voice）</label><input type="text" data-idx="${i}" data-f="voice" value="${esc(j.voice)}" placeholder="默认音色"></div>
          <div><label>字幕</label><select data-idx="${i}" data-f="subtitles">${optionsHtml(BATCH_TOGGLE_OPTS, j.subtitles)}</select></div>
          <div><label>特效</label><select data-idx="${i}" data-f="effects">${optionsHtml(BATCH_TOGGLE_OPTS, j.effects)}</select></div>
          <div><label>特效音</label><select data-idx="${i}" data-f="sfx">${optionsHtml(BATCH_TOGGLE_OPTS, j.sfx)}</select></div>
          <div><label>氛围粒子</label><select data-idx="${i}" data-f="ambient">${optionsHtml(BATCH_TOGGLE_OPTS, j.ambient)}</select></div>
        </div>
        <label>背景音乐（music/ 目录下拉选择）</label>
        <select data-idx="${i}" data-f="bgm">${bgmOptions(j.bgm)}</select>
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
    if (j.style) o.style = j.style;
    put('source', (j.source || '').trim());
    put('assets', (j.assets || '').trim());
    put('brief', (j.brief || '').trim());
    if (j.tts) o.tts = j.tts;
    put('voice', (j.voice || '').trim());
    put('bgm', (j.bgm || '').trim());
    put('duration', ('' + (j.duration || '')).trim());
    // 画幅二选 + 自动联动填充（与单任务表单同款：竖屏模糊填充、横屏补边）
    const aspect = j.aspect || '9:16';
    o.aspect = aspect;
    o.fit = aspect === '9:16' ? 'blur' : 'pad';
    if (j.dual_aspect) o.dual_aspect = true;
    if (j.subtitles === 'on') o.subtitles = true; else if (j.subtitles === 'off') o.subtitles = false;
    if (j.effects === 'on') o.effects = true; else if (j.effects === 'off') o.effects = false;
    if (j.sfx === 'on') o.sfx = true; else if (j.sfx === 'off') o.sfx = false;
    if (j.ambient === 'on') o.ambient_particles = true; else if (j.ambient === 'off') o.ambient_particles = false;
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
  const rp = h('#rewritePrompt');
  if (rp) rp.value = meta.rewrite_style_prompt || '';
  const sfp = h('#subFontSizeP');
  if (sfp) sfp.value = (meta.subtitle_font_size_portrait != null ? meta.subtitle_font_size_portrait : '');
  const sfl = h('#subFontSizeL');
  if (sfl) sfl.value = (meta.subtitle_font_size_landscape != null ? meta.subtitle_font_size_landscape : '');
  const sfn = h('#subFontName');
  if (sfn && meta.subtitle_font_options) {
    sfn.innerHTML = meta.subtitle_font_options.map(f => `<option value="${esc(f)}">${esc(f)}</option>`).join('');
    sfn.value = meta.subtitle_font_name || '';
  }
  syncSubPreview();  // meta 回填后立即按当前字体/字号刷新预览
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

async function saveRewritePrompt() {
  const value = h('#rewritePrompt').value.trim();
  const { status, data } = await api('/api/settings', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name: 'REWRITE_STYLE_PROMPT', value }) });
  const msg = h('#rewritePromptMsg');
  if (status === 200) {
    h('#rewritePrompt').value = data.value || '';
    msg.textContent = value ? '已保存并生效' : '已清空（只用内置内容类型模板）';
    S.meta.rewrite_style_prompt = data.value;
  } else {
    msg.textContent = '保存失败：' + esc(data.error || status);
  }
  setTimeout(() => { msg.textContent = ''; }, 4000);
}

function syncSubPreview() {
  // 字体/字号所见即所得（2026-07-15 用户点名：盲选字体再生成成片成本太高）。
  const pv = h('#subFontPreview');
  if (!pv) return;
  const fam = (h('#subFontName') && h('#subFontName').value) || 'Microsoft YaHei';
  // 预览跟竖屏字号（主战场画幅）。
  const raw = parseFloat(h('#subFontSizeP') && h('#subFontSizeP').value);
  const scale = isNaN(raw) ? 1.0 : Math.min(3, Math.max(0.7, raw));
  pv.style.fontFamily = '"' + fam + '", "Microsoft YaHei", sans-serif';
  pv.style.fontSize = Math.round(24 * scale) + 'px';
}

async function saveSubtitleStyle() {
  const sizeP = h('#subFontSizeP').value.trim();
  const sizeL = h('#subFontSizeL').value.trim();
  const name = h('#subFontName').value;
  const msg = h('#subStyleMsg');
  // 端点一次一项：竖屏字号、横屏字号、字体分三次保存（都走白名单校验）。
  const post = (n, v) => api('/api/settings', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name: n, value: v }) });
  const r1 = await post('SUBTITLE_FONT_SIZE_PORTRAIT', sizeP);
  const r2 = await post('SUBTITLE_FONT_SIZE_LANDSCAPE', sizeL);
  const r3 = await post('SUBTITLE_FONT_NAME', name);
  if (r1.status === 200 && r2.status === 200 && r3.status === 200) {
    h('#subFontSizeP').value = r1.data.value || '';
    h('#subFontSizeL').value = r2.data.value || '';
    h('#subFontName').value = r3.data.value || '';
    S.meta.subtitle_font_size_portrait = r1.data.value;
    S.meta.subtitle_font_size_landscape = r2.data.value;
    S.meta.subtitle_font_name = r3.data.value;
    msg.textContent = '已保存并生效';
  } else {
    const err = (r1.data && r1.data.error) || (r2.data && r2.data.error) || (r3.data && r3.data.error) || (r1.status + '/' + r2.status + '/' + r3.status);
    msg.textContent = '保存失败：' + esc(err);
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
  h('#rewritePromptSave').addEventListener('click', saveRewritePrompt);
  h('#subStyleSave').addEventListener('click', saveSubtitleStyle);
  h('#subFontName').addEventListener('change', syncSubPreview);
  h('#subFontSizeP').addEventListener('input', syncSubPreview);
  // 文案对比弹窗：点 × 或遮罩空白处关闭（点框体内部不关）。
  h('#compareClose').addEventListener('click', closeCompare);
  h('#compareModal').addEventListener('click', e => { if (e.target === h('#compareModal')) closeCompare(); });
  h('#promptClose').addEventListener('click', closeStylePrompt);
  h('#promptModal').addEventListener('click', e => { if (e.target === h('#promptModal')) closeStylePrompt(); });
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
          <h2>画幅</h2>
          <p class="desc">竖屏 9:16（抖音/快手/视频号/小红书）或横屏 16:9（B站/西瓜），自动联动填充方式。</p>
          <div class="chips" id="aspectChips"></div>
          <div class="switch-row" style="margin-top:10px"><span>双画幅都出（9:16 + 16:9 各自原生配图，耗时/生图费约 ×2）</span><label class="switch"><input type="checkbox" id="f_dual"><span class="slider"></span></label></div>
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
          <label>配音语速</label>
          <input type="number" id="f_voice_speed" min="0.5" max="2.0" step="0.1" placeholder="留空=默认 1.1×">
          <p class="desc" style="margin-top:6px">1.0=原速，0.5~2.0；改语速后分镜/字幕自动跟随。</p>
          <label>背景音乐（music/ 目录下拉选择）</label>
          <select id="f_bgm"><option value="">无背景音乐</option></select>
          <div id="bgmWarn" class="bgm-warn">⚠ 尚未选择背景音乐，成片将没有 BGM</div>
          <audio id="bgmPreview" controls preload="none" style="display:none"></audio>
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
            <div class="switch-row"><span>字幕</span><label class="switch"><input type="checkbox" id="f_subtitles" checked><span class="slider"></span></label></div>
            <div class="switch-row"><span>特效</span><label class="switch"><input type="checkbox" id="f_effects" checked><span class="slider"></span></label></div>
            <div class="switch-row"><span>特效音（片头/章节卡/花字条音效）</span><label class="switch"><input type="checkbox" id="f_sfx" checked><span class="slider"></span></label></div>
            <div class="switch-row"><span>氛围粒子（全片金色微尘上浮）</span><label class="switch"><input type="checkbox" id="f_ambient" checked><span class="slider"></span></label></div>
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
        <h2>改写文风指令</h2>
        <p class="desc">DeepSeek 文案改写的全局自定义指令：口吻、句式、禁忌词、结尾习惯等，追加到内置内容类型模板之后（冲突时以此为准）。
        留空 = 只用内置七种内容类型模板；改完点保存立即生效并写入 settings.yaml。示例："多用短句和反问，别用'家人们''宝子'这类称呼，每节结尾留一个悬念"。</p>
        <textarea id="rewritePrompt" style="min-height:96px" placeholder="留空 = 内置模板默认文风"></textarea>
        <div class="batch-actions">
          <button type="button" class="btn" id="rewritePromptSave">保存指令</button>
          <span id="rewritePromptMsg" class="task-meta"></span>
        </div>
      </div>
      <div class="card">
        <h2>字幕样式</h2>
        <p class="desc">烧录字幕的字号与字体族。字号为缩放系数（0.7~3.0，留空 = 默认 1.0），字体从下拉白名单选择。
        默认即对标爆款博主样式：<b>粗体白字 + 厚黑描边</b>；改完点保存立即生效并写入 settings.yaml。</p>
        <div class="batch-actions" style="align-items:flex-end;flex-wrap:wrap;gap:14px">
          <label style="display:flex;flex-direction:column;gap:6px">竖屏字号系数（9:16）
            <input type="number" id="subFontSizeP" min="0.7" max="3" step="0.05" placeholder="1.0（默认）" style="width:7em"></label>
          <label style="display:flex;flex-direction:column;gap:6px">横屏字号系数（16:9）
            <input type="number" id="subFontSizeL" min="0.7" max="3" step="0.05" placeholder="1.0（默认）" style="width:7em"></label>
          <label style="display:flex;flex-direction:column;gap:6px">字体
            <select id="subFontName"></select></label>
          <button type="button" class="btn" id="subStyleSave">保存样式</button>
          <span id="subStyleMsg" class="task-meta"></span>
        </div>
        <div id="subFontPreview" style="margin-top:14px;padding:12px 18px;background:#101014;border-radius:10px;color:#ffffff;font-weight:700;-webkit-text-stroke:1.2px #000;paint-order:stroke fill;text-shadow:0 2px 8px rgba(0,0,0,.7);font-size:24px;line-height:1.5">真正困住你的，从来不是事情本身 0123</div>
        <p class="desc" style="margin-top:8px">预览随上方选择实时变化（本机字体渲染：未安装的字体显示为系统默认，最终以成片为准）。</p>
      </div>
      <div class="card">
        <h2>依赖状态</h2>
        <div class="dep-list" id="depList"></div>
      </div>
      <div class="notice">凭据保存后会写入项目根目录的 <b>credentials.yaml</b>，重启自动加载，<b>无需每次重填</b>；你也可以直接用文本编辑器改这个文件。（该文件含明文密钥，已加入 .gitignore，切勿提交或分享。）写稿 AI 三选一即可：DeepSeek 国内直连、注册即送额度，推荐没有海外账号的用户使用。豆包配音：新版控制台（快捷API接入）只需填「豆包 API Key」一项；旧版才需要 AppID+Token 两项。</div>
    </div>
  </div>

  <div class="modal-overlay" id="compareModal" style="display:none">
    <div class="modal-box">
      <div class="modal-head">
        <span class="modal-title">文案对比</span>
        <button type="button" class="modal-close" id="compareClose" aria-label="关闭">×</button>
      </div>
      <div class="compare-grid">
        <div class="compare-col"><h3>原视频文案</h3><div class="compare-content" id="compareSource"></div></div>
        <div class="compare-col"><h3>AI 改写稿</h3><div class="compare-content" id="compareRewrite"></div></div>
      </div>
    </div>
  </div>

  <div class="modal-overlay" id="promptModal" style="display:none">
    <div class="modal-box">
      <div class="modal-head">
        <span class="modal-title" id="promptModalTitle">内置提示词</span>
        <button type="button" class="modal-close" id="promptClose" aria-label="关闭">×</button>
      </div>
      <pre id="promptModalBody" style="white-space:pre-wrap;max-height:64vh;overflow:auto;padding:14px 16px;background:#101014;border-radius:10px;font-size:12.5px;line-height:1.8"></pre>
      <p class="desc" style="margin-top:8px">想统一调整所有风格的口吻？去「设置」页的「改写文风指令」加一条全局指令（会追加在上面提示词末尾，冲突时以你的为准）。</p>
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
