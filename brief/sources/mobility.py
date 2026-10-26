"""Movilidad: pico y placa (configurable). Las noticias de movilidad las aporta sources/news.py."""

from __future__ import annotations

from datetime import date

import holidays

from brief.config import Config
from brief.models import MobilitySummary

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


def build_mobility(cfg: Config, today: date) -> MobilitySummary:
    """Pico y placa de hoy (si hay placa configurada); las noticias de movilidad van en Brief.news."""
    return MobilitySummary(pyp_text=pico_y_placa_text(cfg.section("pico_y_placa"), today))
