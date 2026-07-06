"""行情数据读取与 minute/tick 适配层。"""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from cb_backtest.events import Bar, MarketEvent

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover
    tqdm = None


DEFAULT_MINUTE_SCHEMA = {
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

DEFAULT_TICK_SCHEMA = {
    "symbol": "股票代码",
    "timestamp": "成交时间",
    "price": "成交价",
    "volume": "成交量",
    "amount": "成交额",
    "trade_date": "交易日期",
}


@dataclass(slots=True)
class DataConfig:
    path: Path
    frequency: str = "minute"
    file_pattern: str = "*.pkl"
    schema: dict[str, str] | None = None
    start_date: str | None = None
    end_date: str | None = None
    symbols: list[str] | None = None
    max_symbols: int | None = None
    synthetic_tick_seconds: int | None = None
    minute_price_path_mode: str = "auto"


class MarketDataPortal:
    """读取多标的数据文件，并按全市场时间顺序输出事件。"""

    def __init__(self, config: DataConfig, logger: Callable[[str], None] | None = None):
        self.config = config
        self.logger = logger
        self.total_rows = 0
        self.synthetic_ticks_per_bar = self._calc_synthetic_ticks_per_bar()
        self.schema = config.schema or (
            DEFAULT_TICK_SCHEMA if config.frequency.lower() == "tick" else DEFAULT_MINUTE_SCHEMA
        )

    def iter_events(self) -> Iterator[MarketEvent]:
        frames = self._load_frames()
        heap: list[tuple[pd.Timestamp, int, int, int]] = []

        for frame_idx, frame in enumerate(frames):
            if frame.empty:
                continue
            heapq.heappush(heap, (self._event_timestamp(frame.iloc[0], 0), frame_idx, 0, 0))

        event_frequency = "tick" if self._use_synthetic_ticks() else self.config.frequency
        while heap:
            _, frame_idx, row_idx, sub_idx = heapq.heappop(heap)
            frame = frames[frame_idx]
            row = frame.iloc[row_idx]
            bar = self._row_to_bar(row, sub_idx)
            yield MarketEvent(
                timestamp=bar.timestamp,
                symbol=bar.symbol,
                data={"bar": bar, "raw": row.to_dict()},
                frequency=event_frequency,
            )

            next_sub_idx = sub_idx + 1
            if self._use_synthetic_ticks() and next_sub_idx < self.synthetic_ticks_per_bar:
                heapq.heappush(heap, (self._event_timestamp(row, next_sub_idx), frame_idx, row_idx, next_sub_idx))
                continue

            next_row_idx = row_idx + 1
            if next_row_idx < len(frame):
                heapq.heappush(heap, (self._event_timestamp(frame.iloc[next_row_idx], 0), frame_idx, next_row_idx, 0))

    def _load_frames(self) -> list[pd.DataFrame]:
        files = self._resolve_files()
        self._log(f"[步骤 4/7] 已找到 {len(files)} 个行情文件，开始逐个读取和标准化")
        frames: list[pd.DataFrame] = []
        total_events = 0

        iterator = files
        progress = None
        if tqdm is not None:
            progress = tqdm(files, desc="文件读取", unit="file", dynamic_ncols=True)
            iterator = progress

        for file_path in iterator:
            if progress is None:
                self._log(f"[步骤 4/7] 正在读取文件: {file_path.name}")
            frame = self._read_file(file_path)
            if frame.empty:
                self._log(f"[步骤 4/7] 文件为空，已跳过: {file_path.name}")
                continue

            frame = self._normalize(frame, fallback_symbol=file_path.stem)
            if frame.empty:
                self._log(f"[步骤 4/7] 文件标准化后无有效记录，已跳过: {file_path.name}")
                continue

            frames.append(frame)
            event_count = len(frame) * self.synthetic_ticks_per_bar
            total_events += event_count
            self._log(
                f"[步骤 4/7] 文件读取完成: {file_path.name}，有效记录 {len(frame)} 条，折算事件 {event_count} 条"
            )

        if progress is not None:
            progress.close()

        self.total_rows = total_events
        self._log(
            f"[步骤 4/7] 数据准备完成，共载入 {len(frames)} 个标的，"
            f"原始记录 {sum(len(frame) for frame in frames)} 条，折算事件 {self.total_rows} 条"
        )
        return frames

    def _resolve_files(self) -> list[Path]:
        self._log(f"[步骤 3/7] 正在扫描行情目录: {self.config.path}，文件模式: {self.config.file_pattern}")
        all_files = sorted(self.config.path.glob(self.config.file_pattern))
        if self.config.symbols:
            wanted = {s.lower() for s in self.config.symbols}
            all_files = [p for p in all_files if p.stem.lower() in wanted]
            self._log(f"[步骤 3/7] 已按 symbols 过滤，剩余 {len(all_files)} 个文件")
        if self.config.max_symbols:
            all_files = all_files[: self.config.max_symbols]
            self._log(f"[步骤 3/7] 已应用 max_symbols={self.config.max_symbols}，实际读取 {len(all_files)} 个文件")
        if not all_files:
            raise FileNotFoundError(f"没有找到行情文件: {self.config.path / self.config.file_pattern}")
        self._log(f"[步骤 3/7] 文件扫描完成，命中 {len(all_files)} 个文件")
        return all_files

    @staticmethod
    def _read_file(path: Path) -> pd.DataFrame:
        with path.open("rb") as f:
            header = f.read(8)
        if header.startswith(b"ARROW1"):
            return pd.read_feather(path)
        if path.suffix.lower() in {".csv", ".txt"}:
            return pd.read_csv(path)
        if path.suffix.lower() == ".parquet":
            return pd.read_parquet(path)
        return pd.read_pickle(path)

    def _normalize(self, frame: pd.DataFrame, fallback_symbol: str) -> pd.DataFrame:
        timestamp_col = self.schema["timestamp"]
        if timestamp_col not in frame.columns:
            raise KeyError(f"行情缺少时间列 `{timestamp_col}`，请检查 schema 配置")

        out = frame.copy()
        out["_timestamp"] = pd.to_datetime(out[timestamp_col])
        start = pd.to_datetime(self.config.start_date) if self.config.start_date else None
        end = pd.to_datetime(self.config.end_date) if self.config.end_date else None
        if start is not None:
            out = out[out["_timestamp"] >= start]
        if end is not None:
            out = out[out["_timestamp"] <= end]

        symbol_col = self.schema.get("symbol")
        if symbol_col and symbol_col in out.columns:
            out["_symbol"] = out[symbol_col].astype(str)
        else:
            out["_symbol"] = fallback_symbol

        return out.sort_values("_timestamp").reset_index(drop=True)

    def _row_to_bar(self, row: pd.Series, sub_idx: int = 0) -> Bar:
        if self._use_synthetic_ticks():
            price = self._synthetic_tick_price(row, sub_idx)
            tick_timestamp = self._event_timestamp(row, sub_idx).to_pydatetime()
            volume = self._split_value(self._get_value(row, "volume"), self.synthetic_ticks_per_bar)
            amount = self._split_value(self._get_value(row, "amount"), self.synthetic_ticks_per_bar)
            return Bar(
                timestamp=tick_timestamp,
                symbol=str(row["_symbol"]),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
                amount=amount,
                suspended=bool(self._get_value(row, "suspended", 0)),
                trade_date=str(self._get_value(row, "trade_date", "")) or None,
                extra={
                    "source_frequency": "minute",
                    "synthetic_tick": True,
                    "synthetic_tick_index": sub_idx + 1,
                    "synthetic_tick_count": self.synthetic_ticks_per_bar,
                    "synthetic_tick_seconds": self.config.synthetic_tick_seconds,
                    "minute_open": self._as_float(self._get_value(row, "open")),
                    "minute_high": self._as_float(self._get_value(row, "high")),
                    "minute_low": self._as_float(self._get_value(row, "low")),
                    "minute_close": self._as_float(self._get_value(row, "close")),
                },
            )

        if self.config.frequency.lower() == "tick":
            price = self._get_value(row, "price")
            open_price = high_price = low_price = close_price = float(price) if price is not None else None
        else:
            open_price = self._as_float(self._get_value(row, "open"))
            high_price = self._as_float(self._get_value(row, "high"))
            low_price = self._as_float(self._get_value(row, "low"))
            close_price = self._as_float(self._get_value(row, "close"))

        return Bar(
            timestamp=row["_timestamp"].to_pydatetime(),
            symbol=str(row["_symbol"]),
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=self._as_float(self._get_value(row, "volume")),
            amount=self._as_float(self._get_value(row, "amount")),
            suspended=bool(self._get_value(row, "suspended", 0)),
            trade_date=str(self._get_value(row, "trade_date", "")) or None,
        )

    def _event_timestamp(self, row: pd.Series, sub_idx: int) -> pd.Timestamp:
        end_time = row["_timestamp"]
        if not self._use_synthetic_ticks():
            return end_time

        step = int(self.config.synthetic_tick_seconds or 1)
        total = self.synthetic_ticks_per_bar
        first_tick = end_time - timedelta(seconds=step * (total - 1))
        return first_tick + timedelta(seconds=step * sub_idx)

    def _synthetic_tick_price(self, row: pd.Series, sub_idx: int) -> float:
        open_price = self._as_float(self._get_value(row, "open"))
        high_price = self._as_float(self._get_value(row, "high"))
        low_price = self._as_float(self._get_value(row, "low"))
        close_price = self._as_float(self._get_value(row, "close"))

        fallback = close_price if close_price is not None else open_price
        open_price = fallback if open_price is None else open_price
        high_price = fallback if high_price is None else high_price
        low_price = fallback if low_price is None else low_price
        close_price = fallback if close_price is None else close_price
        if fallback is None:
            return 0.0

        if self.config.minute_price_path_mode == "close_only":
            points = [open_price, close_price]
        else:
            if close_price >= open_price:
                points = [open_price, low_price, high_price, close_price]
            else:
                points = [open_price, high_price, low_price, close_price]

        if self.synthetic_ticks_per_bar <= 1:
            return float(points[-1])

        position = sub_idx / (self.synthetic_ticks_per_bar - 1)
        if len(points) == 2:
            return float(points[0] + (points[1] - points[0]) * position)

        segment_count = len(points) - 1
        scaled = position * segment_count
        segment_idx = min(int(scaled), segment_count - 1)
        local = scaled - segment_idx
        start_price = points[segment_idx]
        end_price = points[segment_idx + 1]
        return float(start_price + (end_price - start_price) * local)

    def _get_value(self, row: pd.Series, key: str, default: Any = None) -> Any:
        col = self.schema.get(key)
        if not col or col not in row:
            return default
        value = row[col]
        if pd.isna(value):
            return default
        return value

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if value is None or pd.isna(value):
            return None
        return float(value)

    @staticmethod
    def _split_value(value: Any, parts: int) -> float | None:
        if value is None or pd.isna(value):
            return None
        if parts <= 1:
            return float(value)
        return float(value) / parts

    def _calc_synthetic_ticks_per_bar(self) -> int:
        if not self._use_synthetic_ticks():
            return 1
        seconds = int(self.config.synthetic_tick_seconds or 1)
        return max(math.ceil(60 / seconds), 1)

    def _use_synthetic_ticks(self) -> bool:
        return self.config.frequency.lower() == "minute" and (self.config.synthetic_tick_seconds or 0) > 0

    def _log(self, message: str) -> None:
        if self.logger is not None:
            self.logger(message)
