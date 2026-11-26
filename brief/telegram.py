"""Envío por Telegram Bot API (mensajes con o sin sonido, teclados inline, encuestas y callbacks)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from brief.http import make_session

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/sendMessage"
POLL_API = "https://api.telegram.org/bot{token}/sendPoll"
UPDATES_API = "https://api.telegram.org/bot{token}/getUpdates"
ANSWER_CB_API = "https://api.telegram.org/bot{token}/answerCallbackQuery"
EDIT_MARKUP_API = "https://api.telegram.org/bot{token}/editMessageReplyMarkup"


def send_messages(
    token: str,
    chat_id: str,
    messages: list[str],
    disable_notification: bool | Sequence[bool] = False,
    reply_markups: list[dict[str, Any] | None] | None = None,
) -> None:
    """Envía cada mensaje con parse_mode HTML. Lanza excepción si Telegram rechaza alguno.

    ``disable_notification`` puede ser un booleano global o una secuencia por mensaje
    (p. ej. [False, True, True] para que solo el primer mensaje suene).
    El token nunca se registra en logs.
    """
    session = make_session()
    for idx, text in enumerate(messages):
        if isinstance(disable_notification, Sequence) and not isinstance(disable_notification, (str, bytes)):
            silent = bool(disable_notification[idx]) if idx < len(disable_notification) else True
        else:
            silent = bool(disable_notification)
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if silent:
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
    log.info("Enviados %d mensaje(s) a Telegram", len(messages))


def send_quiz_poll(
    token: str,
    chat_id: str,
    question: str,
    options: list[str],
    correct_option_id: int,
    explanation: str,
    disable_notification: bool = True,
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


def answer_callback_query(token: str, callback_query_id: str, text: str) -> bool:
    """Confirma el clic en un botón inline de Telegram.

    Captura consultas vencidas (HTTP 400 'query is too old') o errores de red sin romper el programa.
    """
    session = make_session(retries=1)
    try:
        resp = session.post(
            ANSWER_CB_API.format(token=token),
            json={"callback_query_id": callback_query_id, "text": text[:200]},
            timeout=10,
        )
        if not resp.ok:
            log.info(
                "answerCallbackQuery %s omitido (posible consulta vencida, HTTP %s): %s",
                callback_query_id,
                resp.status_code,
                resp.text[:160],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo responder answerCallbackQuery %s: %s", callback_query_id, exc)
        return False


def edit_message_reply_markup(
    token: str,
    chat_id: str | int,
    message_id: int,
    reply_markup: dict[str, Any],
) -> bool:
    """Actualiza los botones inline del mensaje original (p. ej. para mostrar '✅ Guardado')."""
    session = make_session(retries=1)
    try:
        resp = session.post(
            EDIT_MARKUP_API.format(token=token),
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": reply_markup,
            },
            timeout=10,
        )
        if not resp.ok:
            log.info(
                "editMessageReplyMarkup omitido (chat=%s, msg=%s, HTTP %s): %s",
                chat_id,
                message_id,
                resp.status_code,
                resp.text[:160],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo actualizar teclado del mensaje %s: %s", message_id, exc)
        return False
