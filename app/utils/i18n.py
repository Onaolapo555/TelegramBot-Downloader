from __future__ import annotations

import json
from pathlib import Path

_LOCALES_DIR = Path(__file__).parent.parent / "locales"
_CACHE: dict[str, dict[str, str]] = {}


def load_locale(lang: str = "en") -> dict[str, str]:
    if lang in _CACHE:
        return _CACHE[lang]
    p = _LOCALES_DIR / f"{lang}.json"
    if not p.exists():
        p = _LOCALES_DIR / "en.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    _CACHE[lang] = data
    return data


def t(key: str, lang: str = "en", **kwargs) -> str:
    loc = load_locale(lang)
    tmpl = loc.get(key, key)
    try:
        return tmpl.format(**kwargs)
    except Exception:
        return tmpl
