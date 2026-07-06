"""涨幅因子。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from cb_backtest.events import Bar
from cb_backtest.factors.base import Factor


@dataclass(slots=True)
class ReturnPercentState:
    trade_date: str | None = None
    prev_close: float | None = None


class ReturnPercentFactor(Factor):
    """(最新价 / 前收盘价 - 1) * 100。"""

    def make_state(self) -> ReturnPercentState:
        return ReturnPercentState()

    def update_symbol(self, bar: Bar, state: ReturnPercentState, history: dict[str, deque[Bar]]) -> float | None:
        bars = history[bar.symbol]
        if bar.close is None or not bars:
            return None
        if state.trade_date != bar.trade_date:
            state.trade_date = bar.trade_date
            state.prev_close = None
            for item in reversed(bars):
                if item.trade_date and bar.trade_date and item.trade_date != bar.trade_date:
                    if item.close is not None:
                        state.prev_close = float(item.close)
                        break
        if state.prev_close in (None, 0):
            return None
        return round((float(bar.close) / float(state.prev_close) - 1.0) * 100, 2)


FACTOR_CLASS = ReturnPercentFactor
