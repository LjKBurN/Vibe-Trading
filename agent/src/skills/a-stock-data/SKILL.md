---
name: a-stock-data
category: data-source
description: A股全栈数据工具包 (V3.2.2) — 覆盖行情(腾讯+百度K线)、研报(东财+同花顺)、信号(热点+北向+龙虎榜+解禁+行业)、资金面(融资融券+大宗交易+股东户数+分红+资金流)、新闻(东财个股+全球资讯)、基础数据(mootdx财务/F10+东财+新浪三表)、公告(巨潮)。免费，无API key，不封IP。
---

## Overview

七层数据架构，27 个端点，全部免费无需 API key：

1. **Tencent Finance** — 实时行情 + PE/PB/市值/换手率/涨跌停 (不封 IP)
2. **mootdx TCP** — 历史 K 线 + 五档盘口 + 财务快照 + F10 (不封 IP)
3. **Baidu Stock** — K 线带 MA5/MA10/MA20
4. **Eastmoney** — 研报/新闻/概念/龙虎榜/解禁/行业/资金流 (限流防封)
5. **THS** — 一致预期 EPS / 当日强势股 + 题材归因 / 北向资金 (零鉴权)
6. **Sina** — 财报三表 (资产负债表/利润表/现金流量表)
7. **CNInfo** — 巨潮公告 (动态 orgId 映射)

Helper module: `src/api/astock_helpers.py` (所有函数可直接 import)。

## Quick Start — 行情层

### 实时行情 + 基本面 (Tencent)

```python
from src.api.astock_helpers import tencent_quote, tencent_full_snapshot

# 个股
df = tencent_quote(["600519"])
# Returns: code, name, price, last_close, change_pct, pe_ttm, pb, mcap_yi,
#          turnover_pct, limit_up, limit_down, volume, amount

# 批量
df = tencent_quote(["600519", "000001", "300750"])

# 全市场快照 (~5600 只)
df = tencent_full_snapshot()
```

### K 线带均线 (Baidu)

```python
from src.api.astock_helpers import baidu_kline_with_ma

data = baidu_kline_with_ma("600519")
# keys 包含: time, open, close, high, low, volume, ma5avgprice, ma10avgprice, ma20avgprice
```

### 历史 OHLCV (mootdx TCP)

```python
from mootdx.quotes import Quotes
client = Quotes.factory(market="std")

# 日线 (支持日期范围)
df = client.get_k_data(code="600519", start_date="2024-01-01", end_date="2025-01-01")

# 分钟线 (15m, offset 分页)
df = client.bars(symbol="600519", frequency=1, offset=800)
```

频率码: 8=1m, 0=5m, 1=15m, 2=30m, 3=1H, 4=1D, 5=1W, 6=1M

## Quick Start — 研报层

```python
from src.api.astock_helpers import eastmoney_reports, ths_eps_forecast

# 东财研报列表 + PDF 下载 + 评级 + 三年 EPS
reports = eastmoney_reports("688017", page_size=10)

# 同花顺一致预期 EPS
df = ths_eps_forecast("688017")
# Returns: 年度, 预测机构数, 最小值, 均值, 最大值
```

## Quick Start — 信号层

```python
from src.api.astock_helpers import (
    ths_hot_reason,              # 当日强势股 + 题材归因
    hsgt_realtime,               # 北向资金分钟级
    eastmoney_concept_blocks_v2, # 概念板块归属
    eastmoney_fund_flow_minute,  # 个股资金流分钟级
    dragon_tiger_board,          # 龙虎榜席位
    daily_dragon_tiger,          # 全市场龙虎榜
    lockup_expiry,               # 限售解禁日历
    industry_comparison,         # 行业板块排名
)

# 当日强势股 + 题材归因 (核心: reason 字段)
df = ths_hot_reason("2026-05-09")
# Returns: 代码, 名称, 涨幅%, 题材归因, 换手率%, 成交额, 大单净量

# 北向资金 (沪深股通分钟级)
df = hsgt_realtime()

# 概念板块归属
blocks = eastmoney_concept_blocks_v2("600519")
print(blocks["concept_tags"])  # ['食品饮料', '白酒Ⅲ', '贵州板块', ...]

# 个股资金流 (分钟级)
flow = eastmoney_fund_flow_minute("000858")

# 龙虎榜
data = dragon_tiger_board("002475", "2026-05-17", look_back=30)

# 全市场龙虎榜
data = daily_dragon_tiger("2026-05-16", min_net_buy=5000)

# 限售解禁
data = lockup_expiry("002475", "2026-05-17")

# 行业排名
data = industry_comparison(20)
```

## Quick Start — 资金面 / 筹码层

```python
from src.api.astock_helpers import (
    margin_trading,       # 融资融券明细
    block_trade,          # 大宗交易
    holder_num_change,    # 股东户数变化
    dividend_history,     # 分红送转历史
    stock_fund_flow_120d, # 个股资金流120日
)

# 融资融券
data = margin_trading("600519")

# 大宗交易
data = block_trade("600519")

# 股东户数 (筹码集中度)
data = holder_num_change("600519")

# 分红送转
data = dividend_history("600519")

# 120日资金流 (主力/大单/中单/小单/超大单)
data = stock_fund_flow_120d("600519")
```

## Quick Start — 新闻层

```python
from src.api.astock_helpers import eastmoney_stock_news, eastmoney_global_news

# 个股新闻
news = eastmoney_stock_news("688017", page_size=10)

# 全球资讯 7×24
news = eastmoney_global_news(page_size=50)
```

## Quick Start — 基础数据层

```python
from src.api.astock_helpers import (
    eastmoney_stock_info,        # 东财个股基本面
    sina_financial_report_v2,    # 新浪财报三表 (V3.2.2 修复)
)
from mootdx.quotes import Quotes

client = Quotes.factory(market="std")

# mootdx 财务快照 (37 字段)
fin = client.finance(symbol="688017")

# mootdx F10 (9 大类文本)
text = client.F10(symbol="688017", name="公司概况")

# 东财个股基本面
info = eastmoney_stock_info("688017")

# 新浪财报三表: "fzb"(资产负债表) / "lrb"(利润表) / "llb"(现金流量表)
lrb = sina_financial_report_v2("600519", "lrb")
```

## Quick Start — 公告层

```python
from src.api.astock_helpers import cninfo_announcements_v2

# 巨潮公告 (V3.2.2 修复: 动态 orgId 映射)
anns = cninfo_announcements_v2("688017", page_size=10)
# Returns: [{title, type, date, url}, ...]
```

## Data Source Priority

| 优先级 | 数据源 | 用途 | 封 IP 风险 |
|--------|--------|------|-----------|
| 1 | **mootdx** (TCP) | K 线 + 盘口 + 财务 + F10 | 不封 IP |
| 2 | **腾讯财经** (HTTP) | 实时 PE/PB/市值/涨跌停 | 不封 IP |
| 3 | 同花顺/百度/新浪 | EPS/热点/北向/K 线 MA/财报 | 低 |
| 4 | **东财** (HTTP) | 龙虎榜/解禁/融资/资金流/研报/新闻 | 有风控，已限流 |

## API Reference — Complete List

### 行情层
| Function | Description |
|----------|-------------|
| `tencent_quote(codes)` | 批量实时行情 + PE/PB/市值 |
| `tencent_full_snapshot()` | 全市场快照 (~5600 只) |
| `baidu_kline_with_ma(code)` | K 线 + MA5/MA10/MA20 |

### 研报层
| Function | Description |
|----------|-------------|
| `eastmoney_reports(code)` | 研报列表 + 评级 + EPS 预测 |
| `ths_eps_forecast(code)` | 同花顺一致预期 EPS |

### 信号层
| Function | Description |
|----------|-------------|
| `ths_hot_reason(date)` | 当日强势股 + 题材归因 |
| `hsgt_realtime()` | 北向资金分钟级 (沪深股通) |
| `eastmoney_concept_blocks_v2(code)` | 概念板块归属 |
| `eastmoney_fund_flow_minute(code)` | 个股资金流分钟级 |
| `dragon_tiger_board(code, date)` | 龙虎榜席位 + 机构动向 |
| `daily_dragon_tiger(date)` | 全市场龙虎榜 |
| `lockup_expiry(code, date)` | 限售解禁日历 |
| `industry_comparison(top_n)` | 行业板块排名 |

### 资金面 / 筹码层
| Function | Description |
|----------|-------------|
| `margin_trading(code)` | 融资融券明细 |
| `block_trade(code)` | 大宗交易 |
| `holder_num_change(code)` | 股东户数变化 |
| `dividend_history(code)` | 分红送转历史 |
| `stock_fund_flow_120d(code)` | 个股资金流 120 日 |

### 新闻层
| Function | Description |
|----------|-------------|
| `eastmoney_stock_news(code)` | 东财个股新闻 |
| `eastmoney_global_news()` | 东财全球资讯 7×24 |

### 基础数据层
| Function | Description |
|----------|-------------|
| `eastmoney_stock_info(code)` | 东财个股基本面 |
| `sina_financial_report_v2(code, type)` | 新浪财报三表 |

### 公告层
| Function | Description |
|----------|-------------|
| `cninfo_announcements_v2(code)` | 巨潮公告 (动态 orgId) |

### 通用工具
| Function | Description |
|----------|-------------|
| `get_prefix(code)` | 股票代码 → 市场前缀 |
| `em_get(url, params)` | 东财统一限流请求入口 |

## Built-in Backtest Loader

`backtest/loaders/astock_loader.py` 注册为 **首选** A 股数据源，使用 mootdx TCP 获取 OHLCV + 可选腾讯基本面补充。

Fallback chain: `[astock, mootdx, akshare]`

```python
from backtest.runner import run
result = run(strategy=..., source="auto")   # auto → astock for A-shares
result = run(strategy=..., source="astock")  # explicit
```

## Known Limitations

| Limitation | Workaround |
|------------|------------|
| 腾讯行情仅实时 (无历史) | 用 mootdx `get_k_data()` / `bars()` |
| mootdx 不支持北交所 | Fallback 到 akshare |
| 东财有风控 (限流 0.5s) | `em_get()` 自动限流 |
| 腾讯 PE/PB 仅当日快照 | 历史 PE 时序用 tushare `daily_basic` |
| mootdx 返回前复权数据 | 不复权用 tushare/akshare |
| 部分大陆住宅 IP 被 push2 间歇封锁 | 换网络/重试/调大 `EM_MIN_INTERVAL` |

## Reference

- Helper module: `src/api/astock_helpers.py`
- Backtest loader: `backtest/loaders/astock_loader.py`
- Tencent Finance API: `https://qt.gtimg.cn/q=`
- mootdx docs: https://www.mootdx.com/
- a-stock-data (upstream): https://github.com/simonlin1212/a-stock-data
