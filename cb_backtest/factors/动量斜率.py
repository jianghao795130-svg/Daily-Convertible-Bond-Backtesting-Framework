"""动量斜率因子。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from cb_backtest.events import Bar
from cb_backtest.factors.base import Factor


@dataclass(slots=True)
class MomentumSlopeState:
    closes: deque[float] = field(default_factory=deque)


class MomentumSlopeFactor(Factor):
    """最近 N 个收盘价线性回归斜率的归一化值。"""

    def __init__(self, window: int = 20, params: int | None = None):
        if params is not None:
            window = int(params)
        super().__init__(window=window)
        self.window = int(window)
        self.x_sum = sum(range(self.window))
        self.x_sq_sum = sum(i * i for i in range(self.window))

    def make_state(self) -> MomentumSlopeState:
        return MomentumSlopeState(closes=deque(maxlen=self.window))

    def update_symbol(
        self,
        bar: Bar,
        state: MomentumSlopeState,
        history: dict[str, deque[Bar]],
    ) -> float | None:
        if bar.close in (None, 0):
            return None
        state.closes.append(float(bar.close))
        if len(state.closes) < self.window:
            return None

        closes = list(state.closes)
        y_sum = sum(closes)
        xy_sum = sum(idx * price for idx, price in enumerate(closes))
        denominator = self.window * self.x_sq_sum - self.x_sum * self.x_sum
        if denominator == 0:
            return None

        slope = (self.window * xy_sum - self.x_sum * y_sum) / denominator
        mean_price = y_sum / self.window
        if mean_price == 0:
            return None
        return float(slope / mean_price)


FACTOR_CLASS = MomentumSlopeFactor
