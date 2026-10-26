from datetime import UTC, date, datetime

from brief.consensus import agreement_score, combine, consensus_air, consensus_weather
from brief.formatter import build_sections
from brief.models import AirReading, Brief, MobilitySummary, NewsItem, Quote, WeatherReading
from brief.personalization import build_greeting
from brief.sources.trm import compute_trm

TH = {"temp_c": 3.0, "rain_pct": 25.0, "aqi": 30.0, "trm_pct": 1.5}
PERIODS = {"mañana": (6, 12), "tarde": (12, 18), "noche": (18, 24)}
BANNED = [
    "ecmwf",
    "gfs",
    "icon",
    "open-meteo",
    "openmeteo",
    "weatherapi",
    "openweather",
    "waqi",
    "ideam",
    "superfinanciera",
    "er-api",
    "verificado con",
    "fuente:",
    "fuentes",
    "un modelo",
    "zenquotes",
]


def test_perfect_agreement_is_capped_at_98():
    assert agreement_score(0, 3, 3, 3) == 98


def test_score_always_between_30_and_98():
    for spread in (0, 1, 3, 10, 1000):
        for n in (1, 2, 3, 5):
            for expected in (None, 1, 3, 10):
                s = agreement_score(spread, 3, n, expected)
                assert 30 <= s <= 98


def test_single_source_max_60():
    assert agreement_score(0, 3, 1) == 60
    assert combine({"a": 15}, 3).score == 60


def test_dispersion_and_missing_sources_lower_score():
    full = agreement_score(0, 3, 3, 3)
    assert agreement_score(3, 3, 3, 3) < full
    assert agreement_score(0, 3, 2, 3) == 92  # 100 - 8, ya bajo el tope de 98
    assert agreement_score(1.5, 3, 3, 3) > agreement_score(3, 3, 3, 3)


def test_no_votes_gives_none():
    assert agreement_score(0, 3, 0) is None
    assert combine({"a": None}, 3).score is None


def test_formula_values():
    # 100 - 30*(3/3) = 70 ; con 1 fuente faltante: 62
    assert agreement_score(3, 3, 3, 3) == 70
    assert agreement_score(3, 3, 3, 4) == 62


def test_air_and_trm_scores():
    air = consensus_air([AirReading("A", 40), AirReading("B", 42)], TH, expected=2)
    assert air is not None and 30 <= air.score <= 98
    one = consensus_air([AirReading("A", 40)], TH, expected=2)
    assert one is not None and one.score <= 60
    rows = [(date(2026, 10, 25), 3800.0), (date(2026, 10, 24), 3790.0)]
    both = compute_trm(rows, date(2026, 10, 25), 3801.0, TH)
    alone = compute_trm(rows, date(2026, 10, 25), None, TH)
    assert both is not None and alone is not None
    assert both.score > alone.score and alone.score <= 60


def test_failed_source_reduces_weather_score():
    readings = [WeatherReading(s, temp=15, tmin=11, tmax=20) for s in ("a", "b")]
    full = consensus_weather(readings, TH, PERIODS, {"temp": 2, "rain": 0})
    missing = consensus_weather(readings, TH, PERIODS, {"temp": 4, "rain": 0})
    assert (full.score, missing.score) == (98, 84)  # 100 - 16 por dos fuentes que no votaron


def test_rain_confidence_uses_its_own_sources_not_weather_score():
    from brief.formatter import rain_section, weather_section

    readings = [
        WeatherReading("T1", temp=10, tmin=8, tmax=16, hourly_pop={8: 80, 14: 80, 20: 80}),
        WeatherReading("T2", temp=18, tmin=14, tmax=24, hourly_pop={8: 80, 14: 80, 20: 80}),
    ]
    w = consensus_weather(readings, TH, PERIODS, {"temp": 4, "rain": 2})
    assert w is not None
    assert w.score == 34  # 100 - 50 (dispersión tope) - 16 (2 fuentes de temp faltantes)
    assert w.rain_score == 98  # 2/2 fuentes de lluvia coinciden exactamente
    assert w.score != w.rain_score
    assert f"Confianza del dato: {w.score} %" in weather_section(w, {})
    assert f"Confianza del dato: {w.rain_score} %" in (rain_section(w, {}) or "")


def test_message_has_no_model_or_source_names():
    readings = [
        WeatherReading(
            f"Open-Meteo/{m}",
            temp=15,
            tmin=11,
            tmax=20,
            hourly_pop={14: 90, 8: 10},
            hourly_mm={14: 3.0},
            hourly_feels={8: 12, 14: 17},
        )
        for m in ("ECMWF", "GFS", "ICON")
    ]
    weather = consensus_weather(readings, TH, PERIODS, {"temp": 3, "rain": 3})
    air = consensus_air([AirReading("WAQI", 40), AirReading("Open-Meteo AQ", 44)], TH, 2)
    trm = compute_trm([(date(2026, 10, 25), 3800.0)], date(2026, 10, 25), 3801.0, TH)
    brief = Brief(
        name="Juli",
        today=date(2026, 10, 25),
        greeting=build_greeting("Juli", date(2026, 10, 25)),
        weather=weather,
        air=air,
        trm=trm,
        mobility=MobilitySummary(pyp_text=None),
        news=[NewsItem("Marcha en Bogotá", "https://x.co", "Citytv", datetime(2026, 10, 25, tzinfo=UTC))],
        quote=Quote("Caminante, no hay camino.", "Antonio Machado"),
    )
    text = "\n".join(build_sections(brief, {})).lower()
    for word in BANNED:
        assert word not in text, word
    assert text.count("confianza del dato:") == 4
