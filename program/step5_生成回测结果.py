from __future__ import annotations

import argparse
from time import perf_counter
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from program.common import generate_report_from_step4, get_pipeline_paths, load_config


def run(settings) -> dict[str, str]:
    started = perf_counter()
    paths = get_pipeline_paths(settings)
    print("[Step5] 正在根据 Step4 缓存生成最终报告")
    report = generate_report_from_step4(paths["step4"], paths["step5"], settings)
    elapsed = perf_counter() - started
    print(f"[Step5] 耗时: {elapsed:.2f} 秒")
    return {
        "summary_file": str(report["summary_file"]),
        "equity": str(report["equity"]),
        "fills": str(report["fills"]),
        "chart": str(report["chart"]),
        "elapsed_seconds": f"{elapsed:.2f}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Step5 根据交易结果生成回测报告")
    parser.add_argument("-c", "--config", default="config.py")
    args = parser.parse_args()
    settings = load_config(args.config)
    result = run(settings)
    print(result)


if __name__ == "__main__":
    main()
