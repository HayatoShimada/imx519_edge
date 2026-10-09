// 撮影アプリ（スマホのブラウザ向け）。API は同じオリジンの /api/*
const $ = (id) => document.getElementById(id);
const STATE_LABELS = { starting: '準備中', idle: '待機中', moving: '移動中', capturing: '撮影中', error: 'エラー' };
const KEY_PRODUCT = 'imx519.product';

let options = null;
let product = null;
let status = null;
let job = null;

function store(key, value) {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {}
}
function restore(key) {
  try { return JSON.parse(localStorage.getItem(key)); } catch { return null; }
}

let toastTimer;
function toast(message, error = false) {
  const el = $('toast');
  el.textContent = message;
  el.className = error ? 'toast error' : 'toast';
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), error ? 6000 : 3000);
}

// --- ログ（サーバーの出来事と、この画面の出来事） ---

const LOG_MAX = 200;
function addLog(entry, client = false) {
  const li = document.createElement('li');
  li.className = `${entry.level === 'error' ? 'error' : ''} ${client ? 'client' : ''}`;
  const t = new Date((entry.t ?? Date.now() / 1000) * 1000);
  const time = document.createElement('time');
  time.textContent = t.toLocaleTimeString('ja-JP', { hour12: false });
  li.append(time, (client ? '画面: ' : '') + entry.message);
  const box = $('log');
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
  box.append(li);
  while (box.children.length > LOG_MAX) box.firstChild.remove();
  if (atBottom) box.scrollTop = box.scrollHeight;
}
const clientLog = (message, level = 'info') => addLog({ level, message }, true);

// --- 通信状態 ---

function setComm(id, level, text) {
  const el = $(id);
  el.className = `comm-item ${level}`;
  el.querySelector('b').textContent = text;
}

const comm = { eventsAt: 0, eventsOpen: false };

async function pingApi() {
  const start = performance.now();
  try {
    const res = await fetch('/api/ping', { cache: 'no-store' });
    if (!res.ok) throw new Error(res.status);
    const ms = Math.round(performance.now() - start);
    setComm('comm-api', ms < 300 ? 'ok' : 'warn', `${ms} ms`);
    if (comm.apiDown) { comm.apiDown = false; clientLog(`API に戻りました（${ms} ms）`); }
  } catch (e) {
    setComm('comm-api', 'bad', '応答なし');
    if (!comm.apiDown) { comm.apiDown = true; clientLog(`API が応答しません: ${e.message}`, 'error'); }
  }
}

async function checkCms() {
  try {
    const h = await (await fetch('/api/health', { cache: 'no-store' })).json();
    setComm('comm-cms', h.cms.ok ? 'ok' : 'bad', h.cms.ok ? `${h.cms.ms} ms` : 'NG');
    $('comm-cms').title = h.cms.ok ? `85pi から CMS に届きます（${h.cms.role} として）` : h.cms.error;
    if (!h.cms.ok && !comm.cmsDown) clientLog(`CMS に届きません: ${h.cms.error}`, 'error');
    comm.cmsDown = !h.cms.ok;
  } catch {
    setComm('comm-cms', 'bad', '不明');
  }
}

function watchEvents() {
  // 通知（SSE）はサーバーが 5 秒ごとに何か送る。12 秒来なければ止まっているとみなす
  const age = (Date.now() - comm.eventsAt) / 1000;
  if (!comm.eventsOpen) setComm('comm-events', 'bad', '再接続中');
  else if (age > 12) setComm('comm-events', 'warn', `${Math.round(age)} 秒無音`);
  else setComm('comm-events', 'ok', '接続');
}

// --- ライブビュー（MJPEG を自分で読み、フレーム数と通信量を測る） ---

const live = { abort: null, frames: [], bytes: [], lastAt: 0, url: null };

function indexOf(buf, pattern, from = 0) {
  outer: for (let i = from; i <= buf.length - pattern.length; i++) {
    for (let j = 0; j < pattern.length; j++) if (buf[i + j] !== pattern[j]) continue outer;
    return i;
  }
  return -1;
}
const CRLF2 = new TextEncoder().encode('\r\n\r\n');

async function startLive() {
  stopLive(false);
  const controller = new AbortController();
  live.abort = controller;
  $('live-toggle').textContent = 'ライブビューを切断';
  $('live-idle').textContent = '接続中…';
  $('live-idle').hidden = false;
  setComm('comm-live', 'warn', '接続中');
  clientLog('ライブビューに接続します');
  try {
    const res = await fetch('/api/preview.mjpg', { signal: controller.signal, cache: 'no-store' });
    if (!res.ok) throw new Error(`${res.status}`);
    const reader = res.body.getReader();
    let buf = new Uint8Array(0);
    for (;;) {
      const { value, done } = await reader.read();
      if (done) throw new Error('サーバーが切断しました');
      const next = new Uint8Array(buf.length + value.length);
      next.set(buf);
      next.set(value, buf.length);
      buf = next;
      // --frame\r\nContent-Type: image/jpeg\r\nContent-Length: N\r\n\r\n<JPEG>\r\n
      for (;;) {
        const headEnd = indexOf(buf, CRLF2);
        if (headEnd < 0) break;
        const head = new TextDecoder().decode(buf.subarray(0, headEnd));
        const length = Number(/Content-Length: (\d+)/i.exec(head)?.[1]);
        const start = headEnd + 4;
        if (!length || buf.length < start + length) break;
        showFrame(buf.slice(start, start + length));
        buf = buf.slice(start + length + 2);
      }
    }
  } catch (e) {
    if (controller.signal.aborted) return;
    clientLog(`ライブビューが切れました: ${e.message}`, 'error');
    stopLive(false);
    setComm('comm-live', 'bad', '切断');
  }
}

function showFrame(jpeg) {
  const now = performance.now();
  live.frames.push(now);
  live.bytes.push([now, jpeg.length]);
  live.lastAt = now;
  const url = URL.createObjectURL(new Blob([jpeg], { type: 'image/jpeg' }));
  $('live').src = url;
  if (live.url) URL.revokeObjectURL(live.url);
  live.url = url;
  $('live-idle').hidden = true;
}

function stopLive(log = true) {
  if (live.abort) {
    live.abort.abort();
    live.abort = null;
    if (log) clientLog('ライブビューを切断しました');
  }
  live.frames = [];
  live.bytes = [];
  $('live-toggle').textContent = 'ライブビューに接続';
  $('live-idle').textContent = 'ライブビューは未接続です';
  $('live-idle').hidden = false;
  $('live').removeAttribute('src');
  if (live.url) URL.revokeObjectURL(live.url);
  live.url = null;
  $('live-stats').textContent = '';
  setComm('comm-live', '', '未接続');
}

function watchLive() {
  if (!live.abort) return;
  const now = performance.now();
  live.frames = live.frames.filter((t) => now - t < 3000);
  live.bytes = live.bytes.filter(([t]) => now - t < 3000);
  const fps = live.frames.length / 3;
  const kbps = (live.bytes.reduce((a, [, n]) => a + n, 0) * 8) / 3 / 1000;
  const age = live.lastAt ? (now - live.lastAt) / 1000 : null;
  $('live-stats').textContent = `${fps.toFixed(1)} fps・${Math.round(kbps)} kbps` + (age != null ? `・${age.toFixed(1)} 秒前` : '');
  const capturing = status?.state === 'capturing';
  if (age == null || age > 3) setComm('comm-live', capturing ? 'warn' : 'bad', capturing ? '撮影中は停止' : '受信なし');
  else setComm('comm-live', fps >= 3 ? 'ok' : 'warn', `${fps.toFixed(1)} fps`);
}

async function api(path, init = {}) {
  const res = await fetch(path, {
    ...init,
    headers: init.body ? { 'Content-Type': 'application/json' } : undefined,
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(body?.detail ?? `${res.status}`);
  return body;
}
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body ?? {}) });

// --- 状態 ---

function renderStatus() {
  if (!status) return;
  const el = $('state');
  el.textContent = STATE_LABELS[status.state] ?? status.state;
  el.className = 'chip ' + ({ idle: 'ok', capturing: 'busy', moving: 'busy', error: 'bad' }[status.state] ?? '');
  el.title = status.error ?? '';
  $('disk').textContent = `空き ${status.disk.free_gb} GB`;
  $('disk').className = 'chip ' + (status.disk.free_gb < status.disk.reserve_gb + 2 ? 'bad' : 'muted');

  const locked = status.locked;
  $('locked').textContent = locked
    ? `固定中: レンズ ${locked.lens_position.toFixed(2)} / 露光 ${Math.round(locked.base_exposure_us)} µs / WB ${locked.colour_gains.map((g) => g.toFixed(2)).join(', ')}`
    : '自動（AE / AWB / AF）。撮影の最初に測光して固定します。';
  if (locked && document.activeElement !== $('lens')) {
    $('lens').value = locked.lens_position;
    $('lens-value').textContent = Number(locked.lens_position).toFixed(2);
    if (document.activeElement !== $('exposure')) $('exposure').value = Math.round(locked.base_exposure_us);
  }

  const pt = status.pantilt;
  $('pantilt-on').hidden = !pt.available;
  $('pantilt-off').hidden = pt.available;
  $('pan').textContent = pt.pan ?? '—';
  $('tilt').textContent = pt.tilt ?? '—';

  if (status.job) renderJob(status.job);
  const d = status.defaults;
  if (!$('ev').value) $('ev').value = d.ev.join(' ');
  if (!$('frames').value) $('frames').value = d.frames;
  if (!$('positions').value) $('positions').value = d.positions;
}

function renderJob(j) {
  job = j;
  const running = j.status === 'queued' || j.status === 'running';
  $('progress').hidden = !running && j.status !== 'done';
  $('progress-fill').style.width = `${Math.round((100 * j.done) / j.total)}%`;
  const label = { queued: '待機', running: '撮影中', done: '完了', failed: '失敗', cancelled: '中止' }[j.status];
  $('progress-text').textContent = `${label} ${j.done} / ${j.total} 枚 — ${j.session_id}${j.error ? `（${j.error}）` : ''}`;
  $('shoot').hidden = running;
  $('cancel').hidden = !running;
  $('live-note').hidden = !running;
  $('live-note').textContent = running ? '撮影中はライブビューを止めています' : '';
}

function connectEvents() {
  const events = new EventSource('/api/events');
  const seen = () => (comm.eventsAt = Date.now());
  events.onopen = () => {
    if (!comm.eventsOpen) clientLog('通知に接続しました');
    comm.eventsOpen = true;
    seen();
    watchEvents();
  };
  events.addEventListener('ping', seen);
  events.addEventListener('log', (e) => { seen(); addLog(JSON.parse(e.data)); });
  events.addEventListener('status', (e) => { seen(); status = JSON.parse(e.data); renderStatus(); });
  events.addEventListener('job', (e) => {
    seen();
    const j = JSON.parse(e.data);
    const was = job?.status;
    renderJob(j);
    if (j.status !== was && ['done', 'failed', 'cancelled'].includes(j.status)) {
      if (j.status === 'done') toast(`撮影が終わりました（${j.session_id}）`);
      else if (j.status === 'failed') toast(`撮影に失敗しました: ${j.error}`, true);
      loadSessions();
    }
  });
  events.onerror = () => {
    if (comm.eventsOpen) clientLog('通知が切れました。再接続します', 'error');
    comm.eventsOpen = false;
    watchEvents();
    $('state').textContent = '再接続中…';
    $('state').className = 'chip bad';
  };
}

// --- 画面 1: 商品 ---

function selectTab(name) {
  document.querySelectorAll('[data-tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
  document.querySelectorAll('[data-panel]').forEach((p) => (p.hidden = p.dataset.panel !== name));
  if (name === 'held') loadHeld();
}

async function loadOptions() {
  options = await api('/api/cms/options');
  $('hold-warning').hidden = options.hold_supported;
  $('create').disabled = !options.hold_supported;

  $('kinds').innerHTML = '';
  options.kinds.forEach((k, i) => {
    const label = document.createElement('label');
    label.innerHTML = `<input type="radio" name="kind" value="${k.value}" ${i === 0 ? 'checked' : ''}><span>${k.label}</span>`;
    $('kinds').append(label);
  });
  $('brands').innerHTML = options.brands.map((b) => `<option value="${escapeHtml(b.name)}"></option>`).join('');
  $('category').innerHTML = '<option value="">（選ばない）</option>' +
    options.categories.map((c) => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.label)}（${c.count}）</option>`).join('');
  $('product-type').innerHTML = '<option value="">（選ばない）</option>' +
    options.product_types.map((t) => `<option value="${escapeHtml(t.name)}">${escapeHtml(t.name)}（${t.count}）</option>`).join('');
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function brandMatch() {
  const name = $('brand').value.trim();
  if (!name) return { id: null, name: null };
  const found = options.brands.find((b) => b.name.toLowerCase() === name.toLowerCase());
  return found ? { id: found.id, name: null } : { id: null, name };
}

async function loadHeld() {
  try {
    const held = await api('/api/cms/products');
    $('held').innerHTML = '';
    held.forEach((p) => {
      const li = document.createElement('li');
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = `${p.title}（#${p.id}）`;
      b.onclick = () => openShoot(p);
      li.append(b);
      $('held').append(li);
    });
    $('held-empty').hidden = held.length > 0;
  } catch (e) {
    toast(`下書きを読めません: ${e.message}`, true);
  }
}

// --- 画面 2: 撮影 ---

function openShoot(p) {
  product = p;
  store(KEY_PRODUCT, p);
  $('p-id').textContent = p.id;
  $('p-title').textContent = p.title;
  $('view-product').hidden = true;
  $('view-shoot').hidden = false;
  loadSessions();
}

function openProduct() {
  product = null;
  store(KEY_PRODUCT, null);
  stopLive();
  $('view-shoot').hidden = true;
  $('view-product').hidden = false;
}

async function loadSessions() {
  if (!product) return;
  const sessions = await api(`/api/sessions?cms_id=${product.id}`).catch(() => []);
  $('sessions').innerHTML = sessions
    .map((s) => `<li class="session"><span class="mono">${escapeHtml(s.session_id)}</span><span>${s.shots} 枚・${(s.bytes / 1e9).toFixed(2)} GB</span></li>`)
    .join('');
  $('sessions-empty').hidden = sessions.length > 0;
}

async function withBusy(button, fn) {
  button.disabled = true;
  try { await fn(); } catch (e) { toast(e.message, true); } finally { button.disabled = false; }
}

function bind() {
  document.querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => selectTab(b.dataset.tab)));
  $('brand').addEventListener('input', () => {
    const m = brandMatch();
    $('brand-hint').textContent = m.name ? `「${m.name}」を新しいブランドとして追加します` : '';
  });
  $('category').addEventListener('change', () => {
    const c = options.categories.find((x) => x.id === $('category').value);
    if (c?.product_type) $('product-type').value = c.product_type;
  });
  $('new-product').addEventListener('submit', (e) => {
    e.preventDefault();
    withBusy($('create'), async () => {
      const brand = brandMatch();
      const category = options.categories.find((x) => x.id === $('category').value);
      const p = await post('/api/cms/products', {
        kind: document.querySelector('input[name="kind"]:checked').value,
        name: $('name').value,
        brand_id: brand.id,
        brand_name: brand.name,
        product_type: $('product-type').value || null,
        category_id: category?.id ?? null,
        category_name: category?.name ?? null,
      });
      $('new-product').reset();
      $('brand-hint').textContent = '';
      toast(`下書きを作りました: ${p.title}`);
      openShoot(p);
    });
  });
  $('change-product').onclick = openProduct;
  $('live-toggle').onclick = () => (live.abort ? stopLive() : startLive());
  $('log-clear').onclick = () => ($('log').innerHTML = '');
  $('log-errors').onchange = () => $('log').classList.toggle('errors-only', $('log-errors').checked);

  $('shoot').onclick = () => withBusy($('shoot'), async () => {
    const ev = $('ev').value.trim().split(/\s+/).map(Number).filter((n) => !Number.isNaN(n));
    const j = await post('/api/jobs', {
      cms_product_id: product.id,
      ev: ev.length ? ev : null,
      frames: Number($('frames').value) || null,
      positions: Number($('positions').value) || null,
      note: $('note').value || null,
    });
    renderJob(j);
  });
  $('cancel').onclick = () => withBusy($('cancel'), () => api(`/api/jobs/${job.id}`, { method: 'DELETE' }));

  $('meter').onclick = () => withBusy($('meter'), async () => { await post('/api/camera/meter'); toast('測光して固定しました'); });
  $('auto').onclick = () => withBusy($('auto'), () => post('/api/camera/auto'));
  $('lens').addEventListener('input', () => ($('lens-value').textContent = Number($('lens').value).toFixed(2)));
  $('apply-settings').onclick = () => withBusy($('apply-settings'), () => api('/api/camera/settings', {
    method: 'PUT',
    body: JSON.stringify({ lens_position: Number($('lens').value), base_exposure_us: Number($('exposure').value) || null }),
  }));

  document.querySelectorAll('[data-move]').forEach((b) => (b.onclick = () => withBusy(b, async () => {
    const [dp, dt] = b.dataset.move.split(',').map(Number);
    const pt = status.pantilt;
    await post('/api/pantilt/move', { pan: (pt.pan ?? 307) + dp, tilt: (pt.tilt ?? 307) + dt });
  })));
  $('pantilt-stop').onclick = () => post('/api/pantilt/stop').then(() => toast('停止しました'));
  $('preset-go').onclick = () => withBusy($('preset-go'), async () => {
    const presets = await api('/api/pantilt/presets');
    const p = presets[$('presets').value];
    if (p) await post('/api/pantilt/move', { ...p, approach: p.approach ?? '+' });
  });
  $('preset-save').onclick = () => withBusy($('preset-save'), async () => {
    const name = prompt('プリセットの名前');
    if (!name) return;
    const presets = await api('/api/pantilt/presets');
    presets[name] = { pan: status.pantilt.pan, tilt: status.pantilt.tilt, approach: '+' };
    await api('/api/pantilt/presets', { method: 'PUT', body: JSON.stringify(presets) });
    await loadPresets();
  });
}

async function loadPresets() {
  const presets = await api('/api/pantilt/presets').catch(() => ({}));
  $('presets').innerHTML = '<option value="">プリセット</option>' +
    Object.keys(presets).map((n) => `<option>${escapeHtml(n)}</option>`).join('');
}

async function main() {
  bind();
  (await api('/api/logs').catch(() => [])).forEach((entry) => addLog(entry));
  connectEvents();
  pingApi();
  checkCms();
  setInterval(pingApi, 5000);
  setInterval(checkCms, 30000);
  setInterval(() => { watchEvents(); watchLive(); }, 1000);
  document.addEventListener('visibilitychange', () => {
    // 画面を閉じているあいだはライブビューを切り、通信量を抑える
    if (document.hidden && live.abort) stopLive();
  });
  try {
    await loadOptions();
  } catch (e) {
    toast(`CMS を読めません: ${e.message}`, true);
  }
  loadPresets();
  const saved = restore(KEY_PRODUCT);
  if (saved) openShoot(saved);
}

main();
