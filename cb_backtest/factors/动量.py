"""动量因子。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from cb_backtest.events import Bar
from cb_backtest.factors.base import Factor


@dataclass(slots=True)
class MomentumState:
    closes: deque[float] = field(default_factory=deque)


class MomentumFactor(Factor):
    """当前价格相对前 N 个事件价格的收益率。"""

    def __init__(self, window: int = 20):
        super().__init__(window=window)
        self.window = int(window)

    def make_state(self) -> MomentumState:
        return MomentumState(closes=deque(maxlen=self.window + 1))

    def update_symbol(self, bar: Bar, state: MomentumState, history: dict[str, deque[Bar]]) -> float | None:
        if bar.close is None:
            return None
        state.closes.append(float(bar.close))
        if len(state.closes) <= self.window:
            return None
        old_price = state.closes[0]
        if old_price == 0:
            return None
        return float(float(bar.close) / old_price - 1.0)


FACTOR_CLASS = MomentumFactor
