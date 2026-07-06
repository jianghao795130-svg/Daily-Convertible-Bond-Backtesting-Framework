"""m 系列趋势因子的共享增量状态。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from cb_backtest.events import Bar


@dataclass(slots=True)
class MeanTrendRuntimeState:
    trade_date: str | None = None
    prev_close: float | None = None
    closes: deque[float] = field(default_factory=deque)
    close_sum: float = 0.0
    cumulative: float = 0.0
    streak: int = 0
    last_change: float | None = None
    last_speed: float | None = None


class MeanTrendTracker:
    """按帖子 rolling(min_periods=1) 口径增量更新 m 系列因子。"""

    def __init__(self, window: int):
        self.window = int(window)
        self.states: dict[str, MeanTrendRuntimeState] = {}

    def update(self, bar: Bar, bars: deque[Bar]) -> MeanTrendRuntimeState | None:
        if bar.close is None:
            return None

        state = self.states.setdefault(bar.symbol, MeanTrendRuntimeState())
        if state.trade_date != bar.trade_date:
            self._reset_for_new_day(state, bar, bars)

        if state.prev_close in (None, 0):
            state.last_change = None
            state.last_speed = None
            return state

        prev_mean_return = None
        if state.closes:
            prev_mean_return = (state.close_sum / len(state.closes) / float(state.prev_close) - 1.0) * 100.0

        close_value = float(bar.close)
        if len(state.closes) >= self.window:
            old_value = state.closes.popleft()
            state.close_sum -= old_value

        state.closes.append(close_value)
        state.close_sum += close_value

        current_mean_return = (state.close_sum / len(state.closes) / float(state.prev_close) - 1.0) * 100.0
        change = 0.0 if prev_mean_return is None else current_mean_return - prev_mean_return

        if change >= 0:
            state.cumulative += change
            state.streak += 1
        else:
            state.cumulative = 0.0
            state.streak = 0

        state.last_change = float(change)
        state.last_speed = float(state.cumulative / state.streak) if state.streak > 0 else 0.0
        return state

    def _reset_for_new_day(self, state: MeanTrendRuntimeState, bar: Bar, bars: deque[Bar]) -> None:
        state.trade_date = bar.trade_date
        state.prev_close = self._find_previous_day_close(bars, bar.trade_date)
        state.closes = deque()
        state.close_sum = 0.0
        state.cumulative = 0.0
        state.streak = 0
        state.last_change = None
        state.last_speed = None

    @staticmethod
    def _find_previous_day_close(bars: deque[Bar], current_trade_date: str | None) -> float | None:
        for item in reversed(bars):
            if item.trade_date and current_trade_date and item.trade_date != current_trade_date:
                if item.close is not None:
                    return float(item.close)
        return None
