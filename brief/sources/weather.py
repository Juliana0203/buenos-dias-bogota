"""Fuentes de clima: Open-Meteo (multimodelo), WeatherAPI, OpenWeatherMap e IDEAM."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from brief.config import Config
from brief.http import get_json
from brief.models import WeatherReading

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WEATHERAPI_URL = "https://api.weatherapi.com/v1/forecast.json"
OWM_WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
OWM_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
SODA_URL = "https://www.datos.gov.co/resource/{dataset}.json"

_MODEL_LABEL = {
    "ecmwf_ifs025": "ECMWF",
    "gfs_seamless": "GFS",
    "icon_seamless": "ICON",
}


def _num(value: Any) -> float | None:
    """Convierte a float; None si no es un número válido."""
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _series(block: dict[str, Any], var: str, model: str, single: bool) -> list[Any]:
    """Obtiene la serie de una variable para un modelo (con o sin sufijo)."""
    if f"{var}_{model}" in block:
        return block[f"{var}_{model}"]
    return block.get(var, []) if single else []


def _hourly_map(times: list[Any], series: list[Any]) -> dict[int, float]:
    """Serie horaria -> {hora local: valor}, omitiendo valores nulos."""
    out: dict[int, float] = {}
    for i, ts in enumerate(times):
        v = _num(series[i]) if i < len(series) else None
        if v is not None:
            out[int(ts[11:13])] = v
    return out


def parse_open_meteo(data: dict[str, Any], models: dict[str, list[str]], now_hour: int) -> list[WeatherReading]:
    """Convierte la respuesta de Open-Meteo en una lectura por modelo.

    ``models`` indica qué modelos votan en cada variable (temperature, precip_probability,
    precip_amount); un modelo solo aporta las variables en las que está listado.
    """
    hourly = data.get("hourly", {})
    current = data.get("current", {})
    times = hourly.get("time", [])
    all_models = list(dict.fromkeys(m for group in models.values() for m in group))
    single = len(all_models) == 1
    readings: list[WeatherReading] = []
    for model in all_models:
        in_temp = model in models.get("temperature", [])
        in_pop = model in models.get("precip_probability", [])
        in_mm = model in models.get("precip_amount", [])

        def at_hour(series: list[Any]) -> float | None:
            return _num(series[now_hour]) if now_hour < len(series) else None

        def cur(var: str, model: str = model) -> float | None:
            return _num(current.get(f"{var}_{model}", current.get(var) if single else None))

        reading = WeatherReading(source=f"Open-Meteo/{_MODEL_LABEL.get(model, model)}")
        if in_temp:
            temps = _series(hourly, "temperature_2m", model, single)
            feels = _series(hourly, "apparent_temperature", model, single)
            winds = _series(hourly, "wind_speed_10m", model, single)
            uvs = _series(hourly, "uv_index", model, single)
            valid_t = [t for t in (_num(x) for x in temps) if t is not None]
            valid_uv = [u for u in (_num(x) for x in uvs) if u is not None]
            reading.temp = cur("temperature_2m") if cur("temperature_2m") is not None else at_hour(temps)
            reading.feels_like = (
                cur("apparent_temperature") if cur("apparent_temperature") is not None else at_hour(feels)
            )
            reading.tmin = min(valid_t) if valid_t else None
            reading.tmax = max(valid_t) if valid_t else None
            reading.wind_kmh = cur("wind_speed_10m") if cur("wind_speed_10m") is not None else at_hour(winds)
            reading.uv = max(valid_uv) if valid_uv else None
            reading.hourly_feels = _hourly_map(times, feels)
        if in_pop:
            reading.hourly_pop = _hourly_map(times, _series(hourly, "precipitation_probability", model, single))
        if in_mm:
            reading.hourly_mm = _hourly_map(times, _series(hourly, "precipitation", model, single))
        if reading.temp is None and reading.tmax is None and not reading.hourly_pop and not reading.hourly_mm:
            log.warning("Open-Meteo: el modelo %s no devolvió datos útiles", model)
            continue
        readings.append(reading)
    return readings


def fetch_open_meteo(cfg: Config) -> list[WeatherReading]:
    """Consulta Open-Meteo con varios modelos en una sola petición."""
    models = {k: list(v) for k, v in cfg.section("weather").get("models", {}).items()}
    all_models = list(dict.fromkeys(m for group in models.values() for m in group))
    if not all_models:
        return []
    params = {
        "latitude": cfg.lat,
        "longitude": cfg.lon,
        "hourly": "temperature_2m,apparent_temperature,precipitation_probability,precipitation,wind_speed_10m,uv_index",
        "current": "temperature_2m,apparent_temperature,wind_speed_10m",
        "models": ",".join(all_models),
        "timezone": str(cfg.tz.key),
        "forecast_days": 1,
        "wind_speed_unit": "kmh",
    }
    data = get_json(OPEN_METEO_URL, params)
    return parse_open_meteo(data, models, datetime.now(cfg.tz).hour)


def parse_weatherapi(data: dict[str, Any]) -> WeatherReading:
    """Convierte la respuesta de WeatherAPI.com."""
    cur = data["current"]
    day_block = data["forecast"]["forecastday"][0]
    day = day_block["day"]
    pops: dict[int, float] = {}
    feels: dict[int, float] = {}
    for h in day_block.get("hour", []):
        p = _num(h.get("chance_of_rain"))
        if p is not None:
            pops[int(h["time"][11:13])] = p
        f = _num(h.get("feelslike_c"))
        if f is not None:
            feels[int(h["time"][11:13])] = f
    return WeatherReading(
        source="WeatherAPI",
        temp=_num(cur.get("temp_c")),
        feels_like=_num(cur.get("feelslike_c")),
        tmin=_num(day.get("mintemp_c")),
        tmax=_num(day.get("maxtemp_c")),
        wind_kmh=_num(cur.get("wind_kph")),
        uv=_num(day.get("uv")),
        hourly_pop=pops,
        hourly_feels=feels,
    )


def fetch_weatherapi(cfg: Config) -> WeatherReading | None:
    """WeatherAPI.com (requiere WEATHERAPI_KEY; si falta, se omite)."""
    key = cfg.secrets.weatherapi_key
    if not key:
        log.info("WEATHERAPI_KEY no configurada: se omite WeatherAPI")
        return None
    data = get_json(
        WEATHERAPI_URL,
        {"key": key, "q": f"{cfg.lat},{cfg.lon}", "days": 1, "aqi": "no", "alerts": "no"},
    )
    return parse_weatherapi(data)


def parse_openweather(current: dict[str, Any], forecast: dict[str, Any], tz: ZoneInfo, today: Any) -> WeatherReading:
    """Convierte OpenWeatherMap (clima actual + pronóstico cada 3 h).

    El pronóstico solo cubre horas futuras, por eso no se reporta mínima del día.
    """
    main = current.get("main", {})
    temp = _num(main.get("temp"))
    temps = [temp] if temp is not None else []
    pops: dict[int, float] = {}
    for item in forecast.get("list", []):
        local = datetime.fromtimestamp(item["dt"], tz=UTC).astimezone(tz)
        if local.date() != today:
            continue
        t = _num(item.get("main", {}).get("temp"))
        if t is not None:
            temps.append(t)
        pop = _num(item.get("pop"))
        if pop is not None:
            for h in range(local.hour, min(local.hour + 3, 24)):
                pops[h] = pop * 100
    wind = _num(current.get("wind", {}).get("speed"))
    return WeatherReading(
        source="OpenWeatherMap",
        temp=temp,
        feels_like=_num(main.get("feels_like")),
        tmax=max(temps) if temps else None,
        wind_kmh=wind * 3.6 if wind is not None else None,
        hourly_pop=pops,
    )


def fetch_openweather(cfg: Config) -> WeatherReading | None:
    """OpenWeatherMap (requiere OPENWEATHER_API_KEY; si falta, se omite)."""
    key = cfg.secrets.openweather_key
    if not key:
        log.info("OPENWEATHER_API_KEY no configurada: se omite OpenWeatherMap")
        return None
    params = {"lat": cfg.lat, "lon": cfg.lon, "appid": key, "units": "metric"}
    current = get_json(OWM_WEATHER_URL, params)
    forecast = get_json(OWM_FORECAST_URL, params)
    return parse_openweather(current, forecast, cfg.tz, datetime.now(cfg.tz).date())


def parse_ideam(rows: list[dict[str, Any]], now_utc: datetime, max_age_hours: float) -> WeatherReading | None:
    """Observación reciente del IDEAM (datos.gov.co). Se descarta si está desactualizada.

    El IDEAM reporta fechas en UTC; los datos abiertos suelen llegar con retraso.
    """
    if not rows:
        return None
    row = rows[0]
    obs = datetime.fromisoformat(row["fechaobservacion"]).replace(tzinfo=UTC)
    age_h = (now_utc - obs).total_seconds() / 3600
    if age_h > max_age_hours:
        log.info("IDEAM: observación de hace %.1f h (> %s h), se descarta", age_h, max_age_hours)
        return None
    value = _num(row.get("valorobservado"))
    if value is None:
        return None
    return WeatherReading(source="IDEAM", temp=value)


def fetch_ideam(cfg: Config) -> WeatherReading | None:
    """Última temperatura observada en la estación IDEAM configurada."""
    ideam = cfg.section("weather").get("ideam", {})
    if not ideam.get("enabled"):
        return None
    rows = get_json(
        SODA_URL.format(dataset=ideam["dataset"]),
        {
            "$where": f"codigoestacion='{ideam['station_code']}'",
            "$order": "fechaobservacion DESC",
            "$limit": 1,
        },
    )
    return parse_ideam(rows, datetime.now(UTC), float(ideam.get("max_age_hours", 3)))


def build_tasks(cfg: Config) -> dict[str, Callable[[], Any]]:
    """Tareas a ejecutar en paralelo (cada una devuelve lectura(s) o None)."""
    return {
        "open-meteo": lambda: fetch_open_meteo(cfg),
        "weatherapi": lambda: fetch_weatherapi(cfg),
        "openweather": lambda: fetch_openweather(cfg),
        "ideam": lambda: fetch_ideam(cfg),
    }
