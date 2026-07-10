"""短长动量差因子。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from cb_backtest.events import Bar
from cb_backtest.factors.base import Factor


@dataclass(slots=True)
class ShortLongMomentumSpreadState:
    closes: deque[float] = field(default_factory=deque)


class ShortLongMomentumSpreadFactor(Factor):
    """短周期动量减去长周期动量。"""

    def __init__(self, params: tuple[int, int] = (5, 20)):
        super().__init__(params=params)
        self.short_window = int(params[0])
        self.long_window = int(params[1])
        if self.short_window <= 0 or self.long_window <= self.short_window:
            raise ValueError("短长动量差参数必须满足 0 < short_window < long_window")

    def make_state(self) -> ShortLongMomentumSpreadState:
        return ShortLongMomentumSpreadState(closes=deque(maxlen=self.long_window + 1))

    def update_symbol(
        self,
        bar: Bar,
        state: ShortLongMomentumSpreadState,
        history: dict[str, deque[Bar]],
    ) -> float | None:
        if bar.close in (None, 0):
            return None
        close = float(bar.close)
        state.closes.append(close)
        if len(state.closes) <= self.long_window:
            return None

        short_base = state.closes[-self.short_window - 1]
        long_base = state.closes[0]
        if short_base == 0 or long_base == 0:
            return None

        short_momentum = close / short_base - 1.0
        long_momentum = close / long_base - 1.0
        return float(short_momentum - long_momentum)


FACTOR_CLASS = ShortLongMomentumSpreadFactor
