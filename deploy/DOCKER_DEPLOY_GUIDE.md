# JDDataDisplay Docker 上云部署 · 完整闭环教程

> 本文档记录把 `JDDataDisplay`（电商数据可视化：FastAPI + Vue3 + SQLite）容器化并部署到腾讯云轻量应用服务器的**真实过程**，目的是让你照着跑通「本地构建 → 上传 → 云端构建 → 启动 → 数据生成 → 公网访问」的完整链路。
>
> 本次真实部署结果：应用已在云端单容器运行，公网 HTTPS 访问地址
> **https://classic-pre-gst-temperatures.trycloudflare.com/**

---

## 0. 为什么这个项目非常适合 Docker

| 利好点 | 说明 |
|---|---|
| **单端口同源** | FastAPI 在 `backend/app/main.py` 里自己处理了 SPA 回退（`/{full_path:path}` → `index.html`）、`/images`、`/thumbs`、`/api`、`/docs`。一个容器一个端口 `8000` 就能跑完整前端 + API + 图片，**不需要 nginx 反代**。 |
| **配置零耦合** | `backend/jobs/config.py` 所有路径/开关都支持环境变量覆盖（`JD_DATA_DIR`、`POP_SOURCE_DIR`、`JD_REFRESH_URL`…），无写死绝对路径。Docker 化几乎不改业务代码。 |
| **作者已为 Docker 预留** | `scripts/refresh_data.py` 注释明写「Docker 通用」，批处理不依赖 uvicorn 是否运行。 |
| **无外部强依赖** | SQLite 嵌入式（PG 源默认关闭），没有要连的外部数据库。 |

> ⚠️ **重要事实（文档矛盾点）**：`docs/异地克隆部署指南.md` 说 `ResourceData/` 默认提交进 git，但实际 `.gitignore` 第 24 行 `ResourceData/` 是被忽略的。以 `.gitignore` 为准 → **git clone 拿不到业务数据**，必须单独传输。本次就是把本地真实 `ResourceData/` 上传到服务器的。

---

## 1. 整体架构（链路图）

```
浏览器 ──HTTPS──> Cloudflare Tunnel 公网域名 (trycloudflare.com)
                        │  隧道（出站，免开防火墙）
                        ▼
              云服务器宿主机 127.0.0.1:8000
                        │  docker 端口映射
                        ▼
              容器 jddisplay :8000 (uvicorn)
                ├─ /api /docs /images /thumbs / SPA   (FastAPI)
                ├─ cron 每日 20:15 → refresh_data.py   (数据刷新)
                └─ 挂载卷
                      ├─ /app/ResourceData   (只读，数据源)
                      └─ /app/backend/app/data (可读写，JSON+图片+SQLite)
```

设计取舍：**单镜像多阶段 + 单容器单端口**，不引入 nginx（后端已处理 SPA 回退）。数据用 bind mount 持久化，不 bake 进镜像。

---

## 2. 交付的配置文件清单

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 多阶段：node 阶段 build 前端 → python 阶段装依赖 + 拷 dist + 跑 uvicorn |
| `.dockerignore` | 排除 `.venv`、`node_modules`、`ResourceData`、`backend/app/data` 等，减小构建上下文 |
| `docker/entrypoint.sh` | 容器入口：首次若无数据则跑 `run_batch`，后台起 cron，前台 `exec uvicorn` |
| `docker/crontab` | 每日 20:15 跑 `refresh_data.py`（输出重定向到容器 stdout） |
| `docker-compose.yml` | 编排主应用 + 卷挂载 + 端口映射（127.0.0.1 回环，不暴露公网）+ cpolar sidecar |
| `.env` | `CPOLAR_TOKEN=…`（被 `.gitignore` 忽略，切勿提交） |

---

## 3. Dockerfile 逐段讲解

```dockerfile
# 阶段1：前端构建
FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci                       # 先装依赖，利用层缓存
COPY frontend/ ./
RUN npm run build                # vue-tsc 类型检查 + vite build → frontend/dist

# 阶段2：后端运行
FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \   # 阿里云源，规避 Pillow AVIF 轮子缺失
    PIP_TRUSTED_HOST=mirrors.aliyun.com
RUN apt-get update && apt-get install -y --no-install-recommends \
        libavif-dev \            # ★ AVIF 缩略图所需系统库（slim 镜像默认缺，头号风险点）
        cron \                   # 定时刷新
        tini \                   # PID1，正确转发信号、回收僵尸
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY config.yml ./config.yml
COPY --from=frontend /build/dist ./frontend/dist   # 对应 main.py 里的 FRONTEND_DIST
COPY docker/crontab /etc/cron.d/jddisplay-cron
RUN chmod 0644 /etc/cron.d/jddisplay-cron && crontab /etc/cron.d/jddisplay-cron
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
```

> `FRONTEND_DIST` 在 `main.py` 里是 `Path(__file__).resolve().parents[2] / "frontend" / "dist"`，即 `/app/frontend/dist`，所以 `COPY --from=frontend /build/dist ./frontend/dist` 位置正确。

**entrypoint.sh**
```bash
#!/usr/bin/env bash
set -e
DATA_DIR="/app/backend/app/data"
# 首次守卫：没数据就先生成，失败也不阻断服务启动
if [ ! -f "${DATA_DIR}/status.json" ]; then
  echo "[entrypoint] 首次 run_batch 生成数据..."
  python -m backend.jobs.run_batch || echo "[entrypoint] 首次 run_batch 失败，请检查 ResourceData 挂载"
fi
cron || service cron start || true      # 后台定时刷新
exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000   # 前台主进程
```

**docker/crontab**（踩坑修正版，见第 7 节）
```
# Daily 20:15 refresh
15 20 * * * cd /app && /usr/local/bin/python scripts/refresh_data.py --no-post >> /proc/1/fd/1 2>&1
```

---

## 4. 本地构建验证（可选）

需要本机 Docker daemon 在运行（Docker Desktop / Docker Engine）。本环境 daemon 未启动，故**直接在云端构建验证**（效果等价）。
```bash
docker build -t jddisplay .
docker run --rm -p 8000:8000 \
  -v "$PWD/ResourceData:/app/ResourceData:ro" \
  -v "$PWD/backend/app/data:/app/backend/app/data" \
  jddisplay
```

---

## 5. 上传项目与数据到云服务器（腾讯云 Lighthouse）

`deploy_project_preparation` 工具会把整个目录上传。但**直接传整个项目会超时**（`.git` 历史目录 + 海量小文件太大）。正确做法：先生成一个**精简副本**排除 `.git` / 依赖 / 生成物，只保留源码 + 真实 `ResourceData` + Docker 配置：

```powershell
# 在本地 Windows PowerShell 执行
robocopy e:/JDDataDisplay e:/JDDeploy /E `
  /XD .git node_modules __pycache__ .venv .scratch data dist `
  /XF *.pyc *.pyo *.tsbuildinfo
```
> 实测副本约 84 MB（`ResourceData` 真实数据占 31 MB），上传几秒完成。

然后通过 Lighthouse 集成上传：
```
deploy_project_preparation(FolderPath="e:/JDDeploy",
                           InstanceId="lhins-ca3q5aa5",
                           Region="ap-guangzhou",
                           ProjectName="JDDataDisplay")
```
服务器落地路径：`/root/JDDeploy_20261016173056`（时间戳目录，每次不同）。

> 📋 **可照抄的独立传输清单**：见 [`deploy/UPLOAD_TO_SERVER.md`](./UPLOAD_TO_SERVER.md) —— 含完整 PowerShell 脚本、上传参数、服务器验证命令与常见坑，可直接一步步执行。

---

## 6. 云端构建与启动

> 服务器已预配 **Docker 镜像加速器** `mirror.ccs.tencentyun.com`，拉 `node`/`python` 基础镜像走内网加速，很快。

```bash
cd /root/JDDeploy_20261016173056

# 后台构建（避免命令超时），日志写到 build.log
nohup docker compose build > /root/build.log 2>&1 & disown
# 轮询：tail -n 20 /root/build.log
```
构建关键节点（真实日志）：
- `npm ci` + `vite build` 成功，产出 `frontend/dist`
- `apt-get` 装好 `libavif-dev`/`cron`/`tini`
- `pip install` 走阿里云源，`pillow-avif-plugin` 轮子**成功下载**（文档担心的缺轮子问题在阿里云源不存在）
- 镜像生成：`jddeploy_20261016173056-jddisplay`（约 482 MB）

启动（entrypoint 会自动首次 `run_batch` 生成数据）：
```bash
docker compose up -d jddisplay
docker compose logs --tail=30 jddisplay
```
首次日志确认：`摄入 22 文件 / 732 行 / 生成 72 缩略图 / success: true` → `Uvicorn running on http://0.0.0.0:8000`。

---

## 7. 验证清单（每一步都要对）

```bash
# 容器状态
docker compose ps                     # jddisplay Up, 127.0.0.1:8000->8000

# 应用可访问
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/        # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/docs    # 200
curl -s http://127.0.0.1:8000/api/manifest | head -c 300               # 返回真实业务 JSON

# 数据持久化（bind mount 同步到宿主机）
cat /root/JDDeploy_20261016173056/backend/app/data/status.json          # success:true
```
✅ 三项全过，说明「构建 → 跑批 → 托管」链路完整。

> **踩坑记录 1（cron bad minute）**：第一版 `docker/crontab` 写了 `SHELL=/bin/bash` + `PATH=...` 变量行，cron 把 `PATH=` 行误当成任务条目报 `bad minute`，镜像构建失败。修正：删掉变量行，`python` 用绝对路径 `/usr/local/bin/python`，任务只留一行。

---

## 8. 定时刷新（容器内 cron）

`docker/crontab` 每天 20:15 执行 `scripts/refresh_data.py --no-post`，输出重定向到容器 stdout（`/proc/1/fd/1`，即 tini 的 stdout），`docker compose logs` 可直接看到。验证 cron 已装入：
```bash
docker compose exec jddisplay crontab -l    # 应显示 15 20 * * * ... 那一行
```

---

## 9. 公网访问（穿透）—— 两种方案

本项目选了「内网穿透，不开放防火墙端口，隐藏真实 IP」。下面记录真实踩坑与最终打通方案。

### 9.1 cpolar（按计划选，但遇到客户端兼容性问题）

- `cpolar/cpolar:latest` 镜像从 Docker Hub 拉取**超时**（腾讯云加速器对该用户镜像回源失败）→ 容器方式不可行。
- 改用宿主机安装 cpolar 二进制：官方 stable 脚本装的是 **3.3.12（约 2020 年版本）**，与当前 cpolar 服务端握手协议不兼容，日志报 `connection reset by peer`，隧道始终建不起来。
- 配置文件 `/usr/local/etc/cpolar/cpolar.yml` 已正确指向 8000：
  ```yaml
  authtoken: <你的 token>
  tunnels:
    web:
      proto: http
      addr: "8000"
  ```
- **结论**：cpolar 免费版现代用法通常要求在网页控制台**先创建一条指向 8000 的隧道**，再 `cpolar start web`。这一步需你登录 cpolar 控制台操作（AI 无法代登）。完成创建后告诉我隧道名，我执行 `cpolar start <名>` 即可打通。`docker-compose.yml` 里的 cpolar sidecar 服务保留，待镜像源/控制台问题解决后可直接用。

### 9.2 Cloudflare Tunnel（本次实际打通方案，立即可用）

免费、自带 HTTPS、不开放任何防火墙端口，且无需控制台预创建。在服务器执行：
```bash
cd /tmp
wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O cloudflared
chmod +x cloudflared
# 后台常驻（quick tunnel，免登录，给随机 HTTPS 子域名）
nohup ./cloudflared tunnel --url http://localhost:8000 --no-autoupdate > /root/cf.log 2>&1 &
sleep 20; cat /root/cf.log     # 找到 Your quick Tunnel has been created! ... https://xxx.trycloudflare.com
```
本次得到的公网地址：**https://classic-pre-gst-temperatures.trycloudflare.com**

验证（从任意能上网的机器）：
```bash
curl -s -o /dev/null -w '%{http_code}\n' https://classic-pre-gst-temperatures.trycloudflare.com/   # 200
```

> 这是 account-less quick tunnel：域名**每次重启 cloudflared 会变**，且 Cloudflare 不保证 SLA。生产建议用「命名隧道」（需免费 Cloudflare 账号 + 域名）：
> `cloudflared tunnel login` → `cloudflared tunnel create jd` → `cloudflared tunnel route dns jd jd.example.com` → `cloudflared tunnel run jd`，并把进程交给 systemd 守护。

---

## 10. 运维常用命令

```bash
cd /root/JDDeploy_20261016173056

# 看日志（含 cron 刷新输出）
docker compose logs -f jddisplay

# 手动重新生成数据
docker compose exec jddisplay python -m backend.jobs.run_batch

# 改了代码后重新构建并重启
docker compose build && docker compose up -d

# 重启服务
docker compose restart jddisplay

# 升级 cloudflared（持久化建议用 systemd）
systemctl enable cloudflared   # 自建 service 文件后
```

数据备份：直接打包宿主机卷目录 `backend/app/data` 与 `ResourceData` 即可，二者都在项目目录下，未进镜像。

---

## 11. 一键复盘（从零复现步骤）

1. 本地 `robocopy` 生成精简副本 `JDDeploy`（排除 `.git`/依赖/生成物，保留 `ResourceData` + Docker 配置）。
2. `deploy_project_preparation` 上传到 `lhins-ca3q5aa5` → `/root/JDDeploy_<时间戳>`。
3. 云端 `docker compose build`（镜像加速器 + 阿里云 pip 源，AVIF 系统库在 Dockerfile 装好）。
4. `docker compose up -d jddisplay` → entrypoint 首次 `run_batch` 生成数据。
5. `curl` 验证 `/`、`/docs`、`/api/manifest` 全部 200/有数据。
6. 穿透：`cpolar start <控制台隧道>`（需你先建）**或** `cloudflared tunnel --url http://localhost:8000`（已通）。
7. 浏览器打开公网 HTTPS 地址，看板可见。

---

## 12. 踩坑总结（排错速查）

| 现象 | 根因 | 解决 |
|---|---|---|
| `docker build` 连不上 daemon | 本机 Docker Desktop 未启动 | 直接在云端构建验证 |
| `bad minute` 构建失败 | crontab 里 `PATH=` 变量行被误解析为任务 | 删变量行，python 用绝对路径 |
| pip 装 `pillow-avif-plugin` 缺轮子 | 清华源偶缺 | Dockerfile 配阿里云 pip 镜像源 ✅ |
| AVIF 缩略图报错 | slim 缺 `libavif` | Dockerfile `apt-get install libavif-dev` ✅ |
| cpolar 镜像拉取超时 | Docker Hub 用户镜像回源慢 | 改为宿主机装 cloudflared |
| cpolar 隧道 `connection reset` | stable 客户端 3.3.12 与服务端协议不兼容 | 改用 Cloudflare Tunnel（已通） |
| 上传整个项目超时 | `.git` 历史 + 海量小文件太大 | 先 robocopy 精简副本再传 |
