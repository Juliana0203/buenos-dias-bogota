"""TRM oficial (Superintendencia Financiera, datos.gov.co) con respaldo USD/COP.

El valor mostrado es la TRM oficial. La segunda fuente (tasa de mercado de un API
de cambio) solo sirve para verificar que el dato oficial es coherente.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from brief.config import Config
from brief.consensus import run_parallel
from brief.http import get_json
from brief.models import CONF_HIGH, CONF_MID, TrmSummary

log = logging.getLogger(__name__)

SODA_URL = "https://www.datos.gov.co/resource/{dataset}.json"
OFFICIAL = "TRM Superfinanciera"
BACKUP = "open.er-api.com"
HISTORY_DAYS = 45


def parse_rows(rows: list[dict[str, Any]]) -> list[tuple[date, float]]:
    """Convierte filas SODA en [(vigencia_desde, valor)] únicas y ordenadas (más reciente primero)."""
    seen: dict[date, float] = {}
    for row in rows:
        try:
            seen[date.fromisoformat(row["vigenciadesde"][:10])] = float(row["valor"])
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(seen.items(), key=lambda kv: kv[0], reverse=True)


def fetch_official(cfg: Config, today: date) -> list[tuple[date, float]]:
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
    history: list[tuple[date, float]],
    today: date,
    backup: float | None,
    thresholds: Mapping[str, float],
) -> TrmSummary | None:
    """Calcula variaciones y alertas de mínimo/máximo a partir del historial."""
    rows = [(d, v) for d, v in history if d <= today]
    if not rows:
        return None
    rows.sort(key=lambda kv: kv[0], reverse=True)
    valid_from, value = rows[0]
    previous = rows[1][1] if len(rows) > 1 else None
    days = int(thresholds.get("trm_alert_days", 30))
    window = [v for d, v in rows if d >= today - timedelta(days=days)]
    avg = statistics.fmean(window) if window else None
    enough = len(window) >= 5
    change = value - previous if previous is not None else None

    sources = [OFFICIAL]
    confidence = CONF_MID
    if backup is not None:
        sources.append(BACKUP)
        gap = abs(backup - value) / value * 100
        if gap <= float(thresholds.get("trm_pct", 1.5)):
            confidence = CONF_HIGH
        else:
            log.warning("TRM oficial (%.2f) y respaldo (%.2f) difieren %.1f%%", value, backup, gap)
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
    )


def gather_trm(cfg: Config, today: date) -> TrmSummary | None:
    """Consulta la TRM oficial y el respaldo en paralelo. Sin dato oficial, devuelve None."""
    res = run_parallel({"official": lambda: fetch_official(cfg, today), "backup": lambda: fetch_backup(cfg)})
    if not res.get("official"):
        return None
    return compute_trm(res["official"], today, res.get("backup"), cfg.section("thresholds"))
