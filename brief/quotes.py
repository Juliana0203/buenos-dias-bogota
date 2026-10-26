"""Frase del día: archivo local determinista por fecha, con zenquotes.io de vez en cuando."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from brief.config import ROOT, Config
from brief.http import get_json
from brief.models import Quote

log = logging.getLogger(__name__)
QUOTES_FILE = ROOT / "data" / "quotes_es.json"


def load_quotes(path: Path = QUOTES_FILE) -> list[Quote]:
    with path.open(encoding="utf-8") as fh:
        return [Quote(text=q["text"], author=q["author"]) for q in json.load(fh)]


def pick_local(quotes: list[Quote], today: date) -> Quote:
    """Índice por día ordinal: no se repite mientras haya >= N frases consecutivas."""
    return quotes[today.toordinal() % len(quotes)]


def uses_api(today: date, every_n_days: int) -> bool:
    return every_n_days > 0 and today.toordinal() % every_n_days == 0


def fetch_zenquote(url: str) -> Quote | None:
    """Frase de zenquotes.io (en inglés). None si falla."""
    data = get_json(url)
    if isinstance(data, list) and data and data[0].get("q"):
        return Quote(text=data[0]["q"], author=data[0].get("a") or "Anónimo", origin="api")
    return None


def get_quote(cfg: Config, today: date, quotes: list[Quote] | None = None) -> Quote:
    """Frase del día. Si la API falla, cae a la frase local."""
    quotes = quotes or load_quotes()
    qcfg = cfg.section("quotes")
    if uses_api(today, int(qcfg.get("api_every_n_days", 0))):
        try:
            q = fetch_zenquote(qcfg["api_url"])
            if q:
                return q
        except Exception as exc:  # noqa: BLE001
            log.warning("zenquotes falló: %s", exc)
    return pick_local(quotes, today)
