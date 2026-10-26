"""Orquestador: recopila datos, arma el mensaje, lo envía y guarda el histórico."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta

from brief import history
from brief.config import Config, load_config
from brief.consensus import gather_air, gather_weather, run_parallel
from brief.formatter import format_brief
from brief.models import Brief
from brief.personalization import build_greeting
from brief.quotes import get_quote
from brief.sources.mobility import build_mobility
from brief.sources.news import gather_news, select_top
from brief.sources.trm import gather_trm
from brief.telegram import send_messages

log = logging.getLogger("brief")


def wait_until_send_time(cfg: Config, now: datetime | None = None) -> float:
    """Duerme hasta la hora de envío si faltan <= max_wait_minutes; devuelve segundos dormidos."""
    sched = cfg.section("schedule")
    now = now or datetime.now(cfg.tz)
    hh, mm = (int(x) for x in str(sched.get("send_time", "08:00")).split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    remaining = (target - now).total_seconds()
    if remaining <= 0:
        return 0.0
    if remaining > timedelta(minutes=int(sched.get("max_wait_minutes", 25))).total_seconds():
        log.warning(
            "Faltan %.0f min para la hora de envío (más del máximo de espera); envío ya",
            remaining / 60,
        )
        return 0.0
    log.info("Esperando %.0f s hasta las %s", remaining, sched.get("send_time"))
    time.sleep(remaining)
    return remaining


def build_brief(cfg: Config) -> Brief:
    """Reúne todas las secciones. Cada fuente fallida deja su sección en None."""
    now = datetime.now(cfg.tz)
    today = now.date()
    res = run_parallel(
        {
            "weather": lambda: gather_weather(cfg),
            "air": lambda: gather_air(cfg),
            "trm": lambda: gather_trm(cfg, today),
            "news": lambda: gather_news(cfg),
        }
    )
    weather, air, trm = res["weather"], res["air"], res["trm"]
    all_news = res["news"] or []
    news_cfg = cfg.section("news")
    mobility = build_mobility(cfg, all_news, today)
    alert_links = {a.link for a in mobility.alerts}
    top = select_top(
        [n for n in all_news if n.link not in alert_links],
        int(news_cfg.get("colombia_count", 3)),
        int(news_cfg.get("bogota_count", 2)),
    )
    rain = (
        max((s.value for s in weather.rain_periods.values() if s.value is not None), default=None) if weather else None
    )
    return Brief(
        name=cfg.name,
        today=today,
        greeting=build_greeting(cfg.name, today, rain),
        weather=weather,
        air=air,
        trm=trm,
        mobility=mobility,
        news=top,
        quote=get_quote(cfg, today),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brief", description="Resumen matutino para Bogotá")
    parser.add_argument("--dry-run", action="store_true", help="imprime el mensaje sin enviar ni guardar")
    parser.add_argument("--no-wait", action="store_true", help="no espera hasta la hora de envío")
    parser.add_argument("--force", action="store_true", help="envía aunque ya se haya enviado hoy")
    parser.add_argument("--config", default=None, help="ruta alternativa a config.yaml")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    cfg = load_config(args.config)
    today = datetime.now(cfg.tz).date()

    if not args.dry_run and not args.force and history.already_sent_today(today):
        log.info("Ya se envió el resumen de hoy (%s); no se repite.", today)
        return 0

    if not args.dry_run and not (cfg.secrets.telegram_token and cfg.secrets.telegram_chat_id):
        log.error("Faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID")
        return 2

    brief = build_brief(cfg)
    messages = format_brief(brief, cfg.section("alerts"))

    if args.dry_run:
        for i, msg in enumerate(messages, 1):
            print(f"----- mensaje {i}/{len(messages)} ({len(msg)} caracteres) -----")
            print(msg)
        return 0

    if not args.no_wait:
        wait_until_send_time(cfg)
    send_messages(cfg.secrets.telegram_token, cfg.secrets.telegram_chat_id, messages)  # type: ignore[arg-type]

    row = history.build_row(brief, sent=True)
    history.append_row(row)
    history.write_latest(brief, row)
    history.write_history_json()
    return 0


if __name__ == "__main__":
    sys.exit(main())
