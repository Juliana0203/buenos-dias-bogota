"""Cliente HTTP con timeout y reintentos con backoff exponencial."""

from __future__ import annotations

import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; buenos-dias-bogota/1.0)"


def make_session(retries: int = 3, backoff: float = 1.0) -> requests.Session:
    """Crea una sesión con reintentos ante errores de red y códigos 429/5xx."""
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> Any:
    """GET que devuelve JSON; lanza excepción si falla tras los reintentos."""
    sess = session or make_session()
    resp = sess.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()
