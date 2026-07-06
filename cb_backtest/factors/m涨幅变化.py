"""m 涨幅变化因子。"""

from __future__ import annotations

from collections import deque

from cb_backtest.events import Bar
from cb_backtest.factors._mean_trend_tracker import MeanTrendTracker
from cb_backtest.factors.base import Factor


class MeanReturnChangeFactor(Factor):
    """mN 均价涨幅相对前一事件的变化值。"""

    def __init__(self, window: int = 3):
        super().__init__(window=window)
        self.window = int(window)
        self.tracker = MeanTrendTracker(window=self.window)

    def update_symbol(self, bar: Bar, state: dict, history: dict[str, deque[Bar]]) -> float | None:
        bars = history[bar.symbol]
        trend_state = self.tracker.update(bar, bars)
        if trend_state is None or trend_state.last_change is None:
            return None
        return float(trend_state.last_change)


FACTOR_CLASS = MeanReturnChangeFactor
