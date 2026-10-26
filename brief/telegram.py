"""Envío por Telegram Bot API."""

from __future__ import annotations

import logging

from brief.http import make_session

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/sendMessage"


def send_messages(token: str, chat_id: str, messages: list[str]) -> None:
    """Envía cada mensaje con parse_mode HTML. Lanza excepción si Telegram rechaza alguno.

    El token nunca se registra en logs.
    """
    session = make_session()
    for text in messages:
        resp = session.post(
            API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if not resp.ok:
            raise RuntimeError(f"Telegram respondió {resp.status_code}: {resp.text[:300]}")
    log.info("Enviados %d mensaje(s) a Telegram", len(messages))
