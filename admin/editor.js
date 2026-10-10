/* AH Magazine · редактор заметок (Notes).
   Страница заметки как на сайте: обложка и заголовок, ниже колонка текста. Блоки: абзац, подзаголовок, цитата,
   примечание, фото (на всю ширину, в колонку, пара). В тексте — жирный, курсив, ссылки; хранится разметкой
   **жирный**, *курсив*, [текст](https://…) — той же, что рисует сайт (AH.inl). RU и EN правятся по очереди
   (переключатель наверху). Черновик сохраняется сам. */
(function () {
  'use strict';

  const TYPES = { p: 'Абзац', h: 'Подзаголовок', quote: 'Цитата', example: 'Примечание' };
  const PH = {
    ru: { p: 'Текст абзаца', h: 'Подзаголовок', quote: 'Цитата', example: 'Примечание мелким шрифтом', title: 'Заголовок', sub: 'Подзаголовок — по желанию', cap: 'Подпись к фото — по желанию', by: 'Кто сказал, откуда — по желанию', kicker: 'Надпись над подзаголовком — по желанию' },
    en: { p: 'Paragraph', h: 'Heading', quote: 'Quote', example: 'Small note', title: 'Title', sub: 'Subtitle', cap: 'Caption', by: 'Attribution', kicker: 'Kicker' }
  };

  // ---------- разметка ⇄ DOM ----------
  function mdEsc(s) { return String(s).replace(/([\\*[\]])/g, '\\$1'); }
  function wrap(s, m) {
    const lead = s.match(/^\s*/)[0], trail = s.match(/\s*$/)[0];
    return lead + m + s.trim() + m + trail;
  }
  function dom2md(node) {
    let out = '';
    node.childNodes.forEach((n) => {
      if (n.nodeType === 3) { out += mdEsc(n.nodeValue.replace(/ /g, ' ')); return; }
      if (n.nodeType !== 1) return;
      const tag = n.tagName;
      if (tag === 'BR') { out += ' '; return; }
      let inner = dom2md(n);
      const st = n.style || {};
      const bold = tag === 'B' || tag === 'STRONG' || /^(bold|bolder|[6-9]00)$/.test(st.fontWeight || '');
      const ital = tag === 'I' || tag === 'EM' || st.fontStyle === 'italic';
      if (tag === 'A') {
        const href = (n.getAttribute('href') || '').trim();
        if (/^(https?:\/\/|mailto:|\/(?![\/\\]))/.test(href) && inner.trim()) {
          const url = href.replace(/[\s)*]/g, (ch) => '%' + ch.charCodeAt(0).toString(16).toUpperCase().padStart(2, '0'));
          inner = wrap(inner, '\u0000').replace(/\u0000(.*)\u0000/s, (m, t) => '[' + t + '](' + url + ')');
        }
      }
      if (ital && inner.trim()) inner = wrap(inner, '*');
      if (bold && inner.trim()) inner = wrap(inner, '**');
      if ((tag === 'DIV' || tag === 'P') && out && !/\s$/.test(out)) out += ' ';
      out += inner;
    });
    return out;
  }
  function readMd(el) { return dom2md(el).replace(/\s+/g, ' ').trim(); }
  function md2html(s) { return window.AH.inl(s || ''); }
  function plain(s) { const d = document.createElement('div'); d.innerHTML = md2html(s); return d.textContent; }

  // ---------- каретка ----------
  function caretRange() { const s = window.getSelection(); return s && s.rangeCount ? s.getRangeAt(0) : null; }
  function textBefore(el) {
    const r = caretRange();
    if (!r || !el.contains(r.startContainer)) return null;
    const pre = document.createRange();
    pre.selectNodeContents(el);
    pre.setEnd(r.startContainer, r.startOffset);
    return pre.toString();
  }
  function atStart(el) { const r = caretRange(); return r && r.collapsed && textBefore(el) === ''; }
  function atEnd(el) {
    const r = caretRange();
    if (!r || !r.collapsed || !el.contains(r.endContainer)) return false;
    const post = document.createRange();
    post.selectNodeContents(el);
    post.setStart(r.endContainer, r.endOffset);
    return post.toString() === '';
  }
  function placeCaret(el, where) {
    el.focus();
    const s = window.getSelection(), r = document.createRange();
    if (typeof where === 'number') {
      let left = where, done = false;
      const walk = (n) => {
        if (done) return;
        if (n.nodeType === 3) {
          if (n.nodeValue.length >= left) { r.setStart(n, left); done = true; return; }
          left -= n.nodeValue.length;
        } else n.childNodes.forEach(walk);
      };
      walk(el);
      if (!done) { r.selectNodeContents(el); r.collapse(false); }
      r.collapse(true);
    } else { r.selectNodeContents(el); r.collapse(where === 'start'); }
    s.removeAllRanges();
    s.addRange(r);
  }

  // ---------- всплывающее меню ----------
  let popClose = null;
  function popover(anchor, html, onClick) {
    if (popClose) popClose();
    const el = document.createElement('div');
    el.className = 'pop';
    el.innerHTML = html;
    document.body.appendChild(el);
    const r = anchor.getBoundingClientRect();
    const w = el.offsetWidth, h = el.offsetHeight;
    let left = Math.min(window.innerWidth - w - 8, Math.max(8, r.left));
    let top = r.bottom + 6;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
    el.style.left = left + 'px';
    el.style.top = top + 'px';
    const close = () => { el.remove(); document.removeEventListener('mousedown', out, true); document.removeEventListener('keydown', key); popClose = null; };
    const out = (e) => { if (!el.contains(e.target) && !anchor.contains(e.target)) close(); };
    const key = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('mousedown', out, true);
    document.addEventListener('keydown', key);
    el.addEventListener('mousedown', (e) => { if (e.target.closest('button')) e.preventDefault(); });
    el.addEventListener('click', (e) => { const b = e.target.closest('[data-v]'); if (b) { close(); onClick(b.dataset.v, b); } });
    popClose = close;
    return close;
  }

  // ======================= редактор =======================

  function open(root, r, opts) {
    const A = window.ADM, esc = A.esc, api = A.api, toast = A.toast, fail = A.fail;
    const x = r.note;
    const nid = x.id;
    let doc = A.clone(x.doc);
    if (!doc.bl || !doc.bl.length) doc.bl = [{ type: 'p', ru: '', en: '' }];
    let L = 'ru';
    const st = { status: r.status, dirty: r.dirty, live: r.live, at: x.at || r.at || null };
    let savedJson = JSON.stringify(doc), saving = null, saveErr = null, savedAt = x.updated || null, timer = null, dead = false;
    let cur = 0;                                   // блок с курсором

    root.innerHTML = '<section class="ne">' +
      '<div class="ne-bar"><a class="lnk" href="#/notes">← Notes</a><span class="ne-st" id="ne-st"></span>' +
      '<span class="ne-sp"></span>' +
      '<div class="seg" id="ne-lang"><button data-v="ru" class="on">RU</button><button data-v="en">EN</button></div>' +
      '<button class="btn ghost" data-a="settings">Настройки</button>' +
      '<button class="btn ghost" data-a="tr">Перевести</button>' +
      '<a class="btn ghost" href="/admin/preview/' + encodeURIComponent(x.preview || '') + '" target="_blank" rel="noopener" data-a="preview">Предпросмотр ↗</a>' +
      '<button class="btn pri" data-a="pub">Опубликовать…</button></div>' +
      '<div class="ne-fmt" id="ne-fmt" role="toolbar" aria-label="Оформление">' +
      '<select class="inp sel sm" id="ne-type" aria-label="Вид блока">' + Object.keys(TYPES).map((k) => '<option value="' + k + '">' + TYPES[k] + '</option>').join('') + '</select>' +
      '<button data-c="bold" title="Жирный (Ctrl+B)"><b>Ж</b></button><button data-c="italic" title="Курсив (Ctrl+I)"><i>К</i></button>' +
      '<button data-c="link" title="Ссылка (Ctrl+K)">Ссылка</button><span class="ne-sep"></span>' +
      '<button data-c="photo">+ Фото</button><button data-c="quote">+ Цитата</button><button data-c="h">+ Подзаголовок</button>' +
      '<span class="ne-hint">Enter — новый абзац · фото можно перетащить прямо в текст</span></div>' +
      '<p class="ne-en" id="ne-en" hidden>Английской версии ещё нет: на английской странице будет русский текст. «Перевести» заполнит её — потом поправь.</p>' +
      '<article class="site ne-page"><div class="frame"><section class="split ne-hero">' +
      '<button class="split-im ne-cover" data-a="cover" id="ne-cover" type="button"></button>' +
      '<div class="split-pn"><span class="k" id="ne-kick"></span>' +
      '<h1 contenteditable="true" class="ed1" data-k="title" spellcheck="true"></h1>' +
      '<p contenteditable="true" class="ed1 sub" data-k="sub"></p>' +
      '<span class="by"><input type="date" class="ne-date" id="ne-date" aria-label="Дата заметки"></span></div></section></div>' +
      '<div class="art ne-art" id="ne-art"></div></article></section>';

    const art = root.querySelector('#ne-art');
    const q = (s) => root.querySelector(s);

    // ---------- состояние и сохранение ----------
    function statusText() {
      const base = st.status === 'scheduled' ? 'Выйдет ' + A.ftime(st.at) : st.status === 'live' ? (st.dirty ? 'На сайте · правки не опубликованы' : 'На сайте') : 'Черновик';
      let s2;
      if (saveErr) s2 = '<span class="err-t">не сохранилось — повторю</span>';
      else if (saving || timer) s2 = 'сохраняю…';
      else s2 = savedAt ? 'сохранено ' + A.ftime(savedAt) : '';
      q('#ne-st').innerHTML = '<b>' + esc(base) + '</b>' + (s2 ? ' · ' + s2 : '');
      q('[data-a="pub"]').textContent = st.status === 'live' ? (st.dirty ? 'Обновить на сайте…' : 'На сайте ✓') : st.status === 'scheduled' ? 'По расписанию…' : 'Опубликовать…';
    }
    function changed() {
      clearTimeout(timer);
      timer = setTimeout(() => { timer = null; save(); }, 1200);
      if (st.status === 'live') st.dirty = true;
      statusText();
    }
    async function save() {
      if (dead && !saving) { /* при уходе со страницы — сохранить ещё раз */ }
      const json = JSON.stringify(doc);
      if (json === savedJson) { statusText(); return; }
      if (saving) { await saving; return save(); }
      saving = (async () => {
        try {
          const r2 = await api('note/save', { body: { id: nid, doc } });
          savedJson = json; saveErr = null; savedAt = r2.updated;
          st.status = r2.status; st.dirty = r2.dirty;
        } catch (e) {
          saveErr = e;
          if (e.message === 'Нужно войти') dead = true;          // вход истёк — повторять нечего
          if (!dead) { clearTimeout(timer); timer = setTimeout(() => { timer = null; save(); }, 5000); }
        }
      })();
      await saving;
      saving = null;
      if (!dead) statusText();
    }
    async function flush() { clearTimeout(timer); timer = null; await save(); if (saveErr) throw saveErr; }

    // ---------- шапка ----------
    function drawHead() {
      const c = doc.cover;
      const cov = q('#ne-cover');
      cov.innerHTML = c ? '<img src="' + (c.i === 0 ? '/img/c/' + esc(c.k) + '.jpg' : '/img/f/' + esc(c.k) + '-' + c.i + '.jpg') + '" alt=""><span class="ne-cov-t">Сменить обложку</span>'
        : '<span class="ne-cov-empty">+ Обложка<em>первое, что видно в анонсе и в списке Notes</em></span>';
      q('#ne-kick').textContent = 'Notes · ' + doc.cats.map((cc) => window.AH.T[L].cat[cc]).join(' · ');
      const t = q('[data-k="title"]'), s = q('[data-k="sub"]');
      t.textContent = (doc.t && doc.t[L]) || '';
      s.textContent = (doc.sub && doc.sub[L]) || '';
      t.dataset.ph = L === 'en' && doc.t.ru ? doc.t.ru : PH[L].title;
      s.dataset.ph = L === 'en' && doc.sub && doc.sub.ru ? doc.sub.ru : PH[L].sub;
      q('#ne-date').value = doc.d;
      const enEmpty = L === 'en' && !doc.bl.some((b) => b.en);
      q('#ne-en').hidden = !enEmpty;
    }

    // ---------- блоки ----------
    function refUrl(rf, big) { return rf.m === false || big ? '/img/f/' + rf.k + '-' + rf.i + '.jpg' : '/img/m/' + rf.k + '-' + rf.i + '.jpg'; }
    function blockHtml(b, i) {
      const ph = (k) => L === 'en' && b.ru && k === b.type ? plain(b.ru).slice(0, 140) : PH[L][k];
      let inner;
      if (b.type === 'img') {
        const pair = b.ph.length > 1;
        inner = '<figure class="' + (pair ? 'pair' : b.sz === 'col' ? 'fc' : '') + '">' +
          (pair ? '<div class="pr">' : '') + b.ph.map((rf, n) => '<span class="nph" ' + (pair ? 'style="flex:' + (rf.w / rf.h).toFixed(4) + ' 1 0"' : '') + '><img src="' + esc(refUrl(rf)) + '" width="' + rf.w + '" height="' + rf.h + '" alt="">' +
            '<span class="nph-ctl"><button data-im="replace" data-n="' + n + '">Заменить</button>' + (pair ? '<button data-im="drop" data-n="' + n + '">Убрать</button>' : '') + '</span></span>').join('') + (pair ? '</div>' : '') +
          '<figcaption contenteditable="true" class="ed1" data-k="cap" data-ph="' + esc(L === 'en' && b.cap && b.cap.ru ? plain(b.cap.ru) : PH[L].cap) + '">' + md2html(b.cap && b.cap[L]) + '</figcaption>' +
          '<span class="fig-ctl">' + (pair ? '<button data-im="swap">Поменять местами</button>' : '<button data-im="sz">' + (b.sz === 'col' ? 'Шире — на всю страницу' : 'Уже — в колонку текста') + '</button><button data-im="add">+ Второе фото рядом</button>') + '</span></figure>';
      } else if (b.type === 'h') {
        inner = '<div class="h"><span class="kick ed1" contenteditable="true" data-k="kicker" data-ph="' + PH[L].kicker + '">' + esc(b.kicker || '') + '</span>' +
          '<h2 class="t" contenteditable="true" data-ph="' + esc(ph('h')) + '">' + md2html(b[L]) + '</h2></div>';
      } else if (b.type === 'quote') {
        inner = '<blockquote><span class="t" contenteditable="true" data-ph="' + esc(ph('quote')) + '">' + md2html(b[L]) + '</span>' +
          '<cite class="ed1" contenteditable="true" data-k="by" data-ph="' + esc(L === 'en' && b.by && b.by.ru ? b.by.ru : PH[L].by) + '">' + esc((b.by && b.by[L]) || '') + '</cite></blockquote>';
      } else if (b.type === 'example') {
        inner = '<p class="ex t" contenteditable="true" data-ph="' + esc(ph('example')) + '">' + md2html(b[L]) + '</p>';
      } else {
        inner = '<p class="t" contenteditable="true" data-ph="' + esc(ph('p')) + '">' + md2html(b[L]) + '</p>';
      }
      return '<div class="blk b-' + b.type + (i === 0 ? ' first' : '') + '" data-i="' + i + '"><div class="gut"><button class="gh" data-g="menu" title="Блок: вид, порядок, удалить" aria-label="Меню блока">⋮⋮</button><button class="gh" data-g="add" title="Добавить блок ниже" aria-label="Добавить блок ниже">+</button></div>' + inner + '</div>';
    }
    function draw(focus) {
      art.innerHTML = doc.bl.map(blockHtml).join('') + '<button class="ne-add" data-g="end" type="button">+ Добавить блок</button>';
      if (focus) {
        const el = editableOf(focus.i);
        if (el) placeCaret(el, focus.at === undefined ? 'end' : focus.at);
      }
    }
    function blkOf(el) { const w = el.closest('.blk'); return w ? +w.dataset.i : -1; }
    function editableOf(i) { const w = art.querySelector('.blk[data-i="' + i + '"]'); return w ? w.querySelector('.t') : null; }
    function isText(b) { return b && b.type !== 'img'; }
    function newBlock(type) { return type === 'img' ? null : { type, ru: '', en: '' }; }
    function syncEl(el) {
      const i = blkOf(el);
      if (i < 0) {
        const k = el.dataset.k;
        if (k === 'title') doc.t[L] = el.textContent.replace(/\s+/g, ' ').trim();
        if (k === 'sub') { const v = el.textContent.replace(/\s+/g, ' ').trim(); doc.sub = doc.sub || { ru: '', en: '' }; doc.sub[L] = v; if (!doc.sub.ru && !doc.sub.en) doc.sub = null; }
        return;
      }
      const b = doc.bl[i];
      const k = el.dataset.k;
      if (k === 'cap') { b.cap = b.cap || { ru: '', en: '' }; b.cap[L] = readMd(el); if (!b.cap.ru && !b.cap.en) delete b.cap; }
      else if (k === 'kicker') { b.kicker = el.textContent.replace(/\s+/g, ' ').trim(); if (!b.kicker) delete b.kicker; }
      else if (k === 'by') { const v = el.textContent.replace(/\s+/g, ' ').trim(); b.by = b.by || { ru: '', en: '' }; b.by[L] = v; if (!b.by.ru && !b.by.en) delete b.by; }
      else b[L] = readMd(el);
    }

    // ввод
    root.addEventListener('input', (e) => {
      const el = e.target.closest('[contenteditable]');
      if (el) {
        if (!el.textContent && el.innerHTML) el.innerHTML = '';       // пустое поле — без <br>, чтобы видна была подсказка
        syncEl(el); changed(); return;
      }
      if (e.target.id === 'ne-date') { if (e.target.value) { doc.d = e.target.value; changed(); } }
    });
    root.addEventListener('focusin', (e) => {
      const el = e.target.closest('.t');
      if (!el) return;
      cur = blkOf(el);
      const b = doc.bl[cur];
      if (b) q('#ne-type').value = b.type;
    });
    // вставка — только текст: абзацы через пустую строку становятся отдельными блоками
    root.addEventListener('paste', (e) => {
      const el = e.target.closest('[contenteditable]');
      if (!el) return;
      e.preventDefault();
      const text = (e.clipboardData || window.clipboardData).getData('text/plain').replace(/\r/g, '');
      const parts = text.split(/\n\s*\n/).map((s) => s.replace(/\s*\n\s*/g, ' ').trim()).filter(Boolean);
      if (!parts.length) return;
      const i = blkOf(el);
      if (!el.classList.contains('t') || parts.length === 1 || !isText(doc.bl[i])) {
        document.execCommand('insertText', false, parts.join(' '));
        return;
      }
      document.execCommand('insertText', false, parts[0]);
      const tail = cutAfterCaret(el);
      syncEl(el);
      const add = parts.slice(1).map((p) => ({ type: 'p', ru: '', en: '', [L]: mdEsc(p) }));
      add[add.length - 1][L] = (add[add.length - 1][L] + (tail ? ' ' + tail : '')).trim();
      doc.bl.splice(i + 1, 0, ...add);
      draw({ i: i + add.length, at: plain(add[add.length - 1][L]).length - plain(tail).length - (tail ? 1 : 0) });
      changed();
    });
    function cutAfterCaret(el) {
      const r = caretRange();
      if (!r) return '';
      r.deleteContents();
      const post = document.createRange();
      post.setStart(r.endContainer, r.endOffset);
      post.setEnd(el, el.childNodes.length);
      const box = document.createElement('div');
      box.appendChild(post.extractContents());
      return readMd(box);
    }
    root.addEventListener('keydown', (e) => {
      const el = e.target.closest('[contenteditable]');
      if (!el || e.isComposing) return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && (e.key === 'k' || e.key === 'K' || e.key === 'л' || e.key === 'Л')) { e.preventDefault(); linkCmd(); return; }
      if (mod && (e.key === 's' || e.key === 'ы')) { e.preventDefault(); flush().then(() => toast('Сохранено', 'ok'), fail); return; }
      const i = blkOf(el);
      // однострочные поля: Enter — дальше
      if (!el.classList.contains('t')) {
        if (e.key === 'Enter') {
          e.preventDefault();
          if (el.dataset.k === 'title') q('[data-k="sub"]').focus();
          else if (el.dataset.k === 'sub') { const t = editableOf(0); if (t) placeCaret(t, 'start'); }
          else if (i >= 0) { insertAfter(i, 'p'); }
        }
        return;
      }
      const b = doc.bl[i];
      if (e.key === 'Enter') {
        e.preventDefault();
        const tail = cutAfterCaret(el);
        syncEl(el);
        const nb = { type: b.type === 'example' ? 'example' : 'p', ru: '', en: '' };
        nb[L] = tail;
        doc.bl.splice(i + 1, 0, nb);
        draw({ i: i + 1, at: 'start' });
        changed();
        return;
      }
      if (e.key === 'Backspace' && atStart(el)) {
        const prev = doc.bl[i - 1];
        if (!b.ru && !b.en && doc.bl.length > 1) {
          e.preventDefault();
          doc.bl.splice(i, 1);
          if (isText(prev)) draw({ i: i - 1, at: 'end' }); else draw(i > 0 ? null : { i: 0, at: 'start' });
          changed();
          return;
        }
        if (isText(prev)) {
          e.preventDefault();
          syncEl(el);
          const at = plain(prev[L]).length + (prev[L] && b[L] ? 1 : 0);
          ['ru', 'en'].forEach((lg) => { prev[lg] = [prev[lg], b[lg]].filter(Boolean).join(' '); });
          doc.bl.splice(i, 1);
          draw({ i: i - 1, at });
          changed();
        }
        return;
      }
      if (e.key === 'Delete' && atEnd(el)) {
        const next = doc.bl[i + 1];
        if (isText(next)) {
          e.preventDefault();
          syncEl(el);
          const at = plain(b[L]).length + (b[L] && next[L] ? 1 : 0);
          ['ru', 'en'].forEach((lg) => { b[lg] = [b[lg], next[lg]].filter(Boolean).join(' '); });
          doc.bl.splice(i + 1, 1);
          draw({ i, at });
          changed();
        }
        return;
      }
      if ((e.key === 'ArrowUp' || e.key === 'ArrowLeft') && atStart(el)) {
        for (let j = i - 1; j >= 0; j--) { const t = editableOf(j); if (t) { e.preventDefault(); placeCaret(t, 'end'); break; } }
        return;
      }
      if ((e.key === 'ArrowDown' || e.key === 'ArrowRight') && atEnd(el)) {
        for (let j = i + 1; j < doc.bl.length; j++) { const t = editableOf(j); if (t) { e.preventDefault(); placeCaret(t, 'start'); break; } }
      }
    });

    // ---------- панель оформления ----------
    q('#ne-type').addEventListener('change', (e) => {
      const b = doc.bl[cur];
      if (!isText(b)) return;
      b.type = e.target.value;
      if (b.type !== 'h') delete b.kicker;
      if (b.type !== 'quote') delete b.by;
      draw({ i: cur, at: 'end' });
      changed();
    });
    q('#ne-fmt').addEventListener('mousedown', (e) => { if (e.target.closest('button')) e.preventDefault(); });
    q('#ne-fmt').addEventListener('click', (e) => {
      const b = e.target.closest('[data-c]');
      if (!b) return;
      const c = b.dataset.c;
      if (c === 'bold' || c === 'italic') {
        const el = document.activeElement && document.activeElement.closest && document.activeElement.closest('[contenteditable]');
        if (!el || !(el.classList.contains('t') || el.dataset.k === 'cap')) { toast('Поставь курсор в текст', 'err'); return; }
        document.execCommand(c);
        syncEl(el); changed();
      }
      if (c === 'link') linkCmd();
      if (c === 'photo') choosePhoto((rf) => insertImg(cur, [rf]));
      if (c === 'quote' || c === 'h') insertAfter(cur, c);
    });
    function linkCmd() {
      const el = document.activeElement && document.activeElement.closest && document.activeElement.closest('[contenteditable]');
      if (!el || !(el.classList.contains('t') || el.dataset.k === 'cap')) { toast('Выдели слова в тексте, чтобы сделать ссылку', 'err'); return; }
      const sel = window.getSelection();
      const range = sel.rangeCount ? sel.getRangeAt(0).cloneRange() : null;
      const node = range && (range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentNode);
      const a = node && node.closest('a');
      if (!a && (!range || range.collapsed)) { toast('Сначала выдели слова для ссылки', 'err'); return; }
      A.modal('<h2 class="m-h">' + (a ? 'Ссылка' : 'Новая ссылка') + '</h2><label class="fl"><span class="fl-l">Адрес</span><input class="inp" id="lk-u" value="' + esc(a ? a.getAttribute('href') : '') + '" placeholder="https://…"></label>' +
        '<p class="m-p sm">Внешние ссылки откроются в новой вкладке. На свою страницу можно ссылаться коротко: /o/1374/</p>' +
        '<div class="m-act">' + (a ? '<button class="btn" data-m="unlink">Убрать ссылку</button>' : '') + '<button class="btn" data-m="close">Отмена</button><button class="btn pri" data-m="ok">Готово</button></div>', (act, b, close) => {
        const restore = () => { el.focus(); sel.removeAllRanges(); if (range) sel.addRange(range); };
        if (act === 'unlink') { close(); restore(); if (a) { const r2 = document.createRange(); r2.selectNodeContents(a); sel.removeAllRanges(); sel.addRange(r2); } document.execCommand('unlink'); syncEl(el); changed(); }
        if (act === 'ok') {
          let u = document.getElementById('lk-u').value.trim();
          if (u && !/^(https?:\/\/|mailto:|\/)/.test(u)) u = 'https://' + u;
          if (!/^(https?:\/\/[^\s]+|mailto:[^\s]+|\/(?![\/\\])[^\s]*)$/.test(u)) { toast('Адрес не похож на ссылку', 'err'); return; }
          close(); restore();
          if (a) a.setAttribute('href', u); else document.execCommand('createLink', false, u);
          syncEl(el); changed();
        }
      });
    }

    // ---------- блоки: меню, вставка, порядок ----------
    function insertAfter(i, type) {
      if (type === 'img') { choosePhoto((rf) => insertImg(i, [rf])); return; }
      const nb = newBlock(type);
      doc.bl.splice(i + 1, 0, nb);
      draw({ i: i + 1, at: 'start' });
      changed();
    }
    function insertImg(i, refs) {
      const at = Math.min(doc.bl.length, Math.max(0, i + 1));
      doc.bl.splice(at, 0, { type: 'img', ph: refs.slice(0, 2), sz: 'wide' });
      if (!doc.cover && refs[0]) setCover(refs[0], true);
      draw();
      changed();
      const w = art.querySelector('.blk[data-i="' + at + '"]');
      if (w) w.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
    art.addEventListener('click', (e) => {
      const g = e.target.closest('[data-g]');
      if (g) {
        const i = g.dataset.g === 'end' ? doc.bl.length - 1 : blkOf(g);
        if (g.dataset.g === 'menu') blockMenu(g, i);
        else addMenu(g, i);
        return;
      }
      const im = e.target.closest('[data-im]');
      if (im) imgCmd(im, blkOf(im));
    });
    function addMenu(anchor, i) {
      popover(anchor, '<button data-v="p">Абзац</button><button data-v="h">Подзаголовок</button><button data-v="quote">Цитата</button><button data-v="example">Примечание</button><button data-v="img">Фото</button>', (v) => insertAfter(i, v));
    }
    function blockMenu(anchor, i) {
      const b = doc.bl[i];
      const types = isText(b) ? Object.keys(TYPES).map((k) => '<button data-v="t:' + k + '"' + (b.type === k ? ' class="on"' : '') + '>' + TYPES[k] + '</button>').join('') + '<hr>' : '';
      popover(anchor, types + (i > 0 ? '<button data-v="up">↑ Выше</button>' : '') + (i < doc.bl.length - 1 ? '<button data-v="down">↓ Ниже</button>' : '') + '<button data-v="del" class="danger">Удалить блок</button>', (v) => {
        if (v.indexOf('t:') === 0) { b.type = v.slice(2); if (b.type !== 'h') delete b.kicker; if (b.type !== 'quote') delete b.by; draw({ i, at: 'end' }); }
        if (v === 'up') { doc.bl.splice(i - 1, 0, doc.bl.splice(i, 1)[0]); draw(); }
        if (v === 'down') { doc.bl.splice(i + 1, 0, doc.bl.splice(i, 1)[0]); draw(); }
        if (v === 'del') { doc.bl.splice(i, 1); if (!doc.bl.length) doc.bl.push({ type: 'p', ru: '', en: '' }); draw(); }
        changed();
      });
    }
    function imgCmd(btn, i) {
      const b = doc.bl[i], n = +btn.dataset.n || 0, c = btn.dataset.im;
      if (c === 'sz') { b.sz = b.sz === 'col' ? 'wide' : 'col'; draw(); changed(); }
      if (c === 'swap') { b.ph.reverse(); draw(); changed(); }
      if (c === 'drop') { b.ph.splice(n, 1); draw(); changed(); }
      if (c === 'add') choosePhoto((rf) => { b.ph = [b.ph[0], rf]; b.sz = 'wide'; draw(); changed(); });
      if (c === 'replace') choosePhoto((rf) => { b.ph[n] = rf; draw(); changed(); });
    }
    // перетаскивание блоков за ⋮⋮
    let dragI = null;
    art.addEventListener('mousedown', (e) => { const g = e.target.closest('[data-g="menu"]'); if (g) { const w = g.closest('.blk'); w.draggable = true; dragI = +w.dataset.i; } });
    art.addEventListener('mouseup', () => { if (dragI !== null) { $$blk().forEach((w) => { w.draggable = false; }); dragI = null; } });
    art.addEventListener('dragstart', (e) => { if (dragI === null) return; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', 'blk'); e.target.classList && e.target.classList.add('dragging'); });
    art.addEventListener('dragend', () => { $$blk().forEach((w) => { w.draggable = false; w.classList.remove('dragging', 'drop-a', 'drop-b'); }); dragI = null; });
    function $$blk() { return Array.from(art.querySelectorAll('.blk')); }
    function dropTarget(e) {
      const ws = $$blk();
      for (const w of ws) { const r2 = w.getBoundingClientRect(); if (e.clientY < r2.top + r2.height / 2) return { i: +w.dataset.i, before: true, w }; }
      const last = ws[ws.length - 1];
      return last ? { i: +last.dataset.i, before: false, w: last } : { i: -1, before: false, w: null };
    }
    art.addEventListener('dragover', (e) => {
      const files = e.dataTransfer && Array.from(e.dataTransfer.types || []).indexOf('Files') >= 0;
      if (dragI === null && !files) return;
      e.preventDefault();
      const t = dropTarget(e);
      $$blk().forEach((w) => w.classList.remove('drop-a', 'drop-b'));
      if (t.w) t.w.classList.add(t.before ? 'drop-b' : 'drop-a');
    });
    art.addEventListener('drop', async (e) => {
      const t = dropTarget(e);
      $$blk().forEach((w) => w.classList.remove('drop-a', 'drop-b'));
      if (dragI !== null) {
        e.preventDefault();
        let to = t.before ? t.i : t.i + 1;
        const moved = doc.bl.splice(dragI, 1)[0];
        if (dragI < to) to -= 1;
        doc.bl.splice(to, 0, moved);
        dragI = null;
        draw(); changed();
        return;
      }
      if (e.dataTransfer.files && e.dataTransfer.files.length) {
        e.preventDefault();
        const got = await A.uploadFiles(e.dataTransfer.files);
        if (!got.length) return;
        const refs = got.map((a) => ({ k: a.uid, i: 0, w: a.w, h: a.h }));
        const at = t.before ? t.i - 1 : t.i;
        if (refs.length === 2) insertImg(at, refs);
        else refs.reverse().forEach((rf) => insertImg(at, [rf]));
      }
    });

    // ---------- фото: выбор ----------
    function noteRefs() {
      const out = [], seen = {};
      const add = (rf) => { const k = rf.k + '-' + rf.i; if (!seen[k]) { seen[k] = 1; out.push(rf); } };
      if (doc.cover) add(doc.cover);
      doc.bl.forEach((b) => { if (b.type === 'img') b.ph.forEach(add); });
      return out;
    }
    function choosePhoto(done, forCover) {
      const D = A.D;
      const tile = (rf) => '<button class="pk" data-m="pick" data-r="' + esc(JSON.stringify(rf)) + '"><img src="' + esc(refUrl(rf)) + '" alt="" loading="lazy"></button>';
      const mine = noteRefs();
      A.modal('<h2 class="m-h">' + (forCover ? 'Обложка заметки' : 'Фото') + '</h2>' +
        '<div class="pk-up"><button class="btn pri" data-m="up">Загрузить с компьютера</button><input type="file" id="pk-file" accept="image/*" ' + (forCover ? '' : 'multiple') + ' hidden><span class="mu sm">JPEG, PNG, WebP, HEIC · лучше от 1600 px по длинной стороне</span></div>' +
        (mine.length ? '<h3 class="h3">Уже в заметке</h3><div class="pk-grid">' + mine.map(tile).join('') + '</div>' : '') +
        '<h3 class="h3">Из архива сайта</h3><input class="inp search" id="pk-q" placeholder="Найти запись: название, имя, место" autocomplete="off"><div class="pk-recs" id="pk-recs"></div>',
      async (act, el, close) => {
        if (act === 'up') { document.getElementById('pk-file').click(); return; }
        if (act === 'pick') { close(); done(JSON.parse(el.dataset.r)); }
      }, { wide: true });
      document.getElementById('pk-file').onchange = async (e) => {
        const got = await A.uploadFiles(e.target.files);
        if (!got.length) return;
        document.getElementById('modal').querySelector('[data-m="close"]').click();
        got.forEach((a) => done({ k: a.uid, i: 0, w: a.w, h: a.h }));
      };
      const box = document.getElementById('pk-recs');
      const norm = (s) => String(s || '').toLowerCase().replace(/ё/g, 'е');
      const drawRecs = (qv) => {
        const nq = norm(qv).trim();
        const recs = D.objects.filter((o) => !o.tmp && o.img && (o.img.v || 0) >= 2 && (!nq || norm(o.t.ru + ' ' + o.t.en + ' ' + (o.pl ? o.pl.ru : '') + ' ' + (o.p || []).map((p) => (D.people[p.id] || {}).ru).join(' ')).indexOf(nq) >= 0)).slice(0, nq ? 12 : 4);
        box.innerHTML = recs.map((o) => {
          const k = String(o.ik || o.id);
          return '<div class="pk-rec"><span class="pk-rt">' + esc(o.t.ru) + '</span><div class="pk-grid">' + o.img.segs.map((g, i) => tile({ k, i, w: g[1], h: g[2] })).join('') + '</div></div>';
        }).join('') || '<p class="mu sm">Ничего не нашлось. Записи со старыми фото (800 px) здесь не показываются.</p>';
      };
      document.getElementById('pk-q').addEventListener('input', A.debounce((e) => drawRecs(e.target.value), 200));
      drawRecs('');
    }
    async function setCover(rf, quiet) {
      // у обложки свои файлы (img/c, img/t): фото из архива или из середины заметки — копией в фото редакции
      try {
        let c = rf;
        if (!(rf.k.charAt(0) === 'u' && rf.i === 0)) {
          if (!quiet) toast('Готовлю обложку…');
          const a = (await api('asset/copy', { body: { k: rf.k, i: rf.i } })).asset;
          c = { k: a.uid, i: 0, w: a.w, h: a.h, cw: a.cw, ch: a.ch };
        } else {
          c = Object.assign({ cw: rf.w, ch: rf.h }, rf);
          const k = Math.min(1, 1200 / Math.max(rf.w, rf.h));
          c.cw = Math.round(rf.w * k); c.ch = Math.round(rf.h * k);
        }
        doc.cover = c;
        drawHead();
        changed();
      } catch (e) { fail(e); }
    }
    q('#ne-cover').addEventListener('click', () => choosePhoto((rf) => setCover(rf), true));

    // ---------- шапка: поля ----------
    q('#ne-lang').addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b || b.dataset.v === L) return;
      L = b.dataset.v;
      root.querySelectorAll('#ne-lang button').forEach((x) => x.classList.toggle('on', x === b));
      root.querySelector('.ne-page').lang = L;
      drawHead(); draw();
    });

    // ---------- перевод ----------
    async function translateAll() {
      await flush().catch(() => {});
      const set = [];
      const items = [];
      const push = (ru, apply) => { items.push(ru || ''); set.push(apply); };
      push(doc.t.ru, (v) => { doc.t.en = v; });
      if (doc.sub && doc.sub.ru) push(doc.sub.ru, (v) => { doc.sub.en = v; });
      doc.bl.forEach((b) => {
        if (b.type === 'img') { if (b.cap && b.cap.ru) push(b.cap.ru, (v) => { b.cap.en = v; }); return; }
        push(b.ru, (v) => { b.en = v; });
        if (b.by && b.by.ru) push(b.by.ru, (v) => { b.by.en = v; });
      });
      const hasEn = doc.t.en || doc.bl.some((b) => b.en);
      if (!(await A.confirmBox(hasEn ? 'Перевести заново?' : 'Перевести на английский?', hasEn ? 'Английская версия заполнится переводом русской — правки в английском тексте заменятся.' : 'Claude переведёт заголовок, текст, подписи и цитаты. Это стоит несколько центов. Потом английскую версию можно поправить.', 'Перевести'))) return;
      toast('Перевожу… это может занять минуту');
      try {
        const out = (await api('translate', { body: { items, ctx: 'note' } })).items;
        out.forEach((v, n) => set[n](v));
        L = 'en';
        root.querySelectorAll('#ne-lang button').forEach((x) => x.classList.toggle('on', x.dataset.v === 'en'));
        drawHead(); draw(); changed();
        toast('Готово — проверь английскую версию', 'ok');
      } catch (e) { fail(e); }
    }

    // ---------- настройки ----------
    function settings() {
      const D = A.D;
      const AHc = window.AH.T.ru.cat;
      const pplHtml = () => (doc.ppl || []).map((p, n) => '<span class="tag">' + esc(p.ru || p.en) + '<button type="button" data-m="ppl-del" data-n="' + n + '" aria-label="Убрать">✕</button></span>').join('');
      const prev = location.origin + '/admin/preview/' + encodeURIComponent(x.preview || '');
      A.modal('<h2 class="m-h">Настройки заметки</h2>' +
        '<div class="fl"><span class="fl-l">Рубрика</span><div class="chips">' + window.AH.CATS.map((c) => '<button type="button" class="chip' + (doc.cats.indexOf(c) >= 0 ? ' on' : '') + '" data-m="cat" data-v="' + c + '">' + esc(AHc[c]) + '</button>').join('') + '</div></div>' +
        '<div class="fl"><span class="fl-l">Имена в указателе <em>внизу заметки и на страницах этих имён</em></span><div class="chips" id="st-ppl">' + pplHtml() + '<span class="ac"><input class="inp ac-in" id="st-ppl-in" placeholder="+ имя из указателя" autocomplete="off"><span class="ac-list" hidden></span></span></div></div>' +
        '<div class="fl"><span class="fl-l">Ссылка на предпросмотр <em>работает без входа — можно отправить</em></span><div class="copy-row"><input class="inp" readonly value="' + esc(prev) + '"><button class="btn sm" data-m="copy">Скопировать</button></div></div>' +
        '<div class="m-list">' +
        '<button class="m-li" data-m="hist">История версий<em>вернуть прежний текст</em></button>' +
        (st.status === 'live' ? '<button class="m-li" data-m="bot">Анонс во входящие бота<em>пост #ahmagnotes со ссылкой на заметку</em></button><button class="m-li danger" data-m="unpub">Снять с сайта<em>заметка станет черновиком</em></button>'
          : '<button class="m-li danger" data-m="del">Удалить черновик</button>') + '</div>' +
        '<div class="m-act"><button class="btn pri" data-m="close">Готово</button></div>', async (act, el, close) => {
        if (act === 'cat') {
          const c = el.dataset.v, i = doc.cats.indexOf(c);
          if (i >= 0 && doc.cats.length > 1) doc.cats.splice(i, 1); else if (i < 0) doc.cats.push(c);
          el.parentNode.querySelectorAll('.chip').forEach((b) => b.classList.toggle('on', doc.cats.indexOf(b.dataset.v) >= 0));
          drawHead(); changed();
        }
        if (act === 'ppl-del') { doc.ppl.splice(+el.dataset.n, 1); redrawPpl(); changed(); }
        if (act === 'copy') { const inp = el.parentNode.querySelector('input'); inp.select(); try { await navigator.clipboard.writeText(inp.value); } catch (e2) { document.execCommand('copy'); } toast('Ссылка скопирована', 'ok'); }
        if (act === 'hist') { close(); A.history('note', nid, doc.t.ru, (r2) => { doc = r2.doc; drawHead(); draw(); savedJson = JSON.stringify(doc); toast('Версия возвращена в черновик', 'ok'); if (st.status === 'live') st.dirty = true; statusText(); }); }
        if (act === 'bot') { close(); try { await api('note/tobot', { body: { id: nid } }); toast('Бот собирает анонс — пришлёт сообщение', 'ok'); } catch (e2) { fail(e2); } }
        if (act === 'unpub') {
          close();
          if (!(await A.confirmBox('Снять заметку с сайта?', 'Страница заметки пропадёт с сайта, текст останется здесь черновиком.', 'Снять', true))) return;
          try { await api('note/unpublish', { body: { id: nid } }); st.status = 'draft'; st.dirty = false; statusText(); A.kick(); toast('Заметка снимается с сайта', 'ok'); } catch (e2) { fail(e2); }
        }
        if (act === 'del') {
          close();
          if (!(await A.confirmBox('Удалить черновик?', 'Текст пропадёт насовсем.', 'Удалить', true))) return;
          try { dead = true; clearTimeout(timer); await api('note/delete', { body: { id: nid } }); savedJson = JSON.stringify(doc); toast('Черновик удалён', 'ok'); opts.onLeave(); } catch (e2) { dead = false; fail(e2); }
        }
      });
      function redrawPpl() {
        const box = document.getElementById('st-ppl');
        box.querySelectorAll('.tag').forEach((t) => t.remove());
        box.insertAdjacentHTML('afterbegin', pplHtml());
      }
      const inp = document.getElementById('st-ppl-in');
      const norm = (s) => String(s || '').toLowerCase().replace(/ё/g, 'е');
      A.autocomplete(inp, inp.nextElementSibling, (qv) => Object.keys(D.people).filter((k) => norm(D.people[k].ru + ' ' + D.people[k].en).indexOf(norm(qv)) >= 0 && !(doc.ppl || []).some((p) => p.id === k))
        .slice(0, 8).map((k) => ({ label: D.people[k].ru, sub: D.people[k].en, v: k })), (k) => {
        doc.ppl = doc.ppl || [];
        doc.ppl.push({ id: k, ru: D.people[k].ru, en: D.people[k].en });
        redrawPpl(); changed();
      });
    }

    // ---------- публикация ----------
    function problems() {
      const out = [];
      if (!doc.t.ru) out.push('нет заголовка');
      if (!doc.cover) out.push('нет обложки');
      if (!doc.bl.some((b) => b.type === 'p' && b.ru)) out.push('нет ни одного абзаца');
      return out;
    }
    function publishMenu() {
      const pr = problems();
      const enMissing = !doc.t.en || doc.bl.some((b) => b.type !== 'img' && b.ru && !b.en);
      const now = new Date(Date.now() + 3600e3);
      const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      const head = st.status === 'live' ? (st.dirty ? 'Обновить заметку на сайте' : 'Заметка на сайте') : st.status === 'scheduled' ? 'Заметка по расписанию' : 'Опубликовать заметку';
      A.modal('<h2 class="m-h">' + head + '</h2>' +
        (pr.length ? '<p class="note-bar err">Пока нельзя: ' + esc(pr.join(', ')) + '.</p>' : '') +
        (!pr.length && enMissing ? '<p class="note-bar">Английская версия неполная — там, где перевода нет, на английской странице будет русский текст.</p>' : '') +
        (st.status === 'scheduled' ? '<p class="m-p">Выйдет ' + esc(A.ftime(st.at)) + '.</p>' : '') +
        (st.status === 'live' && !st.dirty ? '<p class="m-p">На сайте — последняя версия. Новые правки сохранятся черновиком, пока ты их не опубликуешь.</p>' : '') +
        (!pr.length && !(st.status === 'live' && !st.dirty) ? '<div class="m-choice"><button class="choice" data-m="now"><b>' + (st.status === 'live' ? 'Обновить сейчас' : 'Опубликовать сейчас') + '</b><span>на сайте через минуту-две</span></button>' +
          (st.status !== 'live' ? '<div class="choice sched"><b>Запланировать</b><span><input type="datetime-local" class="inp" id="pb-at" value="' + local + '"> <button class="btn sm" data-m="at">Поставить</button></span></div>' : '') + '</div>' +
          (st.status !== 'live' ? '<label class="check"><input type="checkbox" id="pb-ann"> Сразу положить анонс во входящие бота</label>' : '') : '') +
        '<div class="m-act">' + (st.status === 'scheduled' ? '<button class="btn" data-m="unsched">Отменить расписание</button>' : '') + '<button class="btn" data-m="close">Закрыть</button></div>', async (act, el, close) => {
        const ann = document.getElementById('pb-ann');
        const announce = !!(ann && ann.checked);
        if (act === 'now') {
          close();
          try {
            await flush();
            const r2 = await api('note/publish', { body: { id: nid, announce } });
            doc = r2.doc; savedJson = JSON.stringify(doc);
            st.status = 'live'; st.dirty = !!r2.dirty; st.live = true;
            drawHead(); draw(); statusText(); A.kick();
            toast('Заметка уходит на сайт' + (announce ? ' · анонс собирается в боте' : ''), 'ok');
          } catch (e) { fail(e); }
        }
        if (act === 'at') {
          const v = document.getElementById('pb-at').value;
          if (!v) { toast('Выбери дату и время', 'err'); return; }
          close();
          try {
            await flush();
            const r2 = await api('note/publish', { body: { id: nid, at: new Date(v).toISOString(), announce } });
            st.status = 'scheduled'; st.at = r2.at; statusText();
            toast('Заметка выйдет ' + A.ftime(r2.at), 'ok');
          } catch (e) { fail(e); }
        }
        if (act === 'unsched') {
          close();
          try { await api('note/unpublish', { body: { id: nid } }); st.status = 'draft'; st.at = null; statusText(); toast('Расписание отменено', 'ok'); } catch (e) { fail(e); }
        }
      });
    }

    root.querySelector('.ne-bar').addEventListener('click', (e) => {
      const b = e.target.closest('[data-a]');
      if (!b) return;
      const a = b.dataset.a;
      if (a === 'settings') settings();
      if (a === 'tr') translateAll();
      if (a === 'pub') publishMenu();
      if (a === 'preview') { flush().catch(() => {}); }
    });
    const onVis = () => { if (document.visibilityState === 'hidden') save(); };
    document.addEventListener('visibilitychange', onVis);

    drawHead();
    draw();
    statusText();
    if (!doc.t.ru) q('[data-k="title"]').focus();

    return {
      // уход внутри редакции: destroy() сохранит сам, спрашивать — только если сохранение не проходит;
      // закрытие вкладки (closing): и если последние правки ещё не ушли
      unsaved: (closing) => !dead && (!!saveErr || (!!closing && JSON.stringify(doc) !== savedJson)),
      destroy: () => {
        document.removeEventListener('visibilitychange', onVis);
        if (popClose) popClose();
        if (JSON.stringify(doc) !== savedJson) save();
        dead = true;
        clearTimeout(timer);
      }
    };
  }

  window.NoteEditor = { open, dom2md, mdEsc };
})();
