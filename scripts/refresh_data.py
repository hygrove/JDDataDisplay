# -*- coding: utf-8 -*-
"""每日数据刷新入口（跨平台核心逻辑，Windows / Linux / Docker 通用）。

为什么单独抽一个 Python 入口：
  「计划任务」是 Windows 专属概念，而「刷新数据」这件事本身与操作系统无关。
  把逻辑放在这里之后：Windows 用计划任务调它，Linux / Docker 用 cron 或 entrypoint 调它，
  命令完全一样——
      python scripts/refresh_data.py
  将来迁到 Docker 时不需要重写任何业务代码，只换调度方式即可。

迁移兼容性设计（换电脑 / 换盘符 / 换系统都成立）：
  1. 项目根 ROOT 由 __file__ 推导，绝不写死 E:\\ 之类的绝对路径；
  2. 主动把 ROOT 塞进 sys.path，保证从任意工作目录（cron 常常是 /）被唤起都能 import backend；
  3. 「找哪个 python 解释器」交给外层启动器在运行期解析，不把路径烧进计划任务里，
     这样 .venv 重建或位置变动后，定时任务依然能跑（见 refresh_daily.ps1）；
  4. 刷新接口地址走环境变量 JD_REFRESH_URL，Docker 里换 host/port 不用改代码。

职责：
  1. 调用 backend.jobs.run_batch.run_once() 跑完整批处理（Excel -> SQLite -> JSON）。
     **这一步不依赖 uvicorn 服务是否启动**，服务没起也照样把最新数据准备好；
  2. 可选：尽力 POST /api/refresh，仅用于让「正好在运行」的服务立刻清缓存（失败忽略）；
  3. 把结果与批处理捕获输出以 UTF-8 追加写进 logs/refresh.log。

环境变量：
  JD_REFRESH_URL            刷新接口地址，默认 http://127.0.0.1:8000/api/refresh
  JD_REFRESH_POST_TIMEOUT   尽力 POST 的超时秒数，默认 10

用法：
  python scripts/refresh_data.py            # 跑批 + 尽力 POST
  python scripts/refresh_data.py --no-post  # 只跑批（Docker / 无服务场景推荐）

退出码：
  0 = 成功；1 = 失败（供计划任务 / cron 判断是否报警）。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

# ROOT：项目根目录（scripts/ 的上级）。从 __file__ 推导而非写死盘符，保证换机器/换盘符可用
ROOT = Path(__file__).resolve().parents[1]

# 把根目录塞进 sys.path：cron / 计划任务唤起时当前工作目录可能是任意位置，
# 不补这一行会出现「直接跑脚本时找不到 backend 包」的经典问题
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 日志文件：与既有约定保持一致（logs/refresh.log，UTF-8 追加）
LOG_FILE = ROOT / "logs" / "refresh.log"

# 刷新接口地址与超时：均可用环境变量覆盖，便于 Docker 里换成容器名/其它端口
DEFAULT_URL = os.getenv("JD_REFRESH_URL", "http://127.0.0.1:8000/api/refresh")
DEFAULT_POST_TIMEOUT = float(os.getenv("JD_REFRESH_POST_TIMEOUT", "10"))


def log(message: str) -> None:
    """把一行日志以 UTF-8 追加写入 logs/refresh.log（目录不存在时自动创建）。

    Args:
        message: 要记录的文本（允许中文；统一 UTF-8 落盘，避免控制台编码把中文搞乱）。
    """
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{stamp} {message}\n")


def best_effort_post(url: str, timeout: float) -> str:
    """尽力 POST 刷新接口：只为让「正在运行」的服务立刻清缓存，失败不影响数据已更新。

    Args:
        url: 刷新接口完整地址（如 http://127.0.0.1:8000/api/refresh）。
        timeout: 请求超时秒数；服务没起时应当很快失败，不要拖长整个刷新。

    Returns:
        str: 一行结果描述，成功形如 "POST OK 200"，失败形如 "POST SKIP (...)"。
    """
    try:
        req = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return f"POST OK {resp.status}"
    except Exception as e:  # noqa: BLE001
        # 服务没启动是**正常**情况（本脚本的定位就是不依赖服务），跳过即可
        return f"POST SKIP ({type(e).__name__}: {e})"


def main() -> int:
    """执行一次完整刷新，返回进程退出码。

    Returns:
        int: 0 表示批处理成功；1 表示导入失败 / 批处理抛异常 / 批处理返回失败状态。
    """
    parser = argparse.ArgumentParser(description="刷新 JD 数据（跑批处理 + 可选通知服务）")
    parser.add_argument("--no-post", action="store_true", help="跳过向服务 POST 刷新通知")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"刷新接口地址，默认 {DEFAULT_URL}")
    parser.add_argument("--post-timeout", type=float, default=DEFAULT_POST_TIMEOUT, help="POST 超时秒数")
    args = parser.parse_args()

    log("===== refresh start =====")
    try:
        from backend.jobs.run_batch import run_once
    except Exception as e:  # noqa: BLE001
        # 典型场景：换机器后 .venv 没建 / 依赖没装。必须写日志，否则计划任务失败是哑的
        log(f"IMPORT ERROR {type(e).__name__}: {e}")
        print(f"导入 backend.jobs.run_batch 失败：{e}", file=sys.stderr)
        return 1

    # 捕获批处理自身的 print 输出：统一以 UTF-8 落盘，绕开 Windows 控制台 GBK 编码的坑
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            status = run_once()
    except Exception as e:  # noqa: BLE001
        log(f"BATCH ERROR {type(e).__name__}: {e}")
        log(buf.getvalue().strip())
        log("===== refresh end (fail) =====")
        print(f"批处理异常：{e}", file=sys.stderr)
        return 1

    ok = bool(getattr(status, "success", False))
    log("BATCH " + ("OK" if ok else "FAIL") + " "
        + json.dumps(json.loads(status.model_dump_json()), ensure_ascii=False))
    # 批处理输出可能很长，只留最后 20 行，避免日志文件无限膨胀
    tail = "\n".join(buf.getvalue().strip().splitlines()[-20:])
    if tail:
        log(f"BATCH OUTPUT:\n{tail}")

    if not args.no_post:
        log(best_effort_post(args.url, args.post_timeout))

    log("===== refresh end " + ("ok" if ok else "fail") + " =====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
