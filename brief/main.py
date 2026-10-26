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
from brief.formatter import _is_past_period, format_brief
from brief.models import Brief
from brief.personalization import build_greeting
from brief.quotes import get_quote
from brief.sources.good_news import gather_good_news
from brief.sources.mobility import build_mobility
from brief.sources.news import gather_news
from brief.sources.trm import gather_trm
from brief.telegram import send_messages

log = logging.getLogger("brief")


def parse_as_of(value: str | None) -> int | None:
    """Convierte 'HH:MM' en la hora entera (0-23), o None si no se especificó."""
    if value is None:
        return None
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"--as-of inválido ({value!r}): usa el formato HH:MM")
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"--as-of fuera de rango ({value!r}): HH entre 00-23 y MM entre 00-59")
    return hh


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


def build_brief(cfg: Config, as_of_hour: int | None = None) -> Brief:
    """Reúne todas las secciones. Cada fuente fallida deja su sección en None."""
    now = datetime.now(cfg.tz)
    today = now.date()
    res = run_parallel(
        {
            "weather": lambda: gather_weather(cfg, now_hour=as_of_hour),
            "air": lambda: gather_air(cfg),
            "trm": lambda: gather_trm(cfg, today),
            "news": lambda: gather_news(cfg),
            "good_news": lambda: gather_good_news(cfg),
        }
    )
    weather, air, trm = res["weather"], res["air"], res["trm"]
    news = res["news"] or []
    rain = (
        max(
            (
                s.value
                for k, s in weather.rain_periods.items()
                if s.value is not None and not _is_past_period(k, weather.now_hour, weather.period_hours)
            ),
            default=None,
        )
        if weather
        else None
    )
    return Brief(
        name=cfg.name,
        today=today,
        greeting=build_greeting(cfg.name, today, rain),
        weather=weather,
        air=air,
        trm=trm,
        mobility=build_mobility(cfg, today),
        news=news,
        good_news=res["good_news"],
        quote=get_quote(cfg, today),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brief", description="Resumen matutino para Bogotá")
    parser.add_argument("--dry-run", action="store_true", help="imprime el mensaje sin enviar ni guardar")
    parser.add_argument("--no-wait", action="store_true", help="no espera hasta la hora de envío")
    parser.add_argument("--force", action="store_true", help="envía aunque ya se haya enviado hoy")
    parser.add_argument(
        "--as-of",
        default=None,
        metavar="HH:MM",
        help="simula la hora actual (hora Bogotá) para el cálculo de franjas 'ya pasó'",
    )
    parser.add_argument("--config", default=None, help="ruta alternativa a config.yaml")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    as_of_hour = parse_as_of(args.as_of)
    cfg = load_config(args.config)
    today = datetime.now(cfg.tz).date()

    if not args.dry_run and not args.force and history.already_sent_today(today):
        log.info("Ya se envió el resumen de hoy (%s); no se repite.", today)
        return 0

    if not args.dry_run and not (cfg.secrets.telegram_token and cfg.secrets.telegram_chat_id):
        log.error("Faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID")
        return 2

    brief = build_brief(cfg) if as_of_hour is None else build_brief(cfg, as_of_hour)
    if as_of_hour is not None and brief.weather is not None:
        brief.weather.now_hour = as_of_hour
    messages = format_brief(brief, cfg.section("alerts"))

    if args.dry_run:
        for i, msg in enumerate(messages, 1):
            print(f"----- mensaje {i}/{len(messages)} ({len(msg)} caracteres) -----")
            print(msg)
        return 0

    if not args.no_wait:
        wait_until_send_time(cfg)
    send_messages(cfg.secrets.telegram_token, cfg.secrets.telegram_chat_id, messages)  # type: ignore[arg-type]

    # El envío ya ocurrió: lo primero es registrarlo para que nunca se duplique.
    row = history.build_row(brief, sent=True)
    try:
        history.append_row(row)
    except Exception:
        log.exception(
            "ERROR GRAVE: el mensaje se envió pero NO se pudo registrar en history.csv; "
            "un reintento duplicaría el envío"
        )
        return 3
    try:
        history.write_latest(brief, row)
        history.write_history_json()
    except Exception:
        log.exception("El mensaje se envió y history.csv se guardó, pero falló latest.json/history.json del dashboard")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
