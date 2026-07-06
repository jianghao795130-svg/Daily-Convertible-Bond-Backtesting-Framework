from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from time import perf_counter
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from program.common import (
    get_market_portal,
    get_parallel_worker_count,
    get_pipeline_paths,
    load_config,
    progress,
    save_metadata,
)


def _process_one_file(config_path: str, file_path_str: str, out_dir_str: str) -> tuple[str, int]:
    settings = load_config(config_path)
    portal = get_market_portal(settings)
    file_path = Path(file_path_str)
    out_dir = Path(out_dir_str)

    frame = portal._read_file(file_path)
    frame = portal._normalize(frame, fallback_symbol=file_path.stem)
    if frame.empty:
        return file_path.stem, 0

    records = []
    for row_idx in range(len(frame)):
        row = frame.iloc[row_idx]
        total = portal.synthetic_ticks_per_bar
        for sub_idx in range(total):
            bar = portal._row_to_bar(row, sub_idx)
            records.append(
                {
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
                    "event_frequency": "tick" if total > 1 else settings.frequency,
                }
            )
    event_frame = pd.DataFrame(records).sort_values("timestamp").reset_index(drop=True)
    event_frame.to_pickle(out_dir / f"{file_path.stem}.pkl")
    return file_path.stem, len(event_frame)


def run(settings) -> dict[str, str]:
    started = perf_counter()
    paths = get_pipeline_paths(settings)
    out_dir = paths["step1"]
    portal = get_market_portal(settings)
    files = portal._resolve_files()
    workers = get_parallel_worker_count(settings)
    config_path = str(Path(settings.__file__).resolve())

    saved = 0
    rows = 0
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_process_one_file, config_path, str(file_path), str(out_dir))
            for file_path in files
        ]
        for future in progress(futures, desc="Step1 整理数据", unit="file"):
            symbol, event_rows = future.result()
            if event_rows <= 0:
                continue
            saved += 1
            rows += event_rows

    save_metadata(
        out_dir / "step1_meta.json",
        {
            "files": saved,
            "events": rows,
            "synthetic_ticks_per_bar": portal.synthetic_ticks_per_bar,
            "workers": workers,
        },
    )
    elapsed = perf_counter() - started
    print(f"[Step1] 耗时: {elapsed:.2f} 秒")
    return {
        "output_dir": str(out_dir),
        "files": str(saved),
        "events": str(rows),
        "workers": str(workers),
        "elapsed_seconds": f"{elapsed:.2f}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Step1 整理原始数据为标准事件缓存")
    parser.add_argument("-c", "--config", default="config.py")
    args = parser.parse_args()
    settings = load_config(args.config)
    result = run(settings)
    print(result)


if __name__ == "__main__":
    main()
