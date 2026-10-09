#!/usr/bin/env node
/* Renders every page of the site ahead of time with the same code the browser runs (web/app.js).
   usage: node prerender.js config.json
   config: {data, out, site, css, js, dataUrl, built, only?: [{lang, name, id|tab, path}], provisional?: {...}}
   Writes <out>/<path>/index.html for every page, 404.html, sitemap.xml, feed.xml, en/feed.xml,
   and prints the list of written files (relative to out) as JSON. */
'use strict';
const fs = require('fs');
const path = require('path');

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const AH = require(path.join(__dirname, 'web', 'app.js'));
const SITE = cfg.site.replace(/\/$/, '');                     // https://theahmag.com
const raw = fs.readFileSync(cfg.data, 'utf8');
const written = [];

function data() { return JSON.parse(raw); }                    // createSite marks entries, so each language gets its own copy
function write(rel, content) {
  const file = path.join(cfg.out, rel);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, content);
  written.push(rel);
}
const esc = AH.esc;
function clip(s, n) {
  s = String(s || '').replace(/\s+/g, ' ').trim();
  if (s.length <= n) return s;
  const cut = s.slice(0, n - 1);
  const sp = cut.lastIndexOf(' ');
  return (sp > n * 0.6 ? cut.slice(0, sp) : cut).replace(/[\s,;:—–-]+$/, '') + '…';
}
function fileOf(url) { return url.replace(/^\//, '').replace(/\?.*$/, '') + 'index.html'; }

const COPY = {
  ru: {
    site: 'AH Magazine — визуальный архив искусства, пространства и структуры: архитектура, интерьеры, искусство, фотография, кино и находки из архивов.',
    archive: (n) => `${n} в архиве AH Magazine: архитектура, искусство, фотография, кино и находки из архивов.`,
    notes: 'Notes — длинные тексты AH Magazine о том, что стоит за архивом.',
    index: 'Указатель AH Magazine: архитекторы, художники, фотографы, страны и годы всех объектов архива.',
    countries: 'Страны в архиве AH Magazine.', time: 'Время: объекты архива AH Magazine по десятилетиям.',
    about: 'AH Magazine — визуальный архив искусства, пространства и структуры. Журнал начался в марте 2025 года как Telegram-канал.',
    partners: 'Партнёрство с AH Magazine: публикация проекта, текст в Notes, подборка, видео для Instagram.',
    person: (name, sub) => `${name} в архиве AH Magazine${sub ? ': ' + sub : ''}.`,
    locale: 'ru_RU', other: 'en_US', feed: 'AH Magazine — новое в архиве'
  },
  en: {
    site: 'AH Magazine is a visual archive of art, space and structure: architecture, interiors, art, photography, cinema and archival finds.',
    archive: (n) => `${n} in the AH Magazine archive: architecture, art, photography, cinema and archival finds.`,
    notes: 'Notes: longer essays from AH Magazine on what lies behind the archive.',
    index: 'The AH Magazine index: architects, artists, photographers, countries and years across the archive.',
    countries: 'Countries in the AH Magazine archive.', time: 'Periods: the AH Magazine archive by decade.',
    about: 'AH Magazine is a visual archive of art, space and structure. It started in March 2025 as a Telegram channel.',
    partners: 'Partnerships with AH Magazine: project features, essays in Notes, series and Instagram videos.',
    person: (name, sub) => `${name} in the AH Magazine archive${sub ? ': ' + sub : ''}.`,
    locale: 'en_US', other: 'ru_RU', feed: 'AH Magazine: new in the archive'
  }
};

// what a page tells search engines and link previews
function meta(site, r, L) {
  const D = site.D, c = COPY[L], tr = site.tr;
  const m = { type: 'website', image: SITE + '/share.jpg', w: 1200, h: 630, description: c.site, published: null };
  const img = (o) => { m.image = SITE + '/img/c/' + (o.ik || o.id) + '.jpg'; m.w = o.img.cw; m.h = o.img.ch; };
  if (r.name === 'object') {
    const o = site.BY[r.id];
    const lines = [o.s && tr(o.s), (o.b[L] && o.b[L][0]) || (o.b.ru && o.b.ru[0])].filter(Boolean);
    const by = [site.byline(o), o.pl && tr(o.pl) !== site.byline(o) ? tr(o.pl) : '', o.y ? tr(o.y) : ''].filter(Boolean).join(', ');
    m.description = clip(lines[0] ? (by ? by + '. ' : '') + lines[0] : (tr(o.t) + (by ? ' — ' + by : '')), 200);
    m.type = 'article'; m.published = o.d; img(o);
  } else if (r.name === 'note') {
    const n = site.NBY[r.id];
    const first = (n.bl.find((b) => b.type === 'p') || {});
    m.description = clip(tr(n.sf) || tr(first), 220);
    m.type = 'article'; m.published = n.d; img(n);
  } else if (r.name === 'person') {
    const p = D.people[r.id];
    const items = p.objs.map((x) => site.BY[x]).filter((o) => o && !o.tmp);
    const roles = p.roles.map((x) => site.t('role')[x]).filter(Boolean).join(', ');
    m.description = clip(c.person(tr(p), [roles, site.count(items.length)].filter(Boolean).join(' · ')), 200);
    if (items[0]) img(items[0]);
  } else if (r.name === 'archive') {
    m.description = c.archive(site.count(D.objects.filter((o) => !o.tmp).length));
    if (D.objects[0]) img(D.objects[0]);
  } else if (r.name === 'notes') {
    m.description = c.notes;
    if (D.notes[0]) img(D.notes[0]);
  } else if (r.name === 'index') m.description = r.tab === 'countries' ? c.countries : r.tab === 'time' ? c.time : c.index;
  else if (r.name === 'about') m.description = c.about;
  else if (r.name === 'partners') m.description = c.partners;
  return m;
}

// structured data (schema.org) for search engines: the site itself on the home page, articles with their date
// and picture on entry and note pages, and the same breadcrumb trail the entry page shows
const ORG = { '@type': 'Organization', name: 'AH Magazine', url: SITE + '/',
  logo: { '@type': 'ImageObject', url: SITE + '/icon-512.png', width: 512, height: 512 }, sameAs: ['https://t.me/ahmagazine'] };
function ld(site, r, L, m, canon, title) {
  const out = [];
  if (r.name === 'home') {
    out.push({ '@context': 'https://schema.org', '@type': 'WebSite', name: 'AH Magazine', url: canon, inLanguage: L,
      description: m.description, publisher: ORG });
  } else if (r.name === 'object' || r.name === 'note') {
    const x = r.name === 'object' ? site.BY[r.id] : site.NBY[r.id];
    const when = x.d + 'T09:00:00+03:00';
    out.push({ '@context': 'https://schema.org', '@type': 'Article', headline: clip(title, 110), description: m.description,
      image: [m.image], datePublished: when, dateModified: when, inLanguage: L, url: canon, mainEntityOfPage: canon,
      author: { '@type': 'Organization', name: 'AH Magazine', url: SITE + '/' }, publisher: ORG });
    if (r.name === 'object' && x.cats && x.cats[0] && site.catLabel(x.cats[0])) {
      out.push({ '@context': 'https://schema.org', '@type': 'BreadcrumbList', itemListElement: [
        { '@type': 'ListItem', position: 1, name: site.t('archive'), item: SITE + site.url({ name: 'archive' }) },
        { '@type': 'ListItem', position: 2, name: site.catLabel(x.cats[0]), item: SITE + site.url({ name: 'archive', st: { cat: x.cats[0] } }) },
        { '@type': 'ListItem', position: 3, name: title }] });
    }
  }
  return out.map((o) => '<script type="application/ld+json">' + JSON.stringify(o).replace(/</g, '\\u003c') + '</script>');
}

const LEGACY = "(function(){var h=location.hash,l=null,b='';try{l=localStorage.getItem('ah-lang')}catch(e){}if(l==='en')b='/en';" +
  "function m(h){var p;try{p=decodeURIComponent(h.slice(1)).split('~')}catch(e){return null}var a=p[0];" +
  "if(/^o-\\d+$/.test(a))return '/o/'+a.slice(2)+'/';if(/^n-\\d+$/.test(a))return '/n/'+a.slice(2)+'/';" +
  "if(/^p-[a-z0-9-]+$/.test(a))return '/p/'+a.slice(2)+'/';if(a==='notes'||a==='about'||a==='partners')return '/'+a+'/';" +
  "if(a==='index')return '/index/'+(p[1]==='countries'||p[1]==='time'?p[1]+'/':'');" +
  "if(a==='archive'){var q=[];p.slice(1).forEach(function(s){var i=s.indexOf('.');if(i>0)q.push(s.slice(0,i)+'='+encodeURIComponent(s.slice(i+1)))});return '/archive/'+(q.length?'?'+q.join('&'):'')}" +
  "if(a==='home')return '/';return null}" +
  "if(h&&h.length>1){var t=m(h);if(t){location.replace(b+t);return}}" +
  "if(location.pathname==='/'&&b&&!h)location.replace('/en/')})();";
const WAIT = "if(location.search.length>1){document.documentElement.className+=' ah-wait';setTimeout(function(){document.documentElement.classList.remove('ah-wait')},3000)}";

function page(site, r, L, opts) {
  opts = opts || {};
  const v = site.view(r);
  const m = meta(site, r, L);
  const url = opts.url || site.url(r);
  const canon = SITE + url.replace(/\?.*$/, '');
  const alt = (lang) => SITE + AH.urlOf(r, lang).replace(/\?.*$/, '');
  const head = [
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">',
    `<title>${esc(v.title)}</title>`,
    `<meta name="description" content="${esc(m.description)}">`
  ];
  if (opts.noindex) head.push('<meta name="robots" content="noindex">');
  else if (r.name !== '404') {
    head.push(`<link rel="canonical" href="${canon}">`,
      `<link rel="alternate" hreflang="ru" href="${alt('ru')}">`,
      `<link rel="alternate" hreflang="en" href="${alt('en')}">`,
      `<link rel="alternate" hreflang="x-default" href="${alt('ru')}">`);
  }
  head.push(
    '<meta property="og:site_name" content="AH Magazine">',
    `<meta property="og:type" content="${m.type}">`,
    `<meta property="og:title" content="${esc(v.title.replace(/ — AH Magazine$/, ''))}">`,
    `<meta property="og:description" content="${esc(m.description)}">`,
    `<meta property="og:url" content="${canon}">`,
    `<meta property="og:image" content="${m.image}">`,
    `<meta property="og:image:width" content="${m.w}">`,
    `<meta property="og:image:height" content="${m.h}">`,
    `<meta property="og:locale" content="${COPY[L].locale}">`,
    `<meta property="og:locale:alternate" content="${COPY[L].other}">`,
    '<meta name="twitter:card" content="summary_large_image">',
    '<meta name="telegram:channel" content="@ahmagazine">');
  if (m.published) head.push(`<meta property="article:published_time" content="${m.published}">`);
  if (!opts.noindex) head.push(...ld(site, r, L, m, canon, v.title.replace(/ — AH Magazine$/, '')));
  head.push(
    '<meta name="theme-color" content="#FFFFFF">',
    '<link rel="icon" href="/favicon.ico" sizes="32x32">',
    '<link rel="icon" href="/icon.svg" type="image/svg+xml">',
    '<link rel="apple-touch-icon" href="/apple-touch-icon.png">',
    '<link rel="manifest" href="/site.webmanifest">',
    `<link rel="alternate" type="application/rss+xml" title="${esc(COPY[L].feed)}" href="${SITE}${L === 'en' ? '/en' : ''}/feed.xml">`,
    `<style>${cfg.css}</style>`);
  if (r.name === 'home') head.push(`<script>${LEGACY}</script>`);
  if (r.name === 'archive') head.push(`<script>${WAIT}</script>`);
  head.push(`<script src="${cfg.js}" defer data-data="${cfg.dataUrl}"></script>`);
  return '<!doctype html>\n<html lang="' + L + '">\n<head>\n' + head.join('\n') + '\n</head>\n<body>\n' +
    `<div id="app" data-page="${r.name}" data-lang="${L}">${v.html}</div>\n` +
    '<div class="ov" id="ov-menu" hidden></div>\n' +
    '<div class="ov" id="ov-search" role="dialog" aria-modal="true" aria-labelledby="so-label" hidden></div>\n' +
    '</body>\n</html>\n';
}

function routes(D) {
  const out = [{ name: 'home' }, { name: 'archive', st: {} }, { name: 'notes' },
    { name: 'index', tab: 'names' }, { name: 'index', tab: 'countries' }, { name: 'index', tab: 'time' },
    { name: 'about' }, { name: 'partners' }];
  D.objects.forEach((o) => { if (!o.tmp) out.push({ name: 'object', id: o.id }); });
  D.notes.forEach((n) => { if (!n.tmp) out.push({ name: 'note', id: n.id }); });
  Object.keys(D.people).forEach((k) => out.push({ name: 'person', id: k }));
  return out;
}

function xml(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }

if (cfg.provisional) {
  // one page for an entry whose post is about to go out: /a/<key>/ until the channel gives it its number
  const p = cfg.provisional;
  for (const L of ['ru', 'en']) {
    const site = AH.createSite(data());
    site.setLang(L);
    const r = { name: p.kind === 'note' ? 'note' : 'object', id: p.id, lang: L };
    const url = (L === 'en' ? '/en' : '') + '/a/' + p.key + '/';
    // the entry has no number yet: its own address in either language is /a/<key>/
    const own = new RegExp('(/en)?/' + (p.kind === 'note' ? 'n' : 'o') + '/' + p.id + '/', 'g');
    write(fileOf(url), page(site, r, L, { url, noindex: true }).replace(own, (m0, en) => (en || '') + '/a/' + p.key + '/'));
  }
} else {
  const sitemap = [];
  for (const L of ['ru', 'en']) {
    const site = AH.createSite(data());
    site.setLang(L);
    for (const r of routes(site.D)) {
      r.lang = L;
      const url = site.url(r);
      if (cfg.only && !cfg.only.includes(url)) continue;
      write(fileOf(url), page(site, r, L));
      if (L === 'ru') {
        const last = r.name === 'object' ? site.BY[r.id].d : r.name === 'note' ? site.NBY[r.id].d : cfg.built;
        sitemap.push({ ru: SITE + url, en: SITE + AH.urlOf(r, 'en'), last });
      }
    }
    if (L === 'ru') write('404.html', page(site, { name: '404', lang: 'ru' }, 'ru'));
    // RSS: the newest entries and notes
    const items = site.D.objects.filter((o) => !o.tmp).slice(0, 40).map((o) => ({ o, url: SITE + site.url({ name: 'object', id: o.id }), d: o.d }))
      .concat(site.D.notes.filter((n) => !n.tmp).map((n) => ({ n, url: SITE + site.url({ name: 'note', id: n.id }), d: n.d })))
      .sort((a, b) => (a.d < b.d ? 1 : a.d > b.d ? -1 : 0)).slice(0, 40);
    const pre = L === 'en' ? '/en' : '';
    const rss = ['<?xml version="1.0" encoding="UTF-8"?>',
      '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>',
      `<title>${xml(COPY[L].feed)}</title>`, `<link>${SITE}${pre}/</link>`,
      `<atom:link href="${SITE}${pre}/feed.xml" rel="self" type="application/rss+xml"/>`,
      `<description>${xml(COPY[L].site)}</description>`, `<language>${L}</language>`];
    for (const it of items) {
      const x = it.o || it.n;
      const title = site.tr(x.t);
      const desc = it.o ? (x.s ? site.tr(x.s) : site.byline(x)) : site.tr(x.sf);
      rss.push('<item>', `<title>${xml(title)}</title>`, `<link>${it.url}</link>`, `<guid isPermaLink="true">${it.url}</guid>`,
        `<pubDate>${new Date(x.d + 'T09:00:00+03:00').toUTCString()}</pubDate>`,
        `<description>${xml(clip(desc, 300))}</description>`,
        `<enclosure url="${SITE}/img/c/${x.ik || x.id}.jpg" type="image/jpeg" length="0"/>`, '</item>');
    }
    rss.push('</channel></rss>');
    write((pre ? 'en/' : '') + 'feed.xml', rss.join('\n') + '\n');
  }
  if (!cfg.only) {
    const sm = ['<?xml version="1.0" encoding="UTF-8"?>',
      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">'];
    for (const e of sitemap) {
      for (const [loc, lang] of [[e.ru, 'ru'], [e.en, 'en']]) {
        sm.push('<url>', `<loc>${xml(loc)}</loc>`, `<lastmod>${e.last}</lastmod>`,
          `<xhtml:link rel="alternate" hreflang="ru" href="${xml(e.ru)}"/>`,
          `<xhtml:link rel="alternate" hreflang="en" href="${xml(e.en)}"/>`,
          `<xhtml:link rel="alternate" hreflang="x-default" href="${xml(e.ru)}"/>`, '</url>');
      }
    }
    sm.push('</urlset>');
    write('sitemap.xml', sm.join('\n') + '\n');
  }
}
process.stdout.write(JSON.stringify(written));
