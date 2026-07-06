"""Event-driven backtest engine."""

from __future__ import annotations

import importlib
import inspect
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cb_backtest.broker import Broker
from cb_backtest.data import DataConfig, MarketDataPortal
from cb_backtest.events import Bar, MarketEvent, SignalEvent
from cb_backtest.factor_config import parse_filter_factor, parse_score_factor, parse_timing_factor
from cb_backtest.factors.base import Factor, FactorRegistry
from cb_backtest.report import build_report
from cb_backtest.strategies.base import Strategy
from cb_backtest.utils import import_object

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover
    tqdm = None


@dataclass(slots=True)
class EngineConfig:
    data: DataConfig
    broker: Broker
    factors: list[Factor]
    strategies: list[Strategy]
    output_dir: Path
    history_window: int = 500
    progress_interval: int = 5000


class BacktestEngine:
    """Main event loop for the backtest."""

    def __init__(self, config: EngineConfig):
        self.config = config
        self.data = MarketDataPortal(config.data, logger=self._log)
        self.broker = config.broker
        self.factors = config.factors
        self.strategies = config.strategies
        self.factor_registry = FactorRegistry()
        self.history: dict[str, deque[Bar]] = defaultdict(lambda: deque(maxlen=config.history_window))
        self.latest_bars: dict[str, Bar] = {}
        self.pending_signals: dict[str, list[SignalEvent]] = defaultdict(list)
        self.event_count = 0
        self.current_trade_date: str | None = None
        self.total_events = 0

    @classmethod
    def from_settings(cls, settings: Any) -> "BacktestEngine":
        config_dir = _settings_base_dir(settings)
        data_config = DataConfig(
            path=_resolve_settings_path(settings.data_path, config_dir),
            frequency=settings.frequency,
            file_pattern=getattr(settings, "file_pattern", "*.pkl"),
            schema=getattr(settings, "schema", None),
            start_date=getattr(settings, "start_date", None),
            end_date=getattr(settings, "end_date", None),
            symbols=getattr(settings, "symbols", None),
            max_symbols=getattr(settings, "max_symbols", None),
            synthetic_tick_seconds=getattr(settings, "synthetic_tick_seconds", None),
            minute_price_path_mode=getattr(settings, "minute_price_path_mode", "auto"),
        )
        broker = Broker(
            initial_cash=settings.initial_cash,
            commission_rate=settings.commission_rate,
            min_commission=getattr(settings, "min_commission", 0.0),
            slippage_bps=getattr(settings, "slippage_bps", 0.0),
            lot_size=getattr(settings, "lot_size", 10),
        )
        factors = [_build_factor(item) for item in _collect_factor_items(settings)]
        strategies = [_build_strategy(item) for item in settings.strategy_list]
        config = EngineConfig(
            data=data_config,
            broker=broker,
            factors=factors,
            strategies=strategies,
            output_dir=_resolve_settings_path(settings.output_dir, config_dir),
            history_window=getattr(settings, "history_window", 500),
            progress_interval=getattr(settings, "progress_interval", 5000),
        )
        return cls(config)

    def run(self) -> dict:
        self._log("[步骤 1/7] 回测引擎启动")
        self._log(
            f"[步骤 2/7] 参数检查完成: 数据频率={self.config.data.frequency}, "
            f"开始日期={self.config.data.start_date}, 结束日期={self.config.data.end_date}, "
            f"策略数={len(self.strategies)}, 因子数={len(self.factors)}"
        )
        self._log("[步骤 5/7] 开始进入事件循环，依次处理行情、因子、策略、下单和成交")

        progress = None
        if tqdm is not None:
            estimated_total = self.data.total_rows if self.data.total_rows > 0 else None
            progress = tqdm(total=estimated_total, desc="事件回放", unit="event", dynamic_ncols=True)

        for event in self.data.iter_events():
            if self.total_events == 0:
                self.total_events = self.data.total_rows
                self._log(f"[步骤 5/7] 事件总数统计完成，本次预计处理 {self.total_events} 个行情事件")
                if progress is not None and progress.total is None:
                    progress.total = self.total_events
                    progress.refresh()
            self._on_market(event)
            if progress is not None:
                progress.update(1)

        if progress is not None:
            progress.close()

        self._log(
            f"[步骤 6/7] 事件循环结束，共处理 {self.event_count} 个行情事件，累计成交 {len(self.broker.fills)} 笔"
        )
        pending_count = sum(len(items) for items in self.pending_signals.values())
        if pending_count:
            self._log(f"[步骤 6.5/7] 回测结束时仍有 {pending_count} 个未成交信号，已按无后续 bar 处理")
        self._log(f"[步骤 7/7] 开始生成回测结果文件，输出目录: {self.config.output_dir}")
        return build_report(self.broker, self.config.output_dir)

    def _on_market(self, event: MarketEvent) -> None:
        bar: Bar = event.data["bar"]
        self.event_count += 1
        if bar.trade_date and bar.trade_date != self.current_trade_date:
            self.current_trade_date = bar.trade_date
            self._log(
                f"[事件循环] 进入交易日 {bar.trade_date}，当前时间 {bar.timestamp:%Y-%m-%d %H:%M:%S}，"
                f"账户权益 {self.broker.equity():,.2f}"
            )

        self._execute_pending_signals(bar)
        self.history[bar.symbol].append(bar)
        self.latest_bars[bar.symbol] = bar
        self.broker.update_market(bar)

        for factor in self.factors:
            value = factor.update(bar, self.history)
            self.factor_registry.set_value(factor.name, bar.symbol, value)

        for strategy in self.strategies:
            signals = strategy.on_bar(bar, self.history, self.factor_registry, self.broker)
            if signals:
                self._log(
                    f"[事件循环] {bar.timestamp:%Y-%m-%d %H:%M:%S} 策略 {strategy.name} 生成 {len(signals)} 个信号"
                )
            for signal in signals:
                self.pending_signals[signal.symbol].append(signal)
                self._log(
                    f"[事件循环] 信号已挂起，等待下一根 bar 成交: {signal.symbol}，原因: {signal.reason or '无'}"
                )

        if self.event_count % max(self.config.progress_interval, 1) == 0:
            self._log(
                f"[事件循环] 已处理 {self.event_count} 个行情事件，当前时间 "
                f"{bar.timestamp:%Y-%m-%d %H:%M:%S}，当前标的 {bar.symbol}，"
                f"现金 {self.broker.cash:,.2f}，权益 {self.broker.equity():,.2f}"
            )

    def _execute_pending_signals(self, bar: Bar) -> None:
        pending = self.pending_signals.pop(bar.symbol, [])
        if not pending:
            return

        carry_over: list[SignalEvent] = []
        for signal in pending:
            order = self.broker.signal_to_order(signal, bar)
            if order is None:
                self._log(f"[事件循环] 挂起信号未形成订单: {signal.symbol}，原因: {signal.reason or '无'}")
                continue

            fill = self.broker.execute_order(order, bar)
            if fill is None:
                carry_over.append(signal)
                self._log(
                    f"[事件循环] 下一根 bar 未成交，继续等待后续 bar: {order.symbol} {order.side.value} {order.quantity}"
                )
                continue

            self._log(
                f"[事件循环] 下一根 bar 成交完成: {fill.symbol} {fill.side.value} {fill.quantity}，"
                f"成交价 {fill.price:.4f}，手续费 {fill.commission:.4f}，"
                f"当前权益 {self.broker.equity():,.2f}"
            )
            for hooked_strategy in self.strategies:
                hooked_strategy.on_fill(fill, self.broker)

        if carry_over:
            self.pending_signals[bar.symbol].extend(carry_over)

    @staticmethod
    def _log(message: str) -> None:
        print(message)


def _collect_factor_items(settings: Any) -> list[dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}

    for item in getattr(settings, "factor_list", []):
        if isinstance(item, dict):
            name = item["name"]
            params = item.get("params")
            key = item.get("key", _factor_key(name, params))
            items[key] = {
                **item,
                "name": key,
                "display_name": name,
                "params": _normalize_factor_params(name, params),
            }

    for strategy in getattr(settings, "strategy_list", []):
        for raw in strategy.get("factor_list", []):
            spec = parse_score_factor(raw)
            items.setdefault(spec.key, _factor_item_from_name(spec.name, spec.params, spec.key))
        for raw in strategy.get("filter_list", []):
            spec = parse_filter_factor(raw)
            items.setdefault(spec.key, _factor_item_from_name(spec.name, spec.params, spec.key))
        for raw in _flatten_timing_nodes(strategy.get("stock_timing_list", [])):
            spec = parse_timing_factor(raw)
            items.setdefault(spec.key, _factor_item_from_name(spec.name, spec.params, spec.key))

    need_close = any(
        strategy.get("min_trade_price") is not None or strategy.get("max_trade_price") is not None
        for strategy in getattr(settings, "strategy_list", [])
    )
    if need_close:
        items.setdefault("收盘价", _factor_item_from_name("收盘价", None, "收盘价"))

    return list(items.values())


def _flatten_timing_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes or []:
        if "logic" in node:
            out.extend(_flatten_timing_nodes(node.get("conditions", [])))
        elif "compare" in node:
            out.extend(_flatten_compare_operands(node))
        elif "name" in node:
            out.append(node)
    return out


def _flatten_compare_operands(node: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for side in ("left", "right"):
        operand = node.get(side, {})
        if isinstance(operand, dict) and "name" in operand:
            leaf = dict(operand)
            leaf["signal"] = node.get("signal", "buy")
            leaf.setdefault("weight", 1.0)
            leaf.setdefault("method", "val:>=0")
            out.append(leaf)
    return out


def _factor_item_from_name(name: str, params: Any, key: str) -> dict[str, Any]:
    cls = _load_factor_class_by_filename(name)
    if cls is None:
        raise ValueError(
            f"未找到因子 `{name}`。配置里的名字会直接对应 cb_backtest/factors/{name}.py，请创建同名文件。"
        )
    return {
        "name": key,
        "display_name": name,
        "class_obj": cls,
        "params": _normalize_factor_params(name, params),
    }


def _load_factor_class_by_filename(name: str) -> type[Factor] | None:
    module_name = f"cb_backtest.factors.{name}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise

    explicit = getattr(module, "FACTOR_CLASS", None)
    if explicit is not None:
        if not inspect.isclass(explicit) or not issubclass(explicit, Factor):
            raise TypeError(f"{module_name}.FACTOR_CLASS 必须是 Factor 子类")
        return explicit

    candidates = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj is Factor:
            continue
        if issubclass(obj, Factor) and obj.__module__ == module.__name__:
            candidates.append(obj)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"{module_name} 中没有找到 Factor 子类或 FACTOR_CLASS")
    raise ValueError(f"{module_name} 中找到多个 Factor 子类，请显式设置 FACTOR_CLASS")


def _normalize_factor_params(name: str, params: Any) -> dict[str, Any]:
    if params is None:
        return {}
    if isinstance(params, dict):
        return params
    if name in {
        "动量",
        "momentum",
        "分钟涨跌幅",
        "均线偏离",
        "成交额均值",
        "近平均交易量",
        "m累计涨幅",
        "m连续增长次数",
        "m涨幅变化",
        "m涨幅变化速度",
        "m涨幅变化公式",
    }:
        return {"window": int(params)}
    return {"params": params}


def _build_factor(item: dict[str, Any]) -> Factor:
    cls = item.get("class_obj") or import_object(item["class"])
    params = item.get("params", {})
    factor = cls(**params)
    if "name" in item:
        factor.name = item["name"]
    return factor


def _build_strategy(item: dict[str, Any]) -> Strategy:
    cls = import_object(item["class"])
    params = {k: v for k, v in item.items() if k != "class"}
    return cls(**params)


def _factor_key(name: str, params: Any) -> str:
    if params is None:
        return name
    return f"{name}__{repr(params)}"


def _settings_base_dir(settings: Any) -> Path:
    config_file = getattr(settings, "__file__", None)
    if config_file:
        return Path(config_file).resolve().parent
    return Path.cwd()


def _resolve_settings_path(value: Any, base_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()
