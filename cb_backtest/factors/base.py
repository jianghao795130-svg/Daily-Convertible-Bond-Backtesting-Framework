"""因子基类与因子注册表。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict, deque
from typing import Any

from cb_backtest.events import Bar


class Factor(ABC):
    """所有因子都继承这个基类。

    当前框架默认采用“按 symbol 保存状态、按事件增量更新”的模式：

    1. 每个因子实例内部自动维护 `symbol -> state`
    2. 每来一个新事件，只更新当前 symbol 的状态
    3. 子类只需要实现：
       - `make_state()`：创建该因子的默认状态
       - `update_symbol()`：如何基于当前事件增量更新并返回因子值

    这样以后新增因子时，默认就是增量更新思路，而不是在 `update()` 里反复重扫历史。
    """

    name = "factor"

    def __init__(self, **params: Any):
        self.params = params
        self.symbol_states: dict[str, Any] = {}

    def update(self, bar: Bar, history: dict[str, deque[Bar]]) -> float | int | bool | None:
        """输入最新事件，自动取出该 symbol 的状态并增量更新。"""

        state = self.symbol_states.get(bar.symbol)
        if state is None:
            state = self.make_state()
            self.symbol_states[bar.symbol] = state
        return self.update_symbol(bar, state, history)

    def make_state(self) -> Any:
        """创建单个 symbol 的默认状态。"""

        return {}

    @abstractmethod
    def update_symbol(
        self,
        bar: Bar,
        state: Any,
        history: dict[str, deque[Bar]],
    ) -> float | int | bool | None:
        """基于当前 symbol 的状态执行一次增量更新并返回因子值。"""


class FactorRegistry:
    """保存所有因子的最新快照。"""

    def __init__(self) -> None:
        self.values: dict[str, dict[str, float | int | bool | None]] = defaultdict(dict)

    def set_value(self, factor_name: str, symbol: str, value: float | int | bool | None) -> None:
        self.values[factor_name][symbol] = value

    def get_value(self, factor_name: str, symbol: str) -> float | int | bool | None:
        return self.values.get(factor_name, {}).get(symbol)

    def snapshot(self, factor_name: str) -> dict[str, float | int | bool | None]:
        return dict(self.values.get(factor_name, {}))
