@echo off
REM 启动 JD 数据可视化后端（uvicorn），使用项目自带 venv。
REM 项目根目录由本脚本所在位置自动推导（scripts\..），
REM 因此无论把整个项目文件夹改名或移动到何处都能正常运行，无需改代码。
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] 未找到虚拟环境: .venv\Scripts\python.exe
  echo         请先创建: python -m venv .venv
  echo         再安装依赖: .venv\Scripts\python.exe -m pip install -r backend/requirements.txt
  pause
  exit /b 1
)
echo Starting JD viz service on 0.0.0.0:8000 ...
".venv\Scripts\python.exe" -m uvicorn backend.app.main:app --app-dir "%CD%" --host 0.0.0.0 --port 8000
