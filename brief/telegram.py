"""Envío por Telegram Bot API (mensajes con o sin sonido, botones inline, encuestas y callbacks)."""

from __future__ import annotations

import logging
from typing import Any

from brief.http import make_session

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/sendMessage"
POLL_API = "https://api.telegram.org/bot{token}/sendPoll"
UPDATES_API = "https://api.telegram.org/bot{token}/getUpdates"
ANSWER_CB_API = "https://api.telegram.org/bot{token}/answerCallbackQuery"


def send_messages(
    token: str,
    chat_id: str,
    messages: list[str],
    disable_notification: bool = False,
    reply_markups: list[dict[str, Any] | None] | None = None,
) -> None:
    """Envía cada mensaje con parse_mode HTML. Lanza excepción si Telegram rechaza alguno.

    El token nunca se registra en logs.
    """
    session = make_session()
    for idx, text in enumerate(messages):
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if disable_notification:
            payload["disable_notification"] = True
        if reply_markups and idx < len(reply_markups) and reply_markups[idx]:
            payload["reply_markup"] = reply_markups[idx]
        resp = session.post(
            API.format(token=token),
            json=payload,
            timeout=20,
        )
        if not resp.ok:
            raise RuntimeError(f"Telegram respondió {resp.status_code}: {resp.text[:300]}")
    log.info(
        "Enviados %d mensaje(s) a Telegram (silencioso=%s)",
        len(messages),
        disable_notification,
    )
