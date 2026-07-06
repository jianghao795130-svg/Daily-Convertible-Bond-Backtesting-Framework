"""滚动窗口类因子的共享增量状态。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass(slots=True)
class RollingWindowState:
    trade_date: str | None = None
    values: deque[float] = field(default_factory=deque)
    value_sum: float = 0.0


class DailyRollingWindowTracker:
    """按交易日重置的固定窗口滚动和。"""

    def __init__(self, window: int):
        self.window = int(window)
        self.states: dict[str, RollingWindowState] = {}

    def update(self, symbol: str, trade_date: str | None, value: float | None) -> RollingWindowState | None:
        if value is None:
            return None

        state = self.states.setdefault(symbol, RollingWindowState())
        if state.trade_date != trade_date:
            state.trade_date = trade_date
            state.values = deque()
            state.value_sum = 0.0

        if len(state.values) >= self.window:
            state.value_sum -= state.values.popleft()

        state.values.append(float(value))
        state.value_sum += float(value)
        return state
