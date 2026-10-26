import json
from datetime import UTC, date, datetime

import pytest

from brief import history, main
from brief.config import Config, Secrets
from brief.models import (
    AirSummary,
    Brief,
    Greeting,
    MobilitySummary,
    NewsItem,
    Quote,
    Stat,
    TrmSummary,
    WeatherSummary,
)

DAY = date(2026, 10, 25)


def _stat(v, n=2):
    return Stat(value=v, low=v - 1, high=v + 1, spread=2.0, n=n, confidence="media", sources=["A", "B"])


def full_brief() -> Brief:
    weather = WeatherSummary(
        temp=_stat(14),
        feels_like=_stat(13),
        tmin=_stat(11),
        tmax=_stat(21),
        wind_kmh=_stat(3),
        uv=_stat(7),
        rain_periods={"mañana": _stat(46), "tarde": _stat(100), "noche": _stat(97)},
        rain_peak_hour=14,
        rain_peak_pct=100.0,
        sources=["A", "B"],
        confidence="alta",
        rain_sources=["A", "B"],
        rain_confidence="alta",
        rain_note="lluvia probable, aunque un modelo no la ve",
    )
    return Brief(
        name="Juli",
        today=DAY,
        greeting=Greeting("Buen día, Juli", holiday_today=None, holiday_tomorrow=None),
        weather=weather,
        air=AirSummary(aqi=_stat(58), level="moderada", pollutant="PM2.5", sources=["A"], confidence="baja"),
        trm=TrmSummary(
            3158.43, "2026-10-25", 3185.01, -26.58, -0.83, 3232.3, -2.29, False, False, 3150.0, ["T"], "alta"
        ),
        mobility=MobilitySummary(pyp_text=None),
        news=[NewsItem("Titular", "https://x.co", "Fuente", datetime(2026, 10, 25, tzinfo=UTC))],
        quote=Quote("Frase", "Autor"),
    )


def test_write_latest_serializes_nested_stats(tmp_path):
    brief = full_brief()
    row = history.build_row(brief, sent=True)
    history.write_latest(brief, row, docs=tmp_path)
    data = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert data["weather"]["rain_periods"]["mañana"]["value"] == 46
    assert data["weather"]["rain_note"].startswith("lluvia probable")
    assert data["trm"]["value"] == 3158.43 and data["quote"]["author"] == "Autor"
    assert data["summary"]["rain_prob"] == "100"


def test_default_handles_dates_sets_dataclasses_and_unknown():
    assert history._default(DAY) == "2026-10-25"
    assert history._default(datetime(2026, 10, 25, 8, 0)) == "2026-10-25T08:00:00"
    assert history._default({"b", "a"}) == ["a", "b"]
    assert history._default(Stat(value=1.0))["value"] == 1.0
    with pytest.raises(TypeError, match="complex"):
        history._default(1j)


def test_history_json_and_dashboard_files(tmp_path):
    csv_path = tmp_path / "history.csv"
    history.append_row(history.build_row(full_brief(), sent=True), csv_path)
    history.write_history_json(csv_path, tmp_path)
    rows = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert rows[0]["date"] == "2026-10-25" and rows[0]["temp_max"] == "21.0"
    html = (history.DOCS / "index.html").read_text(encoding="utf-8")
    assert "latest.json" in html and "history.json" in html and "chart.js" in html.lower()


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    cfg = Config(
        raw={"persona": {"name": "Juli"}, "location": {"timezone": "America/Bogota"}, "alerts": {}},
        secrets=Secrets(telegram_token="t", telegram_chat_id="c"),
    )
    csv_path = tmp_path / "history.csv"
    monkeypatch.setattr(main, "load_config", lambda _=None: cfg)
    monkeypatch.setattr(main, "build_brief", lambda _: full_brief())
    monkeypatch.setattr(main, "format_brief", lambda b, a=None: ["hola"])
    sent: list = []
    monkeypatch.setattr(main, "send_messages", lambda *a: sent.append(a))
    monkeypatch.setattr(history, "HISTORY_CSV", csv_path)
    monkeypatch.setattr(history, "DOCS", tmp_path)
    monkeypatch.setattr(history, "append_row", lambda row, path=csv_path: _orig_append(row, path))
    monkeypatch.setattr(history, "write_history_json", lambda path=csv_path, docs=tmp_path: _orig_hj(path, docs))
    monkeypatch.setattr(history, "write_latest", lambda b, r, docs=tmp_path: _orig_latest(b, r, docs))
    return tmp_path, csv_path, sent


_orig_append = history.append_row
_orig_hj = history.write_history_json
_orig_latest = history.write_latest


def test_main_success_exits_zero_and_saves_everything(run_env):
    tmp, csv_path, sent = run_env
    assert main.main(["--no-wait", "--force"]) == 0
    assert len(sent) == 1
    assert (tmp / "latest.json").exists() and (tmp / "history.json").exists()
    assert history.read_history(csv_path)[0]["sent"] == "1"


def test_dashboard_failure_after_send_keeps_record_and_reports_error(run_env, monkeypatch, caplog):
    tmp, csv_path, sent = run_env

    def boom(*a, **k):
        raise TypeError("falla simulada")

    monkeypatch.setattr(history, "write_latest", boom)
    assert main.main(["--no-wait", "--force"]) == 4
    assert history.already_sent_today(DAY, csv_path) or history.read_history(csv_path)[0]["sent"] == "1"
    assert "latest.json" in caplog.text


def test_csv_failure_after_send_exits_with_clear_error(run_env, monkeypatch, caplog):
    def boom(*a, **k):
        raise OSError("disco lleno")

    monkeypatch.setattr(history, "append_row", boom)
    assert main.main(["--no-wait", "--force"]) == 3
    assert "NO se pudo registrar" in caplog.text


def test_morning_brief_workflow_has_backup_cron_pull_and_failure_alert():
    import yaml

    wf_path = history.ROOT / ".github" / "workflows" / "morning-brief.yml"
    raw = wf_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    triggers = data.get("on") or data.get(True)
    crons = [item["cron"] for item in triggers["schedule"]]
    assert "40 12 * * *" in crons and "10 13 * * *" in crons
    assert raw.index("git pull --rebase") < raw.index("python -m brief.main")
    assert "el resumen de hoy falló: revisa Actions" in raw
