"""均线偏离因子。"""

from __future__ import annotations

from collections import deque

from cb_backtest.events import Bar
from cb_backtest.factors._rolling_window_state import DailyRollingWindowTracker
from cb_backtest.factors.base import Factor


class BiasFactor(Factor):
    """价格相对均线的偏离度。"""

    def __init__(self, window: int = 5):
        super().__init__(window=window)
        self.window = int(window)
        self.tracker = DailyRollingWindowTracker(window=self.window)

    def update_symbol(self, bar: Bar, state: dict, history: dict[str, deque[Bar]]) -> float | None:
        if bar.close in (None, 0):
            return None

        state = self.tracker.update(
            symbol=bar.symbol,
            trade_date=bar.trade_date,
            value=float(bar.close),
        )
        if state is None or len(state.values) < self.window:
            return None

        ma = state.value_sum / len(state.values)
        if ma == 0:
            return None
        return float(bar.close / ma - 1.0)


FACTOR_CLASS = BiasFactor
