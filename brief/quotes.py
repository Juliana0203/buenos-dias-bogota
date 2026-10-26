"""Frase del día: solo el archivo local en español, determinista por fecha."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from brief.config import ROOT, Config
from brief.models import Quote

QUOTES_FILE = ROOT / "data" / "quotes_es.json"


def load_quotes(path: Path = QUOTES_FILE) -> list[Quote]:
    with path.open(encoding="utf-8") as fh:
        return [Quote(text=q["text"], author=q.get("author") or "Anónimo") for q in json.load(fh)]


def pick_local(quotes: list[Quote], today: date) -> Quote:
    """Índice por día ordinal: no se repite mientras haya >= N frases consecutivas."""
    return quotes[today.toordinal() % len(quotes)]


def get_quote(cfg: Config, today: date, quotes: list[Quote] | None = None) -> Quote:
    """Frase del día (solo local, en español)."""
    return pick_local(quotes or load_quotes(), today)
