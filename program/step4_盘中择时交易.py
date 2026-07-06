from __future__ import annotations

import argparse
import heapq
import sys
from collections import defaultdict, deque
from pathlib import Path
from time import perf_counter

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from cb_backtest.events import SignalEvent
from cb_backtest.factors.base import FactorRegistry
from program.common import (
    frame_row_to_bar,
    get_broker,
    get_pipeline_paths,
    get_strategy_instance,
    load_config,
    load_selection_map,
    progress,
    save_metadata,
    save_step4_artifacts,
)


def run(settings) -> dict[str, str]:
    started = perf_counter()
    paths = get_pipeline_paths(settings)
    step2_dir = paths["step2"]
    step3_dir = paths["step3"]
    step4_dir = paths["step4"]

    selection_map = load_selection_map(step3_dir / "selected_symbols.pkl")
    used_symbols = sorted({symbol for symbols in selection_map.values() for symbol in symbols})
    frames = []
    for symbol in progress(used_symbols, desc="Step4 读取候选池事件", unit="symbol"):
        file_path = step2_dir / f"{symbol}.pkl"
        if file_path.exists():
            frame = pd.read_pickle(file_path).sort_values("timestamp").reset_index(drop=True)
            if not frame.empty:
                frames.append(frame)

    heap: list[tuple[pd.Timestamp, int, int]] = []
    for frame_idx, frame in enumerate(frames):
        heapq.heappush(heap, (pd.to_datetime(frame.iloc[0]["timestamp"]), frame_idx, 0))

    broker = get_broker(settings)
    strategy = get_strategy_instance(settings)
    strategy._is_selection_time = lambda bar: False  # type: ignore[attr-defined]
    factor_registry = FactorRegistry()
    history: dict[str, deque] = defaultdict(lambda: deque(maxlen=getattr(settings, "history_window", 5000)))
    pending_signals: dict[str, list[SignalEvent]] = defaultdict(list)
    current_trade_date = None
    event_count = 0

    factor_columns = []
    if frames:
        base_cols = {
            "timestamp",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "suspended",
            "trade_date",
            "extra",
            "event_frequency",
        }
        factor_columns = [col for col in frames[0].columns if col not in base_cols]

    total_events = sum(len(frame) for frame in frames)
    event_bar = progress(range(total_events), desc="Step4 盘中事件回放", unit="event")
    for _ in event_bar:
        if not heap:
            break
        _, frame_idx, row_idx = heapq.heappop(heap)
        frame = frames[frame_idx]
        row = frame.iloc[row_idx]
        bar = frame_row_to_bar(row)

        if current_trade_date != bar.trade_date:
            current_trade_date = bar.trade_date
            strategy.state.selected_symbols = set(selection_map.get(str(bar.trade_date), set()))
            strategy.state.last_selection_ts = bar.timestamp

        carry_over: list[SignalEvent] = []
        for signal in pending_signals.pop(bar.symbol, []):
            order = broker.signal_to_order(signal, bar)
            if order is None:
                continue
            fill = broker.execute_order(order, bar)
            if fill is None:
                carry_over.append(signal)
                continue
            strategy.on_fill(fill, broker)
        if carry_over:
            pending_signals[bar.symbol].extend(carry_over)

        history[bar.symbol].append(bar)
        broker.update_market(bar)
        for factor_name in factor_columns:
            factor_registry.set_value(factor_name, bar.symbol, row.get(factor_name))

        signals = strategy.on_bar(bar, history, factor_registry, broker)
        for signal in signals:
            pending_signals[signal.symbol].append(signal)

        event_count += 1
        next_row = row_idx + 1
        if next_row < len(frame):
            heapq.heappush(heap, (pd.to_datetime(frame.iloc[next_row]["timestamp"]), frame_idx, next_row))

    artifact_paths = save_step4_artifacts(step4_dir, broker)
    save_metadata(
        step4_dir / "step4_meta_extra.json",
        {
            "events": event_count,
            "symbols": len(used_symbols),
            "pending_signals_at_end": sum(len(items) for items in pending_signals.values()),
        },
    )
    elapsed = perf_counter() - started
    print(f"[Step4] 耗时: {elapsed:.2f} 秒")
    return {
        "equity_file": str(artifact_paths["equity"]),
        "fills_file": str(artifact_paths["fills"]),
        "events": str(event_count),
        "elapsed_seconds": f"{elapsed:.2f}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Step4 使用选债结果和因子缓存执行盘中择时交易")
    parser.add_argument("-c", "--config", default="config.py")
    args = parser.parse_args()
    settings = load_config(args.config)
    result = run(settings)
    print(result)


if __name__ == "__main__":
    main()
