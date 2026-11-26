"""Curaduría, enriquecimiento, formato rotativo y archivo público del Radar semanal de tecnología (Bloque B)."""

from __future__ import annotations

import argparse
import html
import json
import logging
import math
import random
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

DEFAULT_USER_STACK = (
    "python",
    "pandas",
    "scikit-learn",
    "sklearn",
    "tensorflow",
    "django",
    "postgres",
    "postgresql",
    "next.js",
    "nextjs",
    "typescript",
)

CORE_EDGE_TERMS = (
    "llama.cpp",
    "llamacpp",
    "gguf",
    "small language model",
    "slm",
    "on-device",
    "offline",
    "local llm",
    "local model",
    "quantization",
    "bitnet",
)

# Cada concepto es UN SOLO término técnico (nunca una combinación de dos).
CONCEPT_GLOSSARY: list[dict[str, Any]] = [
    {
        "triggers": ["quantization", "cuantizacion", "gguf", "bitnet", "int4", "int8", "slm", "on-device", "llama.cpp"],
        "term": "Cuantización de pesos",
        "explanation": (
            "Consiste en reducir la precisión numérica de los parámetros de un modelo (por ejemplo, de 16 bits "
            "a 4 bits) para que ocupe hasta cuatro veces menos memoria y corra en CPU o dispositivos locales."
        ),
        "everyday_example": (
            "Es como guardar una fotografía en JPEG de alta calidad en vez de RAW sin comprimir: visualmente es "
            "casi idéntica, pero cabe en el teléfono y abre al instante."
        ),
        "quiz_question": "¿Qué efecto directo tiene cuantizar los pesos de un modelo de lenguaje de FP16 a INT4?",
        "quiz_correct": "Reduce el uso de RAM cerca de 4 veces y acelera la inferencia local con pérdida acotada",
        "quiz_distractors": [
            "Duplica el número de cabezas de atención durante el preentrenamiento",
            "Elimina la necesidad de usar un tokenizador para el texto de entrada",
            "Convierte una arquitectura autorregresiva en un modelo de difusión bidireccional",
        ],
        "quiz_explanation": (
            "Con 4 bits por peso en vez de 16, el modelo reduce ~4x su huella en memoria y corre en CPU/edge."
        ),
    },
    {
        "triggers": ["rag", "retrieval", "embedding", "vector", "corpus"],
        "term": "Generación aumentada por recuperación (RAG)",
        "explanation": (
            "Antes de redactar una respuesta, el sistema consulta un índice de documentos verificados y pasa "
            "los fragmentos más pertinentes en el prompt del modelo."
        ),
        "everyday_example": (
            "Es la diferencia entre responder un examen de memoria y responderlo a libro abierto consultando "
            "la página exacta antes de escribir."
        ),
        "quiz_question": (
            "¿Cuál es la ventaja técnica central de RAG frente a consultar únicamente los pesos del modelo?"
        ),
        "quiz_correct": "Ancla la generación en fragmentos recuperados de fuentes verificables y actualizables",
        "quiz_distractors": [
            "Reduce la complejidad cuadrática de la autoatención a tiempo constante O(1)",
            "Reemplaza el descenso de gradiente por búsqueda aleatoria durante el entrenamiento",
            "Permite ejecutar modelos sin ningún tipo de representación vectorial",
        ],
        "quiz_explanation": (
            "RAG inyecta pasajes recuperados de documentos reales, reduciendo alucinaciones y permitiendo citar."
        ),
    },
    {
        "triggers": ["markov", "regime", "hidden", "sliding-window"],
        "term": "Modelos ocultos de Markov (HMM)",
        "explanation": (
            "Representan un sistema cuyo estado interno no se observa directamente, pero puede inferirse "
            "a partir de la secuencia de señales ruidosas que emite en el tiempo."
        ),
        "everyday_example": (
            "Es como deducir si afuera está lloviendo desde una oficina sin ventanas solo viendo si las personas "
            "que entran al edificio traen el paraguas mojado."
        ),
        "quiz_question": (
            "En un modelo oculto de Markov, ¿qué propiedad cumple la transición hacia el siguiente estado?"
        ),
        "quiz_correct": "Depende únicamente del estado actual y no de toda la trayectoria histórica previa",
        "quiz_distractors": [
            "Requiere conocer todas las observaciones futuras antes de estimar el estado presente",
            "Obliga a que las variables observables sigan siempre una distribución uniforme",
            "Impide que el sistema cambie de régimen una vez iniciado el proceso",
        ],
        "quiz_explanation": (
            "La propiedad de Markov establece que el estado presente resume la información relevante del pasado."
        ),
    },
    {
        "triggers": ["bayesian", "stochastic", "monte carlo", "optimization", "convex", "variational", "gradient"],
        "term": "Optimización estocástica",
        "explanation": (
            "Actualiza los parámetros del modelo usando estimaciones ruidosas del gradiente calculadas sobre "
            "muestras aleatorias pequeñas en vez de evaluar todo el conjunto de datos en cada paso."
        ),
        "everyday_example": (
            "Es como probar una cucharada de sopa para decidir si le falta sal en lugar de tener que tomarse "
            "toda la olla antes de ajustar la receta."
        ),
        "quiz_question": "¿Qué compromiso fundamental introduce la optimización estocástica por minilotes?",
        "quiz_correct": "Reduce drásticamente el costo por iteración a cambio de introducir varianza en el gradiente",
        "quiz_distractors": [
            "Garantiza converger al mínimo global exacto de funciones no convexas en un solo paso",
            "Elimina la necesidad de elegir una tasa de aprendizaje o criterio de parada",
            "Exige almacenar la matriz hessiana completa en memoria durante cada época",
        ],
        "quiz_explanation": (
            "Los minilotes abaratan cada paso de actualización introduciendo ruido controlable en el gradiente."
        ),
    },
    {
        "triggers": ["lora", "qlora", "adapter", "fine-tuning", "distillation"],
        "term": "Adaptadores de bajo rango (LoRA)",
        "explanation": (
            "Congela los pesos originales de un modelo preentrenado e inyecta pares de matrices pequeñas "
            "entrenables que capturan el ajuste a una tarea con una fracción mínima de memoria."
        ),
        "everyday_example": (
            "Es como pegar notas adhesivas con correcciones puntuales sobre un manual impreso en vez de "
            "reescribir e imprimir todo el libro desde cero."
        ),
        "quiz_question": "¿Por qué LoRA reduce tanto la memoria necesaria durante el ajuste fino de un modelo?",
        "quiz_correct": "Porque congela los pesos base y solo calcula gradientes sobre matrices de rango bajo",
        "quiz_distractors": [
            "Porque elimina la mitad de las capas del transformador antes de entrenar",
            "Porque convierte todos los textos de entrenamiento en vectores de una sola dimensión",
            "Porque desactiva el mecanismo de atención y entrena solo la capa de salida",
        ],
        "quiz_explanation": (
            "Al entrenar solo matrices factorizadas de rango r << d, los estados del optimizador ocupan muy poca RAM."
        ),
    },
    {
        "triggers": ["context", "agent", "compaction", "memory", "transformer", "attention"],
        "term": "Compactación de ventana de contexto",
        "explanation": (
            "Resume o descarta selectivamente pasos intermedios de una conversación o traza de agente "
            "para conservar solo las decisiones y datos relevantes dentro del límite de tokens."
        ),
        "everyday_example": (
            "Es como archivar los borradores al terminar una reunión y conservar solo el acta de una página "
            "con los acuerdos antes de pasar al siguiente proyecto."
        ),
        "quiz_question": "¿Qué problema técnico evita la compactación de contexto en agentes de larga ejecución?",
        "quiz_correct": "La degradación de atención, aumento de latencia y desborde del límite de tokens activos",
        "quiz_distractors": [
            "La pérdida de conexión TCP con el servidor de base de datos relacional",
            "La corrupción física de los archivos de pesos GGUF en el disco duro",
            "El bloqueo del compilador al verificar tipos estáticos en TypeScript",
        ],
        "quiz_explanation": (
            "Mantener el contexto acotado reduce costo por token, latencia de prefill y distracción por ruido previo."
        ),
    },
    {
        "triggers": ["postgres", "sqlite", "tabular", "table", "sql", "database", "schema"],
        "term": "Motores SQL embebidos",
        "explanation": (
            "Ejecutan consultas relacionales y transacciones ACID directamente dentro del proceso de la "
            "aplicación sobre un archivo local sin requerir un servidor de base de datos separado."
        ),
        "everyday_example": (
            "Es como llevar una libreta de bolsillo contigo para anotar y consultar al instante, en vez de "
            "llamar a un archivo central cada vez que necesitas un dato."
        ),
        "quiz_question": (
            "¿Cuándo resulta especialmente ventajoso un motor SQL embebido frente a un servidor cliente-servidor?"
        ),
        "quiz_correct": "En lecturas locales de baja latencia, aplicaciones offline o pruebas reproducibles sin red",
        "quiz_distractors": [
            "Cuando miles de servidores remotos deben escribir simultáneamente por red sin bloqueo",
            "Cuando se requiere entrenar tensores en GPU sin usar almacenamiento en disco",
            "Cuando la aplicación no utiliza ningún esquema ni consultas estructuradas",
        ],
        "quiz_explanation": (
            "Al correr en el mismo proceso de la app, elimina la latencia de red y simplifica el despliegue offline."
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
    reading_minutes: int  # 0 para papers (se omite); calculado por longitud real para noticias/herramientas
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
    """Detecta el tema más afín por coincidencias reales de palabras clave.

    Los términos núcleo de edge/offline AI (como llama.cpp, GGUF, cuantización, modelos locales)
    aportan peso doble para asegurar que puntúen alto.
    """
    text = normalize(f"{cand.title} {cand.abstract} {' '.join(cand.categories)}")
    best_topic = cand.default_topic if cand.default_topic in interests_cfg else next(iter(interests_cfg), "ml_applied")
    best_score = 0.0
    best_kws: list[str] = []

    for topic, meta in interests_cfg.items():
        kws = meta.get("keywords", []) if isinstance(meta, dict) else []
        core_kws = set(meta.get("core_keywords", []) if isinstance(meta, dict) else [])
        if topic == "small_models_edge":
            core_kws.update(CORE_EDGE_TERMS)
        matched = [kw for kw in kws if re.search(rf"\b{re.escape(normalize(kw))}", text)]
        if not matched:
            continue
        hit_units = sum(2.0 if kw in core_kws else 1.0 for kw in matched)
        w = float(weights.get(topic, 1.0))
        topic_score = hit_units * w
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

    Solo marca ``outside_radar = True`` cuando ``affinity_score < min_affinity``.
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
    max_per_source: int = 2,
    max_per_topic: int = 2,
) -> list[tuple[str, TechCandidate]]:
    """Selecciona hasta 7 piezas garantizando:
    - Máximo ``max_per_source`` (2) piezas de la misma fuente.
    - Máximo ``max_per_topic`` (2) piezas del mismo tema.
    - Las 6 primeras cumplen el umbral de afinidad (not outside_radar); solo la joya puede estar fuera del radar.
    """
    used_urls: set[str] = set()
    topic_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    selected: list[tuple[str, TechCandidate]] = []

    in_radar = [c for c in candidates if not c.outside_radar]

    def pick_best(pool: list[TechCandidate], prefer_outside_or_rare: bool = False) -> TechCandidate | None:
        avail = [
            c
            for c in pool
            if c.url not in used_urls
            and source_counts.get(c.source, 0) < max_per_source
            and topic_counts.get(c.matched_topic, 0) < max_per_topic
        ]
        if not avail:
            return None

        def adjusted(c: TechCandidate) -> float:
            diversity_penalty = topic_counts.get(c.matched_topic, 0) * 2.0 + source_counts.get(c.source, 0) * 1.5
            bonus = 0.0
            if prefer_outside_or_rare:
                if c.outside_radar:
                    bonus += 2.5
                elif c.matched_topic not in topic_counts:
                    bonus += 1.5
            return c.score - diversity_penalty + bonus

        best = max(avail, key=adjusted)
        used_urls.add(best.url)
        topic_counts[best.matched_topic] = topic_counts.get(best.matched_topic, 0) + 1
        source_counts[best.source] = source_counts.get(best.source, 0) + 1
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
        n = pick_best(news_pool) or pick_best(in_radar)
        if n:
            selected.append((ROLE_NEWS, n))

    tools_pool = [c for c in in_radar if c.kind == "tool"] or [
        c for c in candidates if c.kind == "tool" and c.url not in used_urls
    ]
    t = pick_best(tools_pool) or pick_best(in_radar)
    if t:
        selected.append((ROLE_TOOL, t))

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


_HYPHEN_COMPOUNDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bstateof-the-art\b", re.IGNORECASE), "state-of-the-art"),
    (re.compile(r"\bstate-of-theart\b", re.IGNORECASE), "state-of-the-art"),
    (re.compile(r"\bendto-end\b", re.IGNORECASE), "end-to-end"),
    (re.compile(r"\bend-toend\b", re.IGNORECASE), "end-to-end"),
    (re.compile(r"\boutof-distribution\b", re.IGNORECASE), "out-of-distribution"),
    (re.compile(r"\boutof-domain\b", re.IGNORECASE), "out-of-domain"),
    (re.compile(r"\bindomain\b", re.IGNORECASE), "in-domain"),
    (re.compile(r"\brealworld\b", re.IGNORECASE), "real-world"),
    (re.compile(r"\blargescale\b", re.IGNORECASE), "large-scale"),
    (re.compile(r"\blowresource\b", re.IGNORECASE), "low-resource"),
    (re.compile(r"\bfinegrained\b", re.IGNORECASE), "fine-grained"),
    (re.compile(r"\b(\w+)(of|to|in|on|by)-the-(\w+)\b", re.IGNORECASE), r"\1-\2-the-\3"),
]


def _repair_hyphenated_words(text: str) -> str:
    """Corrige palabras cortadas por salto de línea PDF o compuestas con guion perdido (p. ej. 'stateof-the-art')."""
    # Une guion seguido de espacio entre minúsculas ("quantiza- tion" -> "quantization")
    out = re.sub(r"([a-zA-ZáéíóúñÁÉÍÓÚÑ]{2,})-\s+([a-zA-ZáéíóúñÁÉÍÓÚÑ]{2,})", r"\1\2", text)
    for pattern, repl in _HYPHEN_COMPOUNDS:
        out = pattern.sub(repl, out)
    return out


def _clean_raw_abstract(abstract: str, title: str) -> str:
    """Quita el título repetido, prefijos como 'Release:'/'Show HN:', repara guiones, espacios antes de punto y '..'."""
    text = _repair_hyphenated_words(strip_html(abstract or "").strip())
    if not text:
        return ""
    text = re.sub(r"^(?:release|show hn|ask hn|abstract|tl;dr)\s*:\s*", "", text, flags=re.IGNORECASE).strip()
    if title:
        t_clean = strip_html(title).strip()
        if text.lower().startswith(t_clean.lower()):
            text = text[len(t_clean) :].lstrip(" :-–—.,;|")
        short_t = re.sub(r"^(?:release|show hn)\s*:\s*", "", t_clean, flags=re.IGNORECASE).strip()
        if short_t and text.lower().startswith(short_t.lower()):
            text = text[len(short_t) :].lstrip(" :-–—.,;|")
    text = re.sub(r"\s+([.,;!?])", r"\1", text)
    text = re.sub(r"\.{2,}", ".", text)
    return text.strip()


def _split_complete_sentences(text: str) -> list[str]:
    if not text:
        return []
    raw_parts = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    for part in raw_parts:
        s = re.sub(r"\.{2,}", ".", part.strip())
        if not s:
            continue
        if s[-1] not in ".!?":
            s += "."
        out.append(s)
    return out


def _extractive_complete_summary(cand: TechCandidate, max_words: int = 25) -> str:
    """Extracto limpio:
    - Quita el título repetido y prefijos como 'Release:'.
    - Máximo ``max_words`` (25) palabras, oración completa, sin '..'.
    - Si el extracto está vacío o no aporta nada (repite el título), devuelve '' (se omite).
    """
    cleaned = _clean_raw_abstract(cand.abstract, cand.title)
    if not cleaned:
        return ""
    if fuzz.ratio(normalize(cleaned), normalize(cand.title)) > 80:
        return ""

    lang = _detect_lang_tag(cand)
    prefix = f"[{lang}]"
    budget = max(10, max_words - 1)  # reserva 1 palabra para [EN]/[ES]
    sentences = _split_complete_sentences(cleaned)
    if not sentences:
        return ""

    # 1. Busca la primera oración completa que quepa en budget (entre 4 y budget palabras)
    chosen = next((s for s in sentences if 4 <= len(s.split()) <= budget), None)
    if chosen is None:
        # 2. Si la primera oración supera budget, toma cláusulas completas hasta punto y coma, dos puntos o coma
        first = sentences[0]
        clauses = re.split(r"[;:]\s+|,\s+", first)
        acc: list[str] = []
        used = 0
        for cl in clauses:
            w_cl = cl.strip().split()
            if not w_cl:
                continue
            if used + len(w_cl) <= budget:
                acc.append(" ".join(w_cl))
                used += len(w_cl)
            else:
                break
        if acc and used >= 5:
            body = ", ".join(acc).rstrip(".,;:!?") + "."
        else:
            body = " ".join(first.split()[:budget]).rstrip(".,;:!?") + "."
    else:
        body = chosen

    body = re.sub(r"\s+([.,;!?])", r"\1", body)
    body = re.sub(r"\.{2,}", ".", body)
    if fuzz.ratio(normalize(body), normalize(cand.title)) > 80:
        return ""
    return f"{prefix} {body}"


def _concrete_project_match(cand: TechCandidate, projects_cfg: dict[str, Any]) -> tuple[str, list[str], str]:
    """Devuelve (nombre_proyecto, keywords_coincidentes, linea_formateada) SOLO si coinciden >= 2 términos distintos."""
    text = normalize(f"{cand.title} {cand.abstract}")
    best_proj = ""
    best_matches: list[str] = []

    for proj_name, meta in projects_cfg.items():
        if not isinstance(meta, dict):
            continue
        kws = meta.get("keywords") or []
        hits = list(dict.fromkeys(kw for kw in kws if re.search(rf"\b{re.escape(normalize(kw))}", text)))
        if len(hits) >= 2 and len(hits) > len(best_matches):
            best_matches = hits
            best_proj = proj_name

    if not best_proj or len(best_matches) < 2:
        return "", [], ""
    angle = (projects_cfg.get(best_proj) or {}).get("angle", "aplicación directa")
    matched_str = ", ".join(best_matches[:3])
    line = f"Conexión con {best_proj} (por {matched_str}): aplicable a {angle}."
    return best_proj, best_matches, line


def _concrete_reason_hook(
    role: str,
    cand: TechCandidate,
    topic_label: str,
    projects_cfg: dict[str, Any],
) -> str:
    """Construye la razón concreta 🎯 SOLO si hay coincidencia con un interés o proyecto.

    Si no hay coincidencia concreta ni con intereses ni con proyectos, devuelve '' (se omite la línea 🎯).
    Nunca rellena con una oración suelta del abstract.
    """
    proj_name, proj_kws, _ = _concrete_project_match(cand, projects_cfg)
    if not cand.matched_keywords and not proj_kws:
        return ""

    parts: list[str] = []
    if cand.matched_keywords:
        kws = ", ".join(cand.matched_keywords[:3])
        parts.append(f"relevante para {topic_label.lower()} por {kws}")
    if proj_name and proj_kws:
        parts.append(f"conecta con {proj_name} ({', '.join(proj_kws[:2])})")
    if cand.categories:
        parts.append(f"área {', '.join(cand.categories[:2])}")
    elif cand.authors:
        lead = cand.authors[0] + (" et al." if len(cand.authors) > 1 else "")
        parts.append(f"por {lead}")
    elif cand.votes > 0:
        parts.append(f"{cand.votes} votos en {cand.source}")

    reason = " · ".join(parts)
    return (reason[0].upper() + reason[1:]).rstrip(".") + "."


def _reading_minutes(cand: TechCandidate, role: str) -> int:
    """Omite el tiempo de lectura (devuelve 0) para papers o cuando el texto es solo un snippet (< 180 palabras)."""
    if cand.kind == "paper" or role in (ROLE_ACCESSIBLE, ROLE_DEEP, ROLE_APPLIED):
        return 0
    words = len(strip_html(cand.abstract or "").split())
    if words < 180:
        return 0
    return max(1, round(words / 180))


def enrich_digest_batch_with_anthropic(
    selected: list[tuple[str, TechCandidate]],
    wcfg: dict[str, Any],
    api_key: str,
) -> dict[str, Any] | None:
    """Hace UNA sola llamada por digest a Anthropic con tu stack y proyectos exactos y valida el JSON."""
    if not selected:
        return None
    interests = wcfg.get("interests") or {}
    max_words = int(wcfg.get("summary_max_words", 25))
    primary_model = str(wcfg.get("anthropic_model", "claude-haiku-5-5"))
    fallback_model = str(wcfg.get("anthropic_fallback_model", "claude-sonnet-5-5"))
    stack_list = list(wcfg.get("user_stack") or DEFAULT_USER_STACK)

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
        "sin inventar datos, cifras, métricas ni autores:\n"
        f"- Mi stack técnico: {', '.join(stack_list)}.\n"
        "- Mis proyectos:\n"
        "  1. Aula Ya: educación rural offline con IA local y SMS.\n"
        "  2. Table Master: hojas de cálculo con IA para limpiar datos.\n"
        "  3. PostMark: recuerdos anclados a lugares.\n\n"
        "Devuelve EXCLUSIVAMENTE un objeto JSON válido con:\n"
        '- "story_of_the_week": una línea de gancho concreta en español a partir de las piezas elegidas.\n'
        '- "pieces": una lista con un objeto por pieza en el mismo orden ("index"), cada uno con:\n'
        '  - "index": entero\n'
        '  - "title_es": titular traducido al español fielmente\n'
        '  - "hook": razón concreta apoyada en un dato del resumen y mi interés/proyecto (o null si no hay)\n'
        f'  - "summary_es": resumen propio en español de MÁXIMO {max_words} palabras, oración completa sin ".."\n'
        '  - "project_line": conexión con Aula Ya, Table Master o PostMark SOLO si es real y directa; si no, null\n'
        '  - "reading_minutes": entero calculado por longitud real (o 0 si es un paper).\n\n'
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
    max_words = int(wcfg.get("summary_max_words", 25))

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
        is_paper = cand.kind == "paper" or role in (ROLE_ACCESSIBLE, ROLE_DEEP, ROLE_APPLIED)

        if used_llm and idx < len(llm_pieces) and isinstance(llm_pieces[idx], dict):
            item_llm = llm_pieces[idx]
            title_es = str(item_llm.get("title_es") or cand.title).strip()
            raw_hook = item_llm.get("hook")
            hook = str(raw_hook).strip() if raw_hook and str(raw_hook).lower() != "null" else ""
            raw_sum = str(item_llm.get("summary_es") or "").strip()
            raw_sum = re.sub(r"\.{2,}", ".", raw_sum)
            words = raw_sum.split()
            if len(words) > max_words:
                raw_sum = " ".join(words[:max_words]).rstrip(".,;:") + "."
            summary_es = raw_sum
            raw_proj = item_llm.get("project_line")
            proj_line = str(raw_proj).strip() if raw_proj and str(raw_proj).lower() != "null" else ""
            read_min = 0 if is_paper else int(item_llm.get("reading_minutes") or _reading_minutes(cand, role))
        else:
            title_es = cand.title
            hook = _concrete_reason_hook(role, cand, topic_label, projects_cfg)
            summary_es = _extractive_complete_summary(cand, max_words)
            _, _, proj_line = _concrete_project_match(cand, projects_cfg)
            read_min = _reading_minutes(cand, role)

        pieces.append(
            DigestPiece(
                id=cand.short_id,
                role=role,
                kind="paper" if is_paper else cand.kind,
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
                project_line=proj_line if is_paper else "",
            )
        )
    return pieces, used_llm, llm_story


def pick_concept_in_60s(pieces: list[DigestPiece]) -> dict[str, Any]:
    """Busca un término técnico único que aparezca en las lecturas de la semana; si ninguno aplica, lo indica."""
    for entry in CONCEPT_GLOSSARY:
        for p in pieces:
            blob = normalize(f"{p.original_title} {p.summary_es} {p.hook}")
            for trig in entry["triggers"]:
                if re.search(rf"\b{re.escape(normalize(trig))}\b", blob):
                    return {
                        **entry,
                        "from_readings": True,
                        "source_title": p.original_title,
                    }
    fallback = CONCEPT_GLOSSARY[0]
    return {
        **fallback,
        "from_readings": False,
        "source_title": "",
    }


def is_tool_compatible_with_stack(
    piece: DigestPiece,
    stack_keywords: list[str] | tuple[str, ...] = DEFAULT_USER_STACK,
) -> bool:
    """True si la herramienta/repo es compatible con el stack del usuario."""
    if piece.kind != "tool":
        return False
    blob = normalize(f"{piece.original_title} {piece.summary_es} {piece.hook} {piece.url}")
    return any(re.search(rf"\b{re.escape(normalize(kw))}\b", blob) for kw in stack_keywords)


def build_weekly_challenge(
    pieces: list[DigestPiece],
    stack_keywords: list[str] | tuple[str, ...] = DEFAULT_USER_STACK,
) -> str:
    """Reto ejecutable compatible con el stack (Python, pandas, scikit-learn, TF, Django, Postgres, Next.js, TS)."""
    compatible_tool = next((p for p in pieces if is_tool_compatible_with_stack(p, stack_keywords)), None)
    if compatible_tool:
        return (
            f"Herramienta compatible con tu stack (20 min): prueba «{compatible_tool.original_title}» "
            f"({compatible_tool.url}) en un entorno local y anota si encaja en tus flujos."
        )

    paper = next((p for p in pieces if p.kind == "paper"), None)
    if paper:
        return (
            f"Lectura enfocada (20 min): lee el abstract y la conclusión de «{paper.original_title}» "
            "y escribe 3 notas concretas sobre cómo aplicar la idea en tu stack."
        )

    any_piece = pieces[0] if pieces else None
    if any_piece and any_piece.kind == "tool":
        return (
            f"Lectura enfocada (20 min): revisa el diseño y la conclusión de «{any_piece.original_title}» "
            "y escribe 3 notas sobre qué patrón podrías llevar a Python, Postgres o TypeScript."
        )
    if any_piece:
        return (
            f"Análisis rápido (15 min): revisa «{any_piece.original_title}» "
            "y escribe en 3 notas cómo afecta a Aula Ya, Table Master o PostMark."
        )
    return "Dedica 20 minutos a leer el abstract y la conclusión de tu última lectura guardada y escribe 3 notas."


def build_weekly_quiz(
    pieces: list[DigestPiece],
    concept: dict[str, Any],
    rng: random.Random | None = None,
) -> QuizSpec:
    """Construye un mini quiz con posición aleatoria de la respuesta correcta y explicación <= 200 caracteres."""
    rng = rng or random.Random()
    question = str(concept.get("quiz_question") or f"¿Cuál es la característica clave de {concept['term']}?")
    correct_text = str(concept.get("quiz_correct") or "Optimiza el uso de recursos manteniendo la precisión")
    distractors = list(
        concept.get("quiz_distractors")
        or [
            "Duplica el número de capas sin cambiar los pesos",
            "Elimina el paso de tokenización en tiempo de inferencia",
            "Obliga a entrenar desde cero sin datos previos",
        ]
    )
    explanation = str(concept.get("quiz_explanation") or concept.get("explanation") or "")[:200]

    tagged = [(True, correct_text), *((False, d) for d in distractors[:3])]
    rng.shuffle(tagged)
    correct_idx = next(i for i, (is_corr, _) in enumerate(tagged) if is_corr)
    options = [text for _, text in tagged]

    return QuizSpec(
        question=question[:300],
        options=options,
        correct_option_id=correct_idx,
        explanation=explanation,
    )


def choose_weekly_format(week_num: int, last_format_index: int | None = None) -> tuple[int, str]:
    """Rota entre los 3 formatos sin repetir el mismo dos domingos seguidos."""
    idx = week_num % len(FORMAT_NAMES)
    if last_format_index is not None and idx == last_format_index:
        idx = (idx + 1) % len(FORMAT_NAMES)
    return idx, FORMAT_NAMES[idx]


def dominant_topic_for_shown_pieces(pieces: list[DigestPiece]) -> tuple[str, int]:
    """Calcula el tema dominante exactamente sobre las piezas que se muestran (desempatando por las del teaser)."""
    if not pieces:
        return "Tecnología", 0
    counts: dict[str, int] = {}
    teaser_counts: dict[str, int] = {}
    for idx, p in enumerate(pieces):
        counts[p.topic_label] = counts.get(p.topic_label, 0) + 1
        if idx < 3:
            teaser_counts[p.topic_label] = teaser_counts.get(p.topic_label, 0) + 1
    dominant = max(
        counts,
        key=lambda k: (counts[k], teaser_counts.get(k, 0)),
    )
    return dominant, counts[dominant]


def build_story_of_the_week(pieces: list[DigestPiece], llm_story: str | None = None) -> str:
    """Con LLM: frase a partir de las piezas. Sin LLM: línea de gancho concreta sobre las piezas mostradas."""
    if not pieces:
        return "Semana con pocas publicaciones que superaran el umbral de afinidad; solo se incluye lo verificado."
    if llm_story:
        return llm_story
    dominant, dom_count = dominant_topic_for_shown_pieces(pieces)
    sources = list(dict.fromkeys(p.source for p in pieces[:3]))
    src_str = ", ".join(sources[:2])
    return f"Foco en {dominant.lower()} ({dom_count} de {len(pieces)} lecturas, destacando {src_str})."


def build_weekly_digest(
    cfg: Config,
    candidates: list[TechCandidate] | None = None,
    ref_date: date | None = None,
    week_override: str | None = None,
    feedback_path: Path = FEEDBACK_JSON,
    sent_path: Path = SENT_JSON,
    quiz_rng: random.Random | None = None,
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
    max_per_src_digest = int(wcfg.get("max_pieces_per_source", 2))
    max_per_top_digest = int(wcfg.get("max_pieces_per_topic", 2))
    stack_kws = list(wcfg.get("user_stack") or DEFAULT_USER_STACK)

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
    selected = select_diverse_pieces(
        deduped,
        max_pieces=int(wcfg.get("max_pieces", 7)),
        max_per_source=max_per_src_digest,
        max_per_topic=max_per_top_digest,
    )

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
    challenge = build_weekly_challenge(pieces, stack_keywords=stack_kws)
    quiz = build_weekly_quiz(pieces, concept, rng=quiz_rng)
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
    """Formatea una pieza:
    - Título original intacto siempre con enlace al original.
    - Sin tiempo de lectura falso para papers; calculado por longitud real para noticias/herramientas.
    - Línea 🎯 solo si p.hook no está vacía.
    - Extracto solo si p.summary_es no está vacío.
    """
    link = html.escape(p.url, quote=True)
    radar_note = " · <i>fuera de tu radar habitual</i>" if p.outside_radar else ""
    meta_parts = ([f"{p.reading_minutes} min"] if p.reading_minutes > 0 else []) + [esc(p.source)]
    head = (
        f'{idx}. <b>[{esc(p.role)}]</b> <a href="{link}">{esc(p.original_title)}</a> '
        f"<i>({' · '.join(meta_parts)})</i>"
    )
    lines = [
        head,
        f"   🏷️ {esc(p.topic_label)}{radar_note}",
    ]
    if used_llm and p.title_es and p.title_es != p.original_title:
        lines.append(f"   🇪🇸 <i>{esc(p.title_es)}</i>")
    if p.hook:
        lines.append(f"   🎯 {esc(p.hook)}")
    if p.summary_es:
        lines.append(f"   {esc(p.summary_es)}")
    if p.project_line:
        lines.append(f"   🛠️ <i>{esc(p.project_line)}</i>")
    return "\n".join(lines)


def format_weekly_messages(
    digest: WeeklyDigest,
) -> tuple[list[str], list[dict[str, Any] | None], list[bool]]:
    """Construye los mensajes del Radar semanal:
    - Mensaje 1 (teaser pantalla de bloqueo, ÚNICO con sonido): 1 línea de gancho concreta + 3 titulares
      (con LLM en español; sin LLM título original con [EN]/[ES]).
    - Mensaje 2 (silencioso): papers de la semana con detalle y teclado inline.
    - Mensaje 3 (silencioso): lanzamientos/herramienta/joya + Concepto en 60s + Reto de la semana.
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

    teaser_lines = [f"{header} ({esc(digest.week_id)}) — {esc(digest.story_of_the_week)}"]
    for i, p in enumerate(digest.pieces[:3], 1):
        if digest.used_llm and p.title_es:
            teaser_lines.append(f"{i}. {esc(p.title_es)} <i>({esc(p.source)})</i>")
        else:
            teaser_lines.append(f"{i}. [{p.language_tag}] {esc(p.original_title)} <i>({esc(p.source)})</i>")
    msg1 = "\n".join(teaser_lines[:4])[:MAX_LEN]
    kb1 = build_piece_keyboard(digest.pieces[0].id, digest.pieces[0].topic)

    paper_roles = (ROLE_ACCESSIBLE, ROLE_DEEP, ROLE_APPLIED)
    papers = [p for p in digest.pieces if p.role in paper_roles]
    others = [p for p in digest.pieces if p.role not in paper_roles]

    m2_lines = ["📄 <b>Papers de la semana</b>"]
    for i, p in enumerate(papers, 1):
        m2_lines.append("")
        m2_lines.append(_format_piece_html(i, p, used_llm=digest.used_llm))
    msg2 = "\n".join(m2_lines)[:MAX_LEN]
    kb2 = build_piece_keyboard(papers[0].id, papers[0].topic) if papers else None

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
        read_tag = f" · **Lectura:** ~{p.reading_minutes} min" if p.reading_minutes > 0 else ""
        meta_line = f"- **Tema:** {p.topic_label}{radar_tag} · **Fuente:** {p.source}{read_tag}"
        lines.extend(
            [
                f"### {i}. [{p.role}] [{p.original_title}]({p.url})",
                meta_line,
            ]
        )
        if digest.used_llm and p.title_es and p.title_es != p.original_title:
            lines.append(f"- **Traducción:** {p.title_es}")
        if p.hook:
            lines.append(f"- **Por qué importa:** {p.hook}")
        if p.summary_es:
            lines.append(f"- **Extracto:** {p.summary_es}")
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
            "concept": {
                "term": digest.concept["term"],
                "explanation": digest.concept["explanation"],
                "everyday_example": digest.concept["everyday_example"],
                "from_readings": digest.concept.get("from_readings", False),
            },
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
        print(f"Explicación ({len(digest.quiz.explanation)} chars): {digest.quiz.explanation}")
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
