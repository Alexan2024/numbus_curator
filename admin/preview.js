/* Предпросмотр заметки по закрытой ссылке: та же вёрстка, что на сайте (site/web/app.js), с черновиком внутри. */
(function () {
  'use strict';
  const box = document.getElementById('pv');
  const tok = location.pathname.split('/').filter(Boolean).pop();
  const SITE = 'https://theahmag.com';
  let data = null, lang = 'ru';

  function render() {
    const site = window.AH.createSite(JSON.parse(JSON.stringify(data.D)));
    site.setLang(lang);
    const v = site.view({ name: 'note', id: data.id, lang });
    box.innerHTML = v.html;
    document.title = 'Предпросмотр · ' + v.title;
    document.documentElement.lang = lang;
    // ссылки ведут на сам сайт, а не на адрес редакции
    box.querySelectorAll('a[href^="/"]').forEach((a) => { a.href = SITE + a.getAttribute('href'); a.target = '_blank'; a.rel = 'noopener'; });
    box.querySelectorAll('button[data-act]').forEach((b) => { b.disabled = true; });
  }
  fetch('/admin/preview/' + encodeURIComponent(tok) + '/data', { cache: 'no-store' })
    .then((r) => { if (!r.ok) throw new Error(r.status === 404 ? 'Ссылка устарела или у заметки ещё нет обложки' : 'Ошибка ' + r.status); return r.json(); })
    .then((d) => { data = d; render(); })
    .catch((e) => { box.innerHTML = '<p class="pv-wait">' + window.AH.esc(e.message) + '</p>'; });
  document.querySelector('.pv-lang').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b || !data) return;
    lang = b.dataset.l;
    document.querySelectorAll('.pv-lang button').forEach((x) => x.classList.toggle('on', x === b));
    render();
  });
})();
