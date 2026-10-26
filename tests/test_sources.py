from datetime import UTC, date, datetime, timedelta

from brief.models import NewsItem
from brief.sources.mobility import find_alerts, pico_y_placa_text
from brief.sources.news import dedupe, parse_feed, score_item, select_top, short_summary
from brief.sources.trm import compute_trm, parse_rows
from brief.sources.weather import parse_open_meteo  # noqa: F401

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Marcha en Bogota bloquea la calle 26</title><link>https://a/1</link>
<pubDate>Sun, 25 Oct 2026 10:00:00 GMT</pubDate>
<description>&lt;p&gt;Manifestantes bloquean la via. Hay desvios.&lt;/p&gt;</description></item>
<item><title>Sin fecha</title><link>https://a/2</link></item>
</channel></rss>"""


def test_parse_feed_skips_undated_and_strips_html():
    items = parse_feed(RSS, {"name": "F", "scope": "colombia"})
    assert len(items) == 1
    assert items[0].scope == "bogota" and items[0].summary == "Manifestantes bloquean la via."


def test_short_summary_limits_words_and_drops_title_copy():
    long = "uno " * 40
    assert len(short_summary(long, "otro", 20).split()) <= 20
    assert short_summary("Mismo titular", "Mismo titular") == ""


def _item(title, scope="colombia", hours=1):
    return NewsItem(title, f"https://x/{title}", "S", NOW - timedelta(hours=hours), scope=scope)


def test_dedupe_similar_titles():
    a = _item("Lluvias dejan inundaciones en Cartagena")
    b = _item("Inundaciones en Cartagena por lluvias")
    c = _item("Dólar baja en Colombia")
    for i in (a, b, c):
        i.score = 1
    assert len(dedupe([a, b, c], 78)) == 2


def test_score_prioritizes_keywords_over_gossip():
    kw = {"marcha": 5}
    assert score_item(_item("Marcha hoy"), kw, ["farandula"], NOW) > score_item(
        _item("Farandula del día"), kw, ["farandula"], NOW
    )


def test_select_top_balances_scopes():
    items = [_item(f"c{i}") for i in range(5)] + [_item(f"b{i}", "bogota") for i in range(5)]
    for n, i in enumerate(items):
        i.score = n
    top = select_top(items, 3, 2)
    assert sum(i.scope == "bogota" for i in top) == 2 and len(top) == 5


def test_mobility_alerts_need_bogota_context():
    alerts = find_alerts(
        [_item("Marcha en Medellín"), _item("Cierre vial en la Caracas", "bogota")],
        ["marcha", "cierre vial"],
    )
    assert [a.title for a in alerts] == ["Cierre vial en la Caracas"]


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
        for d, v in [(25, 3100), (24, 3150), (23, 3200), (22, 3210), (21, 3220), (20, 3230)]
    ]
    t = compute_trm(parse_rows(rows), date(2026, 10, 25), 3105.0, {"trm_pct": 1.5})
    assert t.value == 3100 and t.previous == 3150 and t.change_abs == -50
    assert t.is_min30 and not t.is_max30 and t.confidence == "alta"
    assert compute_trm([], date(2026, 10, 25), None, {}) is None
