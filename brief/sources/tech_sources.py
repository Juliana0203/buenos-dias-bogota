"""Fuentes para el Radar semanal de tecnología: arXiv, Hugging Face Daily Papers, Semantic Scholar, HN y RSS."""

from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    matched_topic: str = "ml_applied"

    @property
    def short_id(self) -> str:
        digest = hashlib.sha1(self.url.encode("utf-8")).hexdigest()[:8]
        return f"p_{digest}"


def parse_arxiv_xml(xml_text: str | bytes, default_topic: str = "ml_applied") -> list[TechCandidate]:
    """Interpreta la respuesta Atom XML de la API de arXiv."""
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
    return out


def fetch_arxiv(src_cfg: dict[str, Any]) -> list[TechCandidate]:
    """Consulta la API oficial de arXiv para las categorías configuradas."""
    if not src_cfg.get("enabled", True):
        return []
    cats = src_cfg.get("categories") or ["cs.LG", "cs.AI", "cs.CL", "stat.ML", "math.OC", "cs.CY"]
    query = " OR ".join(f"cat:{c}" for c in cats)
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": int(src_cfg.get("max_results", 30)),
    }
    resp = make_session().get(src_cfg.get("url", "https://export.arxiv.org/api/query"), params=params, timeout=20)
    resp.raise_for_status()
    return parse_arxiv_xml(resp.content, default_topic=src_cfg.get("topic", "ml_applied"))


def parse_hf_daily_papers(rows: list[dict[str, Any]], default_topic: str = "small_models_edge") -> list[TechCandidate]:
    """Interpreta la respuesta JSON de Hugging Face Daily Papers."""
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
            published = datetime.fromisoformat(pub_raw.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            published = datetime.now(UTC)
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
    return out


def fetch_hf_daily_papers(src_cfg: dict[str, Any]) -> list[TechCandidate]:
    if not src_cfg.get("enabled", True):
        return []
    url = src_cfg.get("url", "https://huggingface.co/api/daily_papers")
    data = get_json(url, {"limit": int(src_cfg.get("max_results", 20))}, timeout=20)
    if not isinstance(data, list):
        return []
    return parse_hf_daily_papers(data, default_topic=src_cfg.get("topic", "small_models_edge"))


def parse_semantic_scholar(data: dict[str, Any], default_topic: str = "ml_applied") -> list[TechCandidate]:
    """Interpreta resultados de búsqueda de Semantic Scholar."""
    out: list[TechCandidate] = []
    for item in data.get("data") or []:
        title = strip_html(item.get("title") or "")
        paper_id = item.get("paperId")
        fallback_url = f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else ""
        url = item.get("url") or fallback_url
        if not title or not url:
            continue
        pub_raw = item.get("publicationDate") or ""
        try:
            published = datetime.fromisoformat(f"{pub_raw}T00:00:00+00:00") if pub_raw else datetime.now(UTC)
        except ValueError:
            published = datetime.now(UTC)
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
    return out


def fetch_semantic_scholar(src_cfg: dict[str, Any]) -> list[TechCandidate]:
    if not src_cfg.get("enabled", True):
        return []
    base_url = src_cfg.get("url", "https://api.semanticscholar.org/graph/v1/paper/search")
    queries = src_cfg.get("queries") or ["small language model edge quantization"]
    year = datetime.now(UTC).year
    limit_per_q = max(3, int(src_cfg.get("max_results", 12)) // max(1, len(queries)))
    out: list[TechCandidate] = []
    sess = make_session(retries=1, backoff=0.5)
    for q in queries:
        try:
            data = get_json(
                base_url,
                {
                    "query": q,
                    "limit": limit_per_q,
                    "year": f"{year - 1}-{year}",
                    "fields": "title,abstract,url,publicationDate,citationCount,authors",
                },
                timeout=12,
                session=sess,
            )
            out.extend(parse_semantic_scholar(data, default_topic=src_cfg.get("topic", "ml_applied")))
        except Exception as exc:  # noqa: BLE001
            log.warning("Semantic Scholar falló para query %r: %s", q, exc)
    return out


def _classify_hn_or_rss_kind(title: str, url: str, summary: str = "") -> str:
    text = f"{title} {url} {summary}".lower()
    tool_markers = (
        "github.com/",
        "gitlab.com/",
        "pypi.org/",
        "crates.io/",
        "show hn:",
        "open-source",
        "open source",
        "library",
        "cli ",
        "framework",
        "sdk",
        "tool",
        "repo",
        "v0.",
        "v1.",
        "release",
    )
    if any(m in text for m in tool_markers):
        return "tool"
    return "news"


def parse_hn_item(item: dict[str, Any], default_topic: str = "software_engineering") -> TechCandidate | None:
    if not item or item.get("type") != "story" or item.get("dead") or item.get("deleted"):
        return None
    title = strip_html(item.get("title") or "")
    url = item.get("url") or f"https://news.ycombinator.com/item?id={item.get('id')}"
    if not title or not url:
        return None
    ts = int(item.get("time") or 0)
    published = datetime.fromtimestamp(ts, tz=UTC) if ts > 0 else datetime.now(UTC)
    text = strip_html(item.get("text") or "")
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


def fetch_hacker_news(src_cfg: dict[str, Any]) -> list[TechCandidate]:
    if not src_cfg.get("enabled", True):
        return []
    base = src_cfg.get("url", "https://hacker-news.firebaseio.com/v0").rstrip("/")
    max_items = int(src_cfg.get("max_items", 20))
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
    for raw in raw_items.values():
        if isinstance(raw, dict):
            cand = parse_hn_item(raw, default_topic=src_cfg.get("topic", "software_engineering"))
            if cand:
                out.append(cand)
    return out


def parse_tech_rss(content: bytes | str, feed_cfg: dict[str, Any]) -> list[TechCandidate]:
    parsed = feedparser.parse(content)
    out: list[TechCandidate] = []
    for entry in parsed.entries:
        title = strip_html(entry.get("title", ""))
        link = entry.get("link", "")
        when = _entry_datetime(entry)
        if not title or not link or when is None:
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
    return out


def fetch_tech_rss(feed_cfg: dict[str, Any]) -> list[TechCandidate]:
    resp = make_session().get(feed_cfg["url"], timeout=15)
    resp.raise_for_status()
    items = parse_tech_rss(resp.content, feed_cfg)
    if not items:
        raise ValueError("el feed técnico no devolvió entradas válidas")
    return items


def gather_tech_candidates(cfg: Config) -> list[TechCandidate]:
    """Consulta en paralelo todas las fuentes habilitadas del Radar semanal y registra conteos y fallos."""
    wcfg = cfg.section("weekly_digest")
    sources_cfg = wcfg.get("sources") or {}
    tasks: dict[str, Any] = {}

    if (sources_cfg.get("arxiv") or {}).get("enabled", True):
        tasks["arXiv"] = lambda: fetch_arxiv(sources_cfg.get("arxiv") or {})
    if (sources_cfg.get("huggingface_papers") or {}).get("enabled", True):
        tasks["HuggingFace Papers"] = lambda: fetch_hf_daily_papers(sources_cfg.get("huggingface_papers") or {})
    if (sources_cfg.get("semantic_scholar") or {}).get("enabled", True):
        tasks["Semantic Scholar"] = lambda: fetch_semantic_scholar(sources_cfg.get("semantic_scholar") or {})
    if (sources_cfg.get("hacker_news") or {}).get("enabled", True):
        tasks["Hacker News"] = lambda: fetch_hacker_news(sources_cfg.get("hacker_news") or {})

    for feed in sources_cfg.get("rss_feeds") or []:
        if feed.get("enabled", True):
            tasks[f"RSS:{feed['name']}"] = lambda f=feed: fetch_tech_rss(f)

    results = run_parallel(tasks, max_workers=8)
    failed = [name for name, res in results.items() if res is None]
    if failed:
        log.warning("Fuentes del Radar semanal sin respuesta: %s", ", ".join(failed))

    per_source = {name: len(res) for name, res in results.items() if res is not None}
    all_items = [item for res in results.values() for item in (res or [])]
    log.info(
        "Radar semanal — candidatos leídos por fuente: %s (total=%d, fallidas=%d)",
        ", ".join(f"{k}: {v}" for k, v in per_source.items()) or "ninguna",
        len(all_items),
        len(failed),
    )
    return all_items
