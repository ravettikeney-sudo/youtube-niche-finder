/* Живые декорации: настоящее видео камина (справа внизу) и комнаты с телевизором (слева внизу),
   анимация поиска: лента превью под лупой и «студия» с ростом просмотров и лайков. Анимация идёт только когда её видно,
   не чаще 30 кадров в секунду; при «уменьшить движение» в системе — один неподвижный кадр. */
(function () {
  'use strict';
  const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const DPR = Math.min(2, window.devicePixelRatio || 1);
  const rnd = (a, b) => a + Math.random() * (b - a);
  const PICS = (typeof WALL !== 'undefined' ? WALL : []).map(id => `https://i.ytimg.com/vi/${id}/mqdefault.jpg`);
  const imgs = [];
  function loadPics(n) {
    for (let i = imgs.length; i < Math.min(n, PICS.length); i++) {
      const im = new Image(); im.decoding = 'async'; im.src = PICS[(i * 7) % PICS.length]; imgs.push(im);
    }
  }
  const ready = im => im && im.complete && im.naturalWidth > 0;

  // цикл анимации: кадр только для видимых сцен
  const scenes = [];
  let last = 0;
  function loop(t) {
    requestAnimationFrame(loop);
    if (document.hidden || t - last < 33) return;
    const dt = Math.min(0.1, (t - last) / 1000 || 0.033); last = t;
    for (const s of scenes) if (s.visible()) s.frame(t / 1000, dt);
  }
  function setup(canvas, w, h) {
    canvas.width = Math.round(w * DPR); canvas.height = Math.round(h * DPR);
    const ctx = canvas.getContext('2d'); ctx.setTransform(DPR, 0, 0, DPR, 0, 0); return ctx;
  }
  function sprite(r, stops) {
    const c = document.createElement('canvas'); c.width = c.height = r * 2;
    const g = c.getContext('2d'), gr = g.createRadialGradient(r, r, 0, r, r, r);
    stops.forEach(([o, col]) => gr.addColorStop(o, col)); g.fillStyle = gr; g.fillRect(0, 0, r * 2, r * 2); return c;
  }
  const onScreen = el => { const r = el.getBoundingClientRect(); return el.offsetParent !== null && r.bottom > 0 && r.top < innerHeight && r.width > 0; };

  /* ================= АНИМАЦИЯ ПОИСКА ================= */
  // лента превью едет под лупой; что прошло под лупой — получает отметку: в нише / не та тема
  // (для разбора превью — рамки «лицо» и «текст»). Это иллюстрация процесса, настоящие цифры — в строке ниже.
  function scanner(holder, mode) {
    const cv = document.createElement('canvas'); cv.className = 'scan-cv'; cv.setAttribute('aria-hidden', 'true');
    holder.prepend(cv);
    loadPics(20);
    let W = 0, H = 132, ctx = null;
    const CW = 112, CH = 63, GAP = 16, SPEED = 64;
    const cards = [];
    let off = 0;
    const off2 = document.createElement('canvas'), o = off2.getContext('2d');
    function resize() {
      const w = Math.round(cv.clientWidth || holder.clientWidth - 48);
      if (w === W || w <= 0) return; W = w; ctx = setup(cv, W, H);
      off2.width = W * DPR; off2.height = H * DPR; o.setTransform(DPR, 0, 0, DPR, 0, 0);
    }
    let seq = 0;
    const mk = () => ({ i: seq, im: imgs[(seq * 3) % Math.max(1, imgs.length)], ok: ((seq++ * 7919) % 100) < 64, face: [rnd(.1, .5), rnd(.1, .4), rnd(.25, .35)], txt: [rnd(.45, .6), rnd(.55, .75)] });
    function frame(t, dt) {
      resize(); if (!ctx) return;
      if (!RM) off += SPEED * dt;
      const step = CW + GAP, need = Math.ceil(W / step) + 2;
      while (cards.length < need) cards.push(mk());
      if (off >= step) { off -= step; cards.shift(); cards.push(mk()); }
      const lx = W / 2, ly = H / 2, R = 50;
      o.clearRect(0, 0, W, H);
      cards.forEach((c, k) => {
        const x = k * step - off, y = ly - CH / 2, passed = x + CW / 2 < lx - R * .2;
        if (!c.im || !ready(c.im)) c.im = imgs[(c.i * 3) % Math.max(1, imgs.length)];
        o.save(); o.beginPath(); o.roundRect(x, y, CW, CH, 8); o.clip();
        if (ready(c.im)) o.drawImage(c.im, x, y, CW, CH); else { o.fillStyle = '#d9dce6'; o.fillRect(x, y, CW, CH); }
        if (passed && mode === 'niche' && !c.ok) { o.fillStyle = 'rgba(30,30,40,.55)'; o.fillRect(x, y, CW, CH); }
        o.restore();
        if (passed) {
          if (mode === 'niche') {
            o.fillStyle = c.ok ? '#16a34a' : '#6b7280'; o.beginPath(); o.arc(x + CW - 12, y + 12, 10, 0, 6.28); o.fill();
            o.strokeStyle = '#fff'; o.lineWidth = 2.4; o.lineCap = 'round'; o.beginPath();
            if (c.ok) { o.moveTo(x + CW - 17, y + 12); o.lineTo(x + CW - 13, y + 16); o.lineTo(x + CW - 7, y + 8); }
            else { o.moveTo(x + CW - 16, y + 8); o.lineTo(x + CW - 8, y + 16); o.moveTo(x + CW - 8, y + 8); o.lineTo(x + CW - 16, y + 16); }
            o.stroke();
          } else {
            const [fx, fy, fs] = c.face; o.strokeStyle = '#facc15'; o.lineWidth = 2; o.strokeRect(x + fx * CW, y + fy * CH, fs * CW * .6, fs * CW * .6);
            o.strokeStyle = '#22d3ee'; o.strokeRect(x + 6, y + c.txt[1] * CH, c.txt[0] * CW, 12);
          }
        }
      });
      ctx.clearRect(0, 0, W, H); ctx.drawImage(off2, 0, 0, W, H);
      // лупа: увеличенный кусок ленты
      const Z = 1.45;
      ctx.save(); ctx.beginPath(); ctx.arc(lx, ly, R, 0, 6.28); ctx.clip();
      ctx.fillStyle = '#fff'; ctx.fillRect(lx - R, ly - R, R * 2, R * 2);
      ctx.drawImage(off2, (lx - R / Z) * DPR, (ly - R / Z) * DPR, (2 * R / Z) * DPR, (2 * R / Z) * DPR, lx - R, ly - R, 2 * R, 2 * R);
      const sc = ((t * 1.2) % 1) * 2 * R;               // сканирующая линия
      const sg = ctx.createLinearGradient(lx - R + sc - 12, 0, lx - R + sc, 0); sg.addColorStop(0, 'rgba(99,102,241,0)'); sg.addColorStop(1, 'rgba(99,102,241,.45)');
      ctx.fillStyle = sg; ctx.fillRect(lx - R + sc - 12, ly - R, 12, 2 * R);
      const hl = ctx.createRadialGradient(lx - R * .4, ly - R * .5, 2, lx - R * .4, ly - R * .5, R); hl.addColorStop(0, 'rgba(255,255,255,.35)'); hl.addColorStop(.5, 'rgba(255,255,255,0)');
      ctx.fillStyle = hl; ctx.fillRect(lx - R, ly - R, 2 * R, 2 * R);
      ctx.restore();
      ctx.strokeStyle = '#4f46e5'; ctx.lineWidth = 5; ctx.beginPath(); ctx.arc(lx, ly, R, 0, 6.28); ctx.stroke();
      ctx.strokeStyle = 'rgba(255,255,255,.8)'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(lx, ly, R - 3.5, 0, 6.28); ctx.stroke();
      ctx.strokeStyle = '#312e81'; ctx.lineWidth = 9; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(lx + R * .72, ly + R * .72); ctx.lineTo(lx + R * 1.1, ly + R * 1.1 > H - 6 ? H - 6 : ly + R * 1.1); ctx.stroke();
    }
    addEventListener('resize', () => { W = 0 });
    return { frame, visible: () => onScreen(cv) };
  }

  /* ================= «СТУДИЯ»: рост просмотров и лайков, пока идёт поиск ================= */
  // Иллюстрация: так выглядит рост канала, который зашёл в нишу с открытым окном. Кривая растёт вместе с процентом поиска.
  function studio(holder, pctEl) {
    const box = document.createElement('div'); box.className = 'studio'; box.setAttribute('aria-hidden', 'true');
    box.innerHTML = `<div class="st-head"><span class="st-dot"></span>Аналитика · в реальном времени<small>иллюстрация</small></div>
      <div class="st-nums"><div><small>Просмотры</small><b data-k="v">0</b></div><div><small>Лайки</small><b data-k="l">0</b></div><div><small>Подписчики</small><b data-k="s">0</b></div></div>
      <canvas class="st-cv"></canvas><div class="st-fx"></div>`;
    const scan = holder.querySelector('.scan-cv');
    const row = document.createElement('div'); row.className = 'scan-row';
    scan.replaceWith(row); row.append(scan, box);
    const cv = box.querySelector('canvas'), fx = box.querySelector('.st-fx');
    const els = { v: box.querySelector('[data-k=v]'), l: box.querySelector('[data-k=l]'), s: box.querySelector('[data-k=s]') };
    let W = 0, H = 70, ctx = null, shown = 0, lastPop = 0, pts = [];
    const fmt = n => n >= 1e6 ? (n / 1e6).toFixed(1).replace('.', ',') + ' млн' : n >= 1e4 ? Math.round(n / 1e3) + ' тыс.' : String(Math.round(n));
    function frame(t, dt) {
      const w = Math.round(cv.clientWidth); if (w && w !== W) { W = w; ctx = setup(cv, W, H); }
      if (!ctx) return;
      const pct = Math.max(2, Math.min(100, parseFloat(pctEl.textContent) || 0));
      shown += (pct - shown) * Math.min(1, dt * 2.5);
      const k = shown / 100, grow = x => Math.pow(x, 2.2);
      const views = 1200 + 2.4e6 * grow(k) + Math.sin(t * 3) * 40;
      els.v.textContent = fmt(views); els.l.textContent = fmt(views * .043); els.s.textContent = fmt(views * .0021);
      // линия просмотров как в YouTube Studio
      ctx.clearRect(0, 0, W, H);
      const N = 60; pts = [];
      const top = grow(k) || 1e-6;  // масштаб по текущему максимуму: кривая всегда «упирается» вверх, как в студии
      for (let i = 0; i <= N; i++) { const x = i / N * k; pts.push([x / Math.max(k, .02) * W * Math.min(1, .25 + k), H - 6 - (H - 16) * (grow(x) / top * .88 + .04 + Math.sin(i * 1.7) * .015)]); }
      if (pts.length > 1) {
        const g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, 'rgba(62,166,255,.35)'); g.addColorStop(1, 'rgba(62,166,255,0)');
        ctx.beginPath(); ctx.moveTo(pts[0][0], H); pts.forEach(p => ctx.lineTo(p[0], p[1])); ctx.lineTo(pts[pts.length - 1][0], H); ctx.fillStyle = g; ctx.fill();
        ctx.beginPath(); pts.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.strokeStyle = '#3ea6ff'; ctx.lineWidth = 2.2; ctx.stroke();
        const e = pts[pts.length - 1]; ctx.fillStyle = '#3ea6ff'; ctx.beginPath(); ctx.arc(e[0], e[1], 3.5 + Math.sin(t * 6) * 1, 0, 6.28); ctx.fill();
      }
      // всплывающие лайки и «+подписчик»
      if (!RM && t - lastPop > .45 + Math.random() * .5) {
        lastPop = t; const el = document.createElement('span');
        const r = Math.random(); el.className = 'st-pop' + (r < .2 ? ' sub' : r < .35 ? ' heart' : '');
        el.textContent = r < .2 ? '+1 подписчик' : r < .35 ? '❤' : '👍';
        el.style.left = (10 + Math.random() * 80) + '%'; fx.append(el); setTimeout(() => el.remove(), 1700);
      }
    }
    return { frame, visible: () => onScreen(box) };
  }

  /* ================= ВИДЕО ПО УГЛАМ ================= */
  function cornerVideos() {
    const big = matchMedia('(min-width: 1500px)');
    const vids = [...document.querySelectorAll('.dec video')];
    if (!vids.length) return;
    let loaded = false;
    const sync = () => {
      if (!big.matches) { vids.forEach(v => v.pause()); return; }
      if (!loaded) {  // WebM (Chrome, Firefox, Edge) и MP4 (Safari) — браузер возьмёт подходящий
        vids.forEach(v => { v.innerHTML = `<source src="${v.dataset.src}.webm" type="video/webm"><source src="${v.dataset.src}.mp4" type="video/mp4">`; v.load(); });
        loaded = true;
      }
      vids.forEach(v => { if (RM || document.hidden) v.pause(); else v.play().catch(() => {}); });
    };
    sync(); big.addEventListener('change', sync); document.addEventListener('visibilitychange', sync);
  }

  function init() {
    cornerVideos();
    const p = document.getElementById('progress'), tp = document.getElementById('thProgress');
    if (p) { scenes.push(scanner(p, 'niche')); scenes.push(studio(p, document.getElementById('pPct'))); }
    if (tp) { scenes.push(scanner(tp, 'thumbs')); scenes.push(studio(tp, document.getElementById('thPct'))); }
    if (RM) {
      const still = () => scenes.forEach(s => s.visible() && s.frame(performance.now() / 1000, 0.5));
      setInterval(still, 1500); still();
    } else requestAnimationFrame(loop);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
