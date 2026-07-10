"""动量稳定性因子。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import sqrt

from cb_backtest.events import Bar
from cb_backtest.factors.base import Factor


@dataclass(slots=True)
class MomentumStabilityState:
    prev_close: float | None = None
    returns: deque[float] = field(default_factory=deque)
    return_sum: float = 0.0
    return_sq_sum: float = 0.0


class MomentumStabilityFactor(Factor):
    """最近 N 个单步收益的均值 / 标准差。"""

    def __init__(self, window: int = 20, params: int | None = None):
        if params is not None:
            window = int(params)
        super().__init__(window=window)
        self.window = int(window)

    def make_state(self) -> MomentumStabilityState:
        return MomentumStabilityState(returns=deque(maxlen=self.window))

    def update_symbol(
        self,
        bar: Bar,
        state: MomentumStabilityState,
        history: dict[str, deque[Bar]],
    ) -> float | None:
        if bar.close in (None, 0):
            return None
        close = float(bar.close)
        if state.prev_close in (None, 0):
            state.prev_close = close
            return None

        ret = close / float(state.prev_close) - 1.0
        if len(state.returns) >= self.window:
            old = state.returns.popleft()
            state.return_sum -= old
            state.return_sq_sum -= old * old

        state.returns.append(ret)
        state.return_sum += ret
        state.return_sq_sum += ret * ret
        state.prev_close = close

        count = len(state.returns)
        if count < self.window:
            return None

        mean_ret = state.return_sum / count
        variance = max(state.return_sq_sum / count - mean_ret * mean_ret, 0.0)
        std_ret = sqrt(variance)
        if std_ret == 0:
            return None
        return float(mean_ret / std_ret)


FACTOR_CLASS = MomentumStabilityFactor
