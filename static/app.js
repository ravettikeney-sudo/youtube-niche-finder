/* Niche Finder — интерфейс */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const S = { key: null, me: null, data: null, job: null, filter: 'young', view: 'cards', sort: 'rising_score', dir: -1,
            codes: [], codeFilter: 'all', codeLimit: 25, days: 1, publicUrl: null, chart: null };
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ---------- утилиты */
const store = {
  get(k) { try { return localStorage.getItem(k) } catch { return null } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v) } catch {} }
};
const nf = new Intl.NumberFormat('ru', { notation: 'compact', maximumFractionDigits: 1 });
const fmt = n => n == null ? '—' : nf.format(n);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const plural = (n, a, b, c) => { n = Math.abs(n) % 100; const d = n % 10; return n > 10 && n < 20 ? c : d > 1 && d < 5 ? b : d === 1 ? a : c };
const daysTxt = d => !d ? 'бессрочно' : d < 1 ? `${Math.max(1, Math.round(d * 24))} ч` : d === 1 ? 'сутки' : Number.isInteger(d) ? `${d} ${plural(d, 'день', 'дня', 'дней')}` : `${String(+d.toFixed(1)).replace('.', ',')} дн.`;
const dec = v => v == null ? '—' : String(+(+v).toFixed(v >= 10 ? 0 : 2)).replace('.', ',');
const ageTxt = d => d == null ? '—' : d < 60 ? `${d} ${plural(d, 'день', 'дня', 'дней')}` : d < 730 ? `${Math.round(d / 30)} мес.` : `${(d / 365).toFixed(1).replace('.', ',')} г.`;
const dt = t => t ? new Date(t * 1000).toLocaleString('ru', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—';
const tone = s => s >= 60 ? 'hi' : s >= 40 ? 'mid' : 'lo';
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const toneColor = s => css('--' + tone(s));
function toast(m) { const t = $('#toast'); t.textContent = m; t.classList.add('show'); clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove('show'), 2200) }
async function copy(text, msg = 'Скопировано') {
  try { await navigator.clipboard.writeText(text) } catch {
    const a = document.createElement('textarea'); a.value = text; document.body.append(a); a.select(); document.execCommand('copy'); a.remove()
  }
  toast(msg)
}
function countUp(el, to, ms = 1200) {
  if (reduce) { el.textContent = to; return }
  const t0 = performance.now();
  const step = t => { const p = Math.min(1, (t - t0) / ms); el.textContent = Math.round(to * (1 - Math.pow(1 - p, 3))); if (p < 1) requestAnimationFrame(step) };
  requestAnimationFrame(step)
}

/* ---------- API */
async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { 'Content-Type': 'application/json', 'X-Key': S.key || '', ...(opts.headers || {}) } });
  let body = null; try { body = await r.json() } catch {}
  if (r.status === 401 && path !== '/api/login') { logout('Срок ключа истёк или ключ отозван. Введите новый ключ.'); throw new Error('auth') }
  if (!r.ok) throw new Error(body?.detail || `Ошибка ${r.status}`);
  return body
}

/* ---------- вход */
async function login(key, quiet) {
  const me = await api('/api/login', { method: 'POST', body: JSON.stringify({ key }) });
  S.key = key; S.me = me; store.set('nf_key', key);
  enterApp(); if (!quiet) toast(me.role === 'admin' ? 'Вы вошли как администратор' : 'Доступ открыт')
}
function logout(msg) {
  store.set('nf_key', null); S.key = null; S.me = null;
  $('#app').classList.add('hidden'); $('#login').classList.remove('hidden');
  $('#loginErr').textContent = msg || ''; $('#keyInput').value = ''; $('#keyInput').focus()
}
$('#loginForm').onsubmit = async e => {
  e.preventDefault(); const k = $('#keyInput').value.trim(); $('#loginBtn').disabled = true; $('#loginErr').textContent = '';
  try { await login(k) } catch (x) {
    $('#loginErr').textContent = x.message; $('.login-card').classList.remove('shake'); void $('.login-card').offsetWidth; $('.login-card').classList.add('shake')
  } finally { $('#loginBtn').disabled = false }
};
$('#keyInput').oninput = e => { const p = e.target.selectionStart; e.target.value = e.target.value.toUpperCase(); e.target.setSelectionRange(p, p) };
$('#logout').onclick = () => logout();

function enterApp() {
  $('#login').classList.add('hidden'); $('#app').classList.remove('hidden');
  const b = $('#whoBadge');
  if (S.me.role === 'admin') { b.textContent = 'Администратор · бессрочно'; b.className = 'badge admin'; $$('.admin-only').forEach(x => x.classList.remove('hidden')) }
  else { tickBadge(); clearInterval(S.tick); S.tick = setInterval(tickBadge, 60000); $$('.admin-only').forEach(x => x.classList.add('hidden')) }
}
function tickBadge() {
  if (!S.me.expires_at) { $('#whoBadge').textContent = 'Доступ бессрочный'; $('#whoBadge').className = 'badge'; return }
  const left = (S.me.expires_at || 0) - Date.now() / 1000, b = $('#whoBadge');
  if (left <= 0) return logout('Срок вашего ключа истёк. Попросите новый ключ.');
  const d = Math.floor(left / 86400), h = Math.floor(left % 86400 / 3600), m = Math.floor(left % 3600 / 60);
  b.textContent = 'Доступ ещё ' + (d ? `${d} д ${h} ч` : h ? `${h} ч ${m} мин` : `${m} мин`);
  b.className = 'badge'
}

/* ---------- разделы */
function show(v) {
  $$('.view').forEach(x => x.classList.toggle('hidden', x.id !== 'v-' + v));
  $$('nav.tabs button').forEach(b => b.dataset.go === v ? b.setAttribute('aria-current', 'page') : b.removeAttribute('aria-current'));
  if (v === 'history') loadHistory();
  if (v === 'admin') loadAdmin();
  window.scrollTo({ top: 0 })
}
$$('[data-go]').forEach(b => b.onclick = e => { e.preventDefault(); show(b.dataset.go) });

/* ---------- анализ */
$('#searchForm').onsubmit = async e => {
  e.preventDefault(); $('#runErr').textContent = ''; $('#goBtn').disabled = true;
  $('#result').classList.add('hidden'); setProgress(1, 'Отправляю запрос…');
  try {
    const r = await api('/api/analyze', { method: 'POST', body: JSON.stringify({
      url: $('#url').value.trim(), keywords: $('#kw').value.trim() || null,
      max_channels: +$('#mc').value || 30, per_query: +$('#pq').value || 25, max_age_days: +$('#age').value || 730 }) });
    S.job = r.job_id; history.replaceState(null, '', '?job=' + S.job); poll()
  } catch (x) { if (x.message !== 'auth') fail(x.message) }
};
function setProgress(p, msg) {
  $('#progress').classList.remove('hidden');
  const stage = p < 8 ? 0 : p < 15 ? 1 : p < 40 ? 2 : p < 96 ? 3 : 4;
  $$('#steps li').forEach((li, i) => li.className = i < stage ? 'done' : i === stage ? 'now' : '');
  $('#pMsg').textContent = msg; $('#pPct').textContent = Math.round(p) + '%'
}
function fail(m) { $('#runErr').textContent = m; $('#goBtn').disabled = false; $('#progress').classList.add('hidden') }
async function poll() {
  try {
    const j = await api('/api/job/' + S.job);
    if (j.status === 'done') { $('#goBtn').disabled = false; $('#progress').classList.add('hidden'); render(j.result) }
    else if (j.status === 'error') fail(j.error);
    else { setProgress(j.progress, j.message); setTimeout(poll, 1500) }
  } catch (x) { if (x.message !== 'auth') fail(x.message) }
}
function openReport(id) { S.job = id; history.replaceState(null, '', '?job=' + id); show('analyze'); $('#result').classList.add('hidden'); setProgress(0, 'Открываю отчёт…'); poll() }

/* ---------- отрисовка отчёта */
function render(d) {
  S.data = d; $('#result').classList.remove('hidden');
  const n = d.niche, sc = n.scores;
  if (!$('#url').value) $('#url').value = d.competitor.url;

  // кольцо
  const arc = $('#ringArc'), C = 540.35;
  arc.style.stroke = toneColor(n.overall); arc.style.strokeDashoffset = C;
  requestAnimationFrame(() => requestAnimationFrame(() => arc.style.strokeDashoffset = C * (1 - n.overall / 100)));
  countUp($('#ovNum'), n.overall);
  $('#verdict').textContent = n.verdict;

  const M = [
    ['Спрос', sc.demand, `обычно ${fmt(n.median_search_views)} просмотров на видео`],
    ['Конкуренция', sc.competition, `каналов-гигантов ${n.big_channels_share}%`, true],
    ['Окно входа', sc.entry_window, `растут ${n.young_rising} молодых из ${n.analyzed}`],
    ['Монетизация', sc.monetization, `${n.rpm_category}: ~$${n.rpm_range[0]}–${n.rpm_range[1]} за 1000`],
    ['Свежесть', sc.freshness, `молодых каналов: ${n.young}`]];
  $('#meters').innerHTML = M.map(([t, v, h, inv]) => `<div class="meter"><div class="name">${t}<small>${esc(h)}</small></div>
    <div class="track"><div class="fill" data-w="${v}" style="background:${toneColor(inv ? 100 - v : v)}"></div></div><div class="num">${v}</div></div>`).join('');
  setTimeout(() => $$('#meters .fill').forEach((f, i) => setTimeout(() => f.style.width = f.dataset.w + '%', reduce ? 0 : i * 110)), 60);

  // конкурент
  const c = d.competitor;
  const eng = c.engagement || {};
  $('#comp').innerHTML = `<div class="banner${c.banner ? '' : ' empty'}" style="${c.banner ? `background-image:url('${esc(c.banner)}')` : ''}"></div>
    <div class="body">${avatar(c, 'ava')}
      <div style="flex:1;min-width:240px"><h3><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a></h3>
      <div class="tags">${d.keywords.map(k => `<span class="tag">${esc(k)}</span>`).join('')}</div>
      ${c.description ? `<p class="desc">${esc(c.description.split('\n')[0].slice(0, 220))}</p>` : ''}</div>
      <div class="facts">
        ${fact('Подписчики', fmt(c.subs))}${fact('Обычно просмотров', fmt(c.median_views))}
        ${fact('Возраст', (c.age_exact ? '' : 'более ') + ageTxt(c.age_days))}${fact('Видео в неделю', dec(c.uploads_per_week))}
        ${c.avg_duration ? fact('Длина видео', durTxt(c.avg_duration)) : ''}${c.trend ? fact('Тренд', trendTxt(c.trend)) : ''}
        ${c.income ? fact('Доход в месяц', incTxt(c.income)) : ''}${eng.likes_pct ? fact('Лайки', dec(eng.likes_pct) + '%') : ''}
        ${eng.comments_per_1k ? fact('Комментарии', dec(eng.comments_per_1k) + ' на 1000') : ''}
      </div></div>`;

  d.channels.forEach(x => x._inc = x.income ? x.income[1] : null);
  renderPlan(d); renderPulse(d);
  drawMap(d);
  const off = d.dynamics?.off_topic || 0;
  $('#chLead').textContent = `Проверено ${n.analyzed} ${plural(n.analyzed, 'канал', 'канала', 'каналов')}: в нише — ${n.analyzed - off}, из них молодых — ${n.young}, растущих молодых — ${n.young_rising}.` + (off ? ` Ещё ${off} нашлись по запросам, но по темам не похожи на конкурента — они отмечены «вне ниши» и не влияют на оценки.` : '');
  renderChannels();

  $('#topics').innerHTML = d.entry_topics.length ? d.entry_topics.slice(0, 10).map(v => `
    <a class="vid" href="https://www.youtube.com/watch?v=${esc(v.id)}" target="_blank" rel="noopener">
      <img loading="lazy" src="https://i.ytimg.com/vi/${esc(v.id)}/mqdefault.jpg" alt="">
      <div><b>${esc(v.title)}</b><small>${esc(v.channel)} · ${fmt(v.subs)} подп.<br>${fmt(v.views)} просмотров — в ${String(v.x_median).replace('.', ',')} раза больше обычного</small></div></a>`).join('')
    : `<div class="empty">У молодых каналов этой ниши пока нет роликов-хитов. Попробуйте другие ключевые слова.</div>`;

  const maxV = Math.max(1, ...d.keyword_table.map(k => k.median_views));
  $('#kwTable').innerHTML = `<tr><th>Запрос</th><th>Оценка</th><th>Просмотры</th><th class="r">Молодых</th></tr>` +
    d.keyword_table.map(k => `<tr><td><a href="https://www.youtube.com/results?search_query=${encodeURIComponent(k.keyword)}" target="_blank" rel="noopener">${esc(k.keyword)}</a></td>
      <td><span class="pill ${tone(k.score)}">${k.score}</span></td>
      <td><div class="inbar"><span style="width:${Math.max(4, k.median_views / maxV * 90)}px"></span>${fmt(k.median_views)}</div></td>
      <td class="r">${k.young_share}%</td></tr>`).join('');

  renderDur(d); renderTitles(d); renderSearches(d);
  $('#genAt').textContent = new Date(d.generated_at).toLocaleString('ru', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
  $('#result').scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
}
const fact = (l, v) => `<div class="fact"><small>${l}</small><b>${v}</b></div>`;
function avatar(c, cls) {
  const ini = esc((c.title || '?').trim()[0] || '?');
  return c.avatar ? `<img class="${cls}" src="${esc(c.avatar)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.outerHTML='<div class=&quot;${cls}&quot;>${ini}</div>'">`
    : `<div class="${cls}">${ini}</div>`
}

const durTxt = s => s == null ? '—' : s < 60 ? `${s} сек` : s < 3600 ? `${Math.round(s / 60)} мин` : `${Math.floor(s / 3600)} ч ${Math.round(s % 3600 / 60)} мин`;
const trendTxt = t => t >= 1.3 ? `↑ ×${dec(t)}` : t <= 0.75 ? `↓ ×${dec(t)}` : '→ ровно';
const incTxt = i => i ? `$${fmt(i[0])}–${fmt(i[1])}` : '—';
const LANGS = { ru: 'русский', uk: 'украинский', en: 'английский', es: 'испанский', pt: 'португальский', de: 'немецкий', fr: 'французский', it: 'итальянский', tr: 'турецкий', hi: 'хинди', ar: 'арабский', zh: 'китайский', ja: 'японский', ko: 'корейский', other: 'другой' };
const PLAN_ICONS = [['Формат', '▶'], ['Частота', '⟳'], ['Ориентир', '◎'], ['Длина', '⏱'], ['Заголовки', 'Aa'], ['Свободные темы', '✦'], ['Запросы зрителей', '⌕'], ['Лучший запрос', '★'], ['Первое видео', '●'], ['Доход', '$'], ['Внимание', '!']];

function renderPlan(d) {
  const plan = d.plan || [];
  $('#plan').closest('.sec').classList.toggle('hidden', !plan.length);
  $('#plan').innerHTML = plan.map(t => {
    const ic = (PLAN_ICONS.find(([k]) => t.startsWith(k)) || ['', '•'])[1];
    const i = t.indexOf(':');
    const body = i > 0 && i < 40 ? `<b>${esc(t.slice(0, i))}:</b>${esc(t.slice(i + 1))}` : esc(t);
    return `<li><i aria-hidden="true">${ic}</i><span>${body}</span></li>`
  }).join('')
}
function renderPulse(d) {
  const y = d.dynamics, n = d.niche;
  $('#pulse').closest('.sec').classList.toggle('hidden', !y);
  if (!y) return;
  const tile = (label, val, note) => `<div class="stat"><small>${label}</small><b>${val}</b><p>${note}</p></div>`;
  $('#pulse').innerHTML = [
    tile('Свежие видео', fmt(y.month_median_views), `обычно столько просмотров у роликов за последний месяц · ${y.month_videos} видео от ${y.month_channels} каналов`),
    tile('Каналы ускоряются', `${y.accelerating} из ${y.trend_known}`, `набирают больше, чем раньше; замедляются — ${y.slowing}`),
    tile('Частота растущих', y.upw_young != null ? dec(y.upw_young) : '—', 'видео в неделю выпускают растущие новички'),
    tile('Доход новичка', n.income_young ? incTxt(n.income_young) : '—', 'в месяц с рекламы у растущего молодого канала, грубая оценка'),
    tile('Лучшая длина', n.best_duration || '—', 'видео такой длины работают лучше остальных'),
    `<div class="stat"><small>Языки ниши</small><div class="langs">${(y.languages || []).map(l => `<span class="pill grey">${esc(LANGS[l.code] || l.name)} ${l.share}%</span>`).join('')}</div><p>язык конкурента — ${esc((y.comp_lang || '').toLowerCase())}</p></div>`
  ].join('')
}
function renderDur(d) {
  const rows = d.durations || [];
  $('#durChart').closest('.sec').classList.toggle('hidden', !rows.length);
  if (!rows.length) return;
  if (S.dur) S.dur.destroy();
  const best = d.niche.best_duration, hi = css('--hi'), primary = css('--primary'), faint = css('--faint'), muted = css('--muted'), line = css('--line'), ink = css('--ink');
  const one = { id: 'one', afterDatasetsDraw(ch) { const { ctx, chartArea: a, scales: { y } } = ch; const py = y.getPixelForValue(1); ctx.save(); ctx.strokeStyle = muted; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(a.left, py); ctx.lineTo(a.right, py); ctx.stroke(); ctx.restore() } };
  S.dur = new Chart($('#durChart'), {
    type: 'bar',
    data: { labels: rows.map(r => r.bucket), datasets: [{ data: rows.map(r => r.perf ?? 0), borderRadius: 6,
      backgroundColor: rows.map(r => r.bucket === best ? hi : (r.perf ?? 0) >= 1 ? primary : faint + '88') }] },
    options: { maintainAspectRatio: false, animation: reduce ? false : { duration: 900, delay: c => c.dataIndex * 80 },
      scales: { x: { grid: { display: false }, ticks: { color: muted }, border: { display: false } },
                y: { beginAtZero: true, grid: { color: line }, ticks: { color: muted, callback: v => '×' + dec(v) }, border: { display: false } } },
      plugins: { legend: { display: false }, tooltip: { backgroundColor: ink, displayColors: false, padding: 10,
        callbacks: { label: it => { const r = rows[it.dataIndex]; return r.videos ? [`×${dec(r.perf)} к обычным просмотрам`, `видео: ${r.videos}`, `хиты: ${r.hit_share}%`] : 'нет видео такой длины' } } } } },
    plugins: [one]
  });
  $('#durNote').textContent = best ? `Лучше всего работают видео ${best}. Пунктир — обычный уровень просмотров канала.` : 'Пока мало данных, чтобы выбрать лучшую длину.'
}
function renderTitles(d) {
  const t = d.titles;
  $('#titleBox').closest('.sec').classList.toggle('hidden', !t);
  if (!t) return;
  const f = (t.features || []).slice(0, 7);
  const bar = l => { const w = Math.min(l, 2) / 2 * 100, c = l >= 1.1 ? 'var(--hi)' : l <= 0.9 ? 'var(--lo)' : 'var(--faint)'; return `<div class="bar"><span style="width:${w}%;background:${c}"></span></div>` };
  $('#titleBox').innerHTML = (f.length ? f.map(x => `<div class="feat"><div>${esc(x.name)}<small>в ${x.share}% заголовков · хитов ${x.hit_rate}%</small></div>${bar(x.lift)}<div class="x">×${dec(x.lift)}</div></div>`).join('')
      : '<p class="hint">Недостаточно видео для анализа заголовков.</p>') +
    (t.len_hit ? `<p class="hint" style="margin-top:12px">Длина заголовка у хитов — около ${t.len_hit} символов (у всех — ${t.len_all}). Хитом становится ${t.base_hit_rate}% роликов ниши.</p>` : '') +
    (t.hit_words?.length ? `<div class="wordset"><h4>Слова, которые чаще встречаются у хитов</h4><div class="tags">${t.hit_words.slice(0, 16).map(w => `<span class="tag" title="у хитов в ${dec(w.lift)} раза чаще">${esc(w.word)}</span>`).join('')}</div></div>` : '') +
    (t.gaps?.length ? `<div class="wordset"><h4>Этих тем нет у конкурента — свободное место</h4><div class="tags">${t.gaps.map(w => `<span class="pill blue">${esc(w.word)}</span>`).join('')}</div></div>` : '')
}
function renderSearches(d) {
  const g = d.searches || [];
  $('#searches').closest('.sec').classList.toggle('hidden', !g.length);
  $('#searches').innerHTML = g.map(x => `<div class="sgrp"><h4>${esc(x.keyword)}</h4><div class="tags">${x.queries.map(q => `<a href="https://www.youtube.com/results?search_query=${encodeURIComponent(q)}" target="_blank" rel="noopener">${esc(q)}</a>`).join('')}</div></div>`).join('')
}

/* ---------- карта ниши */
function drawMap(d) {
  if (S.chart) S.chart.destroy();
  const ch = d.channels.filter(c => c.age_days != null && c.views_per_day && c.relevant !== false);
  const maxSubs = Math.max(1, ...ch.map(c => c.subs || 1));
  const r = s => 5 + 20 * Math.sqrt((s || 1) / maxSubs);
  const vpds = ch.map(c => c.views_per_day).sort((a, b) => a - b);
  const medV = vpds[Math.floor(vpds.length / 2)] || 100;
  const maxAge = d.niche.max_age_days || 730;
  const ink = css('--ink'), muted = css('--muted'), line = css('--line'), win = css('--window'), hi = css('--hi'), primary = css('--primary');
  const old = maxAge * 1.08; // каналы с неизвестным точным возрастом — справа от окна входа
  const pts = ch.map((c, i) => ({ x: c.age_exact ? c.age_days : Math.max(c.age_days, old + (i * 37 % 100) / 100 * maxAge * .35), y: c.views_per_day, r: r(c.subs), c }));
  const comp = d.competitor;
  const compPt = comp.age_days && comp.views_per_day ? [{ x: comp.age_exact ? comp.age_days : Math.max(comp.age_days, old), y: comp.views_per_day, r: r(comp.subs), c: comp }] : [];

  const zone = { id: 'zone', beforeDatasetsDraw(chart) {
    const { ctx, chartArea: a, scales: { x, y } } = chart;
    const x1 = Math.min(x.getPixelForValue(maxAge), a.right), y1 = Math.max(y.getPixelForValue(medV), a.top);
    ctx.save(); ctx.fillStyle = win; ctx.fillRect(a.left, a.top, x1 - a.left, y1 - a.top);
    ctx.strokeStyle = hi; ctx.setLineDash([5, 5]); ctx.lineWidth = 1; ctx.strokeRect(a.left, a.top, x1 - a.left, y1 - a.top);
    ctx.setLineDash([]); ctx.fillStyle = hi; ctx.font = `600 13px ${getComputedStyle(document.body).fontFamily}`;
    ctx.fillText('Окно входа', a.left + 10, a.top + 20); ctx.restore()
  } };
  S.chart = new Chart($('#map'), {
    type: 'bubble',
    data: { datasets: [
      { data: pts, backgroundColor: pts.map(p => p.c.age_exact ? toneColor(p.c.rising_score) + 'B3' : toneColor(p.c.rising_score) + '1F'), borderColor: pts.map(p => toneColor(p.c.rising_score)), borderWidth: pts.map(p => p.c.age_exact ? 1.5 : 2), borderDash: [], hoverBorderWidth: 3 },
      { data: compPt, backgroundColor: 'transparent', borderColor: primary, borderWidth: 3 }] },
    options: {
      maintainAspectRatio: false,
      animation: reduce ? false : { duration: 700, delay: ctx => ctx.type === 'data' && ctx.mode === 'default' ? 250 + ctx.dataIndex * 45 : 0 },
      animations: reduce ? {} : { radius: { from: 0 } },
      layout: { padding: 6 },
      scales: {
        x: { title: { display: true, text: 'Возраст канала, дней', color: muted }, min: 0, grid: { color: line }, ticks: { color: muted }, border: { display: false } },
        y: { type: 'logarithmic', title: { display: true, text: 'Просмотров в день', color: muted }, grid: { color: line }, border: { display: false },
             ticks: { color: muted, callback: v => [1, 10, 100, 1e3, 1e4, 1e5, 1e6, 1e7].includes(v) ? fmt(v) : '' } } },
      plugins: { legend: { display: false },
        tooltip: { backgroundColor: ink, titleColor: css('--bg'), bodyColor: css('--bg'), padding: 12, displayColors: false,
          callbacks: { title: it => it[0].raw.c.title, label: it => { const c = it.raw.c; return [`Оценка: ${c.rising_score ?? '—'}`, `Возраст: ${(c.age_exact ? '' : 'более ') + ageTxt(c.age_days)}`, `Подписчики: ${fmt(c.subs)}`, `${fmt(c.views_per_day)} просмотров в день`] } } } },
      onClick: (e, els) => { if (!els.length || els[0].datasetIndex) return; focusCard(pts[els[0].index].c.id) },
      onHover: (e, els) => e.native.target.style.cursor = els.length ? 'pointer' : 'default'
    },
    plugins: [zone]
  })
}
function focusCard(id) {
  if (S.view !== 'cards') setView('cards');
  let el = document.getElementById('ch-' + id);
  if (!el) { setFilter('all'); el = document.getElementById('ch-' + id) }
  if (!el) return;
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' });
  el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 1600)
}

/* ---------- каналы */
function filtered() {
  const ch = S.data.channels;
  const inN = c => c.relevant !== false;
  return S.filter === 'young' ? ch.filter(c => inN(c) && c.young) : S.filter === 'rising' ? ch.filter(c => inN(c) && c.rising_score >= 60)
    : S.filter === 'niche' ? ch.filter(inN) : ch
}
function setFilter(f) { S.filter = f; $$('.chips [data-f]').forEach(b => b.setAttribute('aria-pressed', b.dataset.f === f)); renderChannels() }
function setView(v) { S.view = v; $$('[data-view]').forEach(b => b.setAttribute('aria-pressed', b.dataset.view === v)); $('#cards').classList.toggle('hidden', v !== 'cards'); $('#tableWrap').classList.toggle('hidden', v !== 'table'); renderChannels() }
$$('.chips [data-f]').forEach(b => b.onclick = () => setFilter(b.dataset.f));
$$('[data-view]').forEach(b => b.onclick = () => setView(b.dataset.view));

function miniRing(s) {
  const C = 2 * Math.PI * 21;
  return `<div class="mini" title="Оценка роста"><svg width="52" height="52" viewBox="0 0 52 52"><circle cx="26" cy="26" r="21" fill="none" stroke="var(--sunk)" stroke-width="5"/>
    <circle cx="26" cy="26" r="21" fill="none" stroke="var(--${tone(s)})" stroke-width="5" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - s / 100)}"/></svg><b>${s}</b></div>`
}
function spark(vals) {
  if (!vals || vals.length < 2) return '';
  const w = 280, h = 40, mx = Math.max(...vals.map(v => Math.log10(v + 1)), 1);
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * w).toFixed(1)},${(h - 3 - Math.log10(v + 1) / mx * (h - 6)).toFixed(1)}`).join(' ');
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-label="Просмотры последних видео"><polyline points="${pts}" fill="none" stroke="var(--primary)" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>
    <div class="spark-l"><span>раньше</span><span>просмотры последних ${vals.length} видео</span><span>сейчас</span></div>`
}
function renderChannels() {
  const rows = [...filtered()].sort((a, b) => { const x = a[S.sort], y = b[S.sort]; if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * S.dir });
  if (!rows.length) {
    const msg = `<div class="empty">Под этот фильтр каналов нет. Выберите «Вся ниша».</div>`;
    $('#cards').innerHTML = msg; $('#chTable').innerHTML = `<tr><td>${msg}</td></tr>`; return
  }
  if (S.view === 'cards') {
    $('#cards').innerHTML = rows.map(c => `<article class="card${c.relevant === false ? ' off' : ''}" id="ch-${esc(c.id)}">
      <div class="head">${avatar(c, 'ava')}<div class="t"><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a>
        <small><span class="pill ${tone(c.rising_score)}">${esc(c.label)}</span>${c.relevant === false ? ' <span class="pill grey">вне ниши</span>' : c.young ? ' <span class="pill blue">молодой</span>' : ''}${c.format && c.format !== 'videos' ? ` <span class="pill grey">${c.format === 'shorts' ? 'Shorts' : 'стримы'}</span>` : ''}</small></div>
        ${miniRing(c.rising_score)}</div>
      <div class="kv">
        <div><small>Подписчики</small><b>${fmt(c.subs)}</b></div>
        <div><small>Возраст</small><b>${(c.age_exact ? '' : '> ') + ageTxt(c.age_days)}</b></div>
        <div><small>Видео</small><b>${c.videos}${c.videos_capped ? '+' : ''}</b></div>
        <div><small>Обычно просм.</small><b>${fmt(c.median_views)}</b></div>
        <div><small>Просм. в день</small><b>${fmt(c.views_per_day)}</b></div>
        <div><small>Просм./подп.</small><b>${dec(c.views_per_sub)}</b></div></div>
      <div class="badges">
        ${c.trend ? `<span class="badge2 ${c.trend >= 1.3 ? 'up' : c.trend <= 0.75 ? 'down' : ''}" title="Свежие видео против предыдущих">${c.trend >= 1.3 ? 'ускоряется' : c.trend <= 0.75 ? 'замедляется' : 'стабильно'} ${trendTxt(c.trend).split(' ')[0]}${c.trend >= 1.3 || c.trend <= 0.75 ? ' ×' + dec(c.trend) : ''}</span>` : ''}
        ${c.hit_rate != null ? `<span class="badge2" title="Доля видео, набравших вдвое больше обычного">хиты ${c.hit_rate}%</span>` : ''}
        ${c.avg_duration ? `<span class="badge2" title="Типичная длина видео">⏱ ${durTxt(c.avg_duration)}</span>` : ''}
        ${c.shorts_share ? `<span class="badge2">Shorts ${c.shorts_share}%</span>` : ''}
        ${c.income ? `<span class="badge2" title="Грубая оценка дохода с рекламы YouTube">${incTxt(c.income)}/мес</span>` : ''}
        ${c.lang ? `<span class="badge2">${esc(LANGS[c.lang] || c.lang)}</span>` : ''}
      </div>
      ${spark(c.spark)}
      ${c.hit_videos?.[0] ? `<div class="hitv">Хит: <a href="https://www.youtube.com/watch?v=${esc(c.hit_videos[0].id)}" target="_blank" rel="noopener">${esc(c.hit_videos[0].title)}</a> — ${fmt(c.hit_videos[0].views)}</div>` : ''}
      <div class="tags">${(c.found_by || []).map(k => `<span class="tag">${esc(k)}</span>`).join('')}</div>
    </article>`).join('')
  } else {
    const cols = [['title', 'Канал'], ['rising_score', 'Оценка'], ['subs', 'Подписчики'], ['age_days', 'Возраст'], ['videos', 'Видео'], ['median_views', 'Обычно просм.'], ['views_per_day', 'Просм. в день'], ['views_per_sub', 'Просм./подп.'], ['uploads_per_week', 'Видео в нед.'], ['trend', 'Тренд'], ['hit_rate', 'Хиты'], ['avg_duration', 'Длина'], ['_inc', 'Доход/мес'], ['last_upload_days', 'Последнее']];
    $('#chTable').innerHTML = '<tr>' + cols.map(([k, t]) => `<th data-k="${k}" class="${k === S.sort ? 'sorted' : ''}${k === 'title' ? '' : ' r'}" scope="col">${t}${k === S.sort ? (S.dir < 0 ? ' ↓' : ' ↑') : ''}</th>`).join('') + '</tr>' +
      rows.map(c => `<tr><td class="name"><a href="${esc(c.url)}" target="_blank" rel="noopener"><b>${esc(c.title)}</b></a>${c.relevant === false ? ' <span class="pill grey">вне ниши</span>' : c.young ? ' <span class="pill blue">молодой</span>' : ''}</td>
        <td class="r"><span class="pill ${tone(c.rising_score)}">${c.rising_score}</span></td><td class="r">${fmt(c.subs)}</td><td class="r">${(c.age_exact ? '' : '> ') + ageTxt(c.age_days)}</td>
        <td class="r">${c.videos}${c.videos_capped ? '+' : ''}</td><td class="r">${fmt(c.median_views)}</td><td class="r">${fmt(c.views_per_day)}</td>
        <td class="r">${dec(c.views_per_sub)}</td><td class="r">${dec(c.uploads_per_week)}</td>
        <td class="r">${c.trend ? trendTxt(c.trend) : '—'}</td><td class="r">${c.hit_rate != null ? c.hit_rate + '%' : '—'}</td><td class="r">${durTxt(c.avg_duration)}</td><td class="r">${incTxt(c.income)}</td><td class="r">${c.last_upload_days == null ? '—' : ageTxt(c.last_upload_days) + ' назад'}</td></tr>`).join('');
    $$('#chTable th').forEach(th => th.onclick = () => { const k = th.dataset.k; S.dir = k === S.sort ? -S.dir : -1; S.sort = k; renderChannels() })
  }
}

/* ---------- экспорт и шаринг */
$('#csvBtn').onclick = () => {
  const h = [['title', 'Канал'], ['url', 'Ссылка'], ['rising_score', 'Оценка'], ['label', 'Статус'], ['subs', 'Подписчики'], ['age_days', 'Возраст, дней'], ['videos', 'Видео'], ['median_views', 'Медиана просмотров'], ['max_views', 'Макс. просмотров'], ['views_per_sub', 'Просм./подп.'], ['views_per_day', 'Просм. в день'], ['uploads_per_week', 'Видео в неделю'], ['last_upload_days', 'Дней с последнего видео'], ['young', 'Молодой'], ['relevant', 'В нише'], ['relevance', 'Похожесть на конкурента, %'], ['trend', 'Тренд (свежие/прошлые)'], ['hit_rate', 'Доля хитов, %'], ['avg_duration', 'Типичная длина, сек'], ['shorts_share', 'Доля Shorts, %'], ['lang', 'Язык'], ['income', 'Доход в месяц, $ (оценка)'], ['found_by', 'Найден по запросам']];
  const q = v => '"' + String(Array.isArray(v) ? v.join(', ') : v === true ? 'да' : v === false ? 'нет' : v ?? '').replace(/"/g, '""') + '"';
  const csv = [h.map(x => q(x[1])).join(';')].concat(S.data.channels.map(c => h.map(([k]) => q(c[k])).join(';'))).join('\n');
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv' }));
  a.download = `ниша_${(S.data.competitor.title || 'канал').replace(/[^\wа-яё]+/gi, '_')}.csv`; a.click()
};
$('#shareBtn').onclick = () => copy(location.origin + '/?job=' + S.job, 'Ссылка скопирована. Открыть её сможет любой, у кого есть ключ.');
$('#printBtn').onclick = () => print();

/* ---------- история */
async function loadHistory() {
  const rows = await api('/api/history'), admin = S.me.role === 'admin';
  $('#histLead').textContent = admin ? 'Все анализы всех пользователей. Нажмите на строку, чтобы открыть отчёт.' : 'Ваши прошлые отчёты. Нажмите, чтобы открыть.';
  $('#histTable').innerHTML = histRows(rows, admin)
}
function histRows(rows, admin) {
  if (!rows.length) return `<tr><td><div class="empty">Здесь появятся ваши отчёты. Запустите первый анализ во вкладке «Анализ».</div></td></tr>`;
  const st = { done: '<span class="pill hi">готов</span>', error: '<span class="pill lo">ошибка</span>', running: '<span class="pill grey">идёт</span>' };
  return `<tr><th>Конкурент</th><th>Когда</th>${admin ? '<th>Ключ</th>' : ''}<th>Статус</th><th class="r">Оценка</th></tr>` + rows.map(r => `<tr>
    <td class="name">${r.status === 'done' ? `<a href="?job=${esc(r.id)}" data-job="${esc(r.id)}">${esc(r.title || r.url)}</a>` : esc(r.url)}</td>
    <td>${dt(r.created_at)}</td>${admin ? `<td>${r.hint ? esc(r.hint) : 'вы'}</td>` : ''}<td>${st[r.status] || ''}</td>
    <td class="r">${r.overall != null ? `<span class="pill ${tone(r.overall)}">${r.overall}</span>` : '—'}</td></tr>`).join('')
}
document.addEventListener('click', e => { const a = e.target.closest('[data-job]'); if (a) { e.preventDefault(); openReport(a.dataset.job) } });

/* ---------- админ */
async function loadAdmin() {
  const r = await api('/api/admin/codes'); S.codes = r.codes; S.publicUrl = r.public_url;
  const link = S.publicUrl || location.origin;
  $('#urlBox').innerHTML = `<code>${esc(link)}</code><button class="btn sm" id="copyUrl">Скопировать</button>
    <span class="hint" style="margin:0">${S.publicUrl ? 'Публичная ссылка, работает из любого места.' : 'Это адрес, по которому вы сейчас открыли сервис. Чтобы коллеги заходили из интернета, запустите сервис с публичной ссылкой — см. инструкцию по установке.'}</span>`;
  $('#copyUrl').onclick = () => copy(link);
  $('#codesCsv').href = '/api/admin/codes.csv?key=' + encodeURIComponent(S.key);
  const cnt = s => S.codes.filter(c => c.status === s).length;
  const stats = [['Всего ключей', S.codes.length], ['Не выданы', cnt('new')], ['Активны', cnt('active')], ['Истекли', cnt('expired')], ['Отозваны', cnt('revoked')]];
  $('#stats').innerHTML = stats.map(([l, v]) => `<div class="stat"><small>${l}</small><b>${v}</b></div>`).join('');
  renderCodes();
  $('#actTable').innerHTML = histRows((await api('/api/history')).slice(0, 15), true)
}
function renderCodes() {
  const q = $('#codeSearch').value.trim().toLowerCase();
  const rows = S.codes.filter(c => (S.codeFilter === 'all' || c.status === S.codeFilter) && (!q || (c.note || '').toLowerCase().includes(q) || c.hint.toLowerCase().includes(q)));
  const st = { new: '<span class="pill grey">не активирован</span>', active: '<span class="pill hi">активен</span>', expired: '<span class="pill mid">истёк</span>', revoked: '<span class="pill lo">отозван</span>' };
  const total = rows.length; const shown = rows.slice(0, S.codeLimit);
  $('#codesTable').innerHTML = `<tr><th>Ключ</th><th>Срок</th><th>Статус</th><th>Истекает</th><th>Заметка</th><th class="r">Входов</th><th class="r">Анализов</th><th>Действия</th></tr>` +
    (shown.length ? shown.map(c => `<tr>
      <td><b>${esc(c.hint)}</b></td><td>${daysTxt(c.days)}</td><td>${st[c.status]}</td>
      <td>${!c.days ? 'никогда' : c.status === 'new' ? 'с первого входа' : dt(c.expires_at)}</td>
      <td class="name">${esc(c.note) || '<span class="hint" style="margin:0">—</span>'}</td>
      <td class="r">${c.uses}</td><td class="r">${c.analyses}</td>
      <td><div class="actions">
        <button class="icon-btn" data-a="note" data-id="${c.id}">Заметка</button>
        <select class="icon-btn" data-sel="${c.id}" aria-label="Действие с ключом">
          <option value="">Ещё…</option>${c.days ? '<option value="extend">Продлить на 1 день</option>' : ''}
          ${c.status !== 'new' && c.days ? '<option value="reset">Сбросить срок (с нового входа)</option>' : ''}
          ${c.status === 'revoked' ? '<option value="restore">Вернуть доступ</option>' : '<option value="revoke">Отозвать</option>'}
          <option value="delete">Удалить</option></select>
      </div></td></tr>`).join('') : `<tr><td colspan="8"><div class="empty">Нет ключей под этот фильтр.</div></td></tr>`) +
    (total > shown.length ? `<tr><td colspan="8" style="text-align:center"><button class="btn quiet sm" id="moreCodes">Показать ещё (${total - shown.length})</button></td></tr>` : '');
  const more = $('#moreCodes'); if (more) more.onclick = () => { S.codeLimit += 50; renderCodes() }
}
$$('#codeFilter [data-s]').forEach(b => b.onclick = () => { S.codeFilter = b.dataset.s; S.codeLimit = 25; $$('#codeFilter [data-s]').forEach(x => x.setAttribute('aria-pressed', x === b)); renderCodes() });
$('#codeSearch').oninput = renderCodes;
$('#codesTable').onchange = e => { const sel = e.target.closest('[data-sel]'); if (sel && sel.value) { codeAction(sel.value, sel.dataset.sel); sel.value = '' } };
$('#codesTable').onclick = e => { const b = e.target.closest('[data-a]'); if (b) codeAction(b.dataset.a, b.dataset.id) };
async function codeAction(a, id) {
  const body = { action: a };
  if (a === 'note') { const c = S.codes.find(x => x.id === id); const n = prompt('Заметка к ключу (кому выдан):', c?.note || ''); if (n == null) return; body.note = n }
  if (a === 'extend') body.days = 1;
  if (a === 'delete' && !confirm('Удалить ключ навсегда? Человек с этим ключом потеряет доступ.')) return;
  if (a === 'revoke' && !confirm('Отозвать ключ? Доступ закроется сразу. Потом можно вернуть.')) return;
  await api('/api/admin/codes/' + id, { method: 'POST', body: JSON.stringify(body) });
  toast({ note: 'Заметка сохранена', extend: 'Срок продлён на 1 день', reset: 'Срок начнётся со следующего входа', revoke: 'Ключ отозван', restore: 'Ключ снова действует', delete: 'Ключ удалён' }[a]);
  loadAdmin()
}
$$('#daysSeg [data-d]').forEach(b => b.onclick = () => { S.days = +b.dataset.d; $$('#daysSeg [data-d]').forEach(x => x.setAttribute('aria-pressed', x === b)) });
$('#createForm').onsubmit = async e => {
  e.preventDefault();
  const r = await api('/api/admin/codes', { method: 'POST', body: JSON.stringify({ count: +$('#cCount').value || 1, days: S.days, note: $('#cNote').value }) });
  showKeys(r.keys, S.days); $('#cNote').value = ''; loadAdmin()
};
function invite(key, days) {
  const base = S.publicUrl || location.origin;
  return `Привет! Даю доступ к Niche Finder — сервису поиска ниш на YouTube.

1. Открой ссылку: ${base}/?key=${encodeURIComponent(key)}
   Ключ подставится сам.
2. Если сервис попросит ключ, вставь его: ${key}
3. Вставь ссылку на канал конкурента и нажми «Найти нишу». Анализ идёт 1–3 минуты.

${days ? `Ключ действует ${daysTxt(days)} с момента первого входа.` : 'Ключ бессрочный.'} Подсказки — во вкладке «Как пользоваться».`
}
function dialog(title, text, bodyHtml, buttons) {
  $('#dlgTitle').textContent = title; $('#dlgText').textContent = text; $('#dlgBody').innerHTML = bodyHtml; $('#dlgRow').innerHTML = '';
  buttons.forEach(([label, fn, cls]) => { const b = document.createElement('button'); b.className = 'btn ' + (cls || ''); b.textContent = label; b.onclick = fn; $('#dlgRow').append(b) });
  $('#dlg').showModal()
}
function showKeys(keys, days) {
  const one = keys.length === 1;
  dialog(one ? 'Ключ создан' : `Создано ключей: ${keys.length}`,
    'Сохраните ключи сейчас: полностью они показываются только один раз.',
    `<textarea id="dlgTa" readonly>${esc(one ? invite(keys[0], days) : keys.join('\n'))}</textarea>`,
    [[one ? 'Скопировать приглашение' : 'Скопировать все ключи', () => copy($('#dlgTa').value)],
     ...(one ? [['Скопировать только ключ', () => copy(keys[0]), 'quiet']] : []),
     ['Готово', () => $('#dlg').close(), 'quiet']])
}
$('#invBtn').onclick = () => {
  dialog('Приглашение по ключу', 'Вставьте ключ из таблицы — получите готовый текст для коллеги.',
    `<input class="field" id="invKey" placeholder="NF-XXXX-XXXX-XXXX-XXXX" style="margin-bottom:10px">
     <div class="seg" id="invDays" style="margin-bottom:10px;width:max-content"><button type="button" data-d="1" aria-pressed="true">Сутки</button><button type="button" data-d="3" aria-pressed="false">3 дня</button><button type="button" data-d="10" aria-pressed="false">10 дней</button><button type="button" data-d="30" aria-pressed="false">30 дней</button><button type="button" data-d="0" aria-pressed="false">Бессрочно</button></div>
     <textarea id="dlgTa" readonly></textarea>`,
    [['Скопировать', () => copy($('#dlgTa').value)], ['Закрыть', () => $('#dlg').close(), 'quiet']]);
  let d = 1; const upd = () => $('#dlgTa').value = invite($('#invKey').value.trim().toUpperCase() || 'NF-XXXX-XXXX-XXXX-XXXX', d);
  $('#invKey').oninput = upd;
  $$('#invDays [data-d]').forEach(b => b.onclick = () => { d = +b.dataset.d; $$('#invDays [data-d]').forEach(x => x.setAttribute('aria-pressed', x === b)); upd() });
  upd()
};

/* ---------- старт */
(async function init() {
  const p = new URLSearchParams(location.search);
  const urlKey = p.get('key'), job = p.get('job');
  if (urlKey) history.replaceState(null, '', job ? '?job=' + job : location.pathname);
  const key = urlKey || store.get('nf_key');
  if (!key) return logout();
  try { await login(key, !urlKey) } catch (x) { if (x.message !== 'auth') logout(urlKey ? 'Ключ из ссылки не подошёл или его срок истёк.' : ''); return }
  if (job) openReport(job)
})();
