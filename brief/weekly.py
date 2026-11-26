"""Curaduría, enriquecimiento, formato rotativo y archivo público del Radar semanal de tecnología (Fases 2 y 3)."""

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

from brief.config import ROOT, Config, load_config
from brief.feedback import (
    FEEDBACK_JSON,
    build_piece_keyboard,
    effective_topic_weights,
    load_feedback,
    register_pieces_in_catalog,
    save_feedback,
    sync_telegram_feedback,
)
from brief.formatter import MAX_LEN, esc
from brief.http import make_session
from brief.sources.news import normalize, strip_html
from brief.sources.tech_sources import TechCandidate, gather_tech_candidates
from brief.telegram import send_messages, send_quiz_poll

log = logging.getLogger(__name__)

DIGESTS_DIR = ROOT / "digests"
DOCS_DIR = ROOT / "docs"

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

EN_ES_TERMS: list[tuple[str, str]] = [
    ("small language models", "modelos de lenguaje pequeños"),
    ("small language model", "modelo de lenguaje pequeño"),
    ("large language models", "modelos de lenguaje grandes"),
    ("large language model", "modelo de lenguaje grande"),
    ("language models", "modelos de lenguaje"),
    ("language model", "modelo de lenguaje"),
    ("reinforcement learning", "aprendizaje por refuerzo"),
    ("machine learning", "aprendizaje automático"),
    ("deep learning", "aprendizaje profundo"),
    ("fine-tuning", "ajuste fino"),
    ("quantization", "cuantización"),
    ("distillation", "destilación"),
    ("open-source", "código abierto"),
    ("open source", "código abierto"),
    ("on-device", "en dispositivo"),
    ("benchmark", "evaluación comparativa"),
    ("survey", "revisión"),
    ("towards", "hacia"),
    ("efficient", "eficiente"),
    ("reasoning", "razonamiento"),
    ("retrieval", "recuperación"),
    ("optimization", "optimización"),
    ("education", "educación"),
    ("classroom", "aula"),
    ("students", "estudiantes"),
    ("tabular", "tabular"),
]

CONCEPT_GLOSSARY: list[dict[str, Any]] = [
    {
        "triggers": ["quantization", "cuantizacion", "gguf", "bitnet", "int4", "int8", "slm", "edge"],
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
        "triggers": ["rag", "retrieval", "embedding", "vector", "search"],
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
        "triggers": ["bayesian", "stochastic", "markov", "monte carlo", "optimization", "convex", "variational"],
        "term": "Optimización estocástica y búsqueda con incertidumbre",
        "explanation": (
            "En vez de calcular el gradiente exacto con millones de datos en cada paso, estima la dirección "
            "de mejora con muestras aleatorias pequeñas y controla la varianza."
        ),
        "everyday_example": (
            "Es como probar una cucharada de sopa para saber si le falta sal en lugar de tener que tomarse "
            "toda la olla antes de decidir."
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
]


@dataclass
class DigestPiece:
    """Pieza curada y enriquecida lista para el Radar semanal."""

    id: str
    role: str
    kind: str  # "paper" | "news" | "tool"
    topic: str
    topic_label: str
    title_es: str
    original_title: str
    hook: str
    summary_es: str
    reading_minutes: int
    url: str
    source: str
    score: float
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
    concept: dict[str, str]
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


def match_interest_topic(
    cand: TechCandidate,
    interests_cfg: dict[str, Any],
    weights: dict[str, float],
) -> tuple[str, float]:
    """Detecta el tema más afín al candidato y calcula el puntaje de interés ponderado."""
    text = normalize(f"{cand.title} {cand.abstract} {' '.join(cand.categories)}")
    best_topic = cand.default_topic if cand.default_topic in interests_cfg else next(iter(interests_cfg), "ml_applied")
    best_score = 0.0

    for topic, meta in interests_cfg.items():
        kws = meta.get("keywords", []) if isinstance(meta, dict) else []
        hits = sum(1 for kw in kws if re.search(rf"\b{re.escape(normalize(kw))}", text))
        if topic == cand.default_topic:
            hits += 1
        w = float(weights.get(topic, 1.0))
        topic_score = hits * w
        if topic_score > best_score:
            best_score = topic_score
            best_topic = topic

    return best_topic, best_score


def score_candidate(
    cand: TechCandidate,
    interests_cfg: dict[str, Any],
    weights: dict[str, float],
    now: datetime,
    max_age_days: float = 7.0,
) -> float:
    """Puntúa un candidato por relevancia a intereses, novedad (últimos 7 días) y señales de calidad."""
    age_days = (now - cand.published).total_seconds() / 86400.0
    if age_days > max_age_days or age_days < -1.0:
        return 0.0
    topic, interest_pts = match_interest_topic(cand, interests_cfg, weights)
    cand.matched_topic = topic
    if interest_pts <= 0:
        return 0.0

    freshness = max(0.0, 1.0 - max(0.0, age_days) / max_age_days) * 2.5
    quality = 0.0
    if cand.votes > 0:
        quality += min(4.0, math.log1p(cand.votes) * 0.8)
    if cand.citations > 0:
        quality += min(3.5, math.log1p(cand.citations) * 0.9)
    trusted = ("arXiv", "Hugging Face Papers", "MIT Technology Review", "Google Research Blog", "Simon Willison")
    if cand.source in trusted:
        quality += 1.0

    cand.score = round(interest_pts + freshness + quality, 3)
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
    - 3 papers (uno accesible, uno profundo y uno aplicado)
    - 2 noticias o lanzamientos de alto impacto
    - 1 herramienta o repo para probar
    - 1 joya inesperada o poco conocida
    """
    used_urls: set[str] = set()
    topic_counts: dict[str, int] = {}
    selected: list[tuple[str, TechCandidate]] = []

    def pick_best(pool: list[TechCandidate], prefer_rare: bool = False) -> TechCandidate | None:
        avail = [c for c in pool if c.url not in used_urls]
        if not avail:
            return None

        def adjusted(c: TechCandidate) -> float:
            diversity_penalty = topic_counts.get(c.matched_topic, 0) * 1.8
            rare_bonus = (1.5 if c.votes < 30 and c.matched_topic not in topic_counts else 0.0) if prefer_rare else 0.0
            return c.score - diversity_penalty + rare_bonus

        best = max(avail, key=adjusted)
        used_urls.add(best.url)
        topic_counts[best.matched_topic] = topic_counts.get(best.matched_topic, 0) + 1
        return best

    papers = [c for c in candidates if c.kind == "paper"]
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

    news_pool = [c for c in candidates if c.kind == "news"]
    for _ in range(2):
        n = pick_best(news_pool)
        if n:
            selected.append((ROLE_NEWS, n))

    tools_pool = [c for c in candidates if c.kind == "tool"] or [c for c in candidates if c.url not in used_urls]
    t = pick_best(tools_pool)
    if t:
        selected.append((ROLE_TOOL, t))

    gem_pool = [c for c in candidates if c.url not in used_urls]
    g = pick_best(gem_pool, prefer_rare=True)
    if g:
        selected.append((ROLE_GEM, g))

    return selected[:max_pieces]


def _spanish_title(title: str, topic_label: str) -> str:
    """Adapta el título al español sin inventar datos (si ya está en español lo conserva)."""
    out = title.strip()
    lower = out.lower()
    for en, es in EN_ES_TERMS:
        if en in lower:
            pattern = re.compile(re.escape(en), re.IGNORECASE)
            out = pattern.sub(es, out)
            lower = out.lower()
    if out == title.strip() and not any(w in lower.split() for w in ("de", "la", "el", "en", "para", "con")):
        return f"[{topic_label}] {out}"
    return out


def _own_summary_40w(cand: TechCandidate, topic_label: str, max_words: int = 40) -> str:
    """Construye un resumen propio en español de máximo 40 palabras sin copiar literalmente el original."""
    raw = strip_html(cand.abstract or "")
    first = re.split(r"(?<=[.!?])\s+", raw, maxsplit=1)[0] if raw else ""
    for en, es in EN_ES_TERMS:
        if en in first.lower():
            first = re.compile(re.escape(en), re.IGNORECASE).sub(es, first)
    words = [w for w in first.split() if len(w) > 1]
    core = " ".join(words[:18]).rstrip(".,;:")
    if cand.kind == "paper":
        author_hint = f" ({cand.authors[0]} et al.)" if cand.authors else ""
        base = f" Trabajo en {topic_label.lower()}{author_hint} publicado en {cand.source}."
        if core:
            base += f" Analiza: {core}."
    elif cand.kind == "tool":
        base = f"Herramienta o repositorio destacado en {cand.source} para {topic_label.lower()}."
        if core:
            base += f" Propuesta central: {core}."
    else:
        base = f"Reporte de {cand.source} con impacto en {topic_label.lower()}."
        if core:
            base += f" Punto clave: {core}."
    final_words = base.strip().split()
    if len(final_words) > max_words:
        return " ".join(final_words[:max_words]).rstrip(".,;:") + "."
    return " ".join(final_words)


def _hook_for_piece(role: str, cand: TechCandidate, topic_label: str) -> str:
    low = topic_label.lower()
    if role == ROLE_ACCESSIBLE:
        return f"Por qué importa: puerta de entrada clara para aplicar {low} sin infraestructura pesada."
    if role == ROLE_DEEP:
        return f"Por qué importa: formaliza las garantías teóricas y límites de {low}."
    if role == ROLE_APPLIED:
        return f"Por qué importa: resultados medibles listos para llevar a un prototipo real de {topic_label.lower()}."
    if role == ROLE_TOOL:
        return f"Por qué importa: ahorro directo de tiempo de desarrollo en {topic_label.lower()}."
    if role == ROLE_GEM:
        return f"Por qué importa: ángulo poco explorado en {topic_label.lower()} que conecta ideas de distintas áreas."
    return f"Por qué importa: marca hacia dónde se mueve el ecosistema de {topic_label.lower()} esta semana."


def _project_line_for_paper(cand: TechCandidate, projects_cfg: dict[str, Any]) -> str:
    """Conecta un paper con Aula Ya, Table Master o PostMark según tema y palabras clave."""
    text = f"{cand.title} {cand.abstract}".lower()
    if any(k in text for k in ("educat", "tutor", "student", "classroom", "learn", "quiz", "feedback", "assess")):
        proj = "Aula Ya"
    elif any(k in text for k in ("table", "tabular", "document", "extract", "schema", "sql", "structured", "optim")):
        proj = "Table Master"
    elif any(k in text for k in ("watermark", "verify", "classif", "trace", "pipeline", "content", "detect", "text")):
        proj = "PostMark"
    else:
        # Por afinidad de tema en config.yaml
        proj = "Aula Ya"
        for name, meta in projects_cfg.items():
            if cand.matched_topic in (meta.get("topics") or []):
                proj = name
                break
    angle = (projects_cfg.get(proj) or {}).get(
        "angle",
        "integrar la técnica en un flujo experimental acotado",
    )
    return f"En una línea para {proj}: útil para {angle}."


def _reading_minutes(cand: TechCandidate) -> int:
    if cand.kind == "paper":
        return 12
    if cand.kind == "tool":
        return 5
    words = len((cand.abstract or "").split())
    return max(3, min(8, 3 + words // 120))


def _enrich_with_anthropic(
    cand: TechCandidate,
    role: str,
    topic_label: str,
    api_key: str,
    model: str,
    max_words: int = 40,
) -> dict[str, str] | None:
    """Usa Anthropic API (si está configurada) con prompt estricto anti-alucinación."""
    prompt = (
        "Eres un editor técnico riguroso. Basándote ÚNICAMENTE en el título y resumen original adjuntos, "
        "sin inventar autores, cifras, métricas ni resultados que no estén explícitos en el texto, "
        "devuelve un JSON con las claves:\n"
        '- "title_es": título traducido al español claro\n'
        '- "hook": una sola frase breve que explique por qué importa\n'
        f'- "summary_es": resumen propio en español de MÁXIMO {max_words} palabras (sin copiar frases literales)\n'
        '- "project_line": una línea conectando la idea con Aula Ya (edtech/tutoría), Table Master (tablas/datos) '
        "o PostMark (verificación/flujos de contenido) solo si aplica.\n\n"
        f"Rol: {role}\nTema: {topic_label}\nTítulo original: {cand.title}\nResumen original: {cand.abstract[:1200]}"
    )
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
                "max_tokens": 350,
                "temperature": 0.1,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        resp.raise_for_status()
        content = resp.json()["content"][0]["text"]
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return None
        parsed = json.loads(match.group(0))
        words = str(parsed.get("summary_es", "")).split()
        if len(words) > max_words:
            parsed["summary_es"] = " ".join(words[:max_words]).rstrip(".,;:") + "."
        return parsed
    except Exception as exc:  # noqa: BLE001
        log.warning("Fallo enriquecimiento LLM para %r; usando resumen extractivo: %s", cand.title, exc)
        return None


def build_digest_pieces(
    selected: list[tuple[str, TechCandidate]],
    wcfg: dict[str, Any],
    anthropic_key: str | None = None,
) -> tuple[list[DigestPiece], bool]:
    """Construye las piezas finales del digest (con LLM si hay clave, o extractivo corto determinista)."""
    interests = wcfg.get("interests") or {}
    projects_cfg = wcfg.get("projects") or {}
    max_words = int(wcfg.get("summary_max_words", 40))
    model = str(wcfg.get("anthropic_model", "claude-3-5-haiku-latest"))
    used_llm = False

    if not anthropic_key:
        log.info("ANTHROPIC_API_KEY no configurada: usando resumen extractivo propio corto (<= %d palabras)", max_words)

    pieces: list[DigestPiece] = []
    for role, cand in selected:
        t_meta = interests.get(cand.matched_topic) or {}
        topic_label = str(t_meta.get("label") or cand.matched_topic)
        llm_data = (
            _enrich_with_anthropic(cand, role, topic_label, anthropic_key, model, max_words)
            if anthropic_key
            else None
        )
        if llm_data:
            used_llm = True
            title_es = str(llm_data.get("title_es") or _spanish_title(cand.title, topic_label))
            hook = str(llm_data.get("hook") or _hook_for_piece(role, cand, topic_label))
            summary_es = str(llm_data.get("summary_es") or _own_summary_40w(cand, topic_label, max_words))
            proj_line = str(llm_data.get("project_line") or _project_line_for_paper(cand, projects_cfg))
        else:
            title_es = _spanish_title(cand.title, topic_label)
            hook = _hook_for_piece(role, cand, topic_label)
            summary_es = _own_summary_40w(cand, topic_label, max_words)
            proj_line = _project_line_for_paper(cand, projects_cfg) if cand.kind == "paper" else ""

        pieces.append(
            DigestPiece(
                id=cand.short_id,
                role=role,
                kind=cand.kind,
                topic=cand.matched_topic,
                topic_label=topic_label,
                title_es=title_es,
                original_title=cand.title,
                hook=hook,
                summary_es=summary_es,
                reading_minutes=_reading_minutes(cand),
                url=cand.url,
                source=cand.source,
                score=cand.score,
                project_line=proj_line if cand.kind == "paper" else "",
            )
        )
    return pieces, used_llm


def pick_concept_in_60s(pieces: list[DigestPiece]) -> dict[str, str]:
    blob = " ".join(f"{p.original_title} {p.summary_es} {p.topic}" for p in pieces).lower()
    for entry in CONCEPT_GLOSSARY:
        if any(t in blob for t in entry["triggers"]):
            return {
                "term": entry["term"],
                "explanation": entry["explanation"],
                "everyday_example": entry["everyday_example"],
            }
    fallback = CONCEPT_GLOSSARY[0]
    return {
        "term": fallback["term"],
        "explanation": fallback["explanation"],
        "everyday_example": fallback["everyday_example"],
    }


def build_weekly_challenge(pieces: list[DigestPiece]) -> str:
    tool = next((p for p in pieces if p.role == ROLE_TOOL), None)
    applied = next((p for p in pieces if p.role == ROLE_APPLIED), None)
    target = tool or applied or (pieces[0] if pieces else None)
    if not target:
        return "Dedica 20 minutos a revisar tu backlog de lecturas guardadas en biblioteca/."
    if target.kind == "tool":
        return (
            f"En 20 minutos: clona o instala «{target.original_title}» ({target.source}), "
            f"corre un ejemplo mínimo con datos de Aula Ya o Table Master y anota si reduce código o latencia."
        )
    return (
        f"En 25 minutos: lee la sección de método y resultados de «{target.original_title}» "
        f"y escribe un boceto de 5 líneas sobre cómo validar esa idea en tu flujo actual."
    )


def build_weekly_quiz(pieces: list[DigestPiece], concept: dict[str, str]) -> QuizSpec:
    """Construye una pregunta de opción múltiple (sendPoll tipo quiz) con respuesta explicada."""
    term = concept["term"]
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
            question="¿Por qué la optimización estocástica es clave con grandes volúmenes de datos?",
            options=[
                "Estima la dirección de mejora con minilotes sin recorrer todo el dataset por paso",
                "Garantiza encontrar siempre el mínimo global exacto en una sola iteración",
                "Elimina todos los hiperparámetros de tasa de aprendizaje",
                "Solo funciona cuando la función objetivo es estrictamente lineal",
            ],
            correct_option_id=0,
            explanation="Evaluar el gradiente en un minilote aleatorio reduce drásticamente el costo por iteración.",
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


def build_story_of_the_week(pieces: list[DigestPiece], format_name: str) -> str:
    if not pieces:
        return "Esta semana hubo pocas publicaciones que superaran el umbral de calidad; aquí va solo lo verificado."
    counts: dict[str, int] = {}
    for p in pieces:
        counts[p.topic_label] = counts.get(p.topic_label, 0) + 1
    dominant = max(counts, key=lambda k: counts[k])
    lead = pieces[0]
    if format_name == "ranking_top5":
        return (
            f"La conversación técnica de la semana giró en torno a {dominant.lower()}: "
            f"desde «{lead.title_es}» hasta herramientas listas para llevar a producción local."
        )
    if format_name == "tres_cambios":
        return (
            f"Tres señales claras esta semana: {dominant.lower()} deja el laboratorio y se vuelve más compacto, "
            f"medible y fácil de integrar en proyectos reales."
        )
    return (
        f"La pregunta que atraviesa las lecturas de esta semana: ¿cómo aprovechar los avances en "
        f"{dominant.lower()} sin depender de modelos gigantes ni perder rigor?"
    )


def build_weekly_digest(
    cfg: Config,
    candidates: list[TechCandidate] | None = None,
    ref_date: date | None = None,
    week_override: str | None = None,
    feedback_path: Path = FEEDBACK_JSON,
) -> WeeklyDigest:
    """Ejecuta la tubería completa de curaduría para la semana indicada."""
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
    interests = wcfg.get("interests") or {}
    weights = effective_topic_weights(interests, state)
    read_ids = set(state.get("read_ids") or [])
    max_age_days = float(wcfg.get("max_age_days", 7))

    scored: list[TechCandidate] = []
    discarded_old_or_irrelevant = 0
    discarded_already_read = 0
    for cand in raw_candidates:
        if cand.short_id in read_ids:
            discarded_already_read += 1
            continue
        s = score_candidate(cand, interests, weights, now_utc, max_age_days=max_age_days)
        if s > 0:
            scored.append(cand)
        else:
            discarded_old_or_irrelevant += 1

    deduped = dedupe_candidates(scored, float(wcfg.get("dedup_threshold", 78)))
    discarded_dups = len(scored) - len(deduped)
    selected = select_diverse_pieces(deduped, int(wcfg.get("max_pieces", 7)))

    log.info(
        "Curaduría Radar semanal (%s): leídos=%d, descartados_edad_o_tema=%d, ya_leídos=%d, "
        "descartados_duplicados=%d, elegidos=%d",
        week_id,
        len(raw_candidates),
        discarded_old_or_irrelevant,
        discarded_already_read,
        discarded_dups,
        len(selected),
    )

    pieces, used_llm = build_digest_pieces(selected, wcfg, cfg.secrets.anthropic_key)
    fmt_idx, fmt_name = choose_weekly_format(week_num, state.get("last_format_index"))
    concept = pick_concept_in_60s(pieces)
    challenge = build_weekly_challenge(pieces)
    quiz = build_weekly_quiz(pieces, concept)
    story = build_story_of_the_week(pieces, fmt_name)

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


def _format_piece_html(idx: int, p: DigestPiece) -> str:
    link = html.escape(p.url, quote=True)
    head = (
        f'{idx}. <b>[{esc(p.role)}]</b> <a href="{link}">{esc(p.title_es)}</a> '
        f"<i>({p.reading_minutes} min · {esc(p.source)})</i>"
    )
    lines = [
        head,
        f"   🎯 {esc(p.hook)}",
        f"   {esc(p.summary_es)}",
    ]
    if p.project_line:
        lines.append(f"   🛠️ <i>{esc(p.project_line)}</i>")
    return "\n".join(lines)


def format_weekly_messages(digest: WeeklyDigest) -> tuple[list[str], list[dict[str, Any] | None]]:
    """Divide el Radar semanal en 2 o 3 mensajes cortos (<= 4096 caracteres) con botones inline."""
    headers = {
        "ranking_top5": "📡 <b>Radar semanal · Ranking de lecturas clave</b>",
        "tres_cambios": "📡 <b>Radar semanal · Tres cosas que cambiaron esta semana</b>",
        "pregunta_semana": "📡 <b>Radar semanal · La pregunta técnica de la semana</b>",
    }
    header = headers.get(digest.format_name, "📡 <b>Radar semanal de tecnología</b>")

    if not digest.pieces:
        msg = (
            f"{header} ({esc(digest.week_id)})\n\n"
            f"{esc(digest.story_of_the_week)}\n\n"
            "<i>Esta semana no hubo suficientes candidatos verificados para armar las 7 piezas sin inventar.</i>"
        )
        return [msg[:MAX_LEN]], [None]

    papers = [p for p in digest.pieces if p.kind == "paper"]
    others = [p for p in digest.pieces if p.kind != "paper"]

    # Mensaje 1: Historia de la semana + 3 Papers
    m1_lines = [
        f"{header} · <b>{esc(digest.week_id)}</b>",
        f"💡 <b>Historia de la semana:</b> {esc(digest.story_of_the_week)}",
        "",
        "📄 <b>Papers de la semana</b>",
    ]
    for i, p in enumerate(papers, 1):
        m1_lines.append("")
        m1_lines.append(_format_piece_html(i, p))
    msg1 = "\n".join(m1_lines)[:MAX_LEN]
    kb1 = build_piece_keyboard(papers[0].id, papers[0].topic) if papers else None

    # Mensaje 2: Noticias de impacto, Herramienta para probar y Joya de la semana
    m2_lines = ["🚀 <b>Lanzamientos, herramienta y joya de la semana</b>"]
    start_idx = len(papers) + 1
    for i, p in enumerate(others, start_idx):
        m2_lines.append("")
        m2_lines.append(_format_piece_html(i, p))
    msg2 = "\n".join(m2_lines)[:MAX_LEN]
    kb2 = build_piece_keyboard(others[0].id, others[0].topic) if others else None

    # Mensaje 3: Concepto en 60 segundos + Reto de la semana
    c = digest.concept
    m3_lines = [
        f"🧠 <b>Concepto en 60 segundos: {esc(c['term'])}</b>",
        esc(c["explanation"]),
        f"<i>Ejemplo cotidiano:</i> {esc(c['everyday_example'])}",
        "",
        "⚡ <b>Reto de la semana (15-30 min)</b>",
        esc(digest.challenge),
    ]
    msg3 = "\n".join(m3_lines)[:MAX_LEN]
    tail_piece = others[-1] if others else papers[-1]
    kb3 = build_piece_keyboard(tail_piece.id, tail_piece.topic)

    if others:
        return [msg1, msg2, msg3], [kb1, kb2, kb3]
    return [msg1, msg3], [kb1, kb3]


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
        lines.extend(
            [
                f"### {i}. [{p.role}] [{p.title_es}]({p.url})",
                f"- **Título original:** {p.original_title}",
                f"- **Fuente:** {p.source} · **Tema:** {p.topic_label} · **Lectura:** ~{p.reading_minutes} min",
                f"- **Por qué importa:** {p.hook}",
                f"- **Resumen:** {p.summary_es}",
            ]
        )
        if p.project_line:
            lines.append(f"- **Conexión con proyecto:** {p.project_line}")
        lines.append("")

    c = digest.concept
    lines.extend(
        [
            f"## Concepto en 60 segundos: {c['term']}",
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
    """Escribe digests/AAAA/AAAA-Wnn.md, actualiza digests/README.md y docs/digests.json (Fase 3)."""
    year_dir = digests_dir / str(digest.year)
    year_dir.mkdir(parents=True, exist_ok=True)
    md_path = year_dir / f"{digest.week_id}.md"
    md_path.write_text(render_digest_markdown(digest), encoding="utf-8")

    # Actualiza docs/digests.json para el buscador web
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
                    "title_es": p.title_es,
                    "hook": p.hook,
                    "summary_es": p.summary_es,
                    "reading_minutes": p.reading_minutes,
                    "url": p.url,
                    "source": p.source,
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

    # Reconstruye digests/README.md con un índice de todas las semanas archivadas
    idx_lines = [
        "# Archivo histórico del Radar semanal de tecnología",
        "",
        "Índice de ediciones semanales generadas automáticamente.",
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


def already_sent_this_week(week_id: str, feedback_path: Path = FEEDBACK_JSON) -> bool:
    state = load_feedback(feedback_path)
    return week_id in (state.get("sent_weeks") or [])


def mark_week_sent(digest: WeeklyDigest, feedback_path: Path = FEEDBACK_JSON) -> None:
    state = load_feedback(feedback_path)
    sent_weeks = state.setdefault("sent_weeks", [])
    if digest.week_id not in sent_weeks:
        sent_weeks.append(digest.week_id)
    state["last_format_index"] = digest.stats.get("format_index", 0)
    save_feedback(state, feedback_path)
    register_pieces_in_catalog(
        [
            {
                "id": p.id,
                "title_es": p.title_es,
                "title": p.original_title,
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
    parser.add_argument("--dry-run", action="store_true", help="imprime el digest sin enviar a Telegram")
    parser.add_argument("--force", action="store_true", help="envía aunque ya se haya enviado esta semana")
    parser.add_argument("--as-of", default=None, metavar="HH:MM", help="hora simulada de ejecución")
    parser.add_argument("--week", default=None, metavar="AAAA-Wnn", help="genera o regenera una semana específica")
    parser.add_argument("--write-archive", action="store_true", help="escribe el archivo Markdown incluso en dry-run")
    parser.add_argument("--config", default=None, help="ruta alternativa a config.yaml")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    cfg = load_config(args.config)
    today = datetime.now(cfg.tz).date()
    target_week = args.week or iso_week_id(today)

    if not args.dry_run:
        sync_telegram_feedback(cfg.secrets.telegram_token)

    if not args.dry_run and not args.force and already_sent_this_week(target_week):
        log.info("Ya se envió el Radar semanal de %s; no se duplica.", target_week)
        return 0

    if not args.dry_run and not (cfg.secrets.telegram_token and cfg.secrets.telegram_chat_id):
        log.error("Faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID")
        return 2

    digest = build_weekly_digest(cfg, ref_date=today, week_override=args.week)
    messages, markups = format_weekly_messages(digest)

    if args.dry_run:
        if args.write_archive:
            md_path = write_digest_archive(digest)
            log.info("Archivo semanal escrito en %s", md_path)
        for i, msg in enumerate(messages, 1):
            print(f"----- radar semanal mensaje {i}/{len(messages)} ({len(msg)} caracteres) -----")
            print(msg)
        print("----- mini quiz (sendPoll) -----")
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
        disable_notification=False,
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
        )
    except Exception:
        log.exception("El digest se envió, pero falló el envío del mini quiz sendPoll")

    mark_week_sent(digest)
    write_digest_archive(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
