/* AH Magazine · the site.
   One file runs in two places. At build time (site/prerender.js, in Node) it renders every page ahead, so a
   page opens at once and search engines, Telegram previews and Instant View see the real text. In the browser
   it takes over the page that is already there: later clicks render on the spot from the data file, without
   reloading. Every view is a plain function from data to HTML, so both places produce the same markup. */
(function (root) {
  'use strict';

  var CATS = ['architecture', 'art', 'photography', 'cinema', 'archive'];
  var PER_ORDER = ['pre1800', '1800s', '1900s', '1910s', '1920s', '1930s', '1940s', '1950s', '1960s', '1970s', '1980s', '1990s', '2000s', '2010s', '2020s'];
  var TG = 'https://t.me/ahmagazine', IG = 'https://www.instagram.com/a.h.mag/';
  var CONTACT = '@poweredbytheholyspirit', CONTACT_URL = 'https://t.me/poweredbytheholyspirit';

  var T = {
    ru: {
      archive: 'Архив', notes: 'Notes', index: 'Указатель', about: 'О журнале', partners: 'Партнёрство',
      search: 'Поиск', menu: 'Меню', close: 'Закрыть', home: 'На главную',
      cat: { architecture: 'Архитектура', art: 'Искусство', photography: 'Фотография', cinema: 'Кино', archive: 'Из архивов' },
      catShort: { architecture: 'Архитектура', art: 'Искусство', photography: 'Фото', cinema: 'Кино', archive: 'Из архивов' },
      role: { architect: 'архитектор', studio: 'бюро', artist: 'художник', photographer: 'фотограф', director: 'режиссёр', designer: 'дизайнер', writer: 'автор' },
      cr: { project: 'Проект', photo: 'Фото', source: 'Источник', courtesy: 'Предоставлено', other: 'Также' },
      months: ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'],
      objForms: ['объект', 'объекта', 'объектов'],
      tagline: 'Визуальный архив искусства, пространства и структуры',
      latest: 'Новое в архиве', rubrics: 'Рубрики', allArchive: 'Весь архив', allNotes: 'Все заметки', allIndex: 'Весь указатель',
      view: 'Смотреть', read: 'Читать', newest: 'Сегодня в архиве',
      bandT: 'Новые объекты каждый день', bandP: 'Архив пополняется в Telegram и Instagram. Сайт собирает всё опубликованное в одном месте.',
      all: 'Все', filters: 'Фильтры', country: 'Страна', period: 'Время', order: 'Порядок',
      sNew: 'Сначала новые', sOld: 'Сначала ранние публикации', sYear: 'По году создания',
      grid: 'Галерея', list: 'Список', more: 'Показать ещё', clear: 'Сбросить всё', hint: 'Наведите на снимок',
      nothing: 'По этим фильтрам ничего нет', inSel: 'в выборке', archiveSub: 'архитектура, искусство, фотография, кино и находки из архивов',
      tgPost: 'Пост в Telegram', related: 'Рядом в архиве', prev: 'Предыдущий', next: 'Следующий', since: 'В архиве с',
      video: 'Видео в посте Telegram', inIndex: 'в указателе', photos: 'фото',
      readNext: 'Читать дальше', notesSub: 'Длинные тексты AH Magazine о том, что стоит за архивом',
      names: 'Имена', countries: 'Страны', time: 'Время', indexSub: 'Люди, страны и годы всех объектов архива',
      copy: 'Скопировать', copied: 'Скопировано',
      nfT: 'Такой страницы нет', nfP: 'Возможно, ссылка устарела или объект ещё не добавлен.',
      sPh: 'Название, имя, город, год', sTry: 'Например', sObjs: 'Объекты', sPeople: 'Имена', sNotes: 'Notes',
      sNone: 'Ничего не найдено по запросу', sWait: 'Загружаю архив…', person: 'Имя в указателе', mentioned: 'Упоминается в Notes',
      tgShort: 'Telegram', igShort: 'Instagram', sections: 'Разделы', links: 'Ссылки', crumbs: 'Навигация'
    },
    en: {
      archive: 'Archive', notes: 'Notes', index: 'Index', about: 'About', partners: 'Partnerships',
      search: 'Search', menu: 'Menu', close: 'Close', home: 'Home',
      cat: { architecture: 'Architecture', art: 'Art', photography: 'Photography', cinema: 'Cinema', archive: 'Archival' },
      catShort: { architecture: 'Architecture', art: 'Art', photography: 'Photography', cinema: 'Cinema', archive: 'Archival' },
      role: { architect: 'architect', studio: 'studio', artist: 'artist', photographer: 'photographer', director: 'director', designer: 'designer', writer: 'writer' },
      cr: { project: 'Project', photo: 'Photography', source: 'Source', courtesy: 'Courtesy', other: 'Also' },
      months: ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'],
      objForms: ['entry', 'entries', 'entries'],
      tagline: 'Visual archive of art, space and structure',
      latest: 'New in the archive', rubrics: 'Sections', allArchive: 'Full archive', allNotes: 'All notes', allIndex: 'Full index',
      view: 'View', read: 'Read', newest: 'Today in the archive',
      bandT: 'New entries every day', bandP: 'The archive grows on Telegram and Instagram. The website keeps everything published in one place.',
      all: 'All', filters: 'Filters', country: 'Country', period: 'Period', order: 'Order',
      sNew: 'Newest first', sOld: 'Earliest posts first', sYear: 'By year made',
      grid: 'Grid', list: 'List', more: 'Show more', clear: 'Clear all', hint: 'Hover over an image',
      nothing: 'Nothing matches these filters', inSel: 'in this selection', archiveSub: 'architecture, art, photography, cinema and archival finds',
      tgPost: 'Telegram post', related: 'Related', prev: 'Previous', next: 'Next', since: 'In the archive since',
      video: 'Video in the Telegram post', inIndex: 'in the index', photos: 'photos',
      readNext: 'Read next', notesSub: 'Longer essays from AH Magazine on what lies behind the archive',
      names: 'Names', countries: 'Countries', time: 'Period', indexSub: 'People, countries and years across the archive',
      copy: 'Copy', copied: 'Copied',
      nfT: 'Page not found', nfP: 'The link may be out of date, or the entry has not been added yet.',
      sPh: 'Title, name, city, year', sTry: 'Try', sObjs: 'Entries', sPeople: 'Names', sNotes: 'Notes',
      sNone: 'Nothing found for', sWait: 'Loading the archive…', person: 'Name in the index', mentioned: 'Mentioned in Notes',
      tgShort: 'Telegram', igShort: 'Instagram', sections: 'Sections', links: 'Links', crumbs: 'Breadcrumbs'
    }
  };

  var COPY = {
    about: {
      ru: {
        title: 'О журнале',
        lead: 'AH Magazine — визуальный архив искусства, пространства и структуры.',
        p: [
          'Мы собираем то, что хочется сохранить: здания и интерьеры, картины, фотографии, кадры из фильмов и находки из музейных и библиотечных архивов. Новые проекты стоят здесь рядом со старыми: дом, построенный в 2026 году, и снимок парижского неба 1856-го.',
          'Каждый объект — несколько фотографий и короткий текст о том, что это, кто и где это сделал. Под каждым материалом указан источник.',
          'Журнал начался в марте 2025 года как Telegram-канал. На сайте всё опубликованное собрано в одном месте: архив можно смотреть по рубрикам, странам, времени и именам. Длинные тексты выходят в рубрике Notes.'
        ],
        stats: ['в архиве', 'стран', 'имён в указателе'],
        contactH: 'Связаться',
        contact: 'По всем вопросам пишите в Telegram:'
      },
      en: {
        title: 'About',
        lead: 'AH Magazine is a visual archive of art, space and structure.',
        p: [
          'We collect what we want to keep: buildings and interiors, paintings, photographs, film stills and finds from museum and library archives. New projects sit here next to old ones: a house built in 2026 and a photograph of the Paris sky from 1856.',
          'Each entry is a few photographs and a short text about what it is, who made it and where. The source is credited under every entry.',
          'The magazine started in March 2025 as a Telegram channel. The website keeps everything published in one place, so the archive can be browsed by section, country, period and name. Longer essays appear in Notes.'
        ],
        stats: ['in the archive', 'countries', 'names in the index'],
        contactH: 'Contact',
        contact: 'For all inquiries, write on Telegram:'
      }
    },
    partners: {
      ru: {
        title: 'Партнёрство',
        lead: 'Мы открыты к работе с архитектурными бюро, студиями, галереями, издательствами и брендами, которым близок наш взгляд на архитектуру и искусство.',
        fmtH: 'Форматы',
        fmt: [
          ['Публикация проекта', 'Пост о вашем проекте в Telegram и Instagram и страница в архиве сайта.'],
          ['Текст в Notes', 'Длинный материал на тему, связанную с вашей работой: история, контекст, детали.'],
          ['Подборка', 'Тематическая серия постов вокруг вашего проекта, выставки или коллекции.'],
          ['Видео для Instagram', 'Короткий ролик о проекте в формате Reels.']
        ],
        howH: 'Как мы выбираем',
        how: 'Мы публикуем только то, что сами хотели бы сохранить в архиве. Партнёрские материалы отмечены.',
        // «Аудитория» появится на странице, когда здесь будут настоящие цифры (строка вместо null)
        audH: 'Аудитория',
        aud: null,
        contactH: 'Написать',
        contact: 'Обсудить проект можно в Telegram:'
      },
      en: {
        title: 'Partnerships',
        lead: 'We are open to working with architecture practices, studios, galleries, publishers and brands who share our view of architecture and art.',
        fmtH: 'Formats',
        fmt: [
          ['Project feature', 'A post about your project on Telegram and Instagram, and a page in the website archive.'],
          ['Essay in Notes', 'A long piece on a subject connected to your work: history, context, details.'],
          ['Series', 'A themed series of posts around your project, exhibition or collection.'],
          ['Instagram video', 'A short Reels video about the project.']
        ],
        howH: 'How we choose',
        how: 'We only publish what we would want to keep in the archive ourselves. Partner content is labeled.',
        audH: 'Audience',
        aud: null,
        contactH: 'Get in touch',
        contact: 'To discuss a project, write on Telegram:'
      }
    }
  };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ---------- addresses ----------
  // Russian lives at the root, English under /en/. Archive filters live in the query string so that one
  // page (/archive/) serves every combination.
  function langOfPath(path) { return path === '/en' || path.indexOf('/en/') === 0 ? 'en' : 'ru'; }
  function prefix(L) { return L === 'en' ? '/en' : ''; }
  function stQuery(st) {
    var q = [];
    if (st.cat && st.cat !== 'all') q.push('r=' + st.cat);
    if (st.co) q.push('c=' + st.co);
    if (st.per) q.push('t=' + st.per);
    if (st.sort && st.sort !== 'new') q.push('s=' + st.sort);
    if (st.view === 'list') q.push('v=list');
    return q.length ? '?' + q.join('&') : '';
  }
  function urlOf(r, L) {
    var b = prefix(L);
    switch (r.name) {
      case 'archive': return b + '/archive/' + stQuery(r.st || {});
      case 'object': return b + '/o/' + r.id + '/';
      case 'note': return b + '/n/' + r.id + '/';
      case 'notes': return b + '/notes/';
      case 'index': return b + '/index/' + (r.tab && r.tab !== 'names' ? r.tab + '/' : '');
      case 'person': return b + '/p/' + r.id + '/';
      case 'about': return b + '/about/';
      case 'partners': return b + '/partners/';
      default: return b + '/';
    }
  }
  // old links had the whole address after # (theahmag.com/#o-1374); they still work and lead to the same page
  function legacyRoute(hash) {
    var h = '';
    try { h = decodeURIComponent(String(hash || '').replace(/^#/, '')); } catch (e) { return null; }
    if (!h) return null;
    var parts = h.split('~'), head = parts[0], m;
    if (head === 'home') return { name: 'home' };
    if (head === 'archive') {
      var st = { cat: 'all', co: null, per: null, sort: 'new', view: 'grid' };
      parts.slice(1).forEach(function (seg) {
        var i = seg.indexOf('.'), k = seg.slice(0, i), v = seg.slice(i + 1);
        if (k === 'r') st.cat = v;
        if (k === 'c') st.co = v;
        if (k === 't') st.per = v;
        if (k === 's' && (v === 'old' || v === 'year')) st.sort = v;
        if (k === 'v' && v === 'list') st.view = 'list';
      });
      return { name: 'archive', st: st };
    }
    if ((m = head.match(/^o-(\d+)$/))) return { name: 'object', id: +m[1] };
    if ((m = head.match(/^n-(\d+)$/))) return { name: 'note', id: +m[1] };
    if ((m = head.match(/^p-([a-z0-9-]+)$/))) return { name: 'person', id: m[1] };
    if (head === 'notes') return { name: 'notes' };
    if (head === 'index') return { name: 'index', tab: (parts[1] === 'countries' || parts[1] === 'time') ? parts[1] : 'names' };
    if (head === 'about') return { name: 'about' };
    if (head === 'partners') return { name: 'partners' };
    return null;
  }

  // ---------- the site over one data set ----------
  // createSite(null) still draws the header, footer, menu and the not-found page: the browser needs those
  // before the data file has arrived.
  function createSite(D) {
    D = D || { objects: [], notes: [], people: {}, countries: {}, periods: {} };
    var OBJ = D.objects, NOTES = D.notes, PEOPLE = D.people, COUNTRIES = D.countries, PERIODS = D.periods;
    var BY = {}, NBY = {};
    OBJ.forEach(function (o, i) { o.ix = i; BY[o.id] = o; });
    NOTES.forEach(function (n) { NBY[n.id] = n; });
    var L = 'ru';
    var YEAR = (D.built || '').slice(0, 4) || String(new Date().getFullYear());

    function t(k) { return T[L][k]; }
    function tr(o) { if (!o) return ''; var v = o[L]; return (v != null && v !== '') ? v : (o.ru || ''); }
    function u(r) { return urlOf(r, L); }
    function uo(id) { return u({ name: 'object', id: id }); }
    function un(id) { return u({ name: 'note', id: id }); }
    function up(id) { return u({ name: 'person', id: id }); }
    function ua(st) { return u({ name: 'archive', st: st }); }
    function plural(n, f) {
      if (L === 'en') return n === 1 ? f[0] : f[1];
      var a = Math.abs(n) % 100, b = a % 10;
      if (a > 10 && a < 20) return f[2];
      if (b > 1 && b < 5) return f[1];
      if (b === 1) return f[0];
      return f[2];
    }
    function count(n) { return n + ' ' + plural(n, t('objForms')); }
    function fdate(d) {
      var p = d.split('-'), m = t('months')[+p[1] - 1];
      return (+p[2]) + ' ' + m + ' ' + p[0];
    }
    function perLabel(p) {
      if (p === 'pre1800') return L === 'ru' ? 'До 1800' : 'Before 1800';
      if (p === '1800s') return L === 'ru' ? 'XIX век' : '19th century';
      return L === 'ru' ? p.slice(0, 4) + '-е' : p;
    }
    function catLabel(c) { return t('cat')[c] || ''; }
    function arrow(color) {
      return '<svg width="22" height="8" viewBox="0 0 22 8" fill="none" stroke="' + (color || 'currentColor') + '" stroke-width="1" aria-hidden="true"><line x1="0" y1="4" x2="21" y2="4"/><polyline points="17,1 21,4 17,7"/></svg>';
    }
    // The AH sign as four strokes traced from the logo file (642 × 581, stroke 26, round ends).
    // Path order is the drawing order of the intro: stem with the arch, two posts, crossbar.
    var MARK = '<svg class="mark" viewBox="0 0 642 581" width="642" height="581" fill="none" stroke="currentColor" stroke-width="26" stroke-linecap="round" aria-hidden="true" focusable="false">' +
      '<path d="M20.75 563.5V184.87C20.75 109.27 53.76 40.58 126.35 21.48C176.21 8.36 224.9 14.48 256.73 40.33"/>' +
      '<path d="M323.75 563.5V13.75"/><path d="M627.5 563.5V13.75"/><path d="M78 288.5H569.5"/></svg>';
    var ICON_SEARCH = '<svg width="17" height="17" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.1" aria-hidden="true"><circle cx="7.5" cy="7.5" r="5.5"/><line x1="11.5" y1="11.5" x2="16.5" y2="16.5"/></svg>';
    var ICON_MENU = '<svg width="20" height="12" viewBox="0 0 20 12" fill="none" stroke="currentColor" stroke-width="1.1" aria-hidden="true"><line x1="0" y1="1" x2="20" y2="1"/><line x1="0" y1="6" x2="20" y2="6"/><line x1="0" y1="11" x2="20" y2="11"/></svg>';
    var ICON_X = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.1" aria-hidden="true"><line x1="2" y1="2" x2="14" y2="14"/><line x1="14" y1="2" x2="2" y2="14"/></svg>';
    var ICON_PLAY = '<svg width="10" height="12" viewBox="0 0 10 12" fill="currentColor" aria-hidden="true"><path d="M0 0l10 6-10 6z"/></svg>';
    var ICON_SMALLX = '<svg width="9" height="9" viewBox="0 0 9 9" fill="none" stroke="currentColor" stroke-width="1.1" aria-hidden="true"><line x1="1" y1="1" x2="8" y2="8"/><line x1="8" y1="1" x2="1" y2="8"/></svg>';
    var ICON_FILTER = '<svg width="14" height="10" viewBox="0 0 14 10" fill="none" stroke="currentColor" stroke-width="1" aria-hidden="true"><line x1="0" y1="2" x2="14" y2="2"/><line x1="3" y1="8" x2="11" y2="8"/></svg>';

    // ---------- pictures ----------
    // Per entry: img/c/<key>.jpg the first photo (cover: feeds, link previews), img/t/<key>.jpg a 240 px square
    // for the archive grid, lists and search. <key> is the entry number, or o.ik for entries whose pictures
    // were uploaded before the number was known.
    // Entries with img.v >= 2 have every photo as its own file in two sizes: img/f/<key>-<n>.jpg (up to 2000 px)
    // and img/m/<key>-<n>.jpg (up to 1000 px); the browser picks one through srcset. Older entries keep all
    // photos in one strip, img/p/<key>.jpg, 800 px wide, shown as background slices.
    var MID = 1000;
    var SZ_OBJ = '(max-width: 900px) 100vw, (max-width: 1440px) 62vw, 900px';   // photo column of an entry
    var SZ_FIG = '(max-width: 1080px) 100vw, 1080px';                            // a photo inside a note
    var SZ_HERO = '(max-width: 900px) 100vw, 55vw';                              // split hero, cropped to a square
    function ik(o) { return o.ik || o.id; }
    function hi(img) { return img && img.v >= 2; }
    // srcset of photo i: the 1000 px file and the full one, with their real widths
    function srcset(key, img, i) {
      var g = img.segs[i], k = Math.min(1, MID / Math.max(g[1], g[2]));
      return '/img/m/' + key + '-' + i + '.jpg ' + Math.round(g[1] * k) + 'w, /img/f/' + key + '-' + i + '.jpg ' + g[1] + 'w';
    }
    function picture(key, img, i, alt, eager, sizes) {
      var g = img.segs[i];
      return '<img src="/img/f/' + key + '-' + i + '.jpg" srcset="' + srcset(key, img, i) + '" sizes="' + sizes + '" width="' + g[1] + '" height="' + g[2] +
        '" alt="' + esc(alt) + '"' + (eager ? ' fetchpriority="high"' : ' loading="lazy"') + ' decoding="async">';
    }
    // the cover in the big split hero: for new entries the browser may take the full-size first photo
    function heroImg(o, alt) {
      var key = ik(o), extra = '';
      if (hi(o.img)) {
        var g = o.img.segs[0];
        extra = ' srcset="/img/c/' + key + '.jpg ' + o.img.cw + 'w, /img/f/' + key + '-0.jpg ' + g[1] + 'w" sizes="' + SZ_HERO + '"';
      }
      return '<img src="/img/c/' + key + '.jpg"' + extra + ' width="' + o.img.cw + '" height="' + o.img.ch + '" alt="' + esc(alt || '') + '" fetchpriority="high" decoding="async">';
    }
    function thumb(o, size) {
      return '<img src="/img/t/' + ik(o) + '.jpg" width="' + size + '" height="' + size + '" alt="" loading="lazy" decoding="async">';
    }
    function segStyle(img, i, key) {
      var g = img.segs[i], y0 = g[0], w = g[1], h = g[2];
      var size = img.W / w * 100, py = img.H > h ? y0 / (img.H - h) * 100 : 0;
      return 'aspect-ratio:' + w + '/' + h + ';background-image:url(/img/p/' + key + '.jpg);background-size:' + size.toFixed(4) + '% auto;background-position:0 ' + py.toFixed(4) + '%';
    }
    function cover(o, alt, eager) {
      return '<img src="/img/c/' + ik(o) + '.jpg" width="' + o.img.cw + '" height="' + o.img.ch + '" alt="' + esc(alt || '') + '"' +
        (eager ? ' fetchpriority="high"' : ' loading="lazy"') + ' decoding="async">';
    }
    // hyphenated words (Сан-Роке, Jean-Louis) never break at the hyphen
    function nowrapHy(html) {
      return html.replace(/[\p{L}\p{N}]+(?:-[\p{L}\p{N}]+)+/gu, function (w) { return '<span class="nw">' + w + '</span>'; });
    }
    // headline "Object // Author // Place": when author and place are printed underneath, show only the object
    function headTitle(o) {
      var full = tr(o.t), head = full.split(' // ')[0].trim();
      return head && head !== full && (o.p.length || o.pl) ? head : full;
    }
    function personName(id) { var p = PEOPLE[id]; return p ? tr(p) : ''; }
    function byline(o) {
      var names = o.p.map(function (x) { return personName(x.id); }).filter(Boolean).join(', ');
      return names || tr(o.pl);
    }
    function metaParts(o, linked) {
      var parts = [];
      if (o.p.length) {
        parts.push(o.p.map(function (x) {
          var nm = esc(personName(x.id));
          var note = x.note ? ' <span class="mu">(' + esc(tr(x.note)) + ')</span>' : '';
          return (linked ? '<a href="' + up(x.id) + '">' + nm + '</a>' : nm) + note;
        }).join(', '));
      }
      if (o.pl) parts.push(esc(tr(o.pl)));
      if (o.y) parts.push(esc(tr(o.y)));
      return parts;
    }
    // a credit is [role, name, url] or [role, name, url, English name]
    function creditName(c) { return L === 'en' && c[3] ? c[3] : c[1]; }

    // ---------- routing ----------
    function parseQuery(search) {
      var q = {};
      String(search || '').replace(/^\?/, '').split('&').forEach(function (kv) {
        if (!kv) return;
        var i = kv.indexOf('='), k = i < 0 ? kv : kv.slice(0, i), v = i < 0 ? '' : kv.slice(i + 1);
        try { q[decodeURIComponent(k)] = decodeURIComponent(v.replace(/\+/g, ' ')); } catch (e) {}
      });
      return q;
    }
    function cleanSt(st) {
      return {
        cat: CATS.indexOf(st.cat) >= 0 ? st.cat : 'all',
        co: st.co && COUNTRIES[st.co] ? st.co : null,
        per: st.per && PERIODS[st.per] ? st.per : null,
        sort: st.sort === 'old' || st.sort === 'year' ? st.sort : 'new',
        view: st.view === 'list' ? 'list' : 'grid'
      };
    }
    // → {name, lang, ...}; with data, unknown entries and names become 404
    function parse(path, search) {
      var lang = langOfPath(path), p = lang === 'en' ? path.slice(3) : path, m;
      p = p.replace(/index\.html$/, '');
      if (p.charAt(p.length - 1) !== '/') p += '/';
      var r;
      if (p === '/') r = { name: 'home' };
      else if (p === '/archive/') {
        var q = parseQuery(search);
        r = { name: 'archive', st: cleanSt({ cat: q.r, co: q.c, per: q.t, sort: q.s, view: q.v }) };
      }
      else if ((m = p.match(/^\/o\/(\d+)\/$/))) r = !D.objects.length || BY[+m[1]] ? { name: 'object', id: +m[1] } : { name: '404' };
      else if ((m = p.match(/^\/n\/(\d+)\/$/))) r = !D.objects.length || NBY[+m[1]] ? { name: 'note', id: +m[1] } : { name: '404' };
      else if ((m = p.match(/^\/p\/([a-z0-9-]+)\/$/))) r = !D.objects.length || PEOPLE[m[1]] ? { name: 'person', id: m[1] } : { name: '404' };
      else if (p === '/notes/') r = { name: 'notes' };
      else if ((m = p.match(/^\/index\/(?:(countries|time)\/)?$/))) r = { name: 'index', tab: m[1] || 'names' };
      else if (p === '/about/') r = { name: 'about' };
      else if (p === '/partners/') r = { name: 'partners' };
      else r = { name: '404' };
      r.lang = lang;
      return r;
    }
    function withSt(st, patch) { var n = {}; for (var k in st) n[k] = st[k]; for (var j in patch) n[j] = patch[j]; return n; }

    // ---------- shared chrome ----------
    function section(r) {
      if (r.name === 'archive' || r.name === 'object') return 'archive';
      if (r.name === 'notes' || r.name === 'note') return 'notes';
      if (r.name === 'index' || r.name === 'person') return 'index';
      return r.name;
    }
    function header(r) {
      var sec = section(r);
      function nav(name, key) { return '<a href="' + u({ name: name }) + '"' + (sec === key ? ' aria-current="page"' : '') + '>' + t(key) + '</a>'; }
      var other = L === 'ru' ? 'en' : 'ru';
      var here = urlOf(r.name === '404' ? { name: 'home' } : r, other);
      return '<header class="hd"><div class="wrap hd-in">' +
        '<nav class="hd-nav" aria-label="' + t('sections') + '">' + nav('archive', 'archive') + nav('notes', 'notes') + nav('index', 'index') + nav('about', 'about') + '</nav>' +
        '<a class="logo" href="' + u({ name: 'home' }) + '" aria-label="AH Magazine — ' + t('home') + '">' + MARK + '</a>' +
        '<div class="tools"><div class="lang" role="group" aria-label="Language">' +
        (L === 'ru' ? '<a href="' + urlOf(r.name === '404' ? { name: 'home' } : r, 'ru') + '" data-act="lang" data-v="ru" aria-current="true" hreflang="ru" lang="ru">RU</a><a href="' + here + '" data-act="lang" data-v="en" hreflang="en" lang="en">EN</a>'
          : '<a href="' + here + '" data-act="lang" data-v="ru" hreflang="ru" lang="ru">RU</a><a href="' + urlOf(r.name === '404' ? { name: 'home' } : r, 'en') + '" data-act="lang" data-v="en" aria-current="true" hreflang="en" lang="en">EN</a>') +
        '</div>' +
        '<button class="ib" data-act="search" aria-label="' + t('search') + '">' + ICON_SEARCH + '</button>' +
        '<button class="ib menu-b" data-act="menu" aria-label="' + t('menu') + '">' + ICON_MENU + '</button>' +
        '</div></div></header>';
    }
    function footer() {
      return '<footer class="wrap ft">' +
        '<a class="ft-logo" href="' + u({ name: 'home' }) + '" aria-label="AH Magazine">' + MARK + '</a>' +
        '<p>' + t('tagline') + '</p>' +
        '<nav aria-label="' + t('links') + '"><a href="' + u({ name: 'archive' }) + '">' + t('archive') + '</a><a href="' + u({ name: 'notes' }) + '">' + t('notes') + '</a><a href="' + u({ name: 'index' }) + '">' + t('index') + '</a><a href="' + u({ name: 'about' }) + '">' + t('about') + '</a><a href="' + u({ name: 'partners' }) + '">' + t('partners') + '</a>' +
        '<a href="' + TG + '" target="_blank" rel="noopener">Telegram</a><a href="' + IG + '" target="_blank" rel="noopener">Instagram</a></nav>' +
        '<small>© AH Magazine, 2025–' + YEAR + '</small></footer>';
    }
    function menu(r) {
      var other = L === 'ru' ? 'en' : 'ru';
      var target = !r || r.name === '404' ? { name: 'home' } : r;
      return '<div class="wrap"><div class="ov-top"><a class="logo" href="' + u({ name: 'home' }) + '" aria-label="AH Magazine">' + MARK + '</a>' +
        '<button class="ib" data-act="close" aria-label="' + t('close') + '">' + ICON_X + '</button></div>' +
        '<nav class="menu-l" aria-label="' + t('menu') + '"><a href="' + u({ name: 'archive' }) + '">' + t('archive') + '</a><a href="' + u({ name: 'notes' }) + '">' + t('notes') + '</a><a href="' + u({ name: 'index' }) + '">' + t('index') + '</a><a href="' + u({ name: 'about' }) + '">' + t('about') + '</a><a href="' + u({ name: 'partners' }) + '">' + t('partners') + '</a></nav>' +
        '<div class="menu-s">' + CATS.map(function (c) { return '<a href="' + ua({ cat: c }) + '">' + catLabel(c) + '</a>'; }).join('') + '</div>' +
        '<div class="menu-s" style="border-top:0;padding-top:0">' +
        '<a href="' + urlOf(target, 'ru') + '" data-act="lang" data-v="ru"' + (L === 'ru' ? ' aria-current="true"' : '') + ' hreflang="ru" lang="ru">Русский</a>' +
        '<a href="' + urlOf(target, 'en') + '" data-act="lang" data-v="en"' + (L === 'en' ? ' aria-current="true"' : '') + ' hreflang="en" lang="en">English</a>' +
        '<a href="' + TG + '" target="_blank" rel="noopener">Telegram</a><a href="' + IG + '" target="_blank" rel="noopener">Instagram</a></div></div>';
    }

    // ---------- views ----------
    function vHome() {
      var hero = OBJ[0], latest = OBJ.slice(1, 9);
      var h = '<div class="frame" style="padding-top:32px"><section class="split">' +
        '<a class="split-im" href="' + uo(hero.id) + '">' + heroImg(hero, tr(hero.t)) + '</a>' +
        '<div class="split-pn"><span class="k">' + t('newest') + ' · ' + catLabel(hero.cats[0]) + '</span>' +
        '<h1><a href="' + uo(hero.id) + '">' + nowrapHy(esc(headTitle(hero))) + '</a></h1>' +
        '<span class="by">' + nowrapHy(metaParts(hero, false).join(' · ')) + '</span>' +
        (hero.s ? '<p>' + nowrapHy(esc(tr(hero.s))) + '</p>' : '') +
        '<a class="btn" href="' + uo(hero.id) + '" style="margin-top:10px">' + t('view') + arrow() + '</a></div></section>';

      h += '<section class="sec"><h2 class="lbl">' + t('latest') + '</h2>' + feed(latest) +
        '<div class="center more"><a class="btn" href="' + ua({}) + '">' + t('allArchive') + ' · ' + count(OBJ.length) + arrow() + '</a></div></section>';

      // each rubric shows its newest post that is not already on the page (hero, the feed, an earlier rubric)
      var used = {};
      [hero].concat(latest).forEach(function (o) { used[o.id] = 1; });
      h += '<section class="sec"><h2 class="lbl">' + t('rubrics') + '</h2><div class="rub">' + CATS.map(function (c) {
        var items = OBJ.filter(function (o) { return o.cats.indexOf(c) >= 0; });
        var pick = items.filter(function (o) { return !used[o.id]; })[0] || items[0];
        if (!pick) return '';
        used[pick.id] = 1;
        return '<a href="' + ua({ cat: c }) + '">' + cover(pick, '') + '<span class="t">' + catLabel(c) + '<span class="n">' + items.length + '</span></span></a>';
      }).join('') + '</div></section>';

      h += '<section class="sec"><h2 class="lbl">Notes</h2><div class="notes">' + NOTES.map(noteCard).join('') + '</div></section>';

      var top = Object.keys(PEOPLE).map(function (k) { return [k, PEOPLE[k]]; })
        .sort(function (a, b) { return b[1].objs.length - a[1].objs.length || tr(a[1]).localeCompare(tr(b[1]), L); }).slice(0, 22)
        .sort(function (a, b) { return tr(a[1]).localeCompare(tr(b[1]), L); });
      h += '<section class="sec"><h2 class="lbl">' + t('index') + '</h2><p class="names">' + top.map(function (e) {
        return '<a href="' + up(e[0]) + '">' + esc(tr(e[1])) + '</a>';
      }).join('<span class="dot">·</span><wbr>') + '</p><div class="center" style="padding-top:44px"><a class="btn" href="' + u({ name: 'index' }) + '">' + t('allIndex') + arrow() + '</a></div></section></div>';

      h += '<section class="band"><div class="frame band-in"><span class="k">AH Magazine</span><h2>' + t('bandT') + '</h2><p>' + t('bandP') + '</p>' +
        '<div class="row"><a class="btn" href="' + TG + '" target="_blank" rel="noopener">Telegram' + arrow() + '</a><a class="btn" href="' + IG + '" target="_blank" rel="noopener">Instagram' + arrow() + '</a></div></div></section>';
      return h;
    }
    // A feed of posts (home, related, a person's page). Each post keeps its own proportions: --r is
    // the cover's width to height, which layoutFeeds() uses to set the rows on wide screens and CSS
    // uses for the ribbon width on phones.
    function feedItem(o) {
      var r = (o.img.cw / o.img.ch).toFixed(4);
      return '<a class="fi" href="' + uo(o.id) + '" data-r="' + r + '" style="--r:' + r + '">' + cover(o, '') +
        '<span class="card-tx"><span class="k">' + catLabel(o.cats[0]) + '</span><span class="t">' + esc(byline(o) ? headTitle(o) : tr(o.t)) + '</span>' +
        (byline(o) ? '<span class="a">' + esc(byline(o)) + '</span>' : '') + '</span></a>';
    }
    function feed(list) {
      return '<div class="feed"><div class="feed-in">' + list.map(feedItem).join('') + '</div>' +
        '<div class="feed-bar" aria-hidden="true"><span></span></div></div>';
    }
    function noteCard(n) {
      return '<a class="note-c" href="' + un(n.id) + '">' + '<img src="/img/c/' + ik(n) + '.jpg" width="' + n.img.cw + '" height="' + n.img.ch + '" alt="" loading="lazy" decoding="async">' +
        '<span class="k">' + fdate(n.d) + '</span><span class="t">' + esc(tr(n.t)) + '</span></a>';
    }

    var lim = { key: '', n: 0 };
    var filtersOpen = false;
    function filtered(st) {
      var a = OBJ.filter(function (o) {
        return (st.cat === 'all' || o.cats.indexOf(st.cat) >= 0) && (!st.co || o.co.indexOf(st.co) >= 0) && (!st.per || o.per === st.per);
      });
      if (st.sort === 'old') a = a.slice().reverse();
      if (st.sort === 'year') a = a.slice().sort(function (x, y) {
        var xs = x.y && x.y.s != null ? x.y.s : 1e9, ys = y.y && y.y.s != null ? y.y.s : 1e9;
        return xs - ys || y.id - x.id;
      });
      return a;
    }
    function vArchive(st) {
      var key = ua(withSt(st, { view: 'grid' }));
      var step = st.view === 'list' ? 60 : 120;
      if (lim.key !== key + st.view) { lim.key = key + st.view; lim.n = step; }
      var items = filtered(st);
      var title = t('archive'), sub = t('archiveSub');
      if (st.co) { title = tr(COUNTRIES[st.co]); sub = t('archive'); }
      else if (st.per) { title = perLabel(st.per); sub = t('archive'); }
      else if (st.cat !== 'all') { title = catLabel(st.cat); sub = t('archive'); }
      var h = '<div class="wrap"><section class="ph-head"><h1>' + esc(title) + '</h1><p>' + count(items.length) + ' · ' + esc(sub) + '</p></section>';

      h += '<div class="ctl"><div class="chips" role="group" aria-label="' + t('rubrics') + '">' +
        ['all'].concat(CATS).map(function (c) {
          return '<button class="chip" data-act="go" data-h="' + ua(withSt(st, { cat: c })) + '" aria-pressed="' + (st.cat === c) + '">' + (c === 'all' ? t('all') : t('catShort')[c]) + '</button>';
        }).join('') + '</div>' +
        '<div class="ctl-r"><button data-act="filters" aria-expanded="' + filtersOpen + '" aria-controls="fp">' + t('filters') + ICON_FILTER + '</button>' +
        '<span style="display:inline-flex;gap:14px"><button data-act="go" data-h="' + ua(withSt(st, { view: 'grid' })) + '" aria-pressed="' + (st.view === 'grid') + '">' + t('grid') + '</button>' +
        '<button data-act="go" data-h="' + ua(withSt(st, { view: 'list' })) + '" aria-pressed="' + (st.view === 'list') + '">' + t('list') + '</button></span></div></div>';

      if (filtersOpen) {
        var cos = Object.keys(COUNTRIES).sort(function (a, b) { return tr(COUNTRIES[a]).localeCompare(tr(COUNTRIES[b]), L); });
        h += '<div class="fp" id="fp"><div><h3>' + t('country') + '</h3><div class="fp-co">' + cos.map(function (c) {
          return '<button class="fo" data-act="go" data-h="' + ua(withSt(st, { co: st.co === c ? null : c })) + '" aria-pressed="' + (st.co === c) + '"><span>' + esc(tr(COUNTRIES[c])) + '</span><span class="n">' + COUNTRIES[c].n + '</span></button>';
        }).join('') + '</div></div>' +
          '<div><h3>' + t('period') + '</h3><div class="fp-list">' + PER_ORDER.filter(function (p) { return PERIODS[p]; }).map(function (p) {
            return '<button class="fo" data-act="go" data-h="' + ua(withSt(st, { per: st.per === p ? null : p })) + '" aria-pressed="' + (st.per === p) + '"><span>' + perLabel(p) + '</span><span class="n">' + PERIODS[p] + '</span></button>';
          }).join('') + '</div></div>' +
          '<div><h3>' + t('order') + '</h3><div class="fp-list">' + [['new', t('sNew')], ['old', t('sOld')], ['year', t('sYear')]].map(function (s) {
            return '<button class="fo" data-act="go" data-h="' + ua(withSt(st, { sort: s[0] })) + '" aria-pressed="' + (st.sort === s[0]) + '"><span>' + s[1] + '</span></button>';
          }).join('') + '</div></div></div>';
      }
      var act = [];
      if (st.co) act.push('<button class="pill" data-act="go" data-h="' + ua(withSt(st, { co: null })) + '">' + esc(tr(COUNTRIES[st.co])) + ICON_SMALLX + '</button>');
      if (st.per) act.push('<button class="pill" data-act="go" data-h="' + ua(withSt(st, { per: null })) + '">' + perLabel(st.per) + ICON_SMALLX + '</button>');
      if (st.sort !== 'new') act.push('<button class="pill" data-act="go" data-h="' + ua(withSt(st, { sort: 'new' })) + '">' + (st.sort === 'old' ? t('sOld') : t('sYear')) + ICON_SMALLX + '</button>');
      if (act.length) h += '<div class="act">' + act.join('') + '<button class="clr" data-act="go" data-h="' + ua({ view: st.view }) + '">' + t('clear') + '</button></div>';

      if (!items.length) return h + '<p class="empty">' + t('nothing') + '</p></div>';
      var shown = items.slice(0, lim.n);
      if (st.view === 'list') {
        h += '<div class="lst" style="margin-top:24px">' + shown.map(function (o) {
          var mm = [byline(o), o.y ? tr(o.y) : ''].filter(Boolean).join(' · ');
          return '<a class="lr" href="' + uo(o.id) + '"><span class="th">' + thumb(o, 64) + '</span>' +
            '<span style="min-width:0"><span class="t">' + esc(tr(o.t)) + '</span><span class="mm">' + esc(mm) + '</span></span>' +
            '<span class="c">' + esc(o.p.map(function (x) { return personName(x.id); }).join(', ')) + '</span>' +
            '<span class="c mu">' + esc(tr(o.pl)) + '</span><span class="yr">' + esc(o.y ? tr(o.y) : '') + '</span>' +
            '<span class="k">' + catLabel(o.cats[0]) + '</span></a>';
        }).join('') + '</div>';
      } else {
        h += '<div class="ro" id="ro" aria-live="polite"><span class="mu">' + count(items.length) + ' ' + t('inSel') + '<span class="hint"> · ' + t('hint') + '</span></span></div>';
        h += '<div class="ag" id="ag">' + shown.map(function (o) {
          return '<a class="tile" href="' + uo(o.id) + '" data-id="' + o.id + '" aria-label="' + esc(tr(o.t)) + '">' + thumb(o, 240) + '</a>';
        }).join('') + '</div>';
      }
      if (items.length > shown.length) h += '<div class="center more"><button class="btn" data-act="more">' + t('more') + ' · ' + (items.length - shown.length) + '</button></div>';
      return h + '</div>';
    }

    function related(o) {
      var scored = OBJ.filter(function (x) { return x.id !== o.id && !x.tmp; }).map(function (x) {
        var s = 0;
        x.p.forEach(function (pp) { if (o.p.some(function (q) { return q.id === pp.id; })) s += 6; });
        x.co.forEach(function (c) { if (o.co.indexOf(c) >= 0) s += 2; });
        if (x.cats[0] === o.cats[0]) s += 1.5;
        x.cats.forEach(function (c) { if (o.cats.indexOf(c) >= 0) s += 0.5; });
        return [x, s - Math.abs(x.id - o.id) / 100000];
      }).sort(function (a, b) { return b[1] - a[1]; });
      return scored.slice(0, 4).map(function (e) { return e[0]; });
    }
    // One photo of an entry. New entries: every photo is a real <img> in two sizes. Older entries: the very first
    // photo is the cover file itself (a real <img>, it arrives sooner than the strip, and search engines and
    // previews can see it), the rest are slices of the strip.
    function photo(key, img, i, alt, tgId, asCover) {
      var g = img.segs[i];
      var vid = g[3] && tgId ? '<a class="vid" href="' + TG + '/' + tgId + '" target="_blank" rel="noopener">' + ICON_PLAY + t('video') + '</a>' : '';
      if (hi(img)) {
        return '<div class="ph" style="aspect-ratio:' + g[1] + '/' + g[2] + '">' + picture(key, img, i, alt + ', ' + (i + 1), asCover, SZ_OBJ) + vid + '</div>';
      }
      if (asCover) {
        return '<div class="ph" style="aspect-ratio:' + g[1] + '/' + g[2] + '"><img src="/img/c/' + key + '.jpg" width="' + g[1] + '" height="' + g[2] + '" alt="' + esc(alt + ', 1') + '" fetchpriority="high" decoding="async">' + vid + '</div>';
      }
      return '<div class="ph" role="img" aria-label="' + esc(alt + ', ' + (i + 1)) + '" style="' + segStyle(img, i, key) + '">' + vid + '</div>';
    }
    function vObject(id) {
      var o = BY[id], next = OBJ[o.ix + 1], prev = OBJ[o.ix - 1];
      var title = tr(o.t), key = ik(o), tgId = o.tmp ? null : o.id;
      var h = '<div class="wrap"><nav class="crumb" aria-label="' + t('crumbs') + '"><a href="' + ua({}) + '">' + t('archive') + '</a><span>/</span><a href="' + ua({ cat: o.cats[0] }) + '">' + catLabel(o.cats[0]) + '</a></nav>';
      h += '<div class="obj"><div class="lead-ph">' + photo(key, o.img, 0, title, tgId, true) + '</div>';
      h += '<aside class="obj-tx" id="otx"><span class="k">' + o.cats.map(catLabel).join(' · ') + '</span><h1>' + esc(title) + '</h1>';
      var mp = metaParts(o, true);
      if (mp.length) h += '<p class="by">' + mp.join(' · ') + '</p>';
      var body = o.b[L] && o.b[L].length ? o.b[L] : o.b.ru;
      if (body.length) h += '<div class="body">' + body.map(function (p) { return '<p>' + esc(p) + '</p>'; }).join('') + '</div>';
      if (o.cr.length) {
        h += '<dl class="cr">' + o.cr.map(function (c) {
          var v = c[2] ? '<a href="' + esc(c[2]) + '" target="_blank" rel="noopener">' + esc(creditName(c)) + '</a>' : esc(creditName(c));
          return '<dt>' + (t('cr')[c[0]] || t('cr').other) + '</dt><dd>' + v + '</dd>';
        }).join('') + '</dl>';
      }
      h += '<div class="obj-meta"><span>' + (o.tmp ? '' : 'AH-' + o.id + ' · ') + t('since') + ' ' + fdate(o.d) + '</span>' +
        (o.co.length ? '<span>' + o.co.map(function (c) { return '<a href="' + ua({ co: c }) + '" style="border-bottom:1px solid var(--rule)">' + esc(tr(COUNTRIES[c])) + '</a>'; }).join(', ') + (o.per ? ' · <a href="' + ua({ per: o.per }) + '" style="border-bottom:1px solid var(--rule)">' + perLabel(o.per) + '</a>' : '') + '</span>' : '') + '</div>';
      if (tgId) h += '<div class="obj-links"><a href="' + TG + '/' + tgId + '" target="_blank" rel="noopener">' + t('tgPost') + ' ↗</a></div>';
      h += '</aside>';
      var rest = '';
      for (var i = 0; i < o.img.segs.length; i++) rest += photo(key, o.img, i, title, tgId, i === 0);
      h += '<div class="obj-ph">' + rest + '</div></div>';
      h += '<section class="sec" style="max-width:1240px;margin:0 auto"><h2 class="lbl">' + t('related') + '</h2>' + feed(related(o));
      if (!o.tmp) {
        h += '<nav class="pn" aria-label="' + t('prev') + ' / ' + t('next') + '">' + (prev && !prev.tmp ? '<a href="' + uo(prev.id) + '"><span class="mu">← ' + t('prev') + '</span><span class="t">' + esc(tr(prev.t)) + '</span></a>' : '<span></span>') +
          (next ? '<a class="nx" href="' + uo(next.id) + '"><span class="mu">' + t('next') + ' →</span><span class="t">' + esc(tr(next.t)) + '</span></a>' : '') + '</nav>';
      }
      return h + '</section></div>';
    }

    function vNotes() {
      return '<div class="frame"><section class="ph-head"><h1>Notes</h1><p>' + t('notesSub') + '</p></section><div class="notes">' + NOTES.filter(function (n) { return !n.tmp; }).map(noteCard).join('') + '</div></div>';
    }
    function vNote(id) {
      var n = NBY[id], title = tr(n.t), key = ik(n);
      var h = '<article><div class="frame" style="padding-top:32px"><section class="split"><div class="split-im">' + heroImg(n, title) + '</div>' +
        '<div class="split-pn"><span class="k">Notes · ' + n.cats.map(catLabel).join(' · ') + '</span><h1>' + esc(title) + '</h1>' +
        (n.sub ? '<p>' + esc(tr(n.sub)) + '</p>' : '') + '<span class="by">' + fdate(n.d) + '</span></div></section></div>';
      // the cover is the first photo of the album; the remaining photos go into the slots
      var slots = {}, ph = 1;
      n.slots.forEach(function (s) { if (ph < n.img.segs.length) { slots[s] = ph; ph += 1; } });
      var cols = [], cur = [], first = true;
      function flush() { if (cur.length) { cols.push('<div class="col' + (first ? ' first' : '') + '">' + cur.join('') + '</div>'); first = false; cur = []; } }
      n.bl.forEach(function (b, i) {
        var txt = esc(tr(b));
        if (b.type === 'p') cur.push('<p>' + txt + '</p>');
        else if (b.type === 'h') cur.push('<div class="h">' + (b.kicker ? '<span class="kick">' + esc(b.kicker) + '</span>' : '') + '<h2>' + txt + '</h2></div>');
        else if (b.type === 'example') cur.push('<p class="ex">' + txt + '</p>');
        else if (b.type === 'quote') { flush(); cols.push('<blockquote>' + txt + (b.by ? '<cite>' + esc(tr(b.by)) + '</cite>' : '') + '</blockquote>'); }
        if (slots[i] != null) {
          // a note's photos are separate files (img/f/…): Instant View and search engines need real images
          var g = n.img.segs[slots[i]], alt = title + ', ' + (slots[i] + 1);
          flush(); cols.push('<figure>' + (hi(n.img) ? picture(key, n.img, slots[i], alt, false, SZ_FIG)
            : '<img src="/img/f/' + key + '-' + slots[i] + '.jpg" width="' + g[1] + '" height="' + g[2] + '" alt="' + esc(alt) + '" loading="lazy" decoding="async">') + '</figure>');
        }
      });
      flush();
      h += '<div class="frame art">' + cols.join('') + '<div class="col"><p class="tags">#ahmagnotes</p></div></div></article>';
      var mentioned = n.ppl.filter(function (p) { return PEOPLE[p.id]; });
      if (mentioned.length) h += '<div class="frame sec" style="padding-top:96px"><h2 class="lbl">' + t('index') + '</h2><p class="names" style="font-size:24px">' + mentioned.map(function (p) { return '<a href="' + up(p.id) + '">' + esc(tr(PEOPLE[p.id])) + '</a>'; }).join('<span class="dot">·</span><wbr>') + '</p></div>';
      var others = NOTES.filter(function (x) { return x.id !== n.id && !x.tmp; }).slice(0, 2);
      if (others.length) h += '<section class="frame sec"><h2 class="lbl">' + t('readNext') + '</h2><div class="notes">' + others.map(noteCard).join('') + '</div></section>';
      return h;
    }

    function sortKey(s) { return s.replace(/^[«"“„'(\[]+/, ''); }
    function letterId(l) { return 'ix-' + encodeURIComponent(l).replace(/%/g, ''); }
    function vIndex(tab) {
      var h = '<div class="frame"><section class="ph-head"><h1>' + t('index') + '</h1><p>' + t('indexSub') + '</p></section>';
      h += '<nav class="tabs" aria-label="' + t('index') + '"><a href="' + u({ name: 'index' }) + '"' + (tab === 'names' ? ' aria-current="page"' : '') + '>' + t('names') + '</a><a href="' + u({ name: 'index', tab: 'countries' }) + '"' + (tab === 'countries' ? ' aria-current="page"' : '') + '>' + t('countries') + '</a><a href="' + u({ name: 'index', tab: 'time' }) + '"' + (tab === 'time' ? ' aria-current="page"' : '') + '>' + t('time') + '</a></nav>';
      if (tab === 'names') {
        var groups = {};
        Object.keys(PEOPLE).forEach(function (k) {
          var nm = sortKey(tr(PEOPLE[k])), letter = nm.charAt(0).toUpperCase();
          if (!/[A-ZА-ЯЁ]/.test(letter)) letter = '#';
          (groups[letter] = groups[letter] || []).push([k, PEOPLE[k]]);
        });
        var letters = Object.keys(groups).sort(function (a, b) {
          var ca = /[А-ЯЁ]/.test(a), cb = /[А-ЯЁ]/.test(b);
          if (L === 'ru' && ca !== cb) return ca ? -1 : 1;
          if (a === '#') return 1; if (b === '#') return -1;
          return a.localeCompare(b, L);
        });
        h += '<div class="ix-letters">' + letters.map(function (l) { return '<a href="#' + letterId(l) + '" data-act="jump" data-v="' + letterId(l) + '">' + l + '</a>'; }).join('') + '</div>';
        h += '<div class="ix">' + letters.map(function (l) {
          return '<div class="ix-g" id="' + letterId(l) + '"><div class="ix-l">' + l + '</div>' + groups[l].sort(function (a, b) { return sortKey(tr(a[1])).localeCompare(sortKey(tr(b[1])), L); }).map(function (e) {
            return '<a href="' + up(e[0]) + '"><span>' + esc(tr(e[1])) + '</span><span class="n">' + e[1].objs.length + '</span></a>';
          }).join('') + '</div>';
        }).join('') + '</div>';
      } else if (tab === 'countries') {
        var cs = Object.keys(COUNTRIES).sort(function (a, b) { return tr(COUNTRIES[a]).localeCompare(tr(COUNTRIES[b]), L); });
        h += '<div class="ix">' + cs.map(function (c) {
          return '<a href="' + ua({ co: c }) + '"><span>' + esc(tr(COUNTRIES[c])) + '</span><span class="n">' + COUNTRIES[c].n + '</span></a>';
        }).join('') + '</div>';
      } else {
        h += '<div class="per">' + PER_ORDER.filter(function (p) { return PERIODS[p]; }).map(function (p) {
          return '<a href="' + ua({ per: p }) + '"><span class="t">' + perLabel(p) + '</span><span class="n">' + count(PERIODS[p]) + '</span></a>';
        }).join('') + '</div>';
      }
      return h + '</div>';
    }

    function vPerson(id) {
      var p = PEOPLE[id], items = p.objs.map(function (x) { return BY[x]; }).filter(function (o) { return o && !o.tmp; });
      var roles = p.roles.map(function (r) { return t('role')[r]; }).filter(Boolean).join(', ');
      var sub = [roles, p.life || '', count(items.length)].filter(Boolean).join(' · ');
      var h = '<div class="frame"><section class="ph-head"><span class="k">' + t('person') + '</span><h1>' + esc(tr(p)) + '</h1><p>' + esc(sub) + '</p></section>';
      h += feed(items);
      var inNotes = NOTES.filter(function (n) { return !n.tmp && n.ppl.some(function (q) { return q.id === id; }); });
      if (inNotes.length) h += '<section class="sec"><h2 class="lbl">' + t('mentioned') + '</h2><div class="notes">' + inNotes.map(noteCard).join('') + '</div></section>';
      return h + '<div class="center more"><a class="btn" href="' + u({ name: 'index' }) + '">' + t('index') + arrow() + '</a></div></div>';
    }

    function contactBlock(text) {
      return '<div class="contact"><span>' + text + '</span><a class="handle" href="' + CONTACT_URL + '" target="_blank" rel="noopener">' + CONTACT + '</a><button class="copy" data-act="copy" data-v="' + CONTACT + '">' + t('copy') + '</button></div>';
    }
    function vAbout() {
      var c = COPY.about[L];
      var people = Object.keys(PEOPLE).length, countries = Object.keys(COUNTRIES).length, n = OBJ.filter(function (o) { return !o.tmp; }).length;
      return '<div class="frame"><section class="ph-head"><h1>' + c.title + '</h1></section><div class="prose">' +
        '<p class="lead">' + c.lead + '</p>' + c.p.map(function (x) { return '<p>' + x + '</p>'; }).join('') +
        '<div class="stats"><div><b>' + n + '</b><span>' + plural(n, t('objForms')) + ' ' + c.stats[0] + '</span></div><div><b>' + countries + '</b><span>' + c.stats[1] + '</span></div><div><b>' + people + '</b><span>' + c.stats[2] + '</span></div></div>' +
        '<h2>' + c.contactH + '</h2>' + contactBlock(c.contact) +
        '<div class="obj-links" style="padding-top:8px"><a href="' + TG + '" target="_blank" rel="noopener">Telegram ↗</a><a href="' + IG + '" target="_blank" rel="noopener">Instagram ↗</a></div></div></div>';
    }
    function vPartners() {
      var c = COPY.partners[L];
      return '<div class="frame"><section class="ph-head"><h1>' + c.title + '</h1></section><div class="prose">' +
        '<p class="lead">' + c.lead + '</p><h2>' + c.fmtH + '</h2><div class="fmt">' + c.fmt.map(function (f) { return '<div><b>' + f[0] + '</b><span>' + f[1] + '</span></div>'; }).join('') + '</div>' +
        '<h2>' + c.howH + '</h2><p>' + c.how + '</p>' + (c.aud ? '<h2>' + c.audH + '</h2><p>' + c.aud + '</p>' : '') +
        '<h2>' + c.contactH + '</h2>' + contactBlock(c.contact) + '</div></div>';
    }
    function vNotFound() {
      return '<div class="frame nf"><h1>' + t('nfT') + '</h1><p>' + t('nfP') + '</p><div class="row" style="display:flex;flex-wrap:wrap;gap:12px;justify-content:center"><a class="btn" href="' + u({ name: 'home' }) + '">' + t('home') + arrow() + '</a><a class="btn" href="' + ua({}) + '">' + t('archive') + arrow() + '</a></div></div>';
    }

    // ---------- search ----------
    function norm(s) { return String(s || '').toLowerCase().replace(/ё/g, 'е').normalize('NFKD').replace(/[̀-ͯ]/g, ''); }
    var SIDX = null;   // built on the first search, not on every page load
    function sidx() {
      if (SIDX) return SIDX;
      SIDX = OBJ.filter(function (o) { return !o.tmp; }).map(function (o) {
        var bits = [o.t.ru, o.t.en, o.pl && o.pl.ru, o.pl && o.pl.en, o.y && o.y.ru, o.y && o.y.en, 'AH-' + o.id];
        o.p.forEach(function (x) { var p = PEOPLE[x.id]; if (p) bits.push(p.ru, p.en); });
        o.co.forEach(function (c) { if (COUNTRIES[c]) bits.push(COUNTRIES[c].ru, COUNTRIES[c].en); });
        o.cats.forEach(function (c) { bits.push(T.ru.cat[c], T.en.cat[c]); });
        o.cr.forEach(function (c) { bits.push(c[1], c[3]); });
        return { o: o, s: norm(bits.join(' ')), b: norm((o.b.ru || []).join(' ') + ' ' + (o.b.en || []).join(' ')) };
      });
      return SIDX;
    }
    function runSearch(q) {
      var toks = norm(q).split(/\s+/).filter(Boolean);
      if (!toks.length) return null;
      var hit = function (s) { return toks.every(function (tk) { return s.indexOf(tk) >= 0; }); };
      var ix = sidx();
      var main = ix.filter(function (x) { return hit(x.s); }).map(function (x) { return x.o; });
      var extra = ix.filter(function (x) { return !hit(x.s) && hit(x.s + ' ' + x.b); }).map(function (x) { return x.o; });
      var ppl = Object.keys(PEOPLE).filter(function (k) { return hit(norm(PEOPLE[k].ru + ' ' + PEOPLE[k].en)); }).slice(0, 14);
      var nts = NOTES.filter(function (n) { return !n.tmp && hit(norm(n.t.ru + ' ' + n.t.en + ' ' + n.bl.map(function (b) { return b.ru + ' ' + b.en; }).join(' '))); });
      return { objs: main.concat(extra).slice(0, 40), ppl: ppl, nts: nts };
    }
    function searchShell() {
      return '<div class="frame"><div class="ov-top"><a class="logo" href="' + u({ name: 'home' }) + '" aria-label="AH Magazine">' + MARK + '</a>' +
        '<button class="ib" data-act="close" aria-label="' + t('close') + '">' + ICON_X + '</button></div>' +
        '<label class="sr" for="so-input" id="so-label">' + t('search') + '</label>' +
        '<div class="so-q">' + ICON_SEARCH + '<input id="so-input" type="search" autocomplete="off" spellcheck="false" placeholder="' + t('sPh') + '"></div>' +
        '<div class="so-r" id="so-r"></div></div>';
    }
    function searchResults(q) {
      if (!OBJ.length) return '<p class="empty" style="padding:40px 0">' + t('sWait') + '</p>';
      var r = runSearch(q);
      if (!r) {
        var sugg = Object.keys(PEOPLE).sort(function (a, b) { return PEOPLE[b].objs.length - PEOPLE[a].objs.length; }).slice(0, 4).map(function (k) { return tr(PEOPLE[k]); });
        var cs = Object.keys(COUNTRIES).sort(function (a, b) { return COUNTRIES[b].n - COUNTRIES[a].n; }).slice(0, 4).map(function (c) { return tr(COUNTRIES[c]); });
        return '<div><h2 class="so-h">' + t('sTry') + '</h2><div class="so-tags">' + sugg.concat(cs).map(function (s) {
          return '<button data-act="fill" data-v="' + esc(s) + '">' + esc(s) + '</button>';
        }).join('') + '</div></div>';
      }
      var h = '';
      if (r.ppl.length) h += '<div><h2 class="so-h">' + t('sPeople') + '</h2><div class="so-tags">' + r.ppl.map(function (k) { return '<a href="' + up(k) + '">' + esc(tr(PEOPLE[k])) + '</a>'; }).join('') + '</div></div>';
      if (r.nts.length) h += '<div><h2 class="so-h">' + t('sNotes') + '</h2><div class="lst">' + r.nts.map(function (n) {
        return '<a class="lr" href="' + un(n.id) + '"><span class="th" style="background-image:url(/img/c/' + ik(n) + '.jpg);background-size:cover;background-position:center"></span><span style="min-width:0"><span class="t">' + esc(tr(n.t)) + '</span><span class="mm">' + fdate(n.d) + '</span></span><span class="c"></span><span class="c"></span><span class="yr"></span><span class="k">Notes</span></a>';
      }).join('') + '</div></div>';
      if (r.objs.length) h += '<div><h2 class="so-h">' + t('sObjs') + ' · ' + r.objs.length + '</h2><div class="lst">' + r.objs.map(function (o) {
        var mm = [byline(o), o.y ? tr(o.y) : ''].filter(Boolean).join(' · ');
        return '<a class="lr" href="' + uo(o.id) + '"><span class="th">' + thumb(o, 64) + '</span><span style="min-width:0"><span class="t">' + esc(tr(o.t)) + '</span><span class="mm">' + esc(mm) + '</span></span>' +
          '<span class="c">' + esc(o.p.map(function (x) { return personName(x.id); }).join(', ')) + '</span><span class="c mu">' + esc(tr(o.pl)) + '</span><span class="yr">' + esc(o.y ? tr(o.y) : '') + '</span><span class="k">' + catLabel(o.cats[0]) + '</span></a>';
      }).join('') + '</div></div>';
      if (!h) h = '<p class="empty" style="padding:40px 0">' + t('sNone') + ' «' + esc(q) + '»</p>';
      return h;
    }

    // ---------- a page: what goes into #app, plus the facts the <head> needs ----------
    function known(r) {
      if (r.name === 'object') return !!BY[r.id];
      if (r.name === 'note') return !!NBY[r.id];
      if (r.name === 'person') return !!PEOPLE[r.id];
      if (r.name === 'home') return OBJ.length > 0;
      return true;
    }
    function view(r) {
      var html, title = '';
      if (!known(r)) r = { name: '404', lang: r.lang };
      if (r.name === 'home') html = vHome();
      else if (r.name === 'archive') { html = vArchive(cleanSt(r.st || {})); title = t('archive'); }
      else if (r.name === 'object') { html = vObject(r.id); title = tr(BY[r.id].t); }
      else if (r.name === 'notes') { html = vNotes(); title = 'Notes'; }
      else if (r.name === 'note') { html = vNote(r.id); title = tr(NBY[r.id].t); }
      else if (r.name === 'index') { html = vIndex(r.tab); title = t('index'); }
      else if (r.name === 'person') { html = vPerson(r.id); title = tr(PEOPLE[r.id]); }
      else if (r.name === 'about') { html = vAbout(); title = t('about'); }
      else if (r.name === 'partners') { html = vPartners(); title = t('partners'); }
      else { html = vNotFound(); title = t('nfT'); }
      return { html: header(r) + '<main id="main">' + html + '</main>' + footer(), title: title ? title + ' — AH Magazine' : 'AH Magazine' };
    }

    return {
      D: D, BY: BY, NBY: NBY,
      setLang: function (l) { L = l === 'en' ? 'en' : 'ru'; },
      lang: function () { return L; },
      parse: parse, url: function (r) { return urlOf(r, L); }, view: view, menu: menu,
      searchShell: searchShell, searchResults: searchResults,
      filtered: filtered, byline: byline, count: count, tr: tr, t: t,
      lim: lim, toggleFilters: function () { filtersOpen = !filtersOpen; },
      catLabel: catLabel, perLabel: perLabel, fdate: fdate
    };
  }

  var api = { createSite: createSite, urlOf: urlOf, legacyRoute: legacyRoute, langOfPath: langOfPath, T: T, CATS: CATS, PER_ORDER: PER_ORDER, esc: esc };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AH = api;
  if (typeof document === 'undefined') return;

  // =====================================================================================================
  // The browser: take over the page that is already rendered, then render later pages on the spot.
  // =====================================================================================================
  var app = document.getElementById('app');
  if (!app) return;
  var script = document.currentScript || document.querySelector('script[data-data]');
  var DATA_URL = script && script.getAttribute('data-data');
  var L = langOfPath(location.pathname);
  var site = createSite(null);
  site.setLang(L);
  var full = null;          // the site over the whole data set, once the data file has arrived
  var loading = null;

  try { localStorage.setItem('ah-lang', L); } catch (e) {}

  function loadData() {
    if (full) return Promise.resolve(full);
    if (loading) return loading;
    if (!DATA_URL || !window.fetch) return Promise.reject(new Error('no data'));
    loading = fetch(DATA_URL, { credentials: 'same-origin', cache: 'no-cache' }).then(function (res) {
      if (!res.ok) throw new Error('data ' + res.status);
      return res.text();
    }).then(function (txt) {
      full = createSite(JSON.parse(txt));
      full.setLang(L);
      site = full;
      return full;
    });
    loading.catch(function () { loading = null; });
    return loading;
  }

  function here() { return location.pathname + location.search; }
  function render(forced) {
    var r = forced || site.parse(location.pathname, location.search);
    L = r.lang; site.setLang(L);
    var v = site.view(r);
    app.innerHTML = v.html;
    document.documentElement.lang = L;
    try { document.title = v.title; } catch (e) {}
    after();
  }
  function after() {
    stickyCheck();
    layoutFeeds();
    drawLogo();
    document.documentElement.classList.remove('ah-wait');
  }

  // ---------- navigation ----------
  var mem = {}, lastUrl = here();
  // With the data at hand the next page is drawn on the spot. Without it (the first second after
  // opening, offline, an old browser) the link simply loads the ready page from the server.
  function navigate(url, replace, keepScroll) {
    var q = url.indexOf('?');
    if (!full || !history.pushState || site.parse(q < 0 ? url : url.slice(0, q), q < 0 ? '' : url.slice(q)).name === '404') {
      if (replace) location.replace(url); else location.href = url;
      return;
    }
    var y = window.scrollY;
    try { history.scrollRestoration = 'manual'; } catch (e) {}
    mem[lastUrl] = y;
    if (replace) history.replaceState(null, '', url); else history.pushState(null, '', url);
    lastUrl = here();
    closeOverlays();
    render();
    window.scrollTo(0, keepScroll ? y : 0);
  }
  window.addEventListener('popstate', function () {
    mem[lastUrl] = window.scrollY;
    lastUrl = here();
    loadData().then(function () {
      closeOverlays();
      render();
      window.scrollTo(0, mem[lastUrl] || 0);
    }, function () { location.reload(); });
  });

  // ---------- feeds ----------
  // Wide screens: the posts of a feed are set in rows of one height that fill the line exactly.
  // The row breaks are chosen together (shortest-path over all break points) so that every row
  // stays close to the target height, the last one included; a row that would have to grow far
  // past the target keeps the target height and stops short instead (centred when it is the only
  // row). Phones: the same posts form one ribbon of equal height that scrolls sideways, with a
  // thin line under it showing the position.
  var MQ_PHONE = window.matchMedia ? window.matchMedia('(max-width: 620px)') : null;
  var MQ_NARROW = window.matchMedia ? window.matchMedia('(max-width: 1000px)') : null;
  function breakRows(rs, W, g, H) {
    var n = rs.length, cost = [0], from = [0], i, j, k, sum, h, d;
    for (j = 1; j <= n; j++) {
      cost[j] = Infinity;
      sum = 0;
      for (i = j - 1; i >= 0 && j - i <= 6; i--) {
        sum += rs[i];
        h = (W - g * (j - i - 1)) / sum;
        if (h < H * 0.55 && j - i > 1) break;
        d = h > H ? (h - H) * (h > H * 1.35 ? 4 : 1.3) : H - h;
        if (cost[i] + d * d < cost[j]) { cost[j] = cost[i] + d * d; from[j] = i; }
      }
    }
    var rows = [];
    for (j = n; j > 0; j = from[j]) {
      i = from[j];
      sum = 0;
      for (k = i; k < j; k++) sum += rs[k];
      var fit = (W - g * (j - i - 1)) / sum;   // the height at which this row fills the line exactly
      rows.unshift({ n: j - i, loose: fit > H * 1.35, fit: fit });
    }
    return rows;
  }
  function layoutFeeds() {
    var boxes = app.querySelectorAll('.feed-in'), phone = MQ_PHONE && MQ_PHONE.matches;
    for (var b = 0; b < boxes.length; b++) {
      var box = boxes[b], items = [].slice.call(box.querySelectorAll('.fi'));
      if (phone) {
        if (box.classList.contains('rows')) {
          items.forEach(function (a) { box.appendChild(a); });
          [].slice.call(box.querySelectorAll('.frow')).forEach(function (r) { box.removeChild(r); });
          box.classList.remove('rows');
          box.removeAttribute('data-w');
        }
        barSync(box);
        continue;
      }
      var W = box.clientWidth;
      if (!W || +box.getAttribute('data-w') === W) continue;
      var H = Math.max(170, Math.min(300, W * 0.21)), g = MQ_NARROW && MQ_NARROW.matches ? 20 : 32;
      var rows = breakRows(items.map(function (a) { return +a.getAttribute('data-r'); }), W, g, H);
      var frag = document.createDocumentFragment(), at = 0;
      for (var q = 0; q < rows.length; q++) {
        var div = document.createElement('div');
        div.className = 'frow' + (rows[q].loose ? ' loose' + (rows.length === 1 ? ' solo' : '') : '');
        // a lone row may be set a little taller than the rest, but never wider than the line
        if (rows[q].loose) div.style.setProperty('--rh', Math.min(rows.length === 1 ? H * 1.6 : H, rows[q].fit).toFixed(1) + 'px');
        for (var c = 0; c < rows[q].n; c++) div.appendChild(items[at++]);
        frag.appendChild(div);
      }
      box.textContent = '';
      box.appendChild(frag);
      box.classList.add('rows');
      box.setAttribute('data-w', W);
    }
  }
  function barSync(box) {
    var bar = box.nextElementSibling, s = bar && bar.firstElementChild;
    if (!s || !bar.classList.contains('feed-bar')) return;
    var sw = box.scrollWidth, cw = box.clientWidth;
    bar.style.visibility = sw > cw + 1 ? '' : 'hidden';
    s.style.width = (sw ? Math.min(100, cw / sw * 100) : 100).toFixed(2) + '%';
    s.style.left = (sw ? box.scrollLeft / sw * 100 : 0).toFixed(2) + '%';
  }
  var feedRaf = 0;
  window.addEventListener('resize', function () {
    if (feedRaf) return;
    feedRaf = window.requestAnimationFrame(function () { feedRaf = 0; layoutFeeds(); });
  });
  document.addEventListener('scroll', function (e) {
    var el = e.target;
    if (el && el.classList && el.classList.contains('feed-in')) barSync(el);
  }, { capture: true, passive: true });

  // ---------- logo intro ----------
  // When the site opens, the sign in the header draws itself the way a hand would: the stem rises
  // and bends into the arch, the two posts rise, the crossbar is laid across last. Moving around
  // the site does not replay it. A re-render while it runs (a quick click, a language switch)
  // picks the drawing up at the same moment, because every stroke runs on one shared start time.
  // With reduced motion turned on in the system, the sign is simply there.
  var DRAW = [[80, 800], [340, 640], [480, 640], [840, 600]];  // [delay, duration] in ms, per path
  var DRAW_END = 1480, drawT0 = null, drawWait = false;
  function drawLogo() {
    var svg = app.querySelector('.hd .mark'), tl = document.timeline;
    if (!svg || !svg.animate) return;
    if (drawT0 === null) {
      var still = false;
      try { still = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}
      if (still) { drawT0 = -1; return; }
      if (document.hidden) {  // opened in a background tab: draw when the tab is first shown
        if (!drawWait) {
          drawWait = true;
          document.addEventListener('visibilitychange', function wake() {
            if (document.hidden) return;
            document.removeEventListener('visibilitychange', wake);
            drawLogo();
          });
        }
        return;
      }
      drawT0 = tl && tl.currentTime !== null ? tl.currentTime : -2;  // -2: no shared clock, play once
      strokes(svg, drawT0 >= 0 ? drawT0 : null);
      return;
    }
    if (drawT0 < 0 || !tl || tl.currentTime - drawT0 > DRAW_END) return;
    strokes(svg, drawT0);
  }
  function strokes(svg, t0) {
    var ps = svg.querySelectorAll('path');
    for (var i = 0; i < ps.length && i < DRAW.length; i++) {
      var len = ps[i].getTotalLength(), dash = len + 'px ' + (len + 40) + 'px';
      var an = ps[i].animate([
        { strokeDasharray: dash, strokeDashoffset: (len + 20) + 'px' },
        { strokeDasharray: dash, strokeDashoffset: '0px' }
      ], { duration: DRAW[i][1], delay: DRAW[i][0], easing: 'cubic-bezier(.45,0,.2,1)', fill: 'backwards' });
      if (t0 !== null) an.startTime = t0;
    }
  }
  function stickyCheck() {
    var el = document.getElementById('otx');
    if (!el) return;
    el.classList.remove('flow');
    if (el.offsetHeight > window.innerHeight - 130) el.classList.add('flow');
  }
  window.addEventListener('resize', stickyCheck);

  // ---------- overlays ----------
  var lastFocus = null;
  function openOverlay(id) {
    lastFocus = document.activeElement;
    var el = document.getElementById(id);
    var r = site.parse(location.pathname, location.search);
    if (id === 'ov-menu') el.innerHTML = site.menu(r);
    else {
      el.innerHTML = site.searchShell();
      var inp = document.getElementById('so-input');
      inp.addEventListener('input', function () { results(inp.value); });
      results('');
      if (!full) loadData().then(function () { results(inp.value); }, function () {});
    }
    el.hidden = false;
    document.body.style.overflow = 'hidden';
    var f = id === 'ov-search' ? document.getElementById('so-input') : el.querySelector('button');
    if (f) setTimeout(function () { f.focus(); }, 20);
  }
  function results(q) {
    var box = document.getElementById('so-r');
    if (box) box.innerHTML = site.searchResults(q);
  }
  function closeOverlays() {
    ['ov-menu', 'ov-search'].forEach(function (id) { var el = document.getElementById(id); if (el) el.hidden = true; });
    document.body.style.overflow = '';
  }

  // ---------- clicks and keys ----------
  function internal(a) {
    if (!a || a.target === '_blank' || a.hasAttribute('download')) return null;
    var href = a.getAttribute('href');
    if (!href || href.charAt(0) !== '/' || href.charAt(1) === '/') return null;
    if (/^\/(img|assets)\//.test(href)) return null;
    return href;
  }
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest('[data-act]');
    if (a) {
      var act = a.getAttribute('data-act'), v = a.getAttribute('data-v');
      if (act === 'lang') {
        try { localStorage.setItem('ah-lang', v); } catch (er) {}
        if (v === L) { e.preventDefault(); closeOverlays(); return; }
        e.preventDefault();
        navigate(a.getAttribute('href'), false, true);
        return;
      }
      if (act === 'menu') { openOverlay('ov-menu'); return; }
      if (act === 'search') { openOverlay('ov-search'); return; }
      if (act === 'close') { closeOverlays(); if (lastFocus && lastFocus.focus) lastFocus.focus(); return; }
      if (act === 'filters') {
        loadData().then(function () { site.toggleFilters(); var y = window.scrollY; render(); window.scrollTo(0, y); }, function () {});
        return;
      }
      if (act === 'go') {
        var to = a.getAttribute('data-h');
        loadData().then(function () { navigate(to, true, true); }, function () { location.replace(to); });
        return;
      }
      if (act === 'more') {
        loadData().then(function () {
          var y = window.scrollY, r = site.parse(location.pathname, location.search), st = r.st;
          site.view(r);   // the page came from the build: let the archive count what is already shown
          site.lim.n += (st && st.view === 'list' ? 60 : 120);
          render(); window.scrollTo(0, y);
        }, function () {});
        return;
      }
      if (act === 'fill') { var inp = document.getElementById('so-input'); inp.value = v; results(v); inp.focus(); return; }
      if (act === 'jump') {
        e.preventDefault();
        var tgt = document.getElementById(v);
        if (tgt) window.scrollTo(0, tgt.getBoundingClientRect().top + window.scrollY - 90);
        return;
      }
      if (act === 'copy') {
        var done = function () { a.textContent = site.t('copied'); setTimeout(function () { a.textContent = site.t('copy'); }, 1600); };
        try {
          navigator.clipboard.writeText(v).then(done, function () { selectText(a.previousElementSibling); });
        } catch (er) { selectText(a.previousElementSibling); }
        return;
      }
    }
    var ln = e.target.closest('a[href]'), href = internal(ln);
    if (!href) return;
    if (href === here()) { e.preventDefault(); closeOverlays(); window.scrollTo(0, 0); return; }
    if (!full) return;   // the browser loads the ready page itself
    e.preventDefault();
    navigate(href, false, false);
  });
  function selectText(el) {
    if (!el) return;
    try { var rg = document.createRange(); rg.selectNodeContents(el); var s = window.getSelection(); s.removeAllRanges(); s.addRange(rg); } catch (e) {}
  }
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      var open = !document.getElementById('ov-menu').hidden || !document.getElementById('ov-search').hidden;
      if (open) { closeOverlays(); if (lastFocus && lastFocus.focus) lastFocus.focus(); }
    }
  });
  document.addEventListener('mouseover', function (e) {
    var tile = e.target.closest && e.target.closest('.tile');
    var ro = document.getElementById('ro');
    if (!tile || !ro || !full) return;
    var o = full.BY[+tile.getAttribute('data-id')];
    if (!o) return;
    var rest = [full.byline(o), o.y ? full.tr(o.y) : ''].filter(Boolean).join(', ');
    ro.innerHTML = '<span>' + esc(full.tr(o.t)) + (rest ? '<span class="mu"> — ' + esc(rest) + '</span>' : '') + '</span>';
  });
  document.addEventListener('mouseout', function (e) {
    var ag = document.getElementById('ag'), ro = document.getElementById('ro');
    if (!ag || !ro || !full) return;
    if (e.relatedTarget && ag.contains(e.relatedTarget)) return;
    if (!ag.contains(e.target)) return;
    var st = full.parse(location.pathname, location.search).st, n = full.filtered(st).length;
    ro.innerHTML = '<span class="mu">' + full.count(n) + ' ' + full.t('inSel') + '<span class="hint"> · ' + full.t('hint') + '</span></span>';
  });

  // ---------- start ----------
  // The page arrives rendered. It is drawn again here only when the address asks for something the
  // build could not know: archive filters in the query, or a not-found page in the other language.
  var first = site.parse(location.pathname, location.search);
  var page = app.getAttribute('data-page');
  var needs = (first.name === 'archive' && location.search.length > 1) || (page === '404' && first.lang !== app.getAttribute('data-lang'));
  if (needs) {
    if (page === '404') { render({ name: '404', lang: first.lang }); }
    else loadData().then(function () { render(); }, function () { document.documentElement.classList.remove('ah-wait'); });
  } else {
    after();
  }
  // the text column of an entry may grow once the fonts arrive: check again whether it still fits the screen
  try { if (document.fonts && document.fonts.ready) document.fonts.ready.then(stickyCheck); } catch (e) {}
  // the data file is fetched once the page is up, so that the next click renders at once
  function warm() { loadData().catch(function () {}); }
  if (document.readyState === 'complete') setTimeout(warm, 300);
  else window.addEventListener('load', function () { setTimeout(warm, 300); });
})(this);
