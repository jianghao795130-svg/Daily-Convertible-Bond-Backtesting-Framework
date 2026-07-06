"""可转债事件驱动回测框架的包入口。

这里暴露最常用的 BacktestEngine，外部脚本只需要：

    from cb_backtest import BacktestEngine

即可创建和运行回测。
"""

from cb_backtest.engine import BacktestEngine

__all__ = ["BacktestEngine"]
