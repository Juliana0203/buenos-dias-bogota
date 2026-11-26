"""Curaduría, enriquecimiento, formato rotativo y archivo público del Radar semanal de tecnología (Bloques B y C)."""

from __future__ import annotations

import argparse
import html
import json
import logging
import math
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from brief import history
from brief.config import ROOT, Config, load_config
from brief.feedback import (
    FEEDBACK_JSON,
    build_piece_keyboard,
    effective_topic_weights,
    load_feedback,
    register_pieces_in_catalog,
    sync_telegram_feedback,
)
from brief.formatter import MAX_LEN, esc
from brief.http import make_session
from brief.sources.good_news import looks_spanish
from brief.sources.news import normalize, strip_html
from brief.sources.tech_sources import TechCandidate, gather_tech_candidates
from brief.telegram import send_messages, send_quiz_poll

log = logging.getLogger(__name__)

DIGESTS_DIR = ROOT / "digests"
DOCS_DIR = ROOT / "docs"
SENT_JSON = history.SENT_JSON

ROLE_ACCESSIBLE = "Paper accesible"
ROLE_DEEP = "Paper profundo"
ROLE_APPLIED = "Paper aplicado"
ROLE_NEWS = "Noticia / Lanzamiento"
ROLE_TOOL = "Herramienta / Repo"
ROLE_GEM = "Joya de la semana"

FORMAT_NAMES = ["ranking_top5", "tres_cambios", "pregunta_semana"]

DEEP_MARKERS = (
    "theorem",
    "proof",
    "convergence",
    "bound",
    "stochastic",
    "bayesian",
    "markov",
    "convex",
    "variational",
    "gradient",
    "optimal",
    "asymptotic",
    "theoretical",
    "formulation",
)
APPLIED_MARKERS = (
    "benchmark",
    "production",
    "deployment",
    "quantization",
    "edge",
    "on-device",
    "latency",
    "real-world",
    "system",
    "fine-tuning",
    "pipeline",
    "dataset",
    "evaluation",
    "empirical",
    "classroom",
    "tabular",
)

DEFAULT_OFF_TOPIC = (
    "quantum gravity",
    "null cone",
    "astrophysics",
    "cosmology",
    "general relativity",
    "particle physics",
    "string theory",
    "black hole",
    "superconductor",
    "hadron",
)

CONCEPT_GLOSSARY: list[dict[str, Any]] = [
    {
        "triggers": ["quantization", "cuantizacion", "gguf", "bitnet", "int4", "int8", "slm", "on-device"],
        "term": "Cuantización de modelos (Quantization)",
        "explanation": (
            "Consiste en reducir la precisión numérica de los pesos de una red neuronal (por ejemplo, de 16 bits "
            "a 4 bits) para que ocupe hasta cuatro veces menos memoria y corra sin GPU dedicada."
        ),
        "everyday_example": (
            "Es como guardar una foto en JPEG de alta calidad en vez de RAW sin comprimir: a simple vista se ve "
            "casi idéntica, pero cabe en el teléfono y abre al instante."
        ),
    },
    {
        "triggers": ["rag", "retrieval", "embedding", "vector", "corpus"],
        "term": "Generación aumentada por recuperación (RAG)",
        "explanation": (
            "Antes de responder, el modelo busca fragmentos relevantes en una base de documentos verificados "
            "y los usa como contexto explícito para fundamentar la respuesta."
        ),
        "everyday_example": (
            "Es la diferencia entre presentar un examen de memoria y presentarlo a libro abierto consultando "
            "el capítulo exacto antes de escribir."
        ),
    },
    {
        "triggers": [
            "bayesian",
            "stochastic",
            "markov",
            "monte carlo",
            "optimization",
            "convex",
            "variational",
            "regime",
        ],
        "term": "Optimización estocástica y modelos de cambio de régimen",
        "explanation": (
            "Modela procesos con incertidumbre o estados ocultos estimando la dirección de mejora o el estado "
            "actual mediante muestras y ventanas deslizantes."
        ),
        "everyday_example": (
            "Es como estimar el tráfico de una avenida mirando la velocidad de los últimos 5 minutos en vez "
            "de esperar el promedio de todo el día."
        ),
    },
    {
        "triggers": ["distillation", "destilacion", "lora", "qlora", "fine-tuning", "adapter"],
        "term": "Destilación de conocimiento y adaptadores ligeros (LoRA)",
        "explanation": (
            "Permite transferir el comportamiento de un modelo gigante a uno compacto o ajustar solo una "
            "fracción mínima de parámetros para una tarea específica."
        ),
        "everyday_example": (
            "Es como cuando un especialista resume años de experiencia en una guía práctica de diez páginas "
            "para que un aprendiz resuelva un caso concreto rápido."
        ),
    },
    {
        "triggers": ["context", "agent", "compaction", "memory", "transformer", "attention"],
        "term": "Gestión y compactación de ventana de contexto",
        "explanation": (
            "Los modelos y agentes tienen un límite de tokens activos; compactar el contexto resume o descarta "
            "pasos intermedios para mantener solo las señales útiles en tareas largas."
        ),
        "everyday_example": (
            "Es como limpiar el escritorio al terminar un capítulo y dejar solo una ficha con las 3 conclusiones "
            "clave antes de empezar el siguiente."
        ),
    },
    {
        "triggers": ["postgres", "sqlite", "tabular", "table", "sql", "database", "schema"],
        "term": "Motores tabulares embebidos y esquemas tipados",
        "explanation": (
            "Ejecutan consultas analíticas o relacionales directamente dentro del proceso de la aplicación "
            "o sobre un único ejecutable sin levantar un servidor externo."
        ),
        "everyday_example": (
            "Es como llevar una navaja suiza en el bolsillo en lugar de transportar una caja de herramientas "
            "industrial para ajustar un tornillo."
        ),
    },
]


@dataclass
class DigestPiece:
    """Pieza curada lista para el Radar semanal (conserva el título original intacto)."""

    id: str
    role: str
    kind: str  # "paper" | "news" | "tool"
    topic: str
    topic_label: str
    original_title: str
    title_es: str  # igual a original_title sin LLM; traducción al español si hubo LLM
    hook: str
    summary_es: str
    reading_minutes: int
    url: str
    source: str
    score: float
    language_tag: str = "EN"
    outside_radar: bool = False
    project_line: str = ""


@dataclass
class QuizSpec:
    question: str
    options: list[str]
    correct_option_id: int
    explanation: str


@dataclass
class WeeklyDigest:
    week_id: str  # "AAAA-Wnn"
    year: int
    week_num: int
    generated_date: date
    format_name: str
    story_of_the_week: str
    pieces: list[DigestPiece]
    concept: dict[str, Any]
    challenge: str
    quiz: QuizSpec
    used_llm: bool = False
    stats: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def iso_week_id(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def parse_week_id(week_str: str) -> tuple[int, int, date]:
    """Convierte 'AAAA-Wnn' en (año, semana, domingo_de_esa_semana)."""
    m = re.fullmatch(r"(\d{4})-W(\d{2})", week_str.strip())
    if not m:
        raise ValueError(f"--week inválido ({week_str!r}): usa el formato AAAA-Wnn (p. ej. 2026-W48)")
    year, week = int(m.group(1)), int(m.group(2))
    if not (1 <= week <= 53):
        raise ValueError(f"Semana fuera de rango en {week_str!r}")
    sunday = date.fromisocalendar(year, week, 7)
    return year, week, sunday


def is_off_topic(cand: TechCandidate, off_topic_kws: list[str] | tuple[str, ...] = DEFAULT_OFF_TOPIC) -> bool:
    """True si el candidato pertenece a física teórica, astrofísica u otro dominio fuera de alcance."""
    text = normalize(f"{cand.title} {cand.abstract} {' '.join(cand.categories)}")
    if any(re.search(rf"\b{re.escape(normalize(kw))}", text) for kw in off_topic_kws):
        return True
    # Si viene de arXiv y ninguna categoría es de computación, estadística o matemáticas de optimización
    if cand.categories:
        valid_prefixes = ("cs.", "stat.", "math.oc", "math.st", "math.pr", "eess.")
        if not any(c.lower().startswith(valid_prefixes) for c in cand.categories):
            return True
    return False


def match_interest_topic(
    cand: TechCandidate,
    interests_cfg: dict[str, Any],
    weights: dict[str, float],
) -> tuple[str, float, list[str]]:
    """Detecta el tema más afín por coincidencias reales de palabras clave (sin puntos gratis por default_topic)."""
    text = normalize(f"{cand.title} {cand.abstract} {' '.join(cand.categories)}")
    best_topic = cand.default_topic if cand.default_topic in interests_cfg else next(iter(interests_cfg), "ml_applied")
    best_score = 0.0
    best_kws: list[str] = []

    for topic, meta in interests_cfg.items():
        kws = meta.get("keywords", []) if isinstance(meta, dict) else []
        matched = [kw for kw in kws if re.search(rf"\b{re.escape(normalize(kw))}", text)]
        if not matched:
            continue
        w = float(weights.get(topic, 1.0))
        topic_score = len(matched) * w
        if topic_score > best_score:
            best_score = topic_score
            best_topic = topic
            best_kws = matched

    return best_topic, round(best_score, 3), best_kws


def score_candidate(
    cand: TechCandidate,
    interests_cfg: dict[str, Any],
    weights: dict[str, float],
    now: datetime,
    max_age_days: float = 7.0,
    min_affinity: float = 2.0,
    off_topic_kws: list[str] | tuple[str, ...] = DEFAULT_OFF_TOPIC,
) -> float:
    """Puntúa un candidato por afinidad real, novedad y calidad.

    Si está en dominios excluidos (p. ej. física de gravedad cuántica), devuelve 0.
    Si su afinidad está por debajo de ``min_affinity``, se marca ``outside_radar = True``
    (solo podrá competir como 'joya' fuera del radar habitual si tiene alta calidad).
    """
    age_days = (now - cand.published).total_seconds() / 86400.0
    if age_days > max_age_days or age_days < -1.0:
        return 0.0
    if is_off_topic(cand, off_topic_kws):
        return 0.0

    topic, affinity_pts, matched_kws = match_interest_topic(cand, interests_cfg, weights)
    cand.matched_topic = topic
    cand.affinity_score = affinity_pts
    cand.matched_keywords = matched_kws

    freshness = max(0.0, 1.0 - max(0.0, age_days) / max_age_days) * 2.0
    quality = 0.0
    if cand.votes > 0:
        quality += min(4.0, math.log1p(cand.votes) * 0.8)
    if cand.citations > 0:
        quality += min(3.5, math.log1p(cand.citations) * 0.9)
    trusted = ("arXiv", "Hugging Face Papers", "MIT Technology Review", "Google Research Blog", "Simon Willison")
    if cand.source in trusted:
        quality += 1.0

    if affinity_pts < min_affinity:
        cand.outside_radar = True
        # Solo es elegible como joya fuera del radar si tiene al menos 1 coincidencia o votos/fuente destacada
        if affinity_pts <= 0 and cand.votes < 30:
            cand.score = 0.0
            return 0.0
        cand.score = round(affinity_pts + freshness + quality, 3)
        return cand.score

    cand.outside_radar = False
    cand.score = round(affinity_pts + freshness + quality, 3)
    return cand.score


def dedupe_candidates(candidates: list[TechCandidate], threshold: float = 78.0) -> list[TechCandidate]:
    """Elimina duplicados por URL o similitud de título, conservando el de mayor puntaje."""
    kept: list[TechCandidate] = []
    norms: list[str] = []
    urls: set[str] = set()
    for cand in sorted(candidates, key=lambda c: (c.score, c.published), reverse=True):
        clean_url = cand.url.split("#")[0].rstrip("/")
        if clean_url in urls:
            continue
        norm = normalize(cand.title)
        if any(fuzz.token_set_ratio(norm, other) >= threshold for other in norms):
            continue
        kept.append(cand)
        norms.append(norm)
        urls.add(clean_url)
    return kept


def _paper_subrole(cand: TechCandidate) -> str:
    text = f"{cand.title} {cand.abstract}".lower()
    deep_hits = sum(1 for m in DEEP_MARKERS if m in text)
    app_hits = sum(1 for m in APPLIED_MARKERS if m in text)
    if deep_hits > app_hits and deep_hits >= 1:
        return ROLE_DEEP
    if app_hits >= 1:
        return ROLE_APPLIED
    return ROLE_ACCESSIBLE


def select_diverse_pieces(
    candidates: list[TechCandidate],
    max_pieces: int = 7,
) -> list[tuple[str, TechCandidate]]:
    """Selecciona hasta 7 piezas con diversidad de tema:
    - Las 6 primeras (3 papers, 2 noticias, 1 herramienta) deben cumplir el umbral de afinidad (not outside_radar).
    - Solo la 'joya' (ROLE_GEM) puede estar fuera del radar habitual (outside_radar=True).
    """
    used_urls: set[str] = set()
    topic_counts: dict[str, int] = {}
    selected: list[tuple[str, TechCandidate]] = []

    in_radar = [c for c in candidates if not c.outside_radar]

    def pick_best(pool: list[TechCandidate], prefer_outside_or_rare: bool = False) -> TechCandidate | None:
        avail = [c for c in pool if c.url not in used_urls]
        if not avail:
            return None

        def adjusted(c: TechCandidate) -> float:
            diversity_penalty = topic_counts.get(c.matched_topic, 0) * 1.8
            bonus = 0.0
            if prefer_outside_or_rare:
                if c.outside_radar:
                    bonus += 2.0
                elif c.matched_topic not in topic_counts:
                    bonus += 1.5
            return c.score - diversity_penalty + bonus

        best = max(avail, key=adjusted)
        used_urls.add(best.url)
        topic_counts[best.matched_topic] = topic_counts.get(best.matched_topic, 0) + 1
        return best

    papers = [c for c in in_radar if c.kind == "paper"]
    accessible_pool = [c for c in papers if _paper_subrole(c) == ROLE_ACCESSIBLE] or papers
    deep_pool = [c for c in papers if _paper_subrole(c) == ROLE_DEEP] or papers
    applied_pool = [c for c in papers if _paper_subrole(c) == ROLE_APPLIED] or papers

    for role, pool in (
        (ROLE_ACCESSIBLE, accessible_pool),
        (ROLE_DEEP, deep_pool),
        (ROLE_APPLIED, applied_pool),
    ):
        p = pick_best(pool) or pick_best(papers)
        if p:
            selected.append((role, p))

    news_pool = [c for c in in_radar if c.kind == "news"]
    for _ in range(2):
        n = pick_best(news_pool)
        if n:
            selected.append((ROLE_NEWS, n))

    tools_pool = [c for c in in_radar if c.kind == "tool"] or [
        c for c in candidates if c.kind == "tool" and c.url not in used_urls
    ]
    t = pick_best(tools_pool)
    if t:
        selected.append((ROLE_TOOL, t))

    # La joya puede venir tanto de fuera del radar habitual como de candidatos poco comunes dentro del radar
    gem_pool = [c for c in candidates if c.url not in used_urls]
    g = pick_best(gem_pool, prefer_outside_or_rare=True)
    if g:
        selected.append((ROLE_GEM, g))

    return selected[:max_pieces]


def _detect_lang_tag(cand: TechCandidate) -> str:
    if cand.language.lower().startswith("es"):
        return "ES"
    if looks_spanish(f"{cand.title} {cand.abstract}", ["the", "and", "with", "for", "from", "that", "this"]):
        return "ES"
    return "EN"


def _split_complete_sentences(text: str) -> list[str]:
    cleaned = strip_html(text or "").strip()
    if not cleaned:
        return []
    raw_parts = re.split(r"(?<=[.!?])\s+", cleaned)
    out: list[str] = []
    for part in raw_parts:
        s = part.strip()
        if not s:
            continue
        if s[-1] not in ".!?":
            s += "."
        out.append(s)
    return out


def _extractive_complete_summary(cand: TechCandidate, max_words: int = 40) -> str:
    """Resumen extractivo que siempre termina en una oración completa (<= max_words) y marca [EN] o [ES]."""
    lang = _detect_lang_tag(cand)
    prefix = f"[{lang}]"
    budget = max_words - 1  # reserva 1 palabra para [EN]/[ES]
    sentences = _split_complete_sentences(cand.abstract)

    if not sentences:
        meta: list[str] = [f"Publicado en {cand.source}"]
        if cand.authors:
            meta.append(f"por {cand.authors[0]}")
        if cand.votes:
            meta.append(f"({cand.votes} votos)")
        return f"{prefix} {' '.join(meta)}."

    # Busca una o más oraciones completas que quepan íntegras dentro del presupuesto
    acc: list[str] = []
    used = 0
    for sent in sentences:
        w_count = len(sent.split())
        if used + w_count <= budget:
            acc.append(sent)
            used += w_count
        elif not acc:
            # Si la primera oración supera el presupuesto, busca otra oración del abstract que sí quepa completa
            shorter = next((s for s in sentences[1:] if 5 <= len(s.split()) <= budget), None)
            if shorter:
                acc.append(shorter)
            else:
                # Si todas las oraciones superan budget, corta en una cláusula completa (; o ,) y cierra con punto
                clauses = re.split(r"[;:]\s+|,\s+", sent)
                clause_acc: list[str] = []
                c_words = 0
                for cl in clauses:
                    cw = len(cl.split())
                    if c_words + cw <= budget and cw > 0:
                        clause_acc.append(cl.strip())
                        c_words += cw
                    else:
                        break
                fallback_cl = " ".join(sent.split()[:budget]).rstrip(".,;:")
                text_cl = ", ".join(clause_acc).rstrip(".,;:") if clause_acc else fallback_cl
                acc.append(text_cl + ".")
            break
        else:
            break

    return f"{prefix} {' '.join(acc)}"


def _distinct_concrete_hook(role: str, cand: TechCandidate, topic_label: str) -> str:
    """Construye un gancho específico apoyado en los datos concretos del candidato (sin plantillas repetidas)."""
    sentences = _split_complete_sentences(cand.abstract)
    # Si hay una segunda oración corta en el abstract (distinta de la primera), úsala como dato de gancho
    if len(sentences) >= 2 and 5 <= len(sentences[1].split()) <= 24:
        return sentences[1]

    details: list[str] = []
    if cand.matched_keywords:
        kws = ", ".join(cand.matched_keywords[:3])
        details.append(f"aborda {kws}")
    if cand.categories:
        details.append(f"categorías {', '.join(cand.categories[:2])}")
    if cand.authors:
        lead = cand.authors[0] + (" et al." if len(cand.authors) > 1 else "")
        details.append(f"autores: {lead}")
    if cand.votes > 0:
        details.append(f"{cand.votes} puntos en {cand.source}")
    if cand.citations > 0:
        details.append(f"{cand.citations} citas en {cand.source}")

    if not details:
        details.append(f"publicación de {cand.source} del {cand.published.strftime('%Y-%m-%d')}")

    if role == ROLE_GEM and cand.outside_radar:
        return f"Hallazgo fuera de tu radar habitual ({ cand.source }): " + " · ".join(details) + "."
    return f"{role} en {topic_label.lower()} ({cand.source}): " + " · ".join(details) + "."


def _concrete_project_line(cand: TechCandidate, projects_cfg: dict[str, Any]) -> str:
    """Devuelve la conexión con un proyecto SOLO si hay coincidencia concreta de términos clave; si no, cadena vacía."""
    if cand.kind != "paper":
        return ""
    text = normalize(f"{cand.title} {cand.abstract}")
    best_proj = ""
    best_matches: list[str] = []

    for proj_name, meta in projects_cfg.items():
        if not isinstance(meta, dict):
            continue
        kws = meta.get("keywords") or []
        hits = [kw for kw in kws if re.search(rf"\b{re.escape(normalize(kw))}", text)]
        if len(hits) > len(best_matches):
            best_matches = hits
            best_proj = proj_name

    if not best_proj or not best_matches:
        return ""
    angle = (projects_cfg.get(best_proj) or {}).get("angle", "aplicación directa")
    matched_str = ", ".join(best_matches[:2])
    return f"Conexión con {best_proj} (por {matched_str}): aplicable a {angle}."


def _reading_minutes(cand: TechCandidate) -> int:
    if cand.kind == "paper":
        return 12
    if cand.kind == "tool":
        return 5
    words = len((cand.abstract or "").split())
    return max(3, min(8, 3 + words // 120))


def enrich_digest_batch_with_anthropic(
    selected: list[tuple[str, TechCandidate]],
    wcfg: dict[str, Any],
    api_key: str,
) -> dict[str, Any] | None:
    """Hace UNA sola llamada por digest con las piezas seleccionadas y valida el JSON de respuesta."""
    if not selected:
        return None
    interests = wcfg.get("interests") or {}
    max_words = int(wcfg.get("summary_max_words", 40))
    primary_model = str(wcfg.get("anthropic_model", "claude-haiku-5-5"))
    fallback_model = str(wcfg.get("anthropic_fallback_model", "claude-sonnet-5-5"))

    items_payload = []
    for idx, (role, cand) in enumerate(selected):
        t_meta = interests.get(cand.matched_topic) or {}
        items_payload.append(
            {
                "index": idx,
                "role": role,
                "topic": str(t_meta.get("label") or cand.matched_topic),
                "title": cand.title,
                "abstract": (cand.abstract or "")[:900],
                "kind": cand.kind,
            }
        )

    prompt = (
        "Eres un editor técnico riguroso. Basándote ÚNICAMENTE en el título y resumen original de cada pieza, "
        "sin inventar datos, cifras, métricas ni autores, devuelve EXCLUSIVAMENTE un objeto JSON válido con:\n"
        '- "story_of_the_week": una frase llamativa en español que sintetice las piezas reales de esta semana.\n'
        '- "pieces": una lista con un objeto por pieza en el mismo orden ("index"), cada uno con:\n'
        '  - "index": entero\n'
        '  - "title_es": traducción fiel del titular al español\n'
        '  - "hook": frase de gancho concreta apoyada en un dato explícito del resumen original\n'
        f'  - "summary_es": resumen propio en español de MÁXIMO {max_words} palabras (oraciones completas)\n'
        '  - "project_line": cadena conectando la idea con Aula Ya (edtech/tutoría), Table Master (tablas/datos) '
        "o PostMark (verificación/flujos editoriales) SOLO si la conexión es real y directa; si no aplica, null\n"
        '  - "reading_minutes": entero estimado de tiempo de lectura en minutos.\n\n'
        f"Piezas:\n{json.dumps(items_payload, ensure_ascii=False)}"
    )

    for model in (primary_model, fallback_model):
        try:
            resp = make_session(retries=1).post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 1800,
                    "temperature": 0.1,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            content = resp.json()["content"][0]["text"]
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if not match:
                raise ValueError("La respuesta del LLM no contiene un bloque JSON")
            parsed = json.loads(match.group(0))
            if not isinstance(parsed.get("pieces"), list) or len(parsed["pieces"]) != len(selected):
                raise ValueError("El JSON del LLM no devolvió la lista completa de piezas")
            return parsed
        except Exception as exc:  # noqa: BLE001
            log.warning("Llamada batch a Anthropic (%s) falló: %s", model, exc)
    log.info("Fallo JSON del LLM: se usa el modo extractivo sin LLM")
    return None


def build_digest_pieces(
    selected: list[tuple[str, TechCandidate]],
    wcfg: dict[str, Any],
    anthropic_key: str | None = None,
) -> tuple[list[DigestPiece], bool, str | None]:
    """Construye las piezas del digest conservando siempre el título original intacto."""
    interests = wcfg.get("interests") or {}
    projects_cfg = wcfg.get("projects") or {}
    max_words = int(wcfg.get("summary_max_words", 40))

    llm_batch: dict[str, Any] | None = None
    if anthropic_key:
        llm_batch = enrich_digest_batch_with_anthropic(selected, wcfg, anthropic_key)
    else:
        log.info("ANTHROPIC_API_KEY no configurada: usando modo extractivo sin LLM (<= %d palabras)", max_words)

    used_llm = llm_batch is not None
    llm_story = str(llm_batch.get("story_of_the_week") or "").strip() if llm_batch else None
    llm_pieces = (llm_batch or {}).get("pieces") or []

    pieces: list[DigestPiece] = []
    for idx, (role, cand) in enumerate(selected):
        t_meta = interests.get(cand.matched_topic) or {}
        topic_label = str(t_meta.get("label") or cand.matched_topic)
        lang_tag = _detect_lang_tag(cand)

        if used_llm and idx < len(llm_pieces) and isinstance(llm_pieces[idx], dict):
            item_llm = llm_pieces[idx]
            title_es = str(item_llm.get("title_es") or cand.title).strip()
            hook = str(item_llm.get("hook") or _distinct_concrete_hook(role, cand, topic_label)).strip()
            raw_sum = str(item_llm.get("summary_es") or "").strip()
            words = raw_sum.split()
            if len(words) > max_words:
                raw_sum = " ".join(words[:max_words]).rstrip(".,;:") + "."
            summary_es = raw_sum or _extractive_complete_summary(cand, max_words)
            raw_proj = item_llm.get("project_line")
            proj_line = str(raw_proj).strip() if raw_proj and str(raw_proj).lower() != "null" else ""
            read_min = int(item_llm.get("reading_minutes") or _reading_minutes(cand))
        else:
            # Sin LLM: título original intacto, sin etiquetas antepuestas ni traducciones parciales
            title_es = cand.title
            hook = _distinct_concrete_hook(role, cand, topic_label)
            summary_es = _extractive_complete_summary(cand, max_words)
            proj_line = _concrete_project_line(cand, projects_cfg)
            read_min = _reading_minutes(cand)

        pieces.append(
            DigestPiece(
                id=cand.short_id,
                role=role,
                kind=cand.kind,
                topic=cand.matched_topic,
                topic_label=topic_label,
                original_title=cand.title,
                title_es=title_es,
                hook=hook,
                summary_es=summary_es,
                reading_minutes=read_min,
                url=cand.url,
                source=cand.source,
                score=cand.score,
                language_tag=lang_tag,
                outside_radar=cand.outside_radar,
                project_line=proj_line if cand.kind == "paper" else "",
            )
        )
    return pieces, used_llm, llm_story


def pick_concept_in_60s(pieces: list[DigestPiece]) -> dict[str, Any]:
    """Busca un término técnico que aparezca realmente en las lecturas de la semana; si ninguno aplica, lo indica."""
    for entry in CONCEPT_GLOSSARY:
        for p in pieces:
            blob = normalize(f"{p.original_title} {p.summary_es} {p.hook}")
            for trig in entry["triggers"]:
                if re.search(rf"\b{re.escape(normalize(trig))}\b", blob):
                    return {
                        "term": entry["term"],
                        "explanation": entry["explanation"],
                        "everyday_example": entry["everyday_example"],
                        "from_readings": True,
                        "source_title": p.original_title,
                    }
    fallback = CONCEPT_GLOSSARY[0]
    return {
        "term": fallback["term"],
        "explanation": fallback["explanation"],
        "everyday_example": fallback["everyday_example"],
        "from_readings": False,
        "source_title": "",
    }


def build_weekly_challenge(pieces: list[DigestPiece]) -> str:
    """Reto ejecutable coherente con el tipo de pieza:
    - tool/repo -> probarla 20 minutos
    - paper -> leer abstract y conclusión y escribir 3 notas
    - news -> escribir cómo afecta a uno de tus proyectos
    Nunca 'clonar' un artículo.
    """
    tool = next((p for p in pieces if p.kind == "tool"), None)
    paper = next((p for p in pieces if p.kind == "paper"), None)
    news = next((p for p in pieces if p.kind == "news"), None)
    target = tool or paper or news
    if not target:
        return "Dedica 20 minutos a revisar tu biblioteca de lecturas guardadas en biblioteca/."
    if target.kind == "tool":
        return (
            f"Herramienta/repo (20 min): prueba «{target.original_title}» ({target.url}) en un ejemplo pequeño "
            "y anota si vale la pena integrarla en tu flujo."
        )
    if target.kind == "paper":
        return (
            f"Lectura enfocada (20 min): lee el abstract y la conclusión del paper «{target.original_title}» "
            "y escribe 3 notas concretas con lo que podrías experimentar."
        )
    return (
        f"Análisis rápido (15 min): revisa la noticia «{target.original_title}» "
        "y escribe en 3 líneas cómo afecta a Aula Ya, Table Master o PostMark."
    )


def build_weekly_quiz(pieces: list[DigestPiece], concept: dict[str, Any]) -> QuizSpec:
    """Construye un mini quiz de opción múltiple alineado con el concepto técnico de las lecturas."""
    term = str(concept["term"])
    if "Cuantización" in term:
        return QuizSpec(
            question=f"Según el concepto de la semana ({term}), ¿qué ventaja directa aporta bajar de FP16 a INT4?",
            options=[
                "Reduce memoria y acelera la inferencia local con pérdida mínima",
                "Duplica el número de parámetros entrenables del modelo",
                "Elimina la necesidad de tokenizar el texto de entrada",
                "Convierte un modelo autorregresivo en una base de datos SQL",
            ],
            correct_option_id=0,
            explanation=(
                "Al representar los pesos con 4 bits en vez de 16, el modelo ocupa ~4x menos RAM y corre en CPU/edge."
            ),
        )
    if "RAG" in term:
        return QuizSpec(
            question="¿Cuál es el propósito principal de usar RAG frente a consultar el modelo a secas?",
            options=[
                "Fundamentar la respuesta con fragmentos recuperados de documentos verificados",
                "Aumentar la temperatura de muestreo para generar textos más largos",
                "Reemplazar el optimizador durante el preentrenamiento",
                "Comprimir imágenes en formato vectorial sin pérdida",
            ],
            correct_option_id=0,
            explanation=(
                "RAG recupera pasajes relevantes antes de generar, reduciendo alucinaciones y citando fuentes."
            ),
        )
    if "Optimización estocástica" in term:
        return QuizSpec(
            question="¿Por qué la optimización estocástica y detección por ventana deslizante son útiles en línea?",
            options=[
                "Estiman el estado o gradiente con muestras recientes sin recalcular todo el historial",
                "Garantizan encontrar siempre el mínimo global exacto en un solo paso",
                "Eliminan la varianza de cualquier proceso estocástico",
                "Solo funcionan cuando los datos no cambian con el tiempo",
            ],
            correct_option_id=0,
            explanation="Las muestras locales o ventanas deslizantes permiten adaptarse rápido con bajo costo.",
        )
    if "contexto" in term.lower():
        return QuizSpec(
            question="¿Qué problema resuelve la compactación de ventana de contexto en agentes y LLMs?",
            options=[
                "Mantiene las señales clave sin saturar la memoria de tokens en tareas largas",
                "Aumenta el tamaño físico de la GPU durante la inferencia",
                "Elimina la necesidad de instrucciones del sistema",
                "Reemplaza los pesos del modelo por tablas hash",
            ],
            correct_option_id=0,
            explanation="Compactar o resumir pasos intermedios evita degradación y costos al llenarse la ventana.",
        )
    if "tabulares" in term.lower():
        return QuizSpec(
            question="¿Qué ventaja ofrece un motor relacional o tabular embebido en un único ejecutable?",
            options=[
                "Consultas SQL y validación local sin administrar un servidor externo",
                "Entrenamiento distribuido de redes convolucionales en clúster",
                "Renderizado 3D en tiempo real en el navegador",
                "Reemplazo automático de certificados TLS",
            ],
            correct_option_id=0,
            explanation="Un motor embebido simplifica despliegues, pruebas y pipelines de datos locales.",
        )
    return QuizSpec(
        question=f"¿Qué permite lograr «{term}» al adaptar modelos para una tarea específica?",
        options=[
            "Ajustar pocos parámetros o transferir capacidad a un modelo compacto y rápido",
            "Entrenar desde cero sin datos etiquetados ni función de pérdida",
            "Evitar el uso de tensores durante la inferencia",
            "Multiplicar por diez el tamaño del contexto sin usar memoria",
        ],
        correct_option_id=0,
        explanation=(
            "Los adaptadores ligeros y la destilación concentran capacidad en modelos pequeños de baja latencia."
        ),
    )


def choose_weekly_format(week_num: int, last_format_index: int | None = None) -> tuple[int, str]:
    """Rota entre los 3 formatos sin repetir el mismo dos domingos seguidos."""
    idx = week_num % len(FORMAT_NAMES)
    if last_format_index is not None and idx == last_format_index:
        idx = (idx + 1) % len(FORMAT_NAMES)
    return idx, FORMAT_NAMES[idx]


def build_story_of_the_week(pieces: list[DigestPiece], llm_story: str | None = None) -> str:
    """Con LLM: frase a partir de las piezas. Sin LLM: línea neutra con el tema dominante."""
    if not pieces:
        return "Semana con pocas publicaciones que superaran el umbral de afinidad; solo se incluye lo verificado."
    if llm_story:
        return llm_story
    counts: dict[str, int] = {}
    for p in pieces:
        counts[p.topic_label] = counts.get(p.topic_label, 0) + 1
    dominant = max(counts, key=lambda k: counts[k])
    return f"Tema dominante de la semana: {dominant} ({counts[dominant]} de {len(pieces)} piezas seleccionadas)."


def build_weekly_digest(
    cfg: Config,
    candidates: list[TechCandidate] | None = None,
    ref_date: date | None = None,
    week_override: str | None = None,
    feedback_path: Path = FEEDBACK_JSON,
    sent_path: Path = SENT_JSON,
) -> WeeklyDigest:
    """Ejecuta la tubería completa de curaduría para la semana indicada (sin escribir archivos)."""
    wcfg = cfg.section("weekly_digest")
    if week_override:
        year, week_num, target_date = parse_week_id(week_override)
        week_id = f"{year}-W{week_num:02d}"
    else:
        target_date = ref_date or datetime.now(cfg.tz).date()
        iso = target_date.isocalendar()
        year, week_num = iso.year, iso.week
        week_id = f"{year}-W{week_num:02d}"

    now_utc = datetime(target_date.year, target_date.month, target_date.day, 23, 59, tzinfo=UTC)
    raw_candidates = gather_tech_candidates(cfg) if candidates is None else list(candidates)

    state = load_feedback(feedback_path)
    sent_reg = history.read_sent_registry(sent_path)
    interests = wcfg.get("interests") or {}
    weights = effective_topic_weights(interests, state)
    read_ids = set(state.get("read_ids") or [])
    max_age_days = float(wcfg.get("max_age_days", 7.0))
    min_affinity = float(wcfg.get("min_affinity_score", 2.0))
    off_topic_kws = list(wcfg.get("off_topic_keywords") or DEFAULT_OFF_TOPIC)

    scored: list[TechCandidate] = []
    discarded_old_or_irrelevant = 0
    discarded_already_read = 0
    for cand in raw_candidates:
        if cand.short_id in read_ids:
            discarded_already_read += 1
            continue
        s = score_candidate(
            cand,
            interests,
            weights,
            now_utc,
            max_age_days=max_age_days,
            min_affinity=min_affinity,
            off_topic_kws=off_topic_kws,
        )
        if s > 0:
            scored.append(cand)
        else:
            discarded_old_or_irrelevant += 1

    deduped = dedupe_candidates(scored, float(wcfg.get("dedup_threshold", 78)))
    discarded_dups = len(scored) - len(deduped)
    selected = select_diverse_pieces(deduped, int(wcfg.get("max_pieces", 7)))

    log.info(
        "Curaduría Radar semanal (%s): leídos=%d, descartados_edad_o_afinidad=%d, ya_leídos=%d, "
        "descartados_duplicados=%d, elegidos=%d",
        week_id,
        len(raw_candidates),
        discarded_old_or_irrelevant,
        discarded_already_read,
        discarded_dups,
        len(selected),
    )

    pieces, used_llm, llm_story = build_digest_pieces(selected, wcfg, cfg.secrets.anthropic_key)
    last_fmt = sent_reg.get("last_weekly_format_index")
    if last_fmt is None:
        last_fmt = state.get("last_format_index")
    fmt_idx, fmt_name = choose_weekly_format(week_num, last_fmt)
    concept = pick_concept_in_60s(pieces)
    challenge = build_weekly_challenge(pieces)
    quiz = build_weekly_quiz(pieces, concept)
    story = build_story_of_the_week(pieces, llm_story=llm_story if used_llm else None)

    return WeeklyDigest(
        week_id=week_id,
        year=year,
        week_num=week_num,
        generated_date=target_date,
        format_name=fmt_name,
        story_of_the_week=story,
        pieces=pieces,
        concept=concept,
        challenge=challenge,
        quiz=quiz,
        used_llm=used_llm,
        stats={
            "read": len(raw_candidates),
            "discarded_age_or_topic": discarded_old_or_irrelevant,
            "discarded_already_read": discarded_already_read,
            "discarded_duplicates": discarded_dups,
            "selected": len(pieces),
            "format_index": fmt_idx,
        },
    )


def _format_piece_html(idx: int, p: DigestPiece, used_llm: bool = False) -> str:
    """Formatea una pieza conservando intacto el título original y poniendo la etiqueta de tema en línea propia."""
    link = html.escape(p.url, quote=True)
    radar_note = " · <i>fuera de tu radar habitual</i>" if p.outside_radar else ""
    head = (
        f'{idx}. <b>[{esc(p.role)}]</b> <a href="{link}">{esc(p.original_title)}</a> '
        f"<i>({p.reading_minutes} min · {esc(p.source)})</i>"
    )
    lines = [
        head,
        f"   🏷️ {esc(p.topic_label)}{radar_note}",
    ]
    if used_llm and p.title_es and p.title_es != p.original_title:
        lines.append(f"   🇪🇸 <i>{esc(p.title_es)}</i>")
    lines.append(f"   🎯 {esc(p.hook)}")
    lines.append(f"   {esc(p.summary_es)}")
    if p.project_line:
        lines.append(f"   🛠️ <i>{esc(p.project_line)}</i>")
    return "\n".join(lines)


def format_weekly_messages(
    digest: WeeklyDigest,
) -> tuple[list[str], list[dict[str, Any] | None], list[bool]]:
    """Construye los mensajes del Radar semanal:
    - Mensaje 1 (teaser pantalla de bloqueo): máx. 4 líneas (historia + 3 titulares), ÚNICO con sonido.
    - Mensaje 2 (silencioso, silent=True): papers de la semana con detalle y teclado inline.
    - Mensaje 3 (silencioso, silent=True): lanzamientos/herramienta/joya + Concepto en 60s + Reto de la semana.
    """
    headers = {
        "ranking_top5": "📡 <b>Radar semanal · Ranking</b>",
        "tres_cambios": "📡 <b>Radar semanal · Tres cambios</b>",
        "pregunta_semana": "📡 <b>Radar semanal · Pregunta clave</b>",
    }
    header = headers.get(digest.format_name, "📡 <b>Radar semanal</b>")

    if not digest.pieces:
        msg = (
            f"{header} ({esc(digest.week_id)}) — {esc(digest.story_of_the_week)}\n"
            "<i>Esta semana no hubo suficientes candidatos que superaran el umbral de afinidad.</i>"
        )
        return [msg[:MAX_LEN]], [None], [False]

    # Mensaje 1: Teaser de máximo 4 líneas (el único con sonido)
    teaser_lines = [f"{header} ({esc(digest.week_id)}) — {esc(digest.story_of_the_week)}"]
    for i, p in enumerate(digest.pieces[:3], 1):
        teaser_lines.append(f"{i}. {esc(p.original_title)} <i>({esc(p.source)})</i>")
    msg1 = "\n".join(teaser_lines[:4])[:MAX_LEN]
    kb1 = build_piece_keyboard(digest.pieces[0].id, digest.pieces[0].topic)

    paper_roles = (ROLE_ACCESSIBLE, ROLE_DEEP, ROLE_APPLIED)
    papers = [p for p in digest.pieces if p.role in paper_roles]
    others = [p for p in digest.pieces if p.role not in paper_roles]

    # Mensaje 2: Papers en detalle (silencioso)
    m2_lines = ["📄 <b>Papers de la semana</b>"]
    for i, p in enumerate(papers, 1):
        m2_lines.append("")
        m2_lines.append(_format_piece_html(i, p, used_llm=digest.used_llm))
    msg2 = "\n".join(m2_lines)[:MAX_LEN]
    kb2 = build_piece_keyboard(papers[0].id, papers[0].topic) if papers else None

    # Mensaje 3: Lanzamientos, herramienta, joya + Concepto en 60s + Reto (silencioso)
    m3_lines: list[str] = []
    if others:
        m3_lines.append("🚀 <b>Lanzamientos, herramienta y joya</b>")
        start_idx = len(papers) + 1
        for i, p in enumerate(others, start_idx):
            m3_lines.append("")
            m3_lines.append(_format_piece_html(i, p, used_llm=digest.used_llm))
        m3_lines.append("")

    c = digest.concept
    origin_note = (
        f" <i>(término presente en «{esc(c['source_title'])}»)</i>"
        if c.get("from_readings")
        else " <i>(concepto de la lista local — sin coincidencia directa esta semana)</i>"
    )
    m3_lines.extend(
        [
            f"🧠 <b>Concepto en 60 segundos: {esc(c['term'])}</b>{origin_note}",
            esc(c["explanation"]),
            f"<i>Ejemplo cotidiano:</i> {esc(c['everyday_example'])}",
            "",
            "⚡ <b>Reto de la semana (15-30 min)</b>",
            esc(digest.challenge),
        ]
    )
    msg3 = "\n".join(m3_lines)[:MAX_LEN]
    tail_piece = others[0] if others else papers[-1]
    kb3 = build_piece_keyboard(tail_piece.id, tail_piece.topic)

    if papers:
        return [msg1, msg2, msg3], [kb1, kb2, kb3], [False, True, True]
    return [msg1, msg3], [kb1, kb3], [False, True]


def render_digest_markdown(digest: WeeklyDigest) -> str:
    """Genera el Markdown limpio para digests/AAAA/AAAA-Wnn.md."""
    lines = [
        f"# Radar semanal de tecnología — {digest.week_id}",
        "",
        f"- **Fecha:** {digest.generated_date.isoformat()}",
        f"- **Formato:** `{digest.format_name}`",
        f"- **Historia de la semana:** {digest.story_of_the_week}",
        "",
        "## Lecturas seleccionadas",
        "",
    ]
    if not digest.pieces:
        lines.append("*Semana con contenido insuficiente verificado; se envió resumen corto.*")
        lines.append("")
    for i, p in enumerate(digest.pieces, 1):
        radar_tag = " *(fuera de tu radar habitual)*" if p.outside_radar else ""
        meta_line = (
            f"- **Tema:** {p.topic_label}{radar_tag} · **Fuente:** {p.source} "
            f"· **Lectura:** ~{p.reading_minutes} min"
        )
        lines.extend(
            [
                f"### {i}. [{p.role}] [{p.original_title}]({p.url})",
                meta_line,
            ]
        )
        if digest.used_llm and p.title_es and p.title_es != p.original_title:
            lines.append(f"- **Traducción:** {p.title_es}")
        lines.extend(
            [
                f"- **Gancho:** {p.hook}",
                f"- **Resumen:** {p.summary_es}",
            ]
        )
        if p.project_line:
            lines.append(f"- **Conexión con proyecto:** {p.project_line}")
        lines.append("")

    c = digest.concept
    origin = (
        f"Término detectado en «{c.get('source_title')}»."
        if c.get("from_readings")
        else "Concepto seleccionado de la lista local (ningún término del glosario apareció literalmente esta semana)."
    )
    lines.extend(
        [
            f"## Concepto en 60 segundos: {c['term']}",
            "",
            f"*{origin}*",
            "",
            c["explanation"],
            "",
            f"> **Ejemplo cotidiano:** {c['everyday_example']}",
            "",
            "## Reto de la semana (15-30 min)",
            "",
            digest.challenge,
            "",
            "## Mini quiz",
            "",
            f"**{digest.quiz.question}**",
            "",
        ]
    )
    for idx, opt in enumerate(digest.quiz.options):
        mark = "✅" if idx == digest.quiz.correct_option_id else "•"
        lines.append(f"- {mark} {opt}")
    lines.extend(["", f"*{digest.quiz.explanation}*", ""])
    return "\n".join(lines)


def write_digest_archive(
    digest: WeeklyDigest,
    digests_dir: Path = DIGESTS_DIR,
    docs_dir: Path = DOCS_DIR,
) -> Path:
    """Escribe digests/AAAA/AAAA-Wnn.md, actualiza digests/README.md y docs/digests.json (nunca en --dry-run)."""
    year_dir = digests_dir / str(digest.year)
    year_dir.mkdir(parents=True, exist_ok=True)
    md_path = year_dir / f"{digest.week_id}.md"
    md_path.write_text(render_digest_markdown(digest), encoding="utf-8")

    docs_dir.mkdir(parents=True, exist_ok=True)
    digests_json_path = docs_dir / "digests.json"
    entries: list[dict[str, Any]] = []
    if digests_json_path.exists():
        try:
            entries = json.loads(digests_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            entries = []
    entries = [e for e in entries if e.get("week_id") != digest.week_id]
    topics = sorted({p.topic_label for p in digest.pieces})
    entries.append(
        {
            "week_id": digest.week_id,
            "year": digest.year,
            "week_num": digest.week_num,
            "date": digest.generated_date.isoformat(),
            "format_name": digest.format_name,
            "story_of_the_week": digest.story_of_the_week,
            "topics": topics,
            "markdown_path": f"digests/{digest.year}/{digest.week_id}.md",
            "pieces": [
                {
                    "id": p.id,
                    "role": p.role,
                    "topic": p.topic,
                    "topic_label": p.topic_label,
                    "original_title": p.original_title,
                    "title_es": p.title_es,
                    "hook": p.hook,
                    "summary_es": p.summary_es,
                    "reading_minutes": p.reading_minutes,
                    "url": p.url,
                    "source": p.source,
                    "outside_radar": p.outside_radar,
                    "project_line": p.project_line,
                }
                for p in digest.pieces
            ],
            "concept": digest.concept,
            "challenge": digest.challenge,
        }
    )
    entries.sort(key=lambda e: e["week_id"], reverse=True)
    digests_json_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    idx_lines = [
        "# Archivo histórico del Radar semanal de tecnología",
        "",
        "Índice de ediciones semanales enviadas.",
        "",
        "| Semana | Fecha | Temas principales | Historia de la semana |",
        "|---|---|---|---|",
    ]
    for e in entries:
        w = e["week_id"]
        y = e["year"]
        t_str = ", ".join(e.get("topics") or []) or "—"
        story = str(e.get("story_of_the_week") or "").replace("|", "/")
        idx_lines.append(f"| [{w}]({y}/{w}.md) | {e['date']} | {t_str} | {story} |")
    idx_lines.append("")
    (digests_dir / "README.md").write_text("\n".join(idx_lines), encoding="utf-8")
    return md_path


def already_sent_this_week(week_id: str, sent_path: Path = SENT_JSON) -> bool:
    """Verifica en data/sent.json si la semana ya fue enviada realmente (nunca por existencia de archivo)."""
    reg = history.read_sent_registry(sent_path)
    return week_id in (reg.get("sent_weeks") or [])


def mark_week_sent(
    digest: WeeklyDigest,
    sent_path: Path = SENT_JSON,
    feedback_path: Path = FEEDBACK_JSON,
) -> None:
    """Registra el envío real en data/sent.json y cataloga las piezas en data/feedback.json."""
    reg = history.read_sent_registry(sent_path)
    sent_weeks = reg.setdefault("sent_weeks", [])
    if digest.week_id not in sent_weeks:
        sent_weeks.append(digest.week_id)
    reg["last_weekly_format_index"] = digest.stats.get("format_index", 0)
    history.write_sent_registry(reg, sent_path)

    register_pieces_in_catalog(
        [
            {
                "id": p.id,
                "title": p.original_title,
                "title_es": p.title_es,
                "url": p.url,
                "topic": p.topic,
                "topic_label": p.topic_label,
                "role": p.role,
                "hook": p.hook,
                "summary_es": p.summary_es,
                "week": digest.week_id,
                "date": digest.generated_date.isoformat(),
            }
            for p in digest.pieces
        ],
        path=feedback_path,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="weekly", description="Radar semanal de tecnología")
    parser.add_argument("--dry-run", action="store_true", help="imprime el digest sin enviar ni escribir archivos")
    parser.add_argument("--force", action="store_true", help="envía aunque ya se haya enviado esta semana")
    parser.add_argument("--as-of", default=None, metavar="HH:MM", help="hora simulada de ejecución")
    parser.add_argument("--week", default=None, metavar="AAAA-Wnn", help="genera o regenera una semana específica")
    parser.add_argument("--config", default=None, help="ruta alternativa a config.yaml")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    cfg = load_config(args.config)
    today = datetime.now(cfg.tz).date()
    target_week = args.week or iso_week_id(today)

    # Un --dry-run NUNCA escribe en data/, digests/ ni docs/, ni cuenta como enviado.
    if not args.dry_run:
        sync_telegram_feedback(cfg.secrets.telegram_token)

    if not args.dry_run and not args.force and already_sent_this_week(target_week):
        log.info("Ya se envió el Radar semanal de %s (según data/sent.json); no se duplica.", target_week)
        return 0

    if not args.dry_run and not (cfg.secrets.telegram_token and cfg.secrets.telegram_chat_id):
        log.error("Faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID")
        return 2

    digest = build_weekly_digest(cfg, ref_date=today, week_override=args.week)
    messages, markups, silent_flags = format_weekly_messages(digest)

    if args.dry_run:
        for i, (msg, is_silent) in enumerate(zip(messages, silent_flags, strict=False), 1):
            sound_label = (
                "SILENCIOSO (disable_notification=true)"
                if is_silent
                else "CON SONIDO (teaser pantalla bloqueo)"
            )
            lines_count = len(msg.splitlines())
            print(
                f"----- radar semanal mensaje {i}/{len(messages)} "
                f"[{sound_label}, {lines_count} líneas, {len(msg)} caracteres] -----"
            )
            print(msg)
        print("----- mini quiz (sendPoll, disable_notification=true) -----")
        print(f"Pregunta: {digest.quiz.question}")
        for idx, opt in enumerate(digest.quiz.options):
            mark = "✅" if idx == digest.quiz.correct_option_id else "  "
            print(f"  {mark} [{idx}] {opt}")
        print(f"Explicación: {digest.quiz.explanation}")
        return 0

    send_messages(
        cfg.secrets.telegram_token,  # type: ignore[arg-type]
        cfg.secrets.telegram_chat_id,  # type: ignore[arg-type]
        messages,
        disable_notification=silent_flags,
        reply_markups=markups,
    )
    try:
        send_quiz_poll(
            cfg.secrets.telegram_token,  # type: ignore[arg-type]
            cfg.secrets.telegram_chat_id,  # type: ignore[arg-type]
            digest.quiz.question,
            digest.quiz.options,
            digest.quiz.correct_option_id,
            digest.quiz.explanation,
            disable_notification=True,
        )
    except Exception:
        log.exception("El digest se envió, pero falló el envío del mini quiz sendPoll")

    mark_week_sent(digest)
    write_digest_archive(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
