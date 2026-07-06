"""成交额缩量因子。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from cb_backtest.events import Bar
from cb_backtest.factors.base import Factor


@dataclass(slots=True)
class AmountShrinkState:
    short_values: deque[float] = field(default_factory=deque)
    long_values: deque[float] = field(default_factory=deque)
    short_sum: float = 0.0
    long_sum: float = 0.0


class AmountShrinkFactor(Factor):
    """短窗口成交额均值 / 长窗口成交额均值。

    参数示例：
    - (1, 24): 最近 1 根 bar 的平均成交额除以最近 24 根 bar 的平均成交额。
    - (5, 24): 最近 5 根 bar 的平均成交额除以最近 24 根 bar 的平均成交额。
    """

    name = "成交额缩量因子"

    def __init__(self, params: tuple[int, int] = (1, 24)):
        super().__init__(params=params)
        self.short_window = int(params[0])
        self.long_window = int(params[1])

    def make_state(self) -> AmountShrinkState:
        return AmountShrinkState(
            short_values=deque(maxlen=self.short_window),
            long_values=deque(maxlen=self.long_window),
        )

    def update_symbol(self, bar: Bar, state: AmountShrinkState, history: dict[str, deque[Bar]]) -> float | None:
        """返回短成交额均值与长成交额均值的比值。"""

        if bar.amount is None:
            return None
        amount = float(bar.amount)
        if len(state.short_values) >= self.short_window:
            state.short_sum -= state.short_values.popleft()
        state.short_values.append(amount)
        state.short_sum += amount

        if len(state.long_values) >= self.long_window:
            state.long_sum -= state.long_values.popleft()
        state.long_values.append(amount)
        state.long_sum += amount

        if len(state.short_values) < self.short_window or len(state.long_values) < self.long_window:
            return None
        long_mean = state.long_sum / len(state.long_values)
        if long_mean == 0:
            return None
        short_mean = state.short_sum / len(state.short_values)
        return short_mean / long_mean


FACTOR_CLASS = AmountShrinkFactor
