"""框架通用小工具。"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any


def import_object(path: str) -> Any:
    """按字符串导入类或函数。

    config.py 中使用类似 `cb_backtest.factors.momentum.MomentumFactor`
    的路径声明插件，这个函数负责把字符串解析成真实 Python 对象。
    """

    module_name, object_name = path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, object_name)


def ensure_dir(path: str | Path) -> Path:
    """确保目录存在，并返回 Path 对象。"""

    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
