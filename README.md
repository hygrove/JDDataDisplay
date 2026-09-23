# JD 数据平台

电商多店铺经营数据的可视化看板。Python 批处理每天从 Excel/CSV（可扩展 PostgreSQL）采集清洗数据，导出 JSON；FastAPI 托管前端并提供数据 API；Vue3 前端做表格 + 图表展示。

## 功能一览（当前模块：POP 单品明细）

- 左右布局：左侧模块导航 + 店铺切换，右侧主业务区
- 日期选择（默认最新一天）、SPU 号/商品名搜索、指标排序（点击表头）
- 可配置指标卡/表格列/趋势图（共 **15 个指标**，分 3 组，默认勾选 8 个）：商品明细表 9 项 + 推广数据表 4 项 + 退款/其他 2 项；展示项由 `frontend/src/metrics.ts` 的 `METRICS` 派生，可在 ⚙ 指标配置 中调整
- 3 种图表：日期趋势（金额+访客双轴折线）、SPU 成交金额 TOP10（横向柱状）、店铺成交金额占比（饼图）
- SPU 明细表格：商品图片 180×180、虚拟滚动、分页加载（300 行/页）、缺失指标展示 `--`
- 手动刷新：前端按钮触发后端重跑批处理

> **关于推广指标**：推广费/推广成交金额/ROI/推广占比来自各店铺的 `推广数据_*.csv`（京准通报表），由批处理按 (店铺, 日期, SPU) 左连接补充到明细长表；明细表本身不含这些列。若某 SPU 在推广 csv 中无对应记录，则相关指标展示 `--`。

## 技术栈

| 层 | 选型 |
|---|---|
| 批处理 | Python 3.13 + Polars（清洗/聚合）+ Pydantic v2（建模校验） |
| 后端 | FastAPI + Uvicorn（静态托管 + 数据 API） |
| 前端 | Vue3 + Vite + TypeScript + Pinia + vue-router + @tanstack/vue-virtual + ECharts（vue-echarts 按需引入） |

## 目录结构

```
project/
  backend/
    app/
      main.py            # FastAPI：静态托管 + SPA 回退 + 图片挂载
      api.py             # /api/manifest /module/{id}/rows /summary /spu/{spu}/analysis /status /refresh
      data/              # 批处理产出（manifest.json, status.json, modules/*.json, images/ + thumbs/）
      analysis.py        # 单品分析：节假日口径、工作日/节假日对比、洞察文案
    jobs/
      run_batch.py       # 批处理入口（重试逻辑保留，默认关闭）
      pipeline.py        # 模块管线 + 模块注册表（加模块改这里）
      transforms.py      # Polars 清洗与聚合（长表 -> 层级 JSON / summary）
      models.py          # Pydantic 模型（与前端 src/types.ts 对齐）
      config.py          # 路径/重试等配置，全部支持环境变量覆盖
      sources/
        base.py          # Source 协议 + 统一长表列约定
        excel_source.py  # POP 单品明细 Excel 源（读固定 数据源表目录）
        db_source.py     # PostgreSQL 源骨架（配 PG_DSN 后实现 fetch 即可）
      make_thumbs.py     # SPU 图片多尺寸缩略图（AVIF+WebP）
    requirements.txt
  frontend/
    src/
      modules/index.ts   # 模块注册表：表格列/指标卡片全配置驱动（加模块改这里）
      metrics.ts         # 指标清单：前端展示指标的唯一事实来源（METRICS）
      components/GenericTable.vue   # 虚拟滚动表格（滚动到底自动加载下一页）
      components/GenericChart.vue   # vue-echarts 封装
      components/MetricConfigPanel.vue # ⚙ 指标配置面板（勾选展示指标，存 localStorage）
      components/DateRangePicker.vue   # 统计区间范围选择器
      views/ModuleView.vue          # 通用模块页（所有模块共用；单日卡片 ↔ 区间矩阵）
      views/SpuAnalysisView.vue     # 单品分析页（趋势/工作日节假日/洞察）
      stores/metrics.ts             # Pinia store：指标勾选配置（localStorage jd.metric-config.v1）
      api.ts / router.ts / App.vue / types.ts
  scripts/
    gen_fake_data.py     # 生成 10 万行量级假数据压测
    install_tasks.bat / uninstall_tasks.bat / _install_tasks.ps1  # Windows 计划任务（每日刷新+开机自启）
    stop_uvicorn.bat / refresh_daily.ps1                          # 停止服务 / 每日刷新脚本
```

## 项目结构直观版本
JDDataDisplay/
├── README.md                  # 非常详尽，含快速开始/数据格式/加模块步骤/排障
├── screenshot_module.png
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py            # FastAPI：静态托管 + SPA 回退 + 图片挂载
│   │   └── api.py             # /api 路由 + 缓存 + 分页/过滤/排序
│   └── jobs/                  # 批处理（ETL 层）
│       ├── config.py          # 路径/重试/参数，全环境变量可覆盖
│       ├── models.py          # Pydantic 模型（前后端事实来源）
│       ├── transforms.py      # Polars 清洗/聚合
│       ├── pipeline.py        # 模块管线 + 注册表
│       ├── run_batch.py       # 批处理入口
│       └── sources/           # 数据源抽象
│           ├── base.py        # Source 协议 + 统一长表列 LONG_COLUMNS
│           ├── excel_source.py
│           └── db_source.py   # PostgreSQL 骨架（未启用）
│   └── app/data/              # 批处理产物（运行时生成，非源码）
├── frontend/
│   ├── package.json / tsconfig.json / vite.config.ts / index.html
│   ├── src/
│   │   ├── main.ts / App.vue      # 入口 + 左右布局（侧边导航）
│   │   ├── router.ts               # 模块视图懒加载
│   │   ├── api.ts / types.ts       # fetch 封装 / 与 Pydantic 对齐的 TS 类型
│   │   ├── modules/index.ts        # 前端模块注册表（配置驱动）
│   │   ├── stores/metrics.ts        # Pinia store（指标勾选配置，localStorage）
│   │   ├── components/
│   │   │   ├── GenericTable.vue      # 虚拟滚动表格
│   │   │   └── GenericChart.vue      # ECharts 按需引入封装
│   │   └── views/ModuleView.vue      # 通用模块页（所有模块共用）
│   ├── dist/ / node_modules/
└── scripts/
    ├── gen_fake_data.py           # 10 万行压测假数据
    └── _test_api.py               # 临时冒烟脚本（含硬编码店铺名）

## 快速开始

### 1. 准备 Python 环境

```bash
# 在项目根目录
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

<details>
<summary>可选：用 uv 管理环境（更快，推荐尝鲜）</summary>

uv 是 Rust 写的 Python 包管理器，比 pip 快 10-100 倍，命令与 pip 高度相似：

```bash
pip install uv           # 或去 astral.sh/uv 看安装脚本
uv venv .venv            # 替代 python -m venv，秒建
uv pip install -r backend/requirements.txt   # 替代 pip install
```

平时你只需要把 `pip install X` 换成 `uv pip install X`，其他习惯不变。
</details>

### 2. 跑批处理（Excel → JSON）

```bash
.venv\Scripts\python.exe backend/jobs/run_batch.py
```

- 读取项目内 `ResourceData/数据源表目录/` 下的固定文件夹（不再按最新日期遍历）：含各店铺 `店铺名_商品明细_*.xlsx` 与 `店铺名_推广数据_*.csv`，按 (店铺, 日期, SPU) 去重合并；
- 清洗聚合后写入 `backend/app/data/`；
- 同时把 `单品spu图片/*.png` 拷贝到 `backend/app/data/images/`；
- 结果（成功/失败、耗时、尝试次数）写入 `status.json`。
- 数据源路径可用环境变量覆盖：`POP_SOURCE_DIR`、`JD_DATA_DIR`。

### 3. 启动后端

```bash
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

API 文档（自动生成）：http://localhost:8000/docs

> **端口冲突提示**：本机 8000 端口若已被一个旧的、接口结构不同的后端（模块名 `app.main`、manifest 用 `id/stores/date_range{min,max}` 结构）占用，本项目的 uvicorn 会启动失败（地址已被占用）。处理方式二选一：
> 1. 停掉旧进程再用 8000：在 PowerShell 执行 `netstat -ano | findstr :8000` 找到 PID，`taskkill /F /PID <PID>` 后重跑上面的 uvicorn 命令；
> 2. 或本项目直接改用其他端口（如 8100）：把上面命令的 `--port 8000` 改为 `--port 8100`，访问 `http://localhost:8100`。
> 本项目已验证在 8100 端口端到端跑通（店铺过滤、排序、指标计算、图表、图片、虚拟滚动均正常）。

### 4. 前端

开发模式（热更新，代理 /api 与 /images 到 8000）：

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

生产模式（构建后由 FastAPI 直接托管，单端口交付）：

```bash
cd frontend
npm run build      # 产物在 frontend/dist
# 然后访问 http://localhost:8000
```

### 5. 压测（10 万行验证）

```bash
.venv\Scripts\python.exe scripts/gen_fake_data.py            # 默认 10 万行
.venv\Scripts\python.exe scripts/gen_fake_data.py --rows 500000
```

左侧导航会出现「压测假数据」模块。验证点：表格滚动流畅（虚拟滚动只渲染可视行）、翻页接口毫秒级返回、图表只画聚合点。
注意：每次正式批处理（run_batch / 手动刷新）会以注册表为准重写 manifest.json，压测模块会从导航消失，重新跑一遍 gen_fake_data.py 即可恢复。

## 数据格式

### 明细 JSON（`backend/app/data/modules/pop_spu_detail.json`）

层级结构：**店铺 → SPU → 日期 → 指标**。

```json
{
  "module_id": "pop_spu_detail",
  "date_range": ["2026-08-14", "2026-09-13"],
  "shops": {
    "官方旗舰店": {
      "shop": "官方旗舰店",
      "spus": {
        "10028007135350": {
          "spu": "10028007135350",
          "spu_name": "钻芯 净水器家用直饮RO反渗透纯水机…",
          "category": "净水器",
          "image": "/images/10028007135350.png",
          "dates": {
            "2026-09-13": {
              "visitors": 329, "buyers": 8, "items": 8, "amount": 4916.0,
              "promotion_cost": null, "promotion_amount": null,
              "conversion_rate": 0.0243, "roi": null, "promotion_ratio": null
            }
          }
        }
      }
    }
  }
}
```

指标口径：

- 常规指标直接取源表：访客数←商品访客数，成交人数←成交客户数，成交商品件数、成交金额同名；
- 计算指标：**转化率 = 成交人数 / 访客数**，**ROI = 推广成交金额 / 推广费**，**推广占比 = 推广费 / 成交金额**；分母为 0 或源数据缺失时为 `null`，前端展示 `--`；
- 汇总一律「先求和再算比率」，避免比率直接平均的统计错误。

### 其他文件

- `manifest.json`：模块清单（模块 id、店铺列表、日期范围、SPU 数、行数、更新时间）——前端启动时先拉它渲染导航；
- `modules/{id}.summary.json`：图表用预聚合（店铺×日期趋势、店铺内 TOP10 SPU、店铺汇总），几百~几千点，绝不画原始明细；
- `status.json`：批处理状态（上次运行时间、成败、尝试次数、耗时、消息）。

### API

| 接口 | 说明 |
|---|---|
| `GET /api/manifest` | 模块清单 |
| `GET /api/module/{id}/rows` | 分页明细。参数：`shop` `date` `keyword` `sort_by` `sort_order` `page` `page_size`(≤500) |
| `GET /api/module/{id}/summary` | 图表聚合数据 |
| `GET /api/module/{id}/spu/{spu}/analysis` | 单品分析（趋势 / 工作日节假日 / 洞察），支持 `start`+`end` 区间 |
| `GET /api/status` | 批处理状态 |
| `POST /api/refresh` | 手动重跑批处理（前端「手动刷新」按钮） |

## 如何添加一个新模块（配置驱动，不写重复页面）

1. **实现数据源**：在 `backend/jobs/sources/` 新建文件，实现 `Source` 协议（`fetch() -> pl.DataFrame`），输出统一长表列（见 `sources/base.py` 的 `LONG_COLUMNS`），参考 `excel_source.py`；数据库源参考 `db_source.py` 骨架。
2. **注册模块**：在 `backend/jobs/pipeline.py` 的 `default_registry()` 里追加一个 `ModuleSpec`。
3. **配置前端展示**：在 `frontend/src/modules/index.ts` 的 `MODULE_CONFIGS` 里加一份配置（表格列、指标卡片）；若涉及新指标，在 `frontend/src/metrics.ts` 的 `METRICS` 追加一条 spec，并同步后端 `models.py` / `transforms.py` / `api.py` 的字段。
4. 跑批处理 → 前端左侧自动出现新模块，路由、表格、图表、虚拟滚动全部复用。

如果新模块指标超出当前 15 个：在 `backend/jobs/models.py` 的 `MetricRecord` 加字段 → 前端 `src/types.ts` 同步加（保持对齐）→ 在 `src/metrics.ts` 的 `METRICS` 加一条 spec 并在模块配置里引用即可。

## 定时任务与重试

- **Windows 计划任务**：推荐直接双击 `scripts/install_tasks.bat` 自动注册（脚本按自身位置推导路径，改名/迁移后无需改）；如需手动命令，把下面命令里的 `<项目根目录>` 替换成你的实际项目路径即可：`schtasks /create /tn "JD数据批处理" /tr "\"<项目根目录>\.venv\Scripts\python.exe\" \"<项目根目录>\backend\jobs\run_batch.py\"" /sc daily /st 06:00`
- **Linux cron**：`0 6 * * * /path/.venv/bin/python /path/backend/jobs/run_batch.py`
- **重试机制**：指数退避（2s → 4s → 8s），最多 3 次。按方案要求**功能保留、默认关闭**，开启方式：
  ```
  set BATCH_RETRY_ENABLED=true
  set BATCH_RETRY_MAX_ATTEMPTS=3
  set BATCH_RETRY_BASE_DELAY=2
  ```
- 失败详情可在前端状态栏、`/api/status` 或 `status.json` 查看，前端「手动刷新」可随时补跑。

## 部署（生产）

```
浏览器 → Nginx（gzip / 静态缓存 / 反代）→ Uvicorn:8000 → data/*.json
```

Nginx 参考：

```nginx
server {
    listen 80;
    gzip on;
    gzip_types application/json text/css application/javascript;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }
    location /images/ {
        proxy_pass http://127.0.0.1:8000;
        expires 7d;    # SPU 图片长缓存
    }
}
```

也可以不用 Nginx，FastAPI 直接托管一切（当前已实现），小流量内部使用足够。

## 性能设计（硬性约束的实现位置）

| 约束 | 实现 |
|---|---|
| 不整模块推给前端 | `/rows` 强制分页（默认 300，上限 500 行/页），表格滚动到底自动拉下一页 |
| 图表不画原始点 | 图表只用 summary 预聚合结果（几百~几千点） |
| 内存不堆全模块 | Pinia store 离开页面 `reset()`；后端模块缓存按需加载、手动刷新后清空 |
| 首屏不加载全部代码 | vue-router 懒加载模块视图（ModuleView 单独分包，ECharts 按需引入） |
| 10 万行验证 | `scripts/gen_fake_data.py` 已验证：10 万行模块分页接口毫秒级返回 |

> Web Worker（超大 JSON 解析放后台线程）当前数据量用不到，代码未引入；单模块接近 10 万行且前端需要做重过滤时再加，预留位置：`frontend/src/workers/`。

## 类型对齐（前后端字段不漂移）

`backend/jobs/models.py`（Pydantic）是数据结构的事实来源，`frontend/src/types.ts` 手工与其对齐；前端**展示层**另有 `frontend/src/metrics.ts` 的 `METRICS` 作为「展示哪些指标」的唯一事实来源（新增/调整指标需同步此处 + 后端 models/transforms/api）。字段变更时两处一起改，vue-tsc 构建会兜底类型错误。若后续想自动生成，可引入 `datamodel-code-generator` 从 Pydantic 产 JSON Schema 再转 TS。

## 排障速查

| 现象 | 排查 |
|---|---|
| 页面提示「数据文件不存在」 | 先跑 `run_batch.py` |
| 推广指标全是 `--` | 正常：源表无推广列，接入京准通数据源后自动有值 |
| 图片裂 | 确认 `单品spu图片/` 下有对应 `{spu}.png`，重跑批处理会重新拷贝 |
| 日期文件夹有多个 | 自动取最新；想指定可在 `excel_source.py` 扩展参数 |
| 手动刷新慢 | 刷新是同步重跑批处理，数据量大时按钮会转圈几秒，属正常 |
