"""Backend i18n: gettext-based translations + FastAPI locale dependency."""

import gettext
import os
from collections.abc import Callable
from functools import lru_cache
from typing import Annotated

from fastapi import Header

SUPPORTED_LOCALES = frozenset({"en", "zh"})
_LOCALE_DIR = os.path.join(os.path.dirname(__file__), "messages")


@lru_cache(maxsize=4)
def _load_translation(locale: str) -> gettext.GNUTranslations | gettext.NullTranslations:
    try:
        return gettext.translation("messages", localedir=_LOCALE_DIR, languages=[locale])
    except FileNotFoundError:
        return gettext.NullTranslations()


def get_translator(locale: str) -> Callable[[str], str]:
    safe_locale = locale if locale in SUPPORTED_LOCALES else "en"
    return _load_translation(safe_locale).gettext


def get_locale(accept_language: Annotated[str | None, Header()] = None) -> str:
    """FastAPI dependency: parse Accept-Language header, return 'en' or 'zh'."""
    if not accept_language:
        return "en"
    primary = accept_language.split(",")[0].split(";")[0].strip().lower()
    if primary.startswith("zh"):
        return "zh"
    return "en"
