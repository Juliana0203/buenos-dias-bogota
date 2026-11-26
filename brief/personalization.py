"""Saludos personalizados en español colombiano: más de 40 plantillas sin repetir dos días seguidos."""

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
    "Buenos días, {name}. Respira profundo y arranca este {dia} a tu ritmo.",
    "¡Hola, {name}! Que este {dia} traiga claridad y buenas noticias.",
    "Buen día, {name}. Paso a dejarte lo esencial antes de salir.",
    "Buenos días, {name}. Que todo fluya con serenidad en este {dia}.",
    "Hola, {name}. Aquí tienes el panorama de Bogotá para hoy.",
    "¡Arriba ese ánimo, {name}! Que tengas un {dia} muy productivo.",
    "Buen {dia}, {name}. Un vistazo rápido a lo que importa hoy.",
    "Buenos días, {name}. Que la jornada venga ligera y enfocada.",
    "Hola, {name}. Lista la brújula del día para moverte por Bogotá.",
    "¡Buen día, {name}! Que hoy encuentres foco en lo importante.",
    "Buenos días, {name}. Un {dia} más para avanzar paso a paso.",
    "Hola, {name}, feliz {dia}. Aquí está lo clave para tu jornada.",
    "Buen día, {name}. Que este {dia} te deje buenas conversaciones y logros.",
    "Buenos días, {name}. Arranca con tranquilidad, aquí va el reporte de hoy.",
    "¡Hola, {name}! Un resumen directo al grano para tu {dia}.",
    "Buen {dia}, {name}. Que el día te sorprenda con algo bueno.",
    "Buenos días, {name}. Con cabeza fría y paso firme en este {dia}.",
    "Hola, {name}. Te dejo las señales importantes del día en la ciudad.",
    "¡Buenos días, {name}! Que la calma marque el ritmo de hoy.",
    "Buen día, {name}. Todo listo para que planifiques tu {dia} sin sorpresas.",
    "Hola, {name}. Que hoy avances en lo que más te entusiasma.",
    "Buenos días, {name}. Un reporte breve para que aproveches la mañana.",
    "¡Feliz {dia}, {name}! Que cada hora de hoy sume a tus planes.",
    "Buen día, {name}. Miremos cómo amanece Bogotá este {dia}.",
    "Hola, {name}. Que tengas una mañana despejada y sin contratiempos.",
    "Buenos días, {name}. Aquí va el pulso de la ciudad para empezar bien.",
]
MONDAY = [
    "Buenos días, {name}. Lunes de arranque: con calma y con ganas.",
    "Arriba, {name}, es lunes. Semana nueva, oportunidades nuevas.",
    "¡Feliz lunes, {name}! Primer paso de la semana, sin prisa pero con foco.",
    "Buen lunes, {name}. Semana en blanco para escribir buenos avances.",
]
FRIDAY = [
    "¡Feliz viernes, {name}! Ya casi llegamos al fin de semana.",
    "Buenos días, {name}. Viernes: aguanta un poquito más que ya se acerca el descanso.",
    "Hola, {name}, ¡por fin viernes! Último empujón antes de desconectar.",
    "Buen viernes, {name}. A cerrar la semana con broche de oro.",
]
WEEKEND = [
    "Buenos días, {name}. Fin de semana: sin afanes, que hoy es {dia}.",
    "Hola, {name}, buen {dia}. Hoy toca ir con calma.",
    "¡Feliz {dia}, {name}! Tiempo para recargar baterías y descansar.",
    "Buen {dia} de descanso, {name}. Que disfrutes la jornada a tu manera.",
]
RAIN = [
    "Ojo que hoy el cielo anda con ganas de llorar; mejor sal con paraguas.",
    "Parece que hoy Bogotá se pone romántica con la lluvia.",
]
HOLIDAY = ["Hoy es festivo ({holiday}): a disfrutar el descanso."]
HOLIDAY_TOMORROW = ["Dato: mañana es festivo ({holiday})."]

ALL_TEMPLATES: list[tuple[str, str]] = (
    [(f"gen_{i:02d}", t) for i, t in enumerate(GENERIC)]
    + [(f"mon_{i:02d}", t) for i, t in enumerate(MONDAY)]
    + [(f"fri_{i:02d}", t) for i, t in enumerate(FRIDAY)]
    + [(f"wkd_{i:02d}", t) for i, t in enumerate(WEEKEND)]
)


def date_es(d: date) -> str:
    """Fecha en español, p. ej. 'Domingo 25 de octubre'."""
    return f"{DAYS[d.weekday()].capitalize()} {d.day} de {MONTHS[d.month - 1]}"


def holiday_name(d: date) -> str | None:
    """Nombre del festivo colombiano para la fecha, o None."""
    return holidays.country_holidays("CO", years=d.year, language="es").get(d)


def _pool_with_ids(wd: int) -> list[tuple[str, str]]:
    pool = [(f"gen_{i:02d}", t) for i, t in enumerate(GENERIC)]
    if wd == 0:
        pool.extend((f"mon_{i:02d}", t) for i, t in enumerate(MONDAY))
    elif wd == 4:
        pool.extend((f"fri_{i:02d}", t) for i, t in enumerate(FRIDAY))
    elif wd >= 5:
        pool.extend((f"wkd_{i:02d}", t) for i, t in enumerate(WEEKEND))
    return pool


def build_greeting(
    name: str,
    today: date,
    rain_pct: float | None = None,
    rain_threshold: float = 50,
    rng: random.Random | None = None,
    last_template_id: str | None = None,
) -> Greeting:
    """Arma el saludo. El azar es determinista por fecha salvo que se inyecte un rng.

    Si ``last_template_id`` coincide con la estructura sorteada, rota a otra para no
    repetir dos días seguidos.
    """
    rng = rng or random.Random(today.toordinal())
    h_today = holiday_name(today)
    h_tomorrow = holiday_name(today + timedelta(days=1))
    wd = today.weekday()
    pool = _pool_with_ids(wd)
    tpl_id, tpl = rng.choice(pool)
    if last_template_id and tpl_id == last_template_id and len(pool) > 1:
        others = [item for item in pool if item[0] != last_template_id]
        tpl_id, tpl = rng.choice(others)
    parts = [tpl.format(name=name, dia=DAYS[wd])]
    if h_today:
        parts.append(rng.choice(HOLIDAY).format(holiday=h_today))
    elif h_tomorrow:
        parts.append(rng.choice(HOLIDAY_TOMORROW).format(holiday=h_tomorrow))
    if rain_pct is not None and rain_pct >= rain_threshold:
        parts.append(rng.choice(RAIN))
    return Greeting(
        text=" ".join(parts),
        holiday_today=h_today,
        holiday_tomorrow=h_tomorrow,
        template_id=tpl_id,
    )
