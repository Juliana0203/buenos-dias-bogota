from datetime import UTC, datetime, timedelta

from brief.config import load_config
from brief.models import NewsItem
from brief.sources.good_news import looks_spanish, pick_good_news

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
CFG = load_config().section("good_news")


def _item(title, summary="", hours=5):
    return NewsItem(title, f"https://x/{abs(hash(title))}", "S", NOW - timedelta(hours=hours), summary=summary)


def test_picks_positive_spanish_news():
    items = [
        _item("Científicos logran un avance contra una enfermedad rara"),
        _item("Nuevo récord de energía renovable en Europa"),
        _item("Mercados cierran sin cambios"),
    ]
    picked = pick_good_news(items, CFG, NOW)
    assert picked is not None and ("avance" in picked.title or "récord" in picked.title)


def test_negative_words_discard_even_if_positive():
    assert pick_good_news([_item("Récord de víctimas tras ataque", "")], CFG, NOW) is None
    assert pick_good_news([_item("Rescate tras accidente en la mina")], CFG, NOW) is None


def test_old_news_are_discarded_after_48h():
    assert pick_good_news([_item("Gran avance científico", hours=60)], CFG, NOW) is None


def test_english_news_are_discarded():
    assert not looks_spanish("Scientists make breakthrough with the new record", CFG["stopwords_en"])
    assert pick_good_news([_item("Record recovery for the species after the program")], CFG, NOW) is None


def test_no_candidate_returns_none_without_inventing():
    assert pick_good_news([_item("Se reúne el comité de la ciudad")], CFG, NOW) is None
    assert pick_good_news([], CFG, NOW) is None
