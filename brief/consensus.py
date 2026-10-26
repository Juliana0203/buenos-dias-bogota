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


def combine(values: Mapping[str, float | None], threshold: float) -> Stat:
    """Combina valores por fuente en una estadística con mediana, rango y confianza."""
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


def consensus_weather(
    readings: list[WeatherReading],
    thresholds: Mapping[str, float],
    periods: Mapping[str, tuple[int, int]],
) -> WeatherSummary | None:
    """Consolida lecturas de clima de varias fuentes. Devuelve None si no hay ninguna."""
    if not readings:
        return None
    t_temp = float(thresholds.get("temp_c", 3.0))
    t_rain = float(thresholds.get("rain_pct", 25.0))

    def by(attr: str) -> dict[str, float | None]:
        return {r.source: getattr(r, attr) for r in readings}

    temp = combine(by("temp"), t_temp)
    feels = combine(by("feels_like"), t_temp)
    tmin = combine(by("tmin"), t_temp)
    tmax = combine(by("tmax"), t_temp)
    wind = combine(by("wind_kmh"), float(thresholds.get("wind_kmh", 15.0)))
    uv = combine(by("uv"), float(thresholds.get("uv", 3.0)))

    rain: dict[str, Stat] = {}
    for label, (start, end) in periods.items():
        rain[label] = combine({r.source: _period_max(r.hourly_pop, start, end) for r in readings}, t_rain)

    hours: dict[int, list[float]] = {}
    for r in readings:
        for h, p in r.hourly_pop.items():
            hours.setdefault(h, []).append(p)
    peak_hour = peak_pct = None
    if hours:
        medians = {h: statistics.median(v) for h, v in hours.items()}
        peak_hour = max(medians, key=lambda h: (medians[h], -h))
        peak_pct = medians[peak_hour]

    main = [s for s in (temp, tmin, tmax) if s.available]
    confidence = lowest_confidence(s.confidence for s in main)
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
    )


def consensus_air(readings: list[AirReading], thresholds: Mapping[str, float]) -> AirSummary | None:
    """Consolida lecturas de AQI. Devuelve None si no hay ninguna."""
    if not readings:
        return None
    stat = combine({r.source: r.aqi for r in readings}, float(thresholds.get("aqi", 30.0)))
    pollutants = [r.pollutant for r in readings if r.pollutant]
    return AirSummary(
        aqi=stat,
        level=aqi_level(stat.value or 0),
        pollutant=pollutants[0] if pollutants else None,
        sources=stat.sources,
        confidence=stat.confidence,
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


def gather_weather(cfg: Config) -> WeatherSummary | None:
    """Consulta todas las fuentes de clima en paralelo y las consolida."""
    from brief.sources import weather

    tasks = weather.build_tasks(cfg)
    readings: list[WeatherReading] = _flatten(run_parallel(tasks))
    return consensus_weather(readings, cfg.section("thresholds"), parse_periods(cfg))


def gather_air(cfg: Config) -> AirSummary | None:
    """Consulta todas las fuentes de calidad del aire en paralelo y las consolida."""
    from brief.sources import air_quality

    tasks = air_quality.build_tasks(cfg)
    readings: list[AirReading] = _flatten(run_parallel(tasks))
    return consensus_air(readings, cfg.section("thresholds"))
