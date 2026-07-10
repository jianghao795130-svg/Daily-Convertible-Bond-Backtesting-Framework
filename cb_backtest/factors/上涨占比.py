"""上涨占比因子。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from cb_backtest.events import Bar
from cb_backtest.factors.base import Factor


@dataclass(slots=True)
class UpBarRatioState:
    prev_close: float | None = None
    flags: deque[int] = field(default_factory=deque)
    positive_count: int = 0


class UpBarRatioFactor(Factor):
    """最近 N 个事件中上涨事件的占比。"""

    def __init__(self, window: int = 20, params: int | None = None):
        if params is not None:
            window = int(params)
        super().__init__(window=window)
        self.window = int(window)

    def make_state(self) -> UpBarRatioState:
        return UpBarRatioState(flags=deque(maxlen=self.window))

    def update_symbol(
        self,
        bar: Bar,
        state: UpBarRatioState,
        history: dict[str, deque[Bar]],
    ) -> float | None:
        if bar.close in (None, 0):
            return None
        close = float(bar.close)
        if state.prev_close is None:
            state.prev_close = close
            return None

        flag = 1 if close > float(state.prev_close) else 0
        if len(state.flags) >= self.window:
            state.positive_count -= state.flags.popleft()

        state.flags.append(flag)
        state.positive_count += flag
        state.prev_close = close

        if len(state.flags) < self.window:
            return None
        return float(state.positive_count / len(state.flags))


FACTOR_CLASS = UpBarRatioFactor
