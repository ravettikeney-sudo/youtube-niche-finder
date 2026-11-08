/* Живые декорации: камин справа, комната с телевизором слева, анимация поиска вместо полоски.
   Всё рисуется кодом на canvas — без видео и тяжёлых файлов. Анимация идёт только когда её видно,
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

  /* ================= КАМИН ================= */
  function fireplace(canvas) {
    const W = 340, H = 380, ctx = setup(canvas, W, H);
    const box = { x: 92, y: 150, w: 156, h: 196 };           // топка
    const base = document.createElement('canvas'); base.width = W * DPR; base.height = H * DPR;
    const b = base.getContext('2d'); b.setTransform(DPR, 0, 0, DPR, 0, 0);
    // стена комнаты — тёплая штукатурка
    let g = b.createLinearGradient(0, 0, 0, H); g.addColorStop(0, '#2a1c16'); g.addColorStop(1, '#1a110d');
    b.fillStyle = g; b.fillRect(0, 0, W, H);
    for (let i = 0; i < 900; i++) { b.fillStyle = `rgba(255,220,190,${rnd(0, .025)})`; b.fillRect(rnd(0, W), rnd(0, H), 1.5, 1.5); }
    // каменный портал
    const sx = 38, sy = 86, sw = 264, sh = 270;
    b.fillStyle = '#3a2a22'; b.fillRect(sx, sy, sw, sh);
    const pal = ['#7b5a47', '#6c4c3c', '#86644f', '#5f4436', '#8f6d55', '#735341'];
    for (let y = sy + 2, row = 0; y < sy + sh; y += 23, row++) {
      for (let x = sx + 2 - (row % 2 ? 22 : 0); x < sx + sw; x += 44) {
        const bx = Math.max(sx + 2, x), bw = Math.min(x + 41, sx + sw - 2) - bx; if (bw < 6) continue;
        const gg = b.createLinearGradient(0, y, 0, y + 20); const c0 = pal[(row * 5 + x) % pal.length | 0] || pal[0];
        gg.addColorStop(0, c0); gg.addColorStop(1, '#3f2d24');
        b.fillStyle = gg; b.beginPath(); b.roundRect(bx, y, bw, 20, 3); b.fill();
        for (let k = 0; k < 14; k++) { b.fillStyle = `rgba(0,0,0,${rnd(.05, .18)})`; b.fillRect(bx + rnd(0, bw), y + rnd(0, 20), rnd(1, 3), rnd(1, 2)); }
      }
    }
    // полка-камин (дерево)
    g = b.createLinearGradient(0, 64, 0, 92); g.addColorStop(0, '#8a5a36'); g.addColorStop(.5, '#6d4426'); g.addColorStop(1, '#3d2414');
    b.fillStyle = g; b.beginPath(); b.roundRect(20, 66, 300, 24, 5); b.fill();
    b.fillStyle = 'rgba(255,210,160,.18)'; b.fillRect(24, 67, 292, 2);
    // ваза и рамка на полке
    b.fillStyle = '#2f4b5c'; b.beginPath(); b.ellipse(266, 52, 11, 15, 0, 0, Math.PI * 2); b.fill(); b.fillRect(261, 30, 10, 10);
    b.fillStyle = '#4f7a3b'; for (let i = 0; i < 5; i++) { b.beginPath(); b.ellipse(266 + (i - 2) * 6, 22 - Math.abs(i - 2) * 3, 4, 9, (i - 2) * .4, 0, Math.PI * 2); b.fill(); }
    b.fillStyle = '#5a3c26'; b.fillRect(52, 28, 46, 38); b.fillStyle = '#c9b08a'; b.fillRect(57, 33, 36, 28);
    b.fillStyle = '#7d9bb0'; b.beginPath(); b.moveTo(57, 61); b.lineTo(70, 44); b.lineTo(80, 54); b.lineTo(86, 47); b.lineTo(93, 61); b.fill();
    // топка: арка, закопчённая кладка внутри
    const arch = p => { p.beginPath(); p.moveTo(box.x, box.y + box.h); p.lineTo(box.x, box.y + 40); p.quadraticCurveTo(box.x, box.y, box.x + 40, box.y); p.lineTo(box.x + box.w - 40, box.y); p.quadraticCurveTo(box.x + box.w, box.y, box.x + box.w, box.y + 40); p.lineTo(box.x + box.w, box.y + box.h); p.closePath(); };
    b.save(); arch(b); b.clip();
    g = b.createLinearGradient(0, box.y, 0, box.y + box.h); g.addColorStop(0, '#0b0605'); g.addColorStop(1, '#24120b');
    b.fillStyle = g; b.fillRect(box.x, box.y, box.w, box.h);
    for (let y = box.y + 4; y < box.y + box.h; y += 16) for (let x = box.x + ((y / 16) % 2 ? -10 : 0); x < box.x + box.w; x += 30) { b.strokeStyle = 'rgba(60,30,20,.22)'; b.strokeRect(x, y, 28, 14); }
    b.restore();
    b.strokeStyle = '#2a1a12'; b.lineWidth = 5; arch(b); b.stroke();
    // под камина
    g = b.createLinearGradient(0, 346, 0, 372); g.addColorStop(0, '#8b7b70'); g.addColorStop(1, '#4e443e');
    b.fillStyle = g; b.beginPath(); b.roundRect(26, 346, 288, 24, 4); b.fill();

    // поленья (рисуются поверх огня, но под языками пламени)
    function logs(p, glow) {
      const L = [[118, 318, 104, 17, -.12], [128, 326, 112, 18, .1], [150, 312, 70, 14, 0]];
      for (const [x, y, w, h, a] of L) {
        p.save(); p.translate(x + w / 2, y + h / 2); p.rotate(a);
        const lg = p.createLinearGradient(0, -h / 2, 0, h / 2); lg.addColorStop(0, '#5a3a24'); lg.addColorStop(.6, '#3a2416'); lg.addColorStop(1, '#1d120b');
        p.fillStyle = lg; p.beginPath(); p.roundRect(-w / 2, -h / 2, w, h, h / 2); p.fill();
        p.fillStyle = `rgba(255,${110 + glow * 50 | 0},30,${.25 + glow * .25})`; p.fillRect(-w / 2 + 10, -h / 2 + 1, w - 20, 1.6);
        p.fillStyle = '#c99a6a'; p.beginPath(); p.ellipse(w / 2 - 2, 0, 4, h / 2 - 1, 0, 0, Math.PI * 2); p.fill();
        p.strokeStyle = '#8a643f'; p.lineWidth = 1; p.beginPath(); p.ellipse(w / 2 - 2, 0, 2, h / 4, 0, 0, Math.PI * 2); p.stroke();
        p.restore();
      }
      // угли
      for (let i = 0; i < 16; i++) { const x = 112 + (i * 37) % 116, y = 336 + (i * 13) % 8; p.fillStyle = `rgba(255,${90 + (i * 29) % 90},30,${(.4 + .6 * Math.abs(Math.sin(i * 1.7 + glow * 3))) * .9})`; p.beginPath(); p.arc(x, y, 2 + (i % 3), 0, 6.28); p.fill(); }
      // решётка
      p.strokeStyle = '#121010'; p.lineWidth = 3;
      for (let x = box.x + 14; x < box.x + box.w - 6; x += 16) { p.beginPath(); p.moveTo(x, 330); p.lineTo(x, 346); p.stroke(); }
      p.beginPath(); p.moveTo(box.x + 8, 332); p.lineTo(box.x + box.w - 8, 332); p.stroke();
    }

    // пламя: частицы на уменьшенном холсте (мягкие края сами собой), смешение «lighter»
    const FS = .5, fire = document.createElement('canvas'); fire.width = W * FS * DPR; fire.height = H * FS * DPR;
    const f = fire.getContext('2d'); f.setTransform(FS * DPR, 0, 0, FS * DPR, 0, 0);
    const SPR = [sprite(32, [[0, 'rgba(255,250,215,1)'], [.35, 'rgba(255,220,120,.85)'], [1, 'rgba(255,170,40,0)']]),
      sprite(32, [[0, 'rgba(255,190,70,.9)'], [.45, 'rgba(255,120,25,.55)'], [1, 'rgba(230,60,10,0)']]),
      sprite(32, [[0, 'rgba(235,85,25,.7)'], [.5, 'rgba(180,40,15,.35)'], [1, 'rgba(120,20,10,0)']])];
    const P = [], SP = [];
    function spawn() {
      const x = rnd(box.x + 22, box.x + box.w - 22), c = box.x + box.w / 2;
      const edge = Math.abs(x - c) / (box.w / 2);
      P.push({ x, y: rnd(310, 324), vx: rnd(-5, 5), vy: -rnd(85, 135) * (1 - edge * .4), life: 0, max: rnd(.55, 1.05) * (1 - edge * .35), s: rnd(8, 14) * (1 - edge * .3), ph: rnd(0, 6.28) });
    }
    let t0 = 0;
    function frame(t, dt) {
      const fl = .78 + .12 * Math.sin(t * 7.1) + .07 * Math.sin(t * 13.7 + 1.3) + .05 * Math.sin(t * 23.3);
      ctx.drawImage(base, 0, 0, W, H);
      // отсвет огня на камне, полке и полу
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      let lg = ctx.createRadialGradient(170, 300, 10, 170, 280, 230);
      lg.addColorStop(0, `rgba(255,140,50,${.32 * fl})`); lg.addColorStop(.5, `rgba(255,100,30,${.12 * fl})`); lg.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = lg; ctx.fillRect(0, 0, W, H);
      lg = ctx.createRadialGradient(170, 360, 4, 170, 360, 140); lg.addColorStop(0, `rgba(255,170,80,${.35 * fl})`); lg.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = lg; ctx.fillRect(0, 300, W, 80);
      ctx.restore();
      // частицы
      const n = RM ? 0 : Math.round(dt * 170);
      for (let i = 0; i < n; i++) spawn();
      if (RM && !P.length) for (let i = 0; i < 160; i++) { spawn(); const p = P[P.length - 1]; p.life = rnd(0, p.max * .9); p.y += p.vy * p.life; }
      f.clearRect(0, 0, W, H); f.globalCompositeOperation = 'lighter';
      const cx = box.x + box.w / 2;
      for (let i = P.length - 1; i >= 0; i--) {
        const p = P[i]; p.life += RM ? 0 : dt;
        const k = p.life / p.max; if (k >= 1) { P.splice(i, 1); continue; }
        p.vx += (cx - p.x) * 1.6 * dt + Math.sin(t * 5 + p.ph + p.y * .06) * 46 * dt; p.vx *= 1 - 1.5 * dt;
        p.x += p.vx * dt; p.y += p.vy * dt; p.vy -= 14 * dt;
        const sz = p.s * (1 - k * .75), spr = SPR[k < .28 ? 0 : k < .62 ? 1 : 2];
        f.globalAlpha = (k < .12 ? k / .12 : (1 - k)) * .62;
        f.drawImage(spr, p.x - sz, p.y - sz * 2.2, sz * 2, sz * 4.2);
      }
      f.globalAlpha = 1;
      // поленья поверх нижней части пламени
      ctx.save(); ctx.beginPath(); ctx.rect(box.x, box.y, box.w, box.h); ctx.clip();
      ctx.globalCompositeOperation = 'lighter'; ctx.drawImage(fire, 0, 0, W, H);
      ctx.globalCompositeOperation = 'source-over';
      logs(ctx, fl); ctx.globalCompositeOperation = 'lighter'; ctx.globalAlpha = .45; ctx.drawImage(fire, 0, 0, W, H);
      ctx.restore();
      // искры
      if (!RM && Math.random() < dt * 5) SP.push({ x: rnd(cx - 50, cx + 50), y: 310, vx: rnd(-15, 15), vy: -rnd(70, 140), life: 0, max: rnd(.8, 1.8) });
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      for (let i = SP.length - 1; i >= 0; i--) {
        const s = SP[i]; s.life += dt; if (s.life > s.max || s.y < box.y + 6) { SP.splice(i, 1); continue; }
        s.x += (s.vx + Math.sin(t * 9 + i) * 25) * dt; s.y += s.vy * dt;
        ctx.fillStyle = `rgba(255,${190 - s.life * 60 | 0},80,${1 - s.life / s.max})`; ctx.fillRect(s.x, s.y, 1.8, 1.8);
      }
      ctx.restore();
      // свечи на полке
      for (const [x, y] of [[118, 66], [218, 66]]) {
        ctx.fillStyle = '#efe6d6'; ctx.fillRect(x - 4, y - 22, 8, 22);
        const h = 9 + Math.sin(t * 11 + x) * 1.6;
        ctx.save(); ctx.globalCompositeOperation = 'lighter';
        const cg = ctx.createRadialGradient(x, y - 26, 1, x, y - 26, 26); cg.addColorStop(0, `rgba(255,200,110,${.45 * fl})`); cg.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = cg; ctx.fillRect(x - 30, y - 56, 60, 60);
        ctx.fillStyle = 'rgba(255,230,160,.95)'; ctx.beginPath(); ctx.ellipse(x + Math.sin(t * 7 + x) * .8, y - 24 - h / 2, 2.6, h / 2, 0, 0, Math.PI * 2); ctx.fill();
        ctx.restore();
      }
      t0 = t;
    }
    return { frame, visible: () => onScreen(canvas) };
  }

  /* ================= КОМНАТА С ТЕЛЕВИЗОРОМ ================= */
  function room(canvas) {
    const W = 340, H = 380, ctx = setup(canvas, W, H);
    loadPics(16);
    const base = document.createElement('canvas'); base.width = W * DPR; base.height = H * DPR;
    const b = base.getContext('2d'); b.setTransform(DPR, 0, 0, DPR, 0, 0);
    let g = b.createLinearGradient(0, 0, 0, 300); g.addColorStop(0, '#1f2438'); g.addColorStop(1, '#2b3252');
    b.fillStyle = g; b.fillRect(0, 0, W, 300);
    for (let x = 0; x < W; x += 18) { b.fillStyle = 'rgba(255,255,255,.018)'; b.fillRect(x, 0, 8, 300); }
    // окно с ночным небом
    g = b.createLinearGradient(0, 30, 0, 140); g.addColorStop(0, '#0d1633'); g.addColorStop(1, '#22305a');
    b.fillStyle = g; b.fillRect(18, 34, 62, 104);
    b.fillStyle = '#e9e4c9'; b.beginPath(); b.arc(60, 56, 9, 0, 6.28); b.fill();
    b.fillStyle = '#0d1633'; b.beginPath(); b.arc(64, 53, 8, 0, 6.28); b.fill();
    b.strokeStyle = '#3a3346'; b.lineWidth = 4; b.strokeRect(18, 34, 62, 104); b.beginPath(); b.moveTo(49, 34); b.lineTo(49, 138); b.moveTo(18, 86); b.lineTo(80, 86); b.stroke();
    b.fillStyle = '#5b2f3e'; b.fillRect(8, 26, 16, 124); b.fillRect(74, 26, 16, 124);
    // пол
    g = b.createLinearGradient(0, 296, 0, H); g.addColorStop(0, '#3a2a20'); g.addColorStop(1, '#22170f');
    b.fillStyle = g; b.fillRect(0, 296, W, H - 296);
    for (let x = -40; x < W + 40; x += 34) { b.strokeStyle = 'rgba(0,0,0,.25)'; b.beginPath(); b.moveTo(x, 296); b.lineTo(x - 30, H); b.stroke(); }
    b.fillStyle = '#121522'; b.fillRect(0, 292, W, 6);
    // тумба
    g = b.createLinearGradient(0, 252, 0, 296); g.addColorStop(0, '#5a3f2c'); g.addColorStop(1, '#2e1f15');
    b.fillStyle = g; b.beginPath(); b.roundRect(84, 254, 212, 40, 4); b.fill();
    b.fillStyle = 'rgba(0,0,0,.35)'; b.fillRect(98, 264, 86, 22); b.fillRect(196, 264, 86, 22);
    // цветок и торшер
    b.fillStyle = '#6a4a3a'; b.beginPath(); b.moveTo(300, 296); b.lineTo(332, 296); b.lineTo(326, 266); b.lineTo(306, 266); b.fill();
    b.fillStyle = '#2f5a3a'; for (let i = 0; i < 9; i++) { b.beginPath(); b.ellipse(316 + Math.sin(i * 2.1) * 14, 246 - i * 6, 5, 16, Math.sin(i * 1.3) * .9, 0, 6.28); b.fill(); }
    b.fillStyle = '#2a2a33'; b.fillRect(39, 150, 3, 146); b.beginPath(); b.moveTo(26, 150); b.lineTo(56, 150); b.lineTo(50, 126); b.lineTo(32, 126); b.fill();

    const tv = { x: 98, y: 132, w: 184, h: 112 };
    let cur = 0, nextAt = 0, fade = 1, prog = 0;
    function frame(t, dt) {
      // вся комната чуть выше центра: диван и человек не уходят в растушёванный край
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0); ctx.fillStyle = '#22170f'; ctx.fillRect(0, 0, W, H);
      ctx.translate(0, -40);
      ctx.drawImage(base, 0, 0, W, H);
      if (!RM && t > nextAt) { cur = (cur + 1) % Math.max(1, imgs.length); nextAt = t + 6; fade = 0; prog = 0; loadPics(16); }
      fade = Math.min(1, fade + dt * 2.5); prog += dt / 6;
      const fl = .8 + .12 * Math.sin(t * 2.3) + .08 * Math.sin(t * 9.7 + 2) + (fade < 1 ? (1 - fade) * .3 : 0);
      // телевизор
      ctx.fillStyle = '#0a0a0d'; ctx.beginPath(); ctx.roundRect(tv.x - 5, tv.y - 5, tv.w + 10, tv.h + 10, 6); ctx.fill();
      ctx.fillStyle = '#16161c'; ctx.fillRect(tv.x + tv.w / 2 - 18, tv.y + tv.h + 5, 36, 6);
      const sx = tv.x, sy = tv.y, sw = tv.w, sh = tv.h;
      ctx.fillStyle = '#05060a'; ctx.fillRect(sx, sy, sw, sh);
      const a = imgs[cur], pIm = imgs[(cur + imgs.length - 1) % Math.max(1, imgs.length)];
      if (ready(pIm) && fade < 1) { ctx.globalAlpha = 1 - fade; ctx.drawImage(pIm, sx, sy, sw, sh); }
      if (ready(a)) { ctx.globalAlpha = fade; ctx.drawImage(a, sx, sy, sw, sh); }
      ctx.globalAlpha = 1;
      // интерфейс плеера
      ctx.fillStyle = 'rgba(0,0,0,.35)'; ctx.fillRect(sx, sy + sh - 9, sw, 9);
      ctx.fillStyle = 'rgba(255,255,255,.3)'; ctx.fillRect(sx + 6, sy + sh - 5, sw - 12, 2);
      ctx.fillStyle = '#ff2b2b'; ctx.fillRect(sx + 6, sy + sh - 5, (sw - 12) * Math.min(1, prog), 2);
      ctx.beginPath(); ctx.arc(sx + 6 + (sw - 12) * Math.min(1, prog), sy + sh - 4, 3, 0, 6.28); ctx.fill();
      // блик стекла
      const gl = ctx.createLinearGradient(sx, sy, sx + sw, sy + sh); gl.addColorStop(0, 'rgba(255,255,255,.10)'); gl.addColorStop(.4, 'rgba(255,255,255,0)');
      ctx.fillStyle = gl; ctx.fillRect(sx, sy, sw, sh);
      // свет экрана на комнату
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      let lg = ctx.createRadialGradient(190, 190, 20, 190, 230, 240);
      lg.addColorStop(0, `rgba(130,170,255,${.26 * fl})`); lg.addColorStop(.6, `rgba(90,120,230,${.10 * fl})`); lg.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = lg; ctx.fillRect(0, 0, W, H);
      ctx.restore();
      // диван (спинка) и человек со спины
      const br = Math.sin(t * 1.5) * 1.2, tilt = Math.sin(t * .37) * .05 + (Math.sin(t * .11) > .92 ? .12 : 0);
      ctx.save(); ctx.translate(170, 300 + br);
      ctx.fillStyle = '#121019'; ctx.beginPath(); ctx.ellipse(0, 18, 56, 26, 0, Math.PI, 0); ctx.fill();      // плечи
      ctx.save(); ctx.rotate(tilt); ctx.fillStyle = '#16121a'; ctx.beginPath(); ctx.ellipse(0, -18, 21, 24, 0, 0, 6.28); ctx.fill();
      ctx.strokeStyle = `rgba(150,180,255,${.45 * fl})`; ctx.lineWidth = 2; ctx.beginPath(); ctx.ellipse(0, -18, 21, 24, 0, Math.PI * 1.08, Math.PI * 1.92); ctx.stroke();
      ctx.restore();
      ctx.strokeStyle = `rgba(150,180,255,${.35 * fl})`; ctx.beginPath(); ctx.ellipse(0, 18, 56, 26, 0, Math.PI * 1.1, Math.PI * 1.9); ctx.stroke();
      ctx.restore();
      let sg = ctx.createLinearGradient(0, 312, 0, H); sg.addColorStop(0, '#4a3a63'); sg.addColorStop(1, '#251d33');
      ctx.fillStyle = sg; ctx.beginPath(); ctx.roundRect(14, 316, 312, 80, 26); ctx.fill();
      ctx.fillStyle = `rgba(160,180,255,${.18 * fl})`; ctx.beginPath(); ctx.roundRect(22, 317, 296, 5, 3); ctx.fill();
      ctx.fillStyle = '#3d2f52'; ctx.beginPath(); ctx.roundRect(4, 304, 46, 80, 18); ctx.fill(); ctx.beginPath(); ctx.roundRect(290, 304, 46, 80, 18); ctx.fill();
      // кот на подлокотнике, хвост покачивается
      ctx.fillStyle = '#0f0d14';
      ctx.beginPath(); ctx.ellipse(310, 300, 17, 10, 0, 0, 6.28); ctx.fill();
      ctx.beginPath(); ctx.arc(298, 290, 8, 0, 6.28); ctx.fill();
      ctx.beginPath(); ctx.moveTo(292, 285); ctx.lineTo(293, 276); ctx.lineTo(298, 283); ctx.moveTo(300, 283); ctx.lineTo(304, 276); ctx.lineTo(305, 286); ctx.fill();
      const tw = Math.sin(t * 2.2) * 9;
      ctx.strokeStyle = '#0f0d14'; ctx.lineWidth = 4; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(326, 302); ctx.quadraticCurveTo(338, 292, 334 + tw * .3, 278 + Math.abs(tw) * .2); ctx.stroke();
      ctx.strokeStyle = `rgba(150,180,255,${.3 * fl})`; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(298, 290, 8, Math.PI * 1.1, Math.PI * 1.7); ctx.stroke();
    }
    return { frame, visible: () => onScreen(canvas) };
  }

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
      const w = Math.round(holder.clientWidth - 48);
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

  function init() {
    const big = matchMedia('(min-width: 1500px)');
    const fc = document.getElementById('decFire'), rc = document.getElementById('decRoom');
    if (fc && rc) {
      let on = false;
      const start = () => { if (on || !big.matches) return; on = true; scenes.push(fireplace(fc), room(rc)); };
      start(); big.addEventListener('change', start);
    }
    const p = document.getElementById('progress'), tp = document.getElementById('thProgress');
    if (p) scenes.push(scanner(p, 'niche'));
    if (tp) scenes.push(scanner(tp, 'thumbs'));
    if (RM) { // один неподвижный кадр и перерисовка по мере появления
      const still = () => scenes.forEach(s => s.visible() && s.frame(performance.now() / 1000, 0));
      setInterval(still, 1500); still();
    } else requestAnimationFrame(loop);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
