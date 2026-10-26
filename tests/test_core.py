from datetime import UTC, date, datetime

from brief.config import ROOT
from brief.consensus import aqi_level, combine, consensus_air, consensus_weather
from brief.formatter import fmt_stat, format_brief, split_message
from brief.models import (
    AirReading,
    Brief,
    MobilitySummary,
    NewsItem,
    Quote,
    WeatherReading,
)
from brief.personalization import (
    FRIDAY,
    GENERIC,
    HOLIDAY,
    HOLIDAY_TOMORROW,
    MONDAY,
    RAIN,
    WEEKEND,
    build_greeting,
    date_es,
    holiday_name,
)
from brief.quotes import load_quotes, pick_local

FOOD_WORDS = (
    "tinto", "tintico", "café", "cafecito", "bebida", "chocolate", "aguapanela",
    "cerveza", "vino", "jugo", "desayuno", "almuerzo", "comida", "alimento",
)  # fmt: skip


def test_no_food_or_drink_in_templates():
    for tpl in [*GENERIC, *MONDAY, *FRIDAY, *WEEKEND, *RAIN, *HOLIDAY, *HOLIDAY_TOMORROW]:
        assert not any(w in tpl.lower() for w in FOOD_WORDS), tpl


PERIODS = {"mañana": (6, 12), "tarde": (12, 18), "noche": (18, 24)}
TH = {"temp_c": 3.0, "rain_pct": 25.0, "aqi": 30.0, "uv": 3.0, "wind_kmh": 15.0}


def test_combine_median_and_agreement():
    s = combine({"a": 14, "b": 15, "c": 16}, 3.0)
    assert s.value == 15 and not s.disagree and s.confidence == "alta"


def test_combine_disagreement_gives_range():
    s = combine({"a": 10, "b": 15, "c": 16}, 3.0)
    assert s.disagree and (s.low, s.high) == (10, 16)
    assert fmt_stat(s, " °C") == "entre 10 y 16 °C"


def test_combine_single_and_empty():
    assert combine({"a": 12}, 3).confidence == "baja"
    assert combine({"a": None}, 3).value is None


def test_consensus_weather_uses_median_and_ignores_missing():
    readings = [
        WeatherReading("A", temp=14, tmin=10, tmax=20, hourly_pop={14: 80, 8: 10}),
        WeatherReading("B", temp=15, tmin=11, tmax=21, hourly_pop={14: 60, 8: 20}),
        WeatherReading("C", temp=16, hourly_pop={14: 70, 8: 15}),
    ]
    w = consensus_weather(readings, TH, PERIODS)
    assert w.temp.value == 15
    assert w.tmin.value == 10.5 and w.tmin.n == 2
    assert w.rain_peak_hour == 14
    assert len(w.sources) == 3


def test_consensus_weather_none_when_empty():
    assert consensus_weather([], TH, PERIODS) is None


def test_air_consensus_and_level():
    a = consensus_air([AirReading("X", 40), AirReading("Y", 60)], TH)
    assert a.aqi.value == 50 and a.level == aqi_level(50)
    assert aqi_level(30) == "buena"
    assert consensus_air([], TH) is None


def test_greeting_deterministic_and_holiday():
    d = date(2026, 12, 25)
    g = build_greeting("Juli", d)
    assert g.holiday_today and "Juli" in g.text
    assert build_greeting("Juli", d).text == g.text
    assert holiday_name(date(2026, 12, 24)) is None
    assert build_greeting("Juli", date(2026, 12, 24)).holiday_tomorrow


def test_greeting_mentions_rain():
    assert (
        "paraguas" in build_greeting("Juli", date(2026, 3, 3), rain_pct=90).text
        or "lluvia" in build_greeting("Juli", date(2026, 3, 3), rain_pct=90).text
    )


def test_date_es():
    assert date_es(date(2026, 10, 25)) == "Domingo 25 de octubre"


def test_quotes_at_least_90_no_repeat_in_90_days():
    quotes = load_quotes()
    assert len(quotes) >= 90
    start = date(2026, 1, 1).toordinal()
    picks = [pick_local(quotes, date.fromordinal(start + i)).text for i in range(90)]
    assert len(set(picks)) == 90
    assert pick_local(quotes, date(2026, 5, 5)) == pick_local(quotes, date(2026, 5, 5))


def test_quotes_spanish_with_author_and_no_zenquotes():
    english = {" the ", " and ", " you ", " is ", " of ", " to "}
    raw = (ROOT / "data" / "quotes_es.json").read_text(encoding="utf-8").lower()
    assert "zenquotes" not in raw
    for singer in ("violeta parra", "serrat", "numhauser", "facundo cabral"):
        assert singer not in raw
    for q in load_quotes():
        assert q.author.strip()
        assert not any(w in f" {q.text.lower()} " for w in english)
        assert not any(w in q.text.lower() for w in FOOD_WORDS)
    import brief.quotes as bq

    assert not hasattr(bq, "fetch_zenquote")


def _brief(news):
    return Brief(
        name="Juli",
        today=date(2026, 10, 25),
        greeting=build_greeting("Juli", date(2026, 10, 25)),
        weather=None,
        air=None,
        trm=None,
        mobility=MobilitySummary(pyp_text=None),
        news=news,
        quote=Quote("Hola <b>", "Autor & Co"),
    )


def test_formatter_escapes_and_notes_missing_sections():
    item = NewsItem("A < B & C", "https://x.co/?a=1&b=2", "Fuente", datetime.now(UTC))
    msgs = format_brief(_brief([item]))
    text = "\n".join(msgs)
    assert "A &lt; B &amp; C" in text and "a=1&amp;b=2" in text
    assert "Hola &lt;b&gt;" in text
    assert "No pude verificar hoy: clima, calidad del aire, TRM" in text


def test_split_message_respects_limit():
    sections = ["línea\n" * 300 for _ in range(4)]
    parts = split_message([s.strip() for s in sections], 4096)
    assert len(parts) >= 2 and all(len(p) <= 4096 for p in parts)
    assert split_message(["hola", "mundo"]) == ["hola\n\nmundo"]
