"""Stock Screener HTTP routes for the Web UI.

Mounted by ``agent/api_server.py`` via ``register_screener_routes(app)``.

Routes:

- ``GET /screener/ashare`` — filter A-share stocks by fundamental metrics

Data source priority (by data completeness and stability):
  1. Tencent Finance — full fundamentals (PE/PB/市值/换手率) via HTTP, no IP ban
  2. mootdx (通达信) — quotes + name via TCP (no PE/PB/市值, proxy-proof)
  3. AKShare sina — quotes + name via HTTP (no PE/PB/市值, last resort)
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Snapshot cache (in-memory, 5-minute TTL)
# ---------------------------------------------------------------------------

_snapshot_cache: tuple[float, pd.DataFrame] | None = None
_CACHE_TTL = 300  # seconds


def _get_snapshot() -> pd.DataFrame:
    """Fetch A-share market snapshot with 5-min cache.

    Tries data sources in order: Tencent Finance → mootdx → AKShare sina.
    """
    global _snapshot_cache
    now = time.time()
    if _snapshot_cache is not None and (now - _snapshot_cache[0]) < _CACHE_TTL:
        return _snapshot_cache[1]

    errors: list[str] = []

    # --- Source 1: Tencent Finance (full fundamentals, no IP ban) ---
    try:
        df = _fetch_tencent_snapshot()
        if df is not None and len(df) > 100:
            logger.info("Screener using Tencent Finance snapshot (%d stocks)", len(df))
            _snapshot_cache = (now, df)
            return df
    except Exception as e:
        errors.append(f"tencent: {e}")
        logger.warning("Tencent Finance snapshot failed: %s", e)

    # --- Source 2: mootdx (通达信 TCP, bypasses HTTP proxy issues) ---
    try:
        df = _fetch_mootdx_snapshot()
        if df is not None and len(df) > 100:
            logger.info("Screener using mootdx snapshot (%d stocks)", len(df))
            _snapshot_cache = (now, df)
            return df
    except Exception as e:
        errors.append(f"mootdx: {e}")
        logger.warning("mootdx snapshot failed: %s", e)

    # --- Source 3: AKShare sina (quotes only, no PE/PB/市值) ---
    try:
        df = _fetch_akshare_sina_snapshot()
        if df is not None and len(df) > 100:
            logger.info("Screener using AKShare sina snapshot (%d stocks)", len(df))
            _snapshot_cache = (now, df)
            return df
    except Exception as e:
        errors.append(f"akshare-sina: {e}")
        logger.warning("AKShare sina snapshot failed: %s", e)

    raise HTTPException(
        status_code=502,
        detail=f"All data sources failed: {'; '.join(errors)}",
    )


# ---------------------------------------------------------------------------
# Data source: Tencent Finance (primary, full fundamentals)
# ---------------------------------------------------------------------------

def _fetch_tencent_snapshot() -> pd.DataFrame | None:
    """Fetch full A-share snapshot via Tencent Finance API.

    Uses mootdx to get stock codes, then tencent_quote() for batch quotes.
    Returns PE/PB/market cap/turnover rate — all fundamentals included.
    """
    from src.api.astock_helpers import tencent_full_snapshot

    df = tencent_full_snapshot()
    if df is None or df.empty:
        return None

    # Map Tencent fields to screener output columns
    out = pd.DataFrame({
        "code": df["code"],
        "name": df["name"],
        "close": df["price"],
        "pct_change": df["change_pct"],
        "pe": df["pe_ttm"],
        "pb": df["pb"],
        "market_cap": df["mcap_yi"],  # already in 亿元
        "volume": df["volume"],
        "turnover_rate": df["turnover_pct"],
    })

    # Convert numerics
    for col in ("close", "pct_change", "pe", "pb", "market_cap", "volume", "turnover_rate"):
        out[col] = pd.to_numeric(out[col], errors="coerce")

    return out


# ---------------------------------------------------------------------------
# Data source: mootdx (通达信)
# ---------------------------------------------------------------------------

def _fetch_mootdx_snapshot() -> pd.DataFrame | None:
    """Fetch via mootdx TCP connection. Returns quotes + name + pct_change.
    No PE/PB/市值 (would need per-stock finance calls, too slow for 5000+).
    """
    from mootdx.quotes import Quotes

    client = Quotes.factory(market="std")

    # Get stock lists for both exchanges
    sh = client.stocks(market=1)
    sz = client.stocks(market=0)

    # Filter A-share codes: 6xxxxx (SH), 0xxxxx/3xxxxx (SZ)
    sh_a = sh[sh["code"].str.match(r"^6\d{5}$")]
    sz_a = sz[sz["code"].str.match(r"^(0|3)\d{5}$")]
    sh_codes = sh_a["code"].tolist()
    sz_names = sh_a.set_index("code")["name"].to_dict()

    sz_codes = sz_a["code"].tolist()
    sz_names_map = sz_a.set_index("code")["name"].to_dict()

    all_codes = sh_codes + sz_codes
    all_names = {**sz_names, **sz_names_map}

    # Batch fetch quotes (mootdx handles chunking internally)
    quotes = client.quotes(symbol=all_codes)
    if quotes is None or quotes.empty:
        return None

    # Build output DataFrame
    df = quotes[["code", "price", "last_close", "vol", "amount"]].copy()
    df = df.rename(columns={
        "price": "close",
        "vol": "volume",
    })

    # Add name from stock list
    df["name"] = df["code"].map(all_names).fillna("")

    # Calculate pct_change
    df["pct_change"] = ((df["close"] / df["last_close"]) - 1) * 100
    df.loc[df["last_close"] == 0, "pct_change"] = None

    # No PE/PB/market_cap from mootdx batch quotes
    df["pe"] = None
    df["pb"] = None
    df["market_cap"] = None
    df["turnover_rate"] = None

    # Convert volume to int
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

    # Keep standard columns
    cols = ["code", "name", "close", "pct_change", "market_cap", "pe", "pb", "volume", "turnover_rate"]
    df = df[[c for c in cols if c in df.columns]].copy()

    return df


# ---------------------------------------------------------------------------
# Data source: AKShare sina (quotes only)
# ---------------------------------------------------------------------------

def _fetch_akshare_sina_snapshot() -> pd.DataFrame | None:
    """Fetch via AKShare sina (price/volume only, no PE/PB/市值)."""
    import akshare as ak

    df = ak.stock_zh_a_spot()

    col_map = {
        "代码": "code",
        "名称": "name",
        "最新价": "close",
        "涨跌幅": "pct_change",
        "成交量": "volume",
        "成交额": "amount",
    }
    df = df.rename(columns=col_map)

    keep = [c for c in col_map.values() if c in df.columns]
    df = df[keep].copy()

    # Sina source lacks these
    df["pe"] = None
    df["pb"] = None
    df["market_cap"] = None
    df["turnover_rate"] = None

    for col in ("close", "pct_change", "volume", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


# ---------------------------------------------------------------------------
# Board classification by stock code prefix
# ---------------------------------------------------------------------------

_BOARD_MAP: dict[str, Any] = {
    "main": lambda c: c.startswith(("6", "0")),
    "gem": lambda c: c.startswith("3"),
    "star": lambda c: c.startswith("688"),
    "beijing": lambda c: c.startswith(("4", "8")) and not c.startswith("688"),
}


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

_VALID_SORT_FIELDS = {"market_cap", "pe", "pb", "volume", "turnover_rate", "pct_change"}


def _apply_filters(
    df: pd.DataFrame,
    *,
    market: str | None,
    mcap_min: float | None,
    mcap_max: float | None,
    pe_min: float | None,
    pe_max: float | None,
    pb_min: float | None,
    pb_max: float | None,
    volume_min: float | None,
    exclude_st: bool,
    sort_by: str,
    sort_order: str,
) -> pd.DataFrame:
    """Apply all filter criteria to the snapshot DataFrame."""
    out = df.copy()

    # Exclude ST stocks
    if exclude_st and "name" in out.columns:
        out = out[~out["name"].str.contains("ST", na=False)]

    # Board filter
    if market and market != "all" and "code" in out.columns:
        pred = _BOARD_MAP.get(market)
        if pred:
            out = out[out["code"].apply(pred)]

    # Market cap range
    if mcap_min is not None and "market_cap" in out.columns:
        out = out[out["market_cap"] >= mcap_min]
    if mcap_max is not None and "market_cap" in out.columns:
        out = out[out["market_cap"] <= mcap_max]

    # PE range
    if pe_min is not None and "pe" in out.columns:
        out = out[(out["pe"].isna()) | (out["pe"] >= pe_min)]
    if pe_max is not None and "pe" in out.columns:
        out = out[(out["pe"].isna()) | (out["pe"] <= pe_max)]

    # PB range
    if pb_min is not None and "pb" in out.columns:
        out = out[(out["pb"].isna()) | (out["pb"] >= pb_min)]
    if pb_max is not None and "pb" in out.columns:
        out = out[(out["pb"].isna()) | (out["pb"] <= pb_max)]

    # Volume minimum
    if volume_min is not None and "volume" in out.columns:
        out = out[out["volume"] >= volume_min]

    # Sort
    if sort_by in _VALID_SORT_FIELDS and sort_by in out.columns:
        ascending = sort_order == "asc"
        out = out.sort_values(by=sort_by, ascending=ascending, na_position="last")

    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def register_screener_routes(app: FastAPI) -> None:  # noqa: C901
    @app.get("/screener/ashare")
    async def screen_ashare(
        market: str | None = Query(None, description="Board: main/gem/star/beijing/all"),
        mcap_min: float | None = Query(None, description="Min market cap in yi-yuan"),
        mcap_max: float | None = Query(None, description="Max market cap in yi-yuan"),
        pe_min: float | None = Query(None, description="Min P/E"),
        pe_max: float | None = Query(None, description="Max P/E"),
        pb_min: float | None = Query(None, description="Min P/B"),
        pb_max: float | None = Query(None, description="Max P/B"),
        volume_min: float | None = Query(None, description="Min daily volume"),
        exclude_st: bool = Query(True, description="Exclude ST stocks"),
        sort_by: str = Query("market_cap", description="Sort field"),
        sort_order: str = Query("desc", description="asc or desc"),
        page: int = Query(1, ge=1, description="Page number"),
        page_size: int = Query(30, ge=10, le=100, description="Items per page"),
    ) -> dict[str, Any]:
        snapshot = _get_snapshot()

        filtered = _apply_filters(
            snapshot,
            market=market,
            mcap_min=mcap_min,
            mcap_max=mcap_max,
            pe_min=pe_min,
            pe_max=pe_max,
            pb_min=pb_min,
            pb_max=pb_max,
            volume_min=volume_min,
            exclude_st=exclude_st,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        total = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size
        page_df = filtered.iloc[start:end]

        stocks = []
        for _, row in page_df.iterrows():
            stocks.append({
                "code": str(row.get("code", "")),
                "name": str(row.get("name", "")),
                "close": _safe_float(row.get("close")),
                "pct_change": _safe_float(row.get("pct_change")),
                "market_cap": _safe_float(row.get("market_cap")),
                "pe": _safe_float(row.get("pe")),
                "pb": _safe_float(row.get("pb")),
                "volume": _safe_float(row.get("volume")),
                "turnover_rate": _safe_float(row.get("turnover_rate")),
            })

        return {
            "status": "ok",
            "total": total,
            "page": page,
            "page_size": page_size,
            "stocks": stocks,
        }


def _safe_float(val: Any) -> float | None:
    """Convert a value to float, returning None if not possible."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
