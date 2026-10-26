"""Movilidad: pico y placa (configurable) y alertas detectadas en las noticias."""

from __future__ import annotations

import re
from datetime import date

import holidays

from brief.config import Config
from brief.models import MobilitySummary, NewsItem
from brief.sources.news import normalize

DAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def pico_y_placa_text(cfg_section: dict, today: date, is_holiday: bool | None = None) -> str | None:
    """Texto de pico y placa para hoy, o None si no hay placa configurada.

    La regla oficial cambia cada semestre y no se consulta automáticamente: se calcula
    solo con lo definido en config.yaml (modo weekday/parity). Nunca se inventa.
    """
    plate = cfg_section.get("plate")
    if plate is None or str(plate).strip() == "":
        return None
    digit = int(str(plate).strip()[-1])
    mode = cfg_section.get("mode")
    if mode is None:
        return "Revisa la regla vigente de pico y placa en movilidadbogota.gov.co (no está configurada)."
    if is_holiday is None:
        is_holiday = today in holidays.country_holidays("CO", years=today.year)
    if today.weekday() >= 5 or is_holiday:
        return f"Hoy no hay pico y placa (placa {digit})."
    hours = " y ".join(cfg_section.get("hours", []))
    if mode == "weekday":
        restricted = cfg_section.get("restricted", {}) or {}
        digits = restricted.get(today.weekday(), restricted.get(str(today.weekday()), []))
        applies = digit in digits
    elif mode == "parity":
        applies = (digit % 2) == (today.day % 2)
    else:
        return "Modo de pico y placa no reconocido en config.yaml."
    if applies:
        return f"Hoy tienes pico y placa (placa {digit}), {hours}."
    return f"Hoy no tienes pico y placa (placa {digit})."


def find_alerts(news: list[NewsItem], keywords: list[str], max_alerts: int = 3) -> list[NewsItem]:
    """Noticias de Bogotá cuyo titular contiene palabras clave de movilidad/orden público."""
    kws = [normalize(k) for k in keywords]
    found: list[NewsItem] = []
    for item in sorted(news, key=lambda i: i.score, reverse=True):
        text = normalize(item.title)
        bogota_ctx = item.scope == "bogota" or "bogota" in text or "transmilenio" in text
        if bogota_ctx and any(re.search(rf"\b{re.escape(k)}\b", text) for k in kws):
            found.append(item)
        if len(found) >= max_alerts:
            break
    return found


def build_mobility(cfg: Config, news: list[NewsItem], today: date) -> MobilitySummary:
    mob = cfg.section("mobility")
    return MobilitySummary(
        pyp_text=pico_y_placa_text(cfg.section("pico_y_placa"), today),
        alerts=find_alerts(news, mob.get("keywords", []), int(mob.get("max_alerts", 3))),
    )
