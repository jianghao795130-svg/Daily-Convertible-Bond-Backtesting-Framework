"""因子插件包入口。

用户自定义因子可以放在这个目录下，并在 config.py 的 factor_list 中
通过完整导入路径注册。
"""

from cb_backtest.factors.base import Factor

__all__ = ["Factor"]
