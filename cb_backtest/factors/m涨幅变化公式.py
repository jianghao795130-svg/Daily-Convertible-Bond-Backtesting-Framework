"""m 涨幅变化公式因子。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from cb_backtest.events import Bar
from cb_backtest.factors.base import Factor


@dataclass(slots=True)
class MeanReturnFormulaState:
    trade_date: str | None = None
    prev_close: float | None = None
    closes: deque[float] = field(default_factory=deque)


class MeanReturnFormulaFactor(Factor):
    """复现 ((最新价 - shift(N)) / (前收盘价 * N)) * 100。"""

    def __init__(self, window: int = 1):
        super().__init__(window=window)
        self.window = int(window)

    def make_state(self) -> MeanReturnFormulaState:
        return MeanReturnFormulaState(closes=deque(maxlen=self.window + 1))

    def update_symbol(self, bar: Bar, state: MeanReturnFormulaState, history: dict[str, deque[Bar]]) -> float | None:
        bars = history[bar.symbol]
        if bar.close is None:
            return None

        if state.trade_date != bar.trade_date:
            state.trade_date = bar.trade_date
            state.prev_close = self._prev_close_of_day(bars)
            state.closes = deque(maxlen=self.window + 1)

        state.closes.append(float(bar.close))
        if len(state.closes) <= self.window:
            return None
        if state.prev_close in (None, 0):
            return None
        old_price = state.closes[0]
        value = ((float(bar.close) - float(old_price)) / (float(state.prev_close) * self.window)) * 100
        return round(value, 2)

    @staticmethod
    def _prev_close_of_day(bars: deque[Bar]) -> float | None:
        if not bars:
            return None
        current_trade_date = bars[-1].trade_date
        for item in reversed(bars):
            if item.trade_date and current_trade_date and item.trade_date != current_trade_date:
                if item.close is not None:
                    return float(item.close)
        return None


FACTOR_CLASS = MeanReturnFormulaFactor
