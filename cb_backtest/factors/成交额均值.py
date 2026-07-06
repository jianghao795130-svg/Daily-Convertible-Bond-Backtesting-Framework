"""成交额均值因子。"""

from __future__ import annotations

from collections import deque

from cb_backtest.events import Bar
from cb_backtest.factors._rolling_window_state import DailyRollingWindowTracker
from cb_backtest.factors.base import Factor


class MeanAmountFactor(Factor):
    """最近 N 根 bar 的平均成交额。"""

    def __init__(self, window: int = 5):
        super().__init__(window=window)
        self.window = int(window)
        self.tracker = DailyRollingWindowTracker(window=self.window)

    def update_symbol(self, bar: Bar, state: dict, history: dict[str, deque[Bar]]) -> float | None:
        state = self.tracker.update(
            symbol=bar.symbol,
            trade_date=bar.trade_date,
            value=float(bar.amount) if bar.amount is not None else None,
        )
        if state is None or len(state.values) < self.window:
            return None
        return float(state.value_sum / len(state.values))


FACTOR_CLASS = MeanAmountFactor
