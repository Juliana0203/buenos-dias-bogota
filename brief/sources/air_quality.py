"""Fuentes de calidad del aire (escala US AQI): Open-Meteo Air Quality y WAQI.

La RMCAB (Secretaría de Ambiente de Bogotá) no ofrece una API abierta estable ni un
dataset en datos.gov.co, por lo que no se consulta; WAQI agrega estaciones de la RMCAB
para Bogotá, así que sigue cubriendo esa red de forma indirecta.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from brief.config import Config
from brief.http import get_json
from brief.models import AirReading

log = logging.getLogger(__name__)

OPEN_METEO_AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
WAQI_URL = "https://api.waqi.info/feed/{target}/"


def parse_open_meteo_aq(data: dict[str, Any]) -> AirReading | None:
    """Extrae el US AQI actual de Open-Meteo."""
    cur = data.get("current", {})
    aqi = cur.get("us_aqi")
    if aqi is None:
        return None
    pollutant = None
    if cur.get("pm2_5") is not None and cur.get("pm10") is not None:
        pollutant = "PM2.5" if cur["pm2_5"] >= cur["pm10"] * 0.5 else "PM10"
    return AirReading(source="Open-Meteo AQ", aqi=float(aqi), pollutant=pollutant)


def fetch_open_meteo_aq(cfg: Config) -> AirReading | None:
    data = get_json(
        OPEN_METEO_AQ_URL,
        {
            "latitude": cfg.lat,
            "longitude": cfg.lon,
            "current": "us_aqi,pm2_5,pm10",
            "timezone": str(cfg.tz.key),
        },
    )
    return parse_open_meteo_aq(data)


def parse_waqi(data: dict[str, Any]) -> AirReading | None:
    """Extrae el AQI de la respuesta de WAQI (aqicn.org)."""
    if data.get("status") != "ok":
        return None
    d = data.get("data", {})
    try:
        aqi = float(d["aqi"])
    except (KeyError, TypeError, ValueError):
        return None
    pol = d.get("dominentpol")
    return AirReading(source="WAQI", aqi=aqi, pollutant=pol.upper().replace("PM25", "PM2.5") if pol else None)


def fetch_waqi(cfg: Config) -> AirReading | None:
    """WAQI (requiere WAQI_TOKEN; si falta, se omite)."""
    token = cfg.secrets.waqi_token
    if not token:
        log.info("WAQI_TOKEN no configurado: se omite WAQI")
        return None
    station = cfg.section("air_quality").get("waqi_station")
    target = station or f"geo:{cfg.lat};{cfg.lon}"
    return parse_waqi(get_json(WAQI_URL.format(target=target), {"token": token}))


def build_tasks(cfg: Config) -> dict[str, Callable[[], Any]]:
    return {
        "open-meteo-aq": lambda: fetch_open_meteo_aq(cfg),
        "waqi": lambda: fetch_waqi(cfg),
    }
