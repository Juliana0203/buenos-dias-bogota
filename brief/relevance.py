"""Evaluación de reglas de relevancia para el aviso diario inteligente (Fase 1)."""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from brief.formatter import (
    _fmt_money,
    _is_past_period,
    _signed,
    clothing_tip,
    esc,
    fmt_stat,
)
from brief.models import Brief, RelevanceAlert, RelevanceResult
from brief.personalization import date_es


def evaluate_relevance(
    brief: Brief,
    rel_cfg: Mapping[str, Any],
    alerts_cfg: Mapping[str, float] | None = None,
) -> RelevanceResult:
    """Evalúa las reglas de relevancia (con sonido si hay alertas, o silencioso de 2 líneas)."""
    alerts_cfg = alerts_cfg or {}
    found: list[RelevanceAlert] = []
    w = brief.weather
    a = brief.air
    t = brief.trm

    # 1. Lluvia probable en la franja en que salgo (>= 70 %)
    outing_periods = list(rel_cfg.get("outing_periods") or ["mañana", "tarde"])
    rain_threshold = float(rel_cfg.get("rain_outing_pct", 70.0))
    if w and w.rain_periods:
        hour = w.now_hour
        best_period: str | None = None
        best_val = -1.0
        for p in outing_periods:
            stat = w.rain_periods.get(p)
            if stat and stat.value is not None and not _is_past_period(p, hour, w.period_hours):
                if stat.value >= rain_threshold and stat.value > best_val:
                    best_val = stat.value
                    best_period = p
        if best_period is not None:
            peak_info = f" (pico hacia las {w.rain_peak_hour}:00)" if w.rain_peak_hour is not None else ""
            conf = f" · confianza {w.rain_score} %" if w.rain_score is not None else ""
            headline = (
                f"☔ <b>Hoy sí lleva paraguas:</b> lluvia al {best_val:.0f} % en la {best_period}{peak_info}{conf}."
            )
            found.append(RelevanceAlert(key="rain_outing", priority=1, headline=headline))

    # 2. Alerta de movilidad real (cierres, paros, marchas, o pico y placa que afecte hoy)
    if rel_cfg.get("mobility_alert", True):
        pyp = brief.mobility.pyp_text or ""
        if "Hoy tienes pico y placa" in pyp:
            found.append(
                RelevanceAlert(
                    key="pico_y_placa",
                    priority=2,
                    headline=f"🚗 <b>Pico y placa activo:</b> {esc(pyp)}",
                )
            )
        if brief.news:
            top = brief.news[0]
            link = html.escape(top.link, quote=True)
            extra = f" (+{len(brief.news) - 1} alerta(s) más)" if len(brief.news) > 1 else ""
            headline = (
                f'🚌 <b>Alerta de movilidad:</b> <a href="{link}">{esc(top.title)}</a> ({esc(top.source)}){extra}.'
            )
            found.append(RelevanceAlert(key="mobility_news", priority=2, headline=headline))

    # 3. Calidad del aire dañina (AQI >= umbral, por defecto 101)
    aqi_threshold = float(rel_cfg.get("aqi_harmful", 101.0))
    if a and a.aqi.value is not None and a.aqi.value >= aqi_threshold:
        pol = f" ({esc(a.pollutant)})" if a.pollutant else ""
        conf = f" · confianza {a.score} %" if a.score is not None else ""
        headline = (
            f"🍃 <b>Aire dañino hoy:</b> AQI {fmt_stat(a.aqi)}{pol}, {esc(a.level)}{conf}. Modera el ejercicio afuera."
        )
        found.append(RelevanceAlert(key="aqi_harmful", priority=3, headline=headline))

    # 4. Sensación térmica extrema (frío <= feels_cold_extreme_c o calor >= feels_hot_extreme_c)
    cold_ext = float(rel_cfg.get("feels_cold_extreme_c", 9.0))
    hot_ext = float(rel_cfg.get("feels_hot_extreme_c", 23.0))
    if w:
        hour = w.now_hour
        active_feels = (
            [f for name, f in w.feels_periods.items() if not _is_past_period(name, hour, w.period_hours)]
            if w.feels_periods
            else []
        ) or (list(w.feels_periods.values()) if w.feels_periods else [])
        low_feel = min((f.low for f in active_feels), default=w.tmin.value)
        high_feel = max((f.high for f in active_feels), default=w.tmax.value)
        if low_feel is not None and low_feel <= cold_ext:
            headline = (
                f"🥶 <b>Madrugada o mañana muy fría:</b> sensación térmica baja hasta {low_feel:.0f} °C; "
                "sal bien abrigada."
            )
            found.append(RelevanceAlert(key="feels_extreme_cold", priority=4, headline=headline))
        elif high_feel is not None and high_feel >= hot_ext:
            headline = (
                f"🌡️ <b>Calor inusual en Bogotá:</b> sensación térmica de hasta {high_feel:.0f} °C; "
                "usa ropa fresca e hidrátate."
            )
            found.append(RelevanceAlert(key="feels_extreme_hot", priority=4, headline=headline))

    # 5. Índice UV extremo (>= 8)
    uv_threshold = float(rel_cfg.get("uv_extreme", 8.0))
    if w and w.uv.value is not None and w.uv.value >= uv_threshold:
        headline = f"☀️ <b>Radiación UV muy alta:</b> índice UV {fmt_stat(w.uv, '', 0)}; indispensable bloqueador solar."
        found.append(RelevanceAlert(key="uv_extreme", priority=5, headline=headline))

    # 6. Variación de la TRM >= 1.5 % o mínimo/máximo de 30 días
    trm_threshold = float(rel_cfg.get("trm_change_pct", 1.5))
    if t:
        big_change = t.change_pct is not None and abs(t.change_pct) >= trm_threshold
        if big_change or t.is_min30 or t.is_max30:
            tags: list[str] = []
            if t.change_pct is not None:
                tags.append(f"{_signed(t.change_pct)}% vs. ayer")
            if t.is_min30:
                tags.append("mínimo de 30 días")
            elif t.is_max30:
                tags.append("máximo de 30 días")
            tag_str = f" ({', '.join(tags)})" if tags else ""
            conf = f" · confianza {t.score} %" if t.score is not None else ""
            headline = f"💵 <b>Movimiento en la TRM:</b> {_fmt_money(t.value)} por dólar{tag_str}{conf}."
            found.append(RelevanceAlert(key="trm_move", priority=6, headline=headline))

    # 7. Festivo hoy o mañana
    if rel_cfg.get("holiday_today_or_tomorrow", True):
        if brief.greeting.holiday_today:
            found.append(
                RelevanceAlert(
                    key="holiday_today",
                    priority=7,
                    headline=f"🎉 <b>Hoy es festivo en Colombia:</b> {esc(brief.greeting.holiday_today)}.",
                )
            )
        elif brief.greeting.holiday_tomorrow:
            headline = (
                f"📅 <b>Mañana es festivo:</b> {esc(brief.greeting.holiday_tomorrow)}; "
                "tenlo presente para tus planes."
            )
            found.append(RelevanceAlert(key="holiday_tomorrow", priority=7, headline=headline))

    found.sort(key=lambda x: x.priority)

    useful = _build_useful_line(brief, alerts_cfg)
    conf_summary = _build_confidence_summary(brief)
    return RelevanceResult(
        triggered=bool(found),
        silent=not bool(found),
        alerts=found,
        useful_line=useful,
        confidence_line=conf_summary,
    )


def _build_useful_line(brief: Brief, alerts_cfg: Mapping[str, float]) -> str:
    w = brief.weather
    parts: list[str] = []
    if w:
        tip = clothing_tip(w, alerts_cfg)
        temp_part = ""
        if w.tmin.value is not None and w.tmax.value is not None:
            temp_part = f"{fmt_stat(w.tmin, ' °C')} a {fmt_stat(w.tmax, ' °C')}, "
        elif w.temp.value is not None:
            temp_part = f"ahora {fmt_stat(w.temp, ' °C')}, "
        conf = f" (confianza {w.score} %)" if w.score is not None else ""
        parts.append(f"Clima tranquilo ({temp_part}{tip}){conf}")
    if brief.good_news:
        link = html.escape(brief.good_news.link, quote=True)
        parts.append(f'buena noticia: <a href="{link}">{esc(brief.good_news.title)}</a>')
    elif brief.trm:
        parts.append(f"TRM {_fmt_money(brief.trm.value)}")
    if not parts:
        return f"✨ <i>“{esc(brief.quote.text)}”</i> — {esc(brief.quote.author)}"
    return "💡 Hoy: " + " · ".join(parts) + "."


def _build_confidence_summary(brief: Brief) -> str:
    scores: list[str] = []
    if brief.weather and brief.weather.score is not None:
        scores.append(f"clima {brief.weather.score} %")
    if brief.weather and brief.weather.rain_score is not None:
        scores.append(f"lluvia {brief.weather.rain_score} %")
    if brief.air and brief.air.score is not None:
        scores.append(f"aire {brief.air.score} %")
    if brief.trm and brief.trm.score is not None:
        scores.append(f"TRM {brief.trm.score} %")
    return f"<i>Confianza del dato: {', '.join(scores)}</i>" if scores else ""


def format_smart_daily(brief: Brief, alerts_cfg: Mapping[str, float] | None = None) -> str:
    """Formatea el mensaje diario inteligente:
    - Si ninguna regla se cumple: 2 líneas silenciosas (saludo corto + una cosa útil del día).
    - Si se cumple alguna: 4 a 6 líneas con sonido, empezando por lo más importante.
    """
    rel = brief.relevance
    if rel is None:
        rel = evaluate_relevance(brief, {}, alerts_cfg)
    first_sentence = brief.greeting.text.split(".")[0].strip() + "."
    fecha = date_es(brief.today)

    if not rel.triggered:
        line1 = f"🔕 {esc(first_sentence)} <b>{esc(fecha)}</b> — sin alertas en Bogotá."
        line2 = rel.useful_line
        return f"{line1}\n{line2}"

    lines: list[str] = [rel.alerts[0].headline]
    lines.append(f"☀️ {esc(first_sentence)} <b>{esc(fecha)}</b>")
    for extra_alert in rel.alerts[1:3]:
        lines.append(extra_alert.headline)
    lines.append(rel.useful_line)
    if len(lines) < 4:
        lines.append(f"✨ <i>“{esc(brief.quote.text)}”</i> — {esc(brief.quote.author)}")
    if len(lines) < 6 and rel.confidence_line:
        lines.append(rel.confidence_line)
    elif len(lines) < 4:
        lines.append(f"Cuídate mucho, {esc(brief.name)}.")
    return "\n".join(lines[:6])
