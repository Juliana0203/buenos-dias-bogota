"""Gestión de clics inline de Telegram (getUpdates), aprendizaje de pesos y biblioteca personal."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brief.config import ROOT
from brief.telegram import answer_callback_query, fetch_updates

log = logging.getLogger(__name__)

FEEDBACK_JSON = ROOT / "data" / "feedback.json"
LIBRARY_JSON = ROOT / "docs" / "library.json"
LIBRARY_DIR = ROOT / "biblioteca"

WEIGHT_STEP_MORE = 0.15
WEIGHT_STEP_LESS = -0.15
WEIGHT_MIN = 0.4
WEIGHT_MAX = 3.0


def load_feedback(path: Path = FEEDBACK_JSON) -> dict[str, Any]:
    """Carga data/feedback.json o devuelve la estructura inicial."""
    if not path.exists():
        return {
            "last_update_id": 0,
            "processed_callback_ids": [],
            "topic_adjustments": {},
            "read_ids": [],
            "saved_ids": [],
            "piece_catalog": {},
            "events": [],
            "last_format_index": None,
            "sent_weeks": [],
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}
    return {
        "last_update_id": int(data.get("last_update_id", 0)),
        "processed_callback_ids": list(data.get("processed_callback_ids", [])),
        "topic_adjustments": dict(data.get("topic_adjustments", {})),
        "read_ids": list(data.get("read_ids", [])),
        "saved_ids": list(data.get("saved_ids", [])),
        "piece_catalog": dict(data.get("piece_catalog", {})),
        "events": list(data.get("events", [])),
        "last_format_index": data.get("last_format_index"),
        "sent_weeks": list(data.get("sent_weeks", [])),
    }


def save_feedback(state: dict[str, Any], path: Path = FEEDBACK_JSON) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Limita historiales internos para que el archivo se mantenga liviano
    state["processed_callback_ids"] = state.get("processed_callback_ids", [])[-500:]
    state["events"] = state.get("events", [])[-300:]
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def register_pieces_in_catalog(
    pieces: list[dict[str, Any]],
    path: Path = FEEDBACK_JSON,
) -> None:
    """Guarda metadatos de las piezas enviadas para archivarlas cuando el usuario pulse 'Guardar'."""
    state = load_feedback(path)
    catalog = state.setdefault("piece_catalog", {})
    for p in pieces:
        pid = str(p.get("id", ""))
        if pid:
            catalog[pid] = {
                "id": pid,
                "title_es": p.get("title_es", ""),
                "title": p.get("title", ""),
                "url": p.get("url", ""),
                "topic": p.get("topic", "software_engineering"),
                "topic_label": p.get("topic_label", p.get("topic", "")),
                "role": p.get("role", ""),
                "hook": p.get("hook", ""),
                "summary_es": p.get("summary_es", ""),
                "week": p.get("week", ""),
                "date": p.get("date", datetime.now(UTC).date().isoformat()),
            }
    save_feedback(state, path)


def effective_topic_weights(
    interests_cfg: dict[str, Any],
    state: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Combina los pesos base de config.yaml con los ajustes aprendidos en data/feedback.json."""
    adj = (state or {}).get("topic_adjustments", {})
    out: dict[str, float] = {}
    for topic, meta in interests_cfg.items():
        base = float(meta.get("weight", 1.0)) if isinstance(meta, dict) else float(meta)
        delta = float(adj.get(topic, 0.0))
        out[topic] = round(max(WEIGHT_MIN, min(WEIGHT_MAX, base + delta)), 3)
    return out


def add_to_library(
    piece: dict[str, Any],
    library_json: Path = LIBRARY_JSON,
    library_dir: Path = LIBRARY_DIR,
) -> bool:
    """Agrega una pieza a docs/library.json y a biblioteca/AAAA.md ordenada por tema.

    Devuelve True si se agregó por primera vez, False si ya estaba.
    """
    existing: list[dict[str, Any]] = []
    if library_json.exists():
        try:
            existing = json.loads(library_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []
    pid = piece.get("id")
    url = piece.get("url")
    if any((pid and item.get("id") == pid) or (url and item.get("url") == url) for item in existing):
        return False

    date_str = str(piece.get("date") or datetime.now(UTC).date().isoformat())
    year = date_str[:4] if len(date_str) >= 4 else str(datetime.now(UTC).year)
    entry = {
        "id": pid or url or "",
        "title_es": piece.get("title_es") or piece.get("title") or "Lectura guardada",
        "url": url or "",
        "topic": piece.get("topic") or "general",
        "topic_label": piece.get("topic_label") or piece.get("topic") or "General",
        "role": piece.get("role") or "",
        "hook": piece.get("hook") or "",
        "summary_es": piece.get("summary_es") or "",
        "week": piece.get("week") or "",
        "saved_at": date_str,
        "year": year,
    }
    existing.append(entry)
    library_json.parent.mkdir(parents=True, exist_ok=True)
    library_json.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    # Reconstruye biblioteca/AAAA.md ordenado por tema
    year_items = [i for i in existing if str(i.get("year")) == year]
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for item in year_items:
        label = str(item.get("topic_label") or item.get("topic") or "General")
        by_topic.setdefault(label, []).append(item)

    lines = [f"# Biblioteca personal de lecturas — {year}", ""]
    for topic_label in sorted(by_topic):
        lines.append(f"## {topic_label}")
        lines.append("")
        for item in by_topic[topic_label]:
            title = item["title_es"]
            link = item["url"]
            week = f" ({item['week']})" if item.get("week") else ""
            hook = f" — *{item['hook']}*" if item.get("hook") else ""
            lines.append(f"- [{title}]({link}){week}{hook}")
        lines.append("")

    library_dir.mkdir(parents=True, exist_ok=True)
    (library_dir / f"{year}.md").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return True


def apply_callback_updates(
    updates: list[dict[str, Any]],
    feedback_path: Path = FEEDBACK_JSON,
    library_json: Path = LIBRARY_JSON,
    library_dir: Path = LIBRARY_DIR,
    answer_fn: Any = None,
) -> dict[str, int]:
    """Procesa una lista de updates de Telegram (callback_query) sin duplicar eventos.

    Formatos de callback_data soportados:
    - ``save:<piece_id>``
    - ``read:<piece_id>``
    - ``more:<piece_id>:<topic>``
    - ``less:<piece_id>:<topic>``
    """
    state = load_feedback(feedback_path)
    last_id = int(state.get("last_update_id", 0))
    seen_cb = set(state.get("processed_callback_ids", []))
    counts = {"processed": 0, "skipped_duplicate": 0, "saved": 0, "read": 0, "more": 0, "less": 0}

    for upd in sorted(updates, key=lambda u: int(u.get("update_id", 0))):
        uid = int(upd.get("update_id", 0))
        cb = upd.get("callback_query") or {}
        cb_id = str(cb.get("id") or f"u_{uid}")
        if uid <= last_id or cb_id in seen_cb:
            counts["skipped_duplicate"] += 1
            continue

        data = str(cb.get("data") or "")
        parts = data.split(":")
        action = parts[0] if parts else ""
        piece_id = parts[1] if len(parts) > 1 else ""
        topic = parts[2] if len(parts) > 2 else ""

        catalog = state.get("piece_catalog", {})
        piece = catalog.get(piece_id, {"id": piece_id, "topic": topic or "general", "title_es": piece_id, "url": ""})
        if not topic:
            topic = str(piece.get("topic") or "general")

        reply_text = "Registrado"
        if action == "save":
            if piece_id and piece_id not in state["saved_ids"]:
                state["saved_ids"].append(piece_id)
            added = add_to_library(piece, library_json=library_json, library_dir=library_dir)
            counts["saved"] += 1
            reply_text = "📌 Guardado en tu biblioteca" if added else "📌 Ya estaba en tu biblioteca"
        elif action == "read":
            if piece_id and piece_id not in state["read_ids"]:
                state["read_ids"].append(piece_id)
            # Leer una pieza también sube ligeramente el peso de su tema
            adj = state.setdefault("topic_adjustments", {})
            adj[topic] = round(float(adj.get(topic, 0.0)) + 0.05, 3)
            counts["read"] += 1
            reply_text = "✅ Marcado como leído"
        elif action == "more":
            adj = state.setdefault("topic_adjustments", {})
            adj[topic] = round(float(adj.get(topic, 0.0)) + WEIGHT_STEP_MORE, 3)
            counts["more"] += 1
            reply_text = f"🔼 Más contenido de {topic}"
        elif action == "less":
            adj = state.setdefault("topic_adjustments", {})
            adj[topic] = round(float(adj.get(topic, 0.0)) + WEIGHT_STEP_LESS, 3)
            counts["less"] += 1
            reply_text = f"🔽 Menos contenido de {topic}"

        if answer_fn and cb.get("id"):
            answer_fn(str(cb["id"]), reply_text)

        state["events"].append(
            {
                "update_id": uid,
                "callback_id": cb_id,
                "action": action,
                "piece_id": piece_id,
                "topic": topic,
                "at": datetime.now(UTC).isoformat(),
            }
        )
        seen_cb.add(cb_id)
        state["processed_callback_ids"].append(cb_id)
        last_id = max(last_id, uid)
        counts["processed"] += 1

    state["last_update_id"] = last_id
    save_feedback(state, feedback_path)
    log.info(
        "Callbacks procesados: nuevos=%d, duplicados_omitidos=%d (save=%d, read=%d, more=%d, less=%d)",
        counts["processed"],
        counts["skipped_duplicate"],
        counts["saved"],
        counts["read"],
        counts["more"],
        counts["less"],
    )
    return counts


def sync_telegram_feedback(
    token: str | None,
    feedback_path: Path = FEEDBACK_JSON,
    library_json: Path = LIBRARY_JSON,
    library_dir: Path = LIBRARY_DIR,
) -> dict[str, int]:
    """Consulta getUpdates con offset=last_update_id+1 y aplica todos los callbacks pendientes."""
    if not token:
        log.info("Sin TELEGRAM_BOT_TOKEN: se omite sincronización de callbacks")
        return {"processed": 0, "skipped_duplicate": 0, "saved": 0, "read": 0, "more": 0, "less": 0}
    state = load_feedback(feedback_path)
    last_id = int(state.get("last_update_id", 0))
    offset = (last_id + 1) if last_id > 0 else None
    try:
        updates = fetch_updates(token, offset=offset)
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudieron leer callbacks de Telegram (getUpdates): %s", exc)
        return {"processed": 0, "skipped_duplicate": 0, "saved": 0, "read": 0, "more": 0, "less": 0}
    if not updates:
        log.info("Callbacks de Telegram: 0 eventos pendientes")
        return {"processed": 0, "skipped_duplicate": 0, "saved": 0, "read": 0, "more": 0, "less": 0}
    return apply_callback_updates(
        updates,
        feedback_path=feedback_path,
        library_json=library_json,
        library_dir=library_dir,
        answer_fn=lambda cb_id, txt: answer_callback_query(token, cb_id, txt),
    )


def build_piece_keyboard(piece_id: str, topic: str) -> dict[str, Any]:
    """Construye el teclado inline de Telegram para una pieza del digest."""
    return {
        "inline_keyboard": [
            [
                {"text": "📌 Guardar", "callback_data": f"save:{piece_id}"[:64]},
                {"text": "✅ Ya la leí", "callback_data": f"read:{piece_id}"[:64]},
            ],
            [
                {"text": "🔼 Más como esto", "callback_data": f"more:{piece_id}:{topic}"[:64]},
                {"text": "🔽 Menos como esto", "callback_data": f"less:{piece_id}:{topic}"[:64]},
            ],
        ]
    }
