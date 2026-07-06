from __future__ import annotations

import argparse
from collections import defaultdict
from time import perf_counter
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from cb_backtest.factor_config import parse_filter_factor, parse_score_factor
from cb_backtest.strategies.factor_event_strategy import FactorEventStrategy
from program.common import get_pipeline_paths, load_config, progress, save_metadata


def run(settings) -> dict[str, str]:
    started = perf_counter()
    paths = get_pipeline_paths(settings)
    step2_dir = paths["step2"]
    step3_dir = paths["step3"]
    strategy_cfg = settings.strategy_list[0]
    strategy = FactorEventStrategy(**{k: v for k, v in strategy_cfg.items() if k != "class"})

    symbol_frames = {}
    for file_path in progress(sorted(step2_dir.glob("*.pkl")), desc="Step3 读取因子缓存", unit="file"):
        frame = pd.read_pickle(file_path)
        if not frame.empty:
            symbol_frames[file_path.stem] = frame

    by_date: dict[str, list[dict]] = defaultdict(list)
    rebalance_time = pd.to_datetime(strategy_cfg["rebalance_time"]).time()
    score_specs = [parse_score_factor(item) for item in strategy_cfg.get("factor_list", [])]
    filter_specs = [parse_filter_factor(item) for item in strategy_cfg.get("filter_list", [])]

    for symbol, frame in progress(symbol_frames.items(), desc="Step3 提取每日快照", unit="symbol"):
        grouped = frame.groupby("trade_date")
        for trade_date, group in grouped:
            group = group.sort_values("timestamp")
            rebalance_row = group[group["timestamp"].dt.time >= rebalance_time].head(1)
            if rebalance_row.empty:
                continue
            row = rebalance_row.iloc[0]
            prev_day_rows = frame[frame["trade_date"] < trade_date].sort_values("timestamp")
            prev_suspended = bool(prev_day_rows.iloc[-1]["suspended"]) if not prev_day_rows.empty else False
            record = {
                "trade_date": str(trade_date),
                "symbol": symbol,
                "timestamp": row["timestamp"],
                "prev_day_suspended": prev_suspended,
                "收盘价": row.get("收盘价", row.get("close")),
            }
            for spec in score_specs:
                record[spec.key] = row.get(spec.key)
            for spec in filter_specs:
                record[spec.key] = row.get(spec.key)
            by_date[str(trade_date)].append(record)

    selected_rows = []
    for trade_date, records in progress(sorted(by_date.items()), desc="Step3 每日选债", unit="day"):
        candidates = pd.DataFrame(records)
        if candidates.empty:
            continue
        universe = set(candidates["symbol"].astype(str))
        if strategy.min_trade_price is not None or strategy.max_trade_price is not None:
            price_col = "收盘价"
            if strategy.min_trade_price is not None:
                candidates = candidates[candidates[price_col] >= strategy.min_trade_price]
            if strategy.max_trade_price is not None:
                candidates = candidates[candidates[price_col] <= strategy.max_trade_price]
        if strategy.filter_prev_day_suspended:
            candidates = candidates[~candidates["prev_day_suspended"]]
        for spec in filter_specs:
            op_pass = candidates[spec.key].apply(lambda x: strategy._match_method(float(x), spec.method) if pd.notna(x) else False)
            candidates = candidates[op_pass]
        if candidates.empty:
            continue
        if score_specs:
            rank_score = pd.Series(0.0, index=candidates.index)
            for spec in score_specs:
                ranked = candidates[["symbol", spec.key]].dropna().sort_values(spec.key, ascending=spec.ascending)
                rank_map = {idx: rank for rank, idx in enumerate(ranked.index, start=1)}
                rank_score = rank_score.add(pd.Series(rank_map, dtype=float).reindex(rank_score.index).fillna(len(candidates) + 1) * spec.weight, fill_value=0.0)
            candidates = candidates.assign(_score=rank_score).sort_values("_score")
        else:
            candidates = candidates.sort_values("symbol")
        top = candidates.head(int(strategy.select_num))
        for _, row in top.iterrows():
            selected_rows.append({"trade_date": trade_date, "symbol": row["symbol"], "timestamp": row["timestamp"]})

    selected_df = pd.DataFrame(selected_rows).sort_values(["trade_date", "symbol"]).reset_index(drop=True)
    out_file = step3_dir / "selected_symbols.pkl"
    selected_df.to_pickle(out_file)
    selected_df.to_csv(step3_dir / "selected_symbols.csv", index=False, encoding="utf-8-sig")
    save_metadata(
        step3_dir / "step3_meta.json",
        {"days": int(selected_df["trade_date"].nunique()) if not selected_df.empty else 0, "rows": int(len(selected_df))},
    )
    elapsed = perf_counter() - started
    print(f"[Step3] 耗时: {elapsed:.2f} 秒")
    return {"selection_file": str(out_file), "rows": str(len(selected_df)), "elapsed_seconds": f"{elapsed:.2f}"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Step3 根据因子缓存执行每日选债")
    parser.add_argument("-c", "--config", default="config.py")
    args = parser.parse_args()
    settings = load_config(args.config)
    result = run(settings)
    print(result)


if __name__ == "__main__":
    main()
