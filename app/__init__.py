"""AHMAG bot.

Здесь к модулям бота снаружи подключаются дополнения, сами модули при этом не меняются:
- app/brand.py — логотип на фото (к app.cards и app.instagram);
- app/request.py — пост по запросу и «⚡️ ближайший слот, даже занятый» (к app.bot)."""
import importlib
import importlib.abc
import logging
import sys

# модуль бота → (дополнение, что написать в лог при сбое)
_TARGETS = {
    "app.cards": ("app.brand", "Логотип не подключился к %s"),
    "app.instagram": ("app.brand", "Логотип не подключился к %s"),
    "app.bot": ("app.request", "Пост по запросу не подключился к %s"),
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
        addon, err = _TARGETS[name]

        def exec_module(module, _run=run, _addon=addon, _err=err):
            _run(module)
            try:
                importlib.import_module(_addon).attach(module)
            except Exception:
                logging.getLogger(_addon).exception(_err, module.__name__)

        spec.loader.exec_module = exec_module
        return spec


# прежнее имя класса оставлено, чтобы при горячей перезагрузке не встало два крючка
_BrandHook = _Hook

if not any(type(f).__name__ in ("_Hook", "_BrandHook") for f in sys.meta_path):
    sys.meta_path.insert(0, _Hook())
