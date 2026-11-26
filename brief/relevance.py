"""Evaluación de reglas de relevancia para el aviso diario inteligente (Bloque A)."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from brief.formatter import (
    _fmt_money,
    _is_past_period,
    _signed,
    clothing_tip,
    esc,
    fmt_stat,
)
from brief.models import Brief, NewsItem, RelevanceAlert, RelevanceResult
from brief.personalization import DAYS, MONTHS, date_es
from brief.sources.news import normalize

DEFAULT_REAL_MOBILITY_EFFECTS = [
    "cierre",
    "cierres",
    "desvio",
    "desvios",
    "paro",
    "marcha",
    "marchas",
    "bloqueo",
    "bloqueos",
    "suspension",
    "suspende",
    "suspenden",
    "suspendido",
    "accidente",
    "afectacion",
    "afectaciones",
    "retraso",
    "retrasos",
    "trancon",
    "manifestacion",
    "manifestaciones",
    "por obra",
    "por obras",
    "obra vial",
    "obras viales",
    "sin servicio",
    "inhabilitada",
]

_WEEKDAYS_NORM = [normalize(d) for d in DAYS]
_MONTHS_MAP = {normalize(m): idx + 1 for idx, m in enumerate(MONTHS)}
_DATE_RE = re.compile(
    r"\b(\d{1,2})(?:\s*(?:,|y|al|a)\s*(\d{1,2}))?\s+de\s+(" + "|".join(_MONTHS_MAP.keys()) + r")\b"
)


def _has_real_effect_words(text: str, rel_cfg: Mapping[str, Any] | None = None) -> bool:
    effects = list((rel_cfg or {}).get("mobility_real_effect_keywords") or DEFAULT_REAL_MOBILITY_EFFECTS)
    return any(re.search(rf"\b{re.escape(normalize(kw))}", text) for kw in effects)


def is_effect_within_24h(item: NewsItem, today: date) -> bool:
    """True si la noticia describe un efecto para hoy o las próximas 24 h (o el viernes si es del fin de semana)."""
    text = normalize(f"{item.title} {item.summary}")
    tomorrow = today + timedelta(days=1)
    wd_today = today.weekday()

    # 1. Cierres o eventos de "fin de semana": solo suenan en viernes (4), sábado (5) o domingo (6)
    if "fin de semana" in text and wd_today not in (4, 5, 6):
        return False

    # 2. Fechas explícitas en el texto ("28 de noviembre", "28 y 29 de noviembre")
    explicit_dates: list[date] = []
    for m in _DATE_RE.finditer(text):
        d1_str, d2_str, month_name = m.group(1), m.group(2), m.group(3)
        month_num = _MONTHS_MAP.get(month_name)
        if not month_num:
            continue
        for d_raw in (d1_str, d2_str):
            if not d_raw:
                continue
            try:
                explicit_dates.append(date(today.year, month_num, int(d_raw)))
            except ValueError:
                continue
    if explicit_dates:
        allowed_dates = {today, tomorrow}
        if wd_today == 4:  # el viernes también permite avisar cierres del sábado/domingo de ese fin de semana
            allowed_dates.update({today + timedelta(days=2), today + timedelta(days=3)})
        if not any(d in allowed_dates for d in explicit_dates):
            return False

    # 3. Días de la semana explícitos
    mentioned_wds = {idx for idx, w_name in enumerate(_WEEKDAYS_NORM) if re.search(rf"\b{w_name}\b", text)}
    if mentioned_wds:
        allowed_wds = {wd_today, (wd_today + 1) % 7}
        if wd_today == 4:
            allowed_wds.update({5, 6})
        if not (mentioned_wds & allowed_wds):
            return False

    return True


def is_real_mobility_disruption(
    item: NewsItem,
    rel_cfg: Mapping[str, Any] | None = None,
    today: date | None = None,
) -> bool:
    """True si la noticia reporta una afectación real hoy o en las próximas 24 h.

    - Las notas 'en vivo' o 'minuto a minuto' solo cuentan si el titular menciona un efecto actual
      (y no un anuncio de cierres de fin de semana con normalidad actual).
    - Las notas rutinarias de pico y placa no disparan sonido.
    """
    title_norm = normalize(item.title)
    full_norm = normalize(f"{item.title} {item.summary}")

    if not _has_real_effect_words(full_norm, rel_cfg):
        return False

    is_live_blog = "en vivo" in title_norm or "minuto a minuto" in title_norm
    if is_live_blog:
        if not _has_real_effect_words(title_norm, rel_cfg):
            return False
        if "con normalidad" in title_norm and "fin de semana" in title_norm:
            return False

    if today is not None and not is_effect_within_24h(item, today):
        return False

    return True


def evaluate_relevance(
    brief: Brief,
    rel_cfg: Mapping[str, Any],
    alerts_cfg: Mapping[str, float] | None = None,
    last_trm_extreme_date: date | None = None,
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
            headline = f"☔ <b>Hoy sí lleva paraguas:</b> lluvia al {best_val:.0f} % en la {best_period}{peak_info}."
            found.append(RelevanceAlert(key="rain_outing", priority=1, headline=headline))

    # 2. Alerta de movilidad real hoy o en las próximas 24 h (o pico y placa que afecte hoy según mi placa)
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
        real_news = [n for n in brief.news if is_real_mobility_disruption(n, rel_cfg, today=brief.today)]
        if real_news:
            top = real_news[0]
            link = html.escape(top.link, quote=True)
            extra = f" (+{len(real_news) - 1} alerta(s) más)" if len(real_news) > 1 else ""
            headline = (
                f'🚌 <b>Alerta de movilidad:</b> <a href="{link}">{esc(top.title)}</a> ({esc(top.source)}){extra}.'
            )
            found.append(RelevanceAlert(key="mobility_news", priority=2, headline=headline))

    # 3. Calidad del aire dañina (AQI >= umbral, por defecto 101)
    aqi_threshold = float(rel_cfg.get("aqi_harmful", 101.0))
    if a and a.aqi.value is not None and a.aqi.value >= aqi_threshold:
        pol = f" ({esc(a.pollutant)})" if a.pollutant else ""
        headline = f"🍃 <b>Aire dañino hoy:</b> AQI {fmt_stat(a.aqi)}{pol}, {esc(a.level)}. Modera el ejercicio afuera."
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

    # 6. Variación de la TRM >= 1.5 % o mínimo/máximo de 30 días (con enfriamiento de 5 días para extremos)
    trm_threshold = float(rel_cfg.get("trm_change_pct", 1.5))
    cooldown_days = int(rel_cfg.get("trm_extreme_cooldown_days", 5))
    if t:
        big_change = t.change_pct is not None and abs(t.change_pct) >= trm_threshold
        is_extreme = bool(t.is_min30 or t.is_max30)
        recently_alerted = bool(
            last_trm_extreme_date is not None and 0 < (brief.today - last_trm_extreme_date).days <= cooldown_days
        )
        trigger_extreme = is_extreme and not recently_alerted
        if big_change or trigger_extreme:
            tags: list[str] = []
            if t.change_pct is not None:
                tags.append(f"{_signed(t.change_pct)}% vs. ayer")
            if trigger_extreme and t.is_min30:
                tags.append("mínimo de 30 días")
            elif trigger_extreme and t.is_max30:
                tags.append("máximo de 30 días")
            tag_str = f" ({', '.join(tags)})" if tags else ""
            headline = f"💵 <b>Movimiento en la TRM:</b> {_fmt_money(t.value)} por dólar{tag_str}."
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

    triggered_keys = {al.key for al in found}
    useful = _build_useful_line(
        brief,
        alerts_cfg,
        triggered_keys=triggered_keys,
        include_inline_conf=not bool(found),
        rel_cfg=rel_cfg,
    )
    conf_summary = _build_confidence_summary(brief)
    return RelevanceResult(
        triggered=bool(found),
        silent=not bool(found),
        alerts=found,
        useful_line=useful,
        confidence_line=conf_summary,
    )


def _build_useful_line(
    brief: Brief,
    alerts_cfg: Mapping[str, float],
    triggered_keys: set[str] | None = None,
    include_inline_conf: bool = False,
    rel_cfg: Mapping[str, Any] | None = None,
) -> str:
    """Construye la línea 'Hoy:' sin repetir datos que ya tengan su propia alerta (lluvia, TRM).

    Si todas las señales ya se mostraron en las alertas y no hay dato nuevo que aportar, devuelve ''.
    """
    triggered_keys = triggered_keys or set()
    weather_keys = {"rain_outing", "feels_extreme_cold", "feels_extreme_hot", "uv_extreme"}
    weather_triggered = bool(triggered_keys & weather_keys)

    w = brief.weather
    parts: list[str] = []
    if w:
        tip = clothing_tip(w, alerts_cfg)
        temp_part = ""
        if w.tmin.value is not None and w.tmax.value is not None:
            temp_part = f"{fmt_stat(w.tmin, ' °C')} a {fmt_stat(w.tmax, ' °C')}, "
        elif w.temp.value is not None:
            temp_part = f"ahora {fmt_stat(w.temp, ' °C')}, "
        max_rain = max(
            (
                s.value
                for k, s in w.rain_periods.items()
                if s.value is not None and not _is_past_period(k, w.now_hour, w.period_hours)
            ),
            default=0.0,
        )
        umbrella_pct = float(alerts_cfg.get("rain_umbrella_pct", 40.0))
        if weather_triggered or max_rain >= umbrella_pct:
            # No repetir porcentaje de lluvia si ya se mostró en la alerta principal rain_outing
            rain_tag = (
                f", lluvia moderada ({max_rain:.0f} %)"
                if (max_rain >= umbrella_pct and "rain_outing" not in triggered_keys)
                else ""
            )
            if temp_part or tip:
                parts.append(f"Clima: {temp_part}{tip}{rain_tag}")
        else:
            parts.append(f"Clima tranquilo ({temp_part}{tip})")

    if brief.good_news:
        link = html.escape(brief.good_news.link, quote=True)
        parts.append(f'buena noticia: <a href="{link}">{esc(brief.good_news.title)}</a>')
    elif brief.trm and "trm_move" not in triggered_keys:
        parts.append(f"TRM {_fmt_money(brief.trm.value)}")

    # En mensaje silencioso incluye avisos que no dispararon sonido (p. ej. cierres de fin de semana o pico y placa)
    if include_inline_conf and brief.news:
        non_urgent = [
            n for n in brief.news if not is_real_mobility_disruption(n, rel_cfg, today=brief.today)
        ]
        if non_urgent:
            top = non_urgent[0]
            link = html.escape(top.link, quote=True)
            parts.append(f'<a href="{link}">{esc(top.title)}</a>')

    if include_inline_conf:
        conf_bits: list[str] = []
        if w and w.score is not None:
            conf_bits.append(f"clima {w.score} %")
        if brief.trm and brief.trm.score is not None:
            conf_bits.append(f"TRM {brief.trm.score} %")
        if conf_bits:
            parts.append(f"confianza: {', '.join(conf_bits)}")

    if not parts:
        return f"✨ <i>“{esc(brief.quote.text)}”</i> — {esc(brief.quote.author)}" if include_inline_conf else ""
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
    - Si se cumple alguna: 4 a 6 líneas con sonido, empezando por lo más importante, sin repetir
      datos en 'Hoy:' y mostrando la confianza una sola vez en la línea de cierre.
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
    if rel.useful_line:
        lines.append(rel.useful_line)
    if len(lines) < 4:
        lines.append(f"✨ <i>“{esc(brief.quote.text)}”</i> — {esc(brief.quote.author)}")
    if rel.confidence_line:
        lines = lines[:5]
        lines.append(rel.confidence_line)
    if len(lines) < 4:
        lines.append(f"✨ <i>“{esc(brief.quote.text)}”</i> — {esc(brief.quote.author)}")
    return "\n".join(lines[:6])
