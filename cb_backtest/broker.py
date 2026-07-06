"""Broker and simplified execution logic."""

from __future__ import annotations

from dataclasses import dataclass, field

from cb_backtest.events import Bar, FillEvent, OrderEvent, OrderSide, SignalEvent


@dataclass(slots=True)
class Position:
    """Single-symbol position state."""

    symbol: str
    quantity: int = 0
    avg_cost: float = 0.0

    def market_value(self, price: float | None) -> float:
        return self.quantity * (price or 0.0)


@dataclass(slots=True)
class Broker:
    """Backtest account state and execution."""

    initial_cash: float
    commission_rate: float = 1.2 / 10000
    min_commission: float = 0.0
    slippage_bps: float = 0.0
    lot_size: int = 10
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    last_price: dict[str, float] = field(default_factory=dict)
    fills: list[FillEvent] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = float(self.initial_cash)

    def update_market(self, bar: Bar) -> None:
        if bar.price is not None:
            self.last_price[bar.symbol] = bar.price
        self.equity_curve.append(
            {
                "timestamp": bar.timestamp,
                "cash": self.cash,
                "market_value": self.market_value(),
                "equity": self.equity(),
            }
        )

    def signal_to_order(self, signal: SignalEvent, bar: Bar | None = None) -> OrderEvent | None:
        """Convert a target-position signal into a concrete order."""

        price = self._reference_price(bar) if bar is not None else self.last_price.get(signal.symbol)
        if not price or price <= 0:
            return None
        current_qty = self.positions.get(signal.symbol, Position(signal.symbol)).quantity

        if signal.target_percent is not None:
            target_value = self.equity() * signal.target_percent
        elif signal.target_value is not None:
            target_value = signal.target_value
        else:
            return None

        target_qty = self._round_lot(int(target_value / price))
        diff_qty = target_qty - current_qty
        if diff_qty == 0:
            return None
        return OrderEvent(
            timestamp=signal.timestamp,
            symbol=signal.symbol,
            side=OrderSide.BUY if diff_qty > 0 else OrderSide.SELL,
            quantity=abs(diff_qty),
            execution_price=signal.execution_price,
            reason=signal.reason,
        )

    def execute_order(self, order: OrderEvent, bar: Bar) -> FillEvent | None:
        """Execute against the provided bar."""

        base_price, used_explicit_price = self._resolve_execution_price(order, bar)
        if bar.suspended or base_price is None or base_price <= 0:
            return None

        side_mult = 1 if order.side == OrderSide.BUY else -1
        slip = 0.0
        price = float(base_price)
        if not used_explicit_price:
            slip = float(base_price) * self.slippage_bps / 10000
            price = float(base_price) + side_mult * slip
        quantity = order.quantity

        if order.side == OrderSide.BUY:
            max_affordable = int(self.cash / (price * (1 + self.commission_rate)))
            quantity = min(quantity, self._round_lot(max_affordable))
        else:
            quantity = min(quantity, self.positions.get(order.symbol, Position(order.symbol)).quantity)

        if quantity <= 0:
            return None

        turnover = quantity * price
        commission = max(turnover * self.commission_rate, self.min_commission) if turnover > 0 else 0.0
        if order.side == OrderSide.BUY:
            self.cash -= turnover + commission
            self._increase_position(order.symbol, quantity, price)
        else:
            self.cash += turnover - commission
            self._decrease_position(order.symbol, quantity)
        self.last_price[order.symbol] = price

        fill = FillEvent(
            timestamp=bar.timestamp,
            symbol=order.symbol,
            side=order.side,
            quantity=quantity,
            price=price,
            commission=commission,
            slippage=abs(slip) * quantity,
            reason=order.reason,
        )
        self.fills.append(fill)
        return fill

    def market_value(self) -> float:
        return sum(pos.market_value(self.last_price.get(symbol)) for symbol, pos in self.positions.items())

    def equity(self) -> float:
        return self.cash + self.market_value()

    def _increase_position(self, symbol: str, quantity: int, price: float) -> None:
        pos = self.positions.setdefault(symbol, Position(symbol))
        total_cost = pos.avg_cost * pos.quantity + price * quantity
        pos.quantity += quantity
        pos.avg_cost = total_cost / pos.quantity if pos.quantity else 0.0

    def _decrease_position(self, symbol: str, quantity: int) -> None:
        pos = self.positions.setdefault(symbol, Position(symbol))
        pos.quantity -= quantity
        if pos.quantity <= 0:
            pos.quantity = 0
            pos.avg_cost = 0.0

    def _round_lot(self, quantity: int) -> int:
        if self.lot_size <= 1:
            return max(quantity, 0)
        return max(quantity // self.lot_size * self.lot_size, 0)

    @staticmethod
    def _reference_price(bar: Bar | None) -> float | None:
        if bar is None:
            return None
        if bar.open is not None and bar.open > 0:
            return float(bar.open)
        if bar.price is not None and bar.price > 0:
            return float(bar.price)
        return None

    def _resolve_execution_price(self, order: OrderEvent, bar: Bar) -> tuple[float | None, bool]:
        market_price = self._reference_price(bar)
        if market_price is not None:
            return market_price, False
        if order.execution_price is not None and order.execution_price > 0:
            return float(order.execution_price), True
        if bar.price is not None and bar.price > 0:
            return float(bar.price), False
        return None, False
