"""Arma el mensaje HTML de Telegram a partir del Brief."""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from brief.models import AirSummary, Brief, Stat, TrmSummary, WeatherSummary
from brief.personalization import date_es

MAX_LEN = 4096
SEP = "\n\n"


def esc(text: str) -> str:
    return html.escape(text, quote=False)


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


def weather_recommendation(w: WeatherSummary, alerts: Mapping[str, float]) -> str:
    tips: list[str] = []
    rain = max((s.value or 0 for s in w.rain_periods.values()), default=0)
    if rain >= alerts.get("rain_umbrella_pct", 40):
        tips.append("lleva paraguas")
    tmin = w.tmin.value if w.tmin.value is not None else w.temp.value
    if tmin is not None and tmin <= alerts.get("cold_c", 12):
        tips.append("abrígate bien en la mañana")
    elif w.tmax.value is not None and w.tmax.value >= alerts.get("hot_c", 24):
        tips.append("ropa liviana para la tarde")
    else:
        tips.append("lleva una chaqueta ligera por si acaso")
    if w.uv.value is not None and w.uv.value >= alerts.get("uv_high", 6):
        tips.append("no olvides el bloqueador")
    if w.wind_kmh.value is not None and w.wind_kmh.value >= alerts.get("wind_strong_kmh", 30):
        tips.append("hay viento fuerte")
    return "Recomendación: " + ", ".join(tips) + "."


def weather_section(w: WeatherSummary, alerts: Mapping[str, float]) -> str:
    lines = ["🌦️ <b>Clima en Bogotá</b>"]
    if w.temp.value is not None:
        now = f"Ahora: {fmt_stat(w.temp, ' °C')}"
        if w.feels_like.value is not None:
            now += f" (sensación {fmt_stat(w.feels_like, ' °C')})"
        lines.append(now)
    if w.tmin.value is not None or w.tmax.value is not None:
        lines.append(f"Mínima {fmt_stat(w.tmin, ' °C')} · Máxima {fmt_stat(w.tmax, ' °C')}")
    if w.rain_periods:
        parts = [f"{name} {fmt_stat(s, '%')}" for name, s in w.rain_periods.items() if s.value is not None]
        if parts:
            lines.append("Lluvia: " + " · ".join(parts))
    if w.rain_peak_hour is not None and (w.rain_peak_pct or 0) >= 30:
        lines.append(f"Hora más probable de lluvia: {w.rain_peak_hour}:00 ({w.rain_peak_pct:.0f}%)")
    extra = []
    if w.wind_kmh.value is not None:
        extra.append(f"viento {fmt_stat(w.wind_kmh, ' km/h')}")
    if w.uv.value is not None:
        extra.append(f"índice UV {fmt_stat(w.uv, '', 0)}")
    if extra:
        text = ", ".join(extra)
        lines.append(text[0].upper() + text[1:])
    if w.rain_note:
        lines.append(w.rain_note[0].upper() + w.rain_note[1:] + ".")
    lines.append(weather_recommendation(w, alerts))
    lines.append(f"<i>{_verification_note(w)} Confianza {w.confidence}.</i>")
    return "\n".join(lines)


def _count(n: int) -> str:
    return f"{n} fuente{'s' if n != 1 else ''}"


def _verification_note(w: WeatherSummary) -> str:
    """Cuenta solo las fuentes que votaron en cada variable (temperatura y lluvia)."""
    temp_src = w.temp.sources
    rain_src = w.rain_sources
    if rain_src and set(rain_src) != set(temp_src):
        note = f"Verificado con {_count(len(temp_src))} en temperatura ({esc(', '.join(temp_src))})"
        note += f" y {_count(len(rain_src))} en lluvia ({esc(', '.join(rain_src))})."
    else:
        src = temp_src or rain_src
        note = f"Verificado con {_count(len(src))} ({esc(', '.join(src))})."
    stats = (w.temp, w.tmin, w.tmax, *w.rain_periods.values())
    if any(s.disagree for s in stats):
        note += " Las fuentes difieren, por eso algunos datos van en rango."
    return note


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
    return (
        "🍃 <b>Calidad del aire</b>\n"
        f"AQI {fmt_stat(a.aqi)}: {esc(a.level)}{pol}.\n"
        f"{air_recommendation(a)}\n"
        f"<i>{len(a.sources)} fuente{'s' if len(a.sources) != 1 else ''} ({esc(', '.join(a.sources))}). "
        f"Confianza {a.confidence}.</i>"
    )


def trm_section(t: TrmSummary) -> str:
    lines = ["💵 <b>TRM</b>", f"Hoy: {_fmt_money(t.value)} por dólar"]
    if t.change_abs is not None and t.change_pct is not None:
        lines[-1] += f" ({_signed(t.change_abs)} · {_signed(t.change_pct)}% vs. ayer)"
    if t.vs_avg30_pct is not None and t.avg30 is not None:
        lines.append(f"Frente al promedio de 30 días ({_fmt_money(t.avg30)}): {_signed(t.vs_avg30_pct)}%")
    if t.is_min30:
        lines.append("Alerta: está en su mínimo de los últimos 30 días.")
    elif t.is_max30:
        lines.append("Alerta: está en su máximo de los últimos 30 días.")
    lines.append(f"<i>Fuente: {esc(', '.join(t.sources))}.</i>")
    return "\n".join(lines)


def mobility_section(brief: Brief) -> str:
    m = brief.mobility
    lines = ["🚌 <b>Movilidad y calle</b>"]
    if m.pyp_text:
        lines.append(esc(m.pyp_text))
    if m.alerts:
        for a in m.alerts:
            lines.append(f'• <a href="{html.escape(a.link, quote=True)}">{esc(a.title)}</a> ({esc(a.source)})')
    else:
        lines.append("Sin alertas de Transmilenio, cierres o marchas en las noticias de las últimas 24 h.")
    return "\n".join(lines)


def news_section(brief: Brief) -> str | None:
    if not brief.news:
        return None
    lines = ["📰 <b>Noticias</b>"]
    for n in brief.news:
        lines.append(f'• <a href="{html.escape(n.link, quote=True)}">{esc(n.title)}</a> — {esc(n.source)}')
        if n.summary:
            lines.append(f"  {esc(n.summary)}")
    return "\n".join(lines)


def build_sections(brief: Brief, alerts: Mapping[str, float]) -> list[str]:
    """Secciones en el orden pedido; las que no tienen datos se reemplazan por una nota breve."""
    sections = [f"{esc(brief.greeting.text)}\n<b>{esc(date_es(brief.today))}</b>"]
    omitted: list[str] = []
    for label, data, builder in (
        ("clima", brief.weather, lambda: weather_section(brief.weather, alerts)),  # type: ignore[arg-type]
        ("calidad del aire", brief.air, lambda: air_section(brief.air)),  # type: ignore[arg-type]
        ("TRM", brief.trm, lambda: trm_section(brief.trm)),  # type: ignore[arg-type]
    ):
        if data is None:
            omitted.append(label)
        else:
            sections.append(builder())
    sections.append(mobility_section(brief))
    news = news_section(brief)
    if news:
        sections.append(news)
    else:
        omitted.append("noticias")
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
