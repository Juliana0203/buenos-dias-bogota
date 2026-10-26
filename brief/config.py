"""Carga de config.yaml y secretos desde variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"


@dataclass(frozen=True)
class Secrets:
    """Secretos leídos únicamente del entorno; nunca se escriben en el código."""

    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    weatherapi_key: str | None = None
    openweather_key: str | None = None
    waqi_token: str | None = None

    @classmethod
    def from_env(cls) -> Secrets:
        def env(name: str) -> str | None:
            return os.environ.get(name) or None

        return cls(
            telegram_token=env("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=env("TELEGRAM_CHAT_ID"),
            weatherapi_key=env("WEATHERAPI_KEY"),
            openweather_key=env("OPENWEATHER_API_KEY"),
            waqi_token=env("WAQI_TOKEN"),
        )


@dataclass(frozen=True)
class Config:
    """Configuración completa: YAML + secretos."""

    raw: dict[str, Any]
    secrets: Secrets = field(default_factory=Secrets)

    @property
    def name(self) -> str:
        return self.raw["persona"]["name"]

    @property
    def city(self) -> str:
        return self.raw["location"]["city"]

    @property
    def lat(self) -> float:
        return float(self.raw["location"]["lat"])

    @property
    def lon(self) -> float:
        return float(self.raw["location"]["lon"])

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.raw["location"]["timezone"])

    def section(self, name: str) -> dict[str, Any]:
        """Devuelve una sección del YAML o un diccionario vacío."""
        return self.raw.get(name) or {}


def load_config(path: Path | str | None = None) -> Config:
    """Carga el YAML (por defecto config.yaml en la raíz) y los secretos del entorno."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    with cfg_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Config(raw=raw, secrets=Secrets.from_env())
