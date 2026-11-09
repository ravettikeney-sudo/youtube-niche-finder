/* Niche Finder — интерфейс */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const S = { key: null, me: null, data: null, job: null, filter: 'young', view: 'cards', sort: 'rising_score', dir: -1,
            codes: [], codeFilter: 'all', codeLimit: 40, days: 1, publicUrl: null, chart: null };
const ADMIN_PAGE = location.pathname.replace(/\/+$/, '') === '/admin';
const KEY_SLOT = ADMIN_PAGE ? 'nf_admin_key' : 'nf_key';
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
  if (!S.key && !ADMIN_PAGE && !['/api/login', '/api/plans', '/api/promo', '/api/promo/claim', '/api/hit'].includes(path)) { openGate(); throw new Error('auth') }
  const r = await fetch(path, { ...opts, headers: { 'Content-Type': 'application/json', 'X-Key': S.key || '', ...(opts.headers || {}) } });
  let body = null; try { body = await r.json() } catch {}
  if (r.status === 401 && path !== '/api/login') { logout('Срок ключа истёк или ключ отозван. Введите новый ключ.'); throw new Error('auth') }
  if (!r.ok) throw new Error(body?.detail || `Ошибка ${r.status}`);
  return body
}

/* ---------- вход */
async function login(key, quiet) {
  const me = await api('/api/login', { method: 'POST', body: JSON.stringify({ key }) });
  if (ADMIN_PAGE && me.role !== 'admin') throw new Error('Это вход только для администратора. Обычный ключ вводите на главной странице: ' + location.origin);
  S.key = key; S.me = me; store.set(KEY_SLOT, key);
  enterApp(); updateLimit(me); if (!quiet) toast(me.role === 'admin' ? 'Вы вошли как администратор' : 'Доступ открыт');
  if (ADMIN_PAGE && !new URLSearchParams(location.search).get('job')) show('admin')
}
function logout(msg) {
  store.set(KEY_SLOT, null); S.key = null; S.me = null;
  if (!ADMIN_PAGE) return enterGuest(msg);
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
  S.guest = false; document.body.classList.remove('guest'); document.body.classList.add('member');
  const b = $('#whoBadge');
  if (S.me.role === 'admin') { b.textContent = 'Админ'; b.title = 'Администратор, доступ бессрочный'; b.className = 'badge admin'; $$('.admin-only').forEach(x => x.classList.remove('hidden')) }
  else { tickBadge(); clearInterval(S.tick); S.tick = setInterval(tickBadge, 60000); $$('.admin-only').forEach(x => x.classList.add('hidden')) }
  renderPromo(); renderTg()
}
function tickBadge() {
  if (!S.me.expires_at) { $('#whoBadge').textContent = 'Доступ бессрочный'; $('#whoBadge').className = 'badge'; return }
  const left = (S.me.expires_at || 0) - Date.now() / 1000, b = $('#whoBadge');
  if (left <= 0) return logout('Срок вашего ключа истёк. Продлите доступ — выберите тариф или введите новый ключ.');
  const d = Math.floor(left / 86400), h = Math.floor(left % 86400 / 3600), m = Math.floor(left % 3600 / 60);
  b.textContent = 'Доступ ещё ' + (d ? `${d} д ${h} ч` : h ? `${h} ч ${m} мин` : `${m} мин`);
  b.className = 'badge'
}

/* ---------- акция: бесплатный доступ всем на N часов — большой таймер и попытки */
const pad2 = n => String(n).padStart(2, '0');
function hms(sec) {
  sec = Math.max(0, Math.floor(sec)); const d = Math.floor(sec / 86400); sec %= 86400;
  return (d ? d + ' д ' : '') + `${pad2(Math.floor(sec / 3600))}:${pad2(Math.floor(sec % 3600 / 60))}:${pad2(sec % 60)}`
}
const attWord = n => plural(n, 'попытка', 'попытки', 'попыток');
async function loadPromo() {
  try { S.promo = await api('/api/promo') } catch { S.promo = { active: false } }
  if (S.promo.active) S.promo.endsLocal = Date.now() / 1000 + S.promo.left;  // по часам сервера, а не компьютера
  clearTimeout(S.promoWake);  // запланированная акция: баннер появится ровно в момент начала
  if (!S.promo.active && S.promo.starts_in != null && S.promo.starts_in < 86400) S.promoWake = setTimeout(loadPromo, (S.promo.starts_in + 1) * 1000);
  renderPromo()
}
function promoLeft() { return S.promo && S.promo.active ? S.promo.endsLocal - Date.now() / 1000 : 0 }
function renderPromo() {
  const p = S.promo || {}, guest = !!S.guest, mine = S.me && S.me.promo;
  const g = $('#promoGuest'), gp = $('#gatePromo'), bar = $('#promoBar');
  const showGuest = p.active && guest && promoLeft() > 0;
  g.classList.toggle('hidden', !showGuest); gp.classList.toggle('hidden', !showGuest);
  if (showGuest) {
    const html = `<div class="pg-l"><span class="pg-tag">🎁 Акция · бесплатно для всех</span>
        <h2>Попробуйте Niche Finder бесплатно</h2><p>Один клик — и у вас ключ до конца акции. Без оплаты и регистрации.</p>
        <button class="btn pg-btn" data-promo-claim>Получить бесплатный доступ</button></div>
      <div class="pg-r"><div class="big-num"><small>До конца акции</small><b class="js-promo-timer">${hms(promoLeft())}</b></div>
        <div class="big-num att"><small>Каждому</small><b>${p.attempts}</b><span>${attWord(p.attempts)}</span></div></div>`;
    g.innerHTML = html; gp.innerHTML = `<div class="gp-in"><div><b>🎁 Сейчас бесплатно!</b> Ещё <b class="js-promo-timer">${hms(promoLeft())}</b> — ${p.attempts} ${attWord(p.attempts)} каждому.</div><button class="btn sm" data-promo-claim>Получить бесплатно</button></div>`;
  }
  bar.classList.toggle('hidden', !mine);
  if (mine) {
    const left = Math.max(0, (S.me.expires_at || 0) - Date.now() / 1000), att = Math.max(0, (S.me.daily_limit || 0) - (S.me.used_24h || 0));
    bar.innerHTML = `<div class="pb-in">
      <div class="pb-cell"><small>Бесплатный доступ закончится через</small><b class="pb-timer js-own-timer${left < 900 ? ' hot' : ''}">${hms(left)}</b></div>
      <div class="pb-cell"><small>Осталось</small><b class="pb-att${att <= 1 ? ' hot' : ''}">${att} ${attWord(att).toUpperCase()}</b></div>
      <div class="pb-cell pb-key"><small>Ваш ключ</small><button type="button" class="pb-copy" data-copykey title="Скопировать ключ, чтобы войти с другого устройства">${esc(S.key || '')} <span>⧉</span></button></div>
      <button class="btn pb-buy" data-go="pricing">Купить доступ</button></div>`;
  }
}
setInterval(() => {
  if (S.promo && S.promo.active) {
    const l = promoLeft(); $$('.js-promo-timer').forEach(x => x.textContent = hms(l));
    if (l <= 0) { S.promo.active = false; renderPromo() }
  }
  if (S.me && S.me.promo) {
    const l = (S.me.expires_at || 0) - Date.now() / 1000, t = $('.js-own-timer');
    if (t) { t.textContent = hms(l); t.classList.toggle('hot', l < 900) }
    if (l <= 0) logout('Бесплатный доступ по акции закончился. Понравилось? Выберите тариф — от одного дня.')
  }
}, 1000);
document.addEventListener('click', e => { if (e.target.closest('[data-copykey]') && S.key) copy(S.key) });
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-promo-claim]'); if (!b) return;
  b.disabled = true;
  try {
    const r = await api('/api/promo/claim', { method: 'POST' });
    try { $('#gate').close() } catch {}
    await login(r.key, true);
    renderPromo(); show('analyze'); window.scrollTo({ top: 0 });
    dialog('🎁 Бесплатный доступ открыт!', 'Вы уже вошли — можно сразу искать нишу. Этот браузер вас запомнил. Чтобы зайти с телефона или другого браузера, сохраните ключ:',
      `<div class="promo-key"><code>${esc(r.key)}</code></div><p class="hint" style="margin:8px 0 0">Ключ действует до конца акции — ${hms(r.ends_at - Date.now() / 1000)}, попыток: ${r.attempts}. Ключ всегда виден в полосе с таймером.</p>`,
      [['Скопировать ключ', () => { copy(r.key) }], ['Начать', () => $('#dlg').close(), 'quiet']])
  } catch (x) { toast(x.message) } finally { b.disabled = false }
});

/* ---------- гость: интерфейс открыт, функции — по ключу или после оплаты */
function enterGuest(msg) {
  clearInterval(S.tick);
  S.guest = true; S.me = null; document.body.classList.add('guest'); document.body.classList.remove('member');
  $('#login').classList.add('hidden'); $('#app').classList.remove('hidden');
  $$('.admin-only').forEach(x => x.classList.add('hidden'));
  updateLimit(null);
  const v = $$('.view').find(x => !x.classList.contains('hidden'))?.id?.slice(2);
  if (['history', 'watch', 'admin', 'compare'].includes(v)) show('analyze');
  renderPromo();
  if (msg) openGate(msg, 'Доступ закончился')
}
function openGate(text, title) {
  $('#gateTitle').textContent = title || 'Нужен доступ';
  $('#gateText').textContent = text || 'Чтобы запустить анализ, войдите по ключу или купите доступ — это пара минут.';
  $('#gateErr').textContent = ''; renderGatePlans();
  if (!$('#gate').open) $('#gate').showModal();
  setTimeout(() => $('#gateKey').focus(), 50)
}
$('#gate').addEventListener('click', e => { if (e.target.closest('[data-gclose]') || e.target === $('#gate')) $('#gate').close() });
document.addEventListener('click', e => { if (e.target.closest('[data-gate]')) { e.preventDefault(); openGate('Введите ключ, который получили после оплаты или от администратора.', 'Вход по ключу') } });
$('#gateKey').oninput = e => { const p = e.target.selectionStart; e.target.value = e.target.value.toUpperCase(); e.target.setSelectionRange(p, p) };
$('#gateForm').onsubmit = async e => {
  e.preventDefault(); const k = $('#gateKey').value.trim(), btn = e.target.querySelector('.btn'); btn.disabled = true; $('#gateErr').textContent = '';
  try {
    await login(k); $('#gate').close(); $('#gateKey').value = '';
    if (S.pendingJob) { const j = S.pendingJob; S.pendingJob = null; openReport(j) }
  } catch (x) { $('#gateErr').textContent = x.message === 'auth' ? 'Ключ не подошёл' : x.message; $('.gate-in').classList.remove('shake'); void $('.gate-in').offsetWidth; $('.gate-in').classList.add('shake') }
  finally { btn.disabled = false }
};
/* любые действия гостя открывают окно доступа */
['#searchForm', '#thForm'].forEach(f => $(f).addEventListener('submit', e => {
  if (!S.key) { e.preventDefault(); e.stopImmediatePropagation(); openGate('Чтобы запустить анализ, войдите по ключу или купите доступ — это пара минут.') }
}, true));

/* ---------- тарифы */
const money = (v, cur) => `${new Intl.NumberFormat('ru').format(v)} ${cur || '₽'}`;
async function loadPlans(force) {
  if (S.pay && !force) return S.pay;
  try { S.pay = await api('/api/plans') } catch { S.pay = null }
  return S.pay
}
function planCard(p, i, pay) {
  const perDay = p.days ? Math.round(p.price / p.days) : null;
  return `<article class="tariff${p.badge ? ' hot' : ''}" style="--i:${i}">${p.badge ? `<span class="tariff-badge">${esc(p.badge)}</span>` : ''}
    <h3>${esc(p.name)}</h3><div class="tariff-price">${money(p.price, pay.currency)}</div>
    <div class="tariff-sub">${p.days ? daysTxt(p.days) + ' доступа' : 'бессрочный доступ'}${perDay && p.days > 1 ? ` · ${money(perDay, pay.currency)} в день` : ''}</div>
    <ul><li>${p.limit ? `<b>${p.limit}</b> анализов в сутки` : '<b>без лимита</b> анализов'}</li>${(pay.features || []).map(f => `<li>${esc(f)}</li>`).join('')}<li>Срок идёт с первого входа</li></ul>
    ${p.link ? `<a class="btn buy-btn" href="${esc(p.link)}" target="_blank" rel="noopener">Купить</a>` : '<button class="btn quiet" disabled>Скоро</button>'}
  </article>`
}
async function renderPricing() {
  const pay = await loadPlans(true); if (!pay) return;
  $('#plans').innerHTML = pay.plans.map((p, i) => planCard(p, i, pay)).join('');
  if (pay.note) $('#payNote').textContent = pay.note;
  const c = (pay.contact || '').replace(/^@/, '');
  $('#payContact').innerHTML = c ? `Вопросы по оплате: <a href="https://t.me/${esc(c)}" target="_blank" rel="noopener">@${esc(c)}</a>` : '';
  $('#plansFeat').textContent = 'Во всех тарифах доступны все функции сервиса. Ключ работает в одном браузере или на нескольких устройствах — как вам удобно.'
}
// согласие с офертой перед покупкой
const AGREE = 'nf_agree';
function agreed() { try { return localStorage.getItem(AGREE) === '1' } catch { return S.agreed } }
function syncAgree() { const ok = agreed(); $$('.agree-cb').forEach(c => c.checked = ok); document.body.classList.toggle('agreed', !!ok) }
document.addEventListener('change', e => {
  if (!e.target.classList.contains('agree-cb')) return;
  S.agreed = e.target.checked; try { localStorage.setItem(AGREE, e.target.checked ? '1' : '0') } catch {}
  syncAgree()
});
document.addEventListener('click', e => {
  const a = e.target.closest('#plans a.buy-btn, #gatePlans a.gp'); if (!a || agreed()) return;
  e.preventDefault(); toast('Отметьте согласие с соглашением и политикой — и нажмите «Купить» ещё раз');
  $$('.agree').forEach(l => { l.classList.remove('shake'); void l.offsetWidth; l.classList.add('shake') })
});
syncAgree();
const CAT_RU = {'Education': 'Образование', 'Science & Technology': 'Наука и техника', 'Howto & Style': 'Хобби и стиль', 'Autos & Vehicles': 'Авто', 'News & Politics': 'Новости', 'People & Blogs': 'Блоги', 'Entertainment': 'Развлечения', 'Comedy': 'Юмор', 'Gaming': 'Игры', 'Music': 'Музыка', 'Film & Animation': 'Фильмы и анимация', 'Sports': 'Спорт', 'Travel & Events': 'Путешествия', 'Pets & Animals': 'Животные', 'Nonprofits & Activism': 'НКО'};
async function renderGatePlans() {
  const pay = await loadPlans(); if (!pay) { $('#gatePlans').innerHTML = ''; return }
  $('#gatePlans').innerHTML = pay.plans.map(p => `<${p.link ? `a href="${esc(p.link)}" target="_blank" rel="noopener"` : 'div'} class="gp${p.badge ? ' hot' : ''}"><span><b>${esc(p.name)}</b><small>${p.limit ? p.limit + ' анализов/сутки' : 'без лимита'}</small></span><em>${money(p.price, pay.currency)}</em></${p.link ? 'a' : 'div'}>`).join('')
}

/* ---------- лимит анализов */
function updateLimit(me) {
  if (me) S.me = Object.assign(S.me || {}, me);
  const m = S.me, b = $('#limitBadge'), h = $('#limitHint');
  if (!m || m.role === 'admin' || !m.daily_limit) { b.classList.add('hidden'); h.textContent = ''; return }
  const left = Math.max(0, m.daily_limit - (m.used_24h || 0));
  b.textContent = `Анализов: ${m.used_24h || 0} из ${m.daily_limit}`; b.classList.remove('hidden');
  b.className = 'badge' + (left ? '' : ' warn');
  h.textContent = left ? `На сутки осталось анализов: ${left}. Поиск по теме считается за 3.` : 'Лимит анализов на сутки исчерпан. Он восстанавливается постепенно: через 24 часа после каждого анализа.'
  if (m.promo) { b.classList.add('hidden'); h.textContent = left ? `Бесплатных попыток осталось: ${left}. Любой анализ — одна попытка.` : 'Бесплатные попытки закончились. Понравилось? Купите доступ — от одного дня.'; }
  renderPromo()
}
async function refreshMe() { try { updateLimit(await api('/api/me')) } catch {} }

/* ---------- разделы */
function show(v) {
  $$('.view').forEach(x => x.classList.toggle('hidden', x.id !== 'v-' + v));
  $$('nav.tabs button').forEach(b => b.dataset.go === v ? b.setAttribute('aria-current', 'page') : b.removeAttribute('aria-current'));
  if (v === 'pricing') renderPricing();
  if (v === 'history') loadHistory();
  if (v === 'admin') loadAdmin();
  if (v === 'watch') { loadWatches(); loadRadars() }
  if (v === 'video') loadScriptSources();
  if (v === 'thumbs') loadScriptSources();
  window.scrollTo({ top: 0 })
}
$$('[data-go]').forEach(b => b.onclick = e => { e.preventDefault(); show(b.dataset.go) });

/* ---------- анализ */
S.mode = 'channel';
function setMode(m) {
  S.mode = m;
  $$('#modeSeg [data-mode]').forEach(b => b.setAttribute('aria-pressed', b.dataset.mode === m));
  const t = m === 'topic';
  $('#url').classList.toggle('hidden', t); $('#url').required = !t;
  $('#topicInput').classList.toggle('hidden', !t); $('#topicInput').required = t;
  $('#heroTitle').innerHTML = t ? 'Найдите <span class="grad">нишу</span> по теме' : 'Найдите <span class="grad">нишу</span> по каналу конкурента';
  $('#heroText').textContent = t ? 'Напишите общую тему — например, «кулинария» или «инвестиции». Сервис возьмёт поднишы из подсказок YouTube, проверит каналы в каждой и покажет, где войти легче всего.'
    : 'Вставьте ссылку на канал или любое его видео. Сервис определит тематику, найдёт молодые каналы, которые уже набирают просмотры, и оценит, насколько легко зайти в нишу сейчас.';
  $('#steps').innerHTML = (t ? ['Поднишы', 'Поиск по YouTube', 'Проверка каналов', 'Оценка', 'Рейтинг'] : ['Канал конкурента', 'Тематика', 'Поиск по YouTube', 'Проверка каналов', 'Оценка']).map(x => `<li>${x}</li>`).join('');
  (t ? $('#topicInput') : $('#url')).focus()
}
$$('#modeSeg [data-mode]').forEach(b => b.onclick = () => setMode(b.dataset.mode));

$('#searchForm').onsubmit = async e => {
  e.preventDefault(); $('#runErr').textContent = ''; $('#goBtn').disabled = true;
  $('#result').classList.add('hidden'); $('#topicResult').classList.add('hidden'); setProgress(1, 'Отправляю запрос…');
  try {
    if (S.mode === 'topic') {
      const r = await api('/api/topic', { method: 'POST', body: JSON.stringify({ topic: $('#topicInput').value.trim(),
        lang: $('#lang').value, region: $('#region').value, same_lang: $('#sameLang').checked, exclude: $('#excl').value.trim() || null, max_age_days: +$('#age').value || 730 }) });
      S.job = r.job_id; history.replaceState(null, '', '?job=' + S.job); refreshMe(); poll(); return
    }
    const r = await api('/api/analyze', { method: 'POST', body: JSON.stringify({
      url: $('#url').value.trim(), keywords: $('#kw').value.trim() || null,
      max_channels: +$('#mc').value || 30, per_query: +$('#pq').value || 25, max_age_days: +$('#age').value || 730,
      exclude: $('#excl').value.trim() || null, lang: $('#lang').value, region: $('#region').value, same_lang: $('#sameLang').checked, strict: $('#strict').checked }) });
    S.job = r.job_id; history.replaceState(null, '', '?job=' + S.job); refreshMe(); poll()
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
    if (j.status === 'done') { $('#goBtn').disabled = false; $('#progress').classList.add('hidden'); j.result?.type === 'topic' ? renderTopic(j.result) : j.result?.type === 'thumbs' ? (show('thumbs'), renderThumbs(j.result)) : ['teardown', 'script'].includes(j.result?.type) ? (show('video'), renderVideo(j.result)) : j.result?.type === 'thumbtest' ? (show('thumbs'), setThMode('test'), renderThumbTest(j.result)) : render(j.result) }
    else if (j.status === 'error') fail(j.error);
    else { setProgress(j.progress, j.message); setTimeout(poll, 1500) }
  } catch (x) { if (x.message !== 'auth') fail(x.message) }
}
function openReport(id) {
  if (!S.key && !ADMIN_PAGE) { S.pendingJob = id; return openGate('Отчёт откроется после входа по ключу.', 'Войдите, чтобы открыть отчёт') } S.job = id; history.replaceState(null, '', '?job=' + id); show('analyze'); $('#result').classList.add('hidden'); $('#topicResult').classList.add('hidden'); setProgress(0, 'Открываю отчёт…'); poll() }

/* ---------- отрисовка отчёта */
/* ---------- уведомления в Telegram: «отчёт готов», «доступ заканчивается», акции */
function renderTg() {
  const on = !!(S.me && S.me.tg);
  $$('[data-tglink]').forEach(b => {
    b.classList.toggle('on', on);
    const t = b.querySelector('.tg-txt'), inProg = !!b.closest('.progress');
    t.textContent = on ? (inProg ? 'Пришлю в Telegram, когда отчёт будет готов ✓' : 'Уведомления в Telegram включены ✓') : (inProg ? 'Прислать в Telegram, когда отчёт будет готов' : 'Уведомления в Telegram');
    b.title = on ? 'Отключить — командой /stop в боте' : 'Откроется наш бот — нажмите «Запустить»'
  });
}
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-tglink]'); if (!b) return;
  if (S.me && S.me.tg) return toast('Уведомления уже включены. Выключить — командой /stop в боте');
  const w = window.open('', '_blank');
  try {
    const r = await api('/api/tg/link', { method: 'POST' });
    if (w) w.location = r.url; else location.href = r.url;
    toast('В Telegram нажмите «Запустить» — и уведомления включатся');
    let n = 0; const t = setInterval(async () => {  // ждём, пока человек нажмёт «Запустить» в боте
      n++; try { S.me = await api('/api/me'); renderTg(); if (S.me.tg || n > 40) { clearInterval(t); if (S.me.tg) toast('🔔 Уведомления в Telegram включены') } } catch { clearInterval(t) }
    }, 3000);
  } catch (x) { if (w) w.close(); if (x.message !== 'auth') toast(x.message) }
});

/* ---------- идеи роликов: темы и форматы, которые недавно выстрелили у каналов ниши */
const IDEA_ICO = { open: '🌱', hot: '🔥', good: '✅', busy: '⚠️', single: '⚡' };
function ageShort(d) { return d == null ? '' : d < 1 ? 'сегодня' : d < 30 ? `${d} дн. назад` : `${Math.round(d / 30)} мес. назад` }
function ideaVid(v) {
  return `<a class="iv" href="https://www.youtube.com/watch?v=${esc(v.id)}" target="_blank" rel="noopener" title="${esc(v.title)}">
    <span class="iv-img"><img loading="lazy" src="${ytImg(v.id)}" alt=""><b class="iv-x">×${String(v.ratio >= 10 ? Math.round(v.ratio) : v.ratio.toFixed(1)).replace('.', ',')}</b></span>
    <span class="iv-t">${esc(v.title)}</span>
    <small>${esc(v.channel || '')} · ${fmt(v.views)} просм. (обычно ${fmt(v.norm)}) · ${ageShort(v.age)}</small></a>`
}
function renderIdeas(d) {
  const vi = d.video_ideas, sec = $('#ideasSec');
  if (!vi || (!(vi.ideas || []).length && !(vi.formats || []).length)) {
    sec.classList.toggle('hidden', !(vi && vi.note));
    if (vi && vi.note) { $('#ideasLead').textContent = vi.note; $('#ideasBox').innerHTML = ''; $('#ideasFormats').innerHTML = '' }
    return
  }
  sec.classList.remove('hidden');
  $('#ideasLead').textContent = `Проверил ${vi.checked_videos} роликов каналов ниши за последние 5 месяцев — ${vi.hits} из них набрали в 2+ раза больше обычного для своего канала. Вот какие темы и формулировки за этим стоят.`;
  const F = vi.formats || [];
  $('#ideasFormats').innerHTML = F.length ? `<h3 class="ideas-h3">Форматы, которые выстреливают <small>одна формулировка — разный объект</small></h3>
    <div class="fmts">${F.map(f => `<div class="fmt-card">
      <div class="fmt-tpl">«${esc(f.template)}»</div>
      <div class="fmt-meta"><span class="pill hi">×${String(f.median_ratio >= 10 ? Math.round(f.median_ratio) : f.median_ratio.toFixed(1)).replace('.', ',')}</span> ${f.videos} ${plural(f.videos, 'ролик', 'ролика', 'роликов')} · ${f.channels} ${plural(f.channels, 'канал', 'канала', 'каналов')} · последний ${ageShort(f.newest_days)}</div>
      <p class="hint">${esc(f.why)}</p>
      <div class="iv-row">${f.evidence.slice(0, 3).map(ideaVid).join('')}</div>
      <button class="link-btn" data-idea-thumbs="${esc(f.template.replace(/…/g, '').trim())}">Придумать превью и название в этом формате →</button>
    </div>`).join('')}</div>` : '';
  const I = vi.ideas || [];
  $('#ideasBox').innerHTML = (I.length ? `<h3 class="ideas-h3">Темы на подъёме</h3>` : '') + I.map((t, n) => `<div class="idea v-${t.verdict}" style="--i:${n}">
      <div class="idea-top"><div><span class="idea-v">${IDEA_ICO[t.verdict] || ''} ${esc(t.label)}</span><h4>${esc(t.name)}</h4></div>
        <div class="idea-x"><b>×${String(t.median_ratio >= 10 ? Math.round(t.median_ratio) : t.median_ratio.toFixed(1)).replace('.', ',')}</b><small>к обычным просмотрам</small></div></div>
      <p class="idea-why">${esc(t.why)}</p>
      <div class="idea-facts">
        <span><b>${t.videos}</b> ${plural(t.videos, 'ролик выстрелил', 'ролика выстрелили', 'роликов выстрелили')}</span>
        <span><b>${t.channels}</b> ${plural(t.channels, 'канал', 'канала', 'каналов')}${t.young_channels ? `, из них молодых: ${t.young_channels}` : ''}</span>
        <span>последний — <b>${ageShort(t.newest_days)}</b></span>
        ${t.demand && t.demand.fresh != null ? `<span>в поиске за 3 мес.: <b>${t.demand.fresh}</b> ${plural(t.demand.fresh, 'свежий ролик', 'свежих ролика', 'свежих роликов')}${t.demand.fresh ? `, обычно <b>${fmt(t.demand.fresh_median)}</b> просм.` : ''}</span>` : ''}
      </div>
      <div class="iv-row">${t.evidence.slice(0, 4).map(ideaVid).join('')}</div>
      <div class="idea-how"><b>Как подать:</b><ul>${t.tips.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
        ${(t.hooks || []).length ? `<small>Приёмы в выстреливших названиях: ${t.hooks.map(h => esc(h.hook.toLowerCase()) + (h.count > 1 ? ' ×' + h.count : '')).join(', ')}</small>` : ''}</div>
      <button class="link-btn" data-idea-thumbs="${esc(t.name)}">Придумать превью и названия на эту тему →</button>
    </div>`).join('');
}
document.addEventListener('click', e => {
  const b = e.target.closest('[data-idea-thumbs]'); if (!b || !S.data) return;
  show('thumbs'); $('#thTopic').value = b.dataset.ideaThumbs.slice(0, 120);
  window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
  toast('Тема подставлена в «Превью». Вставьте ссылку на свой канал и нажмите «Разобрать»')
});

/* ---------- соседние ниши: считаются фоном после отчёта, сохраняются в нём */
function neighCard(r, base) {
  const dlt = r.delta == null ? '' : r.delta > 0 ? `<span class="nb-delta up">+${r.delta} к вашей</span>` : r.delta < 0 ? `<span class="nb-delta down">${r.delta} к вашей</span>` : '<span class="nb-delta">как ваша</span>';
  const ex = (r.examples || []).map(c => `<a class="nb-ex" href="${esc(c.url)}" target="_blank" rel="noopener">${avatar(c, 'ava xs')}<span>${esc(c.title)}<small>${fmt(c.subs)} подп. · ${ageTxt(c.age_days)}</small></span></a>`).join('');
  return `<div class="nb-card${r.low_data ? ' low' : ''}">
    <div class="nb-top"><div class="nb-name">${esc(r.name)}</div><div class="nb-score pill ${tone(r.score)}">${r.score}</div></div>
    <div class="nb-sub">${dlt}${r.range ? `<span class="nb-range">обычно ${r.range[0]}–${r.range[1]}</span>` : ''}</div>
    <div class="nb-facts">
      <span><b>${r.scores.entry_window}</b> окно входа</span>
      <span><b>${r.young_rising}</b> из ${r.channels} растут</span>
      <span><b>${fmt(r.median_views)}</b> просмотров</span>
      <span><b>${r.scores.competition}</b> конкуренция</span>
    </div>
    ${ex ? `<div class="nb-exs">${ex}</div>` : ''}
    <div class="nb-foot"><span class="hint">надёжность: ${esc(r.reliability)}${r.low_data ? ' · мало данных' : ''}</span>
      <button class="link-btn" data-ntopic="${esc(r.name)}">Разобрать по теме →</button></div>
  </div>`
}
function renderNeighbors(d) {
  const sec = $('#neighSec'), box = $('#neighBox'), jid = S.job;
  const nb = d.neighbors;
  if (nb) {
    sec.classList.toggle('hidden', !(nb.rows || []).length);
    box.innerHTML = (nb.rows || []).map(r => neighCard(r, d.niche.overall)).join('');
    return
  }
  if (!(d.neighbor_candidates || []).length) { sec.classList.add('hidden'); return }
  sec.classList.remove('hidden');
  box.innerHTML = `<div class="nb-wait"><div class="bar"><i id="nbBar" style="width:3%"></i></div><span id="nbMsg">Считаю соседние ниши: ${esc(d.neighbor_candidates.slice(0, 4).join(', '))}… обычно около минуты</span></div>`;
  if (S._nbFor === jid) return;
  S._nbFor = jid;
  const tick = async (start) => {
    if (S.job !== jid) return;
    try {
      const r = await api(`/api/job/${jid}/neighbors`, start ? { method: 'POST' } : {});
      if (r.status === 'done') { d.neighbors = { rows: r.rows || [] }; if (S.data === d) renderNeighbors(d); return }
      if (r.status === 'error') { box.innerHTML = `<p class="hint">Соседние ниши не посчитались: ${esc(r.error || '')}</p>`; return }
      if (r.status === 'none') return tick(true);
      const bar = $('#nbBar'), msg = $('#nbMsg');
      if (bar) bar.style.width = Math.max(3, r.progress || 0) + '%';
      if (msg && r.message) msg.textContent = r.message;
      setTimeout(() => tick(false), 2500)
    } catch (x) { if (x.message !== 'auth') box.innerHTML = `<p class="hint">${esc(x.message)}</p>` }
  };
  tick(true)
}
document.addEventListener('click', e => {
  const b = e.target.closest('[data-ntopic]'); if (!b) return;
  setMode('topic'); $('#topicInput').value = b.dataset.ntopic; show('analyze'); window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
  toast('Тема подставлена — нажмите «Найти нишу», чтобы разобрать её подробно')
});

/* ---------- «не та ниша»: исключение канала и пересчёт оценок */
const clampN = x => Math.max(0, Math.min(100, x));
const logS = (v, lo, hi) => !v || v <= 0 ? 0 : clampN((Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo)) * 100);
const median = a => { if (!a.length) return 0; const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2 };
function applyExcl(d) {
  const ex = new Set(d.excluded || []);
  d.channels.forEach(c => {
    if (c._rel0 === undefined) { c._rel0 = c.relevant !== false; c._off0 = c.off_reason || null }
    c.excluded = ex.has(c.id);
    c.relevant = c._rel0 && !c.excluded;
    c.off_reason = c.excluded ? 'исключён вручную' : c._off0;
  });
  d._n0 = d._n0 || JSON.parse(JSON.stringify(d.niche));
  d._manual = !!ex.size;
  if (!ex.size) { d.niche = JSON.parse(JSON.stringify(d._n0)); return }
  const n = JSON.parse(JSON.stringify(d._n0)), s = n.scores;
  const rel = d.channels.filter(c => c.relevant);
  const young = rel.filter(c => c.young), yr = young.filter(c => c.rising_score >= 55);
  const subs = rel.map(c => c.subs).filter(Boolean);
  const big = subs.length ? subs.filter(x => x >= 5e5).length / subs.length : 0;
  s.competition = Math.round(clampN(0.6 * big * 100 + 0.4 * logS(median(subs), 1e4, 5e6)));
  s.entry_window = Math.round(clampN(yr.length / Math.max(rel.length, 1) * 250));
  s.freshness = Math.round(clampN(young.length / Math.max(rel.length, 1) * 200));
  n.overall = Math.round(0.30 * s.demand + 0.25 * (100 - s.competition) + 0.30 * s.entry_window + 0.15 * s.monetization);
  n.verdict = n.overall >= 70 ? 'Отличная ниша: спрос есть, новички реально растут. Заходить стоит.'
    : n.overall >= 55 ? 'Хорошая ниша с рабочей точкой входа. Нужен свой угол подачи.'
    : n.overall >= 40 ? 'Средне: либо высокая конкуренция, либо мало растущих новичков. Ищите подниши.'
    : 'Слабая точка входа: ниша занята крупными каналами или спрос низкий.';
  n.median_subs = Math.round(median(subs)); n.big_channels_share = Math.round(big * 100);
  n.analyzed = d._n0.analyzed; n.young = young.length; n.young_rising = yr.length;
  d.niche = n
}
async function toggleExcl(id, on) {
  try {
    const r = await api(`/api/job/${S.job}/exclude`, { method: 'POST', body: JSON.stringify({ channel_id: id, excluded: on }) });
    S.data.excluded = r.excluded; render(S.data, false);
    toast(on ? 'Канал убран из ниши — оценки пересчитаны. В следующих анализах этой ниши он не появится' : 'Канал возвращён в нишу')
  } catch (x) { if (x.message !== 'auth') toast(x.message) }
}
document.addEventListener('click', e => { const b = e.target.closest('[data-excl]'); if (b) toggleExcl(b.dataset.excl, b.dataset.on === '1') });
// каналы, убранные из этой ниши раньше: в отчёт не попали, их можно вернуть для следующих анализов
function renderHidden(d) {
  const el = $('#hiddenCh'), hc = d.hidden_channels || [];
  el.classList.toggle('hidden', !hc.length);
  if (!hc.length) return;
  el.innerHTML = (hc.length === 1 ? '🙈 Не показан канал, который вы раньше убрали из этой ниши: ' : `🙈 Не показаны ${hc.length} ${plural(hc.length, 'канал', 'канала', 'каналов')}, которые вы раньше убрали из этой ниши: `) +
    hc.map(h => `<span class="hc">${esc(h.title || h.id)} <button class="link-btn" data-forget="${esc(h.id)}" title="Снова учитывать в этой нише">вернуть</button></span>`).join(', ');
}
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-forget]'); if (!b || !S.data) return;
  try {
    await api('/api/niche-excl/forget', { method: 'POST', body: JSON.stringify({ comp_id: S.data.competitor.id, channel_id: b.dataset.forget }) });
    S.data.hidden_channels = (S.data.hidden_channels || []).filter(h => h.id !== b.dataset.forget); renderHidden(S.data);
    toast('Канал снова будет учитываться — со следующего анализа этой ниши')
  } catch (x) { if (x.message !== 'auth') toast(x.message) }
});

function render(d, scroll = true) {
  S.data = d; $('#result').classList.remove('hidden'); $('#topicResult').classList.add('hidden');
  applyExcl(d);
  const n = d.niche, sc = n.scores;
  if (!$('#url').value) $('#url').value = d.competitor.url;

  // кольцо
  const arc = $('#ringArc'), C = 540.35;
  arc.style.stroke = toneColor(n.overall); arc.style.strokeDashoffset = C;
  requestAnimationFrame(() => requestAnimationFrame(() => arc.style.strokeDashoffset = C * (1 - n.overall / 100)));
  countUp($('#ovNum'), n.overall);
  const V = d.verification;
  $('#verdict').textContent = (V && V.level === 'низкая' ? 'Ориентировочно: ' : '') + n.verdict;
  $('#ovRange').textContent = V?.range && !d._manual ? `обычно ${V.range[0]}–${V.range[1]}` : '';
  renderVerify(d);
  renderNeighbors(d);
  renderIdeas(d);

  const M = [
    ['Спрос', sc.demand, `обычно ${fmt(n.median_search_views)} просмотров на видео`],
    ['Конкуренция', sc.competition, `каналов-гигантов ${n.big_channels_share}%`, true],
    ['Окно входа', sc.entry_window, `растут ${n.young_rising} молодых из ${n.relevant ?? n.analyzed} каналов ниши`],
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
      ${(d.examples || []).length ? `<p class="desc">Вместе с каналами-примерами: ${d.examples.map(x => `<a href="${esc(x.url)}" target="_blank" rel="noopener"><b>${esc(x.title)}</b></a>`).join(', ')}</p>` : ''}${(d.exclude_words || []).length ? `<p class="desc">Слова-исключения: ${d.exclude_words.map(esc).join(', ')}</p>` : ''}
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
  const off = d.channels.filter(c => !c.relevant && !c.excluded).length;
  const langName = (LANGS[d.competitor.lang] || d.competitor.lang || '').toString();
  $('#chLead').textContent = `Язык ниши — ${langName}. Проверено ${n.analyzed} ${plural(n.analyzed, 'канал', 'канала', 'каналов')}: точно в нише — ${d.channels.filter(c => c.relevant).length}, из них молодых — ${n.young}, растущих молодых — ${n.young_rising}.` + (off ? ` Ещё ${off} нашлись по запросам, но по темам или языку не совпадают с конкурентом — они помечены «вне ниши» и не влияют на оценки.` : '') + ' Каналы подобраны по ключевым словам и по названиям лучших видео ' + ((d.examples || []).length ? 'каналов-примеров.' : 'конкурента.') + ((d.excluded || []).length ? ` Вручную убрано: ${d.excluded.length} — их видно в фильтре «Включая вне ниши».` : '');
  renderHidden(d);
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

  renderDur(d); renderTitles(d); renderSearches(d); renderFormats(d); renderForecast(d); renderGallery(d);
  $('#watchBtn').disabled = false; $('#watchBtn').textContent = 'Следить за нишей';
  $('#radarBtn').disabled = false; $('#radarBtn').textContent = '📡 Включить радар';
  $('#genAt').textContent = new Date(d.generated_at).toLocaleString('ru', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
  if (scroll) $('#result').scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
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
    `<div class="stat"><small>Языки ниши</small><div class="langs">${(y.languages || []).map(l => `<span class="pill grey">${esc(LANGS[l.code] || l.name)} ${l.share}%</span>`).join('')}</div><p>язык конкурента — ${esc((y.comp_lang || '').toLowerCase())}${y.region_name ? `; поиск как из страны «${esc(y.region_name)}»` : ''}${(y.countries || []).length ? `<br>страны каналов: ${y.countries.map(c => esc(c.name) + ' ' + c.share + '%').join(', ')}` : ''}</p></div>`
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
  const rows = [...filtered()].sort((a, b) => { if (a.relevant !== b.relevant) return a.relevant ? -1 : 1; const x = a[S.sort], y = b[S.sort]; if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * S.dir });
  if (!rows.length) {
    const msg = `<div class="empty">Под этот фильтр каналов нет. Выберите «Вся ниша».</div>`;
    $('#cards').innerHTML = msg; $('#chTable').innerHTML = `<tr><td>${msg}</td></tr>`; return
  }
  if (S.view === 'cards') {
    $('#cards').innerHTML = rows.map(c => `<article class="card${c.relevant === false ? ' off' : ''}" id="ch-${esc(c.id)}">
      <div class="head">${avatar(c, 'ava')}<div class="t"><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a>
        <small><span class="pill ${tone(c.rising_score)}">${esc(c.label)}</span>${c.relevant === false ? ` <span class="pill grey">вне ниши${c.off_reason ? ': ' + esc(c.off_reason) : ''}</span>` : c.young ? ' <span class="pill blue">молодой</span>' : ''}${c.suspicious ? ` <span class="pill lo" title="${esc(c.suspicious)}">⚠ накрутка?</span>` : ''}${c.verify ? ` <span class="pill ${c.verify.startsWith('сверено') ? 'hi' : 'mid'}" title="Цифры сверены со страницами видео YouTube">${esc(c.verify.startsWith('сверено') ? '✓ сверено' : '⚠ ' + c.verify)}</span>` : ''}${c.format && c.format !== 'videos' ? ` <span class="pill grey">${c.format === 'shorts' ? 'Shorts' : 'стримы'}</span>` : ''}</small></div>
        ${miniRing(c.rising_score)}</div>
      <div class="kv">
        <div><small>Подписчики</small><b>${fmt(c.subs)}</b></div>
        <div><small>Возраст</small><b title="${c.age_source ? 'по ' + (c.age_source === 'дата регистрации' ? 'дате регистрации канала' : 'первому видео') : ''}${c.joined ? ' · на YouTube с ' + c.joined : ''}">${(c.age_exact ? '' : '> ') + ageTxt(c.age_days)}</b></div>
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
        ${c.category ? `<span class="badge2" title="Категория YouTube${c.topic ? ' · по теме ниши ' + Math.round(c.topic.coverage * 100) + '% роликов' : ''}">${esc(CAT_RU[c.category] || c.category)}</span>` : ''}
        ${c.lang ? `<span class="badge2" title="${c.lang_source ? 'язык подтверждён: ' + esc(c.lang_source) : 'язык по названиям'}">${esc(LANGS[c.lang] || c.lang)}${c.lang_source ? ' ✓' : ''}</span>` : ''}
        ${c.country ? `<span class="badge2" title="Страна канала">📍 ${esc(c.country)}</span>` : ''}
        ${c.joined && c.first_upload && (new Date(c.first_upload) - new Date(c.joined)) / 864e5 > 365 ? `<span class="badge2" title="Канал зарегистрирован давно, но видео появились недавно: старые ролики могли быть удалены">на YouTube с ${c.joined.slice(0, 4)}</span>` : ''}
      </div>
      ${spark(c.spark)}
      ${c.hit_videos?.[0] ? `<div class="hitv">Хит: <a href="https://www.youtube.com/watch?v=${esc(c.hit_videos[0].id)}" target="_blank" rel="noopener">${esc(c.hit_videos[0].title)}</a> — ${fmt(c.hit_videos[0].views)}</div>` : ''}
      ${c.relevance != null ? `<div class="match"><div class="match-h"><span>Похожесть на конкурента</span><b>${c.relevance}%</b></div><div class="track"><div class="fill" style="width:${c.relevance}%;background:var(--${c.relevant === false ? 'faint' : c.relevance >= 60 ? 'hi' : 'mid'})"></div></div>${c.match_terms?.length ? `<small>общие темы: ${c.match_terms.slice(0, 4).map(esc).join(', ')}</small>` : ''}</div>` : ''}
      <div class="tags">${c.similar_videos ? `<span class="tag hl">похожие видео: ${c.similar_videos}</span>` : ''}${(c.found_by || []).map(k => `<span class="tag">${esc(k)}</span>`).join('')}</div>
      <div class="card-foot">${c.excluded ? `<button class="icon-btn" data-excl="${esc(c.id)}" data-on="0">Вернуть в нишу</button>`
        : c.relevant ? `<button class="icon-btn" data-excl="${esc(c.id)}" data-on="1" title="Убрать канал из ниши и пересчитать оценки">✕ Не та ниша</button>` : ''}</div>
    </article>`).join('')
  } else {
    const cols = [['title', 'Канал'], ['relevance', 'Похожесть'], ['rising_score', 'Оценка'], ['subs', 'Подписчики'], ['age_days', 'Возраст'], ['videos', 'Видео'], ['median_views', 'Обычно просм.'], ['views_per_day', 'Просм. в день'], ['views_per_sub', 'Просм./подп.'], ['uploads_per_week', 'Видео в нед.'], ['trend', 'Тренд'], ['hit_rate', 'Хиты'], ['avg_duration', 'Длина'], ['_inc', 'Доход/мес'], ['last_upload_days', 'Последнее']];
    $('#chTable').innerHTML = '<tr>' + cols.map(([k, t]) => `<th data-k="${k}" class="${k === S.sort ? 'sorted' : ''}${k === 'title' ? '' : ' r'}" scope="col">${t}${k === S.sort ? (S.dir < 0 ? ' ↓' : ' ↑') : ''}</th>`).join('') + '</tr>' +
      rows.map(c => `<tr><td class="name"><a href="${esc(c.url)}" target="_blank" rel="noopener"><b>${esc(c.title)}</b></a>${c.relevant === false ? ' <span class="pill grey">вне ниши</span>' : c.young ? ' <span class="pill blue">молодой</span>' : ''}</td><td class="r">${c.relevance != null ? c.relevance + '%' : '—'}</td>
        <td class="r"><span class="pill ${tone(c.rising_score)}">${c.rising_score}</span></td><td class="r">${fmt(c.subs)}</td><td class="r">${(c.age_exact ? '' : '> ') + ageTxt(c.age_days)}</td>
        <td class="r">${c.videos}${c.videos_capped ? '+' : ''}</td><td class="r">${fmt(c.median_views)}</td><td class="r">${fmt(c.views_per_day)}</td>
        <td class="r">${dec(c.views_per_sub)}</td><td class="r">${dec(c.uploads_per_week)}</td>
        <td class="r">${c.trend ? trendTxt(c.trend) : '—'}</td><td class="r">${c.hit_rate != null ? c.hit_rate + '%' : '—'}</td><td class="r">${durTxt(c.avg_duration)}</td><td class="r">${incTxt(c.income)}</td><td class="r">${c.last_upload_days == null ? '—' : ageTxt(c.last_upload_days) + ' назад'}</td></tr>`).join('');
    $$('#chTable th').forEach(th => th.onclick = () => { const k = th.dataset.k; S.dir = k === S.sort ? -S.dir : -1; S.sort = k; renderChannels() })
  }
}

/* ---------- экспорт и шаринг */
$('#csvBtn').onclick = () => {
  const h = [['title', 'Канал'], ['url', 'Ссылка'], ['rising_score', 'Оценка'], ['label', 'Статус'], ['subs', 'Подписчики'], ['age_days', 'Возраст, дней'], ['videos', 'Видео'], ['median_views', 'Медиана просмотров'], ['max_views', 'Макс. просмотров'], ['views_per_sub', 'Просм./подп.'], ['views_per_day', 'Просм. в день'], ['uploads_per_week', 'Видео в неделю'], ['last_upload_days', 'Дней с последнего видео'], ['young', 'Молодой'], ['relevant', 'В нише'], ['off_reason', 'Почему вне ниши'], ['match_terms', 'Общие темы'], ['relevance', 'Похожесть на конкурента, %'], ['trend', 'Тренд (свежие/прошлые)'], ['hit_rate', 'Доля хитов, %'], ['avg_duration', 'Типичная длина, сек'], ['shorts_share', 'Доля Shorts, %'], ['lang', 'Язык'], ['income', 'Доход в месяц, $ (оценка)'], ['found_by', 'Найден по запросам']];
  const q = v => '"' + String(Array.isArray(v) ? v.join(', ') : v === true ? 'да' : v === false ? 'нет' : v ?? '').replace(/"/g, '""') + '"';
  const csv = [h.map(x => q(x[1])).join(';')].concat(S.data.channels.map(c => h.map(([k]) => q(c[k])).join(';'))).join('\n');
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv' }));
  a.download = `ниша_${(S.data.competitor.title || 'канал').replace(/[^\wа-яё]+/gi, '_')}.csv`; a.click()
};
$('#shareBtn').onclick = () => copy(location.origin + '/?job=' + S.job, 'Ссылка скопирована. Открыть её сможет любой, у кого есть ключ.');
$('#printBtn').onclick = () => print();
/* ---------- весь отчёт одним файлом для чата с нейросетью */
async function exportChat(btn) {
  if (!S.job) return;
  btn && (btn.disabled = true);
  try {
    const r = await fetch('/api/job/' + S.job + '/export', { headers: { 'X-Key': S.key || '' } });
    if (!r.ok) { let m = 'Не удалось скачать'; try { m = (await r.json()).detail || m } catch {} throw new Error(m) }
    const cd = r.headers.get('Content-Disposition') || '', m = cd.match(/filename\*=UTF-8''([^;]+)/);
    const a = document.createElement('a'); a.href = URL.createObjectURL(await r.blob());
    a.download = m ? decodeURIComponent(m[1]) : 'отчёт.md'; document.body.append(a); a.click(); a.remove();
    toast('Файл скачан. Загрузите его в чат с нейросетью — внутри есть подсказка, что спросить.')
  } catch (x) { toast(x.message) } finally { btn && (btn.disabled = false) }
}
document.addEventListener('click', e => { const b = e.target.closest('[data-export]'); if (b) { e.preventDefault(); exportChat(b) } });

/* ---------- история */
async function loadHistory() {
  const admin = S.me.role === 'admin';
  S.hist = await api('/api/history');
  $('#histLead').textContent = admin ? 'Вы администратор: здесь анализы всех ключей. Коллеги видят только свои. Нажмите на название, чтобы открыть отчёт.' : 'Ваши прошлые отчёты. Нажмите, чтобы открыть.';
  $('#histFilters').classList.toggle('hidden', !admin);
  const H = S.hist, kc = k => H.filter(r => (r.kind || 'channel') === k).length, day = Date.now() / 1000 - 86400;
  $('#histChips').innerHTML = chip(H.length, 'отчётов') + chip(kc('channel'), 'по каналам', 'c-blue') + chip(kc('topic'), 'по темам', 'c-violet') + chip(kc('thumbs'), 'превью', 'c-pink') + chip(H.filter(r => r.created_at > day).length, 'за сутки', 'c-green');
  if (admin) {
    const keys = new Map();
    S.hist.forEach(r => { const k = r.code_id || 'admin'; const o = keys.get(k) || { n: 0, label: r.code_id ? keyLabel(r) : 'Вы (администратор)' }; o.n++; keys.set(k, o) });
    const cur = S.histKey || '';
    $('#histKey').innerHTML = `<option value="">Все ключи (${S.hist.length})</option>` +
      [...keys].sort((a, b) => b[1].n - a[1].n).map(([k, o]) => `<option value="${esc(k)}"${k === cur ? ' selected' : ''}>${esc(o.label)} — ${o.n}</option>`).join('');
  }
  renderHistory()
}
const keyLabel = r => (r.note ? r.note + ' · ' : '') + (r.hint || '');
function renderHistory() {
  const admin = S.me.role === 'admin', k = admin ? S.histKey || '' : '', kind = admin ? S.histKind || '' : '';
  const rows = (S.hist || []).filter(r => (!k || (r.code_id || 'admin') === k) && (!kind || (r.kind || 'channel') === kind));
  $('#histCount').textContent = admin ? `Показано: ${rows.length}` : '';
  $('#histTable').innerHTML = histRows(rows, admin)
}
$('#histKey').onchange = e => { S.histKey = e.target.value; renderHistory() };
$('#histKind').onchange = e => { S.histKind = e.target.value; renderHistory() };
function openKeyHistory(id) { S.histKey = id; S.histKind = ''; $('#histKind').value = ''; show('history') }
function histRows(rows, admin) {
  if (!rows.length) return `<tr><td><div class="empty">Здесь появятся ваши отчёты. Запустите первый анализ во вкладке «Анализ».</div></td></tr>`;
  const st = { done: '<span class="pill hi">готов</span>', error: '<span class="pill lo">ошибка</span>', running: '<span class="pill grey">идёт</span>' };
  const kinds = { thumbs: '<span class="pill hi">превью</span>', topic: '<span class="pill blue">тема</span>', watch: '<span class="pill grey">слежение</span>', teardown: '<span class="pill blue">разбор</span>', script: '<span class="pill blue">сценарий</span>', thumbtest: '<span class="pill hi">тест превью</span>' };
  return `<tr><th class="cb"></th><th>Конкурент или тема</th><th>Когда</th>${admin ? '<th>Ключ</th>' : ''}<th>Статус</th><th class="r">Оценка</th></tr>` + rows.map(r => `<tr>
    <td class="cb">${r.status === 'done' && (r.kind || 'channel') === 'channel' ? `<input type="checkbox" class="cmp-cb" value="${esc(r.id)}" aria-label="Выбрать для сравнения">` : ''}</td>
    <td class="name"><span class="k-ico k-${r.kind || 'channel'}">${{ channel: '◎', topic: '✦', thumbs: '▶', watch: '◉', teardown: '✂', script: '☰', thumbtest: '◧' }[r.kind || 'channel'] || '◎'}</span>${r.status === 'done' ? `<a href="?job=${esc(r.id)}" data-job="${esc(r.id)}">${esc(r.title || r.url)}</a>` : esc(r.url)} ${kinds[r.kind] || ''}</td>
    <td>${dt(r.created_at)}</td>${admin ? `<td>${r.code_id ? `<a href="#" data-keyhist="${esc(r.code_id)}" title="Все анализы этого ключа">${esc(keyLabel(r))}</a>` : '<span class="pill blue">вы</span>'}</td>` : ''}<td>${st[r.status] || ''}</td>
    <td class="r">${r.overall != null ? `<span class="pill ${tone(r.overall)}">${r.overall}</span>` : '—'}</td></tr>`).join('')
}
document.addEventListener('click', e => { const kh = e.target.closest('[data-keyhist]'); if (kh) { e.preventDefault(); openKeyHistory(kh.dataset.keyhist); return } });
document.addEventListener('click', e => { const a = e.target.closest('[data-job]'); if (a) { e.preventDefault(); openReport(a.dataset.job) } });

/* ---------- админ */
async function loadAdmin() {
  const r = await api('/api/admin/codes'); S.codes = r.codes; S.publicUrl = r.public_url;
  const link = S.publicUrl || location.origin;
  $('#urlBox').innerHTML = `<code>${esc(link)}</code><button class="btn sm" id="copyUrl">Скопировать</button>
    <span class="hint" style="margin:0">${S.publicUrl ? 'Публичная ссылка, работает из любого места.' : 'Это адрес, по которому вы сейчас открыли сервис. Чтобы коллеги заходили из интернета, запустите сервис с публичной ссылкой — см. инструкцию по установке.'}</span>`;
  $('#copyUrl').onclick = () => copy(link);
  loadUpdates(); loadPayAdmin().then(loadAuto); loadPromoAdmin(); loadStats(14);
  $('#codesCsv').href = '/api/admin/codes.csv?key=' + encodeURIComponent(S.key);
  const cnt = s => S.codes.filter(c => c.status === s).length;
  const kc = s => S.codes.filter(c => kst(c) === s).length;
  const stats = [['Всего ключей', S.codes.length, '🔑', 'blue', 'all'], ['Свободны', kc('free'), '📦', 'violet', 'free'], ['Выданы, ждут входа', kc('given'), '✉️', 'blue', 'given'], ['Активны', kc('active'), '⚡', 'green', 'active'], ['Истекли', kc('expired'), '⌛', 'gold', 'expired'], ['Отозваны', kc('revoked'), '⛔', 'red', 'revoked']];
  $('#stats').innerHTML = stats.map(([l, v, ic, c, f], i) => `<button type="button" class="stat s-${c}${S.codeFilter === f ? ' on' : ''}" style="--i:${i}" data-kfilter="${f}" title="Показать: ${l.toLowerCase()}"><span class="s-ico">${ic}</span><small>${l}</small><b>${v}</b><span class="s-go">смотреть →</span></button>`).join('');
  renderCodes();
  $('#actTable').innerHTML = histRows((await api('/api/history')).slice(0, 15), true)
}
/* ---------- админ: тарифы */
async function loadPayAdmin() {
  const pay = await loadPlans(true); if (!pay) return;
  S.payEdit = JSON.parse(JSON.stringify(pay));
  $('#payContactIn').value = pay.contact || ''; $('#payNoteIn').value = pay.note || ''; $('#payCur').value = pay.currency || '₽';
  renderPayAdmin()
}
function renderPayAdmin() {
  $('#payPlans').innerHTML = S.payEdit.plans.map((p, i) => `<div class="pay-plan" data-i="${i}">
    <div class="pp-h"><b>Тариф ${i + 1}</b><button class="kb kb-red" data-pdel="${i}" title="Удалить тариф">🗑</button></div>
    <label>Название<input class="field" data-f="name" value="${esc(p.name)}"></label>
    <div class="pp-2"><label>Дней (0 — бессрочно)<input class="field" data-f="days" type="number" step="0.5" min="0" value="${p.days}"></label>
    <label>Анализов в сутки (0 — без лимита)<input class="field" data-f="limit" type="number" min="0" value="${p.limit}"></label></div>
    <div class="pp-2"><label>Цена<input class="field" data-f="price" type="number" min="0" value="${p.price}"></label>
    <label>Метка<input class="field" data-f="badge" value="${esc(p.badge || '')}" placeholder="Популярный"></label></div>
    <label>Ссылка на оплату (Tribute)<input class="field" data-f="link" value="${esc(p.link || '')}" placeholder="https://t.me/tribute/app?startapp=…"></label>
    <label>ID товара в Tribute (для автовыдачи)<input class="field" data-f="tribute_id" value="${esc(p.tribute_id || '')}" placeholder="из списка товаров (шаг 4)"></label>
  </div>`).join('')
}
$('#payPlans').addEventListener('input', e => {
  const f = e.target.dataset.f, i = +e.target.closest('[data-i]')?.dataset.i; if (!f) return;
  S.payEdit.plans[i][f] = ['days', 'limit', 'price'].includes(f) ? +e.target.value || 0 : e.target.value.trim();

});
$('#payPlans').addEventListener('click', e => { const d = e.target.closest('[data-pdel]'); if (!d) return; if (S.payEdit.plans.length < 2) return toast('Нужен хотя бы один тариф'); S.payEdit.plans.splice(+d.dataset.pdel, 1); renderPayAdmin() });
$('#payAdd').onclick = () => { if (S.payEdit.plans.length >= 8) return toast('Не больше 8 тарифов'); S.payEdit.plans.push({ id: 'p' + Date.now().toString(36).slice(-6), name: 'Новый тариф', days: 7, limit: 10, price: 590, link: '', badge: '' }); renderPayAdmin() };
$('#paySave').onclick = async () => {
  const body = { ...S.payEdit, contact: $('#payContactIn').value.trim(), note: $('#payNoteIn').value.trim(), currency: $('#payCur').value.trim() || '₽' };
  try { await api('/api/admin/plans', { method: 'POST', body: JSON.stringify(body) }); S.pay = null; toast('Тарифы сохранены — страница «Тарифы» обновилась'); loadAuto() } catch (x) { if (x.message !== 'auth') toast(x.message) }
};

/* ---------- админ: автовыдача ключей */
const stTag = (ok, txt, warn) => `<span class="pill ${ok ? 'hi' : warn ? 'mid' : 'grey'}">${ok ? '✓ ' : ''}${txt}</span>`;
async function loadAuto() {
  let a; try { a = await api('/api/admin/auto') } catch { return }
  S.auto = a;
  $('#as1st').innerHTML = a.bot_token_set ? (a.bot_error ? stTag(false, 'ошибка: ' + esc(a.bot_error).slice(0, 80), true) : stTag(true, '@' + esc(a.bot_username || '…') + (a.bot_running ? ' работает' : ''))) : stTag(false, 'не подключён');
  $('#as2st').innerHTML = a.admin_chats ? stTag(true, 'привязан') : stTag(false, 'не привязан');
  $('#as3st').innerHTML = a.api_key_set ? stTag(true, 'ключ сохранён') : stTag(false, 'нет ключа');
  const pay = S.payEdit || await loadPlans(true), mapped = (pay?.plans || []).filter(p => p.tribute_id).length;
  $('#as4st').innerHTML = mapped ? stTag(mapped === pay.plans.length, `привязано ${mapped} из ${pay.plans.length}`, mapped < pay.plans.length) : stTag(false, 'не привязаны');
  $('#hookUrl').textContent = a.webhook_url || 'адрес появится, когда у сервиса будет домен';
  const lw = a.last_webhook, bad = a.last_bad_signature;
  $('#hookLast').innerHTML = (lw ? `Последнее сообщение от Tribute: ${dt(lw.at)} ✓` : 'Сообщений от Tribute ещё не было.') + (bad && (!lw || bad > lw.at) ? ` <span style="color:var(--lo)">⚠ ${dt(bad)} пришёл запрос с неверной подписью — проверьте ключ API.</span>` : '');
  const bu = a.bot_username ? `https://t.me/${a.bot_username}` : 'ссылка на бота появится после шага 1';
  $('#afterMsg').value = `Спасибо за покупку Niche Finder! 🎉\n\nВаш ключ доступа уже ждёт вас в боте: ${bu}\nОткройте бота и нажмите «Старт» — ключ придёт сразу.\n\nСайт: ${a.site || location.origin}`;
  $('#testPlan').innerHTML = (pay?.plans || []).map(p => `<option value="${esc(p.id)}">${esc(p.name)} — ${money(p.price, pay.currency)}</option>`).join('');
  const st = { ok: '<span class="pill hi">выдан</span>', refunded: '<span class="pill lo">возврат, ключ отозван</span>', unmatched: '<span class="pill mid">тариф не найден</span>', test: '<span class="pill blue">тест</span>' };
  $('#buyTable').innerHTML = a.purchases.length ? `<tr><th>Когда</th><th>Покупатель</th><th>Тариф</th><th class="r">Сумма</th><th>Ключ</th><th>Статус</th></tr>` + a.purchases.map(p => `<tr>
    <td>${dt(p.created_at)}</td><td>${p.tg_username ? `<a href="https://t.me/${esc(p.tg_username)}" target="_blank" rel="noopener">@${esc(p.tg_username)}</a>` : esc(p.tg_id || p.trb_user_id || '—')}</td>
    <td>${esc(p.product_name || p.plan_id || '—')}${(p.info || '').includes('тест') ? ' <span class="pill blue">тест</span>' : ''}</td><td class="r">${p.amount ? new Intl.NumberFormat('ru').format(p.amount) + ' ' + esc(p.currency === 'RUB' ? '₽' : p.currency) : '—'}</td>
    <td>${p.key ? `<code class="kc-key sm" data-copy="${esc(p.key)}" title="Скопировать">${esc(p.key)}</code>` : '—'}</td>
    <td>${p.status === 'ok' ? (p.delivered_at ? '<span class="pill hi">✓ доставлен в бот</span>' : p.tg_id ? '<span class="pill mid">ждёт «Старт» в боте</span>' : '<span class="pill mid">отправьте вручную</span>') : st[p.status] || esc(p.status)}</td></tr>`).join('')
    : '<tr><td><div class="empty">Покупок пока нет. Здесь появится каждая оплата через Tribute.</div></td></tr>'
}
async function autoSave(body, okMsg) {
  try { await api('/api/admin/auto', { method: 'POST', body: JSON.stringify(body) }); toast(okMsg); loadAuto() } catch (x) { if (x.message !== 'auth') toast(x.message) }
}
$('#botSave').onclick = () => { const t = $('#botToken').value.trim(); if (!t) return toast('Вставьте токен бота'); autoSave({ bot_token: t }, 'Бот подключён'); $('#botToken').value = '' };
$('#trSave').onclick = () => { const t = $('#trApi').value.trim(); if (!t) return toast('Вставьте ключ API'); autoSave({ api_key: t }, 'Ключ Tribute сохранён'); $('#trApi').value = '' };
$('#botLink').onclick = async () => { try { const r = await api('/api/admin/auto/link', { method: 'POST', body: '{}' }); window.open(r.url, '_blank'); toast('В боте нажмите «Старт». Ссылка действует 15 минут.'); setTimeout(loadAuto, 15000) } catch (x) { if (x.message !== 'auth') toast(x.message) } };
$('#hookCopy').onclick = () => S.auto?.webhook_url ? copy(S.auto.webhook_url, 'Адрес вебхука скопирован') : toast('Адреса пока нет');
$('#afterCopy').onclick = () => copy($('#afterMsg').value, 'Текст скопирован — вставьте его в Tribute');
$('#testBuy').onclick = async () => { try { const r = await api('/api/admin/auto/test', { method: 'POST', body: JSON.stringify({ plan_id: $('#testPlan').value }) }); toast(r.delivered ? 'Готово — ключ пришёл вам в бот' : 'Ключ создан, но бот не смог отправить — нажмите «Старт» в боте'); loadAuto(); loadAdmin() } catch (x) { if (x.message !== 'auth') toast(x.message) } };
$('#trProducts').onclick = async () => {
  try {
    const r = await api('/api/admin/tribute/products'); const plans = S.payEdit?.plans || []; S.trRows = r.rows;
    $('#trList').innerHTML = r.rows.length ? `<div class="tr-list">${r.rows.map(p => { const cur = plans.find(x => String(x.tribute_id) === String(p.id));
      return `<div class="tr-row"><span><b>${esc(p.name || 'Товар')}</b><small>ID ${esc(p.id)}${p.amount != null ? ' · ' + new Intl.NumberFormat('ru').format(p.amount / 100) + ' ' + esc(String(p.currency || '').toUpperCase()) : ''}</small></span>
        <select class="field" data-trp="${esc(p.id)}"><option value="">— не выбран —</option>${plans.map(x => `<option value="${esc(x.id)}" ${cur && cur.id === x.id ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></div>` }).join('')}</div>
      <p class="hint">Выберите тариф для каждого товара и нажмите «Сохранить тарифы» выше.</p>` : '<p class="hint">В Tribute нет цифровых товаров. Создайте их по инструкции.</p>'
  } catch (x) { if (x.message !== 'auth') toast(x.message) }
};
$('#trList').addEventListener('change', e => {
  const sel = e.target.closest('[data-trp]'); if (!sel) return;
  S.payEdit.plans.forEach(p => { if (String(p.tribute_id) === sel.dataset.trp) p.tribute_id = '' });
  const pl = S.payEdit.plans.find(p => p.id === sel.value), row = (S.trRows || []).find(x => String(x.id) === sel.dataset.trp);
  if (pl) { pl.tribute_id = sel.dataset.trp; if (row?.link) pl.link = row.link }
  renderPayAdmin(); toast('Не забудьте нажать «Сохранить тарифы»')
});

/* ---------- обновления сервиса */
async function loadUpdates() {
  const box = $('#updBox');
  let v;
  try { v = await api('/api/admin/version') } catch (x) { box.innerHTML = `<span class="hint" style="margin:0">Не удалось проверить версию: ${esc(x.message)}</span>`; return }
  S.version = v.commit;
  const when = v.date ? new Date(v.date * 1000).toLocaleString('ru', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' }) : '—';
  if (!v.supported) {
    box.innerHTML = `<div><b>Версия от ${when}</b><p class="hint" style="margin:4px 0 0">Обновление кнопкой и автообновление включатся после одного обновления сервера командой <code>niche-finder update</code>.</p></div>`;
    return
  }
  const st = v.pending ? '<span class="pill mid">обновление запланировано</span>'
    : v.update_available ? '<span class="pill blue">есть новая версия</span>' : '<span class="pill hi">последняя версия</span>';
  box.innerHTML = `<div class="upd-info"><div><b>Версия от ${when}</b> ${st}</div>
      <p class="hint" style="margin:4px 0 0">Автообновление включено: сервер сам ставит новые версии каждую ночь около 04:30 по Москве и не прерывает идущие анализы.</p>
      ${v.changes?.length ? `<ul class="upd-list">${v.changes.map(c => `<li>${esc(c)}</li>`).join('')}</ul>` : ''}</div>
    <button class="btn ${v.update_available ? '' : 'quiet'}" id="updBtn" ${v.pending ? 'disabled' : ''}>${v.update_available ? 'Обновить сейчас' : 'Переустановить'}</button>`;
  $('#updBtn').onclick = runUpdate
}
async function runUpdate() {
  if (!confirm('Обновить сервис сейчас? Это займёт 1–2 минуты. Если идут анализы, обновление дождётся их окончания.')) return;
  try {
    const r = await api('/api/admin/update', { method: 'POST' });
    const box = $('#updBox');
    box.innerHTML = `<div><b>Обновляю…</b><p class="hint" style="margin:4px 0 0" id="updMsg">${r.running ? `Жду окончания анализов (${r.running}), затем обновлю.` : 'Скачиваю новую версию.'} Страница перезагрузится сама.</p></div>`;
    const old = S.version, t0 = Date.now();
    const tick = async () => {
      if (Date.now() - t0 > 20 * 60 * 1000) { $('#updMsg').textContent = 'Обновление идёт дольше обычного. Обновите страницу через пару минут.'; return }
      try {
        const v = await api('/api/admin/version');
        if (v.commit && v.commit !== old) { toast('Сервис обновлён'); setTimeout(() => location.reload(), 800); return }
      } catch {}
      setTimeout(tick, 5000)
    };
    setTimeout(tick, 8000)
  } catch (x) { if (x.message !== 'auth') toast(x.message) }
}

/* ---------- ключи: полное управление */
const leftTxt = sec => { if (sec <= 0) return 'истёк'; const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  return d ? `${d} д ${h} ч` + (d < 2 ? ` ${m} мин` : '') : h ? `${h} ч ${m} мин` : `${m} мин` };
function keyTime(c) {
  const now = Date.now() / 1000;
  if (c.status === 'revoked') return `<div class="kt kt-red"><b>Отозван</b><small>доступ закрыт</small></div>`;
  if (!c.days) return `<div class="kt kt-inf"><b>∞ Бессрочно</b><small>${c.activated_at ? 'активирован ' + dt(c.activated_at) : 'ещё не входил'}</small></div>`;
  if (c.status === 'new') return `<div class="kt kt-new"><b>${daysTxt(c.days)}</b><small>срок пойдёт с первого входа</small></div>`;
  const total = c.days * 86400, left = c.expires_at - now, pct = Math.max(0, Math.min(100, left / total * 100));
  if (left <= 0) return `<div class="kt kt-red"><b>Истёк</b><small>${dt(c.expires_at)}</small></div>`;
  return `<div class="kt ${pct < 15 ? 'kt-warn' : 'kt-ok'}" data-exp="${c.expires_at}" data-total="${total}"><b class="kt-left">осталось ${leftTxt(left)}</b>
    <span class="kt-bar"><i style="width:${pct}%"></i></span><small>до ${dt(c.expires_at)} · из ${daysTxt(c.days)}</small></div>`
}
setInterval(() => {  // живой обратный отсчёт
  const now = Date.now() / 1000;
  $$('.kt[data-exp]').forEach(el => { const left = +el.dataset.exp - now, pct = Math.max(0, Math.min(100, left / +el.dataset.total * 100));
    el.querySelector('.kt-left').textContent = left > 0 ? 'осталось ' + leftTxt(left) : 'истёк'; el.querySelector('.kt-bar i').style.width = pct + '%' })
}, 20000);
const KST = { free: 'свободен', given: 'выдан, ждёт входа', active: 'активен', expired: 'истёк', revoked: 'отозван' };
const kst = c => c.status === 'new' ? (c.given_at ? 'given' : 'free') : c.status;
function renderCodes() {
  const q = $('#codeSearch').value.trim().toLowerCase().replace(/[^a-zа-яё0-9]/g, '');
  const rows = S.codes.filter(c => (S.codeFilter === 'all' || kst(c) === S.codeFilter) &&
    (!q || ((c.note || '') + (c.key || '') + c.hint).toLowerCase().replace(/[^a-zа-яё0-9]/g, '').includes(q)));
  if (S.fresh?.size) rows.sort((a, b) => S.fresh.has(b.key) - S.fresh.has(a.key));
  S.codeRows = rows;
  const total = rows.length, shown = rows.slice(0, S.codeLimit);
  $('#codesCount').textContent = `Показано ${shown.length} из ${total}`;
  $('#codesTable').innerHTML = shown.length ? shown.map(c => { const s = kst(c); return `<article class="kc st-${s}${S.fresh?.has(c.key) ? ' fresh' : ''}" data-id="${c.id}">${S.fresh?.has(c.key) ? '<span class="fresh-tag">новый</span>' : ''}
    <div class="kc-top">
      <span class="kc-st"><i></i>${KST[s]}</span>
      ${c.key ? `<code class="kc-key" title="Нажмите, чтобы скопировать" data-a="copy">${esc(c.key)}</code>` : `<code class="kc-key hidden-key" title="Полный ключ не сохранён на сервере">${esc(c.hint)} · полный ключ есть только в вашей таблице</code>`}
      ${c.key ? `<button class="kb kb-blue" data-a="copy">📋 Ключ</button><button class="kb kb-violet" data-a="invite">✉️ Приглашение</button>` : ''}
      <button class="kc-note" data-a="note" title="Кому выдан">✎ ${c.note ? esc(c.note) : '<span>кому выдан?</span>'}</button>
    </div>
    <div class="kc-mid">
      ${keyTime(c)}
      <div class="ku"><div><small>Входов</small><b>${c.uses}</b></div>
        <div><small>Анализов</small><b>${c.analyses ? `<a href="#" data-keyhist="${c.id}" title="Открыть историю ключа">${c.analyses}</a>` : 0}</b></div>
        <div><small>За сутки</small><b>${c.used_24h || 0}<span> / ${c.daily_limit || '∞'}</span></b></div>
        <div><small>${c.last_used ? 'Последний вход' : 'Создан'}</small><b class="sm">${dt(c.last_used || c.created_at)}</b></div></div>
      <div class="kc-act">
        <details class="menu"><summary class="kb">⏱ Срок</summary><div class="menu-pop">
          ${c.days ? `<b>Продлить</b><div class="mrow"><button data-a="ext" data-v="1">+1 день</button><button data-a="ext" data-v="3">+3 дня</button><button data-a="ext" data-v="7">+7</button><button data-a="ext" data-v="30">+30</button></div>
          <div class="mrow"><button data-a="ext" data-v="-1">−1 день</button></div>` : ''}
          <b>Задать срок</b><div class="mrow"><button data-a="set" data-v="1">сутки</button><button data-a="set" data-v="3">3 дня</button><button data-a="set" data-v="10">10 дней</button><button data-a="set" data-v="30">30 дней</button><button data-a="set" data-v="0">∞ бессрочно</button><button data-a="set" data-v="ask">своё…</button></div>
          ${c.activated_at && c.days ? '<button class="mwide" data-a="reset">↺ Начать срок заново со следующего входа</button>' : ''}
        </div></details>
        <details class="menu"><summary class="kb">📊 Лимит: ${c.daily_limit || '∞'}</summary><div class="menu-pop"><b>Анализов в сутки</b><div class="mrow">
          ${[5, 10, 20, 50, 100].map(v => `<button data-a="lim" data-v="${v}" ${c.daily_limit === v ? 'class="on"' : ''}>${v}</button>`).join('')}<button data-a="lim" data-v="0" ${!c.daily_limit ? 'class="on"' : ''}>без лимита</button><button data-a="lim" data-v="ask">своё…</button></div></div></details>
        ${s === 'given' ? '<button class="kb" data-a="ungiven" title="Снять отметку «выдан»">↩ Не выдан</button>' : s === 'free' ? '<button class="kb" data-a="given" title="Отметить, что ключ отдан человеку">✓ Выдан</button>' : ''}
        ${c.status === 'revoked' ? '<button class="kb kb-green" data-a="restore">Вернуть доступ</button>' : '<button class="kb kb-orange" data-a="revoke">⛔ Отозвать</button>'}
        <button class="kb kb-red" data-a="delete" title="Удалить ключ">🗑</button>
      </div>
    </div></article>` }).join('') : '<div class="panel empty">Нет ключей под этот фильтр.</div>';
  if (total > shown.length) $('#codesTable').insertAdjacentHTML('beforeend', `<div style="text-align:center;margin-top:14px"><button class="btn quiet" id="moreCodes">Показать ещё (${total - shown.length})</button></div>`);
  const more = $('#moreCodes'); if (more) more.onclick = () => { S.codeLimit += 40; renderCodes() };
  const cnt = s => S.codes.filter(c => kst(c) === s).length;
  $$('#codeFilter [data-s]').forEach(b => { const n = b.dataset.s === 'all' ? S.codes.length : cnt(b.dataset.s); b.querySelector('em') ? b.querySelector('em').textContent = n : b.insertAdjacentHTML('beforeend', ` <em>${n}</em>`) })
}
// карточки-счётчики над списком ведут к ключам этого типа
document.addEventListener('click', e => {
  const st = e.target.closest('[data-kfilter]'); if (!st) return;
  const b = $(`#codeFilter [data-s="${st.dataset.kfilter}"]`); if (!b) return;
  $('#codeSearch').value = ''; b.click();
  $$('[data-kfilter]').forEach(x => x.classList.toggle('on', x === st));
  $('#codeFilter').scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
  const list = $('#codesTable'); list.classList.remove('flash'); void list.offsetWidth; list.classList.add('flash');
});
$$('#codeFilter [data-s]').forEach(b => b.onclick = () => { S.codeFilter = b.dataset.s; S.codeLimit = 40; $$('#codeFilter [data-s]').forEach(x => x.setAttribute('aria-pressed', x === b)); $$('[data-kfilter]').forEach(x => x.classList.toggle('on', x.dataset.kfilter === b.dataset.s)); renderCodes() });
$('#codeSearch').oninput = renderCodes;
document.addEventListener('click', e => { $$('details.menu[open]').forEach(d => { if (!d.contains(e.target)) d.open = false }) });
$('#copyShown').onclick = async () => {
  const ks = (S.codeRows || []).filter(c => c.key).map(c => c.key);
  if (!ks.length) return toast('В этом списке нет ключей для копирования');
  copy(ks.join('\n'), `Скопировано ключей: ${ks.length}`)
};
$('#codesTable').onclick = async e => {
  const b = e.target.closest('[data-a]'); if (!b) return;
  const id = b.closest('[data-id]').dataset.id, c = S.codes.find(x => x.id === id), a = b.dataset.a, v = b.dataset.v;
  if (a === 'copy' || a === 'invite') {
    await copy(a === 'copy' ? c.key : invite(c.key, c.days), a === 'copy' ? 'Ключ скопирован' : 'Приглашение скопировано — отправьте его человеку');
    if (c.status === 'new' && !c.given_at) { await codeAction('given', id, {}, true) }
    return
  }
  if (a === 'ext') return codeAction('extend', id, { days: +v });
  if (a === 'set') {
    let d = v; if (v === 'ask') { d = prompt('Срок ключа в днях (можно дробно, например 0,5). 0 — бессрочно.', c.days || 1); if (d == null) return; d = +String(d).replace(',', '.'); if (isNaN(d) || d < 0) return toast('Нужно число дней') }
    return codeAction('set_days', id, { days: +d })
  }
  if (a === 'lim') {
    let l = v; if (v === 'ask') { l = prompt('Сколько анализов в сутки? 0 — без лимита.', c.daily_limit || 0); if (l == null || isNaN(+l)) return }
    return codeAction('limit', id, { limit: Math.max(0, Math.round(+l)) })
  }
  codeAction(a, id)
};
async function codeAction(a, id, extra = {}, quiet = false) {
  const body = { action: a, ...extra };
  const c = S.codes.find(x => x.id === id);
  if (a === 'note') { const n = prompt('Кому выдан ключ (заметка):', c?.note || ''); if (n == null) return; body.note = n }
  if (a === 'delete' && !confirm('Удалить ключ навсегда? Человек с этим ключом потеряет доступ.')) return;
  if (a === 'revoke' && !confirm('Отозвать ключ? Доступ закроется сразу. Потом можно вернуть.')) return;
  await api('/api/admin/codes/' + id, { method: 'POST', body: JSON.stringify(body) });
  if (!quiet) toast({ note: 'Заметка сохранена', extend: extra.days > 0 ? `Срок продлён на ${daysTxt(extra.days)}` : 'Срок уменьшен', set_days: extra.days ? `Срок ключа: ${daysTxt(extra.days)}` : 'Ключ теперь бессрочный',
    reset: 'Срок начнётся со следующего входа', limit: 'Лимит изменён', revoke: 'Ключ отозван', restore: 'Ключ снова действует', delete: 'Ключ удалён', given: 'Отмечен как выданный', ungiven: 'Отметка снята' }[a]);
  const keep = $('#codesTable').scrollTop; await loadAdmin(); $('#codesTable').scrollTop = keep
}
S.lim = 10;
$$('#limSeg [data-l]').forEach(b => b.onclick = () => { S.lim = +b.dataset.l; $$('#limSeg [data-l]').forEach(x => x.setAttribute('aria-pressed', x === b)) });
$$('#daysSeg [data-d]').forEach(b => b.onclick = () => { S.days = +b.dataset.d; $$('#daysSeg [data-d]').forEach(x => x.setAttribute('aria-pressed', x === b)) });
$$('[data-newkey]').forEach(b => b.onclick = () => { const f = $('#createForm'); f.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' }); f.closest('.panel').classList.remove('flash'); void f.offsetWidth; f.closest('.panel').classList.add('flash'); setTimeout(() => $('#cNote').focus(), 400) });
$('#createForm').onsubmit = async e => {
  e.preventDefault();
  const r = await api('/api/admin/codes', { method: 'POST', body: JSON.stringify({ count: +$('#cCount').value || 1, days: S.days, note: $('#cNote').value, daily_limit: S.lim }) });
  S.fresh = new Set(r.keys); S.codeFilter = 'all'; S.codeLimit = 40; $('#codeSearch').value = '';
  $$('#codeFilter [data-s]').forEach(x => x.setAttribute('aria-pressed', x.dataset.s === 'all'));
  $('#cNote').value = ''; await loadAdmin(); showKeys(r.keys, S.days)
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
  dialog(one ? '✨ Ключ создан' : `✨ Создано ключей: ${keys.length}`,
    `Срок: ${daysTxt(days)}${days ? ', отсчёт начнётся с первого входа' : ''}. Ключи сохранены — их всегда видно в списке ниже.`,
    `<div class="nk-list">${keys.map(k => `<div class="nk"><code>${esc(k)}</code><button class="kb kb-blue" data-nk="key" data-k="${esc(k)}">📋 Ключ</button><button class="kb kb-violet" data-nk="inv" data-k="${esc(k)}">✉️ Приглашение</button></div>`).join('')}</div>`,
    [...(one ? [] : [['Скопировать все ключи', () => copy(keys.join('\n'), `Скопировано ключей: ${keys.length}`)]]),
     ['Готово', () => $('#dlg').close(), one ? '' : 'quiet']]);
  $('#dlgBody').onclick = async e => {
    const b = e.target.closest('[data-nk]'); if (!b) return;
    await copy(b.dataset.nk === 'key' ? b.dataset.k : invite(b.dataset.k, days), b.dataset.nk === 'key' ? 'Ключ скопирован' : 'Приглашение скопировано — отправьте его человеку');
    const c = S.codes.find(x => x.key === b.dataset.k); if (c && !c.given_at) codeAction('given', c.id, {}, true);
    b.classList.add('done')
  }
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

/* ---------- Shorts или длинные */
function renderFormats(d) {
  const f = d.formats, box = $('#formatBox');
  box.closest('.sec').classList.toggle('hidden', !f);
  if (!f) return;
  const col = (name, x, k) => `<div class="fmt-col">
      <h4>${name}</h4>
      <div class="fmt-kv">
        <div><small>Каналов в нише делают</small><b>${x.channels}</b></div>
        <div><small>Обычно просмотров</small><b>${fmt(x.median_views)}</b></div>
        <div><small>Молодых каналов</small><b>${x.young}</b></div>
        <div><small>У молодых — просмотров</small><b>${fmt(x.young_median_views)}</b></div>
        <div><small>Просм./подп. у молодых</small><b>${dec(x.young_views_per_sub)}</b></div>
        <div><small>Каналов-гигантов</small><b>${x.big_share != null ? x.big_share + '%' : '—'}</b></div>
      </div>
      ${x.examples?.length ? `<p class="hint" style="margin:10px 0 0">Растут в этом формате: ${x.examples.map(e => `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a> (${fmt(e.views)})`).join(', ')}</p>` : ''}
    </div>`;
  box.innerHTML = `<p class="verdict" style="font-size:18px;margin:0 0 18px">${esc(f.recommendation)}</p>
    <div class="fmt-grid">${col('Длинные видео', f.long)}${col('Shorts', f.shorts)}</div>`
}

/* ---------- прогноз роста */
function renderForecast(d) {
  const f = d.forecast, box = $('#forecastBox');
  if (!f) {
    box.innerHTML = '<div class="empty">Молодых каналов в нише слишком мало, чтобы честно посчитать прогноз. Попробуйте снять «строгую точность» или увеличить «Молодой канал — до, дней».</div>';
    return
  }
  const row = (label, a) => a ? `<div class="fc-row"><small>${label}</small><b>${fmt(a[1])}</b><span class="hint" style="margin:0">подписчиков · от ${fmt(a[0])} до ${fmt(a[2])}</span></div>` : '';
  box.innerHTML = `<div class="fc-grid">
      ${row('Через 3 месяца', f.subs_90)}${row('Через полгода', f.subs_180)}${row('Через год', f.subs_365)}
    </div>
    ${f.views_month ? `<p class="hint" style="margin:14px 0 0">Просмотров в месяц у таких каналов: обычно ${fmt(f.views_month[1])} (от ${fmt(f.views_month[0])} до ${fmt(f.views_month[2])}).</p>` : ''}
    <p class="hint" style="margin:6px 0 0">Посчитано по ${f.n} ${f.basis === 'rising' ? 'растущим молодым' : 'молодым'} каналам ниши: жирным — типичный результат, ниже — разброс от слабых к сильным. Если делать регулярно и попадать в темы ниши. Темп считается равномерным, поэтому на старте реальные цифры обычно ниже, а потом выше.</p>
    ${f.channels?.length ? `<p class="hint" style="margin:6px 0 0">Основа: ${f.channels.map(c => `<a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a> — ${fmt(c.subs)} за ${ageTxt(c.age_days)}`).join('; ')}.</p>` : ''}`;
  $('#fcLead').textContent = 'Сколько подписчиков набирают новички этой ниши — по темпу реальных молодых каналов.'
}

/* ---------- галерея хитов */
function renderGallery(d) {
  const g = d.gallery || [];
  $('#gallery').closest('.sec').classList.toggle('hidden', !g.length);
  $('#gallery').innerHTML = g.map(v => `<a class="g-item" href="https://www.youtube.com/watch?v=${esc(v.id)}" target="_blank" rel="noopener">
      <div class="g-img"><img loading="lazy" src="https://i.ytimg.com/vi/${esc(v.id)}/hqdefault.jpg" alt="">${v.x_median ? `<span class="g-x">×${dec(v.x_median)}</span>` : ''}</div>
      <b>${esc(v.title)}</b><small>${esc(v.channel)}${v.young ? ' · молодой' : ''} · ${fmt(v.views)} просм.</small></a>`).join('')
}

/* ---------- следить за нишей */
$('#watchBtn').onclick = async () => {
  try {
    await api('/api/watch', { method: 'POST', body: JSON.stringify({ job_id: S.job }) });
    $('#watchBtn').disabled = true; $('#watchBtn').textContent = 'Ниша отслеживается ✓';
    toast('Ниша добавлена в слежение (вкладка «Радар»). Сервис пересчитает её через неделю и покажет изменения.')
  } catch (x) { if (x.message !== 'auth') toast(x.message) }
};
async function loadWatches() {
  let r; try { r = await api('/api/watches') } catch (x) { return }
  const list = r.watches || [], admin = S.me.role === 'admin';
  $('#watchLead').textContent = 'Сервис раз в неделю сам пересчитывает отслеживаемые ниши и показывает, что изменилось: новые взлетающие каналы, рост подписчиков, новые хиты. '
    + (r.limit ? `Можно следить максимум за ${r.limit} нишами. ` : '') + 'Добавить нишу — кнопка «Следить за нишей» внизу отчёта.';
  $('#watchChips').innerHTML = chip(list.length + (r.limit ? ' из ' + r.limit : ''), 'ниш под присмотром', 'c-green') + chip('7', 'дней между пересчётами') + chip(list.filter(w => w.changes?.new_rising?.length).length, 'с новыми растущими', 'c-pink');
  if (!list.length) {
    $('#watchList').innerHTML = `<div class="panel w-empty"><div class="w-pics">${[30, 31, 32].map((n, i) => `<span class="ph-pic" style="--i:${i}"><img src="${pic(n)}" alt=""></span>`).join('')}<span class="w-eye">👁</span></div>
      <div><h3>Пока ничего не отслеживается</h3><ol class="w-steps"><li><b>Откройте отчёт по нише</b> — новый или из «Истории»</li><li><b>Нажмите «Следить за нишей»</b> внизу отчёта</li><li><b>Раз в неделю</b> здесь появятся новые растущие каналы, хиты и как изменилась оценка</li></ol>
      <button class="btn" data-go2="analyze">Перейти к анализу</button></div></div>`;
    $('[data-go2]').onclick = () => show('analyze'); return
  }
  $('#watchList').innerHTML = list.map(w => {
    const ch = w.changes, dlt = ch?.overall_delta;
    const hist = (w.history || []).map(h => h.overall).filter(x => x != null);
    return `<article class="panel watch">
      <div class="watch-h">
        <div><h3>${esc(w.title || w.url)}</h3>
          <small class="hint">${admin && w.hint ? `ключ ${esc(w.hint)} · ` : ''}пересчитано ${dt(w.last_run)} · следующий пересчёт ${dt(w.next_at)}</small></div>
        <div class="watch-score"><b class="pill ${tone(w.last_overall)}">${w.last_overall ?? '—'}</b>
          ${dlt != null && !ch.first ? `<span class="delta ${dlt > 0 ? 'up' : dlt < 0 ? 'down' : ''}">${dlt > 0 ? '▲ +' : dlt < 0 ? '▼ ' : '= '}${dlt}</span>` : ''}</div>
      </div>
      ${watchTrend(w.history || [])}
      ${ch ? watchChanges(ch) : '<p class="hint">Изменения появятся после первого пересчёта — через неделю или по кнопке «Пересчитать сейчас».</p>'}
      <div class="watch-act">
        ${w.last_job ? `<button class="btn sm" data-job="${esc(w.last_job)}">Открыть последний отчёт</button>` : ''}
        <button class="btn quiet sm" data-wrun="${esc(w.id)}" ${w.running ? 'disabled' : ''}>${w.running ? 'Пересчитывается…' : 'Пересчитать сейчас'}</button>
        <button class="btn line sm" data-wdel="${esc(w.id)}">Перестать следить</button>
      </div></article>`
  }).join('')
}
function watchChanges(ch) {
  const parts = [];
  if (ch.new_rising?.length) parts.push(`<div><h4>Новые растущие каналы</h4><div class="tags">${ch.new_rising.map(c => `<a class="tag hl" href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)} · ${fmt(c.subs)} · ${ageTxt(c.age_days)}</a>`).join('')}</div></div>`);
  if (ch.grew?.length) parts.push(`<div><h4>Быстрее всех выросли</h4><div class="tags">${ch.grew.map(g => `<a class="tag" href="${esc(g.url)}" target="_blank" rel="noopener">${esc(g.title)}: +${fmt(g.gain)} (+${dec(g.pct)}%)</a>`).join('')}</div></div>`);
  if (ch.new_hits?.length) parts.push(`<div><h4>Новые хиты</h4><div class="tags">${ch.new_hits.map(v => `<a class="tag" href="https://www.youtube.com/watch?v=${esc(v.id)}" target="_blank" rel="noopener">${esc((v.title || '').slice(0, 60))} — ×${dec(v.x_median)}</a>`).join('')}</div></div>`);
  if (!parts.length) parts.push('<p class="hint">С прошлого пересчёта заметных изменений нет.</p>');
  return `<div class="watch-ch">${parts.join('')}</div>`
}
document.addEventListener('click', async e => {
  const run = e.target.closest('[data-wrun]'), del = e.target.closest('[data-wdel]');
  try {
    if (run) { run.disabled = true; await api(`/api/watch/${run.dataset.wrun}/run`, { method: 'POST' }); toast('Пересчёт запущен. Он займёт 1–3 минуты.'); setTimeout(loadWatches, 1500) }
    if (del && confirm('Перестать следить за этой нишей?')) { await api(`/api/watch/${del.dataset.wdel}`, { method: 'DELETE' }); toast('Ниша убрана из слежения'); loadWatches() }
  } catch (x) { if (x.message !== 'auth') { toast(x.message); if (run) run.disabled = false } }
});

/* ---------- сравнение ниш */
document.addEventListener('change', e => {
  if (!e.target.classList.contains('cmp-cb')) return;
  const sel = $$('.cmp-cb:checked');
  if (sel.length > 3) { e.target.checked = false; toast('Сравнить можно до 3 отчётов'); return }
  $('#cmpBtn').disabled = sel.length < 2
});
$('#cmpBtn').onclick = () => openCompare($$('.cmp-cb:checked').map(x => x.value));
$('#cmpFromReport').onclick = async () => {  // из отчёта: в историю, этот отчёт уже отмечен
  const jid = S.job; show('history'); await loadHistory?.();
  const cb = $(`.cmp-cb[value="${jid}"]`); if (cb) { cb.checked = true; cb.dispatchEvent(new Event('change', { bubbles: true })) }
  toast('Отметьте ещё 1–2 отчёта и нажмите «Сравнить выбранные»');
};
async function openCompare(ids) {
  show('compare'); $('#cmpTable').innerHTML = '<tr><td><div class="empty">Загружаю отчёты…</div></td></tr>';
  const reps = (await Promise.all(ids.map(id => api('/api/job/' + id).catch(() => null)))).filter(j => j?.result && j.result.type !== 'topic').map(j => j.result);
  if (reps.length < 2) { $('#cmpTable').innerHTML = '<tr><td><div class="empty">Не удалось загрузить отчёты.</div></td></tr>'; return }
  reps.forEach(applyExcl);
  const rows = [
    ['Оценка ниши', r => r.niche.overall, 'max'],
    ['Спрос', r => r.niche.scores.demand, 'max'],
    ['Конкуренция', r => r.niche.scores.competition, 'min'],
    ['Окно входа', r => r.niche.scores.entry_window, 'max'],
    ['Монетизация', r => r.niche.scores.monetization, 'max'],
    ['Обычно просмотров на видео', r => r.niche.median_search_views, 'max', fmt],
    ['Каналов-гигантов', r => r.niche.big_channels_share, 'min', v => v + '%'],
    ['Растущих новичков', r => r.niche.young_rising, 'max'],
    ['Прогноз: подписчиков за 3 мес.', r => r.forecast?.subs_90?.[1], 'max', fmt],
    ['Прогноз: подписчиков за год', r => r.forecast?.subs_365?.[1], 'max', fmt],
    ['Доход новичка в месяц, $', r => r.niche.income_young?.[1], 'max', v => '$' + fmt(v)],
    ['Лучшая длина видео', r => r.niche.best_duration, null],
    ['Длинные у новичков, просмотров', r => r.formats?.long?.young_median_views, 'max', fmt],
    ['Shorts у новичков, просмотров', r => r.formats?.shorts?.young_median_views, 'max', fmt],
    ['Диапазон оценки', r => r.verification?.range ? `${r.verification.range[0]}–${r.verification.range[1]}` : null, null],
    ['Надёжность данных', r => r.verification?.level, null],
    ['Идей роликов найдено', r => ((r.video_ideas?.ideas || []).length + (r.video_ideas?.formats || []).length) || null, 'max'],
    ['Лучшая идея', r => { const i = (r.video_ideas?.ideas || [])[0]; return i ? `${i.name} (${i.label.toLowerCase()})` : null }, null],
    ['Язык и страна', r => (LANGS[r.competitor.lang] || r.competitor.lang) + (r.dynamics?.region_name ? ', ' + r.dynamics.region_name : ''), null],
  ];
  // итог: где заходить проще — по главным показателям, с весами как в оценке ниши
  const key = [['entry_window', 0.30, 'окно входа шире'], ['demand', 0.30, 'спрос выше'], ['competition', -0.25, 'конкуренция ниже'], ['monetization', 0.15, 'монетизация выше']];
  const pts = reps.map(r => key.reduce((s, [k, w]) => s + w * (r.niche.scores[k] || 0), 0));
  const bi = pts.indexOf(Math.max(...pts)), best = reps[bi], rest = reps.filter((_, i) => i !== bi);
  const why = key.filter(([k, w]) => rest.every(o => w > 0 ? best.niche.scores[k] > o.niche.scores[k] : best.niche.scores[k] < o.niche.scores[k])).map(x => x[2]);
  const close = rest.some(o => Math.abs(o.niche.overall - best.niche.overall) <= 4);
  $('#cmpSum').classList.remove('hidden');
  $('#cmpSum').innerHTML = `<div class="cmp-win">${avatar(best.competitor, 'ava-s')}<div><small>${close ? 'Почти вровень, чуть лучше' : 'Заходить проще'}</small><b>${esc(best.competitor.title)}</b>
    <span>${why.length ? why.join(', ') : 'в сумме показателей'}${close ? '. Разница небольшая — выбирайте по тому, что вам ближе снимать.' : '.'}</span></div></div>`;
  const head = `<tr><th></th>${reps.map(r => `<th>${avatar(r.competitor, 'ava-s')} ${esc(r.competitor.title)}<br><small class="hint">${(r.keywords || []).slice(0, 3).map(esc).join(', ')}</small></th>`).join('')}</tr>`;
  $('#cmpTable').innerHTML = head + rows.map(([label, get, best, f]) => {
    const vals = reps.map(r => { try { return get(r) } catch { return null } });
    const nums = vals.filter(v => typeof v === 'number');
    const b = best && nums.length > 1 ? (best === 'max' ? Math.max(...nums) : Math.min(...nums)) : null;
    return `<tr><td class="name">${label}</td>${vals.map(v => `<td class="${b != null && v === b ? 'best' : ''}">${v == null ? '—' : f ? f(v) : esc(v)}</td>`).join('')}</tr>`
  }).join('') + `<tr><td></td>${reps.map(r => `<td><button class="btn sm quiet" data-job="${esc(r._jid || '')}">Открыть отчёт</button></td>`).join('')}</tr>`;
  reps.forEach((r, i) => r._jid = ids[i]);
  $$('#cmpTable [data-job]').forEach((b, i) => b.dataset.job = ids[i])
}

/* ---------- результат поиска по теме */
function renderTopic(d) {
  S.topic = d; $('#topicResult').classList.remove('hidden'); $('#result').classList.add('hidden');
  if (!$('#topicInput').value) $('#topicInput').value = d.topic;
  $('#tTitle').textContent = `Ниши в теме «${d.topic}»`;
  $('#tVerdict').textContent = d.verdict;
  $('#tMeta').textContent = `Язык — ${(LANGS[d.lang] || d.lang)}${d.region_name ? `, поиск как из страны «${d.region_name}»` : ''}. Проверено ${d.channels_checked} каналов в ${d.subniches.length} поднишах. Отчёт от ${new Date(d.generated_at).toLocaleString('ru', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })}.`;
  $('#subList').innerHTML = d.subniches.map((s, i) => `<article class="panel sub">
      <div class="sub-h">${miniRing(s.score)}
        <div><h3>${i + 1}. ${esc(s.name)}${s.is_topic ? ' <span class="pill grey">вся тема</span>' : ''}${s.low_data ? ' <span class="pill grey">мало данных</span>' : ''}${s.reliability ? ` <span class="pill ${{ 'высокая': 'hi', 'средняя': 'mid', 'низкая': 'lo' }[s.reliability]}" title="Насколько можно доверять оценке: зависит от числа каналов и устойчивости оценки">надёжность ${s.reliability}</span>` : ''}</h3>
        <small class="hint">${s.range ? `оценка обычно ${s.range[0]}–${s.range[1]} · ` : ''}${s.channels} каналов · молодых ${s.young} · растущих новичков ${s.young_rising} · ${esc(s.rpm_category)} ~$${s.rpm_range[0]}–${s.rpm_range[1]} за 1000</small></div>
        <button class="btn sm" data-deep="${i}">Подробный анализ</button></div>
      <div class="sub-m">
        ${[['Спрос', s.scores.demand], ['Конкуренция', s.scores.competition, true], ['Окно входа', s.scores.entry_window], ['Монетизация', s.scores.monetization]].map(([t, v, inv]) =>
          `<div><small>${t}</small><div class="track"><div class="fill" style="width:${v}%;background:${toneColor(inv ? 100 - v : v)}"></div></div><b>${v}</b></div>`).join('')}
        <div><small>Обычно просмотров</small><b>${fmt(s.median_views)}</b>${s.recent_median_views != null ? `<small>за год: ${fmt(s.recent_median_views)}</small>` : ''}</div>
      </div>
      ${s.examples.length ? `<div class="sub-ex">${s.examples.map(c => `<a href="${esc(c.url)}" target="_blank" rel="noopener">${avatar(c, 'ava-s')}<span><b>${esc(c.title)}</b><small>${fmt(c.subs)} подп. · ${ageTxt(c.age_days)} · обычно ${fmt(c.median_views)}</small></span></a>`).join('')}</div>` : ''}
      ${s.top_videos.length ? `<p class="hint" style="margin:10px 0 0">Топ видео: ${s.top_videos.map(v => `<a href="https://www.youtube.com/watch?v=${esc(v.id)}" target="_blank" rel="noopener">${esc((v.title || '').slice(0, 70))}</a> (${fmt(v.views)})`).join(' · ')}</p>` : ''}
    </article>`).join('');
  $('#topicResult').scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
}
document.addEventListener('click', e => {
  const b = e.target.closest('[data-deep]'); if (!b || !S.topic) return;
  const s = S.topic.subniches[+b.dataset.deep];
  const ex = s.examples[0];
  if (!ex) { toast('В этой поднише нет подходящего канала-примера. Попробуйте другую.'); return }
  setMode('channel');
  $('#url').value = s.examples.slice(0, 2).map(c => c.url).join(' ');
  $('#kw').value = s.is_topic ? '' : s.name; optsBadge();
  $('details.opts').open = true;
  window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
  toast('Подставил каналы-примеры и ключевую фразу подниши. Нажмите «Найти нишу».')
});

/* ---------- стена из превью-хитов и карта пути */
const WALL = ["EdjUeSwmCv8", "ZeeQ4CZ-GPk", "Sr0iFg9D0Aw", "KvLWuK2TU-w", "cDf98hISlDA", "kLS5Cg_yNdM", "vDmjw-c_R2k", "o1qc29t04GI", "kZ01ojPKaQw", "SmdYWanW8tQ", "hSH0qU1odBI", "LbbaSZz7XL0", "vRtybV6XKVs", "-6ueG_azKos", "PODH_s-U2l4", "8NFqnhWHxNY", "ZaR2636dwF4", "gD319NmeSSM", "JSa31h-v4d4", "xoqNoGvMMUA", "25JUEkGBRrs", "Jb41u1XyKQY", "bFrmJMEdHCc", "Fg9YaXfrv28", "EYxWh1yP2fU", "Kg7FFad7J_8", "2USa5plcv6U", "YGsQOWqc8Kw", "lUclitPknhE", "I4cPgaItmpI", "OT42o5ey-QQ", "c-8LxsXDOYg", "OBl6n6ECVUA", "KWHb0ITnXmQ", "O0wf7bFJrqM", "afuUIpyuZkc", "kI5HquCFFHQ", "f4q2SZj4O9M", "pA3VhqEF04w", "XVNWwL7I45g", "96-s5ZktURI", "p0pGP-dNQ3c", "0awS5j1l_2k", "gb1ZePIXr3I", "PsM1x9IXdes", "Or-6O2GUab8", "Q6f_5a_4Quc", "rAWZWoZgEDk", "D2mgkP-OB4Q", "VucgIgX94Mg", "225S-BDl2Ao", "1bkWSI5IKx0", "RAq7uQAB3l4", "Oi_NMftAMJ0", "agZgIh_yTx4", "tA-3Dypr_G8"];
(function buildWall() {
  const rows = 8, per = 12, inner = $('#wall .wall-in');
  let h = '';
  for (let r = 0; r < rows; r++) {
    const ids = Array.from({ length: per }, (_, k) => WALL[(r * 7 + k * 5) % WALL.length]);
    const cells = ids.map(id => `<img src="https://i.ytimg.com/vi/${id}/mqdefault.jpg" alt="" loading="lazy" decoding="async" onerror="this.remove()">`).join('');
    h += `<div class="wall-row${r % 2 ? ' rev' : ''}" style="--d:${70 + r * 9}s">${cells}${cells}</div>`;
  }
  inner.innerHTML = h
})();
const pic = i => `https://i.ytimg.com/vi/${WALL[i % WALL.length]}/mqdefault.jpg`;
const ARROW = `<svg viewBox="0 0 90 60" class="j-arrow" aria-hidden="true"><path d="M4 40 C 28 4, 58 4, 80 30" /><path class="head" d="M70 30 L 81 31 L 78 20" /></svg>`;
function journey(el, steps) {
  el.innerHTML = `<h2 class="j-title">${steps.title}</h2><div class="j-track">` + steps.items.map((st, i) => (i ? `<div class="j-link">${ARROW}</div>` : '') + `
    <figure class="polaroid" style="--rot:${[-3, 2.5, -2, 3][i % 4]}deg;--i:${i}" ${st.go ? `data-jgo="${st.go}"` : ''}>
      <span class="tape"></span><span class="j-num">${i + 1}</span>
      <div class="j-photo">${st.photo}</div>
      <figcaption><b>${st.t}</b><small>${st.d}</small></figcaption>
    </figure>`).join('') + '</div>'
}
const ringMini = v => `<svg viewBox="0 0 64 64" class="j-ring"><circle cx="32" cy="32" r="26" /><circle cx="32" cy="32" r="26" class="v" style="--v:${v}" /><text x="32" y="38">${v}</text></svg>`;
journey($('#journeyA'), { title: 'Как это работает', items: [
  { t: 'Вставьте ссылку', d: 'на канал или видео конкурента — можно 2–3', go: 'url', photo: `<img src="${pic(3)}" alt=""><span class="j-chip"><i>🔗</i>youtube.com/@канал</span><span class="j-cursor"></span>` },
  { t: 'Сервис ищет нишу', d: 'находит похожие каналы и молодых, которые уже растут', photo: `<div class="j-collage"><img src="${pic(12)}" alt=""><img src="${pic(20)}" alt=""><img src="${pic(33)}" alt=""></div><span class="j-badge">+30 каналов</span>` },
  { t: 'Оценки и карта', d: 'спрос, конкуренция, окно входа, доход и прогноз роста', photo: `<img src="${pic(8)}" alt="" class="dim">${ringMini(74)}<span class="j-bars"><i style="--h:70%"></i><i style="--h:35%"></i><i style="--h:55%"></i><i style="--h:85%"></i></span>` },
  { t: 'План входа', d: 'темы, длина, частота, превью — и файл для чата', photo: `<img src="${pic(15)}" alt=""><span class="j-sticker">ВОТ ПОЧЕМУ…</span><span class="j-doc">⬇ для чата</span>` }] });
journey($('#journeyT'), { title: 'Путь к превью, на которые нажимают', items: [
  { t: 'Ваш канал', d: 'одна ссылка — остальное сервис сделает сам', go: 'thUrl', photo: `<img src="${pic(1)}" alt=""><span class="j-chip"><i>🔗</i>youtube.com/@ваш_канал</span><span class="j-cursor"></span>` },
  { t: 'Конкуренты', d: 'находит сам по темам ваших лучших роликов', photo: `<div class="j-collage"><img src="${pic(24)}" alt=""><img src="${pic(27)}" alt=""><img src="${pic(40)}" alt=""></div><span class="j-badge">нашёл 3 канала</span>` },
  { t: 'Что заходит', d: 'лицо, текст, цвета, стиль — с проверкой надёжности', photo: `<img src="${pic(5)}" alt=""><span class="j-x up">1,8×</span><span class="j-x dn">0,6×</span>` },
  { t: 'Варианты', d: 'текст на превью, промт для нейросети и названия', photo: `<img src="${pic(10)}" alt=""><span class="j-sticker">+3 ЧАСА</span><span class="j-doc">промт ✓ названия ✓</span>` }] });
document.addEventListener('click', e => { const f = e.target.closest('[data-jgo]'); if (f) { const i = $('#' + f.dataset.jgo); i.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' }); setTimeout(() => i.focus(), 300) } });

/* ---------- оформление страниц: фото в баннерах, карточки помощи */
$$('.ph-deco').forEach((d, k) => { d.innerHTML = [0, 1, 2].map(i => `<span class="ph-pic" style="--i:${i}"><img src="${pic(k * 9 + i * 4 + 2)}" alt="" loading="lazy"></span>`).join('') });
(function helpCards() {
  $$('.help dl').forEach(dl => {
    const items = []; let cur;
    [...dl.children].forEach(el => { if (el.tagName === 'DT') { cur = document.createElement('div'); cur.className = 'hcard'; items.push(cur) } cur && cur.append(el) });
    dl.replaceChildren(...items)
  })
})();
const chip = (v, l, cls = '') => `<span class="ph-chip ${cls}"><b>${v}</b>${l}</span>`;

/* ---------- динамика ниши во времени */
function watchTrend(H) {
  H = H.filter(h => h.overall != null);
  if (H.length < 2) return '<p class="hint">График динамики появится после второго пересчёта.</p>';
  const W = 640, Ht = 150, P = 26, xs = i => P + (W - 2 * P) * i / (H.length - 1), ys = v => Ht - P - (Ht - 2 * P) * v / 100;
  const line = (k, col, dash) => { const pts = H.map((h, i) => h[k] != null ? `${xs(i)},${ys(h[k])}` : null).filter(Boolean); return pts.length > 1 ? `<polyline points="${pts.join(' ')}" fill="none" stroke="${col}" stroke-width="${k === 'overall' ? 3 : 2}" ${dash ? 'stroke-dasharray="5 5"' : ''} stroke-linejoin="round" stroke-linecap="round"/>` : '' };
  const band = H.every(h => h.range) ? `<polygon points="${H.map((h, i) => `${xs(i)},${ys(h.range[1])}`).concat(H.map((h, i) => `${xs(i)},${ys(h.range[0])}`).reverse()).join(' ')}" fill="var(--primary)" opacity=".1"/>` : '';
  const first = H[0].overall, last = H[H.length - 1].overall, d = last - first;
  const widths = H.filter(h => h.range).map(h => h.range[1] - h.range[0]);
  const noise = Math.max(4, widths.length ? widths.reduce((a, b) => a + b) / widths.length / 2 : 6);
  const v0 = H.find(h => h.views)?.views, v1 = [...H].reverse().find(h => h.views)?.views;
  const vd = v0 && v1 ? Math.round((v1 / v0 - 1) * 100) : null;
  const verdict = Math.abs(d) < noise ? ['flat', `Ниша стабильна: оценка ${first} → ${last}, изменение в пределах погрешности (±${Math.round(noise)})`]
    : d > 0 ? ['up', `Ниша растёт: оценка ${first} → ${last} (+${d}) — больше погрешности, изменение настоящее`]
    : ['down', `Ниша остывает: оценка ${first} → ${last} (${d}) — больше погрешности, изменение настоящее`];
  return `<div class="wt"><div class="wt-v ${verdict[0]}">${verdict[0] === 'up' ? '📈' : verdict[0] === 'down' ? '📉' : '➖'} ${verdict[1]}${vd != null && H.length > 1 ? `. Просмотры видео ниши: ${vd > 0 ? '+' : ''}${vd}%` : ''}.</div>
    <svg viewBox="0 0 ${W} ${Ht}" class="wt-svg" role="img" aria-label="Динамика оценки ниши">
      ${[25, 50, 75].map(v => `<line x1="${P}" x2="${W - P}" y1="${ys(v)}" y2="${ys(v)}" stroke="var(--line)"/>`).join('')}
      ${band}${line('demand', '#12A150', true)}${line('window', '#E89A00', true)}${line('overall', 'var(--primary)')}
      ${H.map((h, i) => `<circle cx="${xs(i)}" cy="${ys(h.overall)}" r="4" fill="var(--primary)"><title>${new Date(h.t * 1000).toLocaleDateString('ru')} — оценка ${h.overall}</title></circle>`).join('')}
      ${H.map((h, i) => (i === 0 || i === H.length - 1 || H.length <= 6) ? `<text x="${xs(i)}" y="${Ht - 6}" text-anchor="middle" font-size="8.5" fill="var(--muted)">${new Date(h.t * 1000).toLocaleDateString('ru', { day: 'numeric', month: 'short' })}</text>` : '').join('')}
    </svg>
    <div class="wt-leg"><span><i style="background:var(--primary)"></i>оценка ниши</span><span><i style="background:#12A150"></i>спрос</span><span><i style="background:#E89A00"></i>окно входа</span>${band ? '<span><i style="background:var(--primary);opacity:.25"></i>погрешность</span>' : ''}</div></div>`
}

/* ---------- проверка данных */
function renderVerify(d) {
  const V = d.verification, box = $('#verBox');
  $('#verSec').classList.toggle('hidden', !V);
  if (!V) return;
  const lv = { 'высокая': 'hi', 'средняя': 'mid', 'низкая': 'lo' }[V.level] || 'mid';
  const ic = { ok: '✓', warn: '!', bad: '✕' };
  box.className = 'panel ver lv-' + lv;
  box.innerHTML = `<div class="ver-h"><span class="ver-shield">🛡</span><div><h3>Проверка данных</h3>
      <p>Перед выдачей отчёт перепроверяется: цифры сверяются со страницами видео YouTube, отсеиваются накрученные и перезапущенные каналы, оценка пересчитывается на сотнях случайных выборок.</p></div>
      <span class="ver-level">Надёжность: <b>${esc(V.level)}</b></span></div>
    <ul class="ver-list">${V.checks.map(c => `<li class="st-${c.state}"><i>${ic[c.state] || '•'}</i><span><b>${esc(c.title)}</b>${esc(c.detail)}</span></li>`).join('')}</ul>
    ${V.level === 'низкая' ? '<p class="ver-note">Данных мало или они противоречивы — используйте оценку как ориентир. Попробуйте добавить 2–3 канала-примера или свои ключевые слова в настройках поиска.</p>' : ''}`
}

/* ---------- превью */
const xf = v => v == null ? '—' : String(+(+v).toFixed(2)).replace('.', ',') + '×';
const confPill = c => c ? `<span class="pill ${c === 'высокая' ? 'hi' : c === 'средняя' ? 'mid' : 'grey'}">надёжность ${c}</span>` : '';
const ytImg = id => `https://i.ytimg.com/vi/${encodeURIComponent(id)}/mqdefault.jpg`;
try { $('#thUrl').value = store.get('nf_own') || '' } catch {}
$('#thScript').oninput = () => { const n = $('#thScript').value.length; $('#thChars').textContent = n ? `${n} знаков${n > 25000 ? ' — лимит 30 000' : ''}` : '' };
$('#thForm').onsubmit = async e => {
  e.preventDefault();
  const url = $('#thUrl').value.trim(); if (!url) return;
  store.set('nf_own', url);
  $('#thErr').textContent = ''; $('#thGo').disabled = true; $('#thResult').classList.add('hidden');
  thProgress(1, 'Запускаю…');
  try {
    const r = await api('/api/thumbs', { method: 'POST', body: JSON.stringify({ url, competitors: $('#thComp').value.trim() || null, topic: $('#thTopic').value.trim() || null, script: $('#thScript').value.trim() || null }) });
    S.job = r.job_id; history.replaceState(null, '', '?job=' + S.job); refreshMe(); thPoll()
  } catch (x) { $('#thGo').disabled = false; $('#thProgress').classList.add('hidden'); if (x.message !== 'auth') $('#thErr').textContent = x.message }
};
function thProgress(p, m) { $('#thProgress').classList.remove('hidden'); $('#thMsg').textContent = m; $('#thPct').textContent = Math.round(p) + '%'; $('#thBar').style.width = p + '%' }
async function thPoll() {
  try {
    const j = await api('/api/job/' + S.job);
    if (j.status === 'done') { $('#thGo').disabled = false; $('#thProgress').classList.add('hidden'); renderThumbs(j.result) }
    else if (j.status === 'error') { $('#thGo').disabled = false; $('#thProgress').classList.add('hidden'); $('#thErr').textContent = j.error || 'Не получилось разобрать превью.'; refreshMe() }
    else { thProgress(j.progress, j.message); setTimeout(thPoll, 1500) }
  } catch (x) { $('#thGo').disabled = false; if (x.message !== 'auth') $('#thErr').textContent = x.message }
}
function thCard(v, own) {
  const p = v.perf, cls = p >= 1.5 ? '' : p <= 0.67 ? ' lo' : ' mid';
  return `<a class="g-item" href="https://www.youtube.com/watch?v=${esc(v.id)}" target="_blank" rel="noopener">
    <div class="g-img"><img loading="lazy" src="${ytImg(v.id)}" alt=""><span class="g-x${cls}">${xf(p)}</span></div>
    <b>${esc((v.title || '').slice(0, 90))}</b>
    <small>${fmt(v.views)} просмотров${!own && v.channel ? ' · ' + esc(v.channel) : ''}</small>
    <span class="tags">${v.tags.map(t => `<span class="tag">${esc(t)}</span>`).join('')}${(v.hooks || []).map(h => `<span class="tag hl">${esc(h)}</span>`).join('')}</span>
    ${v.text ? `<small class="th-ocr">Текст: «${esc(v.text)}»</small>` : ''}</a>`
}
function thVisual(g, title) {
  if (!g) return `<h3 class="th-h3">${title}</h3><p class="hint">Добавьте ссылки на конкурентов, чтобы сравнить.</p>`;
  const rows = g.visual.filter(e => e.ratio).slice(0, 9);
  if (!rows.length) return `<h3 class="th-h3">${title}</h3><p class="hint">Превью слишком похожи друг на друга, чтобы найти различия.</p>`;
  return `<h3 class="th-h3">${title} <small>${g.n} превью, хитов ${g.hits}</small></h3>` + rows.map(e => {
    const r = e.ratio, w = Math.min(50, Math.abs(Math.log2(r)) * 50), col = r >= 1.05 ? 'var(--hi)' : r <= 0.95 ? 'var(--lo)' : 'var(--faint)';
    return `<div class="feat${e.confidence === 'низкая' ? ' weak' : ''}"><div>${esc(e.name)}<small>с ним ${xf(e.with)} · без него ${xf(e.without)} · в ${e.n_with} из ${e.n_with + e.n_without} превью · надёжность ${e.confidence}</small></div>
      <div class="bar"><span style="${r >= 1 ? 'left:50%' : 'right:50%;left:auto'};width:${w}%;background:${col}"></span></div><div class="x">${xf(r)}</div></div>`
  }).join('')
}
function thCompTip(c) {
  const t = [];
  if (c.sim != null) t.push('похожесть тематики ' + c.sim + '%');
  if (c.topic_coverage != null) t.push('по вашей теме ' + Math.round(c.topic_coverage * 100) + '% роликов');
  if (c.category) t.push('категория YouTube: ' + (CAT_RU[c.category] || c.category));
  if (c.lang_source) t.push('язык подтверждён: ' + c.lang_source);
  if (c.rescued) t.push('тема подтверждена по описаниям и обложкам: ' + c.rescued);
  if (c.views_diff != null) t.push('цифры сверены, расхождение ' + Math.round(c.views_diff * 100) + '%');
  return t.join(' · ')
}
// какие каналы проверены и почему отсеяны — та же строгость, что в анализе ниши
function thChecks(d) {
  if (!d.competitors_auto) return '';
  const rj = d.rejected || [], hc = d.hidden_channels || [], comps = d.competitors || [];
  const diffs = comps.map(c => c.views_diff).filter(x => x != null);
  const parts = [];
  if (comps.length) parts.push(`✓ Конкуренты проверены как в анализе ниши: тот же язык${comps.some(c => c.lang_source) ? ' (по звуку видео)' : ''} и страна, своя тематика, категория YouTube` + (diffs.length ? `, цифры сверены со страницами видео (расхождение до ${Math.round(Math.max(...diffs) * 100)}%)` : ''));
  let h = parts.length ? `<p class="hint th-check">${esc(parts[0])}.</p>` : '';
  if (rj.length) h += `<details class="th-rej"><summary>Отсеяно ${rj.length} ${plural(rj.length, 'похожий канал', 'похожих канала', 'похожих каналов')} — почему</summary><ul>${rj.map(r => `<li><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title || '')}</a> — ${esc(r.reason)}</li>`).join('')}</ul></details>`;
  if (hc.length) h += `<p class="hidden-ch">🙈 ${hc.length === 1 ? 'Не подобран канал, который вы раньше убрали' : `Не подобраны ${hc.length} ${plural(hc.length, 'канал', 'канала', 'каналов')}, которые вы раньше убрали`}: ` + hc.map(x => `<span class="hc">${esc(x.title || x.id)} <button class="link-btn" data-thforget="${esc(x.id)}">вернуть</button></span>`).join(', ') + '</p>';
  return h
}
document.addEventListener('click', async e => {
  const x = e.target.closest('[data-thx]'), f = e.target.closest('[data-thforget]');
  if (!x && !f) return;
  try {
    if (x) {
      await api(`/api/job/${S.thJob || S.job}/thumbs-exclude`, { method: 'POST', body: JSON.stringify({ channel_id: x.dataset.thx, title: x.dataset.tht }) });
      x.closest('.tag-wrap').classList.add('gone'); x.remove();
      toast('Запомнил: при следующем разборе превью этого канала подберу другого конкурента')
    } else {
      await api('/api/niche-excl/forget', { method: 'POST', body: JSON.stringify({ comp_id: 'th:' + S.th.channel.id, channel_id: f.dataset.thforget }) });
      S.th.hidden_channels = (S.th.hidden_channels || []).filter(h => h.id !== f.dataset.thforget); f.closest('.hc').remove();
      toast('Канал снова может быть подобран — со следующего разбора')
    }
  } catch (err) { if (err.message !== 'auth') toast(err.message) }
});
function ownVsCard(v) {  // новый канал: ваше превью рядом с тем, что работает в нише
  return `<a class="g-item" href="https://www.youtube.com/watch?v=${esc(v.id)}" target="_blank" rel="noopener">
    <div class="g-img"><img loading="lazy" src="${ytImg(v.id)}" alt="">${v.style_best ? '<span class="g-x">стиль хитов ✓</span>' : ''}</div>
    <b>${esc((v.title || '').slice(0, 90))}</b>
    <small>${fmt(v.views)} просмотров · ${esc(v.style)}${v.age != null && v.age < 7 ? ' · вышел ' + (v.age ? v.age + ' дн. назад' : 'сегодня') : ''}</small>
    <span class="tags">${v.has.map(t => `<span class="tag hl">✓ ${esc(t.toLowerCase())}</span>`).join('')}${v.missing.map(t => `<span class="tag">нет: ${esc(t.toLowerCase())}</span>`).join('')}${v.avoid.map(t => `<span class="tag warn">⚠ ${esc(t.toLowerCase())}</span>`).join('')}</span></a>`
}
function renderThumbs(d) {
  S.th = d;
  const comp = d.comp, nc = d.new_channel;
  const own = d.own || { n: 0, hits: 0, flops: 0, top: [], weak: [], styles: [], visual: [], recipe: null };
  $('#thResult').classList.remove('hidden');
  const comps = d.competitors || [];
  $('#thHead').innerHTML = `<div class="th-h">${d.channel.avatar ? `<img class="ava" src="${esc(d.channel.avatar)}" alt="">` : ''}
    <div><small class="hint" style="margin:0">Разбор превью</small><h2><a href="${esc(d.channel.url)}" target="_blank" rel="noopener">${esc(d.channel.title)}</a></h2>
    <div class="tags">${comps.length ? (d.competitors_auto ? 'Конкуренты (нашёл сам): ' : 'Конкуренты: ') + comps.map(c => `<span class="tag-wrap"><a class="tag" href="${esc(c.url)}" target="_blank" rel="noopener" title="${esc(thCompTip(c))}">${esc(c.title)} · ${c.n} превью${c.lang_source ? ' ✓' : ''}</a>${d.competitors_auto && c.id ? `<button class="tag-x" data-thx="${esc(c.id)}" data-tht="${esc(c.title)}" title="Не тот конкурент — больше не подбирать">✕</button>` : ''}</span>`).join(' ') : `<span class="hint" style="margin:0">${d.competitors_auto ? 'Похожих каналов на том же языке не нашлось — впишите конкурентов вручную' : 'Каналы конкурентов не открылись — проверьте ссылки'}</span>`}</div></div>
    <div class="th-facts">${nc ? `<div class="fact"><small>Превью ниши</small><b>${comp ? comp.n : 0}</b></div><div class="fact"><small>Ваших роликов</small><b>${nc.videos}</b></div>`
      : `<div class="fact"><small>Превью разобрано</small><b>${own.n + (comp ? comp.n : 0)}</b></div>
      <div class="fact"><small>Ваших хитов</small><b>${own.hits}</b></div><div class="fact"><small>Слабых</small><b>${own.flops}</b></div>`}</div></div>
    ${nc ? `<div class="th-new"><b>🌱 Новый канал — смотрю, что работает в нише</b><span>${nc.videos ? `На канале ${nc.videos} ${plural(nc.videos, 'ролик', 'ролика', 'роликов')}` : 'На канале пока нет роликов'}, а чтобы оценить свои превью, нужно хотя бы 8 старше недели — иначе их не с чем сравнить. Поэтому все выводы ниже — по хитам похожих каналов${nc.videos ? ', а ваши превью показаны рядом с ними' : ''}. Когда роликов станет больше, запустите разбор снова — появится ваша статистика.</span></div>` : ''}
    ${thChecks(d)}
    ${d.ocr ? '' : '<p class="hint">Распознавание текста на картинке на этом сервере не установлено — выводы про текст недоступны.</p>'}`;
  $('#thSummary').innerHTML = d.summary.map((x, i) => `<li><i>${i + 1}</i><span>${esc(x)}</span></li>`).join('');
  // стиль
  const bs = d.best_style, rc = (own.hits >= 6 ? own.recipe : (comp || {}).recipe) || own.recipe;
  if (nc && bs) bs.closest = null;
  const stRows = (g, who) => g ? `<div class="panel"><h3 class="th-h3">${who}</h3>` + g.styles.map(x => `<div class="feat${x.n < 3 ? ' weak' : ''}"><div>${esc(x.name)}<small>${x.n} превью (${x.share}%) · хитов ${x.hit_rate}%</small></div>
      <div class="bar"><span style="${x.median >= 1 ? 'left:50%' : 'right:50%;left:auto'};width:${Math.min(50, Math.abs(Math.log2(x.median)) * 50)}%;background:${x.median >= 1.05 ? 'var(--hi)' : x.median <= 0.95 ? 'var(--lo)' : 'var(--faint)'}"></span></div><div class="x">${xf(x.median)}</div></div>`).join('') + '</div>' : '';
  $('#thStyle').innerHTML = (bs ? `<div class="panel th-best"><small class="hint" style="margin:0">Лучший стиль</small><h3>${esc(bs.name)}</h3><p>${esc(bs.desc)}.</p>
      ${nc ? `<p class="hint" style="margin:0">Так оформлены самые успешные ролики ниши — начните с этого стиля.</p>` : bs.closest && bs.closest.key !== bs.key ? `<p class="hint" style="margin:0">Сейчас у вас чаще всего «${esc(bs.closest.name)}» (${bs.closest.share}% превью), а лучшим стилем сделано ${bs.yours_share}%. Попробуйте чаще делать «${esc(bs.name)}».</p>` : `<p class="hint" style="margin:0">Этот стиль у вас уже основной — держитесь его.</p>`}
      ${rc ? `<div class="th-recipe"><h4>Как выглядят хиты${own.hits >= 6 ? ' на вашем канале' : ' в нише'}</h4><ul>
        <li><b>Лицо:</b> ${rc.face_share}% хитов${rc.face_share ? `, обычно ${rc.face_pct >= 6 ? 'крупно' : 'небольшое'}${rc.face_side ? ', ' + ({ left: 'слева', right: 'справа', center: 'по центру' })[rc.face_side] : ''}` : ''}</li>
        <li><b>Текст:</b> ${rc.text_share}% хитов${rc.words ? `, обычно ${rc.words} ${plural(rc.words, 'слово', 'слова', 'слов')}` : ''}</li>
        <li><b>Картинка:</b> ${rc.bright < 40 ? 'тёмная' : rc.bright > 60 ? 'светлая' : 'средней яркости'}, ${rc.sat > 40 ? 'насыщенные' : 'спокойные'} цвета</li>
        <li><b>Цвета:</b> ${rc.colors.join(', ') || '—'} <span class="sw">${rc.swatches.map(h => `<i style="background:${esc(h)}"></i>`).join('')}</span></li></ul></div>` : ''}</div>` : '') +
    `<div class="th-st2">${stRows(d.own, 'У вас')}${stRows(comp, nc ? 'В нише' : 'У конкурентов')}</div>`;
  $('#thVisOwn').classList.toggle('hidden', !!nc);
  $('#thVisOwn').innerHTML = nc ? '' : thVisual(own, 'У вас');
  $('#thVisComp').innerHTML = thVisual(comp, nc ? 'В нише' : 'У конкурентов');
  $('#thOwnH').textContent = nc ? 'Ваши превью рядом с хитами ниши' : 'Ваши лучшие и слабые превью';
  $('#thOwnLead').textContent = nc ? 'Без оценки успеха — роликов пока мало. Зелёным — что из работающего в нише у превью уже есть, серым — чего не хватает.' : 'Значок — во сколько раз ролик набрал больше или меньше соседних роликов канала.';
  $('#thOwnSec').classList.toggle('hidden', !!nc && !(d.own_vs || []).length);
  $('#thTop').innerHTML = nc ? (d.own_vs || []).map(ownVsCard).join('') : own.top.slice(0, 8).map(v => thCard(v, true)).join('');
  $('#thWeak').innerHTML = nc ? '' : own.weak.map(v => thCard(v, true)).join('');
  $('#thWeakH').classList.toggle('hidden', !!nc || !own.weak.length);
  $('#thCopyH').textContent = nc ? 'Что взять из хитов ниши' : 'Что взять у конкурентов';
  $('#thCopyLead').textContent = nc ? 'Приёмы, с которыми ролики похожих каналов набирают больше просмотров.' : 'То, что приносит конкурентам просмотры, а у вас встречается редко.';
  $('#thCompSec').classList.toggle('hidden', !comp);
  if (comp) $('#thCompTop').innerHTML = comp.top.slice(0, 8).map(v => thCard(v, false)).join('');
  $('#thCopySec').classList.toggle('hidden', !comp);
  $('#thCopy').innerHTML = d.copy.length ? d.copy.map(c => `<li><i>${c.kind === 'название' ? 'Аа' : c.kind === 'стиль' ? '◧' : '◉'}</i><span><b>${esc(c.name)}</b> — ${c.kind === 'стиль' ? 'обычно' : 'с этим'} ${xf(c.ratio)} ${nc ? 'в нише' : 'у конкурентов'}. ${esc(c.text)} ${confPill(c.confidence)}</span></li>`).join('')
    : nc ? '<li><i>·</i><span>Надёжных приёмов в хитах ниши не нашлось — у похожих каналов превью работают примерно одинаково. Ориентируйтесь на лучший стиль и хиты выше.</span></li>'
    : '<li><i>✓</i><span>Явных приёмов, которых у вас нет, а у конкурентов они работают, не нашлось. Ваши превью уже используют то же, что и хиты конкурентов.</span></li>';
  const a = d.attract;
  $('#thTrig').innerHTML = `<h3 class="th-h3">Приёмы в названиях, которые поднимают просмотры</h3>` + (a.triggers.length ? a.triggers.map(t => `<div class="th-trig"><b>${esc(t.name)}</b> <span class="pill hi">${xf(t.ratio)}</span> ${confPill(t.confidence)}
      ${t.examples.length ? `<small>${t.examples.map(x => '«' + esc(x) + '»').join('<br>')}</small>` : ''}</div>`).join('') : '<p class="hint">Явных приёмов не нашлось — названия работают примерно одинаково.</p>');
  $('#thPain').innerHTML = `<h3 class="th-h3">${d.script && d.script.pains.length ? 'Какую боль показать (из вашего сценария)' : 'Боль, на которую кликают в нише'}</h3>` +
    (a.pains.length ? `<ul class="th-list">${a.pains.map(p => `<li>${esc(p)}</li>`).join('')}</ul>` : '<p class="hint">Вставьте сценарий — сервис найдёт в нём боль зрителя.</p>') +
    (a.promises.length ? `<h3 class="th-h3" style="margin-top:16px">Что пообещать</h3><ul class="th-list">${a.promises.map(p => `<li>${esc(p)}</li>`).join('')}</ul>` : '');
  // варианты
  const cons = d.concepts || [];
  $('#thConLead').classList.toggle('hidden', !cons.length);
  $('#thConcepts').innerHTML = cons.length ? cons.map((c, i) => `<div class="panel th-con">
      <div class="th-con-h"><span class="th-num">${i + 1}</span><div><h3>Вариант «${esc(c.name)}»</h3><small>${esc(c.why)}${c.evidence ? ` · в названиях ниши такой приём даёт ${xf(c.evidence.ratio)} (${c.evidence.n} роликов, надёжность ${c.evidence.confidence})` : ''}</small></div></div>
      <div class="th-con-b">
        <div><h4>Текст на превью</h4>${c.thumb_text.length ? `<div class="th-texts">${c.thumb_text.map(t => `<button class="th-tt" data-copy="${esc(t)}">${esc(t)}</button>`).join('')}</div>` : '<p class="hint">По данным превью без текста работают лучше — оставьте картинку чистой.</p>'}
          ${c.emotion ? `<h4>Эмоция в кадре</h4><p>${esc(c.emotion)}</p>` : '<h4>В кадре</h4><p>Без человека: крупно предмет или сцена — у вас лучше заходят превью без лица.</p>'}</div>
        <div><h4>Названия к этому превью</h4><ol class="th-titles">${c.titles.map(t => `<li><span>${esc(t)}</span><button class="icon-btn" data-copy="${esc(t)}">Копировать</button></li>`).join('')}</ol></div>
      </div>
      <details class="th-prompt" open><summary>Промт для нейросети</summary>
        <div class="th-pr"><small>На русском — для Шедеврума, Кандинского, Nano Banana, GPT</small><p>${esc(c.prompt_ru)}</p><button class="btn quiet sm" data-copy="${esc(c.prompt_ru)}">Скопировать промт</button></div>
        <div class="th-pr"><small>На английском — для Midjourney, Flux, Ideogram. Слова в [скобках] переведите</small><p>${esc(c.prompt_en)}</p><button class="btn quiet sm" data-copy="${esc(c.prompt_en)}">Скопировать промт</button></div>
        <p class="hint" style="margin:6px 0 0">Надпись лучше добавить потом в редакторе (Canva, Photoshop): нейросети часто искажают русские буквы.</p></details>
    </div>`).join('') : `<div class="panel empty-cta"><b>Вставьте сценарий нового ролика</b><p class="hint">Тогда здесь появятся 3–4 варианта превью: текст на картинку, эмоция, промты для нейросети и по три названия к каждому варианту — по закономерностям, которые нашлись выше.</p><button class="btn sm" id="thAddScript">Добавить сценарий</button></div>`;
  const add = $('#thAddScript'); if (add) add.onclick = () => { $('#thUrl').value = d.channel.url; $('#thComp').value = d.competitors_auto ? '' : comps.map(c => c.url).join(' '); window.scrollTo({ top: 0, behavior: 'smooth' }); $('#thScript').focus() };
  window.scrollTo({ top: $('#thResult').offsetTop - 80, behavior: reduce ? 'auto' : 'smooth' })
}
$('#thShare').onclick = () => copy(location.origin + '/?job=' + S.job, 'Ссылка скопирована. Открыть её сможет любой, у кого есть ключ.');
document.addEventListener('click', e => { const b = e.target.closest('[data-copy]'); if (b) { e.preventDefault(); copy(b.dataset.copy) } });

/* ---------- настройки поиска: сколько изменено */
const OPT_DEF = { kw: '', excl: '', mc: '30', pq: '25', age: '730', lang: 'auto', region: 'auto' };
function optsBadge() {
  let n = Object.entries(OPT_DEF).filter(([id, v]) => ($('#' + id).value || '').trim() !== v).length;
  n += !$('#sameLang').checked + !$('#strict').checked;
  const b = $('#optsCount'); b.textContent = n ? `изменено: ${n}` : 'по умолчанию'; b.classList.toggle('on', !!n)
}
$('details.opts').addEventListener('input', optsBadge); $('details.opts').addEventListener('change', optsBadge);
$('#optsReset').onclick = () => { Object.entries(OPT_DEF).forEach(([id, v]) => $('#' + id).value = v); $('#sameLang').checked = $('#strict').checked = true; optsBadge(); toast('Настройки сброшены') };

/* ---------- старт */
S.startHash = location.hash;
(async function init() {
  const p = new URLSearchParams(location.search);
  const urlKey = p.get('key'), job = p.get('job');
  if (urlKey) history.replaceState(null, '', job ? '?job=' + job : location.pathname);
  if (ADMIN_PAGE) { document.body.classList.add('admin-page'); $('#loginTitle').textContent = 'Вход администратора'; $('#loginText').textContent = 'Только для владельца сервиса. Введите бессрочный ключ администратора — он запомнится в этом браузере.'; $('#keyInput').placeholder = 'NF-ADMIN-XXXX-XXXX-XXXX-XXXX'; document.title = 'Админ — Niche Finder' }
  const key = urlKey || store.get(KEY_SLOT);
  if (location.hash === '#pricing' && !ADMIN_PAGE) history.replaceState(null, '', location.pathname + location.search);
  const goPricing = () => { if (location.hash === '' && /pricing/.test(S.startHash || '')) show('pricing') };
  if (!key) { if (ADMIN_PAGE) return logout(); enterGuest(); goPricing(); if (job) openReport(job); return }
  try { await login(key, !urlKey) } catch (x) {
    if (x.message !== 'auth') { if (ADMIN_PAGE) logout(x.message); else { store.set(KEY_SLOT, null); S.key = null; enterGuest(urlKey ? 'Ключ из ссылки не подошёл или его срок истёк.' : 'Срок ключа истёк. Продлите доступ или введите новый ключ.') } }
    return
  }
  goPricing();
  if (job) openReport(job)
})();

loadPromo(); setInterval(loadPromo, 5 * 60 * 1000);

/* ---------- админ: акция */
async function loadPromoAdmin() {
  let r; try { r = await api('/api/admin/promo') } catch { return }
  S.promoAdm = r; renderPromoAdmin()
}
function promoChatText(p) {
  const t = s => new Date(s * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' });
  const msk = t(p.ends_at), future = p.started_at > Date.now() / 1000 + 5;
  const url = (S.publicUrl || location.origin) + '/?src=promo';
  return `🔥 Niche Finder — бесплатно для всех, только ${p.hours} ${plural(Math.round(p.hours), 'час', 'часа', 'часов')}!\n\n` +
    `Найдите свою нишу на YouTube: сервис сам находит молодые каналы, которые уже растут, оценивает спрос, конкуренцию и окно входа, разбирает превью и названия.\n\n` +
    `🎁 ${p.attempts} ${attWord(p.attempts)} каждому — одна кнопка, без оплаты\n⏳ ${future ? `Акция ${mskDay(p.started_at)} с ${t(p.started_at)} до ${msk} по Москве` : `Акция до ${msk} по Москве`}\n\n👉 ${url}`;
}
function mskDay(s) {
  const f = d => d.toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' });
  const d = new Date(s * 1000), now = new Date();
  return f(d) === f(now) ? 'сегодня' : f(d) === f(new Date(now.getTime() + 86400000)) ? 'завтра' : d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', ...(d.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}), timeZone: 'Europe/Moscow' })
}
function mskNowInput(addMin) {  // текущее московское время для поля datetime-local
  const d = new Date(Date.now() + (addMin || 0) * 60000);
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(d).map(x => [x.type, x.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour === '24' ? '00' : parts.hour}:${parts.minute}`
}
function renderPromoAdmin() {
  const r = S.promoAdm || {}, p = r.promo, el = $('#promoPanel');
  if (r.upcoming && p) {
    const t = s => new Date(s * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' });
    el.innerHTML = `<div class="pa-live">
        <div class="big-num up"><small>Начнётся сама через</small><b class="js-adm-promo" data-end="${p.started_at}">${hms(p.started_at - Date.now() / 1000)}</b></div>
        <div class="pa-stats"><span>🗓 <b>${mskDay(p.started_at)}</b> с <b>${t(p.started_at)}</b> до <b>${t(p.ends_at)}</b> по Москве</span>
          <span>${p.hours} ${plural(Math.round(p.hours), 'час', 'часа', 'часов')} · ${p.attempts} ${attWord(p.attempts)} каждому</span>
          <span>Включится и выключится сама — сайт можно не открывать.</span></div>
      </div>
      <h4 style="margin:16px 0 6px">Текст для чата (можно отправить заранее)</h4>
      <textarea class="field pa-text" id="promoText" rows="8">${esc(promoChatText(p))}</textarea>
      <div class="row" style="margin-top:10px;gap:8px;flex-wrap:wrap">
        <button class="btn sm" id="promoCopy">Скопировать текст</button>
        <button class="btn line sm" id="promoCancel">Отменить акцию</button>
      </div>`;
    $('#promoCopy').onclick = () => copy($('#promoText').value);
    $('#promoCancel').onclick = async () => { if (!confirm('Отменить запланированную акцию?')) return; await api('/api/admin/promo', { method: 'DELETE' }); toast('Акция отменена'); loadPromoAdmin(); loadPromo() };
    return
  }
  if (r.active && p) {
    const st = r.stats || {}, txt = promoChatText(p);
    el.innerHTML = `<div class="pa-live">
        <div class="big-num"><small>Выключится сама через</small><b class="js-adm-promo" data-end="${p.ends_at}">${hms(p.ends_at - Date.now() / 1000)}</b></div>
        <div class="big-num att"><small>Попыток каждому</small><b>${p.attempts}</b></div>
        <div class="pa-stats">${p.announced_to ? `<span>✈ Разослано в Telegram: <b>${p.announced_to.sent}</b></span>` : p.broadcast ? '<span>✈ Рассылка в Telegram — в течение минуты</span>' : ''}<span><b>${st.keys || 0}</b> взяли ключ</span><span><b>${st.users || 0}</b> запустили анализ</span><span><b>${st.runs || 0}</b> анализов всего</span></div>
      </div>
      <h4 style="margin:16px 0 6px">Текст для чата</h4>
      <textarea class="field pa-text" id="promoText" rows="8">${esc(txt)}</textarea>
      <div class="row" style="margin-top:10px;gap:8px;flex-wrap:wrap">
        <button class="btn sm" id="promoCopy">Скопировать текст</button>
        <button class="btn line sm" id="promoStop">Остановить акцию</button>
        <label class="hint" style="display:flex;gap:6px;align-items:center;margin:0"><input type="checkbox" id="promoRevoke"> и сразу отключить выданные ключи</label>
        <button class="btn line sm" id="promoRefresh">Обновить цифры</button>
      </div>`;
    $('#promoCopy').onclick = () => copy($('#promoText').value);
    $('#promoRefresh').onclick = loadPromoAdmin;
    $('#promoStop').onclick = async () => {
      const rv = $('#promoRevoke').checked;
      if (!confirm('Остановить акцию? Новые бесплатные ключи выдаваться не будут' + (rv ? ', а выданные отключатся сразу.' : '. Уже выданные доработают до конца срока.'))) return;
      await api('/api/admin/promo?revoke=' + rv, { method: 'DELETE' }); toast('Акция остановлена'); loadPromoAdmin(); loadPromo()
    };
  } else {
    const last = p ? `<p class="hint">Прошлая акция: ${p.hours} ч, ${p.attempts} ${attWord(p.attempts)}, ключей взяли: ${(r.stats || {}).keys || 0}, анализов: ${(r.stats || {}).runs || 0}.</p>` : '';
    el.innerHTML = `<form class="pa-form" id="promoForm">
        <label>Сколько часов<input class="field" type="number" id="promoHours" min="0.25" max="72" step="0.25" value="${p ? p.hours : 3}"></label>
        <label>Попыток каждому<input class="field" type="number" id="promoAtt" min="1" max="50" value="${p ? p.attempts : 5}"></label>
        <div class="pa-when" role="radiogroup" aria-label="Когда начать">
          <label><input type="radio" name="promoWhen" value="now" checked> Сейчас</label>
          <label><input type="radio" name="promoWhen" value="at"> По московскому времени</label>
          <input class="field" type="datetime-local" id="promoAt" value="${mskNowInput(60).slice(0, 14)}00" disabled>
        </div>
        <label class="pa-bc"><input type="checkbox" id="promoBc" checked> Разослать в Telegram подписчикам бота${r.subscribers != null ? ` (${r.subscribers})` : ''}, когда начнётся</label>
        <button class="btn" id="promoGo">🎁 Запустить акцию</button>
      </form>${last}
      <p class="hint"><b>Акция выключится сама</b> ровно через указанное время — отключать не нужно. Доступ у всех заканчивается в один момент, в конце акции, кто бы когда ни зашёл: пришедший за 20 минут до конца получит 20 минут. Один бесплатный ключ на устройство (по IP), любой анализ — одна попытка.</p>`;
    const when = () => $('input[name=promoWhen]:checked').value;
    $$('input[name=promoWhen]').forEach(x => x.onchange = () => {
      $('#promoAt').disabled = when() !== 'at'; $('#promoGo').textContent = when() === 'at' ? '🗓 Запланировать акцию' : '🎁 Запустить акцию'
    });
    $('#promoForm').onsubmit = async e => {
      e.preventDefault();
      const body = { hours: +$('#promoHours').value, attempts: +$('#promoAtt').value, broadcast: $('#promoBc').checked };
      if (when() === 'at') { if (!$('#promoAt').value) return toast('Укажите дату и время по Москве'); body.start_msk = $('#promoAt').value }
      try {
        const r = await api('/api/admin/promo', { method: 'POST', body: JSON.stringify(body) });
        toast(r.active ? 'Акция запущена — скопируйте текст для чата' : 'Акция запланирована — включится сама')
      } catch (x) { return toast(x.message) }
      loadPromoAdmin(); loadPromo()
    };
  }
}
setInterval(() => { $$('.js-adm-promo').forEach(x => { const l = +x.dataset.end - Date.now() / 1000; x.textContent = hms(l); if (l <= 0) loadPromoAdmin() }) }, 1000);

/* ---------- счётчик посещений (обезличенный) и статистика для админа */
(() => {
  let seen = false; try { seen = sessionStorage.getItem('nf_hit') === '1' } catch {}
  if (seen || ADMIN_PAGE) return;
  const src = new URLSearchParams(location.search).get('src') || (document.referrer && !document.referrer.includes(location.host) ? new URL(document.referrer).hostname.replace(/^www\./, '') : '');
  api('/api/hit', { method: 'POST', body: JSON.stringify({ src: src || null }) }).catch(() => {});
  try { sessionStorage.setItem('nf_hit', '1') } catch {}
})();
async function loadStats(days) {
  let r; try { r = await api('/api/admin/stats?days=' + (days || 14)) } catch { return }
  const el = $('#statsPanel'); if (!el) return;
  const mx = k => Math.max(1, ...r.days.map(d => d[k]));
  const mV = mx('visitors'), mR = mx('runs');
  const dd = s => new Date(s + 'T12:00:00').toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
  const srcAll = {}; r.days.forEach(d => Object.entries(d.src || {}).forEach(([k, v]) => srcAll[k] = (srcAll[k] || 0) + v));
  const srcTop = Object.entries(srcAll).sort((a, b) => b[1] - a[1]).slice(0, 6);
  el.innerHTML = `<div class="st-tot">
      <div><small>Посетителей</small><b>${fmt(r.total.visitors)}</b></div><div><small>Анализов</small><b>${fmt(r.total.runs)}</b></div>
      <div><small>Покупок</small><b>${r.total.purchases}</b></div><div><small>Выручка</small><b>${fmt(r.total.revenue)} ₽</b></div>
      <div><small>Ключей по акции</small><b>${r.total.promo}</b></div><div><small>Подписчиков бота</small><b>${r.subscribers}</b></div></div>
    <div class="st-chart" role="img" aria-label="Посетители и анализы по дням">${r.days.map(d => `<div class="st-day" title="${dd(d.day)}: посетителей ${d.visitors}, анализов ${d.runs} (запускали ${d.runners}), покупок ${d.purchases}${d.promo ? ', ключей по акции ' + d.promo : ''}">
      <div class="st-bars"><i class="v" style="height:${d.visitors / mV * 100}%"></i><i class="r" style="height:${d.runs / mR * 100}%"></i></div>
      ${d.purchases ? `<span class="st-buy">${d.purchases}</span>` : ''}<small>${dd(d.day).replace('.', '')}</small></div>`).join('')}</div>
    <div class="st-legend"><span><i class="v"></i>посетители</span><span><i class="r"></i>анализы</span><span><b class="st-buy">1</b> покупки</span></div>
    ${srcTop.length ? `<p class="hint">Откуда пришли: ${srcTop.map(([k, v]) => `<b>${esc(k)}</b> ${v}`).join(' · ')}. Чтобы видеть свои источники, добавляйте к ссылке <code>?src=название</code>, например <code>${esc(location.origin)}/?src=chat1</code>.</p>`
      : `<p class="hint">Чтобы видеть, откуда приходят люди, добавляйте к ссылке <code>?src=название</code>, например <code>${esc(location.origin)}/?src=chat1</code>.</p>`}`;
}

/* ---------- ролик: разбор и каркас сценария */
const tfmt = s => { s = Math.round(s || 0); return s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor(s % 3600 / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` };
const xr = v => v == null ? '—' : '×' + String(v >= 10 ? Math.round(v) : (+v).toFixed(1)).replace('.', ',');
function setVMode(m) {
  S.vmode = m;
  $$('[data-vmode]').forEach(b => b.setAttribute('aria-pressed', b.dataset.vmode === m));
  $('#tdForm').classList.toggle('hidden', m !== 'teardown'); $('#scForm').classList.toggle('hidden', m !== 'script');
  $('#vdH1').innerHTML = m === 'teardown' ? 'Почему этот ролик <span class="grad">выстрелил</span>' : 'Каркас сценария <span class="grad">как у хитов</span> ниши';
  $('#vdLead').textContent = m === 'teardown'
    ? 'Вставьте ссылку на чужой хит — сервис сравнит его с обычными роликами того же канала, прочитает субтитры и покажет, что именно сработало: название, превью, длина, первые секунды, обещание, призывы. И что из этого повторить у себя.'
    : 'Сервис прочитает субтитры хитов ниши и посчитает, как они построены: сколько длится вступление, когда звучит обещание, сколько частей и какой длины, где просят подписаться. На выходе — план вашего ролика с таймингом.';
}
$$('[data-vmode]').forEach(b => b.onclick = () => setVMode(b.dataset.vmode));
async function loadScriptSources() {
  if (!S.key) return;
  try {
    const rows = await api('/api/history');
    const ok = rows.filter(r => r.status === 'done' && (r.kind || 'channel') === 'channel').slice(0, 30);
    $('#scSrc').innerHTML = $('#ttSrc').innerHTML = '<option value="">Выберите отчёт по нише из истории…</option>' + ok.map(r => `<option value="${esc(r.id)}">${esc(r.title || r.url)}${r.overall != null ? ' — ' + r.overall : ''}</option>`).join('');
  } catch {}
}
function vdProgress(p, m) { $('#vdProgress').classList.remove('hidden'); $('#vdMsg').textContent = m; $('#vdPct').textContent = Math.round(p) + '%'; $('#vdBar').style.width = p + '%' }
async function vdStart(path, body) {
  $('#vdErr').textContent = ''; $('#tdResult').classList.add('hidden'); $('#scResult').classList.add('hidden');
  vdProgress(1, 'Запускаю…');
  try { const r = await api(path, { method: 'POST', body: JSON.stringify(body) }); S.job = r.job_id; history.replaceState(null, '', '?job=' + S.job); refreshMe(); vdPoll() }
  catch (x) { $('#vdProgress').classList.add('hidden'); if (x.message !== 'auth') $('#vdErr').textContent = x.message }
}
async function vdPoll() {
  try {
    const j = await api('/api/job/' + S.job);
    if (j.status === 'done') { $('#vdProgress').classList.add('hidden'); renderVideo(j.result) }
    else if (j.status === 'error') { $('#vdProgress').classList.add('hidden'); $('#vdErr').textContent = j.error || 'Не получилось.'; refreshMe() }
    else { vdProgress(j.progress, j.message); setTimeout(vdPoll, 1500) }
  } catch (x) { if (x.message !== 'auth') $('#vdErr').textContent = x.message }
}
$('#tdForm').onsubmit = e => { e.preventDefault(); const u = $('#tdUrl').value.trim(); if (u) vdStart('/api/video/teardown', { url: u }) };
$('#scForm').onsubmit = e => {
  e.preventDefault();
  const src = $('#scSrc').value || $('#scUrl').value.trim();
  if (!src) return toast('Выберите отчёт по нише или вставьте ссылку на канал');
  vdStart('/api/video/script', { source: src, minutes: +$('#scMin').value || null })
};
function renderVideo(d) { d.type === 'script' ? (setVMode('script'), renderScript(d)) : (setVMode('teardown'), renderTeardown(d)) }
function tdTimeline(ts, dur) {
  if (!ts) return '';
  const pct = t => Math.max(0, Math.min(100, 100 * t / dur));
  const marks = [];
  if (ts.question_at != null) marks.push([ts.question_at, '❓', 'вопрос']);
  if (ts.promise_at != null) marks.push([ts.promise_at, '🎯', 'обещание']);
  if (ts.first_cta != null) marks.push([ts.first_cta, '🔔', 'призыв подписаться']);
  if (ts.outro_at != null) marks.push([ts.outro_at, '👋', 'концовка']);
  marks.sort((a, b) => a[0] - b[0]); let last = -99;
  marks.forEach(m => { const x = pct(m[0]); m.push(x - last < 6); if (x - last >= 6) last = x });
  const ch = (ts.chapters || []).map(c => `<i class="tl-ch" style="left:${pct(c.t)}%" title="${esc(tfmt(c.t) + ' ' + (c.title || ''))}"></i>`).join('');
  return `<div class="tl"><div class="tl-bar">${ch}${marks.map(([t, ic, n, low]) => `<span class="tl-m${low ? ' low' : ''}" style="left:${pct(t)}%" title="${n} — ${tfmt(t)}">${ic}<small>${tfmt(t)}</small></span>`).join('')}</div>
    <div class="tl-leg">${marks.map(([t, ic, n]) => `<span>${ic} ${n} — <b>${tfmt(t)}</b></span>`).join('')}${ts.has_chapters ? `<span>▏ главы: <b>${ts.parts}</b></span>` : ''}<span>темп: <b>${ts.wpm}</b> слов/мин</span></div></div>`
}
function renderTeardown(d) {
  const v = d.video, n = d.channel_norm || {}, ts = d.transcript, th = d.thumb;
  const el = $('#tdResult'); el.classList.remove('hidden'); $('#scResult').classList.add('hidden');
  el.innerHTML = `<div class="sec"><div class="panel td-head">
      <a class="td-img" href="https://www.youtube.com/watch?v=${esc(v.id)}" target="_blank" rel="noopener"><img src="https://i.ytimg.com/vi/${esc(v.id)}/hqdefault.jpg" alt=""></a>
      <div class="td-info"><small class="hint">Разбор ролика · ${esc(v.channel || '')}</small><h2>${esc(v.title)}</h2>
        <div class="td-facts">
          <div class="td-x ${d.ratio >= 2 ? 'hi' : d.ratio >= 1.2 ? 'mid' : 'lo'}"><b>${xr(d.ratio)}</b><small>к обычным просмотрам канала</small></div>
          <div><b>${fmt(v.views)}</b><small>просмотров (обычно ${fmt(n.median)})</small></div>
          <div><b>${tfmt(v.duration)}</b><small>длина (обычно ${tfmt(n.dur_med)})</small></div>
          <div><b>${v.age_days != null ? v.age_days + ' дн.' : '—'}</b><small>с выхода</small></div>
          ${d.engagement != null ? `<div><b>${String(d.engagement).replace('.', ',')}%</b><small>лайков от просмотров</small></div>` : ''}
          ${n.rank ? `<div><b>№${n.rank}</b><small>из ${n.videos} последних роликов канала</small></div>` : ''}
        </div>
        <div class="dl-row"><button class="btn chat-dl sm" data-export>⬇ Скачать для чата</button></div></div></div></div>
    <div class="sec two">
      <div><h2 class="h2">Почему зашёл</h2><div class="panel"><ul class="td-list">${d.why.map(x => `<li>${esc(x)}</li>`).join('') || '<li>Явных отличий от обычных роликов канала не нашлось.</li>'}</ul></div></div>
      <div><h2 class="h2">Что повторить у себя</h2><div class="panel"><ul class="td-list ok">${d.repeat.map(x => `<li>${esc(x)}</li>`).join('') || '<li>—</li>'}</ul></div></div>
    </div>
    <div class="sec"><h2 class="h2">Как устроен ролик</h2>
      ${ts ? `<div class="panel">${tdTimeline(ts, v.duration || ts.duration)}
        <h4 class="td-h4">Первые 30 секунд</h4><blockquote class="td-quote">${esc(ts.first30 || '—')}</blockquote>
        ${ts.chapters.length ? `<h4 class="td-h4">Главы</h4><ol class="td-ch">${ts.chapters.map(c => `<li><b>${tfmt(c.t)}</b> ${esc(c.title || '')}</li>`).join('')}</ol>` : ''}</div>`
      : '<div class="panel"><p class="hint" style="margin:0">У ролика нет субтитров — разбор начала и структуры недоступен. Остальные выводы — по названию, превью и цифрам.</p></div>'}
    </div>
    <div class="sec two">
      <div><h2 class="h2">Превью</h2><div class="panel">${th ? `<div class="td-pal">${(th.palette || []).map(p => `<span style="background:${esc(p.hex)}" title="${esc(p.name)} ${Math.round(p.share * 100)}%"></span>`).join('')}</div>
        <p class="hint">${th.me.faces ? `Лицо в кадре (${th.me.face_pct}% площади)` : 'Без лица'} · ${th.me.words ? `текст: «${esc(th.me.text)}»` : 'без текста'}${th.compared ? ` · сравнено с ${th.compared} превью канала` : ''}</p>
        ${th.diff.length ? `<ul class="td-list">${th.diff.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '<p class="hint">По оформлению превью похоже на обычные превью канала — выстрелило не за счёт картинки.</p>'}` : '<p class="hint">Превью не удалось разобрать.</p>'}</div></div>
      <div><h2 class="h2">Название и теги</h2><div class="panel"><p><b>${esc(v.title)}</b></p>
        <p class="hint">Приёмы: ${d.hooks.length ? d.hooks.map(esc).join(', ').toLowerCase() : 'без явных приёмов'}</p>
        ${v.tags.length ? `<div class="tags">${v.tags.map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div>` : '<p class="hint">Теги автор не указал.</p>'}</div></div>
    </div>`;
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
}
function renderScript(d) {
  const el = $('#scResult'); el.classList.remove('hidden'); $('#tdResult').classList.add('hidden');
  const total = d.target;
  const planTxt = d.plan.map(p => `${p.from_t}–${p.to_t}  ${p.name}: ${p.what}`).join('\n');
  el.innerHTML = `<div class="sec"><div class="panel sc-head"><small class="hint">Каркас сценария · ниша «${esc(d.source_title || '')}»</small>
      <h2>План ролика на ${tfmt(total)} по ${d.analyzed} хитам ниши</h2>
      <div class="td-facts">
        <div><b>${tfmt(d.median_duration)}</b><small>обычная длина хитов</small></div>
        <div><b>${d.promise_at != null ? tfmt(d.promise_at) : '—'}</b><small>когда звучит обещание</small></div>
        <div><b>${d.first_cta != null ? tfmt(d.first_cta) : '—'}</b><small>призыв подписаться</small></div>
        <div><b>${d.wpm ? Math.round(d.wpm) : '—'}</b><small>слов в минуту</small></div>
        <div><b>${d.part_len ? tfmt(d.part_len) : '—'}</b><small>длина одной части</small></div>
      </div>
      <div class="dl-row"><button class="btn sm" id="scCopy">Скопировать план</button><button class="btn chat-dl sm" data-export>⬇ Скачать для чата</button></div></div></div>
    <div class="sec"><h2 class="h2">Правила ниши</h2><div class="panel"><ul class="td-list ok">${d.rules.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div></div>
    <div class="sec"><h2 class="h2">План с таймингом</h2>
      <div class="sc-bar">${d.plan.map((p, i) => `<span class="sc-seg s${Math.min(i, 3)}${i === d.plan.length - 1 ? ' end' : ''}" style="flex:${Math.max(1, p.to - p.from)}" title="${esc(p.name)} ${p.from_t}–${p.to_t}">${esc(p.name)}</span>`).join('')}</div>
      <div class="sc-plan">${d.plan.map(p => `<div class="sc-row"><div class="sc-t">${p.from_t}<small>–${p.to_t}</small></div><div><b>${esc(p.name)}</b><p>${esc(p.what)}</p></div></div>`).join('')}
        <div class="sc-row cta"><div class="sc-t">${tfmt(d.cta_at)}</div><div><b>🔔 Призыв подписаться</b><p>Там, где зритель уже получил первую пользу, — как у хитов ниши.</p></div></div></div></div>
    <div class="sec"><h2 class="h2">Как начинают хиты ниши</h2><p class="lead">Первые 30 секунд — дословно по субтитрам. Это лучший учебник хука для вашей ниши.</p>
      <div class="sc-ex">${d.examples.map(x => `<div class="panel sc-exc"><a class="iv" href="https://www.youtube.com/watch?v=${esc(x.id)}" target="_blank" rel="noopener"><span class="iv-img"><img loading="lazy" src="${ytImg(x.id)}" alt="">${x.x ? `<b class="iv-x">${xr(x.x)}</b>` : ''}</span><span class="iv-t">${esc(x.title || '')}</span><small>${esc(x.channel || '')} · ${fmt(x.views)} просм. · ${tfmt(x.duration)}${x.promise_at != null ? ' · обещание на ' + tfmt(x.promise_at) : ''}</small></a>
        <blockquote class="td-quote">${esc(x.first30 || '')}</blockquote></div>`).join('')}</div></div>`;
  $('#scCopy').onclick = () => copy(planTxt, 'План скопирован');
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
}

/* ---------- тест превью и названия до публикации */
const TH_LEAD = $('#thLead').innerHTML;
function setThMode(m) {
  S.thmode = m;
  $$('[data-thmode]').forEach(b => b.setAttribute('aria-pressed', b.dataset.thmode === m));
  const t = m === 'test';
  $('#thForm').classList.toggle('hidden', t); $('#journeyT').classList.toggle('hidden', t); $('#ttForm').classList.toggle('hidden', !t);
  if (t) $('#thResult').classList.add('hidden'); else $('#ttResult').classList.add('hidden');
  $('#thH1').innerHTML = t ? 'Проверьте превью <span class="grad">до публикации</span>' : 'Что заставляет <span class="grad">нажать</span> на ваше видео';
  $('#thLead').innerHTML = t ? 'Загрузите 1–3 варианта превью и названия — сервис поставит их в выдачу YouTube среди хитов вашей ниши, проверит лицо, текст, читаемость на телефоне, контраст и приёмы в названии, и скажет, какой вариант сильнее.' : TH_LEAD;
}
$$('[data-thmode]').forEach(b => b.onclick = () => setThMode(b.dataset.thmode));
S.ttImgs = [];
function ttResize(file) {
  return new Promise((ok, bad) => {
    const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => {
      const c = document.createElement('canvas'); c.width = 640; c.height = 360;
      const r = Math.max(640 / img.width, 360 / img.height), w = img.width * r, h = img.height * r;
      c.getContext('2d').drawImage(img, (640 - w) / 2, (360 - h) / 2, w, h); URL.revokeObjectURL(url);
      ok(c.toDataURL('image/jpeg', 0.86))
    };
    img.onerror = () => bad(new Error('Не удалось открыть ' + file.name)); img.src = url
  })
}
async function ttAdd(files) {
  for (const f of [...files].filter(f => f.type.startsWith('image/'))) {
    if (S.ttImgs.length >= 3) { toast('Можно до 3 вариантов'); break }
    try { S.ttImgs.push(await ttResize(f)) } catch (x) { toast(x.message) }
  }
  ttList()
}
function ttList() {
  $('#ttList').innerHTML = S.ttImgs.map((d, i) => `<div class="tt-f"><img src="${d}" alt="Вариант ${i + 1}"><span>Вариант ${i + 1}</span><button type="button" data-ttdel="${i}" aria-label="Убрать">×</button></div>`).join('');
}
$('#ttFiles').onchange = e => { ttAdd(e.target.files); e.target.value = '' };
$('#ttList').onclick = e => { const b = e.target.closest('[data-ttdel]'); if (b) { S.ttImgs.splice(+b.dataset.ttdel, 1); ttList() } };
['dragover', 'dragenter'].forEach(ev => $('#ttDrop').addEventListener(ev, e => { e.preventDefault(); $('#ttDrop').classList.add('over') }));
['dragleave', 'drop'].forEach(ev => $('#ttDrop').addEventListener(ev, e => { e.preventDefault(); $('#ttDrop').classList.remove('over') }));
$('#ttDrop').addEventListener('drop', e => ttAdd(e.dataTransfer.files));
function ttProgress(p, m) { $('#thProgress').classList.remove('hidden'); $('#thMsg').textContent = m; $('#thPct').textContent = Math.round(p) + '%'; $('#thBar').style.width = p + '%' }
$('#ttForm').onsubmit = async e => {
  e.preventDefault();
  if (!S.ttImgs.length) return toast('Добавьте хотя бы одно превью');
  const src = $('#ttSrc').value || $('#ttUrl').value.trim();
  if (!src) return toast('Выберите отчёт по нише или вставьте ссылку на канал');
  $('#thErr').textContent = ''; $('#ttResult').classList.add('hidden'); ttProgress(1, 'Запускаю…');
  try {
    const r = await api('/api/thumbtest', { method: 'POST', body: JSON.stringify({ source: src, images: S.ttImgs, titles: $$('.tt-title').map(i => i.value.trim()).filter(Boolean) }) });
    S.job = r.job_id; history.replaceState(null, '', '?job=' + S.job); refreshMe(); ttPoll()
  } catch (x) { $('#thProgress').classList.add('hidden'); if (x.message !== 'auth') $('#thErr').textContent = x.message }
};
async function ttPoll() {
  try {
    const j = await api('/api/job/' + S.job);
    if (j.status === 'done') { $('#thProgress').classList.add('hidden'); renderThumbTest(j.result) }
    else if (j.status === 'error') { $('#thProgress').classList.add('hidden'); $('#thErr').textContent = j.error || 'Не получилось.'; refreshMe() }
    else { ttProgress(j.progress, j.message); setTimeout(ttPoll, 1500) }
  } catch (x) { if (x.message !== 'auth') $('#thErr').textContent = x.message }
}
const ttTone = s => s >= 70 ? 'hi' : s >= 50 ? 'mid' : 'lo';
function ttChecks(cs) {
  return `<ul class="tt-checks">${cs.map(c => `<li class="${c.ok === true ? 'ok' : c.ok === false ? 'bad' : 'neu'}"><b>${esc(c.label)}</b><span>${esc(c.detail)}</span></li>`).join('')}</ul>`
}
function ttMock(d) {
  const v = d.variants.find(x => x.n === S.ttV) || d.variants[0];
  const t = (d.titles.find(x => x.n === S.ttT) || d.titles[0] || {}).text || 'Ваше название';
  const mine = { mine: true, img: v.img, title: t, channel: 'Ваш канал', meta: 'только что' };
  const others = d.niche.map(x => ({ img: ytImg(x.id), title: x.title, channel: x.channel, meta: fmt(x.views) + ' просмотров' }));
  const feed = [...others.slice(0, 1), mine, ...others.slice(1, 8)];
  const card = x => `<div class="yt-card${x.mine ? ' mine' : ''}"><div class="yt-th"><img src="${x.img}" alt="" loading="lazy">${x.mine ? '<i>ваш ролик</i>' : ''}</div>
    <div class="yt-meta"><span class="yt-av"></span><div><b>${esc(x.title || '')}</b><small>${esc(x.channel || '')}</small><small>${esc(x.meta)}</small></div></div></div>`;
  const row = x => `<div class="yt-row${x.mine ? ' mine' : ''}"><div class="yt-th"><img src="${x.img}" alt="" loading="lazy"></div><div><b>${esc(x.title || '')}</b><small>${esc(x.meta)} · ${esc(x.channel || '')}</small></div></div>`;
  const mob = [...others.slice(0, 2), mine, ...others.slice(2, 4)];
  return `<div class="tt-mocks">
    <div class="tt-mock"><h4>Главная на компьютере</h4><div class="yt-grid">${feed.slice(0, 6).map(card).join('')}</div></div>
    <div class="tt-mock tt-phone"><h4>Телефон — поиск</h4><div class="phone">${mob.map(row).join('')}</div></div>
  </div>`
}
function renderThumbTest(d) {
  setThMode('test');
  S.ttV = S.ttV && d.variants.some(v => v.n === S.ttV) ? S.ttV : d.best_thumb; S.ttT = d.best_title;
  const el = $('#ttResult'); el.classList.remove('hidden'); $('#thResult').classList.add('hidden');
  const ns = d.niche_stats;
  const draw = () => {
    el.innerHTML = `<div class="sec"><div class="panel"><small class="hint">Тест превью · ниша «${esc(d.source_title || '')}» · сравнено с ${ns.videos} хитами</small>
      <h2 class="tt-h">${d.variants.length > 1 ? `Сильнее всего — вариант ${d.best_thumb}` : `Оценка превью: ${d.variants[0].score} из 100`}${d.titles.length > 1 ? ` и название ${d.best_title}` : ''}</h2>
      <p class="hint" style="margin:0">У хитов ниши: лицо — ${Math.round(ns.face_share * 100)}%, надпись — ${Math.round(ns.text_share * 100)}%, обычно ${String(ns.words).replace('.', ',')} сл. на превью.</p>
      <div class="dl-row"><button class="btn chat-dl sm" data-export>⬇ Скачать для чата</button></div></div></div>
    <div class="sec"><h2 class="h2">Как это выглядит в выдаче</h2>
      ${d.variants.length > 1 || d.titles.length > 1 ? `<div class="tt-pick">${d.variants.length > 1 ? `<div class="seg">${d.variants.map(v => `<button type="button" data-ttv="${v.n}" aria-pressed="${v.n === S.ttV}">Превью ${v.n}</button>`).join('')}</div>` : ''}
        ${d.titles.length > 1 ? `<div class="seg">${d.titles.map(v => `<button type="button" data-ttt="${v.n}" aria-pressed="${v.n === S.ttT}">Название ${v.n}</button>`).join('')}</div>` : ''}</div>` : ''}
      ${ttMock(d)}</div>
    <div class="sec"><h2 class="h2">Превью: проверка</h2><div class="tt-vars">${d.variants.map(v => `<div class="panel tt-var${v.n === d.best_thumb && d.variants.length > 1 ? ' best' : ''}">
      <div class="tt-vh"><img src="${v.img}" alt=""><div><span class="tt-score ${ttTone(v.score)}">${v.score}</span><b>Вариант ${v.n}</b>${v.n === d.best_thumb && d.variants.length > 1 ? '<span class="pill hi">лучший</span>' : ''}
        <div class="td-pal">${(v.palette || []).map(p => `<span style="background:${esc(p.hex)}"></span>`).join('')}</div></div></div>${ttChecks(v.checks)}</div>`).join('')}</div></div>
    ${d.titles.length ? `<div class="sec"><h2 class="h2">Название: проверка</h2><div class="tt-vars">${d.titles.map(v => `<div class="panel tt-var${v.n === d.best_title && d.titles.length > 1 ? ' best' : ''}">
      <div class="tt-tn"><span class="tt-score ${ttTone(v.score)}">${v.score}</span><b>${esc(v.text)}</b></div>
      ${v.hooks.length ? `<p class="hint">Приёмы: ${v.hooks.map(esc).join(', ').toLowerCase()}</p>` : ''}${ttChecks(v.checks)}</div>`).join('')}</div></div>` : ''}`;
    $$('[data-ttv]', el).forEach(b => b.onclick = () => { S.ttV = +b.dataset.ttv; draw() });
    $$('[data-ttt]', el).forEach(b => b.onclick = () => { S.ttT = +b.dataset.ttt; draw() });
  };
  draw();
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
}

/* ---------- радар ниши */
$('#radarBtn').onclick = async () => {
  try {
    $('#radarBtn').disabled = true;
    await api('/api/radar', { method: 'POST', body: JSON.stringify({ job_id: S.job }) });
    $('#radarBtn').textContent = 'Радар включён ✓';
    toast('Радар включён. Первая проверка идёт сейчас, просмотры за сутки появятся после второй — через 4 часа. Смотрите вкладку «Радар».')
  } catch (x) { $('#radarBtn').disabled = false; if (x.message !== 'auth') toast(x.message) }
};
const ago = h => h == null ? '—' : h < 1 ? 'меньше часа' : h < 48 ? h + ' ч' : Math.round(h / 24) + ' дн.';
function radarDays(days) {
  if (!days || days.length < 2) return '';
  const mx = Math.max(...days.map(d => d.views), 1);
  return `<span class="rd-days" title="Просмотры по дням">${days.map(d => `<i style="height:${Math.max(6, 100 * Math.max(d.views, 0) / mx)}%" title="${esc(d.day)}: ${fmt(d.views)}"></i>`).join('')}</span>`
}
async function loadRadars() {
  let r; try { r = await api('/api/radars') } catch { return }
  const list = r.radars || [];
  if (!list.length) {
    $('#radarList').innerHTML = `<div class="panel w-empty"><div class="w-pics"><span class="w-eye">📡</span></div>
      <div><h3>Радар пока не включён</h3><ol class="w-steps"><li><b>Откройте отчёт по нише</b> — новый или из «Истории»</li><li><b>Нажмите «📡 Включить радар»</b> под отчётом</li><li><b>Каждые 4 часа</b> здесь будут ролики, которые взлетают прямо сейчас, и реальные просмотры каналов за сутки</li></ol>
      ${r.limit ? `<p class="hint">Можно держать до ${r.limit} радаров.</p>` : ''}</div></div>`;
    return
  }
  $('#radarList').innerHTML = list.map(d => {
    const mx = Math.max(...d.channels.map(c => c.per_day || 0), 1);
    const waiting = d.snapshots < 2;
    return `<article class="panel watch radar">
      <div class="watch-h"><div><h3>📡 ${esc(d.title)}</h3>
        <small class="hint">${d.channels.length} каналов · ${d.running ? 'проверяется сейчас…' : 'проверено ' + dt(d.last_run)} · следующая проверка ${dt(d.next_at)}</small></div></div>
      <h4 class="rd-h">🚀 Ракеты за неделю <span class="hint">ролики до 72 часов, которые набирают в ${'3'}+ раза быстрее обычного для своего канала</span></h4>
      ${d.rockets.length ? `<div class="rd-rockets">${d.rockets.map(v => `<a class="iv" href="https://www.youtube.com/watch?v=${esc(v.vid)}" target="_blank" rel="noopener"><span class="iv-img"><img loading="lazy" src="${ytImg(v.vid)}" alt=""><b class="iv-x">${xr(v.ratio)}</b></span><span class="iv-t">${esc(v.title)}</span><small>${esc(v.channel || '')} · ${fmt(v.views)} просм. · ${ago(v.age_h)}</small></a>`).join('')}</div>`
        : `<p class="hint">Пока ни один свежий ролик не идёт в разы быстрее обычного. Как только появится — пришлём в Telegram.</p>`}
      <h4 class="rd-h">Просмотры каналов за сутки</h4>
      ${waiting ? `<p class="hint">Реальные просмотры за сутки считаются по разнице счётчика канала между проверками — цифры появятся после второй проверки.</p>` : ''}
      <div class="tbl"><table class="rd-tbl"><tr><th>Канал</th><th class="r">За сутки</th><th class="r hide-m">Вчера</th><th class="hide-m">По дням</th><th class="r hide-m">Подписчики</th></tr>
        ${d.channels.map(c => {
          const dl = c.per_day != null && c.prev_day ? Math.round(100 * (c.per_day - c.prev_day) / Math.max(c.prev_day, 1)) : null;
          return `<tr><td class="name"><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title || '')}</a></td>
          <td class="r"><b>${c.per_day != null ? fmt(c.per_day) : '—'}</b>${dl != null ? ` <span class="delta ${dl > 0 ? 'up' : dl < 0 ? 'down' : ''}">${dl > 0 ? '+' : ''}${dl}%</span>` : ''}<span class="rd-bar"><i style="width:${100 * (c.per_day || 0) / mx}%"></i></span></td>
          <td class="r hide-m">${c.prev_day != null ? fmt(c.prev_day) : '—'}</td><td class="hide-m">${radarDays(c.days)}</td><td class="r hide-m">${c.subs ? fmt(c.subs) : '—'}</td></tr>`
        }).join('')}</table></div>
      ${d.fresh.length ? `<h4 class="rd-h">Свежие ролики ниши (до 72 часов)</h4><div class="tbl"><table class="rd-tbl"><tr><th>Ролик</th><th class="r">Возраст</th><th class="r">Просмотры</th><th class="r">Темп</th></tr>
        ${d.fresh.map(v => `<tr><td class="name"><a href="https://www.youtube.com/watch?v=${esc(v.vid)}" target="_blank" rel="noopener">${esc(v.title)}</a><small class="hint" style="display:block">${esc(v.channel || '')}</small></td><td class="r">${ago(v.age_h)}</td><td class="r">${fmt(v.views)}</td><td class="r">${v.ratio != null ? `<span class="pill ${v.ratio >= 3 ? 'hi' : v.ratio >= 1 ? 'mid' : 'grey'}">${xr(v.ratio)}</span>` : '—'}</td></tr>`).join('')}</table></div>
        <p class="hint">Темп — во сколько раз ролик набирает быстрее обычного ролика своего канала того же возраста.</p>` : ''}
      <div class="watch-act">${d.source_job ? `<button class="btn sm" data-job="${esc(d.source_job)}">Открыть отчёт по нише</button>` : ''}
        <button class="btn quiet sm" data-rrun="${esc(d.id)}" ${d.running ? 'disabled' : ''}>${d.running ? 'Проверяется…' : 'Проверить сейчас'}</button>
        <button class="btn line sm" data-rdel="${esc(d.id)}">Выключить радар</button></div></article>`
  }).join('')
}
$('#radarList').addEventListener('click', async e => {
  const run = e.target.closest('[data-rrun]'), del = e.target.closest('[data-rdel]');
  try {
    if (run) { run.disabled = true; await api(`/api/radar/${run.dataset.rrun}/run`, { method: 'POST' }); toast('Проверка запущена — займёт пару минут'); setTimeout(loadRadars, 2500) }
    if (del && confirm('Выключить радар этой ниши? История просмотров удалится.')) { await api(`/api/radar/${del.dataset.rdel}`, { method: 'DELETE' }); toast('Радар выключен'); loadRadars() }
  } catch (x) { if (run) run.disabled = false; if (x.message !== 'auth') toast(x.message) }
});
