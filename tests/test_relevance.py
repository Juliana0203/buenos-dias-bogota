from datetime import UTC, date, datetime

from brief import history
from brief.config import load_config
from brief.formatter import format_brief
from brief.main import build_calm_fixture_brief
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
from brief.sources.good_news import pick_good_news

REL_CFG = load_config().section("relevance")
ALERTS_CFG = load_config().section("alerts")
GOOD_CFG = load_config().section("good_news")


def _stat(v, score=85):
    return Stat(value=v, low=v, high=v, spread=0.0, n=3, confidence="alta", sources=["A", "B", "C"], score=score)


def _calm_brief(day: date = date(2026, 11, 26), trm_value: float = 3630.89) -> Brief:
    """Jueves normal sin lluvia fuerte, sin extremo térmico, sin festivo ni alertas."""
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
        value=trm_value,
        valid_from=day.isoformat(),
        previous=3620.0,
        change_abs=10.89,
        change_pct=0.30,
        avg30=3650.0,
        vs_avg30_pct=-0.52,
        is_min30=False,
        is_max30=False,
        backup_value=3632.0,
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


def test_realistic_calm_day_pipeline_is_silent_and_clima_tranquilo_only_when_no_weather_rules():
    cfg = load_config()
    # Jueves 26 de noviembre de 2026: sin festivo hoy/mañana, lecturas de modelos en calma y cierre de fin de semana
    calm = build_calm_fixture_brief(cfg, today=date(2026, 11, 26), now_hour=8)
    assert calm.relevance is not None
    assert not calm.relevance.triggered and calm.relevance.silent
    msgs = format_brief(calm, ALERTS_CFG)
    assert len(msgs) == 1
    lines = msgs[0].splitlines()
    assert len(lines) == 2
    assert "Clima tranquilo" in lines[1]
    assert "$3.630,89" in lines[1]
    assert "cierres del fin de semana" in lines[1]

    # Si se activa una regla de clima, "Clima tranquilo" desaparece inmediatamente
    assert calm.weather is not None
    calm.weather.uv = _stat(9)
    rel_uv = evaluate_relevance(calm, REL_CFG, ALERTS_CFG)
    assert rel_uv.triggered
    assert "Clima tranquilo" not in rel_uv.useful_line


def test_weekend_closure_and_live_blog_without_current_effect_do_not_sound_on_thursday():
    thursday = date(2026, 11, 26)
    b = _calm_brief(thursday)
    weekend_item = NewsItem(
        "Movilidad en Bogotá en VIVO hoy, 26 de noviembre: "
        "Distrito anunció los cierres del fin de semana por Salsa al Parque",
        "https://infobae.com/colombia/cierres-fin-de-semana",
        "Infobae Colombia",
        datetime.now(UTC),
        summary="Inicia la jornada con normalidad. Los cierres serán el sábado 28 y domingo 29 de noviembre.",
    )
    future_date_item = NewsItem(
        "Cierre vial en la calle 80 el próximo 30 de noviembre por obras del metro",
        "https://eltiempo.com/bogota/cierre-30-nov",
        "El Tiempo",
        datetime.now(UTC),
    )
    b.news = [weekend_item, future_date_item]
    res_thu = evaluate_relevance(b, REL_CFG, ALERTS_CFG)
    assert not res_thu.triggered and res_thu.silent

    # El viernes 27 de noviembre sí avisa los cierres del fin de semana (28 y 29 de noviembre)
    friday = date(2026, 11, 27)
    b_fri = _calm_brief(friday)
    b_fri.news = [
        NewsItem(
            "Distrito anunció cierres viales este fin de semana (28 y 29 de noviembre) por Salsa al Parque",
            "https://eltiempo.com/bogota/salsa",
            "El Tiempo",
            datetime.now(UTC),
        )
    ]
    res_fri = evaluate_relevance(b_fri, REL_CFG, ALERTS_CFG)
    assert any(a.key == "mobility_news" for a in res_fri.alerts)


def test_hoy_line_does_not_repeat_rain_or_trm_already_shown_in_alerts():
    b = _calm_brief()
    assert b.weather is not None and b.trm is not None
    b.weather.rain_periods["tarde"] = _stat(85, score=53)
    b.weather.rain_score = 53
    b.weather.rain_peak_hour = 14
    b.trm.change_pct = 1.90
    b.trm.is_max30 = True
    b.relevance = evaluate_relevance(b, REL_CFG, ALERTS_CFG)
    assert b.relevance.triggered and not b.relevance.silent
    text = format_smart_daily(b, ALERTS_CFG)
    lines = text.splitlines()
    assert 4 <= len(lines) <= 6
    assert lines[0].startswith("☔ <b>Hoy sí lleva paraguas:</b>")
    hoy_line = next(line for line in lines if line.startswith("💡 Hoy:"))
    # La línea "Hoy:" no repite la lluvia (85 %) ni la TRM ($3.630,89) que ya tienen su propia línea de alerta
    assert "85 %" not in hoy_line and "lluvia" not in hoy_line.lower()
    assert "$3.630,89" not in hoy_line and "TRM" not in hoy_line
    assert "Clima tranquilo" not in text
    assert text.count("lluvia 53 %") == 1
    assert text.count("clima 88 %") == 1


def test_trm_30d_extreme_does_not_repeat_within_5_days_unless_daily_change_ge_1_5_pct():
    day = date(2026, 11, 26)
    b = _calm_brief(day)
    assert b.trm is not None
    b.trm.change_pct = 0.40
    b.trm.is_max30 = True

    res_first = evaluate_relevance(b, REL_CFG, ALERTS_CFG, last_trm_extreme_date=None)
    assert any(a.key == "trm_move" for a in res_first.alerts)

    res_repeat = evaluate_relevance(b, REL_CFG, ALERTS_CFG, last_trm_extreme_date=date(2026, 11, 24))
    assert not any(a.key == "trm_move" for a in res_repeat.alerts)

    b.trm.change_pct = 1.75
    res_big = evaluate_relevance(b, REL_CFG, ALERTS_CFG, last_trm_extreme_date=date(2026, 11, 24))
    assert any(a.key == "trm_move" for a in res_big.alerts)


def test_good_news_rejects_controversial_and_debt_words():
    now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    items = [
        NewsItem("Starmind, el polémico proyecto aprobado por EE.UU.", "https://bbc.com/1", "BBC", now),
        NewsItem("Alemania ante una deuda récord pese a avance industrial", "https://dw.com/2", "DW", now),
    ]
    assert pick_good_news(items, GOOD_CFG, now) is None


def test_at_least_40_greeting_templates_and_no_consecutive_repeat(tmp_path):
    assert len(ALL_TEMPLATES) >= 40
    d1 = date(2026, 10, 26)
    g1 = build_greeting("Juli", d1)
    import random

    g_same_seed = build_greeting("Juli", d1, rng=random.Random(d1.toordinal()), last_template_id=g1.template_id)
    assert g_same_seed.template_id != g1.template_id

    csv_path = tmp_path / "history.csv"
    b = _calm_brief(d1)
    history.append_row(history.build_row(b, sent=True), csv_path)
    assert history.last_greeting_id(date(2026, 10, 27), csv_path) == b.greeting.template_id
