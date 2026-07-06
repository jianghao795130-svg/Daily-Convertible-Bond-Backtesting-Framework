# 可转债事件驱动回测框架

这是一个面向可转债盘中研究的 Python 事件驱动回测框架，重点解决因子计算、候选债筛选、盘中信号生成、下一根 K 线成交回放，以及结果报告输出。

这个项目不是演示性质的简化脚本，而是一套完整的研究流程：从原始行情整理开始，到事件缓存、因子缓存、选债、盘中交易回放，再到最终回测结果生成，整条链路都已经打通。

## 项目概览

当前框架主要服务于可转债早盘事件驱动策略研究。默认使用分钟线作为原始输入，并支持将分钟线展开为 `synthetic tick` 事件，以便用更细的节奏进行盘中回放。

整体回测流程如下：

1. 读取并清洗原始行情数据
2. 转换为统一的标准事件 `Bar`
3. 在事件流上逐条计算因子
4. 生成每日候选债列表
5. 回放盘中事件并生成买卖信号
6. 将信号延后到下一根 K 线成交
7. 导出成交记录、资金曲线和汇总报告

## 当前交易逻辑

当前仓库内置的主流程以“可转债盘中因子策略”为中心，核心包括：

- 因子过滤
- 候选债筛选
- 盘中择时信号
- 事件驱动执行
- 按流动性分档控制仓位

当前最新的成交模型是：

- 当前 bar 收完以后才允许生成信号
- 买入和卖出都不会在当前 bar 直接成交
- 信号会先挂起，等下一根可用 bar 到来后再执行
- 默认按下一根 bar 的开盘价撮合

这比“当前 bar 直接成交”的回测方式更接近真实交易约束，也更符合信号在实盘中的可执行性。

## 项目结构

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

## 回测流程

### Step 1. 整理数据

`program/step1_整理数据.py`

负责：

- 读取原始行情文件
- 统一字段格式
- 按日期和标的过滤
- 排序并清洗数据
- 按配置将分钟数据展开为 `synthetic tick`
- 保存为标准事件缓存

### Step 2. 计算因子

`program/step2_计算因子.py`

负责：

- 回放标准事件缓存
- 对每个事件逐条计算因子值
- 保存后续选债和盘中交易需要的因子缓存

### Step 3. 选债

`program/step3_选债.py`

负责：

- 读取因子缓存
- 应用过滤与排序规则
- 生成每日候选可转债列表

### Step 4. 盘中择时交易

`program/step4_盘中择时交易.py`

负责：

- 只回放候选债的盘中事件
- 在事件流上判断择时买卖信号
- 当前 bar 挂起信号
- 下一根 bar 执行成交
- 生成成交记录和资金曲线中间结果

### Step 5. 生成回测结果

`program/step5_生成回测结果.py`

负责：

- 读取成交记录和账户曲线
- 计算汇总统计指标
- 导出表格与图表

### Step 6. 资金曲线异常检查（可选）

`program/step6_检查资金曲线异常（可选）.py`

负责：

- 检查资金曲线异常跳变
- 辅助排查回测逻辑或成交回放问题

## 核心模块说明

### `cb_backtest/events.py`

定义回测过程中统一使用的核心事件对象：

- `MarketEvent`
- `SignalEvent`
- `OrderEvent`
- `FillEvent`
- `Bar`

### `cb_backtest/data.py`

负责原始行情加载、字段映射、标准化，以及分钟数据到事件流的转换。

### `cb_backtest/broker.py`

负责：

- 将目标仓位信号转换为具体订单
- 处理手续费和滑点
- 更新现金与持仓
- 将挂起信号放到下一根 bar 成交

### `cb_backtest/engine.py`

这是主事件驱动回测引擎，负责把下面这些环节串起来：

- 行情回放
- 因子更新
- 策略判断
- 挂起信号执行
- 报告生成

### `cb_backtest/strategies/`

包含多个可复用策略实现，例如：

- `factor_event_strategy.py`
- `intraday_cb_trend.py`
- `rank_rebalance.py`

## 配置说明

默认配置文件位于 `config.py`。

核心配置项包括：

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

当前默认配置的关键参数是：

- 原始行情频率：`minute`
- 事件间隔：`synthetic_tick_seconds = 60`
- 初始资金：`1_000_000`

这意味着当前默认最小回放粒度实际上还是 1 分钟。如果你希望在分钟数据上模拟更细的事件节奏，可以把 `synthetic_tick_seconds` 调小。

## 如何运行

运行完整流程：

```bash
python run_backtest.py -c config.py
```

按步骤单独运行：

```bash
python program/step1_整理数据.py -c config.py
python program/step2_计算因子.py -c config.py
python program/step3_选债.py -c config.py
python program/step4_盘中择时交易.py -c config.py
python program/step5_生成回测结果.py -c config.py
```

## 数据说明

本仓库不会包含以下内容：

- 原始行情数据
- 本地流水线缓存
- 回测结果文件
- IDE 本地配置
- Python 缓存文件

`data/` 目录已经明确排除在版本控制之外，不会上传到 GitHub。

## 文档

更多说明见：

- `docs/框架逻辑及策略说明.md`
- `docs/项目文件结构说明.md`
- `docs/Step1_整理数据详细说明.md`
- `docs/config配置示例.py`

## 当前状态

这个项目当前更适合“持续研究和策略迭代”，而不是已经完全产品化的通用回测平台。它已经足够支持真实研究工作，但后续仍然有很大的扩展空间。

下一步比较适合继续完善的方向包括：

- 更多成交撮合规则
- 更多因子模板和策略模板
- 更严格的配置校验
- 更完整的样例数据与测试用例
