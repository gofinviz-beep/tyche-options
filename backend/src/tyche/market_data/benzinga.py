"""Benzinga (via Massive/Polygon) client — Corporate Guidance.

Massive resells Benzinga's structured datasets under the Polygon API host:
``GET /benzinga/v1/guidance`` returns company-issued forward guidance (EPS and
revenue projections, with prior values when available). This is the highest-
conviction demand-catalyst signal — management raising/cutting its own outlook —
and maps onto the ``guidance_raise`` / ``guidance_cut`` catalyst taxonomy with a
magnitude derived from the change vs. prior guidance.

Uses the existing Polygon/Massive API key + base URL (``apiKey`` query param).
Degrades gracefully: an unavailable endpoint (no Benzinga subscription) yields
``[]`` so ingestion never breaks.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()


class BenzingaAPIError(Exception):
    """Raised on a non-retryable Benzinga/Massive API error."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"Benzinga API error {status_code}: {message}")


class BenzingaClient:
    """Async client for Benzinga datasets served on the Massive/Polygon host."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.polygon.io",
        rate_limit_rpm: int = 60,
        timeout: float = 20.0,
        max_retries: int = 3,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._min_interval = 60.0 / max(rate_limit_rpm, 1)
        self._last_request_time = 0.0
        self._lock = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()

    async def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        params = {**params, "apiKey": self._api_key}
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url, params=params)
                if resp.status_code == 429:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    raise BenzingaAPIError(resp.status_code, resp.text[:200])
                return resp.json()
            except BenzingaAPIError:
                raise
            except Exception as exc:  # noqa: BLE001 - retried
                last_exc = exc
                await asyncio.sleep(2 ** attempt)
        raise BenzingaAPIError(0, f"Request failed after {self._max_retries} retries: {last_exc}")

    @staticmethod
    def _num(value: object) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    async def get_corporate_guidance(
        self,
        ticker: str,
        limit: int = 250,
    ) -> list[dict]:
        """Fetch corporate guidance records (newest first) for a ticker.

        Mirrors the documented ``/benzinga/v1/guidance`` schema. Current
        guidance carries a midpoint plus a ``min``/``max`` range; the prior
        guidance for the same period is provided as ``previous_min``/
        ``previous_max`` ranges. Degrades to ``[]`` on any error / no
        subscription.
        """
        params = {
            "ticker": ticker.upper(),
            "sort": "date.desc",
            "limit": min(max(limit, 1), 50000),
        }
        try:
            data = await self._request("/benzinga/v1/guidance", params)
        except BenzingaAPIError as exc:
            logger.warning("benzinga_guidance_unavailable", ticker=ticker, error=str(exc))
            return []

        results = data.get("results") or []
        if not isinstance(results, list):
            return []

        num = self._num
        rows: list[dict] = []
        for item in results:
            g_date = item.get("date")
            if not g_date:
                continue
            rows.append(
                {
                    "benzinga_id": str(item.get("benzinga_id", "")),
                    "date": str(g_date).split("T")[0].split(" ")[0],
                    "time": item.get("time", ""),
                    "fiscal_period": item.get("fiscal_period", "") or "",
                    "fiscal_year": item.get("fiscal_year"),
                    "positioning": (item.get("positioning") or "").lower(),
                    "release_type": (item.get("release_type") or "").lower(),
                    "importance": item.get("importance"),
                    "currency": item.get("currency", ""),
                    "estimated_eps_guidance": num(item.get("estimated_eps_guidance")),
                    "min_eps_guidance": num(item.get("min_eps_guidance")),
                    "max_eps_guidance": num(item.get("max_eps_guidance")),
                    "estimated_revenue_guidance": num(item.get("estimated_revenue_guidance")),
                    "min_revenue_guidance": num(item.get("min_revenue_guidance")),
                    "max_revenue_guidance": num(item.get("max_revenue_guidance")),
                    "previous_min_eps_guidance": num(item.get("previous_min_eps_guidance")),
                    "previous_max_eps_guidance": num(item.get("previous_max_eps_guidance")),
                    "previous_min_revenue_guidance": num(item.get("previous_min_revenue_guidance")),
                    "previous_max_revenue_guidance": num(item.get("previous_max_revenue_guidance")),
                }
            )
        logger.debug("benzinga_guidance_fetched", ticker=ticker, records=len(rows))
        return rows


# Direction threshold: ignore guidance changes within ±0.5%.
_GUIDANCE_NOISE = 0.005
# A same-period guidance *revision* of ±10% saturates impact at 1.0 (a tight,
# unambiguous catalyst — management moved an existing number).
_REVISION_FULL_PCT = 0.10
# A *year-over-year* guidance change of ±30% saturates impact at 1.0. YoY uses
# a wider scale so ordinary single-digit growth registers as a mild signal,
# while a true demand ramp (e.g. NVDA +89% YoY) saturates.
_YOY_FULL_PCT = 0.30


def _midpoint(lo: float | None, hi: float | None, mid: float | None = None) -> float | None:
    """Midpoint of a guidance range, preferring an explicit midpoint."""
    if mid is not None:
        return mid
    vals = [v for v in (lo, hi) if v is not None]
    return sum(vals) / len(vals) if vals else None


def _pct_verdict(now: float | None, prev: float | None, full_pct: float) -> tuple[str, float] | None:
    """Classify a percentage change into a guidance catalyst + impact."""
    if now is None or prev is None or prev == 0:
        return None
    pct = (now - prev) / abs(prev)
    if abs(pct) < _GUIDANCE_NOISE:
        return None
    catalyst = "guidance_raise" if pct > 0 else "guidance_cut"
    impact = min(1.0, max(0.1, abs(pct) / full_pct))
    return catalyst, impact


def _rev_midpoint(r: dict) -> float | None:
    return _midpoint(
        r.get("min_revenue_guidance"),
        r.get("max_revenue_guidance"),
        r.get("estimated_revenue_guidance"),
    )


def _eps_midpoint(r: dict) -> float | None:
    return _midpoint(
        r.get("min_eps_guidance"),
        r.get("max_eps_guidance"),
        r.get("estimated_eps_guidance"),
    )


# Guide vs. consensus: a beat/miss of ±5% vs. analyst consensus saturates impact
# (analysts cluster tightly, so 5% is a strong forward-looking surprise).
_CONSENSUS_FULL_PCT = 0.05
# Max gap (days) between a guide's *true fiscal-quarter-end* and the matched
# Finnhub consensus period-end. Off-calendar fiscal quarters sit within ~1 month
# of the nearest calendar quarter-end (which is how Finnhub keys estimates), so
# 46d uniquely selects that quarter without bleeding into the adjacent one
# (~91d away).
_CONSENSUS_MAX_GAP_DAYS = 46

_QUARTER_NUM = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}


def _months_back(year: int, month: int, back: int) -> tuple[int, int]:
    """Roll ``(year, month)`` back ``back`` months; returns ``(year, month)``."""
    idx = year * 12 + (month - 1) - back
    return idx // 12, idx % 12 + 1


def _last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def fiscal_quarter_end(
    fiscal_year: int | None, fiscal_period: str, fye_month: int
) -> date | None:
    """True calendar end date of a fiscal quarter (month precision).

    Convention (matches Benzinga / issuer labels): ``FY{fiscal_year}`` ends on
    the last day of ``fye_month`` in calendar year ``fiscal_year`` — e.g.
    NVDA/WMT FYE = January → FY2027 ends Jan 2027, so Q2 FY2027 ends Jul 2026.
    Quarter *q* ends ``3*(4-q)`` months before the fiscal-year end.

    Returns ``None`` for full-year ("FY") or unrecognised periods — those don't
    map onto a single quarterly consensus estimate.
    """
    q = _QUARTER_NUM.get((fiscal_period or "").upper())
    if q is None or fiscal_year is None or not (1 <= fye_month <= 12):
        return None
    year, month = _months_back(fiscal_year, fye_month, 3 * (4 - q))
    return _last_day_of_month(year, month)


def _match_consensus(
    target_end: date | None,
    consensus_by_period: list[tuple[date, float | None, float | None]],
) -> tuple[float | None, float | None] | None:
    """Consensus (rev, eps) for the period-end nearest ``target_end``.

    Finnhub keys estimates by calendar quarter-end; ``target_end`` is the guide's
    true fiscal-quarter-end. We pick the closest consensus period within
    :data:`_CONSENSUS_MAX_GAP_DAYS` so an off-calendar fiscal quarter aligns to
    the right calendar consensus and a guide for a quarter we have no consensus
    for is skipped.
    """
    if target_end is None:
        return None
    best: tuple[float | None, float | None] | None = None
    best_gap = _CONSENSUS_MAX_GAP_DAYS + 1
    for period_end, rev_c, eps_c in consensus_by_period:
        gap = abs((period_end - target_end).days)
        if gap < best_gap:
            best_gap, best = gap, (rev_c, eps_c)
    return best if best_gap <= _CONSENSUS_MAX_GAP_DAYS else None


def _consensus_verdict(
    rev_now: float | None,
    eps_now: float | None,
    consensus: tuple[float | None, float | None],
) -> tuple[str, float] | None:
    """Guide midpoint vs. analyst consensus (revenue preferred, EPS fallback).

    Guide above consensus → ``guidance_raise`` (beat-and-raise); below →
    ``guidance_cut`` (disappointment).
    """
    rev_c, eps_c = consensus
    return _pct_verdict(rev_now, rev_c, _CONSENSUS_FULL_PCT) or _pct_verdict(
        eps_now, eps_c, _CONSENSUS_FULL_PCT
    )


def classify_guidance(record: dict) -> tuple[str, float] | None:
    """Same-period revision verdict for a single record (or ``None``).

    Compares the guidance midpoint to the *prior guidance for the same target
    period*, reconstructed from ``previous_min``/``previous_max`` (revenue
    preferred, EPS fallback). Secondary figures are skipped. This captures the
    cleanest catalyst — management revising an existing number. Cross-period
    (YoY) demand ramps are handled by :func:`derive_guidance_catalysts`.
    """
    if record.get("positioning") == "secondary":
        return None
    rev = _pct_verdict(
        _rev_midpoint(record),
        _midpoint(
            record.get("previous_min_revenue_guidance"),
            record.get("previous_max_revenue_guidance"),
        ),
        _REVISION_FULL_PCT,
    )
    if rev is not None:
        return rev
    return _pct_verdict(
        _eps_midpoint(record),
        _midpoint(
            record.get("previous_min_eps_guidance"),
            record.get("previous_max_eps_guidance"),
        ),
        _REVISION_FULL_PCT,
    )


def derive_guidance_catalysts(
    records: list[dict],
    consensus_by_period: list[tuple[date, float | None, float | None]] | None = None,
    fye_month: int | None = None,
) -> list[tuple[dict, str, float]]:
    """Turn a ticker's full guidance history into directional catalysts.

    Each Benzinga record is a forward guide for a target ``(fiscal_year,
    fiscal_period)``. For each record (oldest→newest, primary figures only) we
    pick the strongest available comparator, in priority order:

    0. **Guide vs. consensus** (when ``consensus_by_period`` *and* ``fye_month``
       are supplied) — the guide midpoint vs. the analyst consensus for the same
       fiscal quarter. The purest forward-looking surprise; tight ±5% impact
       scale. The guide's true fiscal-quarter-end is computed from ``fye_month``
       (see :func:`fiscal_quarter_end`) and matched to the nearest Finnhub
       consensus period — correct even for off-calendar fiscal years (NVDA/WMT
       FYE January, AAPL September, MU August).
    1. **Same-period revision** — Benzinga's ``previous_*`` range (management
       moved an already-issued number). Tight ±10% impact scale.
    2. **Year-over-year** — the guide for the *same fiscal period one year
       earlier* (seasonality-neutral demand trend). Wider ±30% scale.

    ``consensus_by_period`` is ``[(period_end_date, rev_consensus, eps_consensus)]``
    (built from Finnhub ``rev_est_avg`` / ``eps_est_avg``, keyed by calendar
    period-end). ``fye_month`` is the company's fiscal-year-end month (1–12);
    when ``None`` the consensus comparator is skipped (falls back to revision /
    YoY) — never a wrong-quarter match.

    Returns ``(record, catalyst, impact)`` tuples for records that produced a
    directional signal. Records lacking any comparator (e.g. the first guide
    for a period with no prior year and no consensus) are skipped — no false
    catalyst.
    """
    ordered = sorted(records, key=lambda r: str(r.get("date", "")))
    rev_by_period: dict[tuple[int, str], float] = {}
    eps_by_period: dict[tuple[int, str], float] = {}
    out: list[tuple[dict, str, float]] = []
    use_consensus = bool(consensus_by_period) and fye_month is not None

    for r in ordered:
        rev_now = _rev_midpoint(r)
        eps_now = _eps_midpoint(r)
        fy = r.get("fiscal_year")
        fp = r.get("fiscal_period") or ""

        if r.get("positioning") != "secondary":
            verdict = None
            if use_consensus:  # (0) guide vs. consensus (same fiscal quarter)
                target_end = fiscal_quarter_end(fy, fp, fye_month)
                consensus = _match_consensus(target_end, consensus_by_period)
                if consensus is not None:
                    verdict = _consensus_verdict(rev_now, eps_now, consensus)
            if verdict is None:  # (1) same-period revision
                verdict = classify_guidance(r)
            if verdict is None and fy is not None and fp:  # (2) YoY
                rev_yoy = rev_by_period.get((fy - 1, fp))
                eps_yoy = eps_by_period.get((fy - 1, fp))
                verdict = _pct_verdict(rev_now, rev_yoy, _YOY_FULL_PCT) or _pct_verdict(
                    eps_now, eps_yoy, _YOY_FULL_PCT
                )
            if verdict is not None:
                out.append((r, verdict[0], verdict[1]))

        # Record this period's midpoints for future YoY comparisons.
        if fy is not None and fp:
            if rev_now is not None:
                rev_by_period[(fy, fp)] = rev_now
            if eps_now is not None:
                eps_by_period[(fy, fp)] = eps_now

    return out
