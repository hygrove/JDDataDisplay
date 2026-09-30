# -*- coding: utf-8 -*-
"""双击静默启动托盘（纯 Python 引导脚本，无控制台窗口）。

为什么需要它：.pyw 双击时由系统 pythonw 执行（无黑框），本脚本只负责用项目自带的
.venv\\Scripts\\pythonw.exe 拉起真正的托盘程序 `tray_launcher.py`，然后立刻退出。
这样既保留「双击即用」，又把逻辑放在 Python 里（不再依赖 .vbs）。

注意：本文件只用标准库，不 import tray_launcher（系统 python 里没有 pystray / PIL）。

调用示例：
    双击本文件  →  静默启动托盘 + 同进程 FastAPI 服务
"""
import os
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
VENV_PYTHONW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
TRAY_LAUNCHER = os.path.join(SCRIPT_DIR, "tray_launcher.py")
# CREATE_NO_WINDOW：让子进程不分配控制台窗口
CREATE_NO_WINDOW = 0x08000000


def main() -> None:
    """校验 venv 解释器存在后，静默拉起托盘启动器。"""
    if not os.path.exists(VENV_PYTHONW):
        # 出错也必须让用户看到：直接调 user32 弹错误框（不依赖 tkinter）
        try:
            import ctypes
            # 0x10 = MB_ICONERROR（错误图标）
            ctypes.windll.user32.MessageBoxW(
                0, "未找到虚拟环境解释器：\n" + VENV_PYTHONW,
                "JD 数据可视化", 0x10,
            )
        except Exception:
            pass
        return
    subprocess.Popen(
        [VENV_PYTHONW, TRAY_LAUNCHER],
        cwd=SCRIPT_DIR,
        creationflags=CREATE_NO_WINDOW,
        close_fds=True,
    )


main()
