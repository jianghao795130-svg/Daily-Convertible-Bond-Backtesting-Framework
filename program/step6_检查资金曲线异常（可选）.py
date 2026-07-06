from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from program.common import get_pipeline_paths, load_config


def run(settings) -> dict[str, str]:
    started = perf_counter()
    paths = get_pipeline_paths(settings)
    step4_dir = paths["step4"]
    output_dir = paths["root"] / "step6_检查资金曲线异常"
    output_dir.mkdir(parents=True, exist_ok=True)

    equity_path = step4_dir / "equity_curve.pkl"
    if not equity_path.exists():
        raise FileNotFoundError(f"未找到资金曲线文件: {equity_path}")

    equity = pd.read_pickle(equity_path).copy()
    if equity.empty:
        raise ValueError("资金曲线为空，无法检查异常。")

    equity["timestamp"] = pd.to_datetime(equity["timestamp"])
    equity = equity.sort_values("timestamp").reset_index(drop=True)
    equity = equity.drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    equity["prev_equity"] = equity["equity"].shift(1)
    equity["equity_change"] = equity["equity"] - equity["prev_equity"]
    equity["equity_change_pct"] = equity["equity"] / equity["prev_equity"] - 1.0
    equity["trade_date"] = equity["timestamp"].dt.strftime("%Y-%m-%d")

    point_threshold = float(getattr(settings, "equity_anomaly_point_threshold", 0.03))
    day_threshold = float(getattr(settings, "equity_anomaly_day_threshold", 0.05))
    top_n = int(getattr(settings, "equity_anomaly_top_n", 50))

    point_anomalies = equity.loc[
        equity["prev_equity"].notna() & (equity["equity_change_pct"].abs() >= point_threshold),
        ["timestamp", "trade_date", "prev_equity", "equity", "equity_change", "equity_change_pct", "cash", "market_value"],
    ].copy()
    point_anomalies = point_anomalies.sort_values("equity_change_pct", key=lambda s: s.abs(), ascending=False).head(top_n)

    daily = (
        equity.groupby("trade_date", as_index=False)
        .agg(
            start_time=("timestamp", "min"),
            end_time=("timestamp", "max"),
            start_equity=("equity", "first"),
            end_equity=("equity", "last"),
            min_equity=("equity", "min"),
            max_equity=("equity", "max"),
        )
        .copy()
    )
    daily["day_return"] = daily["end_equity"] / daily["start_equity"] - 1.0
    daily["intraday_drawdown"] = daily["min_equity"] / daily["start_equity"] - 1.0
    daily["intraday_rally"] = daily["max_equity"] / daily["start_equity"] - 1.0

    daily_anomalies = daily.loc[
        (daily["day_return"].abs() >= day_threshold) | (daily["intraday_drawdown"].abs() >= day_threshold),
        ["trade_date", "start_time", "end_time", "start_equity", "end_equity", "min_equity", "max_equity", "day_return", "intraday_drawdown", "intraday_rally"],
    ].copy()
    if not daily_anomalies.empty:
        daily_anomalies["abs_day_return"] = daily_anomalies["day_return"].abs()
        daily_anomalies = daily_anomalies.sort_values(
            ["intraday_drawdown", "abs_day_return"],
            ascending=[True, False],
        ).drop(columns=["abs_day_return"])

    worst_points = equity.nsmallest(top_n, "equity_change_pct")[
        ["timestamp", "trade_date", "prev_equity", "equity", "equity_change", "equity_change_pct", "cash", "market_value"]
    ].copy()

    worst_days = daily.sort_values(["intraday_drawdown", "day_return"]).head(top_n)[
        ["trade_date", "start_time", "end_time", "start_equity", "end_equity", "min_equity", "max_equity", "day_return", "intraday_drawdown", "intraday_rally"]
    ].copy()

    point_file = output_dir / "资金曲线逐点异常.csv"
    daily_file = output_dir / "资金曲线按日异常.csv"
    worst_point_file = output_dir / "资金曲线最大单点波动.csv"
    worst_day_file = output_dir / "资金曲线最大日内回撤.csv"
    summary_file = output_dir / "检查摘要.txt"

    point_anomalies.to_csv(point_file, index=False, encoding="utf-8-sig")
    daily_anomalies.to_csv(daily_file, index=False, encoding="utf-8-sig")
    worst_points.to_csv(worst_point_file, index=False, encoding="utf-8-sig")
    worst_days.to_csv(worst_day_file, index=False, encoding="utf-8-sig")

    summary_text = "\n".join(
        [
            f"资金曲线文件: {equity_path}",
            f"总事件点数: {len(equity)}",
            f"逐点异常阈值: {point_threshold:.2%}",
            f"按日异常阈值: {day_threshold:.2%}",
            f"逐点异常数量: {len(point_anomalies)}",
            f"按日异常数量: {len(daily_anomalies)}",
            f"最差单点变动: {equity['equity_change_pct'].min():.4%}",
            f"最佳单点变动: {equity['equity_change_pct'].max():.4%}",
            f"最差日收益: {daily['day_return'].min():.4%}",
            f"最大日内回撤: {daily['intraday_drawdown'].min():.4%}",
        ]
    )
    summary_file.write_text(summary_text, encoding="utf-8")

    elapsed = perf_counter() - started
    print(f"[Step6] 耗时: {elapsed:.2f} 秒")
    return {
        "output_dir": str(output_dir),
        "point_anomalies": str(point_file),
        "daily_anomalies": str(daily_file),
        "worst_points": str(worst_point_file),
        "worst_days": str(worst_day_file),
        "summary_file": str(summary_file),
        "elapsed_seconds": f"{elapsed:.2f}",
    }
def main() -> None:
    parser = argparse.ArgumentParser(description="Step6 检查资金曲线异常跳变")
    parser.add_argument("-c", "--config", default="config.py")
    args = parser.parse_args()
    settings = load_config(args.config)
    result = run(settings)
    print(result)


if __name__ == "__main__":
    main()
