"""A-stock loader: primary A-share OHLCV via mootdx TCP + Tencent fundamentals.

Combines two free, no-auth sources:
  - mootdx TCP for historical OHLCV (daily/intraday K-lines)
  - Tencent Finance for real-time fundamentals (PE/PB/market cap)

No API key required, no IP blocking. Registered as ``astock`` in the loader
registry and used as the primary source in the ``a_share`` fallback chain.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd

from backtest.loaders.base import validate_date_range
from backtest.loaders.registry import register

logger = logging.getLogger(__name__)

# Mootdx frequency codes (same as mootdx_loader.py).
_INTRADAY_FREQ: dict[str, int] = {
    "1m": 8,
    "5m": 0,
    "15m": 1,
    "30m": 2,
    "1H": 3,
}
_DAILY_FREQ: dict[str, int] = {
    "1D": 4,
    "1W": 5,
    "1M": 6,
}

_BARS_PAGE = 800
_MAX_PAGES = 25  # 25 × 800 = 20 000 bars (~10y daily, ~5y 1H, ~3mo 1m)


def _is_a_share(code: str) -> bool:
    """Accept either explicit `.SH/.SZ/.BJ` suffix or bare 6-digit ticker."""
    upper = code.upper()
    if upper.endswith((".SH", ".SZ", ".BJ")):
        return True
    return len(code) == 6 and code.isdigit()


def _is_bj(code: str) -> bool:
    """Detect 北交所 symbols (mootdx does not serve BJ data)."""
    upper = code.upper()
    if upper.endswith(".BJ"):
        return True
    return len(code) == 6 and code.isdigit() and code[0] in ("4", "8")


@register
class DataLoader:
    """Primary A-share OHLCV loader: mootdx TCP + optional Tencent fundamentals."""

    name = "astock"
    markets = {"a_share"}
    requires_auth = False

    def __init__(self) -> None:
        self._client = None

    def is_available(self) -> bool:
        """Available if mootdx is installed (requests is always available)."""
        try:
            import mootdx  # noqa: F401
            return True
        except ImportError:
            return False

    def _get_client(self):
        if self._client is None:
            from mootdx.quotes import Quotes
            self._client = Quotes.factory(market="std")
        return self._client

    def fetch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Fetch A-share OHLCV via mootdx TCP.

        Args:
            codes: Symbol list. `.SH/.SZ/.BJ` suffix or bare 6-digit tickers.
            start_date: YYYY-MM-DD.
            end_date: YYYY-MM-DD.
            interval: ``1m / 5m / 15m / 30m / 1H / 1D / 1W / 1M``.
            fields: Optional. If contains fundamental keys (pe, pb, market_cap),
                    enriches the latest bar with Tencent Finance data.

        Returns:
            Mapping symbol -> OHLCV DataFrame.
        """
        validate_date_range(start_date, end_date)
        if interval not in _DAILY_FREQ and interval not in _INTRADAY_FREQ:
            raise ValueError(
                f"Unsupported interval for astock: {interval!r}. "
                f"Supported: {sorted(_DAILY_FREQ) + sorted(_INTRADAY_FREQ)}"
            )

        result: Dict[str, pd.DataFrame] = {}
        for code in codes:
            if not _is_a_share(code):
                logger.debug("astock: skipping non-A-share symbol %s", code)
                continue
            if _is_bj(code):
                logger.warning(
                    "astock: 北交所 (%s) not supported by mootdx; use akshare/tushare",
                    code,
                )
                continue
            try:
                df = self._fetch_one(code, start_date, end_date, interval)
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as exc:
                logger.warning("astock failed for %s: %s", code, exc)

        # Optional: enrich with Tencent fundamentals (current snapshot only)
        if fields and result:
            self._enrich_fundamentals(result, fields)

        return result

    def _fetch_one(
        self, code: str, start_date: str, end_date: str, interval: str,
    ) -> Optional[pd.DataFrame]:
        symbol = code.split(".")[0]
        client = self._get_client()

        if interval == "1D":
            df = client.get_k_data(code=symbol, start_date=start_date, end_date=end_date)
            return self._normalize_daily(df)

        freq = _DAILY_FREQ.get(interval) or _INTRADAY_FREQ[interval]
        return self._fetch_bars_paginated(client, symbol, freq, start_date, end_date)

    @staticmethod
    def _fetch_bars_paginated(
        client, symbol: str, freq: int, start_date: str, end_date: str,
    ) -> Optional[pd.DataFrame]:
        start_ts = pd.Timestamp(start_date)
        chunks: list[pd.DataFrame] = []
        for page in range(_MAX_PAGES):
            df = client.bars(
                symbol=symbol,
                frequency=freq,
                start=page * _BARS_PAGE,
                offset=_BARS_PAGE,
            )
            if df is None or df.empty:
                break
            chunks.append(df)
            first_dt = pd.to_datetime(df["datetime"].iloc[0])
            if first_dt <= start_ts:
                break
        else:
            logger.warning(
                "astock: %s %s pagination hit cap (%d pages) without reaching %s",
                symbol, freq, _MAX_PAGES, start_date,
            )
        if not chunks:
            return None
        combined = pd.concat(chunks, ignore_index=False)
        return DataLoader._normalize_bars(combined, start_date, end_date)

    @staticmethod
    def _normalize_daily(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        if df is None or df.empty:
            return None
        out = df.rename(columns={"vol": "volume"}).copy()
        out.index = pd.to_datetime(out.index)
        out.index.name = "trade_date"
        for col in ("open", "high", "low", "close", "volume"):
            out[col] = pd.to_numeric(out[col], errors="coerce")
        out = out[["open", "high", "low", "close", "volume"]].dropna(
            subset=["open", "high", "low", "close"]
        )
        return out.sort_index() if not out.empty else None

    @staticmethod
    def _normalize_bars(
        df: Optional[pd.DataFrame], start_date: str, end_date: str,
    ) -> Optional[pd.DataFrame]:
        if df is None or df.empty:
            return None
        out = df.copy()
        if "datetime" in out.columns:
            out["trade_date"] = pd.to_datetime(out["datetime"])
            out = out.set_index("trade_date")
        else:
            out.index = pd.to_datetime(out.index)
            out.index.name = "trade_date"
        out = out.sort_index()
        for col in ("open", "high", "low", "close", "volume"):
            out[col] = pd.to_numeric(out[col], errors="coerce")
        out = out[["open", "high", "low", "close", "volume"]].dropna(
            subset=["open", "high", "low", "close"]
        )
        end_ts = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        out = out.loc[pd.Timestamp(start_date):end_ts]
        return out if not out.empty else None

    @staticmethod
    def _enrich_fundamentals(
        result: Dict[str, pd.DataFrame], fields: List[str],
    ) -> None:
        """Enrich the last bar of each DataFrame with current Tencent fundamentals.

        Tencent provides only the current snapshot, not historical time series.
        """
        fund_fields = {"pe", "pb", "market_cap", "turnover_rate", "pe_ttm", "mcap_yi"}
        if not any(f in fund_fields for f in fields):
            return

        try:
            from src.api.astock_helpers import tencent_quote
        except ImportError:
            logger.debug("astock_helpers not available, skipping enrichment")
            return

        symbols = [c.split(".")[0] for c in result.keys()]
        quotes_df = tencent_quote(symbols)
        if quotes_df is None or quotes_df.empty:
            return

        code_map = quotes_df.set_index("code")
        for sym, df in result.items():
            bare = sym.split(".")[0]
            if bare not in code_map.index:
                continue
            row = code_map.loc[bare]
            enrich = {}
            if "pe" in fields or "pe_ttm" in fields:
                enrich["pe"] = float(row["pe_ttm"]) if pd.notna(row.get("pe_ttm")) else None
            if "pb" in fields:
                enrich["pb"] = float(row["pb"]) if pd.notna(row.get("pb")) else None
            if "market_cap" in fields or "mcap_yi" in fields:
                enrich["market_cap"] = float(row["mcap_yi"]) if pd.notna(row.get("mcap_yi")) else None
            if "turnover_rate" in fields:
                enrich["turnover_rate"] = float(row["turnover_pct"]) if pd.notna(row.get("turnover_pct")) else None

            if enrich:
                for col, val in enrich.items():
                    if col not in df.columns:
                        df[col] = None
                    df.iloc[-1, df.columns.get_loc(col)] = val
