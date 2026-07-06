# Daily Convertible Bond Backtesting Framework

An event-driven backtesting framework for convertible bonds, focused on intraday signal generation, candidate selection, and reproducible research workflows.

This project is built around a real working research pipeline rather than a toy demo. It converts raw market data into standardized events, computes factors, selects tradable bonds, replays intraday bars, and generates complete backtest reports.

## Overview

The framework is designed for early-session convertible bond trading research. The current default setup uses minute bars as the raw input, with optional synthetic tick expansion for finer event replay.

At a high level, the strategy flow is:

1. Load and clean raw market data
2. Convert data into standardized event bars
3. Compute factor values on each event
4. Build daily candidate lists
5. Replay intraday events and generate signals
6. Execute trades on the next bar
7. Export fills, equity curve, and summary reports

## Current Trading Logic

The repository currently includes a factor-driven intraday convertible bond workflow centered on:

- factor filters
- candidate selection
- intraday timing signals
- event-driven execution
- position sizing by liquidity tier

The latest execution model is:

- signals are generated only after the current bar is fully known
- buy and sell orders are not filled on the same bar
- all signals are queued and executed on the next available bar
- the default execution price is the next bar open

This makes the replay logic more realistic than same-bar fills and better matches how a live signal would actually be tradable.

## Project Structure

```text
.
├─ cb_backtest/
│  ├─ broker.py
│  ├─ data.py
│  ├─ engine.py
│  ├─ events.py
│  ├─ factor_config.py
│  ├─ report.py
│  ├─ factors/
│  └─ strategies/
├─ program/
│  ├─ common.py
│  ├─ step1_整理数据.py
│  ├─ step2_计算因子.py
│  ├─ step3_选债.py
│  ├─ step4_盘中择时交易.py
│  ├─ step5_生成回测结果.py
│  └─ step6_检查资金曲线异常（可选）.py
├─ docs/
├─ config.py
└─ run_backtest.py
```

## Pipeline

### Step 1. Data Preparation

`program/step1_整理数据.py`

- loads raw market files
- normalizes schema
- filters dates and symbols
- sorts bars
- expands minute bars into synthetic ticks when needed
- saves standardized event caches

### Step 2. Factor Calculation

`program/step2_计算因子.py`

- replays standardized events
- calculates factor values bar by bar
- stores factor caches for later selection and trading

### Step 3. Daily Selection

`program/step3_选债.py`

- reads factor caches
- applies ranking and filtering rules
- generates daily candidate bond lists

### Step 4. Intraday Trading Replay

`program/step4_盘中择时交易.py`

- replays only selected instruments
- evaluates timing signals intraday
- queues signals on the current bar
- executes them on the next bar
- writes fills and equity artifacts

### Step 5. Report Generation

`program/step5_生成回测结果.py`

- reads fills and account curve
- computes summary statistics
- exports tables and charts

### Optional Step 6. Equity Curve Diagnostics

`program/step6_检查资金曲线异常（可选）.py`

- checks for unusual jumps
- helps debug replay or execution issues

## Core Modules

### `cb_backtest/events.py`

Defines the core event objects:

- `MarketEvent`
- `SignalEvent`
- `OrderEvent`
- `FillEvent`
- `Bar`

### `cb_backtest/data.py`

Handles market data loading and conversion between raw files and replayable bar events.

### `cb_backtest/broker.py`

Responsible for:

- translating target-position signals into orders
- handling commissions and slippage
- updating cash and positions
- filling queued orders on the next bar

### `cb_backtest/engine.py`

The main event-driven backtest engine. It wires together:

- data replay
- factor updates
- strategy evaluation
- pending signal execution
- reporting

### `cb_backtest/strategies/`

Contains reusable strategy implementations, including:

- `factor_event_strategy.py`
- `intraday_cb_trend.py`
- `rank_rebalance.py`

## Configuration

The default configuration lives in `config.py`.

Key fields include:

- `backtest_name`
- `start_date`
- `end_date`
- `frequency`
- `data_path`
- `output_dir`
- `pipeline_root_dir`
- `synthetic_tick_seconds`
- `strategy_list`
- `initial_cash`
- `commission_rate`

Default runtime behavior in the current config:

- raw input frequency: `minute`
- synthetic event spacing: `60` seconds
- initial cash: `1_000_000`

That means the current default replay granularity is effectively one event per minute unless you reduce `synthetic_tick_seconds`.

## How To Run

Run the full workflow:

```bash
python run_backtest.py -c config.py
```

Run individual steps:

```bash
python program/step1_整理数据.py -c config.py
python program/step2_计算因子.py -c config.py
python program/step3_选债.py -c config.py
python program/step4_盘中择时交易.py -c config.py
python program/step5_生成回测结果.py -c config.py
```

## Data Policy

This repository does not include:

- raw market data
- local pipeline cache
- generated backtest results
- local IDE files
- Python cache files

The `data/` directory is intentionally excluded from version control.

## Documentation

Additional notes are available in:

- `docs/框架逻辑及策略说明.md`
- `docs/项目文件结构说明.md`
- `docs/Step1_整理数据详细说明.md`
- `docs/config配置示例.py`

## Status

This is an actively evolving research codebase. It is practical and usable, but still oriented toward iterative strategy development rather than a fully packaged production platform.

Likely next improvements include:

- more execution models
- more factor templates
- better config validation
- sample datasets and tests
