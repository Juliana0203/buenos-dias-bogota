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
        items.append(
            NewsItem(
                title=title,
                link=link,
                source=feed["name"],
                published=when,
                summary=short_summary(entry.get("summary", ""), title, summary_words),
                local=bool(feed.get("local", False)),
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


def _term_pattern(term: str) -> str:
    return rf"\b{re.escape(normalize(term))}"


def mentions_bogota(text: str, bogota_terms: dict[str, list[str]]) -> bool:
    """True si el texto normalizado menciona Bogotá, una localidad, avenida o sistema de la ciudad."""
    return any(re.search(_term_pattern(t), text) for terms in bogota_terms.values() for t in terms)


def mobility_score(text: str, mobility: dict[str, float], services: dict[str, float], services_weight: float) -> float:
    """Suma de pesos de palabras de movilidad (prioridad 1) y de servicios (prioridad 2, con menor peso)."""
    score = sum(float(w) for kw, w in mobility.items() if re.search(_term_pattern(kw), text))
    score += services_weight * sum(float(w) for kw, w in services.items() if re.search(_term_pattern(kw), text))
    return score


def _has_any(text: str, terms: list[str]) -> bool:
    """True si alguno de los términos (sin tildes, prefijo de palabra) aparece en el texto normalizado."""
    return any(re.search(_term_pattern(t), text) for t in terms)


def score_item(item: NewsItem, news_cfg: dict[str, Any], now: datetime) -> float:
    """Puntaje de relevancia, o 0 si NO es de Bogotá o no trata de movilidad/calle (filtro estricto).

    Se exige Bogotá Y una palabra clave de movilidad o servicios; luego se suma un bono por feed local,
    se penaliza farándula/deportes y se premia la frescura.
    """
    text = normalize(f"{item.title} {item.summary}")
    if not mentions_bogota(text, news_cfg.get("bogota_terms", {})):
        return 0.0
    base = mobility_score(
        text,
        news_cfg.get("mobility_keywords", {}),
        news_cfg.get("services_keywords", {}),
        float(news_cfg.get("services_weight", 0.5)),
    )
    if base <= 0:
        return 0.0
    has_effect = _has_any(text, news_cfg.get("effect_keywords", []))
    if not has_effect and _has_any(text, news_cfg.get("mobility_discard_keywords", [])):
        return 0.0
    if not has_effect and not _has_any(text, list(news_cfg.get("services_keywords", {}))):
        return 0.0  # sin efecto en servicio o vías: anécdota o nota sin utilidad práctica
    if _has_any(text, news_cfg.get("low_priority_keywords", [])):
        base -= 6
    if item.local:
        base += float(news_cfg.get("local_bonus", 3))
    age_h = max((now - item.published).total_seconds() / 3600, 0)
    return base + max(0.0, 1 - age_h / 24) if base > 0 else 0.0


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


def filter_bogota_mobility(items: list[NewsItem], news_cfg: dict[str, Any], now: datetime) -> list[NewsItem]:
    """Aplica ventana de tiempo, filtro estricto Bogotá+movilidad y deduplicación; mejor puntaje primero."""
    cutoff = now - timedelta(hours=float(news_cfg.get("max_age_hours", 24)))
    fresh = [i for i in items if cutoff <= i.published <= now + timedelta(hours=1)]
    kept: list[NewsItem] = []
    bogota_count = 0
    discard_words_count = 0
    for item in fresh:
        text = normalize(f"{item.title} {item.summary}")
        if mentions_bogota(text, news_cfg.get("bogota_terms", {})):
            bogota_count += 1
            has_effect = _has_any(text, news_cfg.get("effect_keywords", []))
            if not has_effect and _has_any(text, news_cfg.get("mobility_discard_keywords", [])):
                discard_words_count += 1
        item.score = score_item(item, news_cfg, now)
        if item.score > 0:
            kept.append(item)
    deduped = dedupe(kept, float(news_cfg.get("dedup_threshold", 78)))
    log.info(
        "Filtro noticias Bogotá: total=%d, frescas_24h=%d, mencionan_bogota=%d, "
        "pasan_movilidad=%d, descartadas_palabras_descarte=%d, descartadas_duplicadas=%d, finales=%d",
        len(items),
        len(fresh),
        bogota_count,
        len(kept),
        discard_words_count,
        len(kept) - len(deduped),
        len(deduped),
    )
    return deduped


def gather_news(cfg: Config, now: datetime | None = None) -> list[NewsItem]:
    """Lee los feeds habilitados en paralelo; devuelve solo noticias de Bogotá sobre movilidad/calle (máx. config)."""
    news_cfg = cfg.section("news")
    now = now or datetime.now(UTC)
    feeds = [f for f in news_cfg.get("feeds", []) if f.get("enabled", True)]
    words = int(news_cfg.get("summary_max_words", 20))
    results = run_parallel({f["name"]: (lambda f=f: fetch_feed(f, words)) for f in feeds})
    failed = [name for name, res in results.items() if res is None]
    if failed:
        log.warning("Feeds sin datos: %s", ", ".join(failed))
    per_feed = {name: len(res) for name, res in results.items() if res is not None}
    items = [i for res in results.values() for i in (res or [])]
    log.info(
        "Noticias Bogotá leídas por feed: %s (total=%d)",
        ", ".join(f"{k}: {v}" for k, v in per_feed.items()) or "ninguno",
        len(items),
    )
    return filter_bogota_mobility(items, news_cfg, now)[: int(news_cfg.get("max_items", 4))]
