# Step1 整理数据详细说明

这份文档专门讲：

- `Step1` 到底是在干嘛
- 为什么它是整个回测的第一步
- 它输入什么
- 处理中做了什么
- 最后输出什么
- 这些输出后面给谁用

如果你是小白，可以把 `Step1` 理解成一句话：

**把原始行情文件，整理成后面因子计算和事件回放都能直接用的标准格式事件缓存。**

---

## 1. Step1 是什么

文件位置：

[program/step1_整理数据.py](E:\可转债事件驱动回测框架\program\step1_整理数据.py)

运行命令：

```powershell
C:\Python314\python.exe program\step1_整理数据.py
```

它是整个五步流程里的第一步：

```text
原始数据
  -> Step1 整理数据
  -> Step2 计算因子
  -> Step3 选债
  -> Step4 盘中择时交易
  -> Step5 生成回测结果
```

所以你可以把它理解成：

- 它不是在回测
- 不是在选债
- 不是在买卖
- 它是在做“准备工作”

---

## 2. 为什么必须先做 Step1

因为你原始数据虽然已经是行情数据了，但它还不一定能直接给后面的步骤使用。

后面的步骤希望拿到的是一种统一格式：

- 每个标的一个标准文件
- 时间列统一
- 代码列统一
- 价格列统一
- 成交量列统一
- 交易日统一
- 是否停牌统一
- 如果要做 synthetic tick，也提前拆好

而原始数据经常会有这些问题：

- 列名是中文，而且不同来源列名不一样
- 时间字段不一定是统一格式
- 有的文件名就是代码，但文件里不一定有代码列
- 原始数据可能是分钟数据，但后面想按更细事件节奏回放
- 原始数据可能带有很多后面不直接需要的格式差异

所以 Step1 的意义就是：

**把“原始数据长什么样”这件事，在第一步就统一掉。**

这样后面 Step2、Step3、Step4 就不用一遍遍重新处理这些脏活。

---

## 3. Step1 的输入是什么

Step1 的输入主要来自 [config.py](E:\可转债事件驱动回测框架\config.py)。

最关键的是这些配置：

```python
data_path
file_pattern
schema
start_date
end_date
symbols
max_symbols
frequency
synthetic_tick_seconds
minute_price_path_mode
parallel_workers
```

### 它实际读取的是什么

Step1 会去你配置的原始数据目录里扫描文件，例如：

```python
data_path = Path(r"E:\可转债数据\minute-bond")
file_pattern = "*.pkl"
```

也就是说，它会去这个目录里找所有匹配的文件。

---

## 4. Step1 运行时到底做了什么

Step1 的工作可以拆成 7 个小动作。

---

## 4.1 先加载配置

Step1 一开始会先加载 `config.py`。

它要先知道：

- 原始数据在哪
- 文件格式是什么
- 读哪些文件
- 起止日期是什么
- 是否只读部分标的
- 分钟数据要不要拆成 synthetic tick
- 开几个并行进程

所以它不是盲读数据，而是按配置读。

---

## 4.2 扫描原始数据目录

底层在 [cb_backtest/data.py](E:\可转债事件驱动回测框架\cb_backtest\data.py) 里的 `MarketDataPortal._resolve_files()` 做这件事。

它会：

1. 扫描 `data_path`
2. 按 `file_pattern` 过滤
3. 如果你配了 `symbols`
   - 只保留这些代码对应文件
4. 如果你配了 `max_symbols`
   - 只取前 N 个文件

这一步的结果就是：

**拿到本次 Step1 要处理的原始行情文件列表。**

---

## 4.3 并行处理每个原始文件

在 [program/step1_整理数据.py](E:\可转债事件驱动回测框架\program\step1_整理数据.py) 里，Step1 用了：

```python
ProcessPoolExecutor
```

意思是：

- 一个文件交给一个子进程去处理
- 多个文件可以同时整理

这里受 `parallel_workers` 控制。

比如你配置：

```python
parallel_workers = 4
```

那就最多同时开 4 个进程整理文件。

所以 Step1 的并行粒度是：

**按文件并行**

不是按行并行，也不是按事件并行。

---

## 4.4 读取单个原始文件

每个文件进来以后，会先执行：

- `_read_file()`

它会根据文件类型决定怎么读：

- `.pkl` -> `pd.read_pickle`
- `.csv/.txt` -> `pd.read_csv`
- `.parquet` -> `pd.read_parquet`
- feather 也支持

所以 Step1 不是只认一种格式，只要底层支持就能读。

---

## 4.5 标准化字段

读取完以后，最重要的一步就是“标准化”。

这个动作在：

[cb_backtest/data.py](E:\可转债事件驱动回测框架\cb_backtest\data.py)

里的：

- `_normalize()`

它主要做这些事：

### 1. 把时间列转成标准时间
比如原始数据里时间列可能叫：

```python
"k线结束时间"
```

然后通过 `schema` 映射成框架认识的 `timestamp`。

并且统一转成 pandas datetime。

### 2. 按起止日期过滤
如果你配了：

```python
start_date = "2025-01-01"
end_date = "2026-01-31"
```

那 Step1 在这里就会把超出日期范围的记录删掉。

### 3. 统一 symbol
如果文件里本身有代码列，就用代码列。

如果没有，就退化为：

- 用文件名作为代码

这样保证后面每一行都有 `symbol`。

### 4. 按时间排序
最后会统一按时间升序排好。

这很重要，因为后面 Step2 和 Step4 都假设：

**单个标的文件内部，时间顺序已经正确。**

---

## 4.6 把分钟数据展开成标准事件

这是 Step1 里最关键、也最容易让小白困惑的一步。

### 你的原始数据通常是什么

你现在大多是分钟数据。

也就是说一行原始数据代表的是：

- 这一分钟的开盘价
- 最高价
- 最低价
- 收盘价
- 成交量
- 成交额

但后面的事件驱动框架想处理的是：

- 一条一条事件

所以 Step1 会把每一行原始分钟线，转成标准 `Bar` 事件记录。

### 如果 `synthetic_tick_seconds = 60`

那一根分钟线就只生成 1 个事件。

也就是：

- 原始 1 分钟
- 对应 1 条事件

### 如果 `synthetic_tick_seconds = 1`

那一根分钟线会拆成 60 个伪 tick 事件。

也就是：

- 一分钟
- 被拆成每秒 1 个事件

### Step1 在这里具体做了什么

在 [program/step1_整理数据.py](E:\可转债事件驱动回测框架\program\step1_整理数据.py) 里，逻辑是：

1. 遍历原始文件里的每一行
2. 看这一行会拆成多少个 synthetic tick
3. 每个 sub tick 调一次 `_row_to_bar(row, sub_idx)`
4. 得到标准 `Bar`
5. 再把 `Bar` 转成统一字段字典，收集到 `records`

最后变成一个标准事件表。

---

## 4.7 为什么这里会有 `extra`

Step1 输出时会保留一个字段：

```python
extra
```

这个字段主要是把一些辅助信息也存进去，比如：

- 这是分钟数据拆出来的 synthetic tick
- 当前是第几个 synthetic tick
- 一共拆了几个
- `synthetic_tick_seconds` 是多少
- 原始分钟的 `open/high/low/close`

你可以理解成：

**`extra` 是“附加说明信息”，主逻辑不一定总用，但后面某些策略细节可能要用。**

---

## 5. Step1 的输出是什么

Step1 的输出目录在：

```text
data/pipeline_cache/<backtest_name>/step1_整理数据/
```

例如你现在一般会是：

[step1_整理数据](E:\可转债事件驱动回测框架\data\pipeline_cache\可转债事件驱动因子框架示例\step1_整理数据)

里面通常有：

- 每个标的一个 `.pkl`
- 一个 `step1_meta.json`

### 每个标的一个文件

例如：

```text
sh111023.pkl
sz123001.pkl
...
```

这些文件里已经不是原始分钟数据格式了，而是：

**框架标准事件缓存**

列大概像这样：

- `timestamp`
- `symbol`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `amount`
- `suspended`
- `trade_date`
- `extra`
- `event_frequency`

### `step1_meta.json`

这个文件记录本次 Step1 的摘要：

- 处理了多少个文件
- 一共生成多少条事件
- 每根 bar 拆成多少个 synthetic tick
- 用了多少个并行 worker

它是给你检查用的。

---

## 6. Step1 输出给谁用

Step1 的输出主要给：

### Step2 用

[program/step2_计算因子.py](E:\可转债事件驱动回测框架\program\step2_计算因子.py)

它会读取 Step1 的标准事件缓存，然后逐条算因子。

### Step4 间接受益

虽然 Step4 直接读的是 Step2 因子缓存，但如果没有 Step1 的标准事件结构，后面这条链条根本搭不起来。

所以 Step1 是整个事件驱动流程的地基。

---

## 7. Step1 和真正回测的关系

Step1 本身不做这些事：

- 不计算因子
- 不选债
- 不买卖
- 不算收益
- 不画图

它只做一件事：

**把原始数据整理成标准事件缓存。**

所以你看到 Step1 跑了很久，不代表在回测，只代表它在做“数据预处理”。

---

## 8. 为什么 Step1 做完以后，后面会更方便

因为如果没有 Step1，后面每一步都得重复做这些事：

- 再读原始文件
- 再认列名
- 再转时间
- 再过滤日期
- 再决定 symbol
- 再拆 synthetic tick

这样会非常慢，也很乱。

Step1 的好处是：

- 把这些脏活累活一次做完
- 后面都直接吃标准缓存

这就是流水线思路。

---

## 9. 小白最容易混淆的地方

### 误区 1：Step1 是不是已经开始回测了
不是。

Step1 只是数据整理。

### 误区 2：Step1 会不会生成买卖信号
不会。

买卖信号是 Step4 才会生成。

### 误区 3：Step1 输出的 `.pkl` 是不是原始数据备份
不是简单备份。

它已经是整理后的标准事件缓存了。

### 误区 4：改了日期后能不能跳过 Step1
一般不能。

因为 Step1 已经把日期过滤做进缓存了。

---

## 10. 一句话总结

你可以把 Step1 理解成：

**把你原始的分钟行情文件，清洗、统一、排序、按配置过滤，并在需要时拆成事件流，最终保存成后面步骤都能直接用的标准事件缓存。**
