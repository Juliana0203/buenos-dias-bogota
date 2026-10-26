"""Arma el mensaje HTML de Telegram a partir del Brief."""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from brief.models import AirSummary, Brief, NewsItem, Stat, TrmSummary, WeatherSummary
from brief.personalization import date_es

MAX_LEN = 4096
SEP = "\n\n"
DEFAULT_PERIODS: dict[str, tuple[int, int]] = {"mañana": (6, 12), "tarde": (12, 18), "noche": (18, 24)}


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def _is_past_period(
    name: str,
    now_hour: int | None,
    periods: Mapping[str, tuple[int, int]] = DEFAULT_PERIODS,
) -> bool:
    """True si el programa corre fuera de 6:00-10:00 y la franja ya terminó."""
    if now_hour is None or 6 <= now_hour <= 10:
        return False
    bounds = periods.get(name) or DEFAULT_PERIODS.get(name)
    return bool(bounds and now_hour >= bounds[1])


def fmt_stat(stat: Stat, unit: str = "", decimals: int = 0) -> str:
    """Valor único, o rango 'entre X y Y' si las fuentes difieren."""
    if stat.value is None:
        return "—"
    f = f"{{:.{decimals}f}}"
    if stat.disagree and stat.low is not None and stat.high is not None:
        return f"entre {f.format(stat.low)} y {f.format(stat.high)}{unit}"
    return f"{f.format(stat.value)}{unit}"


def _fmt_money(v: float) -> str:
    return "$" + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _signed(v: float) -> str:
    return f"{v:+.2f}".replace(".", ",")


def clothing_tip(w: WeatherSummary, alerts: Mapping[str, float], now_hour: int | None = None) -> str:
    """Ropa según la sensación térmica del día (mínima más baja y máxima más alta de las franjas vigentes).

    < 12 °C abrigo; 12-17 °C chaqueta ligera; > 22 °C ropa fresca. Sin sensación térmica, usa la temperatura.
    """
    cold = alerts.get("feels_cold_c", 12)
    mild = alerts.get("feels_mild_c", 17)
    warm = alerts.get("feels_warm_c", 22)
    hour = w.now_hour if now_hour is None else now_hour
    if w.feels_periods:
        active = [
            f for name, f in w.feels_periods.items() if not _is_past_period(name, hour, w.period_hours)
        ] or list(w.feels_periods.values())
        low = min(f.low for f in active)
        high = max(f.high for f in active)
    else:
        low = w.tmin.value if w.tmin.value is not None else w.temp.value
        high = w.tmax.value
    tips: list[str] = []
    if low is not None:
        if low < cold:
            tips.append("abrigo para las horas frías")
        elif low <= mild:
            tips.append("chaqueta ligera")
    if high is not None and high > warm:
        tips.append("ropa fresca para las horas de más calor")
    return "lleva " + " y ".join(tips) if tips else "ropa cómoda, sin mayor abrigo"


def weather_recommendation(w: WeatherSummary, alerts: Mapping[str, float], now_hour: int | None = None) -> str:
    """Ropa, bloqueador y viento (el paraguas va en la sección de lluvia)."""
    tips: list[str] = [clothing_tip(w, alerts, now_hour)]
    if w.uv.value is not None and w.uv.value >= alerts.get("uv_high", 6):
        tips.append("no olvides el bloqueador")
    if w.wind_kmh.value is not None and w.wind_kmh.value >= alerts.get("wind_strong_kmh", 30):
        tips.append("hay viento fuerte")
    return "Recomendación: " + ", ".join(tips) + "."


def feels_line(w: WeatherSummary, now_hour: int | None = None) -> str | None:
    """'Sensación térmica: mañana 11-15 °C, tarde 15-18 °C, noche 12-14 °C' (marca 'ya pasó' en franjas vencidas)."""
    if not w.feels_periods:
        return None
    hour = w.now_hour if now_hour is None else now_hour
    parts = [
        f"{name} ya pasó" if _is_past_period(name, hour, w.period_hours) else f"{name} {f.low:.0f}-{f.high:.0f} °C"
        for name, f in w.feels_periods.items()
    ]
    return "Sensación térmica: " + ", ".join(parts)


def confidence_line(score: int | None) -> str | None:
    """'Confianza del dato: 87 %' (concordancia entre fuentes), o None si no hay puntaje."""
    return None if score is None else f"<i>Confianza del dato: {score} %</i>"


def weather_section(w: WeatherSummary, alerts: Mapping[str, float], now_hour: int | None = None) -> str:
    lines = ["🌦️ <b>Clima en Bogotá</b>"]
    if w.temp.value is not None:
        lines.append(f"Ahora: {fmt_stat(w.temp, ' °C')}")
    if w.tmin.value is not None or w.tmax.value is not None:
        lines.append(f"Temperatura real del día: mínima {fmt_stat(w.tmin, ' °C')} · máxima {fmt_stat(w.tmax, ' °C')}")
    feels = feels_line(w, now_hour)
    if feels:
        lines.append(feels)
    extra = []
    if w.wind_kmh.value is not None:
        extra.append(f"viento {fmt_stat(w.wind_kmh, ' km/h')}")
    if w.uv.value is not None:
        extra.append(f"índice UV {fmt_stat(w.uv, '', 0)}")
    if extra:
        text = ", ".join(extra)
        lines.append(text[0].upper() + text[1:])
    lines.append(weather_recommendation(w, alerts, now_hour))
    conf = confidence_line(w.score)
    if conf:
        lines.append(conf)
    return "\n".join(lines)


def rain_headline(period: str, stat: Stat) -> str:
    """Frase natural según la probabilidad de la franja más lluviosa."""
    pct = fmt_stat(stat, " %")
    v = stat.value or 0
    if v >= 70:
        return f"Lluvia muy probable en la {period} ({pct})"
    if v >= 40:
        return f"Lluvia probable en la {period} ({pct})"
    if v >= 20:
        return f"Poca probabilidad de lluvia, algo posible en la {period} ({pct})"
    return "No se espera lluvia"


def rain_section(w: WeatherSummary, alerts: Mapping[str, float], now_hour: int | None = None) -> str | None:
    """Sección de lluvia: titular natural, probabilidad por franja, hora pico y paraguas. Sin nombres de modelos."""
    avail = {k: s for k, s in w.rain_periods.items() if s.value is not None}
    if not avail:
        return None
    hour = w.now_hour if now_hour is None else now_hour
    future = {k: s for k, s in avail.items() if not _is_past_period(k, hour, w.period_hours)}
    active = future or avail
    peak_name, peak = max(active.items(), key=lambda kv: kv[1].value or 0)
    lines = ["☔ <b>Lluvia</b>", rain_headline(peak_name, peak) + "."]
    any_past = len(future) < len(avail)
    if (peak.value or 0) >= 20 or any_past:
        parts = [
            f"{k} ya pasó" if _is_past_period(k, hour, w.period_hours) else f"{k} {fmt_stat(s, ' %')}"
            for k, s in avail.items()
        ]
        lines.append("Por franja: " + ", ".join(parts))
        if (
            (peak.value or 0) >= 20
            and w.rain_peak_hour is not None
            and (w.rain_peak_pct or 0) >= 30
            and (hour is None or 6 <= hour <= 10 or w.rain_peak_hour >= hour)
        ):
            lines.append(f"Hora más probable: {w.rain_peak_hour}:00.")
    if (peak.value or 0) >= alerts.get("rain_umbrella_pct", 40):
        lines.append("Lleva paraguas.")
    conf = confidence_line(w.rain_score)
    if conf:
        lines.append(conf)
    return "\n".join(lines)


def air_recommendation(a: AirSummary) -> str:
    v = a.aqi.value or 0
    if v <= 50:
        return "Buen día para hacer deporte al aire libre."
    if v <= 100:
        return "Aceptable; si eres sensible, modera el ejercicio intenso."
    if v <= 150:
        return "Personas sensibles: reduce el ejercicio al aire libre."
    return "Evita el ejercicio al aire libre y usa tapabocas si sales."


def air_section(a: AirSummary) -> str:
    pol = f", contaminante principal {esc(a.pollutant)}" if a.pollutant else ""
    lines = ["🍃 <b>Calidad del aire</b>", f"AQI {fmt_stat(a.aqi)}: {esc(a.level)}{pol}.", air_recommendation(a)]
    conf = confidence_line(a.score)
    if conf:
        lines.append(conf)
    return "\n".join(lines)


def trm_section(t: TrmSummary) -> str:
    lines = ["💵 <b>TRM</b>", f"Hoy: {_fmt_money(t.value)} por dólar"]
    if t.is_weekend_or_holiday:
        lines[-1] += " (valor vigente del último día hábil)"
    elif t.change_abs is not None and t.change_pct is not None:
        lines[-1] += f" ({_signed(t.change_abs)} · {_signed(t.change_pct)}% vs. ayer)"
    if t.vs_avg30_pct is not None and t.avg30 is not None:
        lines.append(f"Frente al promedio de 30 días ({_fmt_money(t.avg30)}): {_signed(t.vs_avg30_pct)}%")
    if t.is_min30:
        lines.append("Alerta: está en su mínimo de los últimos 30 días.")
    elif t.is_max30:
        lines.append("Alerta: está en su máximo de los últimos 30 días.")
    conf = confidence_line(t.score)
    if conf:
        lines.append(conf)
    return "\n".join(lines)


EMPTY_MOBILITY = "Sin alertas de movilidad importantes por ahora"


def mobility_section(brief: Brief) -> str:
    """Pico y placa + noticias de Bogotá sobre movilidad/calle (titular, resumen propio y enlace)."""
    m = brief.mobility
    lines = ["🚌 <b>Movilidad y calle</b>"]
    if m.pyp_text:
        lines.append(esc(m.pyp_text))
    if not brief.news:
        lines.append(EMPTY_MOBILITY)
    for n in brief.news:
        lines.append(f'• <a href="{html.escape(n.link, quote=True)}">{esc(n.title)}</a> — {esc(n.source)}')
        if n.summary:
            lines.append(f"  {esc(n.summary)}")
    return "\n".join(lines)


def good_news_section(n: NewsItem) -> str:
    """Una sola noticia positiva: titular con enlace, fuente y resumen propio corto."""
    lines = ["🌱 <b>Una buena noticia</b>"]
    lines.append(f'<a href="{html.escape(n.link, quote=True)}">{esc(n.title)}</a> — {esc(n.source)}')
    if n.summary:
        lines.append(esc(n.summary))
    return "\n".join(lines)


def build_sections(brief: Brief, alerts: Mapping[str, float]) -> list[str]:
    """Secciones en el orden pedido; las que no tienen datos se reemplazan por una nota breve."""
    sections = [f"{esc(brief.greeting.text)}\n<b>{esc(date_es(brief.today))}</b>"]
    omitted: list[str] = []
    for label, data, builder in (
        ("clima", brief.weather, lambda: weather_section(brief.weather, alerts)),  # type: ignore[arg-type]
        ("lluvia", brief.weather, lambda: rain_section(brief.weather, alerts)),  # type: ignore[arg-type]
        ("calidad del aire", brief.air, lambda: air_section(brief.air)),  # type: ignore[arg-type]
        ("TRM", brief.trm, lambda: trm_section(brief.trm)),  # type: ignore[arg-type]
    ):
        if data is None:
            if label != "lluvia":  # si falla el clima ya se avisa una sola vez
                omitted.append(label)
            continue
        text = builder()
        if text:
            sections.append(text)
        else:
            omitted.append(label)
    sections.append(mobility_section(brief))
    if brief.good_news and all(
        brief.good_news.link != n.link and brief.good_news.title != n.title for n in brief.news
    ):
        sections.append(good_news_section(brief.good_news))
    sections.append(f"✨ <i>“{esc(brief.quote.text)}”</i>\n— {esc(brief.quote.author)}")
    if omitted:
        sections.append("<i>No pude verificar hoy: " + ", ".join(omitted) + ".</i>")
    sections.append(f"Cuídate mucho, {esc(brief.name)}. ¡Que sea un gran día!")
    return sections


def split_message(sections: list[str], limit: int = MAX_LEN) -> list[str]:
    """Agrupa secciones en mensajes <= limit; parte por líneas si una sección es demasiado larga."""
    pieces: list[str] = []
    for sec in sections:
        if len(sec) <= limit:
            pieces.append(sec)
            continue
        cur = ""
        for line in sec.split("\n"):
            while len(line) > limit:
                if cur:
                    pieces.append(cur)
                    cur = ""
                pieces.append(line[:limit])
                line = line[limit:]
            if cur and len(cur) + 1 + len(line) > limit:
                pieces.append(cur)
                cur = line
            else:
                cur = f"{cur}\n{line}" if cur else line
        if cur:
            pieces.append(cur)
    messages: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len(cur) + len(SEP) + len(p) > limit:
            messages.append(cur)
            cur = p
        else:
            cur = f"{cur}{SEP}{p}" if cur else p
    if cur:
        messages.append(cur)
    return messages


def format_brief(brief: Brief, alerts: Mapping[str, Any] | None = None) -> list[str]:
    """Mensajes HTML listos para Telegram (uno o dos)."""
    return split_message(build_sections(brief, alerts or {}))
