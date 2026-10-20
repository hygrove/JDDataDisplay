# ============================================================
# JDDataDisplay 多阶段构建
# 阶段 1 (frontend): node:22-slim 构建前端 -> 产出 frontend/dist
# 阶段 2 (runtime) : python:3.13-slim 运行 FastAPI + uvicorn
# 最终镜像同时含：后端依赖 + 前端 dist + 项目源码 + cron 定时刷新
# ============================================================

# ---------- 阶段 1：前端构建 ----------
FROM node:22-slim AS frontend
WORKDIR /build

# 仅先拷依赖清单，利用 Docker 层缓存（改源码不重装依赖）
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# 再拷源码并构建（vue-tsc 类型检查 + vite build -> frontend/dist）
COPY frontend/ ./
RUN npm run build

# ---------- 阶段 2：后端运行 ----------
FROM python:3.13-slim AS runtime

# 让 Python 输出不缓冲，便于 docker logs 实时看到刷新日志
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # 阿里云 pip 镜像：规避 Pillow>=12 AVIF 轮子缺失问题（slim 镜像缺 libavif）
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com

# 系统依赖：
#   libavif-dev  -> pillow-avif-plugin / Pillow AVIF 编解码所需（slim 镜像默认缺）
#   cron         -> 容器内定时跑刷新
#   tini         -> 作 PID 1，正确转发信号、回收僵尸进程
RUN apt-get update && apt-get install -y --no-install-recommends \
        libavif-dev \
        cron \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装后端依赖（同样利用层缓存）
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# 拷源码（不含数据：ResourceData / backend/app/data 运行时挂载）
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY config.yml ./config.yml

# 拷前端构建产物（对应 main.py 里 FRONTEND_DIST = /app/frontend/dist）
COPY --from=frontend /build/dist ./frontend/dist

# 安装 cron 任务（每日 20:15 刷新）
COPY docker/crontab /etc/cron.d/jddisplay-cron
RUN chmod 0644 /etc/cron.d/jddisplay-cron \
    && crontab /etc/cron.d/jddisplay-cron

# 容器入口
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

# tini 作 PID 1；entrypoint.sh 后台起 cron、前台 exec uvicorn
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
