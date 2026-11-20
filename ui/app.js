'use strict';
/* Nightwave UI — draws the scene + visualiser and talks to Python through pywebview.api */

const $ = (s) => document.querySelector(s);
const cv = $('#bg'), ctx = cv.getContext('2d');
const BANDS = 64;

// ------------------------------------------------------------------ themes
const THEMES = {
  synthwave: { name: 'Synthwave', sky: ['#04010a', '#170424', '#35093b'], floor: ['#1a0322', '#050108'],
    grid: [255, 60, 200], m1: [255, 70, 200], m2: [120, 90, 255], ringA: [30, 210, 230], ringB: [150, 240, 60],
    orb: [30, 150, 200], accent: '#ff4fd8' },
  ocean: { name: 'Deep Ocean', sky: ['#01030a', '#03122a', '#0a2a48'], floor: ['#04172a', '#010408'],
    grid: [40, 170, 255], m1: [70, 200, 255], m2: [60, 90, 220], ringA: [90, 230, 255], ringB: [130, 110, 255],
    orb: [30, 110, 220], accent: '#4fd2ff' },
  ember: { name: 'Ember', sky: ['#060100', '#1d0603', '#3d1107'], floor: ['#220703', '#060100'],
    grid: [255, 110, 40], m1: [255, 150, 70], m2: [220, 50, 70], ringA: [255, 215, 90], ringB: [255, 60, 90],
    orb: [255, 100, 40], accent: '#ff8a3d' },
  mono: { name: 'OLED Mono', sky: ['#000000', '#030303', '#0b0b0d'], floor: ['#0a0a0a', '#000000'],
    grid: [200, 200, 210], m1: [235, 235, 240], m2: [120, 120, 130], ringA: [255, 255, 255], ringB: [150, 150, 160],
    orb: [110, 110, 125], accent: '#eaeaf0' },
  album: null, // built from the cover art
};
const THEME_ORDER = ['synthwave', 'ocean', 'ember', 'mono', 'album'];
const MODES = { ring: 'Ring', bars: 'Skyline bars', wave: 'Mirror wave' };
const MODE_ORDER = Object.keys(MODES);

// ------------------------------------------------------------------ state
let api = null;
let settings = { theme: 'synthwave', mode: 'ring', onTop: true, compact: false };
let media = { ok: false, active: false };
let posBase = 0, posAt = 0, seeking = null;
const spec = new Float32Array(BANDS);
let target = new Float32Array(BANDS), bass = 0, level = 0, audioOk = false;
let artId = -1, albumTheme = null;
let W = 0, H = 0, DPR = 1, stars = [];

// ------------------------------------------------------------------ helpers
const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const fmt = (s) => { s = Math.max(0, Math.floor(s)); const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(x).padStart(2, '0'); };
const theme = () => (settings.theme === 'album' ? (albumTheme || THEMES.synthwave) : THEMES[settings.theme]) || THEMES.synthwave;
const bandAt = (f) => spec[Math.min(BANDS - 1, Math.floor(clamp(f, 0, 1) * 0.86 * (BANDS - 1)))];

let toastTimer;
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 1700);
}

// called from Python when the widget is hidden to / shown from the tray
let hiddenToTray = false;
window.nwSetHidden = (v) => { hiddenToTray = !!v; if (!v) { pollMedia(); } };
window.nwSettingsChanged = (s) => {
  Object.assign(settings, s);
  $('#btnPin').classList.toggle('on', !!settings.onTop);
  document.body.classList.toggle('compact', !!settings.compact);
  setTimeout(onResize, 60);
};
const paused = () => document.hidden || hiddenToTray;

// ------------------------------------------------------------------ boot
function boot(realApi) {
  if (api) return;
  api = realApi;
  init();
}
window.addEventListener('pywebviewready', () => boot(window.pywebview.api));
if (window.pywebview && window.pywebview.api && window.pywebview.api.get_state) boot(window.pywebview.api);
setTimeout(() => { if (!api && !window.pywebview) boot(mockApi()); }, 2500); // opened in a normal browser → demo

async function init() {
  try { Object.assign(settings, (await api.load_settings()) || {}); } catch (e) { /* defaults */ }
  applyTheme();
  document.body.classList.toggle('compact', !!settings.compact);
  $('#btnPin').classList.toggle('on', !!settings.onTop);
  try { if (!(await api.has_tray())) document.body.classList.add('no-tray'); } catch (e) { document.body.classList.add('no-tray'); }
  bindUI();
  onResize();
  addEventListener('resize', onResize);
  if (!settings.tipDrag) {
    setTimeout(() => toast('Tip: drag anywhere to move me'), 1500);
    settings.tipDrag = true; api.save_settings({ tipDrag: true });
  }
  pollMedia(); setInterval(pollMedia, 500);
  setInterval(pollSpectrum, 33);
  requestAnimationFrame(frame);
}

function onResize() {
  DPR = Math.min(window.devicePixelRatio || 1, 2);
  W = innerWidth; H = innerHeight;
  cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR);
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  stars = Array.from({ length: Math.round(W * H / 900) }, () => ({
    x: Math.random(), y: Math.random() ** 1.4, r: Math.random() < 0.85 ? 1 : 1.6,
    a: Math.random() ** 2 * 0.8, f: 0.5 + Math.random() * 2, p: Math.random() * 6.28 }));
  updateMarquee();
}

function applyTheme() {
  document.documentElement.style.setProperty('--accent', theme().accent);
}

// ------------------------------------------------------------------ polling
let mediaBusy = false, specBusy = false;
// never let one slow call block polling forever
const withTimeout = (p, ms) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), ms))]);
async function pollMedia() {
  if (mediaBusy || !api || hiddenToTray) return; mediaBusy = true;
  try { onMedia(await withTimeout(api.get_state(), 3000)); } catch (e) { /* ignore */ } finally { mediaBusy = false; }
}
async function pollSpectrum() {
  if (specBusy || !api || paused()) return; specBusy = true;
  try {
    const r = await withTimeout(api.get_spectrum(), 1000);
    if (r && r.bands) { target = r.bands; bass = r.bass; level = r.level; audioOk = r.ok; }
  } catch (e) { /* ignore */ } finally { specBusy = false; }
}

function onMedia(s) {
  if (!s) return;
  media = s;
  if (!seeking) { posBase = s.pos || 0; posAt = performance.now(); }
  document.body.classList.toggle('playing', !!s.playing);

  let title, sub;
  if (!s.ok) { title = 'Media controls unavailable'; sub = s.error || ''; }
  else if (!s.active) { title = 'Nothing playing'; sub = 'Play something in Spotify, YouTube…'; }
  else { title = s.title || s.app || 'Unknown'; sub = [s.artist, s.app].filter(Boolean).join('  ·  '); }
  setTitle(title);
  $('#sub').textContent = sub;

  $('#btnPlay').disabled = !s.active || !s.can_toggle;
  $('#btnNext').disabled = !s.active || !s.can_next;
  $('#btnPrev').disabled = !s.active || !s.can_prev;
  $('#prog').classList.toggle('hidden', !(s.active && s.dur > 0));
  $('#bar').classList.toggle('off', !s.can_seek);
  $('#tDur').textContent = fmt(s.dur || 0);

  const n = (s.sessions || []).length;
  $('#sourceName').textContent = s.active ? s.app : (n ? 'Sources' : 'No source');
  $('#sourceCount').textContent = n > 1 ? `+${n - 1}` : '';
  if ($('#sources').classList.contains('open')) renderSources();

  if (s.art_id !== artId) {
    artId = s.art_id;
    if (s.art_id) api.get_art().then(setArt).catch(() => setArt(null));
    else setArt(null);
  }
}

let lastTitle = null;
function setTitle(t) {
  if (t === lastTitle) return;
  lastTitle = t;
  const tr = $('#title .track');
  tr.innerHTML = '';
  const a = document.createElement('span'); a.textContent = t; tr.appendChild(a);
  updateMarquee();
}
function updateMarquee() {
  const box = $('#title'), tr = $('#title .track');
  if (!tr.firstChild) return;
  box.classList.remove('scroll');
  while (tr.children.length > 1) tr.lastChild.remove();
  if (tr.scrollWidth > box.clientWidth + 2) {
    tr.appendChild(tr.firstChild.cloneNode(true));
    box.classList.add('scroll');
    box.style.setProperty('--dur', Math.max(8, tr.scrollWidth / 2 / 28) + 's');
  }
}

function setArt(url) {
  const img = $('#art'), box = $('#artBox');
  if (!url) { box.classList.remove('has'); img.removeAttribute('src'); albumTheme = null; applyTheme(); return; }
  img.onload = () => { box.classList.add('has'); albumTheme = paletteFrom(img); applyTheme(); };
  img.src = url;
}

// build a theme from the dominant colours of the cover art
function paletteFrom(img) {
  try {
    const c = document.createElement('canvas'); c.width = c.height = 24;
    const g = c.getContext('2d'); g.drawImage(img, 0, 0, 24, 24);
    const d = g.getImageData(0, 0, 24, 24).data;
    const bins = Array.from({ length: 12 }, () => ({ w: 0, r: 0, g: 0, b: 0 }));
    for (let i = 0; i < d.length; i += 4) {
      const r = d[i] / 255, gg = d[i + 1] / 255, b = d[i + 2] / 255;
      const mx = Math.max(r, gg, b), mn = Math.min(r, gg, b), sat = mx ? (mx - mn) / mx : 0;
      if (mx < 0.2) continue;
      let h = 0;
      if (mx !== mn) h = mx === r ? ((gg - b) / (mx - mn)) % 6 : mx === gg ? (b - r) / (mx - mn) + 2 : (r - gg) / (mx - mn) + 4;
      const bin = bins[((Math.floor((h * 60 + 360) % 360 / 30)) + 12) % 12];
      const w = sat * sat * mx + 0.02;
      bin.w += w; bin.r += d[i] * w; bin.g += d[i + 1] * w; bin.b += d[i + 2] * w;
    }
    const ranked = bins.map((b, i) => ({ ...b, i })).filter(b => b.w > 0).sort((a, b) => b.w - a.w);
    if (!ranked.length) return null;
    const col = (b) => { const c = [b.r / b.w, b.g / b.w, b.b / b.w]; const m = Math.max(...c) || 1; return mix(c.map(v => v * 255 / m), [255, 255, 255], 0.12); };
    const A = col(ranked[0]);
    const second = ranked.find(b => Math.min(Math.abs(b.i - ranked[0].i), 12 - Math.abs(b.i - ranked[0].i)) >= 2 && b.w > ranked[0].w * 0.08);
    const B = second ? col(second) : [A[2], A[0], A[1]];
    const dark = (c, k) => '#' + c.map(v => Math.round(v * k).toString(16).padStart(2, '0')).join('');
    return { name: 'Album colours', sky: ['#020103', dark(A, 0.06), dark(A, 0.16)], floor: [dark(A, 0.1), '#010101'],
      grid: A, m1: A, m2: B, ringA: A, ringB: B, orb: mix(A, [0, 0, 0], 0.35), accent: rgba(A, 1) };
  } catch (e) { return null; }
}

// ------------------------------------------------------------------ controls
async function control(action, value) {
  if (!api) return;
  if (action === 'toggle') { media.playing = !media.playing; document.body.classList.toggle('playing', media.playing); posBase = curPos(); posAt = performance.now(); }
  if (action === 'seek') { posBase = value; posAt = performance.now(); }
  let ok = false;
  try { ok = await api.control(action, value ?? null); } catch (e) { ok = false; }
  if (!ok) {
    const what = { toggle: 'play/pause', next: 'skipping', prev: 'going back', seek: 'seeking' }[action] || action;
    toast(`${media.app || 'This source'} didn't accept ${what}`);
  }
  setTimeout(pollMedia, 200);
}

function curPos() {
  if (seeking) return seeking.pos;
  let p = posBase;
  if (media.playing && media.dur > 0) p += (performance.now() - posAt) / 1000;
  return clamp(p, 0, media.dur || 0);
}

function renderSources() {
  const box = $('#sources'); box.innerHTML = '';
  const add = (label, id, live, sel, note) => {
    const el = document.createElement('div');
    el.className = 'src' + (live ? ' live' : '') + (sel ? ' sel' : '');
    el.innerHTML = `<span class="dot"></span><span></span>${note ? `<small>${note}</small>` : ''}`;
    el.children[1].textContent = label;
    el.onclick = async () => { box.classList.remove('open'); document.body.classList.remove('menu-open'); await api.select_session(id); pollMedia(); };
    box.appendChild(el);
  };
  add('Automatic', null, false, !media.selected, 'follows Windows');
  const list = media.sessions || [];
  if (list.length) box.appendChild(Object.assign(document.createElement('div'), { className: 'src-sep' }));
  for (const s of list) add(s.name, s.id, s.playing, media.selected === s.id, s.playing ? 'playing' : '');
  if (!list.length) add('No apps are playing media', null, false, false, '');
}

function bindUI() {
  $('#btnPlay').onclick = () => control('toggle');
  $('#btnNext').onclick = () => control('next');
  $('#btnPrev').onclick = () => control('prev');
  $('#btnClose').onclick = () => api.close();
  $('#btnHide').onclick = () => api.hide();
  $('#btnMode').onclick = () => {
    settings.mode = MODE_ORDER[(MODE_ORDER.indexOf(settings.mode) + 1) % MODE_ORDER.length];
    toast(MODES[settings.mode]); api.save_settings({ mode: settings.mode });
  };
  $('#btnTheme').onclick = () => {
    settings.theme = THEME_ORDER[(THEME_ORDER.indexOf(settings.theme) + 1) % THEME_ORDER.length];
    applyTheme(); toast(settings.theme === 'album' ? (albumTheme ? 'Album colours' : 'Album colours (needs cover art)') : THEMES[settings.theme].name);
    api.save_settings({ theme: settings.theme });
  };
  $('#btnPin').onclick = async () => {
    settings.onTop = !settings.onTop; $('#btnPin').classList.toggle('on', settings.onTop);
    toast(settings.onTop ? 'Always on top' : 'Not on top'); await api.set_on_top(settings.onTop);
  };
  $('#btnCompact').onclick = async () => {
    settings.compact = !settings.compact;
    document.body.classList.toggle('compact', settings.compact);
    $('#sources').classList.remove('open'); document.body.classList.remove('menu-open');
    await api.set_compact(settings.compact);
    setTimeout(onResize, 60);
  };
  $('#source').onclick = (e) => {
    e.stopPropagation();
    const open = !$('#sources').classList.contains('open');
    if (open) renderSources();
    $('#sources').classList.toggle('open', open); document.body.classList.toggle('menu-open', open);
  };
  document.addEventListener('pointerdown', (e) => {
    if (!e.target.closest('#sources') && !e.target.closest('#source')) { $('#sources').classList.remove('open'); document.body.classList.remove('menu-open'); }
  });

  // seek bar
  const bar = $('#bar');
  const fracAt = (e) => { const r = bar.querySelector('.track').getBoundingClientRect(); return clamp((e.clientX - r.left) / r.width, 0, 1); };
  bar.addEventListener('pointerdown', (e) => {
    if (!media.can_seek || !media.dur) return;
    bar.setPointerCapture(e.pointerId); bar.classList.add('drag');
    seeking = { pos: fracAt(e) * media.dur };
  });
  bar.addEventListener('pointermove', (e) => { if (seeking) seeking.pos = fracAt(e) * media.dur; });
  bar.addEventListener('pointerup', () => {
    if (!seeking) return;
    const p = seeking.pos; seeking = null; bar.classList.remove('drag'); control('seek', p);
  });

  // resize grip (frameless windows have no border to drag)
  const grip = $('#grip'); let rs = null, pending = null;
  grip.addEventListener('pointerdown', (e) => { grip.setPointerCapture(e.pointerId); rs = { x: e.screenX, y: e.screenY, w: innerWidth, h: innerHeight }; });
  grip.addEventListener('pointermove', (e) => {
    if (!rs) return;
    const want = [rs.w + e.screenX - rs.x, rs.h + e.screenY - rs.y];
    if (!pending) { pending = true; requestAnimationFrame(async () => { await api.resize(want[0], want[1]); pending = false; }); }
  });
  grip.addEventListener('pointerup', () => { if (rs) { rs = null; setTimeout(() => api.remember_size(innerWidth, innerHeight), 120); } });

  // move the window: grab anywhere that isn't a control.
  // On Windows, Python takes over and follows the real cursor until the button is released.
  const NO_DRAG = 'button, #bar, #grip, #sources';
  let drag = null, nativeDrag = null;   // nativeDrag: null = unknown, true/false once tried
  let moving = false, nextPos = null;
  const pump = async () => {             // fallback (non-Windows / demo)
    if (moving || !nextPos) return;
    moving = true; const p = nextPos; nextPos = null;
    try { await api.move_to(p[0], p[1]); } catch (e) { /* ignore */ }
    moving = false; if (nextPos) pump();
  };
  document.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || e.target.closest(NO_DRAG)) return;
    drag = { x: e.clientX, y: e.clientY, sx: e.screenX, sy: e.screenY, id: e.pointerId, started: false, native: false };
  });
  document.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    if (!drag.started) {
      if (Math.hypot(e.screenX - drag.sx, e.screenY - drag.sy) < 3) return;
      drag.started = true; document.body.classList.add('dragging');
      if (nativeDrag !== false) {
        drag.native = true;
        const d = drag;
        api.begin_drag().then((ok) => {
          if (ok) nativeDrag = true;
          else if (nativeDrag === null) { nativeDrag = false; d.native = false; }
          if (d === drag || !drag) document.body.classList.remove('dragging');
        }).catch(() => { nativeDrag = false; d.native = false; });
      }
    }
    if (!drag.native) { nextPos = [e.screenX - drag.x, e.screenY - drag.y]; pump(); }
  });
  const endDrag = (e) => {
    if (!drag || (e && e.pointerId !== drag.id)) return;
    const d = drag; drag = null;
    document.body.classList.remove('dragging');
    if (d.started && !d.native) setTimeout(() => api.end_drag(), 60);
  };
  document.addEventListener('pointerup', endDrag);
  document.addEventListener('pointercancel', endDrag);

  // keyboard: space = play/pause, arrows = prev/next
  addEventListener('keydown', (e) => {
    if (e.code === 'Escape') api.hide();
    else if (e.code === 'Space') { e.preventDefault(); control('toggle'); }
    else if (e.code === 'ArrowRight') control('next');
    else if (e.code === 'ArrowLeft') control('prev');
  });
}

// ------------------------------------------------------------------ drawing
function frame(now) {
  requestAnimationFrame(frame);
  if (paused()) return;
  const t = now / 1000;
  for (let i = 0; i < BANDS; i++) spec[i] += ((target[i] || 0) - spec[i]) * 0.45;
  draw(t);
  // progress
  if (media.dur > 0) {
    const p = curPos(), f = p / media.dur * 100;
    $('#fill').style.width = f + '%'; $('#knob').style.left = f + '%';
    $('#tNow').textContent = fmt(p);
  }
}

function ridge(u, seed) {
  // periodic in u ∈ [0,1) so it can scroll forever without a seam
  let v = 0;
  v += Math.sin((u * 2 + seed * 0.13) * Math.PI * 2) * 0.5;
  v += Math.sin((u * 5 + seed * 0.37) * Math.PI * 2) * 0.25;
  v += Math.sin((u * 11 + seed * 0.71) * Math.PI * 2) * 0.12;
  v += Math.sin((u * 23 + seed * 0.29) * Math.PI * 2) * 0.06;
  return v * 0.5 + 0.5;
}

let gridZ = 0, lastT = 0;
function draw(t) {
  const th = theme(), compact = settings.compact;
  const dt = Math.min(0.05, t - (lastT || t)); lastT = t;
  const horizon = compact ? H * 0.58 : H * 0.79;
  const top = compact ? 0 : 44;
  idle = audioOk && level > 0.015 ? Math.max(0, idle - dt * 2) : Math.min(1, idle + dt * 0.8);

  // sky
  let g = ctx.createLinearGradient(0, 0, 0, horizon);
  g.addColorStop(0, th.sky[0]); g.addColorStop(0.6, th.sky[1]); g.addColorStop(1, th.sky[2]);
  ctx.globalCompositeOperation = 'source-over';
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, horizon + 1);

  // stars
  for (const s of stars) {
    const a = s.a * (0.55 + 0.45 * Math.sin(t * s.f + s.p)) * (1 - s.y * 0.7);
    if (a < 0.03) continue;
    ctx.fillStyle = `rgba(235,225,255,${a})`;
    ctx.fillRect(s.x * W, s.y * horizon * 0.95, s.r, s.r);
  }

  // mountains: two glowing ridge lines that breathe with the bass
  const span = horizon - top;
  drawRidge(horizon, span * 0.17 * (1 + bass * 0.35), 3, t * 0.006, th.m2, 0.9);
  drawRidge(horizon, span * 0.27 * (1 + bass * 0.45), 7, t * 0.003, th.m1, 1);

  // floor + moving grid (speed follows the music)
  g = ctx.createLinearGradient(0, horizon, 0, H);
  g.addColorStop(0, th.floor[0]); g.addColorStop(1, th.floor[1]);
  ctx.fillStyle = g; ctx.fillRect(0, horizon, W, H - horizon);
  gridZ += dt * (0.25 + level * 2.2 + bass * 1.5);
  drawGrid(horizon, th);
  const hl = ctx.createLinearGradient(0, 0, W, 0);
  hl.addColorStop(0, rgba(th.m1, 0)); hl.addColorStop(0.5, rgba(th.m1, 0.95)); hl.addColorStop(1, rgba(th.m1, 0));
  ctx.globalCompositeOperation = 'lighter';
  ctx.fillStyle = hl; ctx.fillRect(0, horizon - 1, W, 2);

  // visualiser
  if (compact) drawBars(0, H, th, 0.35, true);
  else {
    const cx = W / 2, cy = (top + horizon) / 2 + 2;
    const R = Math.min(W * 0.26, span * 0.29);
    if (settings.mode === 'bars') drawBars(top, horizon, th, 1, false);
    else if (settings.mode === 'wave') drawWave(cx, cy, span, th, t);
    else drawRing(cx, cy, R, th, t);
  }
  ctx.globalCompositeOperation = 'source-over';
}
let idle = 1;

function drawRidge(horizon, h, seed, drift, col, alpha) {
  const n = Math.max(40, Math.round(W / 4));
  ctx.globalCompositeOperation = 'source-over';
  ctx.beginPath(); ctx.moveTo(0, horizon);
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const x = i / n * W, u = (i / n * 0.8 + drift) % 1;
    const centre = Math.abs(i / n - 0.5) * 2;               // lower in the middle → valley for the visualiser
    const y = horizon - ridge(u, seed) * h * (0.35 + 0.65 * centre ** 0.8);
    pts.push([x, y]); ctx.lineTo(x, y);
  }
  ctx.lineTo(W, horizon); ctx.closePath();
  ctx.fillStyle = 'rgba(6,2,12,0.88)'; ctx.fill();
  ctx.globalCompositeOperation = 'lighter';
  const path = new Path2D(); pts.forEach(([x, y], i) => (i ? path.lineTo(x, y) : path.moveTo(x, y)));
  ctx.strokeStyle = rgba(col, 0.16 * alpha); ctx.lineWidth = 5; ctx.stroke(path);
  ctx.strokeStyle = rgba(col, 0.85 * alpha); ctx.lineWidth = 1.3; ctx.stroke(path);
}

function drawGrid(horizon, th) {
  const G = H - horizon; if (G < 4) return;
  const K = G * 0.55, gap = 0.16, off = gridZ % gap, cx = W / 2;
  const p = new Path2D();
  for (let k = 0; k < 120; k++) {
    const z = (k + 1) * gap - off; const y = horizon + K / z;
    if (y > H + 1) continue; if (y - horizon < 1.5) break;
    p.moveTo(0, y); p.lineTo(W, y);
  }
  const spacing = Math.max(26, W / 11);
  for (let i = -24; i <= 24; i++) { p.moveTo(cx + i * spacing * (G / K) * 1.2, H + G * 0.0); p.lineTo(cx, horizon); }
  ctx.save(); ctx.beginPath(); ctx.rect(0, horizon, W, G); ctx.clip();
  ctx.globalCompositeOperation = 'lighter';
  ctx.strokeStyle = rgba(th.grid, 0.12 + bass * 0.1); ctx.lineWidth = 3.5; ctx.stroke(p);
  ctx.strokeStyle = rgba(th.grid, 0.55); ctx.lineWidth = 1; ctx.stroke(p);
  ctx.globalCompositeOperation = 'source-over';
  const haze = ctx.createLinearGradient(0, horizon, 0, horizon + G * 0.5);
  haze.addColorStop(0, th.floor[0]); haze.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = haze; ctx.fillRect(0, horizon, W, G * 0.5);
  ctx.restore();
}

function idleWobble(i, t) { return idle * (0.06 + 0.05 * Math.sin(t * 1.8 + i * 0.38)); }

function drawRing(cx, cy, R, th, t) {
  // orb
  ctx.globalCompositeOperation = 'lighter';
  const oR = R * 0.9 * (1 + bass * 0.22);
  const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, oR);
  g.addColorStop(0, rgba(th.orb, 0.8)); g.addColorStop(0.5, rgba(th.orb, 0.28)); g.addColorStop(1, rgba(th.orb, 0));
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, oR, 0, Math.PI * 2); ctx.fill();
  // guide circle
  ctx.strokeStyle = 'rgba(255,255,255,0.16)'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.arc(cx, cy, R * 0.94, 0, Math.PI * 2); ctx.stroke();
  // dots (mirrored left/right, bass at the top)
  const N = 72;
  for (let i = 0; i < N; i++) {
    const k = i <= N / 2 ? i : N - i, f = k / (N / 2);
    const v = Math.min(1.2, bandAt(f) + idleWobble(i, t));
    const a = -Math.PI / 2 + i / N * Math.PI * 2, ca = Math.cos(a), sa = Math.sin(a);
    const r0 = R + 3, r1 = R + 5 + v * R * 0.5;
    const c = mix(th.ringA, th.ringB, f);
    if (v > 0.08) {
      ctx.strokeStyle = rgba(c, 0.32); ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(cx + ca * r0, cy + sa * r0); ctx.lineTo(cx + ca * r1, cy + sa * r1); ctx.stroke();
    }
    const x = cx + ca * r1, y = cy + sa * r1;
    ctx.fillStyle = rgba(c, 0.16); ctx.beginPath(); ctx.arc(x, y, 5 + v * 4, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = rgba(c, 1); ctx.beginPath(); ctx.arc(x, y, 1.9 + v * 1.4, 0, Math.PI * 2); ctx.fill();
  }
}

function drawBars(top, base, th, alpha, compact) {
  const n = compact ? 56 : 40, gap = compact ? 2 : 3, bw = W / n;
  const maxH = (base - top) * (compact ? 0.9 : 0.62);
  ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < n; i++) {
    const f = Math.abs(i - (n - 1) / 2) / ((n - 1) / 2);   // bass in the middle
    const v = Math.min(1, bandAt(f) + idleWobble(i, lastT) * 0.6);
    const h = 2 + v * maxH, x = i * bw + gap / 2, w = bw - gap;
    const c = mix(th.ringA, th.ringB, f);
    const g = ctx.createLinearGradient(0, base - h, 0, base);
    g.addColorStop(0, rgba(c, 0.95 * alpha)); g.addColorStop(1, rgba(c, 0.25 * alpha));
    ctx.fillStyle = g; ctx.fillRect(x, base - h, w, h);
    if (!compact) { ctx.fillStyle = rgba(c, 0.13 * alpha); ctx.fillRect(x, base + 2, w, h * 0.35); }
  }
}

function drawWave(cx, cy, span, th, t) {
  const n = 80, amp = span * 0.33;
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const x = i / n * W, f = Math.abs(x - cx) / (W / 2);
    const env = Math.sin(Math.PI * i / n) ** 0.6;
    const v = Math.min(1.1, bandAt(f) + idleWobble(i, t) * 0.8);
    pts.push([x, v * amp * env]);
  }
  const curve = (sign) => {
    const p = new Path2D(); p.moveTo(0, cy);
    for (let i = 1; i < pts.length; i++) {
      const [x0, a0] = pts[i - 1], [x1, a1] = pts[i];
      p.quadraticCurveTo(x0, cy - sign * a0, (x0 + x1) / 2, cy - sign * (a0 + a1) / 2);
    }
    p.lineTo(W, cy); return p;
  };
  const up = curve(1), down = curve(-1);
  ctx.globalCompositeOperation = 'lighter';
  const g = ctx.createLinearGradient(0, 0, W, 0);
  g.addColorStop(0, rgba(th.ringB, 0)); g.addColorStop(0.5, rgba(th.ringA, 0.28)); g.addColorStop(1, rgba(th.ringB, 0));
  ctx.fillStyle = g; ctx.fill(up); ctx.fill(down);
  const s = ctx.createLinearGradient(0, 0, W, 0);
  s.addColorStop(0, rgba(th.ringB, 0.2)); s.addColorStop(0.5, rgba(th.ringA, 1)); s.addColorStop(1, rgba(th.ringB, 0.2));
  ctx.strokeStyle = s; ctx.lineWidth = 1.6; ctx.stroke(up); ctx.stroke(down);
  ctx.lineWidth = 6; ctx.globalAlpha = 0.15; ctx.stroke(up); ctx.stroke(down); ctx.globalAlpha = 1;
}

// ------------------------------------------------------------------ demo mode (opened in a normal browser)
function mockApi() {
  const songs = [
    { title: 'Midnight City Lights (Extended Mix)', artist: 'Neon Avenue', dur: 247, hue: 300 },
    { title: 'Ocean Drive', artist: 'Low Tide', dur: 198, hue: 190 },
    { title: 'Ember', artist: 'Kilns', dur: 221, hue: 20 },
  ];
  let i = 0, playing = true, pos = 63, at = performance.now();
  const p = () => playing ? pos + (performance.now() - at) / 1000 : pos;
  const art = (hue) => { const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d');
    const gr = g.createLinearGradient(0, 0, 64, 64); gr.addColorStop(0, `hsl(${hue},90%,55%)`); gr.addColorStop(1, `hsl(${hue + 60},80%,25%)`);
    g.fillStyle = gr; g.fillRect(0, 0, 64, 64); return c.toDataURL(); };
  const sessions = [{ id: 'Spotify.exe', name: 'Spotify', playing: true }, { id: 'chrome', name: 'Chrome', playing: false }];
  let sel = null;
  return {
    load_settings: async () => ({}), save_settings: async () => true,
    get_state: async () => ({ ok: true, active: true, ...songs[i], playing, pos: p() % songs[i].dur, can_toggle: true, can_next: true,
      can_prev: true, can_seek: true, app: 'Spotify', app_id: 'Spotify.exe', sessions, selected: sel, art_id: i + 1 }),
    get_art: async () => art(songs[i].hue),
    get_spectrum: async () => {
      const t = performance.now() / 1000, beat = Math.max(0, Math.sin(t * 2 * Math.PI * 2)) ** 6;
      const bands = Array.from({ length: BANDS }, (_, k) => playing ? clamp(0.75 - k / BANDS * 0.5 + 0.18 * Math.sin(t * 3 + k * 0.4) + (k < 8 ? beat * 0.4 : 0) + Math.random() * 0.12 - 0.15, 0, 1) : 0);
      return { ok: true, bands, bass: bands.slice(0, 7).reduce((a, b) => a + b) / 7, level: bands.reduce((a, b) => a + b) / BANDS };
    },
    control: async (a, v) => { if (a === 'toggle') { pos = p(); at = performance.now(); playing = !playing; }
      if (a === 'next') { i = (i + 1) % songs.length; pos = 0; at = performance.now(); }
      if (a === 'prev') { i = (i + songs.length - 1) % songs.length; pos = 0; at = performance.now(); }
      if (a === 'seek') { pos = v; at = performance.now(); } return true; },
    select_session: async (id) => { sel = id; return true; },
    set_on_top: async (v) => v, set_compact: async (v) => v, resize: async () => true, remember_size: async () => true,
    close: async () => toast('Close (demo)'), minimize: async () => true,
    begin_drag: async () => { window.__nativeDrags = (window.__nativeDrags || 0) + 1; return false; },
    move_to: async (x, y) => { (window.__moves = window.__moves || []).push([x, y]); return true; },
    has_tray: async () => true, hide: async () => toast('Hidden to tray (demo)'),
    end_drag: async () => { window.__ended = (window.__ended || 0) + 1; return true; },
  };
}
