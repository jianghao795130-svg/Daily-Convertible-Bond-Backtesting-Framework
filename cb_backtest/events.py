"""事件和行情数据结构定义。

事件驱动框架的核心思想是：所有模块通过标准事件通信。
行情进入系统时是 MarketEvent，策略输出 SignalEvent，账户把信号转成
OrderEvent，成交后生成 FillEvent。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """框架内部支持的事件类型。"""

    MARKET = "MARKET"
    SIGNAL = "SIGNAL"
    ORDER = "ORDER"
    FILL = "FILL"


class OrderSide(str, Enum):
    """订单方向。"""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """订单类型；当前撮合先实现市价单，限价单预留扩展。"""

    MARKET = "MARKET"
    LIMIT = "LIMIT"


@dataclass(slots=True)
class MarketEvent:
    """单条行情事件。

    data 中固定放入：
    - bar: 标准化后的 Bar
    - raw: 原始行情行，方便未来做更细的 tick/盘口因子
    """

    timestamp: datetime
    symbol: str
    data: dict[str, Any]
    frequency: str
    type: EventType = EventType.MARKET


@dataclass(slots=True)
class SignalEvent:
    """策略输出的目标仓位信号。

    策略不直接下单，而是表达“某个标的应调整到多少仓位”。
    Broker 会根据当前账户权益和持仓把它转换成具体买卖数量。
    """

    timestamp: datetime
    symbol: str
    target_percent: float | None = None
    target_value: float | None = None
    execution_price: float | None = None
    reason: str = ""
    type: EventType = EventType.SIGNAL


@dataclass(slots=True)
class OrderEvent:
    """账户模块生成的订单事件。"""

    timestamp: datetime
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    execution_price: float | None = None
    reason: str = ""
    type: EventType = EventType.ORDER


@dataclass(slots=True)
class FillEvent:
    """成交事件，记录真实影响账户的交易结果。"""

    timestamp: datetime
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    commission: float
    slippage: float
    reason: str = ""
    type: EventType = EventType.FILL


@dataclass(slots=True)
class Bar:
    """框架内部统一行情对象。

    分钟数据会映射成标准 OHLCV；tick 数据会把成交价同时填入
    open/high/low/close，从而复用同一套因子、策略、撮合逻辑。
    """

    timestamp: datetime
    symbol: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    amount: float | None = None
    suspended: bool = False
    trade_date: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def price(self) -> float | None:
        """撮合默认价格；优先用 close，没有 close 时退化到 open。"""

        return self.close if self.close is not None else self.open
