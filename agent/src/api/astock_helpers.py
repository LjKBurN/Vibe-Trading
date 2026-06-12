"""Core helpers extracted from a-stock-data (V3.2.2).

Provides direct API access for A-share market data (27 endpoints):
- Tencent Finance (quotes, PE/PB/market cap) — no IP blocking
- Mootdx TCP (K-lines, quotes, finance, F10) — no IP blocking
- Eastmoney Datacenter (reports, news, concepts, dragon tiger, margin, etc.) — with rate limiting
- THS (EPS forecast, hot stocks, northbound) — zero auth
- Baidu Stock (K-line with MA)
- Sina (financial reports)
- CNInfo (announcements with dynamic orgId)
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limiter for eastmoney requests
# ---------------------------------------------------------------------------

_last_em_request: float = 0.0
_EM_MIN_INTERVAL = 0.5  # seconds between eastmoney calls


def _rate_limit_em() -> None:
    """Ensure minimum interval between eastmoney requests."""
    global _last_em_request
    elapsed = time.time() - _last_em_request
    if elapsed < _EM_MIN_INTERVAL:
        time.sleep(_EM_MIN_INTERVAL - elapsed)
    _last_em_request = time.time()


# ---------------------------------------------------------------------------
# Stock code prefix helper
# ---------------------------------------------------------------------------

def get_prefix(code: str) -> str:
    """Return market prefix for a stock code.

    Rules:
        6xx / 9xx → sh (Shanghai)
        8xx       → bj (Beijing)
        others    → sz (Shenzhen)
    """
    if code.startswith("6") or code.startswith("9"):
        return "sh"
    if code.startswith("8"):
        return "bj"
    return "sz"


# ---------------------------------------------------------------------------
# Tencent Finance — batch quotes (primary data source)
# ---------------------------------------------------------------------------

def tencent_quote(codes: list[str] | str) -> pd.DataFrame:
    """Fetch real-time quotes from Tencent Finance API.

    Supports batch queries for all A-share codes in a single request.
    No IP blocking, includes PE/PB/market cap/turnover rate.

    Args:
        codes: Single stock code (e.g. "600519") or list of codes.

    Returns:
        DataFrame with columns: code, name, price, last_close, change_pct,
        pe_ttm, pb, mcap_yi, turnover_pct, limit_up, limit_down, volume, amount
    """
    if isinstance(codes, str):
        codes = [codes]

    # Build Tencent-style codes with market prefix
    prefixed = [f"{get_prefix(c)}{c}" for c in codes]

    # Tencent supports all codes in one URL (comma-separated)
    url = f"https://qt.gtimg.cn/q={','.join(prefixed)}"

    resp = requests.get(url, timeout=15)
    resp.encoding = "gbk"
    text = resp.text

    rows: list[dict[str, Any]] = []
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        # Format: v_sh600519="1~name~..."
        try:
            _, data = line.split("=", 1)
            data = data.strip('"')
            if not data:
                continue
            vals = data.split("~")

            # vals[2] is the raw code (e.g. "600519")
            raw_code = vals[2] if len(vals) > 2 else ""
            if not raw_code or not raw_code.isdigit():
                continue

            row: dict[str, Any] = {
                "code": raw_code,
                "name": vals[1] if len(vals) > 1 else "",
                "price": _safe_float(vals[3]) if len(vals) > 3 else None,
                "last_close": _safe_float(vals[4]) if len(vals) > 4 else None,
                "change_pct": _safe_float(vals[32]) if len(vals) > 32 else None,
                "pe_ttm": _safe_float(vals[39]) if len(vals) > 39 else None,
                "mcap_yi": _safe_float(vals[44]) if len(vals) > 44 else None,
                "turnover_pct": _safe_float(vals[38]) if len(vals) > 38 else None,
                "pb": _safe_float(vals[46]) if len(vals) > 46 else None,
                "limit_up": _safe_float(vals[47]) if len(vals) > 47 else None,
                "limit_down": _safe_float(vals[48]) if len(vals) > 48 else None,
                "volume": _safe_float(vals[6]) if len(vals) > 6 else None,
                "amount": _safe_float(vals[37]) if len(vals) > 37 else None,
            }

            # Calculate pct_change from price/last_close if missing
            if row["change_pct"] is None and row["price"] and row["last_close"] and row["last_close"] != 0:
                row["change_pct"] = round((row["price"] / row["last_close"] - 1) * 100, 2)

            rows.append(row)
        except (IndexError, ValueError):
            continue

    return pd.DataFrame(rows)


def tencent_full_snapshot() -> pd.DataFrame:
    """Fetch full A-share market snapshot via Tencent Finance.

    Uses mootdx to get the complete stock list, then batch-fetches all quotes
    from Tencent. Returns a DataFrame compatible with screener_routes.
    """
    # Step 1: Get stock list from mootdx (TCP, no HTTP proxy issues)
    all_codes = _get_ashare_codes()

    if not all_codes:
        logger.warning("No A-share codes obtained, falling back")
        return pd.DataFrame()

    # Step 2: Batch fetch from Tencent (supports all codes in one request)
    logger.info("Fetching Tencent quotes for %d stocks", len(all_codes))

    # Tencent URL has a practical limit; batch in chunks of 800
    all_dfs: list[pd.DataFrame] = []
    batch_size = 800
    for i in range(0, len(all_codes), batch_size):
        batch = all_codes[i : i + batch_size]
        df_batch = tencent_quote(batch)
        if not df_batch.empty:
            all_dfs.append(df_batch)

    if not all_dfs:
        return pd.DataFrame()

    df = pd.concat(all_dfs, ignore_index=True)

    # Filter to only valid A-share codes (remove empty prices)
    df = df[df["price"].notna() & (df["price"] > 0)].copy()

    logger.info("Tencent snapshot: %d valid stocks", len(df))
    return df


def _get_ashare_codes() -> list[str]:
    """Get all A-share stock codes via mootdx TCP connection."""
    try:
        from mootdx.quotes import Quotes

        client = Quotes.factory(market="std")
        sh = client.stocks(market=1)
        sz = client.stocks(market=0)

        sh_a = sh[sh["code"].str.match(r"^6\d{5}$")]["code"].tolist()
        sz_a = sz[sz["code"].str.match(r"^(0|3)\d{5}$")]["code"].tolist()
        codes = sh_a + sz_a
        logger.info("Got %d A-share codes from mootdx", len(codes))
        return codes
    except Exception as e:
        logger.warning("mootdx stock list failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Eastmoney — rate-limited HTTP client
# ---------------------------------------------------------------------------

_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
}


def em_get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = 15) -> requests.Response:
    """Rate-limited GET to eastmoney APIs."""
    _rate_limit_em()
    req_headers = {**_EM_HEADERS, **(headers or {})}
    resp = requests.get(url, params=params, headers=req_headers, timeout=timeout)
    resp.raise_for_status()
    return resp


def eastmoney_datacenter(
    report_type: str,
    code: str = "",
    page_size: int = 50,
    page: int = 1,
) -> pd.DataFrame:
    """Query Eastmoney DataCenter for various report types.

    Args:
        report_type: One of the standard report type identifiers
        code: Stock code (optional)
        page_size: Results per page
        page: Page number
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": report_type,
        "columns": "ALL",
        "pageSize": page_size,
        "pageNumber": page,
        "sortColumns": "NOTICE_DATE",
        "sortTypes": -1,
    }
    if code:
        params["filter"] = f'(SECURITY_CODE="{code}")'

    resp = em_get(url, params=params)
    data = resp.json()
    if data.get("result") and data["result"].get("data"):
        return pd.DataFrame(data["result"]["data"])
    return pd.DataFrame()


def eastmoney_reports(code: str, page_size: int = 20) -> pd.DataFrame:
    """Fetch research reports for a stock."""
    url = "https://reportapi.eastmoney.com/report/list"
    params = {
        "industryCode": "*",
        "pageSize": page_size,
        "industry": "*",
        "rating": "*",
        "ratingChange": "*",
        "beginTime": "",
        "endTime": "",
        "pageNo": 1,
        "fields": "",
        "qType": 0,
        "orgCode": "",
        "code": code,
        "rcode": "",
    }
    resp = em_get(url, params=params)
    data = resp.json()
    if data.get("data"):
        return pd.DataFrame(data["data"])
    return pd.DataFrame()


def eastmoney_stock_news(code: str, page_size: int = 20) -> pd.DataFrame:
    """Fetch news for a stock from Eastmoney."""
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    params = {
        "cb": "jQuery",
        "param": f'{{"uid":"","keyword":"{code}","type":["cmsArticleWebOld"],"client":"web","clientVersion":"curr","param":{{"cmsArticleWebOld":{{"searchScope":"default","sort":"default","pageIndex":1,"pageSize":{page_size},"preTag":"","postTag":""}}}}}}',
    }
    try:
        resp = em_get(url, params=params)
        # Strip JSONP wrapper
        text = resp.text
        json_str = re.sub(r"^jQuery\(", "", text)
        json_str = re.sub(r"\)$", "", json_str)
        import json
        data = json.loads(json_str)
        articles = (
            data.get("result", {})
            .get("cmsArticleWebOld", {})
            .get("list", [])
        )
        if articles:
            return pd.DataFrame(articles)
    except Exception as e:
        logger.warning("eastmoney_stock_news failed: %s", e)
    return pd.DataFrame()


def eastmoney_concept_blocks(code: str) -> pd.DataFrame:
    """Fetch concept/block membership for a stock."""
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": f"1.{code}" if code.startswith("6") else f"0.{code}",
        "fields": "f12,f14",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json()
        if data.get("data"):
            return pd.DataFrame([data["data"]])
    except Exception as e:
        logger.warning("eastmoney_concept_blocks failed: %s", e)
    return pd.DataFrame()


def eastmoney_stock_info(code: str) -> pd.DataFrame:
    """Fetch basic info for a stock from Eastmoney."""
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    market_id = "1" if code.startswith("6") else "0"
    params = {
        "secid": f"{market_id}.{code}",
        "fields": "f57,f58,f162,f167,f43,f170,f171,f152,f168,f169",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json()
        if data.get("data"):
            return pd.DataFrame([data["data"]])
    except Exception as e:
        logger.warning("eastmoney_stock_info failed: %s", e)
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# CNInfo — announcements
# ---------------------------------------------------------------------------

def cninfo_announcements(code: str, page_size: int = 20) -> pd.DataFrame:
    """Fetch announcements from CNInfo (巨潮资讯)."""
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    # Map stock code to orgId is complex; use search by code
    params = {
        "pageNum": 1,
        "pageSize": page_size,
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": code,
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    try:
        headers = {
            "User-Agent": _EM_HEADERS["User-Agent"],
            "Content-Type": "application/x-www-form-urlencoded",
        }
        resp = requests.post(url, data=params, headers=headers, timeout=15)
        data = resp.json()
        if data.get("announcements"):
            return pd.DataFrame(data["announcements"])
    except Exception as e:
        logger.warning("cninfo_announcements failed: %s", e)
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Sina — financial reports
# ---------------------------------------------------------------------------

def sina_financial_report(code: str, report_type: str = "balance") -> pd.DataFrame:
    """Fetch financial statements from Sina.

    Args:
        code: Stock code (e.g. "600519")
        report_type: "balance" (资产负债表), "profit" (利润表), "cashflow" (现金流量表)
    """
    type_map = {
        "balance": "zcfzb",
        "profit": "lrb",
        "cashflow": "xjllb",
    }
    suffix = type_map.get(report_type, "zcfzb")
    url = f"https://money.finance.sina.com.cn/corp/go.php/vFD_{suffix}/stockid/{code}/ctrl/part/displaytype/4.phtml"

    try:
        import io
        resp = requests.get(url, timeout=15)
        resp.encoding = "gbk"
        tables = pd.read_html(io.StringIO(resp.text))
        if tables:
            return tables[0]
    except Exception as e:
        logger.warning("sina_financial_report failed: %s", e)
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val: Any) -> float | None:
    """Convert a value to float, returning None if not possible."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if pd.notna(f) else None
    except (ValueError, TypeError):
        return None


# ===========================================================================
# Layer 1.3: Baidu Stock — K-line with MA5/MA10/MA20
# ===========================================================================

def baidu_kline_with_ma(code: str, start_time: str = "") -> dict[str, Any]:
    """Baidu Stock K-line — returns data with ma5/ma10/ma20 built-in."""
    url = "https://finance.pae.baidu.com/selfselect/getstockquotation"
    params = {
        "all": "1", "isIndex": "false", "isBk": "false", "isBlock": "false",
        "isFutures": "false", "isStock": "true", "newFormat": "1",
        "group": "quotation_kline_ab", "finClientType": "pc",
        "code": code, "start_time": start_time, "ktype": "1",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/vnd.finance-web.v1+json",
        "Origin": "https://gushitong.baidu.com",
        "Referer": "https://gushitong.baidu.com/",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        d = r.json()
        result = d.get("Result", {})
        md = result.get("newMarketData", {})
        return {
            "keys": md.get("keys", []),
            "rows": md.get("marketData", "").split(";"),
        }
    except Exception as e:
        logger.warning("baidu_kline_with_ma failed: %s", e)
        return {"keys": [], "rows": []}


# ===========================================================================
# Layer 2.2: THS EPS Forecast (同花顺一致预期)
# ===========================================================================

def ths_eps_forecast(code: str) -> pd.DataFrame:
    """THS consensus EPS forecast — direct from basic.10jqka.com.cn."""
    import io
    url = f"https://basic.10jqka.com.cn/new/{code}/worth.html"
    headers = {
        "User-Agent": _EM_HEADERS["User-Agent"],
        "Referer": "https://basic.10jqka.com.cn/",
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.encoding = "gbk"
        dfs = pd.read_html(io.StringIO(r.text))
        for df in dfs:
            cols = [str(c) for c in df.columns]
            if any("每股收益" in c or "均值" in c for c in cols):
                return df
        return dfs[0] if dfs else pd.DataFrame()
    except Exception as e:
        logger.warning("ths_eps_forecast failed: %s", e)
        return pd.DataFrame()


# ===========================================================================
# Layer 3.1: THS Hot Stocks — 当日强势股 + 题材归因
# ===========================================================================

def ths_hot_reason(date: str | None = None) -> pd.DataFrame:
    """THS daily hot stocks with reason tags (题材归因)."""
    from datetime import date as _date
    if date is None:
        date = _date.today().strftime("%Y-%m-%d")
    url = (
        f"http://zx.10jqka.com.cn/event/api/getharden/"
        f"date/{date}/orderby/date/orderway/desc/charset/GBK/"
    )
    headers = {"User-Agent": _EM_HEADERS["User-Agent"]}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        if data.get("errocode", 0) != 0:
            return pd.DataFrame()
        rows = data.get("data") or []
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        rename_map = {
            "name": "名称", "code": "代码", "reason": "题材归因",
            "close": "收盘价", "zhangdie": "涨跌额", "zhangfu": "涨幅%",
            "huanshou": "换手率%", "chengjiaoe": "成交额",
            "chengjiaoliang": "成交量", "ddejingliang": "大单净量", "market": "市场",
        }
        return df.rename(columns=rename_map)
    except Exception as e:
        logger.warning("ths_hot_reason failed: %s", e)
        return pd.DataFrame()


# ===========================================================================
# Layer 3.2: THS Northbound Capital (北向资金)
# ===========================================================================

def hsgt_realtime() -> pd.DataFrame:
    """THS northbound capital — real-time minute-level flow (沪深股通)."""
    headers = {
        "User-Agent": _EM_HEADERS["User-Agent"],
        "Host": "data.hexin.cn",
        "Referer": "https://data.hexin.cn/",
    }
    try:
        r = requests.get(
            "https://data.hexin.cn/market/hsgtApi/method/dayChart/",
            headers=headers, timeout=10,
        )
        d = r.json()
        times = d.get("time", [])
        hgt = d.get("hgt", [])
        sgt = d.get("sgt", [])
        n = len(times)
        return pd.DataFrame({
            "time": times,
            "hgt_yi": hgt[:n] + [None] * (n - len(hgt)),
            "sgt_yi": sgt[:n] + [None] * (n - len(sgt)),
        })
    except Exception as e:
        logger.warning("hsgt_realtime failed: %s", e)
        return pd.DataFrame()


# ===========================================================================
# Layer 3.3: Eastmoney Concept Blocks (V3.2.2 — replaces Baidu PAE)
# ===========================================================================

def eastmoney_concept_blocks_v2(code: str) -> dict[str, Any]:
    """Stock concept/block membership via Eastmoney slist (replaces Baidu PAE)."""
    market_code = 1 if code.startswith("6") else 0
    params = {
        "fltt": "2", "invt": "2",
        "secid": f"{market_code}.{code}",
        "spt": "3", "pi": "0", "pz": "200", "po": "1",
        "fields": "f12,f14,f3,f128",
    }
    headers = {"User-Agent": _EM_HEADERS["User-Agent"], "Referer": "https://quote.eastmoney.com/"}
    try:
        r = em_get("https://push2.eastmoney.com/api/qt/slist/get",
                    params=params, headers=headers, timeout=15)
        d = r.json()
        diff = (d.get("data") or {}).get("diff") or {}
        items = diff.values() if isinstance(diff, dict) else diff
        boards = []
        for it in items:
            boards.append({
                "name": it.get("f14", ""),
                "code": it.get("f12", ""),
                "change_pct": it.get("f3", ""),
                "lead_stock": it.get("f128", ""),
            })
        return {
            "total": len(boards),
            "boards": boards,
            "concept_tags": [b["name"] for b in boards],
        }
    except Exception as e:
        logger.warning("eastmoney_concept_blocks_v2 failed: %s", e)
        return {"total": 0, "boards": [], "concept_tags": []}


# ===========================================================================
# Layer 3.4: Eastmoney Fund Flow Minute (个股资金流分钟级)
# ===========================================================================

def eastmoney_fund_flow_minute(code: str) -> list[dict[str, Any]]:
    """Stock fund flow — minute-level, intraday (主力/大单/中单/小单/超大单)."""
    secid = f"1.{code}" if code.startswith("6") else f"0.{code}"
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {
        "secid": secid, "klt": 1,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    headers = {
        "User-Agent": _EM_HEADERS["User-Agent"],
        "Referer": "https://quote.eastmoney.com/",
    }
    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        d = r.json()
        rows = []
        for line in d.get("data", {}).get("klines", []):
            parts = line.split(",")
            if len(parts) >= 6:
                rows.append({
                    "time": parts[0],
                    "main_net": float(parts[1]),
                    "small_net": float(parts[2]),
                    "mid_net": float(parts[3]),
                    "large_net": float(parts[4]),
                    "super_net": float(parts[5]),
                })
        return rows
    except Exception as e:
        logger.warning("eastmoney_fund_flow_minute failed: %s", e)
        return []


# ===========================================================================
# Layer 3.5: Dragon Tiger Board (龙虎榜)
# ===========================================================================

def dragon_tiger_board(code: str, trade_date: str, look_back: int = 30) -> dict[str, Any]:
    """Dragon Tiger Board — buy/sell seats + institution activity."""
    from datetime import datetime, timedelta
    start = datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=look_back)
    start_str = start.strftime("%Y-%m-%d")

    # 1. Board records
    records = []
    data = eastmoney_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        code=code,
        page_size=50,
    )
    for row in data.to_dict("records") if not data.empty else []:
        trade_dt = str(row.get("TRADE_DATE", ""))[:10]
        if trade_dt < start_str:
            continue
        records.append({
            "date": trade_dt,
            "reason": row.get("EXPLANATION", ""),
            "net_buy": round((row.get("BILLBOARD_NET_AMT") or 0) / 10000, 1),
            "turnover": round(float(row.get("TURNOVERRATE") or 0), 2),
        })

    return {"records": records, "seats": {"buy": [], "sell": []}, "institution": {}}


def eastmoney_datacenter_raw(
    report_name: str,
    filter_str: str = "",
    page_size: int = 50,
    sort_columns: str = "",
    sort_types: str = "-1",
) -> list[dict[str, Any]]:
    """Eastmoney DataCenter raw query — returns list of dicts."""
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": report_name, "columns": "ALL",
        "filter": filter_str, "pageNumber": "1", "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": "WEB", "client": "WEB",
    }
    try:
        r = em_get(url, params=params, timeout=15)
        d = r.json()
        if d.get("result") and d["result"].get("data"):
            return d["result"]["data"]
        return []
    except Exception as e:
        logger.warning("eastmoney_datacenter_raw failed: %s", e)
        return []


# ===========================================================================
# Layer 3.6: Lock-up Expiry (限售解禁日历)
# ===========================================================================

def lockup_expiry(code: str, trade_date: str, forward_days: int = 90) -> dict[str, Any]:
    """Lock-up expiry calendar — history + upcoming."""
    from datetime import datetime, timedelta

    history_data = eastmoney_datacenter_raw(
        "RPT_LIFT_STAGE",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=15,
        sort_columns="FREE_DATE", sort_types="-1",
    )
    history = []
    for row in history_data:
        history.append({
            "date": str(row.get("FREE_DATE", ""))[:10],
            "type": row.get("LIMITED_STOCK_TYPE", ""),
            "shares": row.get("FREE_SHARES_NUM", 0),
            "ratio": row.get("FREE_RATIO", 0),
        })

    end_date = datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=forward_days)
    end_str = end_date.strftime("%Y-%m-%d")
    upcoming_data = eastmoney_datacenter_raw(
        "RPT_LIFT_STAGE",
        filter_str=f'(SECURITY_CODE="{code}")(FREE_DATE>=\'{trade_date}\')(FREE_DATE<=\'{end_str}\')',
        page_size=20,
        sort_columns="FREE_DATE", sort_types="1",
    )
    upcoming = []
    for row in upcoming_data:
        upcoming.append({
            "date": str(row.get("FREE_DATE", ""))[:10],
            "type": row.get("LIMITED_STOCK_TYPE", ""),
            "shares": row.get("FREE_SHARES_NUM", 0),
            "ratio": row.get("FREE_RATIO", 0),
        })

    return {"history": history, "upcoming": upcoming}


# ===========================================================================
# Layer 3.7: Industry Comparison (行业板块排名)
# ===========================================================================

def industry_comparison(top_n: int = 20) -> dict[str, Any]:
    """Industry sector ranking by daily change (东财行业板块)."""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1",
        "fltt": "2", "invt": "2", "fs": "m:90+t:2",
        "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207",
    }
    try:
        r = em_get(url, params=params, timeout=15)
        d = r.json()
        items = d.get("data", {}).get("diff", [])
        if not items:
            return {"top": [], "bottom": [], "total": 0}
        rows = []
        for i, item in enumerate(items):
            rows.append({
                "rank": i + 1,
                "name": item.get("f14", ""),
                "change_pct": item.get("f3", 0),
                "code": item.get("f12", ""),
                "up_count": item.get("f104", 0),
                "down_count": item.get("f105", 0),
                "leader": item.get("f140", ""),
                "leader_change": item.get("f136", 0),
            })
        return {"top": rows[:top_n], "bottom": rows[-top_n:], "total": len(rows)}
    except Exception as e:
        logger.warning("industry_comparison failed: %s", e)
        return {"top": [], "bottom": [], "total": 0}


# ===========================================================================
# Layer 3.8: Daily Dragon Tiger (全市场龙虎榜)
# ===========================================================================

def daily_dragon_tiger(trade_date: str | None = None, min_net_buy: float | None = None) -> dict[str, Any]:
    """Full market Dragon Tiger Board for a given date."""
    from datetime import datetime
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    data = eastmoney_datacenter_raw(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=f"(TRADE_DATE>='{trade_date}')(TRADE_DATE<='{trade_date}')",
        page_size=500,
        sort_columns="BILLBOARD_NET_AMT", sort_types="-1",
    )
    if not data:
        return {"date": trade_date, "total_records": 0, "stocks": []}
    actual_date = str(data[0].get("TRADE_DATE", ""))[:10] if data else trade_date
    stocks = []
    for row in data:
        net_buy = (row.get("BILLBOARD_NET_AMT") or 0) / 10000
        if min_net_buy is not None and net_buy < min_net_buy:
            continue
        stocks.append({
            "code": row.get("SECURITY_CODE", ""),
            "name": row.get("SECURITY_NAME_ABBR", ""),
            "reason": row.get("EXPLANATION", ""),
            "close": row.get("CLOSE_PRICE") or 0,
            "change_pct": round(float(row.get("CHANGE_RATE") or 0), 2),
            "net_buy_wan": round(net_buy, 1),
            "buy_wan": round((row.get("BILLBOARD_BUY_AMT") or 0) / 10000, 1),
            "sell_wan": round((row.get("BILLBOARD_SELL_AMT") or 0) / 10000, 1),
            "turnover_pct": round(float(row.get("TURNOVERRATE") or 0), 2),
        })
    return {"date": actual_date, "total_records": len(stocks), "stocks": stocks}


# ===========================================================================
# Layer 4: Capital / Chips — 融资融券/大宗交易/股东户数/分红/资金流120日
# ===========================================================================

def margin_trading(code: str, page_size: int = 30) -> list[dict[str, Any]]:
    """Margin trading details (融资融券) — daily level."""
    data = eastmoney_datacenter_raw(
        "RPTA_WEB_RZRQ_GGMX",
        filter_str=f'(SCODE="{code}")',
        page_size=page_size,
        sort_columns="DATE", sort_types="-1",
    )
    rows = []
    for row in data:
        rows.append({
            "date": str(row.get("DATE", ""))[:10],
            "rzye": row.get("RZYE", 0),        # 融资余额
            "rzmre": row.get("RZMRE", 0),       # 融资买入额
            "rzche": row.get("RZCHE", 0),        # 融资偿还额
            "rqye": row.get("RQYE", 0),          # 融券余额
            "rqmcl": row.get("RQMCL", 0),        # 融券卖出量
            "rqchl": row.get("RQCHL", 0),        # 融券偿还量
            "rzrqye": row.get("RZRQYE", 0),      # 融资融券余额合计
        })
    return rows


def block_trade(code: str, page_size: int = 20) -> list[dict[str, Any]]:
    """Block trade records (大宗交易)."""
    data = eastmoney_datacenter_raw(
        "RPT_DATA_BLOCKTRADE",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size,
        sort_columns="TRADE_DATE", sort_types="-1",
    )
    rows = []
    for row in data:
        close = row.get("CLOSE_PRICE") or 0
        deal_price = row.get("DEAL_PRICE") or 0
        premium = ((deal_price / close - 1) * 100) if close else 0
        rows.append({
            "date": str(row.get("TRADE_DATE", ""))[:10],
            "price": deal_price,
            "close": close,
            "premium_pct": round(premium, 2),
            "vol": row.get("DEAL_VOLUME", 0),
            "amount": row.get("DEAL_AMT", 0),
            "buyer": row.get("BUYER_NAME", ""),
            "seller": row.get("SELLER_NAME", ""),
        })
    return rows


def holder_num_change(code: str, page_size: int = 10) -> list[dict[str, Any]]:
    """Shareholder count changes (股东户数变化) — quarterly."""
    data = eastmoney_datacenter_raw(
        "RPT HolderNUMLatest".replace(" ", ""),
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size,
        sort_columns="END_DATE", sort_types="-1",
    )
    # Fix: report name has no space
    if not data:
        data = eastmoney_datacenter_raw(
            "RPT_HOLDERNUMLATEST",
            filter_str=f'(SECURITY_CODE="{code}")',
            page_size=page_size,
            sort_columns="END_DATE", sort_types="-1",
        )
    rows = []
    for row in data:
        rows.append({
            "date": str(row.get("END_DATE", ""))[:10],
            "holder_num": row.get("HOLDER_NUM", 0),
            "change_num": row.get("HOLDER_NUM_CHANGE", 0),
            "change_ratio": row.get("HOLDER_NUM_RATIO", 0),
            "avg_shares": row.get("AVG_FREE_SHARES", 0),
        })
    return rows


def dividend_history(code: str, page_size: int = 20) -> list[dict[str, Any]]:
    """Dividend history (分红送转)."""
    data = eastmoney_datacenter_raw(
        "RPT_SHAREBONUS_DET",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size,
        sort_columns="EX_DIVIDEND_DATE", sort_types="-1",
    )
    rows = []
    for row in data:
        rows.append({
            "date": str(row.get("EX_DIVIDEND_DATE", ""))[:10],
            "bonus_rmb": row.get("PRETAX_BONUS_RMB", 0),
            "transfer_ratio": row.get("TRANSFER_RATIO", 0),
            "bonus_ratio": row.get("BONUS_RATIO", 0),
            "plan": row.get("ASSIGN_PROGRESS", ""),
        })
    return rows


def stock_fund_flow_120d(code: str) -> list[dict[str, Any]]:
    """Stock fund flow — 120 trading days, daily level (主力/大单/中单/小单/超大单)."""
    market_code = 1 if code.startswith("6") else 0
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "secid": f"{market_code}.{code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "lmt": "120",
    }
    headers = {
        "User-Agent": _EM_HEADERS["User-Agent"],
        "Referer": "https://quote.eastmoney.com/",
    }
    try:
        r = em_get(url, params=params, headers=headers, timeout=15)
        d = r.json()
        rows = []
        for line in d.get("data", {}).get("klines", []):
            parts = line.split(",")
            if len(parts) >= 7:
                rows.append({
                    "date": parts[0],
                    "main_net": float(parts[1]) if parts[1] != "-" else 0,
                    "small_net": float(parts[2]) if parts[2] != "-" else 0,
                    "mid_net": float(parts[3]) if parts[3] != "-" else 0,
                    "large_net": float(parts[4]) if parts[4] != "-" else 0,
                    "super_net": float(parts[5]) if parts[5] != "-" else 0,
                })
        return rows
    except Exception as e:
        logger.warning("stock_fund_flow_120d failed: %s", e)
        return []


# ===========================================================================
# Layer 5.3: Eastmoney Global News (东财全球资讯7×24)
# ===========================================================================

def eastmoney_global_news(page_size: int = 50) -> list[dict[str, Any]]:
    """Eastmoney global financial news (7x24 rolling)."""
    import uuid
    url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
    params = {
        "client": "web", "biz": "web_724",
        "fastColumn": "102", "sortEnd": "",
        "pageSize": str(page_size),
        "req_trace": str(uuid.uuid4()),
    }
    headers = {
        "User-Agent": _EM_HEADERS["User-Agent"],
        "Referer": "https://kuaixun.eastmoney.com/",
    }
    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        d = r.json()
        rows = []
        for item in d.get("data", {}).get("fastNewsList", []):
            rows.append({
                "title": item.get("title", ""),
                "summary": (item.get("summary", "") or "")[:200],
                "time": item.get("showTime", ""),
            })
        return rows
    except Exception as e:
        logger.warning("eastmoney_global_news failed: %s", e)
        return []


# ===========================================================================
# Layer 6.4: Sina Financial Report (V3.2.2 fixed — report_list structure)
# ===========================================================================

def sina_financial_report_v2(code: str, report_type: str = "lrb", num: int = 8) -> list[dict[str, Any]]:
    """Sina financial statements (V3.2.2 fixed). Returns list of dicts by period."""
    prefix = "sh" if code.startswith("6") else "sz"
    url = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
    params = {
        "paperCode": f"{prefix}{code}",
        "source": report_type,  # "fzb"/"lrb"/"llb"
        "type": "0",
        "page": "1",
        "num": str(num),
    }
    headers = {"User-Agent": _EM_HEADERS["User-Agent"]}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        report_list = r.json().get("result", {}).get("data", {}).get("report_list", {}) or {}
        rows = []
        for period in sorted(report_list.keys(), reverse=True)[:num]:
            obj = report_list[period]
            rec = {"报告期": f"{period[:4]}-{period[4:6]}-{period[6:8]}"}
            for it in obj.get("data", []) or []:
                title = it.get("item_title", "")
                if not title or it.get("item_value") is None:
                    continue
                rec[title] = it.get("item_value")
                tongbi = it.get("item_tongbi")
                if tongbi not in (None, ""):
                    rec[title + "_同比"] = tongbi
            rows.append(rec)
        return rows
    except Exception as e:
        logger.warning("sina_financial_report_v2 failed: %s", e)
        return []


# ===========================================================================
# Layer 7.1: CNInfo Announcements (V3.2.2 fixed — dynamic orgId)
# ===========================================================================

_CNINFO_ORGID_MAP: dict[str, str] = {}


def _cninfo_orgid(code: str) -> str:
    """Resolve stock code to real CNInfo orgId (dynamic lookup with fallback)."""
    global _CNINFO_ORGID_MAP
    if not _CNINFO_ORGID_MAP:
        try:
            r = requests.get(
                "http://www.cninfo.com.cn/new/data/szse_stock.json",
                headers={"User-Agent": _EM_HEADERS["User-Agent"]}, timeout=15,
            )
            _CNINFO_ORGID_MAP = {
                s["code"]: s["orgId"]
                for s in r.json().get("stockList", [])
            }
        except Exception as e:
            logger.warning("CNInfo orgId map fetch failed: %s", e)
    org = _CNINFO_ORGID_MAP.get(code)
    if org:
        return org
    # Fallback
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith("8") or code.startswith("4"):
        return f"gsbj0{code}"
    return f"gssz0{code}"


def cninfo_announcements_v2(code: str, page_size: int = 30) -> list[dict[str, Any]]:
    """CNInfo announcements with dynamic orgId mapping (V3.2.2 fix)."""
    from datetime import datetime
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    org_id = _cninfo_orgid(code)
    payload = {
        "stock": f"{code},{org_id}",
        "tabName": "fulltext",
        "pageSize": str(page_size),
        "pageNum": "1",
        "column": "", "category": "", "plate": "",
        "seDate": "", "searchkey": "", "secid": "",
        "sortName": "", "sortType": "", "isHLtitle": "true",
    }
    headers = {
        "User-Agent": _EM_HEADERS["User-Agent"],
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.cninfo.com.cn/new/disclosure",
        "Origin": "https://www.cninfo.com.cn",
    }
    try:
        r = requests.post(url, data=payload, headers=headers, timeout=15)
        d = r.json()
        rows = []
        for item in d.get("announcements", []) or []:
            ts = item.get("announcementTime")
            date_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d") if isinstance(ts, (int, float)) else ""
            rows.append({
                "title": item.get("announcementTitle", ""),
                "type": item.get("announcementTypeName", ""),
                "date": date_str,
                "url": f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={item.get('announcementId', '')}",
            })
        return rows
    except Exception as e:
        logger.warning("cninfo_announcements_v2 failed: %s", e)
        return []

