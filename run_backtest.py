"""五步流水线总入口。

默认读取项目根目录 config.py，也可以通过 -c/--config 指定其他配置文件。
当前总入口会按顺序执行：
1. 整理数据
2. 计算因子
3. 选债
4. 盘中择时交易
5. 生成回测结果
"""

from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter

from program import step1_整理数据, step2_计算因子, step3_选债, step4_盘中择时交易, step5_生成回测结果
from program.common import load_config


def main() -> None:
    """解析命令行参数，执行五步流水线。"""

    total_started = perf_counter()
    parser = argparse.ArgumentParser(description="可转债事件驱动回测框架")
    parser.add_argument("-c", "--config", default="config.py", help="配置文件路径")
    args = parser.parse_args()

    print("=" * 80)
    print("开始运行回测")
    print("总流程：")
    print("1. 整理原始数据为标准事件缓存")
    print("2. 计算并缓存所有事件的因子值")
    print("3. 根据因子缓存执行每日选债")
    print("4. 根据候选池执行盘中择时交易")
    print("5. 生成成交记录、资金曲线和汇总结果")
    print("=" * 80)
    print(f"[入口] 正在加载配置文件: {args.config}")
    settings = load_config(args.config)
    print(f"[入口] 配置文件加载完成: {Path(settings.__file__).resolve()}")
    print("[入口] 开始执行 Step1: 整理数据")
    result1 = step1_整理数据.run(settings)
    print("[入口] Step1 完成:", result1)
    print("[入口] 开始执行 Step2: 计算因子")
    result2 = step2_计算因子.run(settings)
    print("[入口] Step2 完成:", result2)
    print("[入口] 开始执行 Step3: 选债")
    result3 = step3_选债.run(settings)
    print("[入口] Step3 完成:", result3)
    print("[入口] 开始执行 Step4: 盘中择时交易")
    result4 = step4_盘中择时交易.run(settings)
    print("[入口] Step4 完成:", result4)
    print("[入口] 开始执行 Step5: 生成回测结果")
    result = step5_生成回测结果.run(settings)

    print("=" * 80)
    print("回测完成")
    print("资金曲线:", result["equity"])
    print("成交记录:", result["fills"])
    print("汇总文件:", result["summary_file"])
    print("曲线图:", result["chart"])
    print(f"总耗时: {perf_counter() - total_started:.2f} 秒")
    print("=" * 80)


if __name__ == "__main__":
    main()
