"""AHMAG bot.

Здесь подключается логотип на фото (app/brand.py): когда загружаются app.cards и app.instagram,
к их функциям отправки фото пристёгивается наложение знака. Сами модули при этом не меняются."""
import importlib.abc
import logging
import sys

_TARGETS = ("app.cards", "app.instagram")


class _BrandHook(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name not in _TARGETS:
            return None
        spec = None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(name, path, target)
            if spec:
                break
        if not spec or not spec.loader or not hasattr(spec.loader, "exec_module"):
            return spec
        run = spec.loader.exec_module

        def exec_module(module, _run=run):
            _run(module)
            try:
                from app import brand
                brand.attach(module)
            except Exception:
                logging.getLogger("app.brand").exception("Логотип не подключился к %s", module.__name__)

        spec.loader.exec_module = exec_module
        return spec


if not any(isinstance(f, _BrandHook) for f in sys.meta_path):
    sys.meta_path.insert(0, _BrandHook())
