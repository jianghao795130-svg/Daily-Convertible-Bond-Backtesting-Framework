"""可转债早盘高频趋势策略。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from cb_backtest.broker import Broker
from cb_backtest.events import Bar, SignalEvent
from cb_backtest.factors.base import FactorRegistry
from cb_backtest.strategies.base import Strategy


@dataclass(slots=True)
class TrendMetricState:
    """单个均线窗口的趋势状态。"""

    prev_return: float | None = None
    streak: int = 0
    cumulative_change: float = 0.0
    current_change: float = 0.0
    speed: float = 0.0


@dataclass(slots=True)
class SymbolState:
    """单只转债在策略里的日内状态。"""

    trade_date: str | None = None
    prev_close: float | None = None
    last_price_prev_day: float | None = None
    prices: deque[float] = field(default_factory=lambda: deque(maxlen=5000))
    amounts: deque[float] = field(default_factory=lambda: deque(maxlen=5000))
    metric_states: dict[int, TrendMetricState] = field(
        default_factory=lambda: {1: TrendMetricState(), 2: TrendMetricState(), 3: TrendMetricState()}
    )
    buy_count: int = 0
    loss_count: int = 0
    profit_score: float = 0.0
    last_position_qty: int = 0
    entry_price: float | None = None
    entry_time: datetime | None = None
    max_price_after_entry: float | None = None
    position_scale: float = 0.0
    pending_exit_score: float | None = None


class IntradayConvertibleBondTrendStrategy(Strategy):
    """复现帖子思路的早盘日内趋势策略。"""

    def __init__(
        self,
        name: str,
        allow_buy_time_start: str = "09:30:00",
        allow_buy_time_end: str = "09:35:00",
        force_exit_time: str = "10:00:00",
        max_positions: int = 5,
        filter_prev_day_suspended: bool = True,
        near_avg_amount_window: int = 10,
        min_near_avg_amount: float = 200.0,
        max_intraday_return: float = 12.0,
        trend_min_cum_return: float = 0.4,
        trend_max_cum_return: float = 5.0,
        trend_min_streak: int = 3,
        trend_alt_streak: int = 15,
        speed_min_ratio: float = 1.0,
        speed_max_ratio: float = 2.57,
        base_position_percent: float = 0.08,
        max_position_percent: float = 0.24,
        amount_position_tiers: list[tuple[float, float]] | None = None,
        stop_loss_return: float = -0.1,
        ma2_exit_return_ceiling: float = 0.3,
        lowest_profit_threshold: float = 1.0,
        pullback_ratio: float = 0.8,
        overheat_exit_return: float = 19.0,
        max_buys_per_symbol_per_day: int = 2,
        max_loss_trades_per_symbol_per_day: int = 2,
        max_profit_score_per_symbol_per_day: float = 2.0,
        **_: object,
    ):
        super().__init__(name)
        self.allow_buy_time_start = self._parse_time(allow_buy_time_start)
        self.allow_buy_time_end = self._parse_time(allow_buy_time_end)
        self.force_exit_time = self._parse_time(force_exit_time)
        self.max_positions = int(max_positions)
        self.filter_prev_day_suspended = bool(filter_prev_day_suspended)
        self.near_avg_amount_window = int(near_avg_amount_window)
        self.min_near_avg_amount = float(min_near_avg_amount)
        self.max_intraday_return = float(max_intraday_return)
        self.trend_min_cum_return = float(trend_min_cum_return)
        self.trend_max_cum_return = float(trend_max_cum_return)
        self.trend_min_streak = int(trend_min_streak)
        self.trend_alt_streak = int(trend_alt_streak)
        self.speed_min_ratio = float(speed_min_ratio)
        self.speed_max_ratio = float(speed_max_ratio)
        self.base_position_percent = float(base_position_percent)
        self.max_position_percent = float(max_position_percent)
        self.amount_position_tiers = sorted(amount_position_tiers or [], key=lambda item: item[0], reverse=True)
        self.stop_loss_return = float(stop_loss_return)
        self.ma2_exit_return_ceiling = float(ma2_exit_return_ceiling)
        self.lowest_profit_threshold = float(lowest_profit_threshold)
        self.pullback_ratio = float(pullback_ratio)
        self.overheat_exit_return = float(overheat_exit_return)
        self.max_buys_per_symbol_per_day = int(max_buys_per_symbol_per_day)
        self.max_loss_trades_per_symbol_per_day = int(max_loss_trades_per_symbol_per_day)
        self.max_profit_score_per_symbol_per_day = float(max_profit_score_per_symbol_per_day)
        self.states: dict[str, SymbolState] = {}

    def on_bar(
        self,
        bar: Bar,
        history: dict[str, deque[Bar]],
        factors: FactorRegistry,
        broker: Broker,
    ) -> list[SignalEvent]:
        del factors

        state = self.states.setdefault(bar.symbol, SymbolState())
        self._refresh_symbol_day_state(state, bar)
        self._sync_position_state(state, bar, broker)
        self._append_tick(state, bar)

        if state.prev_close in (None, 0) or bar.price is None or bar.suspended:
            return []
        if self.filter_prev_day_suspended and self._prev_day_suspended(history.get(bar.symbol), bar.trade_date):
            return []

        metrics = self._calc_metrics(state, bar.price)
        if metrics is None:
            return []

        current_qty = broker.positions.get(bar.symbol, None).quantity if bar.symbol in broker.positions else 0
        current_time = bar.timestamp.time()
        signals: list[SignalEvent] = []

        if current_qty > 0:
            exit_reason = self._check_exit_conditions(state, metrics, current_time)
            if exit_reason is not None:
                state.pending_exit_score = metrics["amp_after_buy"] * max(state.position_scale, 1.0)
                signals.append(
                    SignalEvent(
                        timestamp=bar.timestamp,
                        symbol=bar.symbol,
                        target_percent=0.0,
                        reason=f"{self.name}: {exit_reason}",
                    )
                )
            return signals

        if not self._can_open_new_position(state, broker, current_time):
            return []

        if not metrics["buy_signal"]:
            return []

        target_percent, position_scale = self._calc_target_percent(metrics["near_avg_amount"])
        if target_percent <= 0:
            return []

        state.position_scale = position_scale
        signals.append(
            SignalEvent(
                timestamp=bar.timestamp,
                symbol=bar.symbol,
                target_percent=target_percent,
                reason=f"{self.name}: buy signal",
            )
        )
        return signals

    def _refresh_symbol_day_state(self, state: SymbolState, bar: Bar) -> None:
        """交易日切换时重置日内状态。"""

        if state.trade_date == bar.trade_date:
            return

        previous_close = state.prices[-1] if state.prices else state.last_price_prev_day
        if previous_close is not None:
            state.last_price_prev_day = previous_close

        state.trade_date = bar.trade_date
        state.prev_close = state.last_price_prev_day
        state.prices.clear()
        state.amounts.clear()
        state.metric_states = {1: TrendMetricState(), 2: TrendMetricState(), 3: TrendMetricState()}
        state.buy_count = 0
        state.loss_count = 0
        state.profit_score = 0.0
        state.last_position_qty = 0
        state.entry_price = None
        state.entry_time = None
        state.max_price_after_entry = None
        state.position_scale = 0.0
        state.pending_exit_score = None

    def _sync_position_state(self, state: SymbolState, bar: Bar, broker: Broker) -> None:
        """把 broker 里的真实持仓同步回策略状态。"""

        position = broker.positions.get(bar.symbol)
        current_qty = position.quantity if position is not None else 0

        if current_qty > 0 and state.last_position_qty == 0:
            state.buy_count += 1
            state.entry_price = position.avg_cost
            state.entry_time = bar.timestamp
            state.max_price_after_entry = bar.price

        if current_qty == 0 and state.last_position_qty > 0:
            if state.pending_exit_score is not None:
                state.profit_score += state.pending_exit_score
                if state.pending_exit_score < 0:
                    state.loss_count += 1
            state.entry_price = None
            state.entry_time = None
            state.max_price_after_entry = None
            state.position_scale = 0.0
            state.pending_exit_score = None

        state.last_position_qty = current_qty

    def _append_tick(self, state: SymbolState, bar: Bar) -> None:
        """记录当前 tick 的价格和成交额。"""

        if bar.price is not None:
            state.prices.append(float(bar.price))
            state.last_price_prev_day = float(bar.price)
        state.amounts.append(float(bar.amount or 0.0))
        self._update_metric_state(state, 1)
        self._update_metric_state(state, 2)
        self._update_metric_state(state, 3)

    def _update_metric_state(self, state: SymbolState, window: int) -> None:
        """增量更新 m1/m2/m3 的趋势状态。"""

        metric = state.metric_states[window]
        if state.prev_close in (None, 0) or len(state.prices) < window:
            metric.prev_return = None
            metric.current_change = 0.0
            metric.streak = 0
            metric.cumulative_change = 0.0
            metric.speed = 0.0
            return

        ma_price = sum(list(state.prices)[-window:]) / window
        current_return = (ma_price / state.prev_close - 1) * 100
        change = 0.0 if metric.prev_return is None else current_return - metric.prev_return
        metric.current_change = change
        if change >= 0:
            metric.streak += 1
            metric.cumulative_change += change
        else:
            metric.streak = 0
            metric.cumulative_change = 0.0
        metric.speed = metric.cumulative_change / metric.streak if metric.streak > 0 else 0.0
        metric.prev_return = current_return

    def _calc_metrics(self, state: SymbolState, current_price: float) -> dict[str, float | bool] | None:
        """计算当前买卖判断需要的全部核心指标。"""

        if state.prev_close in (None, 0):
            return None

        m1_formula = self._formula_change(state, current_price, 1)
        m2_formula = self._formula_change(state, current_price, 2)
        m3_formula = self._formula_change(state, current_price, 3)
        near_avg_amount = self._mean(list(state.amounts)[-self.near_avg_amount_window :])
        current_return = (current_price / state.prev_close - 1) * 100
        m3 = state.metric_states[3]

        condition1 = (
            (
                m3.cumulative_change >= self.trend_min_cum_return
                and m3.cumulative_change < self.trend_max_cum_return
                and m3.streak > self.trend_min_streak
            )
            or (
                m3.cumulative_change < self.trend_min_cum_return
                and m3.streak > self.trend_alt_streak
            )
        )
        condition2 = (
            m3.current_change >= m3.speed * self.speed_min_ratio
            and m3.current_change < m3.speed * self.speed_max_ratio
        )
        condition3 = current_return < self.max_intraday_return
        condition4 = near_avg_amount >= self.min_near_avg_amount
        buy_signal = condition1 and condition2 and condition3 and condition4

        metrics: dict[str, float | bool] = {
            "near_avg_amount": near_avg_amount,
            "current_return": current_return,
            "m1_formula": m1_formula,
            "m2_formula": m2_formula,
            "m3_formula": m3_formula,
            "buy_signal": buy_signal,
        }
        if state.entry_price not in (None, 0):
            entry_price = float(state.entry_price)
            max_price = max(float(state.max_price_after_entry or entry_price), current_price)
            state.max_price_after_entry = max_price
            metrics["amp_after_buy"] = (current_price / entry_price - 1) * 100
            metrics["max_rose"] = (max_price / entry_price - 1) * 100
        else:
            metrics["amp_after_buy"] = 0.0
            metrics["max_rose"] = 0.0
        return metrics

    def _check_exit_conditions(
        self,
        state: SymbolState,
        metrics: dict[str, float | bool],
        current_time: time,
    ) -> str | None:
        """按帖子思路检查退出条件。"""

        amp_after_buy = float(metrics["amp_after_buy"])
        max_rose = float(metrics["max_rose"])
        m1_formula = float(metrics["m1_formula"])
        m2_formula = float(metrics["m2_formula"])
        m3_formula = float(metrics["m3_formula"])
        current_return = float(metrics["current_return"])
        buy_signal = bool(metrics["buy_signal"])

        if current_time >= self.force_exit_time:
            return "force exit time"
        if current_return >= self.overheat_exit_return:
            return "overheat exit"
        if amp_after_buy <= self.stop_loss_return:
            return "stop loss"
        if amp_after_buy <= self.ma2_exit_return_ceiling and m2_formula < 0:
            return "ma2 exit"
        if (
            self.ma2_exit_return_ceiling < amp_after_buy <= self.lowest_profit_threshold
            and m3_formula < 0
            and m1_formula < 0
        ):
            return "ma3 exit"
        if amp_after_buy > self.lowest_profit_threshold and max_rose > 0:
            if (not buy_signal) and (amp_after_buy / max_rose) < self.pullback_ratio:
                return "pullback exit"
        return None

    def _can_open_new_position(self, state: SymbolState, broker: Broker, current_time: time) -> bool:
        """检查当前时刻是否允许新开仓。"""

        if current_time < self.allow_buy_time_start or current_time > self.allow_buy_time_end:
            return False
        if state.buy_count >= self.max_buys_per_symbol_per_day:
            return False
        if state.loss_count >= self.max_loss_trades_per_symbol_per_day:
            return False
        if state.profit_score > self.max_profit_score_per_symbol_per_day:
            return False
        active_positions = sum(1 for pos in broker.positions.values() if pos.quantity > 0)
        if active_positions >= self.max_positions:
            return False
        return True

    def _calc_target_percent(self, near_avg_amount: float) -> tuple[float, float]:
        """根据近平均成交额决定仓位大小。"""

        scale = 0.0
        for threshold, tier_scale in self.amount_position_tiers:
            if near_avg_amount >= threshold:
                scale = float(tier_scale)
                break
        if scale <= 0:
            return 0.0, 0.0
        target_percent = min(self.base_position_percent * scale, self.max_position_percent)
        return target_percent, scale

    def _prev_day_suspended(self, bars: deque[Bar] | None, current_trade_date: str | None) -> bool:
        """过滤前一交易日停牌标的。"""

        if not bars or not current_trade_date:
            return False
        for item in reversed(bars):
            if item.trade_date and item.trade_date != current_trade_date:
                return bool(item.suspended)
        return False

    @staticmethod
    def _formula_change(state: SymbolState, current_price: float, window: int) -> float:
        """复现帖子里的 m1/m2/m3 涨幅变化公式。"""

        if state.prev_close in (None, 0) or len(state.prices) <= window:
            return 0.0
        old_price = list(state.prices)[-window - 1]
        return ((current_price - old_price) / (state.prev_close * window)) * 100

    @staticmethod
    def _mean(values: list[float]) -> float:
        """安全求均值。"""

        if not values:
            return 0.0
        return sum(values) / len(values)

    @staticmethod
    def _parse_time(value: str) -> time:
        """解析 HH:MM 或 HH:MM:SS。"""

        fmt = "%H:%M:%S" if len(value.split(":")) == 3 else "%H:%M"
        return datetime.strptime(value, fmt).time()
