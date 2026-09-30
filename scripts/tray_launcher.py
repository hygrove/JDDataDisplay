# -*- coding: utf-8 -*-
"""托盘启动器（核心版）：同进程内运行 FastAPI + 系统托盘，双击无黑框。

为什么这么设计（对应规格 §1 / §6）：
- 主线程跑 pystray 事件循环（托盘必须主线程）；uvicorn 在同进程的**子线程**里 `serve()`，
  而不是另起 subprocess 调 `uvicorn` CLI（规格 §8 #2 明令禁止 subprocess，且打包后 CLI 找不到）。
- 这样「退出即停服」：托盘点退出 → 设 `should_exit` → 子线程 `join` 干净退出，不残留端口 / 幽灵图标。
- 单实例：以「端口 8000 是否已被监听」为权威判定（PID 存活探测在 Windows 上会误判，
  2026-09-29 实测导致第二实例抢锁后绑定失败自爆）；重复双击只把已有面板提到前台。
- 绑定失败自保护：若 uvicorn 启动失败（端口被占），托盘随之退出，不留「无服务的僵尸托盘」。

复用现有项目：直接 `import backend.app.main:app`（现有 FastAPI 应用，含前端托管与业务 API），
不新建 src/ 架构、不动现有业务逻辑（按用户 2026-09-26 决策：复用现有 app，本轮只做核心闭环）。

运行：由 `启动服务.pyw` 用 venv 的 `pythonw.exe` 拉起本文件 → 无控制台窗口。
"""
from __future__ import annotations

import atexit
import logging
import os
import socket
import sys
import threading
import webbrowser
from datetime import date
from pathlib import Path

# ---- 路径：确保能 import backend.app.main（无论从哪个 cwd 被拉起）----
SCRIPT_DIR = Path(__file__).resolve().parent            # .../scripts
PROJECT_ROOT = SCRIPT_DIR.parent                         # .../JDDataDisplay
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.main import app                          # 现有 FastAPI 应用（导入即建好路由/挂载）

import pystray
from pystray import Icon, Menu, MenuItem
from PIL import Image, ImageDraw

# ---- 配置（核心版常量；纯本机用户可把 HOST 改成 127.0.0.1）----
HOST = "0.0.0.0"        # 沿用现有项目：保花生壳 jdksh.top 隧道需 0.0.0.0；纯本机可改 127.0.0.1
PORT = 8000
BASE_URL = f"http://127.0.0.1:{PORT}"                  # 浏览器 / 托盘打开面板用本机回环
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOGS_DIR / f"server_{date.today():%Y%m%d}.log"
LOCK_FILE = LOGS_DIR / "tray.lock"

# uvicorn 子线程持有的 Server 对象（供退出时置 should_exit）；图标对象（供退出时隐藏）
_server: "pystray.Icon | None" = None
_icon: "Icon | None" = None


def setup_logging() -> None:
    """把 uvicorn 与应用日志落到 logs/ 文件（pythonw 无 stdout，必须落盘，规格 §8 #13）。"""
    logging.basicConfig(
        filename=str(LOG_FILE), level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s", encoding="utf-8",
    )


def make_icon(size: int = 64) -> Image.Image:
    """用 Pillow 现画托盘图标：项目主色红底(#e1251b) + 白色柱状图，高对比不糊（规格 §8 #9）。

    Args:
        size: 图标边长（像素），默认 64。

    Returns:
        Image.Image: RGBA 透明背景的图标图像，交给 pystray 使用。
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([4, 4, size - 4, size - 4], radius=14, fill="#e1251b")
    bar_w = max(6, size // 8)
    gap = size // 6
    x0 = size // 4
    heights = [size * 0.42, size * 0.62, size * 0.5]      # 三根高度不一的柱，象征数据
    for i, h in enumerate(heights):
        x = x0 + i * (bar_w + gap)
        y_top = size - 12 - h
        draw.rectangle([x, y_top, x + bar_w, size - 12], fill="white")
    return img


def _port_in_use() -> bool:
    """探测 PORT 是否已有服务在监听；这是单实例判定的「权威依据」。

    为什么不用旧版的 PID 存活探测（os.kill(pid, 0)）：
    - 2026-09-29 实测：Windows 上 os.kill(pid, 0) 会把活着的实例误判成已死，
      导致第二实例抢锁后照样起 uvicorn，绑 8000 报 winerror 10048 自爆；
    - 「端口是否被监听」直接等价于「是否已有实例在对外服务」，语义更准、跨平台一致。

    Returns:
        bool: True 表示端口已被占用（已有实例在服务）；False 表示端口空闲。

    调用示例：
        if _port_in_use():
            webbrowser.open(BASE_URL)   # 已有实例在跑，只开面板即可
    """
    # HOST 可能是 0.0.0.0（监听所有网卡），回环探测统一用 127.0.0.1
    probe_host = "127.0.0.1" if HOST in ("0.0.0.0", "::") else HOST
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)                      # 半秒超时：本机连接要么立刻通要么不通
        # connect_ex 返回 0 = 三次握手成功 = 有进程在 LISTEN 并 accept
        return s.connect_ex((probe_host, PORT)) == 0


def acquire_lock() -> None:
    """写入单实例锁文件（抢占式）；仅应在 `_port_in_use()` 为 False 时调用。

    为什么敢无条件抢占：能走到这里说明端口空闲 → 没有任何实例在对外服务，
    此时锁文件无论是否存在都只是「僵尸锁/僵尸托盘」的遗物，直接覆盖重建，
    锁内容写当前进程 PID 仅供人工排查。退出由 `atexit` 统一删锁。

    无参数、无返回值。

    调用示例：
        if not _port_in_use():
            acquire_lock()
    """
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:                                        # 先删旧锁，保证锁里 PID 是本次持有者
        os.remove(LOCK_FILE)
    except OSError:
        pass
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode("utf-8"))
    os.close(fd)


def release_lock() -> None:
    """atexit 时删除锁文件，避免下次启动误判为「已有实例」。"""
    try:
        if LOCK_FILE.exists():
            os.remove(LOCK_FILE)
    except OSError:
        pass


def run_server() -> None:
    """子线程：起 `uvicorn.Server(app).serve()`，同进程托管现有 FastAPI（规格 §4.3 / §8 #2）。

    把 Server 对象存到模块级 `_server`，供退出序列置 `should_exit`。
    若启动失败（如端口被占），在 finally 里停掉托盘并结束进程，不留僵尸托盘。
    """
    global _server
    import uvicorn
    cfg = uvicorn.Config(
        app, host=HOST, port=PORT, log_level="info",
        log_config={
            "version": 1, "disable_existing_loggers": False,
            "formatters": {"default": {"fmt": "%(asctime)s %(levelname)s %(message)s"}},
            "handlers": {
                "file": {
                    "class": "logging.FileHandler",
                    "filename": str(LOG_FILE), "formatter": "default", "encoding": "utf-8",
                },
            },
            "loggers": {
                "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"handlers": ["file"], "level": "INFO", "propagate": False},
                "uvicorn.access": {"handlers": ["file"], "level": "INFO", "propagate": False},
            },
        },
    )
    _server = uvicorn.Server(cfg)
    try:
        _server.run()
    finally:
        # 绑定失败（端口被占）时 uvicorn 起不来：必须把托盘一起停掉并让进程退出，
        # 否则会留下「无服务的僵尸托盘」（2026-09-29 实测出现两个托盘图标）。
        # uvicorn 启动成功后 started=True；绑定失败时保持 False。
        if not getattr(_server, "started", True) and _icon is not None:
            logging.error("uvicorn 未能启动（端口 %s 可能被占用），托盘随之退出", PORT)
            try:
                _icon.stop()                    # pystray 支持跨线程 stop（Win32 后端走 PostMessage）
            except Exception:
                logging.warning("托盘停止失败，进程可能需要手动退出")


def on_open(icon: Icon, item) -> None:
    """菜单项「打开面板」：默认浏览器打开前端地址。"""
    webbrowser.open(BASE_URL)


def on_quit(icon: Icon, item) -> None:
    """退出序列（规格 §6.3）：先隐藏图标再 stop，避免 Windows 幽灵图标；再令 uvicorn 退出。

    注意：本函数在 pystray 主线程内被调用，直接操作 icon 安全；`should_exit` 由子线程的
    serve 循环在下一次迭代读到后自行结束。
    """
    global _server
    if _server is not None:
        _server.should_exit = True
    icon.visible = False
    icon.stop()


def main() -> None:
    """核心启动流程：端口探测（权威单实例判定）→ 写锁 → 起 uvicorn 子线程 → 跑托盘 → 干净退出。"""
    setup_logging()
    logging.info("托盘启动器开始；PROJECT_ROOT=%s", PROJECT_ROOT)
    if _port_in_use():
        # 端口已被监听 = 已有实例在服务：绝不再起第二个 uvicorn，也不碰锁文件
        logging.info("端口 %s 已被监听，视为已有实例，打开面板后退出", PORT)
        webbrowser.open(BASE_URL)
        return
    acquire_lock()
    atexit.register(release_lock)

    global _icon
    _icon = Icon(                              # 先建图标再起线程：保证 run_server 的 finally 里 _icon 可用
        "JDDataDisplay",
        icon=make_icon(64),
        title="JD 数据可视化",
        menu=Menu(
            MenuItem("打开面板", on_open),
            MenuItem("退出", on_quit),
        ),
    )
    server_thread = threading.Thread(target=run_server, daemon=False)
    server_thread.start()
    _icon.run()                              # 主线程阻塞，直到 on_quit 调 icon.stop()
    server_thread.join(timeout=5)           # 等 uvicorn 子线程结束
    if server_thread.is_alive():
        logging.warning("uvicorn 子线程 5s 未退出")
    logging.info("托盘启动器退出")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:                 # pythonw 无控制台，异常必须用弹窗让用户看见
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, "启动失败：\n" + str(exc), "JD 数据可视化", 0x10,
            )
        except Exception:
            pass
        raise
