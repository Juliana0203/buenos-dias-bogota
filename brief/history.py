"""Histórico en CSV, resumen latest.json e history.json para el dashboard."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from brief.config import ROOT
from brief.models import CONF_HIGH, CONF_LOW, CONF_MID, Brief

HISTORY_CSV = ROOT / "data" / "history.csv"
SENT_JSON = ROOT / "data" / "sent.json"
DOCS = ROOT / "docs"
FIELDS = [
    "date",
    "sent",
    "temp_min",
    "temp_max",
    "rain_prob",
    "aqi",
    "trm",
    "confidence",
    "sources",
    "greeting_id",
]
_RANK = {CONF_LOW: 0, CONF_MID: 1, CONF_HIGH: 2}


def read_history(path: Path = HISTORY_CSV) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def already_sent_today(today: date, path: Path = HISTORY_CSV) -> bool:
    """True si ya hay una fila enviada (sent=1) para hoy."""
    iso = today.isoformat()
    return any(r.get("date") == iso and r.get("sent") == "1" for r in read_history(path))


def last_greeting_id(today: date, path: Path = HISTORY_CSV) -> str | None:
    """Devuelve el template_id del saludo del día anterior más reciente en history.csv, o None."""
    iso = today.isoformat()
    prior = [r for r in read_history(path) if r.get("date") and r["date"] < iso and r.get("greeting_id")]
    return prior[-1]["greeting_id"] if prior else None


def read_sent_registry(path: Path = SENT_JSON) -> dict[str, Any]:
    """Lee el registro de envíos reales data/sent.json (nunca se escribe en --dry-run)."""
    if not path.exists():
        return {"sent_weeks": [], "last_trm_extreme_date": None, "last_weekly_format_index": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}
    return {
        "sent_weeks": list(data.get("sent_weeks") or []),
        "last_trm_extreme_date": data.get("last_trm_extreme_date"),
        "last_weekly_format_index": data.get("last_weekly_format_index"),
    }


def write_sent_registry(data: dict[str, Any], path: Path = SENT_JSON) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def last_trm_extreme_alert_date(path: Path = SENT_JSON) -> date | None:
    raw = read_sent_registry(path).get("last_trm_extreme_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def record_trm_extreme_alert(day: date, path: Path = SENT_JSON) -> None:
    reg = read_sent_registry(path)
    reg["last_trm_extreme_date"] = day.isoformat()
    write_sent_registry(reg, path)


def _num(v: float | None, nd: int = 1) -> str:
    return "" if v is None else f"{v:.{nd}f}"


def build_row(brief: Brief, sent: bool) -> dict[str, str]:
    w, a, t = brief.weather, brief.air, brief.trm
    confs = [
        c
        for c in (
            w.confidence if w else None,
            a.confidence if a else None,
            t.confidence if t else None,
        )
        if c
    ]
    sources: list[str] = []
    for s in (w.sources if w else []) + (a.sources if a else []) + (t.sources if t else []):
        if s not in sources:
            sources.append(s)
    rain = max((s.value for s in w.rain_periods.values() if s.value is not None), default=None) if w else None
    return {
        "date": brief.today.isoformat(),
        "sent": "1" if sent else "0",
        "temp_min": _num(w.tmin.value) if w else "",
        "temp_max": _num(w.tmax.value) if w else "",
        "rain_prob": _num(rain, 0),
        "aqi": _num(a.aqi.value, 0) if a else "",
        "trm": _num(t.value, 2) if t else "",
        "confidence": min(confs, key=lambda c: _RANK[c]) if confs else "",
        "sources": "; ".join(sources),
        "greeting_id": brief.greeting.template_id,
    }


def append_row(row: dict[str, str], path: Path = HISTORY_CSV) -> None:
    """Agrega una fila; reemplaza la de la misma fecha si existía sin enviar."""
    rows = [r for r in read_history(path) if not (r["date"] == row["date"] and r.get("sent") != "1")]
    rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({k: r.get(k, "") for k in FIELDS} for r in rows)


def _default(o: Any) -> Any:
    """Serializador JSON para dataclasses, fechas y conjuntos; falla claro con otros tipos."""
    if is_dataclass(o) and not isinstance(o, type):
        return asdict(o)
    if isinstance(o, datetime | date):
        return o.isoformat()
    if isinstance(o, set | frozenset):
        return sorted(o, key=str)
    raise TypeError(f"Objeto de tipo {type(o).__name__} no serializable a JSON")


def write_latest(brief: Brief, row: dict[str, str], docs: Path = DOCS) -> None:
    """Reescribe docs/latest.json con el resumen del día."""
    docs.mkdir(parents=True, exist_ok=True)
    data = {
        "date": row["date"],
        "name": brief.name,
        "greeting": brief.greeting.text,
        "greeting_id": brief.greeting.template_id,
        "relevance": brief.relevance,
        "summary": row,
        "weather": brief.weather,
        "air": brief.air
        and {
            "aqi": brief.air.aqi.value,
            "level": brief.air.level,
            "pollutant": brief.air.pollutant,
            "sources": brief.air.sources,
            "confidence": brief.air.confidence,
        },
        "trm": brief.trm,
        "news": [{"title": n.title, "link": n.link, "source": n.source} for n in brief.news],
        "quote": brief.quote,
    }
    (docs / "latest.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=_default), encoding="utf-8"
    )


def write_history_json(path: Path = HISTORY_CSV, docs: Path = DOCS, days: int = 60) -> None:
    """Copia reciente del CSV en docs/history.json (Pages solo sirve /docs)."""
    docs.mkdir(parents=True, exist_ok=True)
    rows = read_history(path)[-days:]
    (docs / "history.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
