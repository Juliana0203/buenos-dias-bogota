from datetime import UTC, date, datetime

from brief import history
from brief.config import load_config
from brief.formatter import format_brief
from brief.models import (
    AirSummary,
    Brief,
    FeelsRange,
    MobilitySummary,
    NewsItem,
    Quote,
    Stat,
    TrmSummary,
    WeatherSummary,
)
from brief.personalization import ALL_TEMPLATES, build_greeting
from brief.relevance import evaluate_relevance, format_smart_daily

REL_CFG = load_config().section("relevance")
ALERTS_CFG = load_config().section("alerts")


def _stat(v, score=85):
    return Stat(value=v, low=v, high=v, spread=0.0, n=3, confidence="alta", sources=["A", "B", "C"], score=score)


def _calm_brief(day: date = date(2026, 10, 27)) -> Brief:
    """Martes normal sin lluvia fuerte, sin extremo térmico, sin festivo ni alertas."""
    weather = WeatherSummary(
        temp=_stat(15),
        feels_like=_stat(15),
        tmin=_stat(12),
        tmax=_stat(19),
        wind_kmh=_stat(8),
        uv=_stat(5),
        rain_periods={"mañana": _stat(20), "tarde": _stat(35), "noche": _stat(25)},
        rain_peak_hour=15,
        rain_peak_pct=35.0,
        sources=["A", "B"],
        confidence="alta",
        feels_periods={
            "mañana": FeelsRange(12, 16, 2),
            "tarde": FeelsRange(15, 19, 2),
            "noche": FeelsRange(12, 14, 2),
        },
        score=88,
        rain_score=85,
        now_hour=8,
    )
    air = AirSummary(aqi=_stat(42), level="buena", pollutant="PM2.5", sources=["A"], confidence="alta", score=80)
    trm = TrmSummary(
        value=4150.0,
        valid_from=day.isoformat(),
        previous=4140.0,
        change_abs=10.0,
        change_pct=0.24,
        avg30=4160.0,
        vs_avg30_pct=-0.24,
        is_min30=False,
        is_max30=False,
        backup_value=4152.0,
        sources=["T", "B"],
        confidence="alta",
        score=92,
    )
    return Brief(
        name="Juli",
        today=day,
        greeting=build_greeting("Juli", day),
        weather=weather,
        air=air,
        trm=trm,
        mobility=MobilitySummary(pyp_text="Hoy no tienes pico y placa (placa 3)."),
        news=[],
        quote=Quote("Haced las cosas bien; es más importante que hacerlas.", "Antonio Machado"),
    )


def test_calm_day_sends_two_line_silent_message():
    b = _calm_brief()
    b.relevance = evaluate_relevance(b, REL_CFG, ALERTS_CFG)
    assert not b.relevance.triggered and b.relevance.silent
    msgs = format_brief(b, ALERTS_CFG)
    assert len(msgs) == 1
    lines = msgs[0].splitlines()
    assert len(lines) == 2
    assert "sin alertas en Bogotá" in lines[0]
    assert "Hoy:" in lines[1]


def test_rain_in_outing_period_triggers_4_to_6_line_alert_with_umbrella_first():
    b = _calm_brief()
    assert b.weather is not None
    b.weather.rain_periods["tarde"] = _stat(85)
    b.weather.rain_peak_hour = 14
    b.relevance = evaluate_relevance(b, REL_CFG, ALERTS_CFG)
    assert b.relevance.triggered and not b.relevance.silent
    text = format_smart_daily(b, ALERTS_CFG)
    lines = text.splitlines()
    assert 4 <= len(lines) <= 6
    assert lines[0].startswith("☔ <b>Hoy sí lleva paraguas:</b>")
    assert "85 %" in lines[0]


def test_each_relevance_rule_triggers_independently():
    # Sensación extrema fría
    b_cold = _calm_brief()
    assert b_cold.weather is not None
    b_cold.weather.feels_periods["mañana"] = FeelsRange(7, 13, 2)
    res_cold = evaluate_relevance(b_cold, REL_CFG, ALERTS_CFG)
    assert any(a.key == "feels_extreme_cold" for a in res_cold.alerts)

    # UV >= 8
    b_uv = _calm_brief()
    assert b_uv.weather is not None
    b_uv.weather.uv = _stat(9)
    res_uv = evaluate_relevance(b_uv, REL_CFG, ALERTS_CFG)
    assert any(a.key == "uv_extreme" for a in res_uv.alerts)

    # AQI dañino (>= 101)
    b_aqi = _calm_brief()
    assert b_aqi.air is not None
    b_aqi.air.aqi = _stat(115)
    b_aqi.air.level = "dañina para grupos sensibles"
    res_aqi = evaluate_relevance(b_aqi, REL_CFG, ALERTS_CFG)
    assert any(a.key == "aqi_harmful" for a in res_aqi.alerts)

    # Movilidad / pico y placa que me afecta
    b_mob = _calm_brief()
    b_mob.mobility = MobilitySummary(pyp_text="Hoy tienes pico y placa (placa 3), 06:00-09:00 y 15:00-19:00.")
    b_mob.news = [NewsItem("Cierre en la Autopista Norte por obras", "https://x.co/1", "El Tiempo", datetime.now(UTC))]
    res_mob = evaluate_relevance(b_mob, REL_CFG, ALERTS_CFG)
    keys = {a.key for a in res_mob.alerts}
    assert "pico_y_placa" in keys and "mobility_news" in keys

    # TRM variación >= 1.5% o mín/máx 30 días
    b_trm = _calm_brief()
    assert b_trm.trm is not None
    b_trm.trm.change_pct = -1.85
    b_trm.trm.is_min30 = True
    res_trm = evaluate_relevance(b_trm, REL_CFG, ALERTS_CFG)
    assert any(a.key == "trm_move" for a in res_trm.alerts)

    # Festivo hoy o mañana (25 dic)
    b_hol = _calm_brief(date(2026, 12, 24))
    res_hol = evaluate_relevance(b_hol, REL_CFG, ALERTS_CFG)
    assert any(a.key == "holiday_tomorrow" for a in res_hol.alerts)


def test_at_least_40_greeting_templates_and_no_consecutive_repeat(tmp_path):
    assert len(ALL_TEMPLATES) >= 40
    d1 = date(2026, 10, 26)
    g1 = build_greeting("Juli", d1)
    assert g1.template_id
    # Si forzamos el mismo RNG y le pasamos last_template_id=g1.template_id, debe cambiar de plantilla.
    import random

    g_same_seed = build_greeting("Juli", d1, rng=random.Random(d1.toordinal()), last_template_id=g1.template_id)
    assert g_same_seed.template_id != g1.template_id

    # Verifica que history.csv persiste greeting_id y last_greeting_id lo recupera.
    csv_path = tmp_path / "history.csv"
    b = _calm_brief(d1)
    history.append_row(history.build_row(b, sent=True), csv_path)
    assert history.last_greeting_id(date(2026, 10, 27), csv_path) == b.greeting.template_id
