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
    score: int | None = None  # % de concordancia entre fuentes (30-98), None si no hay datos

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
    # Precipitación (mm) por hora local 0-23.
    hourly_mm: dict[int, float] = field(default_factory=dict)
    # Sensación térmica (°C) por hora local 0-23.
    hourly_feels: dict[int, float] = field(default_factory=dict)


@dataclass
class FeelsRange:
    """Rango de sensación térmica de una franja (mediana entre fuentes de la mínima y de la máxima)."""

    low: float
    high: float
    n: int = 0


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
    rain_sources: list[str] = field(default_factory=list)  # votaron en probabilidad de lluvia
    rain_confidence: str = CONF_LOW
    rain_note: str | None = None  # p. ej. "lluvia probable, aunque un modelo no la ve"
    feels_periods: dict[str, FeelsRange] = field(default_factory=dict)
    score: int | None = None  # confianza (concordancia) del clima general
    rain_score: int | None = None  # confianza (concordancia) de la lluvia
    now_hour: int | None = None  # hora local de ejecución (0-23)
    period_hours: dict[str, tuple[int, int]] = field(
        default_factory=lambda: {"mañana": (6, 12), "tarde": (12, 18), "noche": (18, 24)}
    )


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
    score: int | None = None


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
    score: int | None = None
    valid_until: str | None = None
    is_weekend_or_holiday: bool = False


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published: datetime
    summary: str = ""
    local: bool = False
    score: float = 0.0


@dataclass
class MobilitySummary:
    pyp_text: str | None


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
    good_news: NewsItem | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
