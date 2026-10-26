"""Consenso de lluvia con los valores crudos de Open-Meteo del 2026-10-25 (BogotÃ¡)."""

import logging

from brief.consensus import consensus_weather
from brief.formatter import rain_section, weather_section
from brief.models import WeatherReading
from brief.sources.weather import parse_open_meteo

MODELS = {
    "temperature": ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"],
    "precip_probability": ["ecmwf_ifs025", "gfs_seamless"],
    "precip_amount": ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"],
}
TH = {"temp_c": 3.0, "rain_pct": 25.0, "wind_kmh": 15.0, "uv": 3.0}
PERIODS = {"maÃ±ana": (6, 12), "tarde": (12, 18), "noche": (18, 24)}

RAW = {
    "hourly": {
        "time": [f"2026-10-25T{h:02d}:00" for h in range(24)],
        "precipitation_probability_ecmwf_ifs025": [11, 2, 0, 0, 0, 0, 0, 0, 2, 7, 18, 43, 75, 98, 100, 100, 100, 98]
        + [94, 86, 70, 49, 31, 18],
        "precipitation_probability_gfs_seamless": [25, 6, 0, 0, 0, 0, 0, 3, 7, 14, 26, 50, 79, 100, 100, 100, 100, 100]
        + [100, 100, 93, 82, 68, 46],
        "precipitation_probability_icon_seamless": [0] * 17 + [3, 3] + [0] * 5,
        "precipitation_ecmwf_ifs025": [0.0] * 8
        + [0.1, 0.1, 0.1, 0.6, 0.6, 0.6, 0.5, 0.5, 0.5, 0.1, 0.1, 0.1]
        + [0.0] * 4,
        "precipitation_gfs_seamless": [0.0] * 10 + [0.1, 0.2, 0.5, 0.8, 0.8, 0.9, 0.3, 0.1, 0.0, 0.1] + [0.0] * 4,
        "precipitation_icon_seamless": [0.0] * 17 + [0.1] + [0.0] * 6,
        "temperature_2m_ecmwf_ifs025": [13.0, 12.7, 12.0, 11.1, 10.8, 11.6, 13.0, 14.7, 16.9, 19.2, 20.9, 21.4]
        + [21.2, 20.7, 20.2, 19.4, 18.4, 17.3, 16.1, 15.0, 14.3, 13.8, 13.2, 12.5],
        "temperature_2m_gfs_seamless": [12.1, 11.7, 11.4, 11.2, 10.9, 10.6, 10.6, 12.9, 15.9, 18.8, 20.9, 20.8]
        + [19.5, 19.4, 18.2, 17.2, 16.7, 15.4, 14.0, 13.5, 13.8, 13.9, 13.3, 12.6],
        "temperature_2m_icon_seamless": [11.5, 11.1, 10.7, 10.1, 9.6, 9.3, 9.2, 10.8, 13.9, 16.5, 18.4, 19.4]
        + [20.0, 20.1, 19.9, 19.5, 17.7, 16.5, 14.9, 13.5, 12.6, 12.1, 11.6, 11.3],
    }
}


def _today():
    readings = parse_open_meteo(RAW, MODELS, now_hour=8)
    return readings, consensus_weather(readings, TH, PERIODS)


def test_icon_does_not_vote_on_probability():
    readings, w = _today()
    icon = next(r for r in readings if r.source.endswith("ICON"))
    assert icon.hourly_pop == {} and icon.hourly_mm
    assert w.rain_sources == ["Open-Meteo/ECMWF", "Open-Meteo/GFS"]
    assert all(len(s.sources) == 2 for s in w.rain_periods.values())


def test_range_3_100_no_longer_appears_and_confidence_high():
    _, w = _today()
    text = weather_section(w, {})
    assert "3 y 100" not in text and "3-100" not in text
    assert all(not s.disagree for s in w.rain_periods.values())
    assert w.rain_periods["tarde"].value == 100
    assert w.rain_confidence == "alta" and w.confidence == "alta"


def test_internal_note_kept_for_history_but_message_has_no_model_talk():
    _, w = _today()
    assert w.rain_note == "lluvia probable, aunque un modelo no la ve"
    text = rain_section(w, {})
    assert "Lluvia muy probable en la tarde (100 %)" in text
    assert "modelo" not in text.lower()


def test_message_shows_confidence_percentages_without_source_counts():
    _, w = _today()
    text = weather_section(w, {}) + "\n" + rain_section(w, {})
    assert "Verificado con" not in text and "fuentes" not in text
    assert text.count("Confianza del dato:") == 2
    assert 30 <= w.score <= 98 and 30 <= w.rain_score <= 98


def test_two_models_not_seeing_rain_lowers_confidence():
    readings, _ = _today()
    for r in readings:
        if r.source.endswith("GFS"):
            r.hourly_mm = {h: 0.0 for h in range(24)}
    w = consensus_weather(readings, TH, PERIODS)
    assert w.rain_note == "lluvia probable, aunque 2 modelos no la ven"
    assert w.rain_confidence == "baja" and w.confidence == "baja"


def test_suspect_source_is_excluded_and_logged(caplog):
    good = [
        WeatherReading("A", hourly_pop={h: 80.0 for h in range(24)}),
        WeatherReading("B", hourly_pop={h: 90.0 for h in range(24)}),
    ]
    bad = WeatherReading("C", hourly_pop={h: 3.0 for h in range(24)})
    with caplog.at_level(logging.WARNING):
        w = consensus_weather([*good, bad], TH, PERIODS)
    assert "C" not in w.rain_sources
    assert any("sospechosa" in m and "C" in m for m in caplog.messages)
    assert w.rain_periods["tarde"].value == 85


def test_low_source_is_kept_when_others_are_not_high():
    readings = [
        WeatherReading("A", hourly_pop={h: 20.0 for h in range(24)}),
        WeatherReading("C", hourly_pop={h: 3.0 for h in range(24)}),
    ]
    w = consensus_weather(readings, TH, PERIODS)
    assert w.rain_sources == ["A", "C"]


def _flat(source, pop):
    return WeatherReading(source, hourly_pop={h: pop for h in range(24)})


def _rain_conf(pops, mm_each):
    readings = [_flat(f"P{i}", p) for i, p in enumerate(pops)]
    readings += [WeatherReading(f"M{i}", hourly_mm={h: mm / 24 for h in range(24)}) for i, mm in enumerate(mm_each)]
    return consensus_weather(readings, TH, PERIODS)


def test_today_trace_base_is_media_then_mm_promote_to_alta():
    from brief.consensus import combine

    _, w = _today()
    # Paso 1: 2 fuentes de probabilidad que coinciden -> combine() da "media" en cada franja.
    assert all(s.confidence == "media" and not s.disagree for s in w.rain_periods.values())
    assert max(s.spread for s in w.rain_periods.values()) <= TH["rain_pct"]
    # Paso 2: 3 modelos de mm, 1 discrepa (1*2 < 3) -> se promueve a "alta".
    assert w.rain_confidence == "alta"
    assert combine({"a": 1, "b": 2}, 25).confidence == "media"


def test_spread_equal_to_threshold_is_not_disagreement():
    w = _rain_conf([100, 75], [3.9, 3.8, 3.7])
    assert not w.rain_periods["tarde"].disagree and w.rain_confidence == "alta"


def test_two_pop_sources_beyond_threshold_stay_low_even_if_mm_agree():
    w = _rain_conf([100, 60], [3.9, 3.8, 3.7])
    assert w.rain_periods["tarde"].disagree and w.rain_confidence == "baja"


def test_three_pop_sources_in_disagreement_are_not_promoted_by_mm():
    # Con 3 fuentes en desacuerdo combine() da "media"; los mm NO deben subirla a "alta".
    w = _rain_conf([100, 100, 60], [3.9, 3.8, 3.7])
    assert w.rain_periods["tarde"].disagree and w.rain_confidence == "media"
