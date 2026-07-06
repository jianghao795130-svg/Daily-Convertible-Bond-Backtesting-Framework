from __future__ import annotations

import importlib.util
import json
import os
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from cb_backtest.broker import Broker
from cb_backtest.data import DataConfig, MarketDataPortal
from cb_backtest.engine import _build_factor, _build_strategy, _collect_factor_items
from cb_backtest.events import Bar, FillEvent, MarketEvent
from cb_backtest.factors.base import FactorRegistry
from cb_backtest.report import build_report, build_report_from_frames
from cb_backtest.utils import ensure_dir

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover
    tqdm = None


def load_config(path: str):
    input_path = Path(path)
    if input_path.is_absolute():
        config_path = input_path
    else:
        cwd_candidate = Path.cwd() / input_path
        if cwd_candidate.exists():
            config_path = cwd_candidate
        else:
            project_root = Path(__file__).resolve().parent.parent
            root_candidate = project_root / input_path
            if root_candidate.exists():
                config_path = root_candidate
            else:
                config_path = cwd_candidate
    config_path = config_path.resolve()
    spec = importlib.util.spec_from_file_location("user_backtest_config", config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载配置文件: {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_config_base_dir(settings: Any) -> Path:
    config_file = getattr(settings, "__file__", None)
    if config_file:
        return Path(config_file).resolve().parent
    return Path(__file__).resolve().parent.parent


def resolve_setting_path(settings: Any, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (get_config_base_dir(settings) / path).resolve()


def get_pipeline_root(settings: Any) -> Path:
    base = getattr(settings, "pipeline_root_dir", None)
    if base is None:
        base = Path("data") / "pipeline_cache" / settings.backtest_name
    base = resolve_setting_path(settings, base)
    return ensure_dir(base)


def get_pipeline_paths(settings: Any) -> dict[str, Path]:
    root = get_pipeline_root(settings)
    return {
        "root": root,
        "step1": ensure_dir(root / "step1_整理数据"),
        "step2": ensure_dir(root / "step2_计算因子"),
        "step3": ensure_dir(root / "step3_选债"),
        "step4": ensure_dir(root / "step4_盘中择时交易"),
        "step5": ensure_dir(root / "step5_生成回测结果"),
    }


def get_parallel_worker_count(settings: Any) -> int:
    configured = getattr(settings, "parallel_workers", None)
    if configured is not None:
        return max(int(configured), 1)
    cpu_count = os.cpu_count() or 1
    return max(min(cpu_count, 8), 1)


def progress(iterable, **kwargs):
    if tqdm is None:
        return iterable
    return tqdm(iterable, dynamic_ncols=True, **kwargs)


def get_data_config(settings: Any) -> DataConfig:
    return DataConfig(
        path=resolve_setting_path(settings, settings.data_path),
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


def get_market_portal(settings: Any) -> MarketDataPortal:
    return MarketDataPortal(get_data_config(settings), logger=print)


def get_factor_instances(settings: Any):
    return [_build_factor(item) for item in _collect_factor_items(settings)]


def get_strategy_instance(settings: Any):
    if len(settings.strategy_list) != 1:
        raise ValueError("当前分步流水线暂时只支持单策略运行。")
    return _build_strategy(settings.strategy_list[0])


def get_broker(settings: Any) -> Broker:
    return Broker(
        initial_cash=settings.initial_cash,
        commission_rate=settings.commission_rate,
        min_commission=getattr(settings, "min_commission", 0.0),
        slippage_bps=getattr(settings, "slippage_bps", 0.0),
        lot_size=getattr(settings, "lot_size", 10),
    )


def save_metadata(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_selection_map(path: Path) -> dict[str, set[str]]:
    df = pd.read_pickle(path)
    mapping: dict[str, set[str]] = {}
    for trade_date, group in df.groupby("trade_date"):
        mapping[str(trade_date)] = set(group["symbol"].astype(str))
    return mapping


def build_factor_cache_columns(settings: Any) -> list[str]:
    names = []
    for item in _collect_factor_items(settings):
        names.append(item["name"])
    return names


def frame_row_to_bar(row: pd.Series) -> Bar:
    extra = row.get("extra") if "extra" in row else {}
    if isinstance(extra, float) and pd.isna(extra):
        extra = {}
    return Bar(
        timestamp=pd.to_datetime(row["timestamp"]).to_pydatetime(),
        symbol=str(row["symbol"]),
        open=_nullable_float(row.get("open")),
        high=_nullable_float(row.get("high")),
        low=_nullable_float(row.get("low")),
        close=_nullable_float(row.get("close")),
        volume=_nullable_float(row.get("volume")),
        amount=_nullable_float(row.get("amount")),
        suspended=bool(row.get("suspended", False)),
        trade_date=str(row.get("trade_date", "")) or None,
        extra=extra or {},
    )


def _nullable_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def save_step4_artifacts(step4_dir: Path, broker: Broker) -> dict[str, Path]:
    ensure_dir(step4_dir)
    equity = pd.DataFrame(broker.equity_curve)
    fills = pd.DataFrame([asdict(fill) for fill in broker.fills])
    equity_path = step4_dir / "equity_curve.pkl"
    fills_path = step4_dir / "fills.pkl"
    equity.to_pickle(equity_path)
    fills.to_pickle(fills_path)
    save_metadata(
        step4_dir / "step4_meta.json",
        {
            "initial_cash": broker.initial_cash,
            "commission_rate": broker.commission_rate,
            "min_commission": broker.min_commission,
            "slippage_bps": broker.slippage_bps,
            "lot_size": broker.lot_size,
        },
    )
    return {"equity": equity_path, "fills": fills_path}


def generate_report_from_step4(step4_dir: Path, output_dir: Path, settings: Any) -> dict[str, Path | dict]:
    equity = pd.read_pickle(step4_dir / "equity_curve.pkl")
    fills = pd.read_pickle(step4_dir / "fills.pkl")
    return build_report_from_frames(equity, fills, settings.initial_cash, output_dir)


def iter_symbol_events(frame: pd.DataFrame) -> list[MarketEvent]:
    events: list[MarketEvent] = []
    for row in frame.itertuples(index=False):
        bar = Bar(
            timestamp=pd.to_datetime(row.timestamp).to_pydatetime(),
            symbol=str(row.symbol),
            open=_nullable_float(getattr(row, "open")),
            high=_nullable_float(getattr(row, "high")),
            low=_nullable_float(getattr(row, "low")),
            close=_nullable_float(getattr(row, "close")),
            volume=_nullable_float(getattr(row, "volume")),
            amount=_nullable_float(getattr(row, "amount")),
            suspended=bool(getattr(row, "suspended")),
            trade_date=str(getattr(row, "trade_date")) or None,
            extra=getattr(row, "extra") or {},
        )
        events.append(
            MarketEvent(
                timestamp=bar.timestamp,
                symbol=bar.symbol,
                data={"bar": bar, "raw": {}},
                frequency=str(getattr(row, "event_frequency", "tick")),
            )
        )
    return events
