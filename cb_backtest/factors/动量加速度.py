"""动量加速度因子。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from cb_backtest.events import Bar
from cb_backtest.factors.base import Factor


@dataclass(slots=True)
class MomentumAccelerationState:
    closes: deque[float] = field(default_factory=deque)


class MomentumAccelerationFactor(Factor):
    """当前动量相对上一时刻动量的变化。"""

    def __init__(self, window: int = 10, params: int | None = None):
        if params is not None:
            window = int(params)
        super().__init__(window=window)
        self.window = int(window)

    def make_state(self) -> MomentumAccelerationState:
        return MomentumAccelerationState(closes=deque(maxlen=self.window + 2))

    def update_symbol(
        self,
        bar: Bar,
        state: MomentumAccelerationState,
        history: dict[str, deque[Bar]],
    ) -> float | None:
        if bar.close in (None, 0):
            return None
        close = float(bar.close)
        state.closes.append(close)
        if len(state.closes) <= self.window + 1:
            return None

        current_base = state.closes[-self.window - 1]
        prev_close = state.closes[-2]
        prev_base = state.closes[0]
        if current_base == 0 or prev_base == 0:
            return None

        current_momentum = close / current_base - 1.0
        previous_momentum = prev_close / prev_base - 1.0
        return float(current_momentum - previous_momentum)


FACTOR_CLASS = MomentumAccelerationFactor
