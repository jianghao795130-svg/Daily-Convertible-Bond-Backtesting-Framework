"""回测配置文件。"""

from pathlib import Path

backtest_name = "可转债事件驱动因子框架示例"
start_date = "2023-01-01"
end_date = "2025-03-01"
frequency = "minute"
history_window = 5000
progress_interval = 20000

data_path = Path(r"E:\可转债数据\minute-bond")
file_pattern = "*.pkl"
output_dir = Path("data") / "backtest_results" / backtest_name
pipeline_root_dir = Path("data") / "pipeline_cache" / backtest_name
symbols = None
max_symbols = None
synthetic_tick_seconds = 60
minute_price_path_mode = "auto"
parallel_workers = 30

schema = {
    "symbol": "股票代码",
    "timestamp": "k线结束时间",
    "open": "开盘价",
    "close": "收盘价",
    "high": "最高价",
    "low": "最低价",
    "volume": "成交量",
    "amount": "成交额",
    "suspended": "停牌标记",
    "trade_date": "交易日期",
}

factor_list = []

strategy_list = [
    {
        "class": "cb_backtest.strategies.factor_event_strategy.FactorEventStrategy",
        "name": "可转债事件驱动因子策略",

        # 每个交易日第一次到这个时间，先做候选池筛选
        "rebalance_time": "09:30:00",

        # 最多允许同时持有多少只转债
        "max_positions": 999,

        # 每只转债目标仓位比例
        "position_per_symbol": 0.10,

        # 候选池保留前多少只
        "select_num": 999,

        # 是否过滤前一交易日停牌标的
        "filter_prev_day_suspended": True,

        # 只允许交易这个价格区间内的标的
        "min_trade_price": 1,
        "max_trade_price": 1000,

        # 盘中开始检查买卖信号的时间
        "timing_check_time_start": "09:30:00",

        # 论坛原文里“买入时间定在35分之前”，超过这个时间就不再给该标的开新仓
        "buy_cutoff_time": "09:35:00",

        # 超过这个时间后不再开新仓
        "timing_check_time_end": "10:00:00",

        # 到这个时间后，如果还有持仓则全部强平
        "force_exit_time": "10:00:00",

        # False 表示盘中卖出条件和强平都生效
        "sell_only_at_force_exit": False,

        # 是否启用“论坛帖子复现模式”。
        # 开启后，会使用更贴近帖子原文的买价、卖价、单债限制和仓位分档逻辑。
        "forum_mode_enabled": True,

        # 论坛正文里的原始 tick 参考口径是多少秒。
        # 帖子示例里用的是 dt.seconds / 3，所以这里默认写 3。
        "tick_reference_seconds": 3,

        # 是否把当前事件频率下的“近平均交易量”换算回帖子原始 tick 口径后，再参与仓位分档。
        # 例如你现在 synthetic_tick_seconds=60，代表当前一条事件约等于 60 秒；
        # 帖子按 3 秒 tick 研究，那么这里会把当前量能按 60/3=20 倍换算后再和 600/1000/1500/2000 比较。
        "volume_threshold_scale_with_tick": True,

        # 每次基础开仓仓位。
        # 如果没有触发更高的量能分档，就按这个基础仓位下单。
        "base_position_percent": 0.10,

        # 量能分档仓位倍数。
        # 每一项是 (论坛原始 tick 口径下的近平均交易量阈值, 对基础仓位的放大倍数)。
        # 例如基础仓位 10%，若换算后的近平均交易量 > 1000，则实际目标仓位 = 10% * 2.0 = 20%。
        "volume_tier_multipliers": [
            (600, 1.5),
            (1000, 2.0),
            (1500, 2.5),
            (2000, 3.0),
        ],

        # 同一只转债单日最多买几次。
        "max_buys_per_symbol": 2,

        # 同一只转债单日累计盈利超过多少百分点后，不再继续交易它。
        "max_symbol_profit_pct": 2.0,

        # 同一只转债单日累计亏损次数达到多少次后，不再继续交易它。
        "max_symbol_loss_count": 2,

        # 当无法取“当前事件价和下一事件价均价”时，买入退化为当前价上浮多少百分比。
        # 0.3 表示上浮 0.3%，对应帖子里的 * (1 + 3/1000)。
        "entry_fallback_slippage_pct": 0.3,

        # 卖出价按当前价下浮多少百分比。
        # 0.1 表示下浮 0.1%，对应帖子里的千1滑点。
        "exit_slippage_pct": 0,

        "factor_list": [
            # ("动量", False, 20, 1.0),
        ],

        "filter_list": [
            # ("收盘价", None, "val:>=1", True),
            # ("收盘价", None, "val:<=1000", True),
        ],

        "stock_timing_list": [
            # =========================
            # 买入逻辑
            # =========================
            {
                "logic": "and",
                "conditions": [
                    # condition1:
                    # (
                    #   (m3_累计涨幅 >= 0.4 且 m3_累计涨幅 < 5 且 m3_连续增长次数 > 3)
                    #   或
                    #   (m3_累计涨幅 < 0.4 且 m3_连续增长次数 > 15)
                    # )
                    {
                        "logic": "or",
                        "conditions": [
                            {
                                "logic": "and",
                                "conditions": [
                                    {"name": "m累计涨幅", "params": 3, "method": "val:>=0.4", "signal": "buy", "weight": 1.0},
                                    {"name": "m累计涨幅", "params": 3, "method": "val:<5", "signal": "buy", "weight": 1.0},
                                    {"name": "m连续增长次数", "params": 3, "method": "val:>3", "signal": "buy", "weight": 1.0},
                                ],
                            },
                            {
                                "logic": "and",
                                "conditions": [
                                    {"name": "m累计涨幅", "params": 3, "method": "val:<0.4", "signal": "buy", "weight": 1.0},
                                    {"name": "m连续增长次数", "params": 3, "method": "val:>15", "signal": "buy", "weight": 1.0},
                                ],
                            },
                        ],
                    },

                    # condition2:
                    # m3_涨幅变化 >= m3_涨幅变化速度 * 1
                    {
                        "compare": ">=",
                        "left": {"name": "m涨幅变化", "params": 3},
                        "right": {"name": "m涨幅变化速度", "params": 3},
                        "right_multiplier": 1.0,
                        "signal": "buy",
                    },

                    # condition3:
                    # m3_涨幅变化 < m3_涨幅变化速度 * 2.57
                    {
                        "compare": "<",
                        "left": {"name": "m涨幅变化", "params": 3},
                        "right": {"name": "m涨幅变化速度", "params": 3},
                        "right_multiplier": 2.57,
                        "signal": "buy",
                    },

                    # condition4:
                    # 当前总涨幅 < 12%
                    {"name": "涨幅", "params": None, "method": "val:<12", "signal": "buy", "weight": 1.0},

                    # condition5:
                    # 近平均交易量 >= 200
                    {"name": "近平均交易量", "params": 10, "method": "val:>=200", "signal": "buy", "weight": 1.0},
                ],
            },

            # =========================
            # 卖出逻辑
            # =========================
            {
                "logic": "or",
                "conditions": [
                    # ma2 止盈:
                    # 买入后涨幅 <= 0.3%，且 m2_涨幅变化公式 < 0
                    {
                        "logic": "and",
                        "conditions": [
                            {"field": "amp_after_buy", "method": "val:<=0.3", "signal": "sell"},
                            {"name": "m涨幅变化公式", "params": 2, "method": "val:<0", "signal": "sell", "weight": 1.0},
                        ],
                    },

                    # ma3 止盈:
                    # 0.3% < 买入后涨幅 <= 1%，且 m3_涨幅变化公式 < 0，且 m1_涨幅变化公式 < 0
                    {
                        "logic": "and",
                        "conditions": [
                            {"field": "amp_after_buy", "method": "val:>0.3", "signal": "sell"},
                            {"field": "amp_after_buy", "method": "val:<=1", "signal": "sell"},
                            {"name": "m涨幅变化公式", "params": 3, "method": "val:<0", "signal": "sell", "weight": 1.0},
                            {"name": "m涨幅变化公式", "params": 1, "method": "val:<0", "signal": "sell", "weight": 1.0},
                        ],
                    },

                    # 回落止盈:
                    # 当前买入后涨幅 > 1%，当前已经没有买入信号，
                    # 且 amp_after_buy < max_rose * 0.8
                    {
                        "logic": "and",
                        "conditions": [
                            {"field": "buy_signal", "method": "val:==0", "signal": "sell"},
                            {"field": "amp_after_buy", "method": "val:>1", "signal": "sell"},
                            {
                                "compare": "<",
                                "left": {"field": "amp_after_buy"},
                                "right": {"field": "max_rose"},
                                "right_multiplier": 0.8,
                                "signal": "sell",
                            },
                        ],
                    },

                    # 涨幅过热退出:
                    # 当前总涨幅 > 18%
                    {"name": "涨幅", "params": None, "method": "val:>18", "signal": "sell", "weight": 1.0},

                    # 止损:
                    # m2_涨幅变化 < 0，当前没有买入信号，买入后涨幅 < -0.1%，且至少持仓过 2 个事件
                    {
                        "logic": "and",
                        "conditions": [
                            {"name": "m涨幅变化", "params": 2, "method": "val:<0", "signal": "sell", "weight": 1.0},
                            {"field": "buy_signal", "method": "val:==0", "signal": "sell"},
                            {"field": "amp_after_buy", "method": "val:<-0.1", "signal": "sell"},
                            {"field": "bars_since_entry", "method": "val:>1", "signal": "sell"},
                        ],
                    },
                ],
            },
        ],
    }
]

initial_cash = 1_000_000
commission_rate = 0.1 / 10000
min_commission = 0.0
slippage_bps = 0
lot_size = 10
