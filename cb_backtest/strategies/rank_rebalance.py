"""按“过滤列表 + 打分因子列表”定时调仓的策略。

这个策略适配类似选股框架的配置风格：

    factor_list = [("动量", False, 20, 1)]
    filter_list = [("收盘价", None, "val:>2", True)]

其中 factor_list 做综合打分，filter_list 先过滤股票池/转债池。
"""

from __future__ import annotations

import operator
from collections import deque
from datetime import datetime, time, timedelta
from typing import Callable

from cb_backtest.broker import Broker
from cb_backtest.events import Bar, SignalEvent
from cb_backtest.factor_config import FilterFactorSpec, ScoreFactorSpec, parse_filter_factor, parse_score_factor
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


class RankRebalanceStrategy(Strategy):
    """过滤后多因子打分，选择前 N 个标的等权持有。"""

    def __init__(
        self,
        name: str,
        factor_list: list[tuple],
        filter_list: list[tuple] | None = None,
        select_num: int = 5,
        hold_period: str = "1D",
        cap_weight: float = 1.0,
        rebalance_time: str = "14:55",
        filter_prev_day_suspended: bool = True,
        **_: object,
    ):
        super().__init__(name)
        self.score_factors = [parse_score_factor(item) for item in factor_list]
        self.filter_factors = [parse_filter_factor(item) for item in (filter_list or [])]
        self.select_num = int(select_num)
        self.hold_period = hold_period
        self.cap_weight = float(cap_weight)
        self.rebalance_time = self._parse_time(rebalance_time)
        self.filter_prev_day_suspended = filter_prev_day_suspended
        self._last_rebalance_at: datetime | None = None

    def on_bar(
        self,
        bar: Bar,
        history: dict[str, deque[Bar]],
        factors: FactorRegistry,
        broker: Broker,
    ) -> list[SignalEvent]:
        """到达调仓时间且满足持仓周期后，执行过滤、打分、选券。"""

        if bar.timestamp.time() < self.rebalance_time:
            return []
        if not self._can_rebalance(bar.timestamp):
            return []

        universe = self._initial_universe(factors)
        universe = self._exclude_prev_day_suspended(universe, history, bar)
        universe = self._apply_filters(universe, factors)
        ranked = self._score_and_rank(universe, factors)
        if not ranked:
            return []

        selected_symbols = [symbol for symbol, _ in ranked[: self.select_num]]
        selected = set(selected_symbols)
        self._last_rebalance_at = bar.timestamp

        signals: list[SignalEvent] = []
        target_weight = self.cap_weight / len(selected) if selected else 0
        current_symbols = {s for s, p in broker.positions.items() if p.quantity > 0}
        for symbol in selected_symbols:
            signals.append(
                SignalEvent(
                    timestamp=bar.timestamp,
                    symbol=symbol,
                    target_percent=target_weight,
                    reason=f"{self.name}: selected by factor_list",
                )
            )
        for symbol in current_symbols - selected:
            signals.append(
                SignalEvent(
                    timestamp=bar.timestamp,
                    symbol=symbol,
                    target_percent=0,
                    reason=f"{self.name}: filtered out or rank out",
                )
            )
        return signals

    def _initial_universe(self, factors: FactorRegistry) -> set[str]:
        """用所有打分因子的最新值合并出初始候选池。"""

        universe: set[str] = set()
        for spec in self.score_factors:
            universe.update(symbol for symbol, value in factors.snapshot(spec.key).items() if value is not None)
        return universe

    def _apply_filters(self, universe: set[str], factors: FactorRegistry) -> set[str]:
        """按 filter_list 顺序逐层过滤候选池。"""

        current = set(universe)
        for spec in self.filter_factors:
            values = {
                symbol: value
                for symbol, value in factors.snapshot(spec.key).items()
                if symbol in current and value is not None
            }
            current = self._filter_by_method(values, spec)
            if not current:
                break
        return current

    def _exclude_prev_day_suspended(
        self,
        universe: set[str],
        history: dict[str, deque[Bar]],
        current_bar: Bar,
    ) -> set[str]:
        """过滤前一交易日停牌的标的。"""

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
        """找到该标的前一交易日的最后一根 bar。"""

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

    def _filter_by_method(self, values: dict[str, float | None], spec: FilterFactorSpec) -> set[str]:
        """执行 val 或 pct 过滤。"""

        method_type, expr = spec.method.split(":", 1)
        op_text, threshold = self._parse_expression(expr)
        threshold_value = float(threshold)

        clean = {symbol: float(value) for symbol, value in values.items() if value is not None}
        if method_type == "val":
            op = OPS[op_text]
            return {symbol for symbol, value in clean.items() if op(value, threshold_value)}
        if method_type == "pct":
            return self._filter_by_percentile(clean, op_text, threshold_value, spec.ascending)
        raise ValueError(f"不支持的过滤方法: {spec.method}")

    def _filter_by_percentile(
        self,
        values: dict[str, float],
        op_text: str,
        pct: float,
        ascending: bool,
    ) -> set[str]:
        """按排序方向做百分比过滤。

        ascending=True 表示从小到大排名，pct:<0.2 取最小的前 20%。
        ascending=False 表示从大到小排名，pct:<0.9 取最大的前 90%。
        """

        if not 0 <= pct <= 1:
            raise ValueError(f"pct 阈值必须在 0 到 1 之间: {pct}")
        ranked = sorted(values.items(), key=lambda item: item[1], reverse=not ascending)
        n = len(ranked)
        if n == 0:
            return set()

        ranks = {symbol: idx / n for idx, (symbol, _) in enumerate(ranked)}
        op = OPS[op_text]
        return {symbol for symbol, rank_pct in ranks.items() if op(rank_pct, pct)}

    def _score_and_rank(self, universe: set[str], factors: FactorRegistry) -> list[tuple[str, float]]:
        """对过滤后的候选池做多因子排名打分。

        每个因子先转成截面名次分，方向由 ascending 控制；再乘以权重求和。
        分数越小排名越靠前。
        """

        scores = {symbol: 0.0 for symbol in universe}
        valid_counts = {symbol: 0 for symbol in universe}
        for spec in self.score_factors:
            values = {
                symbol: value
                for symbol, value in factors.snapshot(spec.key).items()
                if symbol in universe and value is not None
            }
            ranked = sorted(values.items(), key=lambda item: float(item[1]), reverse=not spec.ascending)
            for rank, (symbol, _) in enumerate(ranked, start=1):
                scores[symbol] += rank * spec.weight
                valid_counts[symbol] += 1
        return sorted(
            [(symbol, score) for symbol, score in scores.items() if valid_counts[symbol] == len(self.score_factors)],
            key=lambda item: item[1],
        )

    def _can_rebalance(self, timestamp: datetime) -> bool:
        """根据 hold_period 判断当前是否允许调仓。"""

        if self._last_rebalance_at is None:
            return True
        return timestamp >= self._last_rebalance_at + self._parse_period(self.hold_period)

    @staticmethod
    def _parse_expression(expr: str) -> tuple[str, str]:
        """把 '<=0.8' 解析为 ('<=', '0.8')。"""

        for op_text in (">=", "<=", "==", "!=", ">", "<"):
            if expr.startswith(op_text):
                return op_text, expr[len(op_text) :]
        raise ValueError(f"无法解析过滤表达式: {expr}")

    @staticmethod
    def _parse_period(value: str) -> timedelta:
        """解析 3D、1H、30M 这类持仓周期。"""

        unit = value[-1].upper()
        amount = int(value[:-1])
        if unit == "D":
            return timedelta(days=amount)
        if unit == "H":
            return timedelta(hours=amount)
        if unit == "M":
            return timedelta(minutes=amount)
        raise ValueError(f"不支持的 hold_period: {value}")

    @staticmethod
    def _parse_time(value: str) -> time:
        """支持 open/close 别名，也支持 HH:MM 字符串。"""

        if value == "open":
            value = "09:30"
        elif value == "close":
            value = "15:00"
        return datetime.strptime(value, "%H:%M").time()
