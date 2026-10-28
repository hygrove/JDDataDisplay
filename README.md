# JD 数据平台

电商多店铺经营数据的可视化看板。Python 批处理每天从 Excel/CSV（可扩展 PostgreSQL）采集清洗数据，写入 SQLite 并导出 JSON；FastAPI 单端口托管前端与数据 API；Vue3 前端做表格 + 图表展示。

> 完整使用说明（页面功能逐项、接口清单、维护注意事项、变更记录）见 **[说明文档.md](说明文档.md)**；
> 新机器从零跑通见 `docs/异地克隆部署指南.md`，SQLite 层设计见 `docs/数据库迁移与架构教学.md`。

## 功能一览（当前模块：POP 单品明细）

- 左右布局：左侧模块导航 + 店铺切换，右侧主业务区
- 日期区间选择（近一周 / 当月 / 上月 / 月份网格四种快捷方式，默认最新一天）、SPU 号或商品名搜索
- 可配置指标卡/表格列/趋势图（共 **15 个指标**，分 3 组，默认勾选 8 个）：商品明细表 9 项 + 推广数据表 4 项 + 退款/其他 2 项；展示项由 `frontend/src/metrics.ts` 的 `METRICS` 派生，可在 ⚙ 指标配置 中调整
- 单日模式卡片网格 ↔ 区间模式「指标 × 日期」矩阵自动切换（由所选区间决定）
- 单品分析页：指标卡、3 张趋势图（含双轴合并）、工作日 vs 节假日对比、**月份对比**（多月份横排 + 点击指标行展开时间轴）、自动洞察文案
- SPU 明细表格：商品图片、虚拟滚动、分页加载（300 行/页）、缺失指标展示 `--`
- 数据导出：一键把当前筛选条件下的**全量**数据导出为 xlsx（每 SPU 一块、图片嵌入纵向居中、比率类总计标橙、数值保留 2 位小数）
- 库存管理（`/inventory`）：8 城现货/采购/出库/周转矩阵表（独立宽表 `inventory_daily`，182 列按列名白名单投影；指标配置驱动，勾选面板扩展列；率值型不合计）。**快照表**：按每店保留自己最新一份快照滚动淘汰旧快照（ADR-0013），各店日期可能不同，页面按店标注「数据截至」
  - 库存预警：城市级阈值三档判定（健康/在途/预警，阈值表可配）+ 补货量建议（销速 × 安全天数 + 覆盖窗口，扣减现货与在途）
  - **可信销速三级估计**（ADR-0010）：有货天数 ≥6 天直算；0<有货天数<6 天**收缩估算**（标 `≈`）；有货天数 ≤0 → 销速 0（宁可不建议，不凭空估）
  - **缺口关注标记**（ADR-0011）：覆盖率（有货天数 ÷ 窗口天数）+ 估算缺货损失双锚判定，页面状态列旁亮紫色「关注」徽章（鼠标移入给出缺货天数与损失件数）；导出的 xlsx 同列保留徽章配色与**单元格批注**（Excel 的悬停提示），「估算缺货损失」列写完整说明句（「周期14 天仅 3 天有货（缺货 11 天），估算缺货损失 15.6 件」）
  - **补货导出搬到后端**（ADR-0008）：xlsx 由 openpyxl 生成，**内嵌商品图缩略图**（SheetJS 做不到）；一个工作簿含城市 sheet + SKU sheet，与页面共用同一套装配逻辑（所见即所得）
  - **ERP 编码独立成表** `erp_map`（ADR-0009）：与库存快照解耦，源表损坏时跳过写入而非覆盖好数据；未登记显示「--」而非 0
  - 结构分布卡：SKU 覆盖 / 上下柜 / 三级类目 / 销售员四维度（带店铺拆分）；库存 SKU 图片按 SKU 取图（与 POP 的 SPU 取图链路独立）
- 手动刷新：前端按钮触发后端重跑批处理（服务无关，服务未启动也能跑批）

> **关于推广指标**：推广费/推广成交金额/ROI/推广占比来自各店铺的 `推广数据_*.csv`（京准通报表），由批处理按 (店铺, 日期, SPU) 左连接补充到明细长表；明细表本身不含这些列。若某 SPU 在推广 csv 中无对应记录，则相关指标展示 `--`。

## 技术栈

| 层 | 选型 |
|---|---|
| 批处理 | Python 3.13 + Polars（清洗/聚合）+ Pydantic v2（建模校验） |
| 后端 | FastAPI + Uvicorn（静态托管 + 数据 API） |
| 前端 | Vue3 + Vite + TypeScript + Pinia + vue-router + @tanstack/vue-virtual + ECharts（vue-echarts 按需引入） |

## 目录结构

```
JDDataDisplay/
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py            # FastAPI：静态托管 + SPA 回退（no-store）+ 图片挂载
│   │   ├── api.py             # /api 路由（DB 直查，ADR-0012）+ 分页/过滤/排序 + xlsx 导出
│   │   ├── analysis.py        # 单品分析：节假日口径、工作日/节假日对比、洞察文案
│   │   └── data/              # 运行时产物（app.db / images / status.json，非源码）
│   └── jobs/                  # 批处理（ETL 层）
│       ├── config.py          # 路径/重试/参数，全环境变量可覆盖
│       ├── models.py          # Pydantic 模型（前后端字段事实来源）
│       ├── transforms.py      # Polars 清洗/聚合（比率「先求和再相除」）
│       ├── pipeline.py        # SPU 图片同步 + 模块注册表（加模块在这里登记）
│       ├── run_batch.py       # 批处理入口（重试逻辑保留，默认关闭）
│       ├── db.py              # SQLite 持久化层（长表 + 源文件哈希增量追踪）
│       ├── make_thumbs.py     # SPU 图片多尺寸缩略图（AVIF + WebP）
│       └── sources/
│           ├── base.py        # Source 协议 + 统一长表列 LONG_COLUMNS
│           ├── excel_source.py  # Excel 源（清洗后写入 SQLite）
│           ├── sqlite_source.py # 从 SQLite 读回长表（展示侧统一入口）
│           └── db_source.py     # PostgreSQL 骨架（未启用）
├── frontend/
│   ├── package.json / tsconfig.json / vite.config.ts / index.html
│   ├── src/
│   │   ├── main.ts / App.vue           # 入口 + 左右布局（侧边导航）
│   │   ├── router.ts                   # 模块视图懒加载 + 路由级加载态
│   │   ├── api.ts / types.ts           # fetch 封装 / 与 Pydantic 对齐的 TS 类型
│   │   ├── metrics.ts                  # 展示指标的唯一事实来源（METRICS）
│   │   ├── modules/index.ts            # 前端模块注册表（配置驱动）
│   │   ├── stores/metrics.ts           # Pinia store（指标勾选，localStorage 按 moduleId）
│   │   ├── utils/month.ts              # 日期区间与月份清单算法
│   │   ├── utils/overlay.ts            # 同页浮层全局互斥
│   │   ├── components/
│   │   │   ├── GenericTable.vue        # 虚拟滚动表格（滚动到底自动加载下一页）
│   │   │   ├── GenericChart.vue        # ECharts 按需引入封装
│   │   │   ├── DateRangePicker.vue     # 统计区间选择器（模块页 / 单品页共用）
│   │   │   ├── MetricConfigPanel.vue   # ⚙ 指标配置面板
│   │   │   └── MonthCompareTable.vue   # 月份对比（多月份横排 + 点击展开时间轴）
│   │   └── views/
│   │       ├── ModuleView.vue          # 通用模块页（单日卡片 ↔ 区间矩阵）
│   │       └── SpuAnalysisView.vue     # 单品分析页（趋势/工作日节假日/月份对比/洞察）
│   ├── verify_month_compare.mjs         # 回归：月份对比纯逻辑
│   ├── verify_agg.mjs                   # 回归：前后端聚合口径一致性
│   └── dist/ / node_modules/            # 构建产物与依赖（不入库）
├── scripts/
│   ├── 启动服务.pyw         # 双击启动：托盘图标 + 无黑框（日常使用推荐入口）
│   ├── tray_launcher.py     # 同进程托盘启动器（pystray 主线程 + uvicorn 子线程，单实例锁）
│   ├── refresh_data.py      # 跨平台数据刷新入口（计划任务 / cron / Docker 通用）
│   ├── setup_sample.py      # 把 sample_data/ 样例铺到 ResourceData/（异地克隆首次部署用）
│   ├── install_tasks.bat / uninstall_tasks.bat # Windows 计划任务（每日刷新 + 开机自启）
│   ├── start_uvicorn.bat / stop_uvicorn.bat   # 前台启动 / 停止（调试用）
│   └── refresh_daily.ps1    # 每日刷新脚本（薄启动器，转调 refresh_data.py）
├── sample_data/             # 入库的演示样例数据（业务数据本身不入库）
├── docs/                    # 专题文档（异地克隆部署 / 数据库架构 / git 笔记）
├── requirements.txt         # 指向 backend/requirements.txt 的引用，便于根目录一条命令安装
└── 说明文档.md              # 完整使用说明（页面功能、接口、维护注意事项、变更记录）
```

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

### 2. 跑批处理（Excel → SQLite → JSON）

```bash
# 异地克隆首次部署：ResourceData/ 不入 git，先铺样例数据（有真实数据源可跳过）
.venv\Scripts\python.exe scripts/setup_sample.py

.venv\Scripts\python.exe backend/jobs/run_batch.py
```

- 读取项目内 `ResourceData/数据源表目录/` 下的固定文件夹（不再按最新日期遍历）：含各店铺 `店铺名_商品明细_*.xlsx` 与 `店铺名_推广数据_*.csv`，按 (店铺, 日期, SPU) 去重合并；
- **先摄入 SQLite**（`backend/app/data/app.db`，按源文件 sha256 增量：源未变则跳过），再从 DB 聚合产出 JSON；
- 清洗聚合后写入 `backend/app/data/`；
- 同时把 `单品spu图片/*.png` 拷贝到 `backend/app/data/images/`；
- 结果（成功/失败、耗时、尝试次数）写入 `status.json`。
- 数据源路径可用环境变量覆盖：`POP_SOURCE_DIR`、`JD_DATA_DIR`、`JD_DB_PATH`。

### 3. 启动后端

**日常使用（Windows，推荐）**：双击 `scripts/启动服务.pyw` —— 无黑框、托盘图标右键「打开面板 / 退出」、单实例防重复。

**命令行（调试 / 服务器）**：

```bash
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

API 文档（自动生成）：http://localhost:8000/docs

### 4. 前端

开发模式（热更新，代理 /api 与 /images 到 8000）：

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

> ⚠️ `vite.config.ts` 的代理目标固定为 `127.0.0.1`（**不要改回 `localhost`**）：本机 `localhost` 会被 Node 20+ 解析成 IPv6 `::1`，而 uvicorn 只监听 IPv4，导致 dev 下 `/api` 与 `/images` 整站 502；`curl localhost` 正常会误导。

生产模式（构建后由 FastAPI 直接托管，单端口交付）：

```bash
cd frontend
npm run build      # 产物在 frontend/dist
# 然后访问 http://localhost:8000
```

>改了前端只需 `npm run build` 即可生效，**无需重启服务**（FastAPI 实时读 dist）。
> 但若页面「看起来没变化」，先按 **Ctrl+F5 强刷**——`index.html` 已设 `no-store`，仍 suspect 浏览器缓存时可清缓存重试。

### 5. 回归验证

```bash
cd frontend
npm run verify            # 跑四份回归脚本（tokens → month → agg → inventory）
npm run verify:tokens     # 设计令牌静态闸门：阶梯单调性 + 对比度下限 + 禁手写浅灰 + 禁文字令牌当边框（纯静态，不需服务）
npm run verify:month      # 月份对比纯逻辑（天数/闰年/跨月/对齐/排序），无需服务
npm run verify:inventory  # 库存展示判定纯函数（商品图降级链 / 缺值显示 / ERP 占位），无需服务
npm run verify:agg        # 前后端聚合口径一致性，需服务已启动在 8000
```

后端单测（stdlib unittest，零新依赖；**必须在 `backend/` 目录跑**）：

```bash
cd backend
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 数据格式

### 明细长表（`backend/app/data/app.db` 的 `daily_detail` 表）

行结构：一行 = 一个 (日期, 店铺, SPU)，`UNIQUE(date, shop, spu)`。

- 只存 16 个**可累加**原始列（访客 / 成交 / 金额 / 推广 / 退款…），比率指标**不入库**；
- 比率（转化率 / 客单价 / ROI / 推广占比 / 搜索点击率）由 `transforms.compute_metrics`
  在**查询时**按「先求和再相除」派生——分母为 0 或源数据缺失时为 `null`，前端展示 `--`；
- 汇总一律「先求和再算比率」，避免比率直接平均的统计错误；
- API 每次请求经 `SqliteSource` 直查该表（ADR-0012，与库存模块同款），摄入落库即生效，
  无中间 JSON 文件、无内存缓存。

### 其他文件

- `status.json`：批处理状态（上次运行时间、成败、尝试次数、耗时、消息）；
- `images/` + `thumbs/`：SPU 原图与多尺寸缩略图（批处理幂等同步生成）。

### API

| 接口 | 说明 |
|---|---|
| `GET /api/manifest` | 模块清单 |
| `GET /api/module/{id}/rows` | 分页明细。参数：`shop` `date` `keyword` `sort_by` `sort_order` `page` `page_size`(≤500) |
| `GET /api/module/{id}/summary` | 图表聚合数据 |
| `GET /api/module/{id}/spu/{spu}/analysis` | 单品分析（趋势 / 工作日节假日 / 洞察），支持 `start`+`end` 区间 |
| `POST /api/module/{id}/export` | 导出当前筛选条件下全量数据为 xlsx（前端「导出数据」按钮） |
| `GET /api/status` | 批处理状态 |
| `POST /api/refresh` | 手动重跑批处理（前端「手动刷新」按钮；先摄入 SQLite 再产 JSON） |

## 如何添加一个新模块（配置驱动，不写重复页面）

1. **实现数据源**：在 `backend/jobs/sources/` 新建文件，实现 `Source` 协议（`fetch() -> pl.DataFrame`），输出统一长表列（见 `sources/base.py` 的 `LONG_COLUMNS`），参考 `excel_source.py`；数据库源参考 `db_source.py` 骨架。
2. **注册模块**：在 `backend/jobs/pipeline.py` 的 `default_registry()` 里追加一个 `ModuleSpec`。
3. **配置前端展示**：在 `frontend/src/modules/index.ts` 的 `MODULE_CONFIGS` 里加一份配置（表格列、指标卡片）；若涉及新指标，在 `frontend/src/metrics.ts` 的 `METRICS` 追加一条 spec，并同步后端 `models.py` / `transforms.py` / `api.py` 的字段。
4. 跑批处理 → 前端左侧自动出现新模块，路由、表格、图表、虚拟滚动全部复用。

如果新模块指标超出当前 15 个：在 `backend/jobs/models.py` 的 `MetricRecord` 加字段 → 前端 `src/types.ts` 同步加（保持对齐）→ 在 `src/metrics.ts` 的 `METRICS` 加一条 spec 并在模块配置里引用即可。

## 定时任务与重试

- **Windows 计划任务**：推荐直接双击 `scripts/install_tasks.bat` 自动注册（脚本按自身位置推导路径，改名/迁移后无需改）；如需手动命令，把下面命令里的 `<项目根目录>` 替换成你的实际项目路径即可：`schtasks /create /tn "JD数据批处理" /tr "\"<项目根目录>\.venv\Scripts\python.exe\" \"<项目根目录>\backend\jobs\run_batch.py\"" /sc daily /st 20:15`
- **Linux cron**：`0 6 * * * /path/.venv/bin/python /path/backend/jobs/run_batch.py`
- **重试机制**：指数退避（2s → 4s → 8s），最多 3 次。**功能保留、默认关闭**（上游源数据本身出错时重试几乎必然再次失败，不如让失败立刻暴露），开启方式：
  ```
  set BATCH_RETRY_ENABLED=true
  set BATCH_RETRY_MAX_ATTEMPTS=3
  set BATCH_RETRY_BASE_DELAY=2
  ```
- 失败详情可在前端状态栏、`/api/status` 或 `status.json` 查看，前端「手动刷新」可随时补跑。

## 源表整理器（可选，默认 dry-run）

源表目录按 30 天滚动窗反复下载，文件持续膨胀。整理器在每次批处理**入库之前**先判定每个源文件的「保留 / 归档 / 隔离 / 跳过」，按 **key 覆盖**（该文件贡献的每个 `(店铺, 日期, SPU)` 都仍被保留集里的其他文件供着）搬走冗余文件——收缩目录而不丢任何数据行。

- 模式由环境变量 `JD_ORGANIZE` 控制：`off` / `dry-run`（**代码默认**，真实目录上什么都不搬，只打印计划）/ `on`（真搬）。
- **生产开关在调度链路**：每晚 20:15 的 SYSTEM 计划任务经 `scripts/refresh_daily.ps1` 拉起批处理，该启动器在未显式设 `JD_ORGANIZE` 时置为 `on`——即生产环境实际在跑整理。改完当晚即生效，无需重启服务；手动跑 `run_batch` / 前端「手动刷新」仍走代码默认 `dry-run`。
- 归档进 `ResourceData/归档/`、异常文件进 `ResourceData/隔离/`，两份清单均可回滚（`--rollback`）。
- 设计约束（归档判据 / 无人值守容错 / 隔离哨兵 / 回收站清理等）见 `docs/adr/`（ADR-0001~0007）与 `docs/organize-go-live.md`。
- 验收：`backend/jobs/verify_organize_acceptance.py accept ResourceData`（逐格比对「全量」与「只留保留集」的指标一致性，当前 11/11 通过）。

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
| 图表不画原始点 | 图表只用 summary 聚合结果（几百~几千点，查询时现算） |
| 内存不堆全模块 | Pinia store 离开页面 `reset()`；后端无缓存、DB 直查（ADR-0012），数据量显著增长后再按需加 API 层缓存 |
| 首屏不加载全部代码 | vue-router 懒加载模块视图（ModuleView 单独分包，ECharts 按需引入） |

> Web Worker（超大 JSON 解析放后台线程）当前数据量用不到，代码未引入；单模块接近 10 万行且前端需要做重过滤时再加，预留位置：`frontend/src/workers/`。

## 类型对齐（前后端字段不漂移）

`backend/jobs/models.py`（Pydantic）是数据结构的事实来源，`frontend/src/types.ts` 手工与其对齐；前端**展示层**另有 `frontend/src/metrics.ts` 的 `METRICS` 作为「展示哪些指标」的唯一事实来源（新增/调整指标需同步此处 + 后端 models/transforms/api）。字段变更时两处一起改，vue-tsc 构建会兜底类型错误。若后续想自动生成，可引入 `datamodel-code-generator` 从 Pydantic 产 JSON Schema 再转 TS。

## 排障速查

| 现象 | 排查 |
|---|---|
| 页面提示「数据文件不存在」 | 先跑 `run_batch.py` |
| 改了前端但页面没变化 | 先 `npm run build`，再 **Ctrl+F5 强刷**；仍无效则确认 uvicorn 是当前这个项目的进程 |
| 改了后端但接口没变化 | 重跑 `run_batch` 并**重启 uvicorn 8000**（旧进程返回旧字段/旧数据） |
| 端口 8000 被占用 | uvicorn 启动失败「地址已被占用」。`netstat -ano \| findstr :8000` 取 PID → `Stop-Process -Id <PID> -Force`；或改用其他端口（`--port 8100`，同时改 `vite.config.ts` 的代理目标） |
| 推广指标全是 `--` | 正常：明细表本身不含推广列，需店铺的 `推广数据_*.csv`（京准通报表）才会左连接补上 |
| 图片裂 | 确认 `单品spu图片/` 下有对应 `{spu}.png`，重跑批处理会重新拷贝 |
| `npm run dev` 整站 502（`/api` 与图片全挂，但生产模式正常） | 代理目标 `localhost` 被 Node 20+ 解析成 IPv6 `::1`，连不上只听 IPv4 的 uvicorn → ECONNREFUSED。`vite.config.ts` 代理目标已固定为 `127.0.0.1`；若仍 502 需**重启 `npm run dev`** 让新配置生效（仅改配置不重启无效），并 Ctrl+F5 强刷 |
| 手动刷新慢 | 刷新是同步重跑批处理，数据量大时按钮转圈几秒属正常；也可用 `scripts/refresh_data.py`（不依赖服务） |
| 页面出现新的未登记店铺/SPU | 数据范围由 `ResourceData/店铺spu登记信息.xlsx` 白名单门控，未登记的不出现；改数据源不会自动新增 |
