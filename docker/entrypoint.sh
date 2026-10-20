#!/usr/bin/env bash
# 容器入口：后台启动 cron，前台启动 uvicorn（由 tini 托管信号）
set -e

DATA_DIR="/app/backend/app/data"

# 首次启动守卫：若还没有 status.json，说明数据未生成，先跑一次批处理。
# ResourceData 以只读卷挂载进来，run_batch 读取它写入 data 卷。
# 即使失败也不阻断服务启动（页面先空着，后续 cron 会再刷）。
if [ ! -f "${DATA_DIR}/status.json" ]; then
    echo "[entrypoint] 未检测到 status.json，首次运行 run_batch 生成数据..."
    python -m backend.jobs.run_batch \
        && echo "[entrypoint] 首次 run_batch 完成" \
        || echo "[entrypoint] 首次 run_batch 失败，请检查 ResourceData 挂载与数据；服务仍会启动"
fi

# 启动 cron（后台守护，负责每日定时刷新）
echo "[entrypoint] 启动 cron ..."
cron || service cron start || true

# 前台启动主服务；exec 让 uvicorn 替换本进程，由 tini 直接托管
echo "[entrypoint] 启动 uvicorn on 0.0.0.0:8000 ..."
exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
