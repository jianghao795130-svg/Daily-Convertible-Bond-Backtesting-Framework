"""回测结果输出模块。"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager
from matplotlib.ticker import MaxNLocator

from cb_backtest.broker import Broker
from cb_backtest.utils import ensure_dir


def _configure_chinese_font() -> None:
    """尽量为 matplotlib 选择可用的中文字体。"""

    candidates = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "PingFang SC",
        "WenQuanYi Zen Hei",
        "Arial Unicode MS",
    ]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            break
    plt.rcParams["axes.unicode_minus"] = False


def build_report(broker: Broker, output_dir: str | Path) -> dict[str, Path | dict]:
    """生成资金曲线、成交记录、策略评价和图表文件。"""

    equity = pd.DataFrame(broker.equity_curve)
    fills = pd.DataFrame([asdict(fill) for fill in broker.fills])
    return build_report_from_frames(equity, fills, broker.initial_cash, output_dir)


def build_report_from_frames(
    equity: pd.DataFrame,
    fills: pd.DataFrame,
    initial_cash: float,
    output_dir: str | Path,
) -> dict[str, Path | dict]:
    """直接用资金曲线和成交表生成报告。"""

    out = ensure_dir(output_dir)
    _configure_chinese_font()

    if not equity.empty:
        equity = equity.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
        equity["timestamp"] = pd.to_datetime(equity["timestamp"])
        equity["return"] = equity["equity"].pct_change().fillna(0.0)
        equity["net_value"] = equity["equity"] / float(initial_cash)
        equity["cummax"] = equity["equity"].cummax()
        equity["drawdown"] = equity["equity"] / equity["cummax"] - 1.0
        equity["drawdown_pct"] = equity["drawdown"] * 100
        equity["position_ratio"] = (equity["market_value"] / equity["equity"]).fillna(0.0).clip(lower=0.0, upper=1.0)
    else:
        equity = pd.DataFrame(
            columns=[
                "timestamp",
                "cash",
                "market_value",
                "equity",
                "return",
                "net_value",
                "cummax",
                "drawdown",
                "position_ratio",
            ]
        )

    summary = _summary(equity, fills, initial_cash)
    equity_path = out / "equity_curve.csv"
    fills_path = out / "fills.csv"
    summary_path = out / "summary.csv"
    chart_path = out / "equity_drawdown.png"

    equity.to_csv(equity_path, index=False, encoding="utf-8-sig")
    fills.to_csv(fills_path, index=False, encoding="utf-8-sig")
    pd.DataFrame([summary]).to_csv(summary_path, index=False, encoding="utf-8-sig")
    _plot_equity_and_drawdown(equity, chart_path)

    return {
        "summary": summary,
        "equity": equity_path,
        "fills": fills_path,
        "summary_file": summary_path,
        "chart": chart_path,
    }


def _summary(equity: pd.DataFrame, fills: pd.DataFrame, initial_cash: float) -> dict:
    """计算策略评价指标。"""

    if equity.empty:
        return {
            "初始资金": initial_cash,
            "净值": 1.0,
            "期末权益": initial_cash,
            "总收益率": 0.0,
            "年化收益": 0.0,
            "最大回撤": 0.0,
            "年化收益回撤比": 0.0,
            "夏普比率": 0.0,
            "胜率": 0.0,
            "收益率标准差": 0.0,
            "事件数": 0,
            "成交笔数": 0,
        }

    final_equity = float(equity.iloc[-1]["equity"])
    net_value = float(equity.iloc[-1]["net_value"])
    total_return = final_equity / initial_cash - 1.0
    max_drawdown = float(equity["drawdown"].min())
    return_std = float(equity["return"].std(ddof=0))

    start_ts = pd.to_datetime(equity.iloc[0]["timestamp"])
    end_ts = pd.to_datetime(equity.iloc[-1]["timestamp"])
    total_seconds = max((end_ts - start_ts).total_seconds(), 1.0)
    years = total_seconds / (365.25 * 24 * 60 * 60)
    annual_return = net_value ** (1 / years) - 1 if years > 0 else 0.0

    sharpe = 0.0
    if return_std > 0:
        sharpe = float((equity["return"].mean() / return_std) * (252**0.5))

    annual_return_drawdown_ratio = 0.0
    if abs(max_drawdown) > 1e-12:
        annual_return_drawdown_ratio = annual_return / abs(max_drawdown)

    win_rate = 0.0
    if not fills.empty:
        sell_fills = fills[fills["side"] == "SELL"].copy()
        if not sell_fills.empty:
            sell_fills["signed_return"] = sell_fills["price"] * sell_fills["quantity"] - sell_fills["commission"]
            win_rate = float((sell_fills["signed_return"] > 0).mean())

    return {
        "初始资金": float(initial_cash),
        "净值": net_value,
        "期末权益": final_equity,
        "总收益率": total_return,
        "年化收益": annual_return,
        "最大回撤": max_drawdown,
        "年化收益回撤比": annual_return_drawdown_ratio,
        "夏普比率": sharpe,
        "胜率": win_rate,
        "收益率标准差": return_std,
        "事件数": int(len(equity)),
        "成交笔数": int(len(fills)),
    }


def _plot_equity_and_drawdown(equity: pd.DataFrame, output_path: Path) -> None:
    """绘制净值曲线和回撤图。"""

    if equity.empty:
        return

    fig, (ax_left, ax_bottom) = plt.subplots(
        2,
        1,
        figsize=(16, 9),
        gridspec_kw={"height_ratios": [4.5, 1.1], "hspace": 0.06},
        sharex=True,
    )
    ax_right = ax_left.twinx()

    ax_left.plot(equity["timestamp"], equity["net_value"], color="#4A86F7", linewidth=1.8, label="净值曲线", zorder=3)
    ax_left.axhline(1.0, color="#4CAF50", linewidth=1.5, alpha=0.9)
    ax_left.set_ylabel("净值", color="#1f1f1f")
    ax_left.tick_params(axis="y", labelcolor="#1f77b4")
    ax_left.set_facecolor("#F5F6F8")

    drawdown_series = equity["drawdown"]
    ax_right.fill_between(
        equity["timestamp"],
        0,
        drawdown_series,
        color="#F7D54A",
        alpha=0.5,
        label="回撤",
        zorder=1,
    )
    ax_right.set_ylabel("回撤", color="#1f1f1f")
    ax_right.tick_params(axis="y", labelcolor="#1f1f1f")

    max_dd = float(drawdown_series.min())
    ax_right.set_ylim(min(max_dd * 1.15, -0.02), 0)
    ax_right.yaxis.set_major_locator(MaxNLocator(nbins=6))

    net_value_min = float(equity["net_value"].min())
    net_value_max = float(equity["net_value"].max())
    net_value_span = max(net_value_max - net_value_min, 1e-6)
    lower = max(net_value_min - net_value_span * 0.08, 0)
    upper = net_value_max + net_value_span * 0.08
    if upper - lower < 0.05:
        center = (net_value_max + net_value_min) / 2
        lower = max(center - 0.025, 0)
        upper = center + 0.025
    ax_left.set_ylim(lower, upper)
    ax_left.grid(True, axis="both", linestyle="-", alpha=0.18, color="#BFC7D5")

    position_ratio = equity["position_ratio"].fillna(0.0)
    ax_bottom.fill_between(
        equity["timestamp"],
        0,
        position_ratio,
        color="#62B9F5",
        alpha=0.9,
    )
    ax_bottom.set_ylim(0, 1)
    ax_bottom.set_yticks([0, 0.5, 1.0])
    ax_bottom.set_yticklabels(["0", "0.5", "1"])
    ax_bottom.set_ylabel("仓位")
    ax_bottom.set_facecolor("#F5F6F8")
    ax_bottom.grid(True, axis="both", linestyle="-", alpha=0.16, color="#BFC7D5")

    start_text = equity["timestamp"].iloc[0].strftime("%Y/%m/%d")
    end_text = equity["timestamp"].iloc[-1].strftime("%Y/%m/%d")
    annual_return = _safe_pct(_summary_value(equity, "annual_return"))
    max_drawdown_text = _safe_pct(abs(max_dd))
    ratio_text = _safe_num(_summary_value(equity, "annual_return_drawdown_ratio"))
    title_text = (
        f"年化收益:{annual_return}  最大回撤:-{max_drawdown_text}  "
        f"收益回撤比:{ratio_text}  回测区间：{start_text} - {end_text}"
    )
    fig.suptitle(title_text, fontsize=16, y=0.98)

    left_handles, left_labels = ax_left.get_legend_handles_labels()
    right_handles, right_labels = ax_right.get_legend_handles_labels()
    ax_left.legend(left_handles + right_handles, left_labels + right_labels, loc="upper left")

    fig.autofmt_xdate()
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def _summary_value(equity: pd.DataFrame, key: str) -> float:
    final_equity = float(equity.iloc[-1]["equity"])
    initial_cash = float(equity.iloc[0]["equity"] / equity.iloc[0]["net_value"]) if equity.iloc[0]["net_value"] else final_equity
    net_value = float(equity.iloc[-1]["net_value"])
    max_drawdown = float(equity["drawdown"].min())
    start_ts = pd.to_datetime(equity.iloc[0]["timestamp"])
    end_ts = pd.to_datetime(equity.iloc[-1]["timestamp"])
    total_seconds = max((end_ts - start_ts).total_seconds(), 1.0)
    years = total_seconds / (365.25 * 24 * 60 * 60)
    annual_return = net_value ** (1 / years) - 1 if years > 0 else 0.0
    annual_return_drawdown_ratio = annual_return / abs(max_drawdown) if abs(max_drawdown) > 1e-12 else 0.0
    mapping = {
        "annual_return": annual_return,
        "annual_return_drawdown_ratio": annual_return_drawdown_ratio,
        "initial_cash": initial_cash,
        "final_equity": final_equity,
    }
    return float(mapping[key])


def _safe_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _safe_num(value: float) -> str:
    return f"{value:.2f}"
