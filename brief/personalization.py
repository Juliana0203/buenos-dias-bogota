"""Saludos personalizados en español colombiano: según día, clima y festivos."""

from __future__ import annotations

import random
from datetime import date, timedelta

import holidays

from brief.models import Greeting

DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]  # fmt: skip

GENERIC = [
    "Buenos días, {name} ☀️ Espero que hayas dormido muy bien.",
    "Arriba, {name}, hoy es {dia}.",
    "¡Buenos días, {name}! Que hoy te rinda el día.",
    "Hola, {name}, buen día. Aquí va tu resumen para salir tranquila a la calle.",
    "Buen día, {name}. Que la buena energía te acompañe hoy.",
    "Buenos días, {name}. Que sea un día lleno de buen ánimo.",
    "Hola, {name}. Un día nuevo para hacerlo con ganas y con calma.",
]
MONDAY = [
    "Buenos días, {name}. Lunes de arranque: con calma y con ganas.",
    "Arriba, {name}, es lunes. Semana nueva, oportunidades nuevas.",
]
FRIDAY = [
    "¡Feliz viernes, {name}! Ya casi llegamos al fin de semana.",
    "Buenos días, {name}. Viernes: aguanta un poquito más que ya se acerca el descanso.",
]
WEEKEND = [
    "Buenos días, {name}. Fin de semana: sin afanes, que hoy es {dia}.",
    "Hola, {name}, buen {dia}. Hoy toca ir con calma.",
]
RAIN = [
    "Ojo que hoy el cielo anda con ganas de llorar; mejor sal con paraguas.",
    "Parece que hoy Bogotá se pone romántica con la lluvia.",
]
HOLIDAY = ["Hoy es festivo ({holiday}): a disfrutar el descanso."]
HOLIDAY_TOMORROW = ["Dato: mañana es festivo ({holiday})."]


def date_es(d: date) -> str:
    """Fecha en español, p. ej. 'Domingo 25 de octubre'."""
    return f"{DAYS[d.weekday()].capitalize()} {d.day} de {MONTHS[d.month - 1]}"


def holiday_name(d: date) -> str | None:
    """Nombre del festivo colombiano para la fecha, o None."""
    return holidays.country_holidays("CO", years=d.year, language="es").get(d)


def build_greeting(
    name: str,
    today: date,
    rain_pct: float | None = None,
    rain_threshold: float = 50,
    rng: random.Random | None = None,
) -> Greeting:
    """Arma el saludo. El azar es determinista por fecha salvo que se inyecte un rng."""
    rng = rng or random.Random(today.toordinal())
    h_today = holiday_name(today)
    h_tomorrow = holiday_name(today + timedelta(days=1))
    wd = today.weekday()
    pool = GENERIC + (MONDAY if wd == 0 else FRIDAY if wd == 4 else WEEKEND if wd >= 5 else [])
    parts = [rng.choice(pool).format(name=name, dia=DAYS[wd])]
    if h_today:
        parts.append(rng.choice(HOLIDAY).format(holiday=h_today))
    elif h_tomorrow:
        parts.append(rng.choice(HOLIDAY_TOMORROW).format(holiday=h_tomorrow))
    if rain_pct is not None and rain_pct >= rain_threshold:
        parts.append(rng.choice(RAIN))
    return Greeting(text=" ".join(parts), holiday_today=h_today, holiday_tomorrow=h_tomorrow)
