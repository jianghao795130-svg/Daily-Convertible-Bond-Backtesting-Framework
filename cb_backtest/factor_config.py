"""策略配置中的因子解析工具。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ScoreFactorSpec:
    name: str
    ascending: bool
    params: Any
    weight: float
    key: str


@dataclass(frozen=True, slots=True)
class FilterFactorSpec:
    name: str
    params: Any
    method: str
    ascending: bool
    key: str


@dataclass(frozen=True, slots=True)
class TimingFactorSpec:
    name: str
    params: Any
    method: str
    signal: str
    weight: float
    key: str


def parse_score_factor(item: tuple) -> ScoreFactorSpec:
    if len(item) != 4:
        raise ValueError(f"factor_list 配置必须是 4 项: {item}")
    name, ascending, params, weight = item
    return ScoreFactorSpec(
        name=str(name),
        ascending=bool(ascending),
        params=params,
        weight=float(weight),
        key=factor_key(str(name), params),
    )


def parse_filter_factor(item: tuple) -> FilterFactorSpec:
    if len(item) == 3:
        name, params, method = item
        ascending = True
    elif len(item) == 4:
        name, params, method, ascending = item
    else:
        raise ValueError(f"filter_list 配置必须是 3 项或 4 项: {item}")
    return FilterFactorSpec(
        name=str(name),
        params=params,
        method=str(method),
        ascending=bool(ascending),
        key=factor_key(str(name), params),
    )


def parse_timing_factor(item: dict[str, Any]) -> TimingFactorSpec:
    if not isinstance(item, dict):
        raise ValueError(f"择时因子节点必须是 dict: {item}")

    name = item.get("name")
    method = item.get("method")
    signal = str(item.get("signal", "buy")).lower()
    if not name:
        raise ValueError(f"择时因子缺少 name: {item}")
    if not method:
        raise ValueError(f"择时因子缺少 method: {item}")
    if signal not in {"buy", "sell"}:
        raise ValueError(f"择时因子的 signal 只支持 buy/sell: {item}")

    params = item.get("params")
    weight = float(item.get("weight", 1.0))
    return TimingFactorSpec(
        name=str(name),
        params=params,
        method=str(method),
        signal=signal,
        weight=weight,
        key=factor_key(str(name), params),
    )


def factor_key(name: str, params: Any) -> str:
    if params is None:
        return name
    return f"{name}__{repr(params)}"
