from __future__ import annotations

import unicodedata


def clean_name(value: str) -> str:
    return value.strip()


def normalize_name(value: str) -> str:
    return unicodedata.normalize("NFKC", clean_name(value)).casefold()
