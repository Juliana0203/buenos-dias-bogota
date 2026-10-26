"""Noticias: lectura de RSS, filtro de 24 h, deduplicación y puntuación por relevancia."""

from __future__ import annotations

import html
import logging
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

import feedparser
from rapidfuzz import fuzz

from brief.config import Config
from brief.consensus import run_parallel
from brief.http import make_session
from brief.models import NewsItem

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def normalize(text: str) -> str:
    """Minúsculas, sin tildes ni signos, para comparar y buscar palabras clave."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return _WS_RE.sub(" ", _NON_ALNUM.sub(" ", text)).strip()


def strip_html(text: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text or ""))).strip()


def short_summary(raw: str, title: str, max_words: int = 20) -> str:
    """Resumen breve (<= max_words) tomado de la primera frase de la descripción del feed.

    Se descarta si repite el titular. Nunca reproduce el artículo completo.
    """
    text = strip_html(raw)
    if not text or fuzz.ratio(normalize(text), normalize(title)) > 85:
        return ""
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    words = first.split()
    if len(words) > max_words:
        return " ".join(words[:max_words]).rstrip(",;:.") + "…"
    return first


def _entry_datetime(entry: Any) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=UTC)


def parse_feed(content: bytes | str, feed: dict[str, Any], summary_words: int = 20) -> list[NewsItem]:
    """Convierte el contenido de un feed en NewsItem (descarta entradas sin fecha/enlace)."""
    parsed = feedparser.parse(content)
    items: list[NewsItem] = []
    for entry in parsed.entries:
        title = strip_html(entry.get("title", ""))
        link = entry.get("link", "")
        when = _entry_datetime(entry)
        if not title or not link or when is None:
            continue
        scope = feed.get("scope", "colombia")
        if scope == "colombia" and "bogota" in normalize(title):
            scope = "bogota"
        items.append(
            NewsItem(
                title=title,
                link=link,
                source=feed["name"],
                published=when,
                summary=short_summary(entry.get("summary", ""), title, summary_words),
                scope=scope,
            )
        )
    return items


def fetch_feed(feed: dict[str, Any], summary_words: int = 20) -> list[NewsItem]:
    """Descarga un feed (con timeout y reintentos) y lo interpreta."""
    resp = make_session().get(feed["url"], timeout=15)
    resp.raise_for_status()
    items = parse_feed(resp.content, feed, summary_words)
    if not items:
        raise ValueError("el feed no devolvió entradas válidas")
    return items


def score_item(item: NewsItem, keywords: dict[str, float], low_priority: list[str], now: datetime) -> float:
    """Puntúa relevancia: palabras clave (+), farándula/deportes (-) y frescura (+)."""
    text = normalize(f"{item.title} {item.summary}")
    score = 0.0
    for kw, weight in keywords.items():
        if re.search(rf"\b{re.escape(normalize(kw))}", text):
            score += float(weight)
    if any(re.search(rf"\b{re.escape(normalize(w))}\b", text) for w in low_priority):
        score -= 6
    age_h = max((now - item.published).total_seconds() / 3600, 0)
    return score + max(0.0, 1 - age_h / 24)


def dedupe(items: list[NewsItem], threshold: float) -> list[NewsItem]:
    """Elimina titulares parecidos entre fuentes, conservando el de mayor puntaje."""
    kept: list[NewsItem] = []
    norms: list[str] = []
    for item in sorted(items, key=lambda i: (i.score, i.published), reverse=True):
        norm = normalize(item.title)
        if any(fuzz.token_set_ratio(norm, other) >= threshold for other in norms):
            continue
        kept.append(item)
        norms.append(norm)
    return kept


def gather_news(cfg: Config, now: datetime | None = None) -> list[NewsItem]:
    """Lee todos los feeds habilitados en paralelo; devuelve noticias de 24 h, deduplicadas y puntuadas."""
    news_cfg = cfg.section("news")
    now = now or datetime.now(UTC)
    feeds = [f for f in news_cfg.get("feeds", []) if f.get("enabled", True)]
    words = int(news_cfg.get("summary_max_words", 20))
    results = run_parallel({f["name"]: (lambda f=f: fetch_feed(f, words)) for f in feeds})
    failed = [name for name, res in results.items() if res is None]
    if failed:
        log.warning("Feeds sin datos: %s", ", ".join(failed))

    cutoff = now - timedelta(hours=float(news_cfg.get("max_age_hours", 24)))
    keywords = news_cfg.get("keywords", {})
    low = news_cfg.get("low_priority_keywords", [])
    items = [i for res in results.values() for i in (res or []) if cutoff <= i.published <= now + timedelta(hours=1)]
    for item in items:
        item.score = score_item(item, keywords, low, now)
    return dedupe(items, float(news_cfg.get("dedup_threshold", 78)))


def select_top(items: list[NewsItem], colombia: int, bogota: int) -> list[NewsItem]:
    """Elige N de Colombia y M de Bogotá; si falta alguno, completa con el otro grupo."""
    ranked = sorted(items, key=lambda i: (i.score, i.published), reverse=True)
    col = [i for i in ranked if i.scope != "bogota"]
    bog = [i for i in ranked if i.scope == "bogota"]
    picked = col[:colombia] + bog[:bogota]
    total = colombia + bogota
    if len(picked) < total:
        rest = [i for i in ranked if i not in picked]
        picked += rest[: total - len(picked)]
    return sorted(picked, key=lambda i: (i.scope == "bogota", -i.score))
