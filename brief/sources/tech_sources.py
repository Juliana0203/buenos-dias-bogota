"""Fuentes para el Radar semanal de tecnología: arXiv, Hugging Face Daily Papers, Semantic Scholar, HN y RSS."""

from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import feedparser

from brief.config import Config
from brief.consensus import run_parallel
from brief.http import get_json, make_session
from brief.sources.news import _entry_datetime, strip_html

log = logging.getLogger(__name__)

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


@dataclass
class TechCandidate:
    """Candidato crudo leído de una fuente académica o técnica."""

    title: str
    url: str
    source: str
    kind: str  # "paper" | "news" | "tool"
    published: datetime
    abstract: str = ""
    authors: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    default_topic: str = "ml_applied"
    language: str = "en"
    votes: int = 0
    citations: int = 0
    score: float = 0.0
    affinity_score: float = 0.0
    matched_topic: str = "ml_applied"
    matched_keywords: list[str] = field(default_factory=list)
    outside_radar: bool = False

    @property
    def short_id(self) -> str:
        digest = hashlib.sha1(self.url.encode("utf-8")).hexdigest()[:8]
        return f"p_{digest}"


def _within_window(published: datetime, now: datetime, max_age_days: float) -> bool:
    age_days = (now - published).total_seconds() / 86400.0
    return -1.0 <= age_days <= max_age_days


def parse_arxiv_xml(
    xml_text: str | bytes,
    default_topic: str = "ml_applied",
    now: datetime | None = None,
    max_age_days: float = 7.0,
    max_items: int = 15,
) -> list[TechCandidate]:
    """Interpreta la respuesta Atom XML de la API de arXiv y filtra a los últimos max_age_days."""
    now = now or datetime.now(UTC)
    root = ET.fromstring(xml_text)
    out: list[TechCandidate] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        title = strip_html(entry.findtext("atom:title", default="", namespaces=ATOM_NS))
        summary = strip_html(entry.findtext("atom:summary", default="", namespaces=ATOM_NS))
        link = entry.findtext("atom:id", default="", namespaces=ATOM_NS).strip()
        pub_raw = entry.findtext("atom:published", default="", namespaces=ATOM_NS).strip()
        if not title or not link or not pub_raw:
            continue
        try:
            published = datetime.fromisoformat(pub_raw.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            continue
        if not _within_window(published, now, max_age_days):
            continue
        authors = [
            strip_html(a.findtext("atom:name", default="", namespaces=ATOM_NS))
            for a in entry.findall("atom:author", ATOM_NS)
        ]
        cats = [c.attrib.get("term", "") for c in entry.findall("atom:category", ATOM_NS) if c.attrib.get("term")]
        out.append(
            TechCandidate(
                title=title,
                url=link,
                source="arXiv",
                kind="paper",
                published=published,
                abstract=summary,
                authors=[a for a in authors if a],
                categories=cats,
                default_topic=default_topic,
                language="en",
            )
        )
        if len(out) >= max_items:
            break
    return out


def fetch_arxiv(src_cfg: dict[str, Any], max_age_days: float = 7.0, max_per_source: int = 15) -> list[TechCandidate]:
    """Consulta la API oficial de arXiv para las categorías configuradas."""
    if not src_cfg.get("enabled", True):
        return []
    cats = src_cfg.get("categories") or ["cs.LG", "cs.AI", "cs.CL", "stat.ML", "math.OC", "cs.CY"]
    query = " OR ".join(f"cat:{c}" for c in cats)
    limit = min(int(src_cfg.get("max_results", 30)), max_per_source * 2)
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": limit,
    }
    resp = make_session().get(src_cfg.get("url", "https://export.arxiv.org/api/query"), params=params, timeout=20)
    resp.raise_for_status()
    return parse_arxiv_xml(
        resp.content,
        default_topic=src_cfg.get("topic", "ml_applied"),
        max_age_days=max_age_days,
        max_items=max_per_source,
    )


def parse_hf_daily_papers(
    rows: list[dict[str, Any]],
    default_topic: str = "small_models_edge",
    now: datetime | None = None,
    max_age_days: float = 7.0,
    max_items: int = 15,
) -> list[TechCandidate]:
    """Interpreta la respuesta JSON de Hugging Face Daily Papers filtrada por ventana de tiempo."""
    now = now or datetime.now(UTC)
    out: list[TechCandidate] = []
    for row in rows:
        paper = row.get("paper") or row
        pid = paper.get("id") or ""
        title = strip_html(paper.get("title") or row.get("title") or "")
        summary = strip_html(paper.get("summary") or row.get("summary") or "")
        pub_raw = paper.get("publishedAt") or row.get("publishedAt") or ""
        if not title or not pid:
            continue
        try:
            published = datetime.fromisoformat(pub_raw.replace("Z", "+00:00")).astimezone(UTC) if pub_raw else now
        except ValueError:
            published = now
        if not _within_window(published, now, max_age_days):
            continue
        upvotes = int(paper.get("upvotes") or row.get("numComments") or 0)
        authors = [a.get("name", "") for a in (paper.get("authors") or []) if isinstance(a, dict) and a.get("name")]
        out.append(
            TechCandidate(
                title=title,
                url=f"https://huggingface.co/papers/{pid}",
                source="Hugging Face Papers",
                kind="paper",
                published=published,
                abstract=summary,
                authors=authors,
                default_topic=default_topic,
                language="en",
                votes=upvotes,
            )
        )
        if len(out) >= max_items:
            break
    return out


def fetch_hf_daily_papers(
    src_cfg: dict[str, Any],
    max_age_days: float = 7.0,
    max_per_source: int = 15,
) -> list[TechCandidate]:
    if not src_cfg.get("enabled", True):
        return []
    url = src_cfg.get("url", "https://huggingface.co/api/daily_papers")
    limit = min(int(src_cfg.get("max_results", 20)), max_per_source)
    data = get_json(url, {"limit": limit}, timeout=20)
    if not isinstance(data, list):
        return []
    return parse_hf_daily_papers(
        data,
        default_topic=src_cfg.get("topic", "small_models_edge"),
        max_age_days=max_age_days,
        max_items=max_per_source,
    )


def parse_semantic_scholar(
    data: dict[str, Any],
    default_topic: str = "ml_applied",
    now: datetime | None = None,
    max_age_days: float = 7.0,
    max_items: int = 15,
) -> list[TechCandidate]:
    """Interpreta resultados de búsqueda de Semantic Scholar filtrados a los últimos max_age_days."""
    now = now or datetime.now(UTC)
    out: list[TechCandidate] = []
    for item in data.get("data") or []:
        title = strip_html(item.get("title") or "")
        paper_id = item.get("paperId")
        fallback_url = f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else ""
        url = item.get("url") or fallback_url
        if not title or not url:
            continue
        pub_raw = item.get("publicationDate") or ""
        if not pub_raw:
            continue
        try:
            published = datetime.fromisoformat(f"{pub_raw}T00:00:00+00:00")
        except ValueError:
            continue
        if not _within_window(published, now, max_age_days):
            continue
        authors = [a.get("name", "") for a in (item.get("authors") or []) if isinstance(a, dict) and a.get("name")]
        out.append(
            TechCandidate(
                title=title,
                url=url,
                source="Semantic Scholar",
                kind="paper",
                published=published,
                abstract=strip_html(item.get("abstract") or ""),
                authors=authors,
                default_topic=default_topic,
                language="en",
                citations=int(item.get("citationCount") or 0),
            )
        )
        if len(out) >= max_items:
            break
    return out


def fetch_semantic_scholar(
    src_cfg: dict[str, Any],
    max_age_days: float = 7.0,
    max_per_source: int = 15,
    api_key: str | None = None,
) -> list[TechCandidate]:
    """Consulta Semantic Scholar con reintento/backoff y S2_API_KEY opcional.

    Si todas las consultas fallan (p. ej. HTTP 429) y obtiene 0 resultados, lanza excepción
    para que la fuente se registre como fallida en lugar de devolver 0 silenciosamente.
    """
    if not src_cfg.get("enabled", True):
        return []
    base_url = src_cfg.get("url", "https://api.semanticscholar.org/graph/v1/paper/search")
    queries = src_cfg.get("queries") or ["small language model edge quantization"]
    year = datetime.now(UTC).year
    limit_per_q = max(3, min(max_per_source, int(src_cfg.get("max_results", 12))) // max(1, len(queries)))
    out: list[TechCandidate] = []
    sess = make_session(retries=2, backoff=1.0)
    if api_key:
        sess.headers.update({"x-api-key": api_key})
    errors: list[str] = []
    for q in queries:
        try:
            data = get_json(
                base_url,
                {
                    "query": q,
                    "limit": limit_per_q,
                    "year": f"{year}",
                    "fields": "title,abstract,url,publicationDate,citationCount,authors",
                },
                timeout=15,
                session=sess,
            )
            out.extend(
                parse_semantic_scholar(
                    data,
                    default_topic=src_cfg.get("topic", "ml_applied"),
                    max_age_days=max_age_days,
                    max_items=max_per_source,
                )
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            log.warning("Semantic Scholar falló para query %r: %s", q, exc)
    if not out and errors:
        raise RuntimeError(f"Semantic Scholar sin resultados por fallo/rate-limit ({errors[0][:120]})")
    return out[:max_per_source]


def _classify_hn_or_rss_kind(title: str, url: str, summary: str = "") -> str:
    """Clasifica como 'tool' solo repositorios, paquetes o herramientas explícitas (no blogs)."""
    url_low = url.lower()
    title_low = title.lower()
    summary_low = summary.lower()
    repo_hosts = ("github.com/", "gitlab.com/", "pypi.org/project/", "crates.io/crates/", "huggingface.co/spaces/")
    if any(h in url_low for h in repo_hosts) and not url_low.endswith(".io/"):
        return "tool"
    if title_low.startswith(("show hn:", "release:")):
        return "tool"
    if any(h in summary_low for h in ("github.com/", "pip install", "cargo install", "npm install")):
        return "tool"
    explicit_tool = ("open-source tool", "open source tool", "cli tool", "python library", "rust library", "plugin for")
    if any(m in f"{title_low} {summary_low}" for m in explicit_tool):
        return "tool"
    return "news"


def parse_hn_item(
    item: dict[str, Any],
    default_topic: str = "software_engineering",
    now: datetime | None = None,
    max_age_days: float = 7.0,
) -> TechCandidate | None:
    if not item or item.get("type") != "story" or item.get("dead") or item.get("deleted"):
        return None
    title = strip_html(item.get("title") or "")
    url = item.get("url") or f"https://news.ycombinator.com/item?id={item.get('id')}"
    if not title or not url:
        return None
    now = now or datetime.now(UTC)
    ts = int(item.get("time") or 0)
    published = datetime.fromtimestamp(ts, tz=UTC) if ts > 0 else now
    if not _within_window(published, now, max_age_days):
        return None
    text = strip_html(item.get("text") or "")
    if "arxiv.org/abs/" in url.lower():
        kind = "paper"
    else:
        kind = _classify_hn_or_rss_kind(title, url, text)
    return TechCandidate(
        title=title,
        url=url,
        source="Hacker News",
        kind=kind,
        published=published,
        abstract=text,
        default_topic=default_topic,
        language="en",
        votes=int(item.get("score") or 0),
    )


def fetch_hacker_news(
    src_cfg: dict[str, Any],
    max_age_days: float = 7.0,
    max_per_source: int = 15,
) -> list[TechCandidate]:
    if not src_cfg.get("enabled", True):
        return []
    base = src_cfg.get("url", "https://hacker-news.firebaseio.com/v0").rstrip("/")
    max_items = min(int(src_cfg.get("max_items", 25)), max_per_source * 2)
    sess = make_session(retries=2, backoff=0.5)
    ids = get_json(f"{base}/topstories.json", timeout=12, session=sess)
    if not isinstance(ids, list):
        return []
    top_ids = ids[:max_items]
    tasks = {
        str(i): (lambda item_id=i: get_json(f"{base}/item/{item_id}.json", timeout=10, session=sess))
        for i in top_ids
    }
    raw_items = run_parallel(tasks, max_workers=8)
    out: list[TechCandidate] = []
    now = datetime.now(UTC)
    for raw in raw_items.values():
        if isinstance(raw, dict):
            cand = parse_hn_item(
                raw,
                default_topic=src_cfg.get("topic", "software_engineering"),
                now=now,
                max_age_days=max_age_days,
            )
            if cand:
                out.append(cand)
                if len(out) >= max_per_source:
                    break
    return out


def parse_tech_rss(
    content: bytes | str,
    feed_cfg: dict[str, Any],
    now: datetime | None = None,
    max_age_days: float = 7.0,
    max_items: int = 15,
) -> list[TechCandidate]:
    """Interpreta un feed RSS/Atom técnico filtrando a los últimos max_age_days y max_items."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=max_age_days)
    parsed = feedparser.parse(content)
    out: list[TechCandidate] = []
    for entry in parsed.entries:
        title = strip_html(entry.get("title", ""))
        link = entry.get("link", "")
        when = _entry_datetime(entry)
        if not title or not link or when is None:
            continue
        if not (cutoff <= when <= now + timedelta(days=1)):
            continue
        summary = strip_html(entry.get("summary", "") or entry.get("description", ""))
        kind = _classify_hn_or_rss_kind(title, link, summary)
        out.append(
            TechCandidate(
                title=title,
                url=link,
                source=feed_cfg["name"],
                kind=kind,
                published=when,
                abstract=summary,
                default_topic=feed_cfg.get("topic", "ml_applied"),
                language=feed_cfg.get("language", "en"),
            )
        )
        if len(out) >= max_items:
            break
    return out


def fetch_tech_rss(
    feed_cfg: dict[str, Any],
    max_age_days: float = 7.0,
    max_per_source: int = 15,
) -> list[TechCandidate]:
    resp = make_session().get(feed_cfg["url"], timeout=15)
    resp.raise_for_status()
    return parse_tech_rss(resp.content, feed_cfg, max_age_days=max_age_days, max_items=max_per_source)


def gather_tech_candidates(cfg: Config) -> list[TechCandidate]:
    """Consulta en paralelo las fuentes habilitadas del Radar semanal (filtradas a 7 días)."""
    wcfg = cfg.section("weekly_digest")
    max_age_days = float(wcfg.get("max_age_days", 7.0))
    max_per_source = int(wcfg.get("max_per_source", 15))
    sources_cfg = wcfg.get("sources") or {}
    tasks: dict[str, Any] = {}

    if (sources_cfg.get("arxiv") or {}).get("enabled", True):
        tasks["arXiv"] = lambda: fetch_arxiv(sources_cfg.get("arxiv") or {}, max_age_days, max_per_source)
    if (sources_cfg.get("huggingface_papers") or {}).get("enabled", True):
        tasks["HuggingFace Papers"] = lambda: fetch_hf_daily_papers(
            sources_cfg.get("huggingface_papers") or {}, max_age_days, max_per_source
        )
    if (sources_cfg.get("semantic_scholar") or {}).get("enabled", True):
        tasks["Semantic Scholar"] = lambda: fetch_semantic_scholar(
            sources_cfg.get("semantic_scholar") or {},
            max_age_days,
            max_per_source,
            api_key=cfg.secrets.s2_api_key,
        )
    if (sources_cfg.get("hacker_news") or {}).get("enabled", True):
        tasks["Hacker News"] = lambda: fetch_hacker_news(
            sources_cfg.get("hacker_news") or {}, max_age_days, max_per_source
        )

    for feed in sources_cfg.get("rss_feeds") or []:
        if feed.get("enabled", True):
            tasks[f"RSS:{feed['name']}"] = lambda f=feed: fetch_tech_rss(f, max_age_days, max_per_source)

    results = run_parallel(tasks, max_workers=8)
    failed = [name for name, res in results.items() if res is None]
    if failed:
        log.warning("Fuentes del Radar semanal sin respuesta: %s", ", ".join(failed))

    per_source = {name: len(res) for name, res in results.items() if res is not None}
    all_items = [item for res in results.values() for item in (res or [])]
    log.info(
        "Radar semanal — candidatos recientes (<=%.0fd, máx %d/fuente): %s (total=%d, fallidas=%d)",
        max_age_days,
        max_per_source,
        ", ".join(f"{k}: {v}" for k, v in per_source.items()) or "ninguna",
        len(all_items),
        len(failed),
    )
    return all_items
