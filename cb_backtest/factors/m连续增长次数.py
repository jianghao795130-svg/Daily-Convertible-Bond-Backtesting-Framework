"""m 连续增长次数因子。"""

from __future__ import annotations

from collections import deque

from cb_backtest.events import Bar
from cb_backtest.factors._mean_trend_tracker import MeanTrendTracker
from cb_backtest.factors.base import Factor


class MeanPositiveStreakCountFactor(Factor):
    """连续非负涨幅变化区间内的连续增长次数。"""

    def __init__(self, window: int = 3):
        super().__init__(window=window)
        self.window = int(window)
        self.tracker = MeanTrendTracker(window=self.window)

    def update_symbol(self, bar: Bar, state: dict, history: dict[str, deque[Bar]]) -> float | None:
        bars = history[bar.symbol]
        state = self.tracker.update(bar, bars)
        if state is None:
            return None
        if state.last_change is None:
            return None
        return float(state.streak)


FACTOR_CLASS = MeanPositiveStreakCountFactor
