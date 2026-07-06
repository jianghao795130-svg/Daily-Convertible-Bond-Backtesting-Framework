"""策略插件包入口。

用户自定义策略可以放在这个目录下，并在 config.py 的 strategy_list 中
通过完整导入路径注册。
"""

from cb_backtest.strategies.base import Strategy

__all__ = ["Strategy"]
