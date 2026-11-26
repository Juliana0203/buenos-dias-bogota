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


def send_quiz_poll(
    token: str,
    chat_id: str,
    question: str,
    options: list[str],
    correct_option_id: int,
    explanation: str,
    disable_notification: bool = False,
) -> None:
    """Envía un mini quiz interactivo con sendPoll (type='quiz')."""
    session = make_session()
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "question": question[:300],
        "options": [o[:100] for o in options[:10]],
        "is_anonymous": False,
        "type": "quiz",
        "correct_option_id": correct_option_id,
        "explanation": explanation[:200],
        "explanation_parse_mode": "HTML",
        "disable_notification": disable_notification,
    }
    resp = session.post(POLL_API.format(token=token), json=payload, timeout=20)
    if not resp.ok:
        raise RuntimeError(f"Telegram sendPoll respondió {resp.status_code}: {resp.text[:300]}")
    log.info("Enviado mini quiz a Telegram")


def fetch_updates(token: str, offset: int | None = None) -> list[dict[str, Any]]:
    """Lee actualizaciones pendientes (callback_query) desde el offset dado."""
    session = make_session()
    params: dict[str, Any] = {"timeout": 1, "allowed_updates": '["callback_query"]'}
    if offset is not None:
        params["offset"] = offset
    resp = session.get(UPDATES_API.format(token=token), params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return list(data.get("result") or [])


def answer_callback_query(token: str, callback_query_id: str, text: str) -> None:
    """Confirma el clic en un botón inline de Telegram (no lanza excepción si ya expiró)."""
    session = make_session()
    try:
        session.post(
            ANSWER_CB_API.format(token=token),
            json={"callback_query_id": callback_query_id, "text": text[:200]},
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo responder answerCallbackQuery %s: %s", callback_query_id, exc)
