#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""项目级回归总闸：依次跑齐所有验证脚本，任一失败即退出码 1。

为什么不直接用 `npm run verify`：
  前端三套（verify_month / verify_agg / verify_promo）是 node 脚本，
  后端两套（verify_promo_greedy / verify_promo_api）是 python 脚本，
  跨语言没有统一的 package.json 入口。硬塞进 npm 会让「跑前端回归」也依赖 python 环境。

分层：
  - verify_promo_greedy.py  纯算法（贪心 / 波动 / 分档 / 文案），不碰 IO；
  - verify_promo_api.py     端点层（取数 / 汇总 / 空态 / OpenAPI），用 TestClient 不占端口；
  - frontend/verify_*.mjs  前端口径（聚合 / 月份对比），⚠️ verify:agg 需要 8000 端口的服务在跑。

运行：.venv\\Scripts\\python verify_all.py            # 全部
     .venv\\Scripts\\python verify_all.py --backend   # 只跑后端（不依赖前端 node 环境）
"""

import shutil
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
PY = sys.executable

# ⚠️ Windows 上 npm 是 npm.cmd，subprocess 不会像 shell 那样自动补 .cmd 后缀，
#    直接跑 ["npm", ...] 会 FileNotFoundError: [WinError 2]。
NPM = shutil.which("npm") or shutil.which("npm.cmd") or "npm.cmd"

BACKEND = [
    ("promo 算法", [PY, str(ROOT / "backend" / "verify_promo_greedy.py")]),
    ("promo 端点", [PY, str(ROOT / "backend" / "verify_promo_api.py")]),
]

FRONTEND = [
    ("月份对比", [NPM, "run", "verify:month"]),
    ("聚合口径", [NPM, "run", "verify:agg"]),   # ⚠️ 需 8000 端口服务
    ("推广前端", [NPM, "run", "verify:promo"]),
]


def run(label, cmd, cwd):
    print(f"\n{'=' * 50}\n▶ {label}\n{'=' * 50}")
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    out = (r.stdout or "").strip()
    if out:
        print(out[-2000:])
    if r.returncode != 0:
        print(f"✗ {label} 失败（退出码 {r.returncode}）")
        if r.stderr:
            print((r.stderr or "")[-1000:])
    return r.returncode == 0


def main():
    backend_only = "--backend" in sys.argv
    results = []
    for label, cmd in BACKEND:
        results.append((label, run(label, cmd, ROOT)))
    if not backend_only:
        for label, cmd in FRONTEND:
            results.append((label, run(label, cmd, ROOT / "frontend")))

    print(f"\n{'=' * 50}\n汇总\n{'=' * 50}")
    for label, passed in results:
        print(f"  {'✓' if passed else '✗'} {label}")
    failed = [label for label, passed in results if not passed]
    if failed:
        print(f"\n{len(failed)} 项失败：{', '.join(failed)}")
        sys.exit(1)
    print("\n全部通过")


if __name__ == "__main__":
    main()
