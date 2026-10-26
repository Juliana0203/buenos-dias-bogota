"""Modelos de datos compartidos entre módulos."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

CONF_HIGH = "alta"
CONF_MID = "media"
CONF_LOW = "baja"


@dataclass
class Stat:
    """Resultado de combinar varias fuentes para una magnitud."""

    value: float | None = None  # mediana
    low: float | None = None
    high: float | None = None
    spread: float = 0.0  # max - min
    n: int = 0
    confidence: str = CONF_LOW
    disagree: bool = False  # True si la dispersión supera el umbral
    sources: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass
class WeatherReading:
    """Lectura de clima de UNA fuente. Los campos desconocidos quedan en None."""

    source: str
    temp: float | None = None
    feels_like: float | None = None
    tmin: float | None = None
    tmax: float | None = None
    wind_kmh: float | None = None
    uv: float | None = None
    # Probabilidad de lluvia (%) por hora local 0-23.
    hourly_pop: dict[int, float] = field(default_factory=dict)


@dataclass
class WeatherSummary:
    """Clima consolidado."""

    temp: Stat
    feels_like: Stat
    tmin: Stat
    tmax: Stat
    wind_kmh: Stat
    uv: Stat
    rain_periods: dict[str, Stat]
    rain_peak_hour: int | None
    rain_peak_pct: float | None
    sources: list[str]
    confidence: str


@dataclass
class AirReading:
    """Lectura de calidad del aire (escala US AQI) de una fuente."""

    source: str
    aqi: float
    pollutant: str | None = None


@dataclass
class AirSummary:
    aqi: Stat
    level: str
    pollutant: str | None
    sources: list[str]
    confidence: str


@dataclass
class TrmSummary:
    """TRM oficial con comparativos."""

    value: float
    valid_from: str
    previous: float | None
    change_abs: float | None
    change_pct: float | None
    avg30: float | None
    vs_avg30_pct: float | None
    is_min30: bool
    is_max30: bool
    backup_value: float | None
    sources: list[str]
    confidence: str


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published: datetime
    summary: str = ""
    scope: str = "colombia"
    score: float = 0.0


@dataclass
class MobilitySummary:
    pyp_text: str | None
    alerts: list[NewsItem] = field(default_factory=list)


@dataclass
class Quote:
    text: str
    author: str
    origin: str = "local"  # "local" | "api"


@dataclass
class Greeting:
    text: str
    holiday_today: str | None = None
    holiday_tomorrow: str | None = None


@dataclass
class Brief:
    """Todo lo necesario para armar el mensaje; las secciones fallidas son None."""

    name: str
    today: date
    greeting: Greeting
    weather: WeatherSummary | None
    air: AirSummary | None
    trm: TrmSummary | None
    mobility: MobilitySummary
    news: list[NewsItem]
    quote: Quote
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
