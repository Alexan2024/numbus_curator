/* AH Magazine · редакция сайта (7.0).
   Одна страница без сборки: архив, Notes (редактор — editor.js), главная, страницы, указатель.
   Данные — /admin/api/site: сайт таким, каким он станет после очереди правок. Вёрстку и тексты
   страниц по умолчанию даёт сам сайт (site/web/app.js → window.AH). */
(function () {
  'use strict';

  const AH = window.AH;
  const esc = AH.esc;
  const CATS = AH.CATS;
  const CAT = AH.T.ru.cat;
  const ROLE = AH.T.ru.role;
  const CR = { project: 'Проект', photo: 'Фото', source: 'Источник', courtesy: 'Предоставлено', other: 'Также' };
  const app = document.getElementById('app');
  const S = { D: null, hidden: [], status: {}, site: 'https://theahmag.com', busy: [], guard: null, lastHash: '' };
  const MAN_BASE = 200000, IG_BASE = 100000;

  // ======================= мелочи =======================

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  async function api(path, opts) {
    opts = opts || {};
    const o = { method: opts.method || (opts.body !== undefined ? 'POST' : 'GET'), headers: {}, credentials: 'same-origin' };
    if (o.method !== 'GET') o.headers['X-AH'] = '1';
    if (opts.body instanceof FormData) o.body = opts.body;
    else if (opts.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.body); }
    let r;
    try { r = await fetch('/admin/api/' + path, o); } catch (e) { throw new Error('Нет связи с сервером — проверь интернет'); }
    if (r.status === 401 && !opts.noAuth) { login(); throw new Error('Нужно войти'); }
    let j = null;
    try { j = await r.json(); } catch (e) { /* не JSON */ }
    if (!r.ok) throw new Error((j && j.error) || ('Ошибка сервера: ' + r.status));
    return j;
  }

  let toastT;
  function toast(text, kind) {
    const el = document.getElementById('toast');
    el.textContent = text;
    el.className = 'on' + (kind ? ' ' + kind : '');
    clearTimeout(toastT);
    toastT = setTimeout(() => { el.className = ''; }, kind === 'err' ? 7000 : 3200);
  }
  function fail(e) { toast(e && e.message ? e.message : String(e), 'err'); }

  // модальное окно: html, обработчик кликов по [data-m]; → закрыть
  function modal(html, onClick, opts) {
    const m = document.getElementById('modal');
    m.innerHTML = '<div class="m-bg" data-m="close"></div><div class="m-box' + (opts && opts.wide ? ' wide' : '') + '" role="dialog" aria-modal="true">' + html + '</div>';
    m.hidden = false;
    document.body.classList.add('m-open');
    const close = () => { m.hidden = true; m.innerHTML = ''; document.body.classList.remove('m-open'); document.removeEventListener('keydown', key); };
    const key = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', key);
    m.onclick = (e) => {
      const t = e.target.closest('[data-m]');
      if (!t) return;
      if (t.dataset.m === 'close') { close(); return; }
      if (onClick) onClick(t.dataset.m, t, close, e);
    };
    const first = m.querySelector('input,textarea,button.pri');
    if (first) setTimeout(() => first.focus(), 30);
    return close;
  }
  function confirmBox(title, text, yes, danger) {
    return new Promise((res) => {
      modal('<h2 class="m-h">' + esc(title) + '</h2>' + (text ? '<p class="m-p">' + text + '</p>' : '') +
        '<div class="m-act"><button class="btn" data-m="close">Отмена</button><button class="btn pri' + (danger ? ' danger' : '') + '" data-m="yes">' + esc(yes) + '</button></div>',
        (act, el, close) => { if (act === 'yes') { close(); res(true); } });
      const m = document.getElementById('modal');
      const obs = new MutationObserver(() => { if (m.hidden) { obs.disconnect(); res(false); } });
      obs.observe(m, { attributes: true, attributeFilter: ['hidden'] });
    });
  }

  function fdate(d) {
    if (!d) return '';
    const p = String(d).slice(0, 10).split('-');
    return (+p[2]) + ' ' + AH.T.ru.months[+p[1] - 1] + ' ' + p[0];
  }
  function ftime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    const now = new Date();
    const hm = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    if (d.toDateString() === now.toDateString()) return 'сегодня в ' + hm;
    return d.getDate() + ' ' + AH.T.ru.months[d.getMonth()] + ' в ' + hm;
  }
  function personName(id) { const p = S.D.people[id]; return p ? (p.ru || p.en) : id; }
  function keyOf(o) { return String(o.ik || o.id); }
  function thumbUrl(o) { return '/img/t/' + keyOf(o) + '.jpg'; }
  function numLabel(o) {
    if (o.src === 'man' || o.id >= MAN_BASE) return o.tg ? 'AH-' + o.tg : 'редакция';
    if (o.src === 'ig' || o.id >= IG_BASE) return o.tg ? 'AH-' + o.tg : 'Instagram';
    return 'AH-' + o.id;
  }
  function headOf(o) { return (o.t && (o.t.ru || o.t.en)) || '—'; }
  function bylineOf(o) {
    const names = (o.p || []).map((x) => personName(x.id)).filter(Boolean).join(', ');
    return [names, o.pl && o.pl.ru, o.y && o.y.ru].filter(Boolean).join(' · ');
  }
  function siteUrl(path) { return S.site + path; }
  const MARK = '<svg class="mk" viewBox="0 0 642 581" fill="none" stroke="currentColor" stroke-width="30" stroke-linecap="round" aria-hidden="true"><path d="M20.75 563.5V184.87C20.75 109.27 53.76 40.58 126.35 21.48C176.21 8.36 224.9 14.48 256.73 40.33"/><path d="M323.75 563.5V13.75"/><path d="M627.5 563.5V13.75"/><path d="M78 288.5H569.5"/></svg>';
  window.ADM = { api, toast, fail, modal, confirmBox, esc, S, clone, debounce, fdate, ftime, personName, MARK, pickPhoto: null, uploadFiles: null };

  // ======================= вход =======================

  let polling = null;
  function login(note) {
    if (app.className === 'login' && !note) return;      // экран входа уже открыт — не сбивать идущий вход
    clearInterval(polling);
    clearTimeout(stTimer);
    if (S.cleanup) { try { S.cleanup(); } catch (e) { /* */ } S.cleanup = null; }
    S.guard = null;
    S.D = null;
    app.className = 'login';
    app.innerHTML = '<div class="lg"><a class="lg-mk" href="' + esc(S.site) + '" target="_blank" rel="noopener">' + MARK + '</a>' +
      '<h1>Редакция AH Magazine</h1><p class="lg-p">Вход без пароля: бот пришлёт в Telegram кнопку подтверждения. Браузер запомнит тебя на 30 дней.</p>' +
      '<button class="btn pri big" id="lg-go">Войти через Telegram</button><p class="lg-st" id="lg-st">' + esc(note || '') + '</p></div>';
    $('#lg-go').onclick = startLogin;
  }
  async function startLogin() {
    const btn = $('#lg-go'), st = $('#lg-st');
    btn.disabled = true;
    st.textContent = 'Отправляю запрос в бот…';
    let id, r0;
    try { r0 = await api('login/start', { body: {}, noAuth: true }); id = r0.id; } catch (e) { st.textContent = e.message; btn.disabled = false; return; }
    st.innerHTML = 'Код входа: <b class="code">' + esc(r0.code || '') + '</b><br>Открой Telegram и нажми <b>«✅ Войти»</b> — код в сообщении бота должен совпасть.<span class="dots"></span>';
    const t0 = Date.now();
    polling = setInterval(async () => {
      if (Date.now() - t0 > 300000) { clearInterval(polling); st.textContent = 'Время вышло — нажми «Войти» ещё раз.'; btn.disabled = false; return; }
      try {
        const r = await api('login/poll', { body: { id }, noAuth: true });
        if (r.state === 'ok') { clearInterval(polling); boot(); }
        else if (r.state === 'denied') { clearInterval(polling); st.textContent = 'Вход отклонён в боте.'; btn.disabled = false; }
        else if (r.state === 'expired') { clearInterval(polling); st.textContent = 'Запрос устарел — нажми «Войти» ещё раз.'; btn.disabled = false; }
      } catch (e) { /* сеть моргнула — следующий опрос */ }
    }, 1500);
  }
  async function logout() {
    try { await api('logout', { body: {} }); } catch (e) { /* всё равно */ }
    login('Ты вышел из редакции.');
  }

  // ======================= каркас =======================

  const NAV = [['archive', 'Архив'], ['notes', 'Notes'], ['home', 'Главная'], ['pages', 'Страницы'], ['index', 'Указатель']];
  function shell() {
    app.className = 'shell';
    app.innerHTML = '<header class="top"><a class="brand" href="#/archive" aria-label="Редакция">' + MARK + '<span>Редакция</span></a>' +
      '<nav class="nav" aria-label="Разделы">' + NAV.map((n) => '<a href="#/' + n[0] + '" data-nav="' + n[0] + '">' + n[1] + '</a>').join('') + '</nav>' +
      '<div class="top-r"><button class="st" id="st" data-act="status" title="Выкладка на сайт"></button>' +
      '<a class="lnk" href="' + esc(S.site) + '" target="_blank" rel="noopener">Сайт ↗</a>' +
      '<button class="lnk" data-act="logout">Выйти</button></div></header><main id="main"></main>';
    app.addEventListener('click', (e) => {
      const a = e.target.closest('[data-act]');
      if (!a) return;
      if (a.dataset.act === 'logout') logout();
      if (a.dataset.act === 'status') showStatus();
    });
  }
  function setNav(name) { $$('[data-nav]').forEach((a) => a.classList.toggle('on', a.dataset.nav === name)); }

  function renderStatus() {
    const el = $('#st');
    if (!el) return;
    const s = S.status || {};
    let t, c;
    if (s.state === 'publishing') { t = 'Выкладываю на сайт…'; c = 'work'; }
    else if (s.state === 'pending' || s.n) { t = 'Ждут выкладки: ' + (s.n || 1); c = 'work'; }
    else if (s.state === 'error') { t = 'Сайт не обновился — повторю'; c = 'err'; }
    else { t = s.ok_at ? 'На сайте · ' + ftime(s.ok_at) : 'Всё на сайте'; c = 'ok'; }
    el.className = 'st ' + c;
    el.innerHTML = '<i></i>' + esc(t);
  }
  function showStatus() {
    const s = S.status || {};
    modal('<h2 class="m-h">Выкладка на сайт</h2><p class="m-p">Правки встают в очередь и уходят на сайт одной выкладкой: сайт пересобирается, по FTP уходят только изменённые файлы. Обычно это минута-две.</p>' +
      '<dl class="kv"><dt>Сейчас</dt><dd>' + esc({ idle: 'всё выложено', pending: 'ждут выкладки', publishing: 'выкладываю', error: 'ошибка' }[s.state] || s.state || '—') + '</dd>' +
      '<dt>В очереди</dt><dd>' + (s.n || 0) + '</dd><dt>Последняя выкладка</dt><dd>' + esc(ftime(s.ok_at) || '—') + '</dd>' +
      (s.error ? '<dt>Ошибка</dt><dd class="err-t">' + esc(s.error) + (s.try_at ? '<br>повтор ' + esc(ftime(s.try_at)) : '') + '</dd>' : '') + '</dl>' +
      '<div class="m-act"><button class="btn" data-m="close">Закрыть</button></div>');
  }
  let stTimer = null, lastState = 'idle';
  async function pollStatus() {
    clearTimeout(stTimer);
    if (!S.D) return;                      // вышли из редакции — не опрашивать
    try {
      const r = await api('status');
      const was = lastState;
      S.status = r.status; S.busy = r.busy || [];
      lastState = r.status.state;
      renderStatus();
      if ((was === 'publishing' || was === 'pending') && lastState === 'idle') toast('Правки на сайте', 'ok');
    } catch (e) { /* молча */ }
    const busy = S.status.state !== 'idle';
    if (S.D) stTimer = setTimeout(pollStatus, busy ? 3000 : 30000);
  }
  function kick() { clearTimeout(stTimer); stTimer = setTimeout(pollStatus, 1200); }
  window.ADM.kick = kick;

  async function load() {
    const r = await api('site');
    S.D = r.D; S.hidden = r.hidden || []; S.status = r.status || {}; S.site = r.site || S.site; S.busy = r.busy || [];
    window.ADM.D = S.D;
    renderStatus();
    return r;
  }
  window.ADM.reload = load;

  async function boot() {
    try { await load(); } catch (e) { if (e.message !== 'Нужно войти') { app.className = 'boot'; app.innerHTML = '<p class="boot-t">' + esc(e.message) + '</p>'; } return; }
    shell();
    pollStatus();
    route();
  }

  // ======================= адреса =======================

  const ROUTES = [
    [/^#?\/?$/, () => { location.hash = '#/archive'; }],
    [/^#\/archive$/, () => viewArchive()],
    [/^#\/o\/new$/, () => viewObj(null)],
    [/^#\/o\/(\d+)$/, (m) => viewObj(+m[1])],
    [/^#\/notes$/, () => viewNotes()],
    [/^#\/n\/(\d+)$/, (m) => viewNote(+m[1])],
    [/^#\/home$/, () => viewHome()],
    [/^#\/pages$/, () => viewPages()],
    [/^#\/index$/, () => viewIndex()]
  ];
  function route() {
    if (!S.D) return;
    const h = location.hash || '#/archive';
    if (S.guard && h !== S.lastHash && !S.guard()) { history.replaceState(null, '', S.lastHash); return; }
    if (S.cleanup) { try { S.cleanup(); } catch (e) { /* */ } S.cleanup = null; }
    S.guard = null;
    S.lastHash = h;
    window.scrollTo(0, 0);
    for (const [rx, fn] of ROUTES) {
      const m = h.match(rx);
      if (m) { fn(m); return; }
    }
    location.hash = '#/archive';
  }
  window.addEventListener('hashchange', route);
  window.addEventListener('beforeunload', (e) => { if (S.guard && !S.guard(true)) { e.preventDefault(); e.returnValue = ''; } });
  function main() { return $('#main'); }
  // охрана несохранённого: dirty() → true, если есть что терять
  // silent — закрытие вкладки: браузер спросит сам
  function guard(dirty) {
    S.guard = (silent) => !dirty(silent) || (!silent && confirm('Есть несохранённые правки. Уйти без сохранения?'));
  }

  // ======================= архив =======================

  const AF = { q: '', cat: 'all', kind: 'all', n: 60 };
  function viewArchive() {
    setNav('archive');
    const D = S.D;
    main().innerHTML = '<section class="pg"><div class="pg-h"><div><h1>Архив</h1><p class="sub" id="a-count"></p></div>' +
      '<a class="btn pri" href="#/o/new">+ Новая запись</a></div>' +
      '<div class="tools"><input class="inp search" id="a-q" type="search" placeholder="Название, имя, место, год, номер" value="' + esc(AF.q) + '" autocomplete="off">' +
      '<div class="seg" id="a-cat">' + [['all', 'Все']].concat(CATS.map((c) => [c, CAT[c]])).map((c) => '<button data-v="' + c[0] + '"' + (AF.cat === c[0] ? ' class="on"' : '') + '>' + esc(c[1]) + '</button>').join('') + '</div>' +
      '<select class="inp sel" id="a-kind">' + [['all', 'Все записи'], ['man', 'Правленые в редакции'], ['noen', 'Без английского'], ['own', 'Заведённые в редакции'], ['ig', 'Из Instagram'], ['old', 'Старые фото (800 px)'], ['hidden', 'Скрытые']]
        .map((k) => '<option value="' + k[0] + '"' + (AF.kind === k[0] ? ' selected' : '') + '>' + k[1] + '</option>').join('') + '</select></div>' +
      '<div class="rows" id="a-rows"></div><div class="more-w" id="a-more"></div></section>';
    const draw = () => {
      const list = archiveList();
      $('#a-count').textContent = list.length + ' из ' + D.objects.length + (S.hidden.length ? ' · скрыто ' + S.hidden.length : '');
      $('#a-rows').innerHTML = list.slice(0, AF.n).map(rowHtml).join('') || '<p class="empty">Ничего не нашлось.</p>';
      $('#a-more').innerHTML = list.length > AF.n ? '<button class="btn" id="a-mo">Показать ещё · ' + (list.length - AF.n) + '</button>' : '';
      const mo = $('#a-mo');
      if (mo) mo.onclick = () => { AF.n += 100; draw(); };
    };
    $('#a-q').addEventListener('input', debounce((e) => { AF.q = e.target.value; AF.n = 60; draw(); }, 150));
    $('#a-cat').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; AF.cat = b.dataset.v; AF.n = 60; $$('#a-cat button').forEach((x) => x.classList.toggle('on', x === b)); draw(); };
    $('#a-kind').onchange = (e) => { AF.kind = e.target.value; AF.n = 60; draw(); };
    draw();
    if (!AF.q) setTimeout(() => { const q = $('#a-q'); if (q && window.innerWidth > 800) q.focus(); }, 50);
  }
  function norm(s) { return String(s || '').toLowerCase().replace(/ё/g, 'е'); }
  function archiveList() {
    const D = S.D;
    let src = AF.kind === 'hidden' ? S.hidden.map((h) => Object.assign({}, h.rec, { _hidden: h })) : D.objects.filter((o) => !o.tmp);
    if (AF.cat !== 'all') src = src.filter((o) => (o.cats || []).indexOf(AF.cat) >= 0);
    if (AF.kind === 'man') src = src.filter((o) => o.man);
    if (AF.kind === 'noen') src = src.filter((o) => !(o.t && o.t.en) || !(o.b && o.b.en && o.b.en.length) && o.b && o.b.ru && o.b.ru.length);
    if (AF.kind === 'own') src = src.filter((o) => o.src === 'man');
    if (AF.kind === 'ig') src = src.filter((o) => o.src === 'ig');
    if (AF.kind === 'old') src = src.filter((o) => !o.img || (o.img.v || 0) < 2);
    const q = norm(AF.q).trim();
    if (q) {
      const toks = q.split(/\s+/);
      src = src.filter((o) => {
        const s = norm([o.t && o.t.ru, o.t && o.t.en, o.pl && o.pl.ru, o.pl && o.pl.en, o.y && o.y.ru, String(o.id), numLabel(o),
          (o.p || []).map((x) => { const p = D.people[x.id]; return p ? p.ru + ' ' + p.en : ''; }).join(' '),
          (o.cr || []).map((c) => c[1]).join(' ')].join(' '));
        return toks.every((t) => s.indexOf(t) >= 0);
      });
    }
    return src;
  }
  function rowHtml(o) {
    const badges = [];
    if (o._hidden) badges.push('<span class="bd">скрыта</span>');
    if (o.man) badges.push('<span class="bd">правлена</span>');
    if (o.img && (o.img.v || 0) < 2) badges.push('<span class="bd mu">800 px</span>');
    if (!(o.t && o.t.en)) badges.push('<span class="bd mu">нет EN</span>');
    return '<a class="row" href="#/o/' + o.id + '"><img src="' + thumbUrl(o) + '" alt="" loading="lazy" width="56" height="56">' +
      '<span class="row-t"><span class="t">' + esc(headOf(o)) + '</span><span class="m">' + esc(bylineOf(o)) + '</span></span>' +
      '<span class="row-r"><span class="bds">' + badges.join('') + '</span><span class="m">' + esc(numLabel(o)) + ' · ' + esc(fdate(o.d)) + '</span></span></a>';
  }

  // ======================= запись =======================

  function objById(id) {
    const o = S.D.objects.find((x) => x.id === id);
    if (o) return { rec: o, hidden: false };
    const h = S.hidden.find((x) => x.id === id);
    return h ? { rec: h.rec, hidden: true, why: h.reason } : null;
  }
  function photosOf(o) {
    const img = o.img || {}, k = keyOf(o), segs = img.segs || [];
    return segs.map((g, i) => ({ k, i, w: g[1], h: g[2], vid: !!g[3], v: img.v || 0, y0: g[0], W: img.W, H: img.H }));
  }
  // фото в квадратной плитке: целиком, без обрезки; у старых записей — кусок общей ленты img/p
  function photoInner(p) {
    const fit = p.w >= p.h ? 'width:100%' : 'height:100%';
    let bg;
    if (p.u) bg = 'background-image:url(/img/m/' + p.u + '-0.jpg)';
    else if (p.v >= 2) bg = 'background-image:url(/img/m/' + p.k + '-' + p.i + '.jpg)';
    else if (p.i === 0 && p.H === p.h) bg = 'background-image:url(/img/c/' + p.k + '.jpg)';
    else {
      const size = p.W / p.w * 100, py = p.H > p.h ? p.y0 / (p.H - p.h) * 100 : 0;
      bg = 'background-image:url(/img/p/' + p.k + '.jpg);background-size:' + size.toFixed(3) + '% auto;background-position:0 ' + py.toFixed(3) + '%';
    }
    return '<span class="ph-i" style="aspect-ratio:' + p.w + '/' + p.h + ';' + fit + ';' + bg + '"></span>';
  }

  function viewObj(id) {
    setNav('archive');
    const D = S.D;
    let found = id ? objById(id) : null;
    if (id && !found) { main().innerHTML = '<section class="pg"><p class="empty">Записи ' + id + ' нет. <a href="#/archive">← В архив</a></p></section>'; return; }
    if (found && found.rec.tmp) { main().innerHTML = '<section class="pg"><p class="empty">Запись ещё временная: пост о ней выходит в канале. Открой её через пару минут. <a href="#/archive">← В архив</a></p></section>'; return; }
    const rec = found ? clone(found.rec) : { d: new Date().toISOString().slice(0, 10), cats: ['architecture'], t: { ru: '', en: '' }, p: [], co: [], b: { ru: [], en: [] }, cr: [] };
    const E = {
      rec, isNew: !id, hidden: found && found.hidden,
      photos: found ? photosOf(found.rec) : [],
      people: (rec.p || []).map((x) => ({ id: x.id, note: x.note })),
      countries: (rec.co || []).slice(),
      dirty: false
    };
    const title = id ? headOf(rec) : 'Новая запись';
    main().innerHTML = '<section class="pg ed">' +
      '<div class="pg-h sticky"><div class="crumbs"><a href="#/archive">← Архив</a>' + (id ? '<span class="mu">' + esc(numLabel(rec)) + '</span>' : '') + (E.hidden ? '<span class="bd">скрыта</span>' : '') + '</div>' +
      '<div class="acts">' +
      (id && !E.hidden ? '<a class="btn ghost" href="' + esc(siteUrl('/o/' + id + '/')) + '" target="_blank" rel="noopener">На сайте ↗</a>' : '') +
      (id ? '<button class="btn ghost" data-a="hist">История</button>' : '') +
      (id && !E.hidden ? '<button class="btn ghost" data-a="bot">В бот…</button>' : '') +
      (id ? '<button class="btn ghost" data-a="hide">' + (E.hidden ? 'Вернуть на сайт' : 'Скрыть') + '</button>' : '') +
      '<button class="btn pri" data-a="save">' + (id ? 'Сохранить' : 'Опубликовать') + '</button></div></div>' +
      (E.hidden ? '<p class="note-bar">Запись скрыта с сайта' + (found.why ? ': ' + esc(found.why) : '') + '. Правки сохранятся в скрытой копии.</p>' : '') +
      '<h1 class="ed-title" id="o-title">' + esc(title) + '</h1>' +
      '<div class="ed-grid"><div class="ed-ph"><div class="lbl-row"><span class="lbl">Фото</span><span class="mu sm">первое — обложка · перетаскивай, чтобы поменять порядок</span></div>' +
      '<div class="ph-grid" id="o-ph"></div><input type="file" id="o-file" accept="image/*" multiple hidden></div>' +
      '<div class="ed-form" id="o-form">' + objForm(rec) + '</div></div></section>';
    const form = $('#o-form');
    const mark = () => { E.dirty = true; };
    form.addEventListener('input', (e) => {
      mark();
      if (e.target.id === 'f-t-ru') $('#o-title').textContent = e.target.value || 'Новая запись';
    });
    guard(() => E.dirty);
    drawPhotos(E, mark);
    drawPeople(E, mark);
    drawCountries(E, mark);
    drawCredits(rec.cr || [], mark);
    form.addEventListener('click', (e) => {
      const b = e.target.closest('[data-f]');
      if (!b) return;
      const f = b.dataset.f;
      if (f === 'cat') { b.classList.toggle('on'); mark(); }
      if (f === 'tr') translateObj(E, mark);
      if (f === 'cr-add') { addCredit(['source', '', '', ''], mark); mark(); }
      if (f === 'cr-del') { b.closest('.cr-row').remove(); mark(); }
    });
    main().querySelector('.acts').addEventListener('click', async (e) => {
      const b = e.target.closest('[data-a]');
      if (!b) return;
      const a = b.dataset.a;
      if (a === 'save') saveObj(E, b);
      if (a === 'hist') showHistory('obj', id, headOf(rec));
      if (a === 'hide') hideObj(E, id);
      if (a === 'bot') botObj(E, id);
    });
  }

  function field(id, label, val, opts) {
    opts = opts || {};
    const tag = opts.area ? 'textarea' : 'input';
    const v = esc(val == null ? '' : val);
    return '<label class="fl' + (opts.cls ? ' ' + opts.cls : '') + '"><span class="fl-l">' + label + (opts.hint ? '<em>' + opts.hint + '</em>' : '') + '</span>' +
      (opts.area ? '<textarea class="inp" id="' + id + '" rows="' + (opts.rows || 3) + '"' + (opts.ph ? ' placeholder="' + esc(opts.ph) + '"' : '') + '>' + v + '</textarea>'
        : '<' + tag + ' class="inp" id="' + id + '" value="' + v + '"' + (opts.ph ? ' placeholder="' + esc(opts.ph) + '"' : '') + (opts.type ? ' type="' + opts.type + '"' : '') + '>') + '</label>';
  }
  function pair(base, label, v, opts) {
    v = v || {};
    return '<div class="pair">' + field(base + '-ru', label, v.ru, opts) + field(base + '-en', label + ' · EN', v.en, Object.assign({}, opts, { hint: '' })) + '</div>';
  }
  function objForm(o) {
    const y = o.y || {};
    return '<div class="fl"><span class="fl-l">Рубрика <em>одна или две</em></span><div class="chips cats">' + CATS.map((c) => '<button type="button" data-f="cat" data-v="' + c + '" class="chip' + ((o.cats || []).indexOf(c) >= 0 ? ' on' : '') + '">' + esc(CAT[c]) + '</button>').join('') + '</div></div>' +
      pair('f-t', 'Заголовок', o.t, { hint: 'Объект // Автор // Место, год', ph: 'Дом у озера // Альвар Аалто // Муураткало, Финляндия, 1953' }) +
      '<div class="fl"><span class="fl-l">Имена <em>архитекторы, бюро, художники, фотографы</em></span><div class="chips" id="o-people"></div></div>' +
      pair('f-pl', 'Место', o.pl, { ph: 'Хельсинки, Финляндия' }) +
      '<div class="pair three">' + field('f-y-ru', 'Год', y.ru, { ph: '1953 или 1950-е' }) + field('f-y-en', 'Год · EN', y.en) + field('f-y-s', 'Для сортировки', y.s, { type: 'number', ph: '1953' }) + '</div>' +
      '<div class="fl"><span class="fl-l">Страна</span><div class="chips" id="o-co"></div></div>' +
      '<div class="tr-row"><span class="lbl">Текст</span><button type="button" class="btn sm" data-f="tr">Перевести на английский</button></div>' +
      field('f-b-ru', 'Текст', (o.b && o.b.ru || []).join('\n\n'), { area: true, rows: 10, hint: 'абзацы — через пустую строку' }) +
      field('f-b-en', 'Текст · EN', (o.b && o.b.en || []).join('\n\n'), { area: true, rows: 8 }) +
      pair('f-s', 'Подводка', o.s, { area: true, rows: 2, hint: 'фраза под заголовком на главной' }) +
      '<div class="fl"><span class="fl-l">Кредиты</span><div id="o-cr"></div><button type="button" class="btn sm" data-f="cr-add">+ Строка</button></div>' +
      '<div class="pair">' + field('f-d', 'Дата в архиве', o.d, { type: 'date' }) + '<span></span></div>';
  }

  // ---------- фото ----------
  function drawPhotos(E, mark) {
    const box = $('#o-ph');
    const draw = () => {
      box.innerHTML = E.photos.map((p, n) => '<div class="ph' + (n === 0 ? ' cover' : '') + '" draggable="true" data-n="' + n + '">' + photoInner(p) +
        (n === 0 ? '<span class="ph-tag">Обложка</span>' : '') + (p.small ? '<span class="ph-warn" title="Меньше 1600 px по длинной стороне">мелкое</span>' : '') + (p.vid ? '<span class="ph-tag r">видео</span>' : '') +
        '<span class="ph-ctl"><button data-p="left" title="Раньше" aria-label="Раньше">←</button><button data-p="right" title="Позже" aria-label="Позже">→</button>' +
        (n ? '<button data-p="first" title="Сделать обложкой">Обложка</button>' : '') + '<button data-p="del" title="Убрать" aria-label="Убрать">✕</button></span></div>').join('') +
        '<button class="ph add" data-p="add"><span>+ Добавить фото</span><em>или перетащи файлы сюда</em></button>';
    };
    draw();
    box.onclick = (e) => {
      const b = e.target.closest('[data-p]');
      if (!b) return;
      if (b.dataset.p === 'add') { $('#o-file').click(); return; }
      const n = +b.closest('.ph').dataset.n;
      const P = E.photos;
      if (b.dataset.p === 'del') { if (P.length === 1) { toast('Нужно хотя бы одно фото', 'err'); return; } P.splice(n, 1); }
      if (b.dataset.p === 'left' && n > 0) P.splice(n - 1, 0, P.splice(n, 1)[0]);
      if (b.dataset.p === 'right' && n < P.length - 1) P.splice(n + 1, 0, P.splice(n, 1)[0]);
      if (b.dataset.p === 'first') P.unshift(P.splice(n, 1)[0]);
      mark(); draw();
    };
    let drag = null;
    box.addEventListener('dragstart', (e) => { const t = e.target.closest('.ph[data-n]'); if (!t) return; drag = +t.dataset.n; e.dataTransfer.effectAllowed = 'move'; t.classList.add('drag'); });
    box.addEventListener('dragend', () => { drag = null; $$('.ph', box).forEach((x) => x.classList.remove('drag', 'over')); });
    box.addEventListener('dragover', (e) => {
      e.preventDefault();
      const t = e.target.closest('.ph[data-n]');
      $$('.ph', box).forEach((x) => x.classList.toggle('over', x === t && drag !== null));
      box.classList.toggle('drop', drag === null);
    });
    box.addEventListener('dragleave', (e) => { if (!box.contains(e.relatedTarget)) box.classList.remove('drop'); });
    box.addEventListener('drop', (e) => {
      e.preventDefault();
      box.classList.remove('drop');
      if (drag === null) { if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files); return; }
      const t = e.target.closest('.ph[data-n]');
      if (!t) return;
      const to = +t.dataset.n;
      E.photos.splice(to, 0, E.photos.splice(drag, 1)[0]);
      drag = null; mark(); draw();
    });
    const addFiles = async (files) => {
      const got = await uploadFiles(files);
      got.forEach((a) => E.photos.push({ u: a.uid, w: a.w, h: a.h, small: a.small }));
      if (got.length) { mark(); draw(); }
    };
    $('#o-file').onchange = (e) => { addFiles(e.target.files); e.target.value = ''; };
  }

  // загрузка фото → [описания]; по одному, с ходом в тосте
  async function uploadFiles(files) {
    const list = Array.from(files).filter((f) => /^image\//.test(f.type) || /\.(heic|heif|jpe?g|png|webp|tiff?)$/i.test(f.name));
    const out = [];
    for (let n = 0; n < list.length; n++) {
      toast('Загружаю фото ' + (n + 1) + ' из ' + list.length + '…');
      const fd = new FormData();
      fd.append('file', list[n], list[n].name);
      try {
        const r = await api('upload', { body: fd });
        out.push(r.asset);
        if (r.asset.small) toast('«' + list[n].name + '» меньше 1600 px — на сайте может выглядеть мягко', 'err');
      } catch (e) { fail(e); }
    }
    if (out.length && !out.some((a) => a.small)) toast(out.length === 1 ? 'Фото загружено' : 'Загружено фото: ' + out.length, 'ok');
    return out;
  }
  window.ADM.uploadFiles = uploadFiles;

  // ---------- имена и страны ----------
  function nameOfEntry(x) {
    if (x.new) return x.new.ru || x.new.en;
    return personName(x.id);
  }
  function roleOfEntry(x) {
    if (x.new) return ROLE[x.new.role] || '';
    const p = S.D.people[x.id];
    return p ? (p.roles || []).map((r) => ROLE[r]).filter(Boolean).join(', ') : '';
  }
  function drawPeople(E, mark) {
    const box = $('#o-people');
    const draw = () => {
      box.innerHTML = E.people.map((x, n) => '<span class="tag">' + esc(nameOfEntry(x)) + (roleOfEntry(x) ? '<em>' + esc(roleOfEntry(x)) + '</em>' : '') + (x.new ? '<em>новое</em>' : '') +
        '<button type="button" data-n="' + n + '" aria-label="Убрать">✕</button></span>').join('') +
        '<span class="ac"><input class="inp ac-in" placeholder="+ имя" autocomplete="off"><span class="ac-list" hidden></span></span>';
      const inp = $('.ac-in', box), lst = $('.ac-list', box);
      autocomplete(inp, lst, (q) => {
        const nq = norm(q);
        const hits = Object.keys(S.D.people).filter((k) => norm(S.D.people[k].ru + ' ' + S.D.people[k].en).indexOf(nq) >= 0)
          .filter((k) => !E.people.some((x) => x.id === k)).slice(0, 8)
          .map((k) => ({ label: S.D.people[k].ru + (S.D.people[k].en !== S.D.people[k].ru ? ' · ' + S.D.people[k].en : ''), sub: (S.D.people[k].roles || []).map((r) => ROLE[r]).join(', '), v: k }));
        hits.push({ label: 'Новое имя: «' + q + '»', sub: 'добавить в указатель', v: '__new' });
        return hits;
      }, (v, q) => {
        if (v === '__new') newPerson(q, (n) => { E.people.push({ new: n }); mark(); draw(); });
        else { E.people.push({ id: v }); mark(); draw(); }
      });
    };
    box.onclick = (e) => { const b = e.target.closest('button[data-n]'); if (!b) return; E.people.splice(+b.dataset.n, 1); mark(); draw(); };
    draw();
  }
  function newPerson(q, done) {
    const lat = /^[\x00-\x7F]+$/.test(q);
    modal('<h2 class="m-h">Новое имя в указателе</h2>' +
      '<div class="pair">' + field('np-ru', 'Как по-русски', lat ? '' : q) + field('np-en', 'Как по-английски', lat ? q : '') + '</div>' +
      '<label class="fl"><span class="fl-l">Кто это</span><select class="inp" id="np-role">' + Object.keys(ROLE).map((r) => '<option value="' + r + '">' + esc(ROLE[r]) + '</option>').join('') + '</select></label>' +
      '<p class="m-p sm">Иностранные имена и бюро по-русски обычно пишутся так же, как в оригинале: «Studio Mumbai», «Альвар Аалто».</p>' +
      '<div class="m-act"><button class="btn" data-m="close">Отмена</button><button class="btn pri" data-m="ok">Добавить</button></div>', (act, el, close) => {
      if (act !== 'ok') return;
      const ru = $('#np-ru').value.trim(), en = $('#np-en').value.trim();
      if (!ru && !en) { toast('Напиши имя', 'err'); return; }
      const role = $('#np-role').value;
      close(); done({ ru: ru || en, en: en || ru, role });
    });
  }
  function drawCountries(E, mark) {
    const box = $('#o-co');
    const label = (c) => typeof c === 'string' ? (S.D.countries[c] ? S.D.countries[c].ru : c) : c.new.ru;
    const draw = () => {
      box.innerHTML = E.countries.map((c, n) => '<span class="tag">' + esc(label(c)) + (typeof c !== 'string' ? '<em>новая</em>' : '') + '<button type="button" data-n="' + n + '" aria-label="Убрать">✕</button></span>').join('') +
        '<span class="ac"><input class="inp ac-in" placeholder="+ страна" autocomplete="off"><span class="ac-list" hidden></span></span>';
      autocomplete($('.ac-in', box), $('.ac-list', box), (q) => {
        const nq = norm(q);
        const hits = Object.keys(S.D.countries).filter((k) => norm(S.D.countries[k].ru + ' ' + S.D.countries[k].en).indexOf(nq) >= 0)
          .filter((k) => E.countries.indexOf(k) < 0).slice(0, 8).map((k) => ({ label: S.D.countries[k].ru, sub: S.D.countries[k].en, v: k }));
        hits.push({ label: 'Новая страна: «' + q + '»', sub: 'нужны названия RU и EN', v: '__new' });
        return hits;
      }, (v, q) => {
        if (v !== '__new') { E.countries.push(v); mark(); draw(); return; }
        modal('<h2 class="m-h">Новая страна</h2><div class="pair">' + field('nc-ru', 'По-русски', q) + field('nc-en', 'По-английски', '') + '</div>' +
          '<div class="m-act"><button class="btn" data-m="close">Отмена</button><button class="btn pri" data-m="ok">Добавить</button></div>', (act, el, close) => {
          if (act !== 'ok') return;
          const ru = $('#nc-ru').value.trim(), en = $('#nc-en').value.trim();
          if (!ru || !en) { toast('Нужны оба названия', 'err'); return; }
          close(); E.countries.push({ new: { ru, en } }); mark(); draw();
        });
      });
    };
    box.onclick = (e) => { const b = e.target.closest('button[data-n]'); if (!b) return; E.countries.splice(+b.dataset.n, 1); mark(); draw(); };
    draw();
  }
  // подсказки под полем: source(q) → [{label, sub, v}], pick(v, q)
  function autocomplete(inp, lst, source, pick) {
    let items = [], cur = -1;
    const close = () => { lst.hidden = true; cur = -1; };
    const draw = () => {
      lst.innerHTML = items.map((it, n) => '<button type="button" class="ac-i' + (n === cur ? ' on' : '') + '" data-n="' + n + '">' + esc(it.label) + (it.sub ? '<em>' + esc(it.sub) + '</em>' : '') + '</button>').join('');
      lst.hidden = !items.length;
    };
    inp.addEventListener('input', () => { const q = inp.value.trim(); items = q ? source(q) : []; cur = items.length ? 0 : -1; draw(); });
    inp.addEventListener('keydown', (e) => {
      if (lst.hidden) return;
      if (e.key === 'ArrowDown') { cur = Math.min(items.length - 1, cur + 1); draw(); e.preventDefault(); }
      if (e.key === 'ArrowUp') { cur = Math.max(0, cur - 1); draw(); e.preventDefault(); }
      if (e.key === 'Enter' && cur >= 0) { e.preventDefault(); const q = inp.value.trim(); const v = items[cur].v; close(); inp.value = ''; pick(v, q); }
      if (e.key === 'Escape') close();
    });
    inp.addEventListener('blur', () => setTimeout(close, 180));
    lst.addEventListener('mousedown', (e) => {
      const b = e.target.closest('.ac-i');
      if (!b) return;
      e.preventDefault();
      const q = inp.value.trim(), v = items[+b.dataset.n].v;
      close(); inp.value = ''; pick(v, q);
    });
  }
  window.ADM.autocomplete = autocomplete;

  // ---------- кредиты ----------
  function addCredit(c, mark) {
    const box = $('#o-cr');
    const row = document.createElement('div');
    row.className = 'cr-row';
    row.innerHTML = '<select class="inp">' + Object.keys(CR).map((r) => '<option value="' + r + '"' + (c[0] === r ? ' selected' : '') + '>' + CR[r] + '</option>').join('') + '</select>' +
      '<input class="inp" placeholder="Имя или издание" value="' + esc(c[1] || '') + '">' +
      '<input class="inp" placeholder="https://…" value="' + esc(c[2] || '') + '">' +
      '<input class="inp" placeholder="Как по-английски (если иначе)" value="' + esc(c[3] || '') + '">' +
      '<button type="button" class="x" data-f="cr-del" aria-label="Убрать строку">✕</button>';
    box.appendChild(row);
  }
  function drawCredits(cr, mark) { $('#o-cr').innerHTML = ''; cr.forEach((c) => addCredit(c, mark)); }

  // ---------- форма → запись ----------
  function v(id) { const el = document.getElementById(id); return el ? el.value.trim() : ''; }
  function paras(s) { return s.split(/\n\s*\n/).map((x) => x.replace(/\s*\n\s*/g, ' ').trim()).filter(Boolean); }
  function readObj(E) {
    const r = clone(E.rec);
    r.cats = $$('#o-form .cats .chip.on').map((b) => b.dataset.v);
    r.t = { ru: v('f-t-ru'), en: v('f-t-en') };
    r.p = E.people.map((x) => x.new ? { new: x.new } : (x.note ? { id: x.id, note: x.note } : { id: x.id }));
    r.co = E.countries.map((c) => typeof c === 'string' ? c : { new: c.new });
    r.pl = { ru: v('f-pl-ru'), en: v('f-pl-en') };
    r.y = { ru: v('f-y-ru'), en: v('f-y-en'), s: v('f-y-s') };
    r.b = { ru: paras(v('f-b-ru')), en: paras(v('f-b-en')) };
    r.s = { ru: v('f-s-ru'), en: v('f-s-en') };
    r.cr = $$('#o-cr .cr-row').map((row) => { const f = $$('select,input', row).map((x) => x.value.trim()); return [f[0], f[1], f[2] || null, f[3] || '']; }).filter((c) => c[1]);
    r.d = v('f-d');
    return r;
  }
  async function saveObj(E, btn) {
    const rec = readObj(E);
    if (!rec.t.ru) { toast('Нужен заголовок', 'err'); $('#f-t-ru').focus(); return; }
    if (!rec.cats.length) { toast('Выбери рубрику', 'err'); return; }
    if (!E.photos.length) { toast('Добавь хотя бы одно фото', 'err'); return; }
    if (rec.b.en.length && rec.b.ru.length && rec.b.en.length !== rec.b.ru.length) toast('В английском тексте другое число абзацев — на сайте это заметно', 'err');
    btn.disabled = true;
    const was = btn.textContent;
    btn.textContent = 'Сохраняю…';
    try {
      const plan = E.photos.map((p) => p.u ? { u: p.u } : { k: p.k, i: p.i });
      const r = await api('obj/save', { body: { rec, photos: plan, new: E.isNew } });
      E.dirty = false;
      await load();
      toast(E.isNew ? 'Запись заведена — на сайте через минуту-две' : 'Сохранено — на сайте через минуту-две', 'ok');
      kick();
      if (E.isNew) location.hash = '#/o/' + r.id;
      else viewObj(r.id);
    } catch (e) { fail(e); btn.disabled = false; btn.textContent = was; }
  }
  async function translateObj(E, mark) {
    const r = readObj(E);
    const hasEn = r.t.en || r.pl.en || r.y.en || r.s.en || r.b.en.length;
    if (hasEn && !(await confirmBox('Перевести заново?', 'Английские поля заполнятся переводом русских — то, что там сейчас, заменится.', 'Перевести'))) return;
    const items = [r.t.ru, r.pl.ru, r.y.ru, r.s.ru].concat(r.b.ru);
    toast('Перевожу…');
    try {
      const out = (await api('translate', { body: { items, ctx: 'obj' } })).items;
      const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ''; };
      set('f-t-en', out[0]); set('f-pl-en', out[1]); set('f-y-en', out[2]); set('f-s-en', out[3]);
      set('f-b-en', out.slice(4).join('\n\n'));
      mark();
      toast('Готово — проверь и сохрани', 'ok');
    } catch (e) { fail(e); }
  }
  async function hideObj(E, id) {
    if (E.hidden) {
      try { await api('obj/hide', { body: { id, hide: false } }); await load(); kick(); toast('Запись вернётся на сайт через минуту-две', 'ok'); viewObj(id); } catch (e) { fail(e); }
      return;
    }
    if (!(await confirmBox('Скрыть запись с сайта?', 'Страница записи пропадёт с сайта. Запись останется в разделе «Скрытые», её можно вернуть.', 'Скрыть', true))) return;
    try { await api('obj/hide', { body: { id, hide: true } }); E.dirty = false; await load(); kick(); toast('Запись скрыта', 'ok'); location.hash = '#/archive'; } catch (e) { fail(e); }
  }
  function botObj(E, id) {
    if (E.dirty) { toast('Сначала сохрани правки', 'err'); return; }
    modal('<h2 class="m-h">Отправить в бот</h2><p class="m-p">Бот соберёт пост по этой записи: возьмёт фото и факты, напишет текст своим голосом и положит пост во входящие. Дальше — как обычно: одобрить, поправить, поставить в слот. Стоит несколько центов.</p>' +
      '<div class="m-choice"><button class="choice" data-m="std"><b>Пост для канала</b><span>текст в канал, копия в Instagram</span></button>' +
      '<button class="choice" data-m="mini"><b>Фото-пост для Instagram</b><span>только Instagram, короткая подпись</span></button></div>' +
      '<div class="m-act"><button class="btn" data-m="close">Отмена</button></div>', async (act, el, close) => {
      if (act !== 'std' && act !== 'mini') return;
      close();
      try { await api('obj/tobot', { body: { id, fmt: act } }); toast('Бот собирает пост — пришлёт сообщение в Telegram', 'ok'); } catch (e) { fail(e); }
    });
  }

  // ======================= история =======================

  async function showHistory(kind, id, title, onRestored) {
    let items;
    try { items = (await api('history?kind=' + kind + '&id=' + encodeURIComponent(id))).items; } catch (e) { fail(e); return; }
    modal('<h2 class="m-h">История · ' + esc(title || '') + '</h2>' +
      (items.length ? '<p class="m-p sm">Прежние версии, новые сверху. Вернуть — значит сохранить эту версию поверх нынешней (нынешняя тоже останется в истории).</p><div class="hist">' + items.map((h) =>
        '<div class="hist-i"><span><b>' + esc(ftime(h.at)) + '</b><em>' + esc(h.label || '') + (h.title ? ' · ' + esc(h.title) : '') + '</em></span>' +
        '<span><button class="btn sm" data-m="see" data-n="' + h.n + '">Посмотреть</button><button class="btn sm" data-m="back" data-n="' + h.n + '">Вернуть</button></span></div>').join('') + '</div>'
        : '<p class="m-p">Правок ещё не было.</p>') +
      '<div class="m-act"><button class="btn" data-m="close">Закрыть</button></div>', async (act, el, close) => {
      const n = +el.dataset.n;
      if (act === 'see') {
        try {
          const it = (await api('history/get?kind=' + kind + '&id=' + encodeURIComponent(id) + '&n=' + n)).item;
          const r = it.rec || it.doc || it.pages || {};
          const txt = r.b ? (r.b.ru || []).join('\n\n') : r.bl ? r.bl.filter((b) => b.ru).map((b) => b.ru).join('\n\n') : JSON.stringify(r, null, 1);
          modal('<h2 class="m-h">' + esc(ftime(it.at)) + ' · ' + esc(it.label || '') + '</h2><h3 class="h3">' + esc((r.t && r.t.ru) || '') + '</h3><pre class="pre">' + esc(txt.slice(0, 6000)) + '</pre>' +
            '<div class="m-act"><button class="btn" data-m="close">Закрыть</button></div>', null, { wide: true });
        } catch (e) { fail(e); }
      }
      if (act === 'back') {
        close();
        try {
          const r = await api('history/restore', { body: { kind, id, n } });
          if (kind === 'obj') { await load(); kick(); toast('Версия возвращена — на сайте через минуту-две', 'ok'); viewObj(+id); }
          else if (onRestored) onRestored(r);
          else { await load(); kick(); toast('Версия возвращена', 'ok'); route(); }
        } catch (e) { fail(e); }
      }
    });
  }
  window.ADM.history = showHistory;

  // ======================= Notes =======================

  async function viewNotes() {
    setNav('notes');
    main().innerHTML = '<section class="pg"><div class="pg-h"><div><h1>Notes</h1><p class="sub">Длинные тексты. Заметки бота и редакции — в одном списке.</p></div><button class="btn pri" id="n-new">+ Новая заметка</button></div><div class="ncards" id="n-list"><p class="empty">Загружаю…</p></div></section>';
    $('#n-new').onclick = async () => {
      try { const r = await api('note/new', { body: {} }); location.hash = '#/n/' + r.note.id; } catch (e) { fail(e); }
    };
    let items;
    try { items = (await api('notes')).items; } catch (e) { fail(e); return; }
    const st = (it) => it.status === 'scheduled' ? 'выйдет ' + ftime(it.at) : it.status === 'live' ? (it.dirty ? 'на сайте · есть неопубликованные правки' : 'на сайте') : 'черновик';
    $('#n-list').innerHTML = items.map((it) => {
      const c = it.cover;
      const img = c ? '<img src="' + (c.i === 0 ? '/img/c/' + c.k + '.jpg' : '/img/f/' + c.k + '-' + c.i + '.jpg') + '" alt="" loading="lazy">' : '<span class="nc-empty">без обложки</span>';
      return '<a class="nc" href="#/n/' + it.id + '"><span class="nc-im">' + img + '</span><span class="nc-st ' + it.status + (it.dirty ? ' dirty' : '') + '">' + esc(st(it)) + '</span>' +
        '<span class="nc-t">' + esc((it.t && (it.t.ru || it.t.en)) || 'Без заголовка') + '</span><span class="m">' + esc(fdate(it.d)) + (it.updated ? ' · правка ' + esc(ftime(it.updated)) : '') + '</span></a>';
    }).join('') || '<p class="empty">Заметок пока нет.</p>';
  }

  async function viewNote(id) {
    setNav('notes');
    main().innerHTML = '<p class="empty pg">Открываю заметку…</p>';
    let r;
    try { r = await api('note?id=' + id); } catch (e) { main().innerHTML = '<section class="pg"><p class="empty">' + esc(e.message) + ' <a href="#/notes">← Notes</a></p></section>'; return; }
    if (location.hash !== '#/n/' + id) return;           // пока грузили, ушли на другую страницу
    const ed = window.NoteEditor.open(main(), r, {
      onLeave: () => { location.hash = '#/notes'; }
    });
    guard((closing) => ed.unsaved(closing));
    S.cleanup = () => ed.destroy();
  }

  // ======================= главная =======================

  function viewHome() {
    setNav('home');
    const D = S.D;
    const pin = D.home && D.home.pin ? D.objects.find((o) => o.id === D.home.pin) : null;
    const hero = pin || D.objects.find((o) => !o.tmp);
    main().innerHTML = '<section class="pg"><div class="pg-h"><div><h1>Главная</h1><p class="sub">Обложка — большая запись наверху главной. По умолчанию это самая новая запись архива; можно закрепить любую.</p></div></div>' +
      (hero ? '<div class="hero"><a class="hero-im" href="#/o/' + hero.id + '"><img src="/img/c/' + keyOf(hero) + '.jpg" alt=""></a><div class="hero-pn"><span class="k">' + (pin ? 'Выбор редакции' : 'Сегодня в архиве') + ' · ' + esc(CAT[hero.cats[0]] || '') + '</span>' +
        '<h2>' + esc(headOf(hero).split(' // ')[0]) + '</h2><span class="m">' + esc(bylineOf(hero)) + '</span>' + (hero.s ? '<p>' + esc(hero.s.ru) + '</p>' : '') + '</div></div>' : '') +
      '<div class="home-act">' + (pin ? '<p>Закреплена запись <b>' + esc(headOf(pin)) + '</b>.</p><button class="btn" id="h-unpin">Снова самая новая</button>' : '<p>Сейчас наверху — самая новая запись.</p>') + '</div>' +
      '<h2 class="h2">Закрепить другую</h2><input class="inp search" id="h-q" type="search" placeholder="Найти запись" autocomplete="off"><div class="rows" id="h-rows"></div></section>';
    const draw = () => {
      const save = { q: AF.q, cat: AF.cat, kind: AF.kind };
      AF.q = $('#h-q').value; AF.cat = 'all'; AF.kind = 'all';
      const list = archiveList().slice(0, 30);
      Object.assign(AF, save);
      $('#h-rows').innerHTML = list.map((o) => rowHtml(o).replace('<a class="row" href="#/o/' + o.id + '"', '<a class="row pick" data-id="' + o.id + '" href="#/o/' + o.id + '"')).join('');
    };
    $('#h-q').addEventListener('input', debounce(draw, 150));
    $('#h-rows').addEventListener('click', async (e) => {
      const a = e.target.closest('.row.pick');
      if (!a) return;
      e.preventDefault();
      if (!(await confirmBox('Закрепить на главной?', esc(headOf(objById(+a.dataset.id).rec)), 'Закрепить'))) return;
      try { await api('home', { body: { pin: +a.dataset.id } }); await load(); kick(); toast('Обложка главной сменится через минуту-две', 'ok'); viewHome(); } catch (err) { fail(err); }
    });
    const un = $('#h-unpin');
    if (un) un.onclick = async () => { try { await api('home', { body: { pin: null } }); await load(); kick(); toast('Наверху снова самая новая запись', 'ok'); viewHome(); } catch (e) { fail(e); } };
    draw();
  }

  // ======================= страницы =======================

  const PG = { page: 'about', L: 'ru' };
  function pagesData() {
    const D = S.D, out = {};
    ['about', 'partners'].forEach((pg) => {
      out[pg] = {};
      ['ru', 'en'].forEach((L) => {
        const base = clone(AH.COPY[pg][L]), o = D.pages && D.pages[pg] && D.pages[pg][L];
        if (o) Object.keys(o).forEach((k) => { if (o[k] != null && o[k] !== '' && !(Array.isArray(o[k]) && !o[k].length)) base[k] = o[k]; });
        out[pg][L] = base;
      });
    });
    return out;
  }
  function viewPages() {
    setNav('pages');
    const P = pagesData();
    let dirty = false;
    guard(() => dirty);
    const formHtml = () => {
      const c = P[PG.page][PG.L];
      if (PG.page === 'about') {
        return field('pg-title', 'Заголовок', c.title) + field('pg-lead', 'Вводная фраза', c.lead, { area: true, rows: 2 }) +
          field('pg-p', 'Текст', (c.p || []).join('\n\n'), { area: true, rows: 12, hint: 'абзацы — через пустую строку' }) +
          field('pg-contactH', 'Заголовок блока связи', c.contactH) + field('pg-contact', 'Текст перед контактом', c.contact);
      }
      return field('pg-title', 'Заголовок', c.title) + field('pg-lead', 'Вводная фраза', c.lead, { area: true, rows: 3 }) +
        field('pg-fmtH', 'Заголовок списка форматов', c.fmtH) +
        '<div class="fl"><span class="fl-l">Форматы <em>название и описание</em></span><div id="pg-fmt">' + (c.fmt || []).map(fmtRow).join('') + '</div><button type="button" class="btn sm" id="pg-fmt-add">+ Формат</button></div>' +
        field('pg-howH', 'Заголовок «Как мы выбираем»', c.howH) + field('pg-how', 'Как мы выбираем', c.how, { area: true, rows: 3 }) +
        field('pg-audH', 'Заголовок «Аудитория»', c.audH) + field('pg-aud', 'Аудитория', c.aud || '', { area: true, rows: 3, hint: 'пусто — раздела на странице нет' }) +
        field('pg-contactH', 'Заголовок блока связи', c.contactH) + field('pg-contact', 'Текст перед контактом', c.contact);
    };
    const fmtRow = (f) => '<div class="fmt-row"><input class="inp" value="' + esc(f[0]) + '" placeholder="Название"><textarea class="inp" rows="2" placeholder="Описание">' + esc(f[1]) + '</textarea><button type="button" class="x" data-x="fmt" aria-label="Убрать">✕</button></div>';
    const read = () => {
      const c = P[PG.page][PG.L];
      const val = (id) => { const el = document.getElementById(id); return el ? el.value.trim() : undefined; };
      ['title', 'lead', 'contactH', 'contact', 'fmtH', 'howH', 'how', 'audH', 'aud'].forEach((k) => { const x = val('pg-' + k); if (x !== undefined) c[k] = k === 'aud' ? (x || null) : x; });
      const p = val('pg-p');
      if (p !== undefined) c.p = paras(p);
      if ($('#pg-fmt')) c.fmt = $$('#pg-fmt .fmt-row').map((r) => [$('input', r).value.trim(), $('textarea', r).value.trim()]).filter((f) => f[0]);
    };
    const draw = () => {
      main().innerHTML = '<section class="pg ed narrow"><div class="pg-h sticky"><div><h1>Страницы</h1></div><div class="acts">' +
        '<a class="btn ghost" href="' + esc(siteUrl((PG.L === 'en' ? '/en' : '') + '/' + PG.page + '/')) + '" target="_blank" rel="noopener">На сайте ↗</a>' +
        '<button class="btn ghost" id="pg-hist">История</button><button class="btn ghost" id="pg-tr">Перевести на английский</button><button class="btn pri" id="pg-save">Сохранить</button></div></div>' +
        '<div class="tabs-row"><div class="seg" id="pg-tab"><button data-v="about"' + (PG.page === 'about' ? ' class="on"' : '') + '>О журнале</button><button data-v="partners"' + (PG.page === 'partners' ? ' class="on"' : '') + '>Партнёрство</button></div>' +
        '<div class="seg" id="pg-lang"><button data-v="ru"' + (PG.L === 'ru' ? ' class="on"' : '') + '>RU</button><button data-v="en"' + (PG.L === 'en' ? ' class="on"' : '') + '>EN</button></div></div>' +
        '<p class="mu sm hintline">В тексте можно **жирный**, *курсив* и [ссылку](https://…). Цифры архива на странице «О журнале» считаются сами.</p>' +
        '<div class="pg-form" id="pg-form">' + formHtml() + '</div></section>';
      $('#pg-form').addEventListener('input', () => { dirty = true; });
      $('#pg-form').addEventListener('click', (e) => { const x = e.target.closest('[data-x="fmt"]'); if (x) { x.closest('.fmt-row').remove(); dirty = true; } });
      const fa = $('#pg-fmt-add');
      if (fa) fa.onclick = () => { $('#pg-fmt').insertAdjacentHTML('beforeend', fmtRow(['', ''])); dirty = true; };
      $('#pg-tab').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; read(); PG.page = b.dataset.v; draw(); };
      $('#pg-lang').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; read(); PG.L = b.dataset.v; draw(); };
      $('#pg-save').onclick = async (e) => {
        read();
        e.target.disabled = true;
        try { await api('pages', { body: { pages: P } }); dirty = false; await load(); kick(); toast('Страницы обновятся на сайте через минуту-две', 'ok'); } catch (err) { fail(err); }
        e.target.disabled = false;
      };
      $('#pg-hist').onclick = () => showHistory('pages', 'all', 'страницы');
      $('#pg-tr').onclick = async () => {
        read();
        const ru = P[PG.page].ru;
        const keys = Object.keys(ru).filter((k) => typeof ru[k] === 'string' && k !== 'stats');
        const items = keys.map((k) => ru[k]).concat(ru.p || []).concat((ru.fmt || []).reduce((a, f) => a.concat(f), []));
        if (!(await confirmBox('Перевести страницу?', 'Английская версия «' + (PG.page === 'about' ? 'О журнале' : 'Партнёрство') + '» заполнится переводом русской.', 'Перевести'))) return;
        toast('Перевожу…');
        try {
          const out = (await api('translate', { body: { items, ctx: 'pages' } })).items;
          const en = P[PG.page].en;
          keys.forEach((k, n) => { en[k] = out[n]; });
          let n = keys.length;
          if (ru.p) { en.p = out.slice(n, n + ru.p.length); n += ru.p.length; }
          if (ru.fmt) en.fmt = ru.fmt.map((f, j) => [out[n + j * 2], out[n + j * 2 + 1]]);
          PG.L = 'en'; dirty = true; draw();
          toast('Готово — проверь английскую версию и сохрани', 'ok');
        } catch (err) { fail(err); }
      };
    };
    draw();
  }

  // ======================= указатель =======================

  const IX = { tab: 'people', q: '' };
  function viewIndex() {
    setNav('index');
    const D = S.D;
    main().innerHTML = '<section class="pg narrow"><div class="pg-h"><div><h1>Указатель</h1><p class="sub">Имена и страны, как они написаны на сайте. Правка меняет написание во всех записях сразу.</p></div></div>' +
      '<div class="tools"><div class="seg" id="ix-tab"><button data-v="people"' + (IX.tab === 'people' ? ' class="on"' : '') + '>Имена · ' + Object.keys(D.people).length + '</button><button data-v="countries"' + (IX.tab === 'countries' ? ' class="on"' : '') + '>Страны · ' + Object.keys(D.countries).length + '</button></div>' +
      '<input class="inp search" id="ix-q" type="search" placeholder="Найти" value="' + esc(IX.q) + '" autocomplete="off"></div><div class="ix" id="ix-list"></div></section>';
    const draw = () => {
      const q = norm(IX.q);
      const src = IX.tab === 'people' ? D.people : D.countries;
      const keys = Object.keys(src).filter((k) => !q || norm(src[k].ru + ' ' + src[k].en + ' ' + k).indexOf(q) >= 0)
        .sort((a, b) => (src[a].ru || '').localeCompare(src[b].ru || '', 'ru')).slice(0, 200);
      $('#ix-list').innerHTML = keys.map((k) => {
        const x = src[k];
        const n = IX.tab === 'people' ? (x.objs || []).length : x.n;
        return '<div class="ix-row" data-k="' + esc(k) + '"><input class="inp" data-f="ru" value="' + esc(x.ru) + '" aria-label="По-русски"><input class="inp" data-f="en" value="' + esc(x.en) + '" aria-label="По-английски">' +
          (IX.tab === 'people' ? '<select class="inp" data-f="role">' + Object.keys(ROLE).map((r) => '<option value="' + r + '"' + ((x.roles || [])[0] === r ? ' selected' : '') + '>' + ROLE[r] + '</option>').join('') + '</select>' +
            '<input class="inp" data-f="life" value="' + esc(x.life || '') + '" placeholder="годы жизни">' : '') +
          '<span class="mu sm">' + n + '</span><button class="btn sm" data-save hidden>Сохранить</button></div>';
      }).join('') || '<p class="empty">Ничего не нашлось.</p>';
    };
    $('#ix-tab').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; IX.tab = b.dataset.v; viewIndex(); };
    $('#ix-q').addEventListener('input', debounce((e) => { IX.q = e.target.value; draw(); }, 150));
    $('#ix-list').addEventListener('input', (e) => { const row = e.target.closest('.ix-row'); if (row) $('[data-save]', row).hidden = false; });
    $('#ix-list').addEventListener('change', (e) => { const row = e.target.closest('.ix-row'); if (row) $('[data-save]', row).hidden = false; });
    $('#ix-list').addEventListener('click', async (e) => {
      const b = e.target.closest('[data-save]');
      if (!b) return;
      const row = b.closest('.ix-row'), k = row.dataset.k;
      const f = (n) => { const el = $('[data-f="' + n + '"]', row); return el ? el.value.trim() : ''; };
      b.disabled = true;
      try {
        if (IX.tab === 'people') {
          const roles = (D.people[k].roles || []).slice();
          roles[0] = f('role');
          await api('person', { body: { id: k, ru: f('ru'), en: f('en'), roles: Array.from(new Set(roles)), life: f('life') } });
        } else await api('country', { body: { id: k, ru: f('ru'), en: f('en') } });
        await load(); kick(); b.hidden = true; toast('Сохранено — на сайте через минуту-две', 'ok');
      } catch (err) { fail(err); }
      b.disabled = false;
    });
    draw();
  }

  // ======================= старт =======================

  boot();
})();
