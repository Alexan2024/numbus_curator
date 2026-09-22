"""AHMAG bot.

Здесь к модулям бота снаружи подключаются дополнения, сами модули при этом не меняются:
- app/brand.py — логотип на фото (к app.cards и app.instagram);
- app/igfit.py — карусель Instagram без полей (к app.instagram);
- app/request.py — пост по запросу, отложенные, «ещё кадры», очередь публикаций (к app.bot);
- app/finds.py — нишевые источники, архивы вглубь, Are.na, слот находки (к app.bot)."""
import importlib
import importlib.abc
import logging
import sys

# модуль бота → дополнения по порядку
_TARGETS = {
    "app.cards": ["app.brand"],
    "app.instagram": ["app.brand", "app.igfit"],
    "app.bot": ["app.request", "app.finds"],
}


class _Hook(importlib.abc.MetaPathFinder):
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
        addons = _TARGETS[name]

        def exec_module(module, _run=run, _addons=addons):
            _run(module)
            for addon in _addons:
                try:
                    importlib.import_module(addon).attach(module)
                except Exception:
                    logging.getLogger(addon).exception("Дополнение %s не подключилось к %s", addon, module.__name__)

        spec.loader.exec_module = exec_module
        return spec


# прежнее имя класса оставлено, чтобы при горячей перезагрузке не встало два крючка
_BrandHook = _Hook

if not any(type(f).__name__ in ("_Hook", "_BrandHook") for f in sys.meta_path):
    sys.meta_path.insert(0, _Hook())
