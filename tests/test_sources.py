from datetime import UTC, date, datetime, timedelta

from brief.config import load_config
from brief.formatter import build_sections, mobility_section, trm_section
from brief.models import Brief, MobilitySummary, NewsItem, Quote
from brief.personalization import build_greeting
from brief.sources.mobility import pico_y_placa_text
from brief.sources.news import dedupe, filter_bogota_mobility, normalize, parse_feed, score_item, short_summary
from brief.sources.trm import compute_trm, parse_rows
from brief.sources.weather import parse_open_meteo  # noqa: F401

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
NEWS_CFG = load_config().section("news")

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Marcha en Bogota bloquea la calle 26</title><link>https://a/1</link>
<pubDate>Sun, 25 Oct 2026 10:00:00 GMT</pubDate>
<description>&lt;p&gt;Manifestantes bloquean la via. Hay desvios.&lt;/p&gt;</description></item>
<item><title>Sin fecha</title><link>https://a/2</link></item>
</channel></rss>"""


def test_parse_feed_skips_undated_and_strips_html():
    items = parse_feed(RSS, {"name": "F", "local": True})
    assert len(items) == 1
    assert items[0].local and items[0].summary == "Manifestantes bloquean la via."


def test_short_summary_limits_words_and_drops_title_copy():
    long = "uno " * 40
    assert len(short_summary(long, "otro", 20).split()) <= 20
    assert short_summary("Mismo titular", "Mismo titular") == ""


def _item(title, hours=1, local=False, summary=""):
    return NewsItem(title, f"https://x/{title}", "S", NOW - timedelta(hours=hours), summary=summary, local=local)


def test_dedupe_similar_titles():
    a = _item("Cierre vial en la Caracas por obras en Bogotá")
    b = _item("Obras en Bogotá: cierre vial en la Caracas")
    c = _item("Marcha en Bogotá bloquea la calle 26")
    for i in (a, b, c):
        i.score = 1
    assert len(dedupe([a, b, c], 78)) == 2


def test_news_outside_bogota_are_discarded():
    items = [
        _item("Marcha en Medellín bloquea la avenida Oriental"),
        _item("Paro de transportadores en Cali"),
        _item("Cierre vial en la autopista Norte por obras"),
        _item("Marcha en Bogotá bloquea la calle 26"),
    ]
    kept = filter_bogota_mobility(items, NEWS_CFG, NOW)
    assert {i.title for i in kept} == {
        "Marcha en Bogotá bloquea la calle 26",
        "Cierre vial en la autopista Norte por obras",
    }


def test_bogota_without_mobility_keywords_is_discarded():
    assert score_item(_item("Bogotá celebra concierto de rock"), NEWS_CFG, NOW) == 0
    assert score_item(_item("Marcha hoy en otro país"), NEWS_CFG, NOW) == 0


def test_mobility_discards_anecdotes_and_keeps_real_effects():
    anecdote = _item("el reclamo viral de una pasajera tras intentar meter una lavadora a una estación de TransMilenio")
    useful = _item("cierre de la Autopista Norte por obras")
    assert score_item(anecdote, NEWS_CFG, NOW) == 0
    assert score_item(useful, NEWS_CFG, NOW) > 0
    kept = filter_bogota_mobility([anecdote, useful], NEWS_CFG, NOW)
    assert [i.title for i in kept] == ["cierre de la Autopista Norte por obras"]
    configured = {normalize(w) for w in NEWS_CFG["mobility_discard_keywords"]}
    expected = {
        "viral",
        "reclamo",
        "video",
        "polemica",
        "me parece",
        "insolito",
        "curioso",
        "usuario",
        "pasajera",
        "pasajero",
        "tiktok",
        "redes sociales",
    }
    assert expected <= configured


def test_mobility_empty_message_and_no_duplicate_news():
    item = _item("cierre de la Autopista Norte por obras")
    empty_brief = Brief(
        name="Juli",
        today=date(2026, 10, 25),
        greeting=build_greeting("Juli", date(2026, 10, 25)),
        weather=None,
        air=None,
        trm=None,
        mobility=MobilitySummary(pyp_text=None),
        news=[],
        quote=Quote("Frase", "Autor"),
    )
    assert "Sin alertas de movilidad importantes por ahora" in mobility_section(empty_brief)
    with_news = Brief(
        name="Juli",
        today=date(2026, 10, 25),
        greeting=build_greeting("Juli", date(2026, 10, 25)),
        weather=None,
        air=None,
        trm=None,
        mobility=MobilitySummary(pyp_text=None),
        news=[item],
        good_news=item,
        quote=Quote("Frase", "Autor"),
    )
    full_text = "\n\n".join(build_sections(with_news, {}))
    assert full_text.count(">cierre de la Autopista Norte por obras</a>") == 1


def test_old_news_are_discarded():
    old = _item("Marcha en Bogotá bloquea la calle 26", hours=30)
    assert filter_bogota_mobility([old], NEWS_CFG, NOW) == []


def test_mobility_ranks_above_services_and_local_bonus():
    mob = _item("Transmilenio con retrasos en Bogotá")
    srv = _item("Corte de agua en Suba, Bogotá")
    assert score_item(mob, NEWS_CFG, NOW) > score_item(srv, NEWS_CFG, NOW) > 0
    assert score_item(_item("Transmilenio con retrasos en Bogotá", local=True), NEWS_CFG, NOW) > score_item(
        mob, NEWS_CFG, NOW
    )


def test_max_four_news():
    titles = ["Marcha", "Bloqueo", "Paro", "Accidente", "Obras", "Transmilenio", "Cierre vial"]
    items = [_item(f"{t} en Bogotá número {n * 100} distinto tema {t}x") for n, t in enumerate(titles)]
    kept = filter_bogota_mobility(items, NEWS_CFG, NOW)
    assert len(kept[: NEWS_CFG["max_items"]]) <= 4


def test_pico_y_placa_modes():
    mon = date(2026, 10, 26)
    assert pico_y_placa_text({"plate": None}, mon) is None
    assert "Revisa" in pico_y_placa_text({"plate": "123", "mode": None}, mon)
    cfg = {"plate": "123", "mode": "weekday", "restricted": {0: [3, 4]}, "hours": ["06:00-09:00"]}
    assert "tienes pico y placa" in pico_y_placa_text(cfg, mon)
    assert "no hay pico" in pico_y_placa_text(cfg, date(2026, 10, 25))


def test_trm_compute_alerts():
    rows = [
        {"vigenciadesde": f"2026-10-{d:02d}T00:00:00.000", "valor": str(v)}
        for d, v in [(23, 3100), (22, 3150), (21, 3200), (20, 3210), (19, 3220), (16, 3230)]
    ]
    t = compute_trm(parse_rows(rows), date(2026, 10, 23), 3105.0, {"trm_pct": 1.5})
    assert t is not None
    assert t.value == 3100 and t.previous == 3150 and t.change_abs == -50
    assert t.is_min30 and not t.is_max30 and t.confidence == "alta"
    assert compute_trm([], date(2026, 10, 23), None, {}) is None


def test_trm_friday_valid_until_monday_has_no_yesterday_variation_on_sunday():
    rows = [
        {
            "vigenciadesde": "2026-10-23T00:00:00.000",
            "vigenciahasta": "2026-10-26T00:00:00.000",
            "valor": "4200.00",
        },
        {
            "vigenciadesde": "2026-10-22T00:00:00.000",
            "vigenciahasta": "2026-10-22T00:00:00.000",
            "valor": "4180.00",
        },
        {
            "vigenciadesde": "2026-10-21T00:00:00.000",
            "vigenciahasta": "2026-10-21T00:00:00.000",
            "valor": "4170.00",
        },
    ]
    parsed = parse_rows(rows)
    sunday = compute_trm(parsed, date(2026, 10, 25), 4202.0, {"trm_pct": 1.5})
    assert sunday is not None
    assert sunday.value == 4200.0
    assert sunday.valid_from == "2026-10-23" and sunday.valid_until == "2026-10-26"
    assert sunday.previous == 4180.0
    assert sunday.is_weekend_or_holiday is True
    assert sunday.change_abs is None and sunday.change_pct is None
    sec = trm_section(sunday)
    assert "vs. ayer" not in sec
    assert "valor vigente del último día hábil" in sec
    assert "Fuente:" not in sec

    friday = compute_trm(parsed, date(2026, 10, 23), 4202.0, {"trm_pct": 1.5})
    assert friday is not None and friday.change_abs == 20.0 and "vs. ayer" in trm_section(friday)
