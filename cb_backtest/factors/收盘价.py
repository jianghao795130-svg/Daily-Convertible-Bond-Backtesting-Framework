"""价格类基础因子。"""

from __future__ import annotations

from collections import deque

from cb_backtest.events import Bar
from cb_backtest.factors.base import Factor


class ClosePriceFactor(Factor):
    """当前收盘价因子，对应配置名“收盘价”。"""

    def update_symbol(self, bar: Bar, state: dict, history: dict[str, deque[Bar]]) -> float | None:
        """返回当前 bar 的收盘价。"""

        return bar.close


FACTOR_CLASS = ClosePriceFactor
