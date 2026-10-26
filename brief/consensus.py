"""Verificación cruzada: consulta fuentes en paralelo y combina con la mediana.

Reglas:
* Se usa la MEDIANA de las fuentes disponibles (robusta ante un valor atípico).
* La dispersión es max - min. Si supera el umbral configurado, se marca
  ``disagree`` y el formateador muestra un rango en vez de un solo valor.
* Confianza: alta (>=3 fuentes y coinciden), media (2 fuentes que coinciden, o
  >=3 con desacuerdo), baja (1 fuente, o 2 en desacuerdo).
* Una fuente que falla se registra y se ignora; nunca se inventan datos.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from brief.config import Config
from brief.models import (
    CONF_HIGH,
    CONF_LOW,
    CONF_MID,
    AirReading,
    AirSummary,
    FeelsRange,
    Stat,
    WeatherReading,
    WeatherSummary,
)

log = logging.getLogger(__name__)

_CONF_ORDER = {CONF_LOW: 0, CONF_MID: 1, CONF_HIGH: 2}


def run_parallel(tasks: Mapping[str, Callable[[], Any]], max_workers: int = 8) -> dict[str, Any]:
    """Ejecuta tareas en hilos. Las que lanzan excepción devuelven None (y se loguean)."""
    results: dict[str, Any] = {}
    if not tasks:
        return results
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {name: pool.submit(fn) for name, fn in tasks.items()}
        for name, fut in futures.items():
            try:
                results[name] = fut.result()
            except Exception as exc:  # noqa: BLE001 - una fuente caída no debe tumbar el resto
                log.warning("Fuente '%s' falló: %s", name, exc)
                results[name] = None
    return results


DEFAULT_CONFIDENCE: dict[str, float] = {
    "spread_penalty_per_threshold": 30.0,
    "spread_penalty_max": 50.0,
    "missing_penalty": 8.0,
    "single_source_cap": 60.0,
    "floor": 30.0,
    "ceiling": 98.0,
}


def agreement_score(
    spread: float,
    threshold: float,
    n: int,
    expected: int | None = None,
    params: Mapping[str, float] | None = None,
) -> int | None:
    """Puntaje (%) de concordancia entre fuentes; None si no votó ninguna.

    100 - min(tope, k * dispersión / umbral) - penalización * (esperadas - votaron).
    Con una sola fuente el máximo es ``single_source_cap``; el resultado queda en [floor, ceiling].
    Mide qué tanto coinciden las fuentes, NO la probabilidad estadística de acierto.
    """
    if n <= 0:
        return None
    p = {**DEFAULT_CONFIDENCE, **(params or {})}
    penalty = (
        min(p["spread_penalty_max"], p["spread_penalty_per_threshold"] * spread / threshold) if threshold > 0 else 0
    )
    score = 100.0 - penalty
    if expected is not None and expected > n:
        score -= p["missing_penalty"] * (expected - n)
    if n == 1:
        score = min(score, p["single_source_cap"])
    return round(max(p["floor"], min(p["ceiling"], score)))


def clamp_score(score: float, params: Mapping[str, float] | None = None) -> int:
    """Limita un puntaje al rango [floor, ceiling] configurado."""
    p = {**DEFAULT_CONFIDENCE, **(params or {})}
    return round(max(p["floor"], min(p["ceiling"], score)))


def combine(
    values: Mapping[str, float | None],
    threshold: float,
    expected: int | None = None,
    params: Mapping[str, float] | None = None,
) -> Stat:
    """Combina valores por fuente en una estadística con mediana, rango, confianza y puntaje.

    ``expected`` es cuántas fuentes se esperaban votar (las que fallaron o se descartaron restan puntaje).
    """
    valid = {k: float(v) for k, v in values.items() if v is not None}
    if not valid:
        return Stat()
    nums = list(valid.values())
    spread = max(nums) - min(nums)
    n = len(nums)
    disagree = n > 1 and spread > threshold
    if n == 1:
        conf = CONF_LOW
    elif disagree:
        conf = CONF_MID if n >= 3 else CONF_LOW
    else:
        conf = CONF_HIGH if n >= 3 else CONF_MID
    return Stat(
        value=statistics.median(nums),
        low=min(nums),
        high=max(nums),
        spread=spread,
        n=n,
        confidence=conf,
        disagree=disagree,
        sources=sorted(valid),
        score=agreement_score(spread, threshold, n, expected, params),
    )


def lowest_confidence(levels: Iterable[str]) -> str:
    """Devuelve el nivel de confianza más bajo de una colección."""
    levels = list(levels)
    if not levels:
        return CONF_LOW
    return min(levels, key=lambda lv: _CONF_ORDER[lv])


def aqi_level(aqi: float) -> str:
    """Nivel en español según la escala US AQI."""
    if aqi <= 50:
        return "buena"
    if aqi <= 100:
        return "moderada"
    if aqi <= 150:
        return "dañina para grupos sensibles"
    if aqi <= 200:
        return "dañina"
    if aqi <= 300:
        return "muy dañina"
    return "peligrosa"


def _period_max(pop: Mapping[int, float], start: int, end: int) -> float | None:
    vals = [p for h, p in pop.items() if start <= h < end]
    return max(vals) if vals else None


def _lower(conf: str) -> str:
    """Baja un nivel de confianza."""
    return {CONF_HIGH: CONF_MID, CONF_MID: CONF_LOW, CONF_LOW: CONF_LOW}[conf]


def filter_suspect_pop(readings: list[WeatherReading], thresholds: Mapping[str, float]) -> list[WeatherReading]:
    """Excluye de la probabilidad de lluvia a las fuentes sospechosas.

    Sospechosa: máximo del día < ``suspect_max_pct`` mientras la mediana de los máximos de las
    demás fuentes supera ``suspect_others_pct``. Devuelve las lecturas que sí votan.
    """
    low = float(thresholds.get("suspect_max_pct", 5.0))
    high = float(thresholds.get("suspect_others_pct", 50.0))
    day_max = {r.source: max(r.hourly_pop.values()) for r in readings if r.hourly_pop}
    voters: list[WeatherReading] = []
    for r in readings:
        if r.source in day_max and len(day_max) > 1:
            others = [v for s, v in day_max.items() if s != r.source]
            if day_max[r.source] < low and statistics.median(others) > high:
                log.warning(
                    "Fuente sospechosa en probabilidad de lluvia: %s (máx. %.0f%%) vs mediana de las demás %.0f%%; "
                    "se excluye de esa variable",
                    r.source,
                    day_max[r.source],
                    statistics.median(others),
                )
                continue
        voters.append(r)
    return voters


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many.format(n=n)


def cross_check_rain(
    pop_periods: Mapping[str, Stat],
    mm_readings: list[WeatherReading],
    thresholds: Mapping[str, float],
    base_conf: str,
) -> tuple[str, str | None]:
    """Contrasta la probabilidad con la precipitación (mm) de los modelos.

    Devuelve (confianza ajustada, texto o None). Un modelo "ve" lluvia si su total diario
    supera ``rain_mm_day``. Si la mayoría coincide con el veredicto de la probabilidad y hay
    >=3 modelos, la confirmación independiente sube la confianza a alta (si no hay desacuerdo
    en la probabilidad, es decir, ninguna franja supera el umbral ``rain_pct``); si discrepan la
    mitad o más, baja un nivel; si discrepan todos, baja a "baja".
    """
    medians = [s.value for s in pop_periods.values() if s.value is not None]
    if not medians or not mm_readings:
        return base_conf, None
    likely = max(medians) >= float(thresholds.get("rain_likely_pct", 50.0))
    min_mm = float(thresholds.get("rain_mm_day", 1.0))
    sees = [sum(r.hourly_mm.values()) >= min_mm for r in mm_readings]
    n = len(sees)
    n_see = sum(sees)
    disagreeing = (n - n_see) if likely else n_see
    # Las fuentes de probabilidad "coinciden" si NINGUNA franja supera el umbral rain_pct
    # (Stat.disagree = dispersión > umbral). Solo entonces los mm pueden subir la confianza.
    pop_sources_agree = not any(s.disagree for s in pop_periods.values())

    note: str | None = None
    if disagreeing:
        if likely and n_see == 0:
            note = "alta probabilidad de lluvia, pero ningún modelo de precipitación la confirma"
        elif likely:
            note = "lluvia probable, aunque " + _plural(disagreeing, "un modelo no la ve", "{n} modelos no la ven")
        else:
            note = "lluvia poco probable, aunque " + _plural(disagreeing, "un modelo sí la ve", "{n} modelos sí la ven")

    conf = base_conf
    if disagreeing == n:
        conf = CONF_LOW
    elif disagreeing * 2 >= n:
        conf = _lower(conf)
    elif n >= 3 and conf == CONF_MID and pop_sources_agree:
        conf = CONF_HIGH
    return conf, note


def feels_by_period(readings: list[WeatherReading], periods: Mapping[str, tuple[int, int]]) -> dict[str, FeelsRange]:
    """Sensación térmica por franja: mediana entre fuentes de la mínima y de la máxima de cada una."""
    out: dict[str, FeelsRange] = {}
    for label, (start, end) in periods.items():
        lows: list[float] = []
        highs: list[float] = []
        for r in readings:
            vals = [v for h, v in r.hourly_feels.items() if start <= h < end]
            if vals:
                lows.append(min(vals))
                highs.append(max(vals))
        if lows:
            out[label] = FeelsRange(low=statistics.median(lows), high=statistics.median(highs), n=len(lows))
    return out


def mm_disagree_fraction(
    pop_periods: Mapping[str, Stat], mm_readings: list[WeatherReading], thresholds: Mapping[str, float]
) -> float:
    """Fracción de modelos de precipitación (mm) que contradicen el veredicto de la probabilidad."""
    medians = [s.value for s in pop_periods.values() if s.value is not None]
    if not medians or not mm_readings:
        return 0.0
    likely = max(medians) >= float(thresholds.get("rain_likely_pct", 50.0))
    min_mm = float(thresholds.get("rain_mm_day", 1.0))
    sees = [sum(r.hourly_mm.values()) >= min_mm for r in mm_readings]
    return sum((not s) if likely else s for s in sees) / len(sees)


def consensus_weather(
    readings: list[WeatherReading],
    thresholds: Mapping[str, float],
    periods: Mapping[str, tuple[int, int]],
    expected: Mapping[str, int] | None = None,
    conf_params: Mapping[str, float] | None = None,
    now_hour: int | None = None,
) -> WeatherSummary | None:
    """Consolida lecturas de clima de varias fuentes. Devuelve None si no hay ninguna.

    ``expected`` ({"temp": N, "rain": M}) indica cuántas fuentes se esperaban en temperatura y lluvia.
    """
    if not readings:
        return None
    expected = expected or {}
    t_temp = float(thresholds.get("temp_c", 3.0))
    t_rain = float(thresholds.get("rain_pct", 25.0))

    def by(attr: str) -> dict[str, float | None]:
        return {r.source: getattr(r, attr) for r in readings}

    temp = combine(by("temp"), t_temp, expected.get("temp"), conf_params)
    feels = combine(by("feels_like"), t_temp, None, conf_params)
    tmin = combine(by("tmin"), t_temp, None, conf_params)
    tmax = combine(by("tmax"), t_temp, None, conf_params)
    wind = combine(by("wind_kmh"), float(thresholds.get("wind_kmh", 15.0)))
    uv = combine(by("uv"), float(thresholds.get("uv", 3.0)))

    pop_voters = filter_suspect_pop(readings, thresholds)
    rain: dict[str, Stat] = {}
    for label, (start, end) in periods.items():
        rain[label] = combine(
            {r.source: _period_max(r.hourly_pop, start, end) for r in pop_voters},
            t_rain,
            expected.get("rain"),
            conf_params,
        )

    hours: dict[int, list[float]] = {}
    for r in pop_voters:
        for h, p in r.hourly_pop.items():
            hours.setdefault(h, []).append(p)
    peak_hour = peak_pct = None
    if hours:
        future_hours = (
            {h: v for h, v in hours.items() if h >= now_hour}
            if now_hour is not None and not (6 <= now_hour <= 10)
            else hours
        )
        medians = {h: statistics.median(v) for h, v in (future_hours or hours).items()}
        peak_hour = max(medians, key=lambda h: (medians[h], -h))
        peak_pct = medians[peak_hour]

    rain_stats = [s for s in rain.values() if s.available]
    rain_sources = sorted({src for s in rain_stats for src in s.sources})
    rain_conf = lowest_confidence(s.confidence for s in rain_stats) if rain_stats else CONF_LOW
    rain_note = None
    rain_score: int | None = None
    if rain_stats:
        mm_readings = [r for r in readings if r.hourly_mm]
        rain_conf, rain_note = cross_check_rain(rain, mm_readings, thresholds, rain_conf)
        period_scores = [s.score for s in rain_stats if s.score is not None]
        base = sum(period_scores) / len(period_scores)
        mm_penalty = 30.0 * mm_disagree_fraction(rain, mm_readings, thresholds)
        rain_score = clamp_score(base - mm_penalty, conf_params)

    main = [s for s in (temp, tmin, tmax) if s.available]
    main_scores = [s.score for s in main if s.score is not None]
    weather_score = min(main_scores) if main_scores else None
    confidence = lowest_confidence([*(s.confidence for s in main), *([rain_conf] if rain_stats else [])])
    sources = sorted({r.source for r in readings})
    return WeatherSummary(
        temp=temp,
        feels_like=feels,
        tmin=tmin,
        tmax=tmax,
        wind_kmh=wind,
        uv=uv,
        rain_periods=rain,
        rain_peak_hour=peak_hour,
        rain_peak_pct=peak_pct,
        sources=sources,
        confidence=confidence,
        rain_sources=rain_sources,
        rain_confidence=rain_conf,
        rain_note=rain_note,
        feels_periods=feels_by_period(readings, periods),
        score=weather_score,
        rain_score=rain_score,
        now_hour=now_hour,
        period_hours=dict(periods),
    )


def consensus_air(
    readings: list[AirReading],
    thresholds: Mapping[str, float],
    expected: int | None = None,
    conf_params: Mapping[str, float] | None = None,
) -> AirSummary | None:
    """Consolida lecturas de AQI. Devuelve None si no hay ninguna."""
    if not readings:
        return None
    stat = combine({r.source: r.aqi for r in readings}, float(thresholds.get("aqi", 30.0)), expected, conf_params)
    pollutants = [r.pollutant for r in readings if r.pollutant]
    return AirSummary(
        aqi=stat,
        level=aqi_level(stat.value or 0),
        pollutant=pollutants[0] if pollutants else None,
        sources=stat.sources,
        confidence=stat.confidence,
        score=stat.score,
    )


def _flatten(results: Mapping[str, Any]) -> list[Any]:
    out: list[Any] = []
    for res in results.values():
        if res is None:
            continue
        out.extend(res if isinstance(res, list) else [res])
    return out


def parse_periods(cfg: Config) -> dict[str, tuple[int, int]]:
    """Franjas horarias del día desde la configuración."""
    raw = cfg.section("weather").get("periods", {})
    return {k: (int(v[0]), int(v[1])) for k, v in raw.items()}


def expected_weather_sources(cfg: Config) -> dict[str, int]:
    """Cuántas fuentes se esperan en temperatura y en lluvia según config.yaml y las claves disponibles."""
    wcfg = cfg.section("weather")
    models = wcfg.get("models", {})
    optional = int(bool(cfg.secrets.weatherapi_key)) + int(bool(cfg.secrets.openweather_key))
    ideam = int(bool((wcfg.get("ideam") or {}).get("enabled")))
    return {
        "temp": len(models.get("temperature", [])) + optional + ideam,
        "rain": len(models.get("precip_probability", [])) + optional,
    }


def expected_air_sources(cfg: Config) -> int:
    """Open-Meteo Air Quality siempre, más WAQI si hay token."""
    return 1 + int(bool(cfg.secrets.waqi_token))


def gather_weather(cfg: Config, now_hour: int | None = None) -> WeatherSummary | None:
    """Consulta todas las fuentes de clima en paralelo y las consolida."""
    from datetime import datetime

    from brief.sources import weather

    tasks = weather.build_tasks(cfg)
    readings: list[WeatherReading] = _flatten(run_parallel(tasks))
    hour = datetime.now(cfg.tz).hour if now_hour is None else now_hour
    return consensus_weather(
        readings,
        cfg.section("thresholds"),
        parse_periods(cfg),
        expected_weather_sources(cfg),
        cfg.section("confidence"),
        now_hour=hour,
    )


def gather_air(cfg: Config) -> AirSummary | None:
    """Consulta todas las fuentes de calidad del aire en paralelo y las consolida."""
    from brief.sources import air_quality

    tasks = air_quality.build_tasks(cfg)
    readings: list[AirReading] = _flatten(run_parallel(tasks))
    return consensus_air(readings, cfg.section("thresholds"), expected_air_sources(cfg), cfg.section("confidence"))
