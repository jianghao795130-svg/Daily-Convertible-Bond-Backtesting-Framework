from __future__ import annotations

import argparse
from collections import defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from time import perf_counter
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from cb_backtest.factors.base import FactorRegistry
from program.common import (
    build_factor_cache_columns,
    get_factor_instances,
    get_parallel_worker_count,
    get_pipeline_paths,
    iter_symbol_events,
    load_config,
    progress,
    save_metadata,
)


def _calc_one_symbol(config_path: str, step1_file: str, out_file: str) -> tuple[str, int]:
    settings = load_config(config_path)
    factors = get_factor_instances(settings)
    factor_names = [factor.name for factor in factors]
    history: dict[str, deque] = defaultdict(lambda: deque(maxlen=getattr(settings, "history_window", 5000)))
    registry = FactorRegistry()

    frame = pd.read_pickle(step1_file)
    events = iter_symbol_events(frame)
    rows = []
    for event in progress(events, desc=f"{Path(step1_file).stem} 计算因子", unit="event", leave=False):
        bar = event.data["bar"]
        history[bar.symbol].append(bar)
        row = {
            "timestamp": pd.Timestamp(bar.timestamp),
            "symbol": bar.symbol,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "amount": bar.amount,
            "suspended": bar.suspended,
            "trade_date": bar.trade_date,
            "extra": bar.extra,
            "event_frequency": event.frequency,
        }
        for factor in factors:
            value = factor.update(bar, history)
            registry.set_value(factor.name, bar.symbol, value)
            row[factor.name] = value
        rows.append(row)
    pd.DataFrame(rows).to_pickle(out_file)
    return Path(step1_file).stem, len(rows)


def run(settings) -> dict[str, str]:
    started = perf_counter()
    paths = get_pipeline_paths(settings)
    step1_dir = paths["step1"]
    step2_dir = paths["step2"]
    files = sorted(step1_dir.glob("*.pkl"))
    workers = get_parallel_worker_count(settings)
    config_path = str(Path(settings.__file__).resolve())

    total_rows = 0
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_calc_one_symbol, config_path, str(file_path), str(step2_dir / file_path.name))
            for file_path in files
        ]
        for future in progress(futures, desc="Step2 因子并行计算", unit="symbol"):
            symbol, rows = future.result()
            total_rows += rows

    save_metadata(
        step2_dir / "step2_meta.json",
        {
            "files": len(files),
            "events": total_rows,
            "workers": workers,
            "factor_columns": build_factor_cache_columns(settings),
        },
    )
    elapsed = perf_counter() - started
    print(f"[Step2] 耗时: {elapsed:.2f} 秒")
    return {
        "output_dir": str(step2_dir),
        "files": str(len(files)),
        "events": str(total_rows),
        "workers": str(workers),
        "elapsed_seconds": f"{elapsed:.2f}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Step2 并行计算所有事件的因子缓存")
    parser.add_argument("-c", "--config", default="config.py")
    args = parser.parse_args()
    settings = load_config(args.config)
    result = run(settings)
    print(result)


if __name__ == "__main__":
    main()
