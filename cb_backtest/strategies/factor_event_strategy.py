"""通用事件驱动因子策略。"""

from __future__ import annotations

import operator
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, Callable

from cb_backtest.broker import Broker
from cb_backtest.events import Bar, FillEvent, OrderSide, SignalEvent
from cb_backtest.factor_config import parse_filter_factor, parse_score_factor, parse_timing_factor
from cb_backtest.factors.base import FactorRegistry
from cb_backtest.strategies.base import Strategy

OPS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


@dataclass(slots=True)
class PositionRuntimeState:
    entry_price: float | None = None
    entry_time: datetime | None = None
    max_price_after_entry: float | None = None
    bars_since_entry: int = 0
    last_buy_signal: float = 0.0
    buy_count: int = 0
    realized_profit_sum: float = 0.0
    realized_loss_count: int = 0
    position_weight: float = 1.0
    last_entry_trade_date: str | None = None


@dataclass(slots=True)
class StrategyRuntimeState:
    current_trade_date: str | None = None
    selected_symbols: set[str] = field(default_factory=set)
    last_selection_ts: datetime | None = None
    last_force_flat_date: str | None = None
    symbol_states: dict[str, PositionRuntimeState] = field(default_factory=dict)


class FactorEventStrategy(Strategy):
    """按过滤因子、评分因子、择时因子驱动的通用策略。"""

    def __init__(
        self,
        name: str,
        factor_list: list[tuple] | None = None,
        filter_list: list[tuple] | None = None,
        stock_timing_list: list[dict] | None = None,
        rebalance_time: str = "09:30:00",
        select_num: int = 10,
        max_positions: int | None = None,
        position_per_symbol: float | None = None,
        timing_check_time_start: str | None = None,
        timing_check_time_end: str | None = None,
        sell_only_at_force_exit: bool = False,
        force_exit_time: str | None = None,
        filter_prev_day_suspended: bool = True,
        min_trade_price: float | None = None,
        max_trade_price: float | None = None,
        buy_cutoff_time: str | None = None,
        forum_mode_enabled: bool = True,
        tick_reference_seconds: int = 3,
        volume_threshold_scale_with_tick: bool = True,
        volume_tier_base_multiplier: float = 1.0,
        max_buys_per_symbol: int = 2,
        max_symbol_profit_pct: float = 2.0,
        max_symbol_loss_count: int = 2,
        entry_fallback_slippage_pct: float = 0.3,
        exit_slippage_pct: float = 0.1,
        base_position_percent: float = 0.10,
        volume_tier_multipliers: list[tuple[float, float]] | None = None,
        **_: object,
    ):
        super().__init__(name)
        self.score_factors = [parse_score_factor(item) for item in (factor_list or [])]
        self.filter_factors = [parse_filter_factor(item) for item in (filter_list or [])]
        self.stock_timing_list = stock_timing_list or []
        self.rebalance_time = self._parse_time(rebalance_time)
        self.select_num = int(select_num)
        self.max_positions = int(max_positions) if max_positions is not None else None
        self.position_per_symbol = float(position_per_symbol) if position_per_symbol is not None else None
        self.timing_check_time_start = self._parse_time(timing_check_time_start) if timing_check_time_start else None
        self.timing_check_time_end = self._parse_time(timing_check_time_end) if timing_check_time_end else None
        self.sell_only_at_force_exit = bool(sell_only_at_force_exit)
        self.force_exit_time = self._parse_time(force_exit_time) if force_exit_time else None
        self.filter_prev_day_suspended = bool(filter_prev_day_suspended)
        self.min_trade_price = float(min_trade_price) if min_trade_price is not None else None
        self.max_trade_price = float(max_trade_price) if max_trade_price is not None else None
        self.buy_cutoff_time = self._parse_time(buy_cutoff_time) if buy_cutoff_time else None
        self.forum_mode_enabled = bool(forum_mode_enabled)
        self.tick_reference_seconds = max(int(tick_reference_seconds), 1)
        self.volume_threshold_scale_with_tick = bool(volume_threshold_scale_with_tick)
        self.volume_tier_base_multiplier = float(volume_tier_base_multiplier)
        self.max_buys_per_symbol = int(max_buys_per_symbol)
        self.max_symbol_profit_pct = float(max_symbol_profit_pct)
        self.max_symbol_loss_count = int(max_symbol_loss_count)
        self.entry_fallback_slippage_pct = float(entry_fallback_slippage_pct)
        self.exit_slippage_pct = float(exit_slippage_pct)
        self.base_position_percent = float(base_position_percent)
        self.volume_tier_multipliers = self._normalize_volume_tier_multipliers(volume_tier_multipliers)
        self.state = StrategyRuntimeState()

    def on_bar(
        self,
        bar: Bar,
        history: dict[str, deque[Bar]],
        factors: FactorRegistry,
        broker: Broker,
    ) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        symbol_state = self.state.symbol_states.setdefault(bar.symbol, PositionRuntimeState())

        if self.state.current_trade_date != bar.trade_date:
            self.state.current_trade_date = bar.trade_date
            self.state.last_force_flat_date = None
            self._reset_daily_symbol_runtime()

        if self._is_selection_time(bar):
            self.state.selected_symbols = self._select_symbols(bar, history, factors)
            self.state.last_selection_ts = bar.timestamp

        if self.force_exit_time and self._is_force_exit_time(bar, broker):
            self.state.last_force_flat_date = bar.trade_date
            return self._build_force_exit_signals(bar, broker)

        current_qty = broker.positions.get(bar.symbol).quantity if bar.symbol in broker.positions else 0

        if current_qty > 0:
            self._refresh_position_state_on_hold(symbol_state, bar)
        else:
            self._reset_position_state(symbol_state)

        buy_tree = self._pick_signal_tree(self.stock_timing_list, "buy")
        symbol_state.last_buy_signal = 1.0 if (buy_tree and self._eval_timing_node(buy_tree, bar.symbol, factors, "buy", symbol_state)) else 0.0

        if bar.symbol not in self.state.selected_symbols and current_qty <= 0:
            return signals

        if not self._within_timing_window(bar.timestamp.time()) and current_qty <= 0:
            return signals

        if current_qty > 0:
            if self.sell_only_at_force_exit:
                return signals
            sell_tree = self._pick_signal_tree(self.stock_timing_list, "sell")
            if sell_tree and self._eval_timing_node(sell_tree, bar.symbol, factors, "sell", symbol_state):
                execution_price = self._calc_forum_exit_price(bar)
                signals.append(
                    SignalEvent(
                        timestamp=bar.timestamp,
                        symbol=bar.symbol,
                        target_percent=0.0,
                        execution_price=execution_price,
                        reason=f"{self.name}: timing sell",
                    )
                )
            return signals

        if not self._can_open_new_position(broker):
            return signals
        if not self._can_symbol_open_new_trade(symbol_state, bar):
            return signals
        if self._is_first_trade_day(history.get(bar.symbol), bar.trade_date):
            return signals
        if buy_tree is None or not self._eval_timing_node(buy_tree, bar.symbol, factors, "buy", symbol_state):
            return signals

        target_percent = self._calc_target_percent(symbol_state)
        if target_percent <= 0:
            return signals

        execution_price = self._calc_forum_entry_price(bar, history.get(bar.symbol))
        if execution_price is None or execution_price <= 0:
            return signals

        symbol_state.position_weight = self._calc_symbol_position_weight(bar, factors)

        signals.append(
            SignalEvent(
                timestamp=bar.timestamp,
                symbol=bar.symbol,
                target_percent=target_percent,
                execution_price=execution_price,
                reason=f"{self.name}: timing buy",
            )
        )

        return signals

    def _refresh_position_state_on_hold(self, state: PositionRuntimeState, bar: Bar) -> None:
        if state.entry_price is None:
            state.entry_price = bar.price
            state.entry_time = bar.timestamp
            state.max_price_after_entry = bar.price
            state.bars_since_entry = 0
            return
        state.bars_since_entry += 1
        if bar.price is not None:
            if state.max_price_after_entry is None or bar.price > state.max_price_after_entry:
                state.max_price_after_entry = bar.price

    @staticmethod
    def _reset_position_state(state: PositionRuntimeState) -> None:
        state.entry_price = None
        state.entry_time = None
        state.max_price_after_entry = None
        state.bars_since_entry = 0

    def _select_symbols(
        self,
        bar: Bar,
        history: dict[str, deque[Bar]],
        factors: FactorRegistry,
    ) -> set[str]:
        universe = self._initial_universe(factors)
        universe = self._apply_price_bounds(universe, factors)
        universe = self._exclude_prev_day_suspended(universe, history, bar)
        universe = self._apply_filters(universe, factors)
        ranked = self._score_and_rank(universe, factors)
        return {symbol for symbol, _ in ranked[: self.select_num]}

    def _initial_universe(self, factors: FactorRegistry) -> set[str]:
        universe: set[str] = set()
        factor_specs = self.score_factors + self.filter_factors
        if not factor_specs:
            factor_specs = self._collect_timing_specs(self.stock_timing_list)
        for spec in factor_specs:
            universe.update(symbol for symbol, value in factors.snapshot(spec.key).items() if value is not None)
        return universe

    def _apply_price_bounds(self, universe: set[str], factors: FactorRegistry) -> set[str]:
        if self.min_trade_price is None and self.max_trade_price is None:
            return universe
        close_snapshot = factors.snapshot("收盘价")
        out: set[str] = set()
        for symbol in universe:
            price = close_snapshot.get(symbol)
            if price is None:
                continue
            price = float(price)
            if self.min_trade_price is not None and price < self.min_trade_price:
                continue
            if self.max_trade_price is not None and price > self.max_trade_price:
                continue
            out.add(symbol)
        return out

    def _apply_filters(self, universe: set[str], factors: FactorRegistry) -> set[str]:
        current = set(universe)
        for spec in self.filter_factors:
            values = {
                symbol: value
                for symbol, value in factors.snapshot(spec.key).items()
                if symbol in current and value is not None
            }
            current = self._filter_by_method(values, spec.method, spec.ascending)
            if not current:
                break
        return current

    def _score_and_rank(self, universe: set[str], factors: FactorRegistry) -> list[tuple[str, float]]:
        if not self.score_factors:
            return [(symbol, 0.0) for symbol in sorted(universe)]

        scores = {symbol: 0.0 for symbol in universe}
        valid_counts = {symbol: 0 for symbol in universe}
        for spec in self.score_factors:
            values = {
                symbol: float(value)
                for symbol, value in factors.snapshot(spec.key).items()
                if symbol in universe and value is not None
            }
            ranked = sorted(values.items(), key=lambda item: item[1], reverse=not spec.ascending)
            for rank, (symbol, _) in enumerate(ranked, start=1):
                scores[symbol] += rank * spec.weight
                valid_counts[symbol] += 1
        return sorted(
            [(symbol, score) for symbol, score in scores.items() if valid_counts[symbol] == len(self.score_factors)],
            key=lambda item: item[1],
        )

    def _pick_signal_tree(self, nodes: list[dict[str, Any]], signal_type: str) -> dict[str, Any] | None:
        matches = []
        for node in nodes:
            if "logic" in node:
                if self._tree_contains_signal(node, signal_type):
                    matches.append(node)
            elif str(node.get("signal", "buy")).lower() == signal_type:
                matches.append(node)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        return {"logic": "and", "conditions": matches}

    def _tree_contains_signal(self, node: dict[str, Any], signal_type: str) -> bool:
        if "logic" not in node:
            return str(node.get("signal", "buy")).lower() == signal_type
        return any(self._tree_contains_signal(child, signal_type) for child in node.get("conditions", []))

    def _eval_timing_node(
        self,
        node: dict[str, Any],
        symbol: str,
        factors: FactorRegistry,
        signal_type: str,
        position_state: PositionRuntimeState,
        ) -> bool:
        if "logic" in node:
            logic = str(node["logic"]).lower()
            children = [child for child in node.get("conditions", []) if self._tree_contains_signal(child, signal_type)]
            if not children:
                return signal_type == "buy"
            if logic == "and":
                return all(self._eval_timing_node(child, symbol, factors, signal_type, position_state) for child in children)
            if logic == "or":
                return any(self._eval_timing_node(child, symbol, factors, signal_type, position_state) for child in children)
            raise ValueError(f"不支持的 timing logic: {logic}")

        if "compare" in node:
            left_value = self._resolve_compare_operand(node.get("left", {}), symbol, factors, position_state)
            right_value = self._resolve_compare_operand(node.get("right", {}), symbol, factors, position_state)
            if left_value is None or right_value is None:
                return False
            op_text = str(node["compare"])
            multiplier = float(node.get("right_multiplier", 1.0))
            offset = float(node.get("right_offset", 0.0))
            adjusted_right = right_value * multiplier + offset
            if op_text not in OPS:
                raise ValueError(f"不支持的 compare 运算符: {op_text}")
            return OPS[op_text](left_value, adjusted_right)

        if "field" in node:
            value = self._read_runtime_field(node["field"], symbol, factors, position_state)
            if value is None:
                return False
            return self._match_method(float(value), str(node["method"]))

        spec = parse_timing_factor(node)
        if spec.signal != signal_type:
            return True
        value = factors.get_value(spec.key, symbol)
        if value is None:
            return False
        return self._match_method(float(value), spec.method)

    def _resolve_compare_operand(
        self,
        operand: dict[str, Any],
        symbol: str,
        factors: FactorRegistry,
        position_state: PositionRuntimeState,
    ) -> float | None:
        if "field" in operand:
            value = self._read_runtime_field(str(operand["field"]), symbol, factors, position_state)
            return None if value is None else float(value)
        if "name" in operand:
            spec = parse_timing_factor(
                {
                    "name": operand["name"],
                    "params": operand.get("params"),
                    "method": operand.get("method", "val:>=0"),
                    "signal": operand.get("signal", "buy"),
                    "weight": operand.get("weight", 1.0),
                }
            )
            value = factors.get_value(spec.key, symbol)
            return None if value is None else float(value)
        if "value" in operand:
            return float(operand["value"])
        return None

    def _read_runtime_field(
        self,
        field_name: str,
        symbol: str,
        factors: FactorRegistry,
        position_state: PositionRuntimeState,
    ) -> float | None:
        if field_name == "buy_signal":
            return position_state.last_buy_signal
        if field_name == "bars_since_entry":
            return float(position_state.bars_since_entry)
        if field_name == "amp_after_buy":
            price = factors.get_value("收盘价", symbol)
            if price is None or position_state.entry_price in (None, 0):
                return None
            return (float(price) / float(position_state.entry_price) - 1.0) * 100
        if field_name == "max_rose":
            if position_state.entry_price in (None, 0) or position_state.max_price_after_entry in (None, 0):
                return None
            return (float(position_state.max_price_after_entry) / float(position_state.entry_price) - 1.0) * 100
        return None

    def _collect_timing_specs(self, nodes: list[dict[str, Any]]) -> list:
        specs = []
        for node in nodes:
            if "logic" in node:
                specs.extend(self._collect_timing_specs(node.get("conditions", [])))
            elif "name" in node:
                specs.append(parse_timing_factor(node))
        return specs

    def _can_open_new_position(self, broker: Broker) -> bool:
        active_positions = sum(1 for pos in broker.positions.values() if pos.quantity > 0)
        if self.max_positions is not None and active_positions >= self.max_positions:
            return False
        return True

    def _calc_target_percent(self, state: PositionRuntimeState) -> float:
        base_percent = self.position_per_symbol if self.position_per_symbol is not None else self.base_position_percent
        if base_percent is not None:
            return max(float(base_percent) * float(state.position_weight or 1.0), 0.0)
        divisor = self.max_positions or self.select_num
        if divisor <= 0:
            return 0.0
        return 1.0 / divisor

    def on_fill(self, fill: FillEvent, broker: Broker) -> None:
        symbol_state = self.state.symbol_states.setdefault(fill.symbol, PositionRuntimeState())
        if fill.side == OrderSide.BUY:
            symbol_state.entry_price = float(fill.price)
            symbol_state.entry_time = fill.timestamp
            symbol_state.max_price_after_entry = float(fill.price)
            symbol_state.bars_since_entry = 0
            symbol_state.buy_count += 1
            symbol_state.last_entry_trade_date = fill.timestamp.strftime("%Y-%m-%d")
            return

        if fill.side == OrderSide.SELL and symbol_state.entry_price not in (None, 0):
            pnl_pct = (float(fill.price) / float(symbol_state.entry_price) - 1.0) * 100.0
            symbol_state.realized_profit_sum += pnl_pct * float(symbol_state.position_weight or 1.0)
            if pnl_pct < 0:
                symbol_state.realized_loss_count += 1
        self._reset_position_state(symbol_state)

    def _reset_daily_symbol_runtime(self) -> None:
        for state in self.state.symbol_states.values():
            state.buy_count = 0
            state.realized_profit_sum = 0.0
            state.realized_loss_count = 0
            state.position_weight = 1.0
            state.last_entry_trade_date = None

    def _can_symbol_open_new_trade(self, state: PositionRuntimeState, bar: Bar) -> bool:
        if self.buy_cutoff_time and bar.timestamp.time() > self.buy_cutoff_time:
            return False
        if state.buy_count >= self.max_buys_per_symbol:
            return False
        if state.realized_profit_sum > self.max_symbol_profit_pct:
            return False
        if state.realized_loss_count >= self.max_symbol_loss_count:
            return False
        return True

    def _calc_symbol_position_weight(self, bar: Bar, factors: FactorRegistry) -> float:
        recent_volume = self._find_factor_value_by_prefix(factors, bar.symbol, "近平均交易量")
        if recent_volume is None:
            return 1.0

        adjusted_volume = float(recent_volume)
        if self.volume_threshold_scale_with_tick:
            adjusted_volume = self._convert_current_volume_to_reference_tick(bar, float(recent_volume))

        weight = self.volume_tier_base_multiplier
        for threshold, multiplier in self.volume_tier_multipliers:
            if adjusted_volume > threshold:
                weight = multiplier
        return weight

    def _convert_current_volume_to_reference_tick(self, bar: Bar, current_volume: float) -> float:
        actual_seconds = self._current_event_seconds(bar)
        if actual_seconds <= 0:
            return current_volume
        return current_volume * (actual_seconds / self.tick_reference_seconds)

    def _current_event_seconds(self, bar: Bar) -> int:
        if not self.forum_mode_enabled:
            return 1
        extra_seconds = bar.extra.get("synthetic_tick_seconds") if bar.extra else None
        if extra_seconds:
            return int(extra_seconds)
        return 1

    def _calc_forum_entry_price(self, bar: Bar, bars: deque[Bar] | None) -> float | None:
        if not self.forum_mode_enabled:
            return bar.price
        if bars and len(bars) >= 2:
            prev_bar = bars[-2]
            if prev_bar.trade_date == bar.trade_date and prev_bar.price is not None and bar.price is not None:
                return (float(prev_bar.price) + float(bar.price)) / 2.0
        if bar.price is None:
            return None
        return float(bar.price) * (1.0 + self.entry_fallback_slippage_pct / 100.0)

    def _calc_forum_exit_price(self, bar: Bar) -> float | None:
        if bar.price is None:
            return None
        if not self.forum_mode_enabled:
            return bar.price
        return float(bar.price) * (1.0 - self.exit_slippage_pct / 100.0)

    @staticmethod
    def _find_factor_value_by_prefix(
        factors: FactorRegistry,
        symbol: str,
        prefix: str,
    ) -> float | int | bool | None:
        for factor_name, snapshot in factors.values.items():
            if factor_name == prefix or factor_name.startswith(f"{prefix}__"):
                return snapshot.get(symbol)
        return None

    @staticmethod
    def _normalize_volume_tier_multipliers(value: list[tuple[float, float]] | None) -> list[tuple[float, float]]:
        if not value:
            return [(600.0, 1.5), (1000.0, 2.0), (1500.0, 2.5), (2000.0, 3.0)]
        return [(float(threshold), float(multiplier)) for threshold, multiplier in value]

    def _exclude_prev_day_suspended(
        self,
        universe: set[str],
        history: dict[str, deque[Bar]],
        current_bar: Bar,
    ) -> set[str]:
        if not self.filter_prev_day_suspended or not current_bar.trade_date:
            return universe

        filtered: set[str] = set()
        for symbol in universe:
            prev_day_bar = self._find_previous_trade_day_bar(history.get(symbol), current_bar.trade_date)
            if prev_day_bar is None or not prev_day_bar.suspended:
                filtered.add(symbol)
        return filtered

    @staticmethod
    def _find_previous_trade_day_bar(bars: deque[Bar] | None, current_trade_date: str) -> Bar | None:
        if not bars:
            return None
        prev_bar: Bar | None = None
        for item in reversed(bars):
            if item.trade_date and item.trade_date != current_trade_date:
                if prev_bar is None or item.trade_date == prev_bar.trade_date:
                    prev_bar = item
                    continue
                break
        return prev_bar

    def _is_first_trade_day(self, bars: deque[Bar] | None, current_trade_date: str | None) -> bool:
        if not bars or not current_trade_date:
            return True
        return self._find_previous_trade_day_bar(bars, current_trade_date) is None

    def _filter_by_method(self, values: dict[str, float | int | bool | None], method: str, ascending: bool) -> set[str]:
        method_type, expr = method.split(":", 1)
        op_text, threshold_text = self._parse_expression(expr)
        threshold = float(threshold_text)
        clean = {symbol: float(value) for symbol, value in values.items() if value is not None}

        if method_type == "val":
            op = OPS[op_text]
            return {symbol for symbol, value in clean.items() if op(value, threshold)}
        if method_type == "pct":
            return self._filter_by_percentile(clean, op_text, threshold, ascending)
        raise ValueError(f"不支持的过滤方式: {method}")

    def _filter_by_percentile(
        self,
        values: dict[str, float],
        op_text: str,
        pct: float,
        ascending: bool,
    ) -> set[str]:
        if not 0 <= pct <= 1:
            raise ValueError(f"pct 阈值必须在 0 到 1 之间: {pct}")
        ranked = sorted(values.items(), key=lambda item: item[1], reverse=not ascending)
        total = len(ranked)
        if total == 0:
            return set()
        ranks = {symbol: idx / total for idx, (symbol, _) in enumerate(ranked)}
        op = OPS[op_text]
        return {symbol for symbol, rank_pct in ranks.items() if op(rank_pct, pct)}

    def _match_method(self, value: float, method: str) -> bool:
        method_type, expr = method.split(":", 1)
        op_text, threshold_text = self._parse_expression(expr)
        threshold = float(threshold_text)
        if method_type != "val":
            raise ValueError(f"择时因子目前只支持 val 判断，不支持: {method}")
        return OPS[op_text](value, threshold)

    def _is_selection_time(self, bar: Bar) -> bool:
        if bar.trade_date != self.state.current_trade_date:
            return False
        if bar.timestamp.time() < self.rebalance_time:
            return False
        if self.state.last_selection_ts is None:
            return True
        return self.state.last_selection_ts.date() != bar.timestamp.date()

    def _within_timing_window(self, current_time: time) -> bool:
        if self.timing_check_time_start and current_time < self.timing_check_time_start:
            return False
        if self.timing_check_time_end and current_time > self.timing_check_time_end:
            return False
        return True

    def _is_force_exit_time(self, bar: Bar, broker: Broker) -> bool:
        if self.force_exit_time is None or bar.trade_date is None:
            return False
        if self.state.last_force_flat_date == bar.trade_date:
            return False
        if bar.timestamp.time() < self.force_exit_time:
            return False
        return any(pos.quantity > 0 for pos in broker.positions.values())

    def _build_force_exit_signals(self, bar: Bar, broker: Broker) -> list[SignalEvent]:
        signals: list[SignalEvent] = []
        for symbol, position in broker.positions.items():
            if position.quantity <= 0:
                continue
            signals.append(
                SignalEvent(
                    timestamp=bar.timestamp,
                    symbol=symbol,
                    target_percent=0.0,
                    reason=f"{self.name}: force exit time",
                )
            )
        return signals

    @staticmethod
    def _parse_expression(expr: str) -> tuple[str, str]:
        for op_text in (">=", "<=", "==", "!=", ">", "<"):
            if expr.startswith(op_text):
                return op_text, expr[len(op_text) :]
        raise ValueError(f"无法解析表达式: {expr}")

    @staticmethod
    def _parse_time(value: str) -> time:
        fmt = "%H:%M:%S" if len(value.split(":")) == 3 else "%H:%M"
        return datetime.strptime(value, fmt).time()
