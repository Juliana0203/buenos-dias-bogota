"""TRM oficial (Superintendencia Financiera, datos.gov.co) con respaldo USD/COP.

El valor mostrado es la TRM oficial. La segunda fuente (tasa de mercado de un API
de cambio) solo sirve para verificar que el dato oficial es coherente.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any

import holidays

from brief.config import Config
from brief.consensus import agreement_score, run_parallel
from brief.http import get_json
from brief.models import CONF_HIGH, CONF_MID, TrmSummary

log = logging.getLogger(__name__)

SODA_URL = "https://www.datos.gov.co/resource/{dataset}.json"
OFFICIAL = "TRM Superfinanciera"
BACKUP = "open.er-api.com"
HISTORY_DAYS = 45

TrmRow = tuple[date, date, float]


def is_business_day(d: date) -> bool:
    """True si la fecha es día hábil en Colombia (lunes a viernes no festivo)."""
    return d.weekday() < 5 and d not in holidays.country_holidays("CO", years=d.year)


def parse_rows(rows: list[dict[str, Any]]) -> list[TrmRow]:
    """Convierte filas SODA en [(vigenciadesde, vigenciahasta, valor)] únicas y ordenadas (más reciente primero)."""
    seen: dict[date, TrmRow] = {}
    for row in rows:
        try:
            d_from = date.fromisoformat(row["vigenciadesde"][:10])
            raw_until = row.get("vigenciahasta") or row["vigenciadesde"]
            d_until = date.fromisoformat(raw_until[:10])
            seen[d_from] = (d_from, max(d_from, d_until), float(row["valor"]))
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(seen.values(), key=lambda r: r[0], reverse=True)


def _normalize_history(history: Iterable[Any]) -> list[TrmRow]:
    """Acepta filas como (desde, hasta, valor), (desde, valor) o diccionarios SODA."""
    items = list(history)
    if not items:
        return []
    if isinstance(items[0], dict):
        return parse_rows(items)
    seen: dict[date, TrmRow] = {}
    for item in items:
        if len(item) == 3:
            d_from, d_until, val = item
            seen[d_from] = (d_from, max(d_from, d_until), float(val))
        else:
            d_from, val = item
            seen[d_from] = (d_from, d_from, float(val))
    return sorted(seen.values(), key=lambda r: r[0], reverse=True)


def fetch_official(cfg: Config, today: date) -> list[TrmRow]:
    """Descarga ~45 días de TRM del dataset oficial."""
    start = today - timedelta(days=HISTORY_DAYS)
    rows = get_json(
        SODA_URL.format(dataset=cfg.section("trm")["soda_dataset"]),
        {
            "$where": f"vigenciadesde >= '{start.isoformat()}T00:00:00' "
            f"AND vigenciadesde <= '{today.isoformat()}T23:59:59'",
            "$order": "vigenciadesde DESC",
            "$limit": 100,
        },
    )
    return parse_rows(rows)


def fetch_backup(cfg: Config) -> float | None:
    """Tasa USD/COP de mercado desde el API de respaldo."""
    data = get_json(cfg.section("trm")["backup_url"])
    rate = data.get("rates", {}).get("COP")
    return float(rate) if rate else None


def compute_trm(
    history: Iterable[Any],
    today: date,
    backup: float | None,
    thresholds: Mapping[str, float],
    conf_params: Mapping[str, float] | None = None,
) -> TrmSummary | None:
    """Calcula variaciones y alertas de mínimo/máximo a partir del historial."""
    rows = [r for r in _normalize_history(history) if r[0] <= today]
    if not rows:
        return None
    covering = [r for r in rows if r[0] <= today <= r[1]]
    valid_from, valid_until, value = covering[0] if covering else rows[0]
    prev_candidates = [v for d_from, _, v in rows if d_from < valid_from and v != value]
    previous = prev_candidates[0] if prev_candidates else None
    weekend_or_holiday = not is_business_day(today)
    change = (value - previous) if (previous is not None and not weekend_or_holiday) else None

    days = int(thresholds.get("trm_alert_days", 30))
    window = [v for d_from, _, v in rows if d_from >= today - timedelta(days=days)]
    avg = statistics.fmean(window) if window else None
    enough = len(window) >= 5

    sources = [OFFICIAL]
    confidence = CONF_MID
    gap = 0.0
    if backup is not None:
        sources.append(BACKUP)
        gap = abs(backup - value) / value * 100
        if gap <= float(thresholds.get("trm_pct", 1.5)):
            confidence = CONF_HIGH
        else:
            log.warning("TRM oficial (%.2f) y respaldo (%.2f) difieren %.1f%%", value, backup, gap)
    score = agreement_score(gap, float(thresholds.get("trm_pct", 1.5)), len(sources), 2, conf_params)
    return TrmSummary(
        value=value,
        valid_from=valid_from.isoformat(),
        previous=previous,
        change_abs=change,
        change_pct=(change / previous * 100) if change is not None and previous else None,
        avg30=avg,
        vs_avg30_pct=((value - avg) / avg * 100) if avg else None,
        is_min30=enough and value <= min(window),
        is_max30=enough and value >= max(window),
        backup_value=backup,
        sources=sources,
        confidence=confidence,
        score=score,
        valid_until=valid_until.isoformat(),
        is_weekend_or_holiday=weekend_or_holiday,
    )


def gather_trm(cfg: Config, today: date) -> TrmSummary | None:
    """Consulta la TRM oficial y el respaldo en paralelo. Sin dato oficial, devuelve None."""
    res = run_parallel({"official": lambda: fetch_official(cfg, today), "backup": lambda: fetch_backup(cfg)})
    if not res.get("official"):
        return None
    return compute_trm(res["official"], today, res.get("backup"), cfg.section("thresholds"), cfg.section("confidence"))
