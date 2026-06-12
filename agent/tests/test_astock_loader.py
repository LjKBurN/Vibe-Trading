"""Contract test for the fork-local ``astock`` loader (mootdx TCP + Tencent)."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.loaders.astock_loader import DataLoader, _is_a_share, _is_bj
from tests.loader_contract import assert_loader_contract


class _FakeStdQuotes:
    """Offline stand-in for mootdx ``Quotes.factory(market="std")``."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame
        self.calls: list[dict] = []

    def get_k_data(self, *, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls.append(
            {"code": code, "start_date": start_date, "end_date": end_date}
        )
        return self._frame.copy()


@pytest.fixture
def fake_client() -> _FakeStdQuotes:
    frame = pd.DataFrame(
        {
            "open": [10.0, 11.0],
            "high": [12.0, 13.0],
            "low": [9.0, 10.0],
            "close": [11.0, 12.0],
            "vol": [100.0, 120.0],
        },
        index=pd.to_datetime(["2025-01-02", "2025-01-03"]),
    )
    return _FakeStdQuotes(frame)


def test_fetch_daily_normalizes_and_satisfies_contract(
    fake_client: _FakeStdQuotes,
) -> None:
    loader = DataLoader()
    loader._client = fake_client  # bypass mootdx client construction

    out = loader.fetch(["000001.SZ"], "2025-01-02", "2025-01-03")

    assert "000001.SZ" in out
    df = out["000001.SZ"]
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.name == "trade_date"
    assert_loader_contract(df, context="astock daily")
    # Symbol stripping: mootdx is called with the bare 6-digit code.
    assert fake_client.calls[0]["code"] == "000001"


def test_symbol_detection_helpers() -> None:
    assert _is_a_share("000001.SZ")
    assert _is_a_share("600519")
    assert not _is_a_share("AAPL")
    assert _is_bj("830799.BJ")
    assert not _is_bj("600519")
