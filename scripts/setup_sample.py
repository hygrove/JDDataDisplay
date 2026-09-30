# -*- coding: utf-8 -*-
"""把样例数据 sample_data/ 复制到 ResourceData/，使 run_batch 能摄入样例做本地/异地测试。

为什么需要它：
  ResourceData/ 含每日增长的业务数据，已配置 .gitignore **不进 git**；
  仓库只跟踪 sample_data/ 下的少量样例表。克隆到一台新机器（异地/CI）后，
  先跑本脚本把样例铺到 ResourceData/，再 `python -m backend.jobs.run_batch`
  即可生成 app.db 与页面 JSON，应用能直接跑起来看效果。

用法（仓库根目录执行）：
  python scripts/setup_sample.py
"""
from __future__ import annotations

import shutil
from pathlib import Path

# 仓库根目录：scripts/ 的上级
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "sample_data"
DST = ROOT / "ResourceData"


def main() -> None:
    """复制整棵 sample_data 树到 ResourceData（已存在则覆盖）。"""
    if not SRC.is_dir():
        raise SystemExit(f"找不到样例目录：{SRC}（请确认 sample_data/ 已随仓库克隆下来）")

    # 目标目录不存在则创建；存在则保留其中已有真实数据，仅叠加样例文件
    DST.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in SRC.rglob("*"):
        rel = item.relative_to(SRC)
        target = DST / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # copy2 保留元数据；覆盖已有同名文件，便于样例随仓库更新
        shutil.copy2(item, target)
        copied += 1

    print(f"已复制 {copied} 个样例文件：{SRC} -> {DST}")
    print("下一步运行：python -m backend.jobs.run_batch")


if __name__ == "__main__":
    main()
