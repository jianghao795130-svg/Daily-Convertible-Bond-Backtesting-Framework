"""策略基类。

策略接收行情、历史窗口、因子快照和账户状态，输出目标仓位信号。
策略不直接修改账户，账户变化统一交给 Broker。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque

from cb_backtest.broker import Broker
from cb_backtest.events import Bar, FillEvent, SignalEvent
from cb_backtest.factors.base import FactorRegistry


class Strategy(ABC):
    """所有自定义策略都继承这个基类。"""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def on_bar(
        self,
        bar: Bar,
        history: dict[str, deque[Bar]],
        factors: FactorRegistry,
        broker: Broker,
    ) -> list[SignalEvent]:
        """处理一根行情 bar，并返回一组目标仓位信号。"""

    def on_fill(self, fill: FillEvent, broker: Broker) -> None:
        """成交回报钩子；默认策略可不处理。"""
