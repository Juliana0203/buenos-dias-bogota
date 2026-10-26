"""Una buena noticia del día: feeds en español, filtro por palabras positivas/negativas, sin traducción."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from brief.config import Config
from brief.consensus import run_parallel
from brief.models import NewsItem
from brief.sources.news import fetch_feed, normalize

log = logging.getLogger(__name__)

SPANISH_HINTS = {"de", "la", "el", "en", "los", "las", "del", "que", "por", "con", "para", "una", "un", "se", "su"}


def looks_spanish(text: str, english_stopwords: list[str]) -> bool:
    """Heurística: descarta textos con palabras vacías del inglés o sin ninguna del español."""
    words = normalize(text).split()
    if not words:
        return False
    english = {w.lower() for w in english_stopwords}
    if sum(w in english for w in words) >= 2:
        return False
    return any(w in SPANISH_HINTS for w in words)


def _matches(text: str, words: list[str]) -> int:
    return sum(bool(re.search(rf"\b{re.escape(normalize(w))}", text)) for w in words)


def positivity(item: NewsItem, cfg: dict[str, Any]) -> int:
    """Número de palabras positivas; 0 si hay alguna negativa, el texto no está en español o no hay positivas."""
    text = normalize(f"{item.title} {item.summary}")
    if not looks_spanish(f"{item.title} {item.summary}", cfg.get("stopwords_en", [])):
        return 0
    if _matches(text, cfg.get("negative_words", [])):
        return 0
    return _matches(text, cfg.get("positive_words", []))


def pick_good_news(items: list[NewsItem], cfg: dict[str, Any], now: datetime) -> NewsItem | None:
    """La noticia más positiva de las últimas horas configuradas; None si ninguna pasa el filtro."""
    cutoff = now - timedelta(hours=float(cfg.get("max_age_hours", 48)))
    best: tuple[int, datetime, NewsItem] | None = None
    old_count = 0
    not_es_count = 0
    neg_count = 0
    no_pos_count = 0
    passed_count = 0
    stopwords_en = cfg.get("stopwords_en", [])
    negative_words = cfg.get("negative_words", [])
    positive_words = cfg.get("positive_words", [])

    for item in items:
        if not cutoff <= item.published <= now + timedelta(hours=1):
            old_count += 1
            continue
        raw_text = f"{item.title} {item.summary}"
        if not looks_spanish(raw_text, stopwords_en):
            not_es_count += 1
            continue
        text = normalize(raw_text)
        if _matches(text, negative_words):
            neg_count += 1
            continue
        score = _matches(text, positive_words)
        if not score:
            no_pos_count += 1
            continue
        passed_count += 1
        if best is None or (score, item.published) > best[:2]:
            best = (score, item.published, item)

    log.info(
        "Buenas noticias evaluadas: total=%d, fuera_de_ventana=%d, no_espanol=%d, "
        "con_negativas=%d, sin_positivas=%d, candidatas=%d",
        len(items),
        old_count,
        not_es_count,
        neg_count,
        no_pos_count,
        passed_count,
    )
    if best is None:
        return None
    best[2].score = float(best[0])
    return best[2]


def gather_good_news(cfg: Config, now: datetime | None = None) -> NewsItem | None:
    """Lee los feeds en español en paralelo y escoge una buena noticia (o None)."""
    gcfg = cfg.section("good_news")
    now = now or datetime.now(UTC)
    feeds = [f for f in gcfg.get("feeds", []) if f.get("enabled", True)]
    words = int(gcfg.get("summary_max_words", 20))
    results = run_parallel({f["name"]: (lambda f=f: fetch_feed(f, words)) for f in feeds})
    failed = [name for name, res in results.items() if res is None]
    if failed:
        log.warning("Feeds de buenas noticias sin datos: %s", ", ".join(failed))
    per_feed = {name: len(res) for name, res in results.items() if res is not None}
    items = [i for res in results.values() for i in (res or [])]
    log.info(
        "Buenas noticias leídas por feed: %s (total=%d)",
        ", ".join(f"{k}: {v}" for k, v in per_feed.items()) or "ninguno",
        len(items),
    )
    return pick_good_news(items, gcfg, now)
