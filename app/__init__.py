"""Пристёжка дополнительных модулей к готовым файлам бота.

Сами cards.py и instagram.py не редактируются: как только Python их загружает,
нужные функции в них подменяются на обёртки из отдельных модулей.

    app.cards       → brand    логотип на фото, которые уходят в канал
    app.instagram   → brand    логотип на фото для Instagram
                    → igfit    пропорция карусели и обрезка без полей

В логах при запуске появляются строки «Логотип подключён: …» и «Карусель Instagram: без полей …»."""
import importlib.util
import logging
import sys
from importlib.abc import Loader, MetaPathFinder

log = logging.getLogger(__name__)

HOOKS = {
    "app.cards": ("brand",),
    "app.instagram": ("brand", "igfit"),
}


def _attach(module) -> None:
    for hook in HOOKS.get(module.__name__, ()):
        try:
            importlib.import_module(f"app.{hook}").attach(module)
        except Exception:
            log.error("Модуль %s не подключился к %s — бот работает без него", hook, module.__name__,
                      exc_info=True)


class _Proxy(Loader):
    def __init__(self, inner: Loader):
        self._inner = inner

    def create_module(self, spec):
        return self._inner.create_module(spec)

    def exec_module(self, module):
        self._inner.exec_module(module)
        _attach(module)


class _Finder(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in HOOKS:
            return None
        sys.meta_path.remove(self)              # чтобы не искать самого себя
        try:
            spec = importlib.util.find_spec(fullname)
        except Exception:
            return None
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _Proxy(spec.loader)
        return spec


if not any(isinstance(f, _Finder) for f in sys.meta_path):
    sys.meta_path.insert(0, _Finder())
