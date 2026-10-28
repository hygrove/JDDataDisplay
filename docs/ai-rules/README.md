# JDDataDisplay · AI 接手规则（唯一事实来源）

> 本文件是本项目 **AI 开发的规则唯一事实来源**。任何"怎么改才安全""为什么不能这么动"的问题，先读这里。  
> 论据与历史决策见 `docs/adr/`（架构决策记录，已被 `AGENTS.md` 注入）；工单过程见 `.scratch/<feature>/`。  
> 规则按"约束 → 为什么 → 怎么验证"组织；环境坑标注**实测版本**，版本升级后可能失效，失效即废弃，别锁死成永恒真理。

---

## 0. 进项目第一件事（必做清单）

1. 读 `AGENTS.md`（已被 IDE 注入）→ 本文件 → 相关 ADR。
2. 后端改动前/后必跑：`python -c "from backend.app.main import app"`（真导入，见 §3）。
3. (可选)前端改动后跑：`npm run build` + 真浏览器实测（见 §3、§5）。
4. 任何不可逆动作（归档/迁移/删数据）走 §6 最小安全流程。
5. 非显然的技术选型，先写 ADR（`docs/adr/`）再动手。

---

## 1. 数据真相（最致命区，静默损坏数据）

### 1.1 长表 `daily_detail` 只增不减

- **约束**：`db.py:_upsert_long_table` 只有 `INSERT OR REPLACE`，全项目**无** `DELETE FROM daily_detail`。
- **为什么**：任何让源数据"消失"的动作（删/移/隔离源表）**不会清理对应行**，会冻结成僵尸行——仍在聚合、永不更新、零报错。
- **推论**：凡"把源表移走"的功能，必须先证明被移走的每个 key 在别处有来源。
- **论据**：`docs/adr/0001-archive-criterion-key-coverage.md`（关键覆盖判定）。

### 1.2 摄入门控 = 全量内容 sha256 字典相等

- **约束**：`db.py` 门控比对 `hashes==old and _count_rows()>0`，**不含 mtime/大小**；增/删/改名/改内容**一律整表重算**。
- **key** = `(shop, date, spu)`；行级去重三层：`unique(keep="last")` + `UNIQUE(date,shop,spu)` + `INSERT OR REPLACE`。
- **怎么验证**：改摄入逻辑后跑一次批处理，确认门控触发/跳过的边界正确。

### 1.3 明细 vs 推广的过滤条件不一致——禁止统一

- **约束**：明细要求 `spu` 非空 + ≠合计 + `date` 匹配 `^\d{4}-\d{2}-\d{2}$`；推广**只**要求 `spu` 非空 + ≠合计（无日期正则）。
- **为什么**：两者来源不同、口径不同，强行统一会漏数据或误判。
- **怎么验证**：key 提取必须复用各自规则，别抽一个公共函数统一。

### 1.4 文件名约定

- `{店铺名}_{商品明细|推广数据}_{起}_{止}.{xlsx|csv}`；`shop = path.name.split("_")[0]`；`~$` 前缀锁文件跳过。
- 数据范围由 `店铺spu登记信息.xlsx` 白名单门控——**改源文件不会自动新增店/SPU**，必须登记。

### 1.5 快照表 vs 长表：两种表语义，别混用

- **约束**：本项目的库存表与 POP 表是**两种不同的表语义**：
  - `daily_detail`（POP 销售明细）= **长表**，只增不减，⛔ 全项目无 `DELETE FROM daily_detail`（§1.1）。
  - `inventory_daily` / `inventory_city_slow`（库存）= **快照表**，按 `(shop, date)` 先删后插（ETL `inventory.py:_replace_file_rows` 本来就如此），并按**保留策略**滚动淘汰旧日期。
- **为什么**：库存快照是「某店某日一次库存状态的完整导出」，全部下游消费（矩阵表 / 补货 / 预警 / 结构分布）只用**单行快照内**的列，⛔ **无任何跨日期聚合**——补货的「近30日出库」「30日有货天数」是**源表自带的列**（`inventory_metrics.CITY_SUFFIXES`），后台导出时已算好。故删旧日期**不会**让任何业务数字算错。
- **保留策略**：每店保留**自己最新一份**快照（per-shop），旧的**永久删除**（真删，不进回收站）。正常情况三店同日下载 ⇒ 等价于「只保留最新一天」；某店漏下载时该店保留自己上一份，⛔ 不被删空（历史时点商家后台导不出来，删了不可再生）。
- **推论**：⛔ **不要把「长表只增不减」套到快照表上**，也别把快照表的删旧策略套到长表上。判据是「这张表是不是快照表」，不是「项目里有没有 DELETE」。
- **论据**：`docs/adr/0015-inventory-snapshot-retention.md`（保留口径、删除顺序自愈论证、与 ADR-0006 回收站语义的边界）。

---

## 2. 单一事实来源（SSOT）

- **指标（明细/推广）**：后端 `sources/base.py:LONG_COLUMNS` 收 16 个可累加原始列；比率（`conversion_rate`/`avg_price`/`roi` 等）不入长表，由 `transforms.compute_metrics` **先求和再相除**，保证行/店/日/区间四层一致。⚠️ 分母依赖 `date` 的指标在 `aggregate(df,["shop"])` 时须在端点内单独分组算。
- **聚合行序必须确定**：`transforms.aggregate` / `build_summary` 的 `group_by`/`unique` 全部带 `maintain_order=True`——polars 默认行序随线程调度漂移（同进程两次调用都不同），ADR-0012 后 summary 每次请求重算，去掉它图表会在两次刷新间重排。⛔ 别当「冗余参数」删掉。
- **指标（前端）**：`frontend/src/metrics.ts:METRICS` 唯一事实来源；新增指标**三处同步**：①`models.py` ②`metrics.ts` spec ③`types.ts`；勾选态存 `localStorage jd.metric-config.v1`。
- **库存指标**：唯一来源 `backend/jobs/inventory_metrics.py`（identity/per_city/national + 可见/可聚合/锁定）；前端矩阵列与勾选面板由 `/api/inventory/meta` **动态构建，不写死**。
- **列投影**：按**列名白名单**投影（不按列位置；生产原表 2000+ 列乱序，"前 22 列是固定列"纯属巧合）。
- **城市匹配**：8 城（北京/武汉/上海/广州/成都/西安/德州/沈阳）**精确等值匹配**；`广州补货B`/`上海零售` 等衍生前缀自然排除——⛔ 不用 `startswith`（会误吞「西安」）。
- **设计令牌（颜色/边框/圆角）**：`frontend/src/style.css` 的 `:root` 是唯一事实来源；组件 scoped 样式一律引用 `var(--color-text-*)` 等变量，⛔ 不写魔法色值（改一处即全局生效）。
- 🔴 **给行 payload 加字段必须两处同时改**：装配处（dict 里塞值）**且** `RestockRowModel` 里显式声明。`model_config = {"extra": "allow"}` 意味着**未声明的字段会被 Pydantic 静默丢弃**——不声明 = 白加，接口返回里没有该字段，而前端类型已声明 → 表现为"前端拿到 undefined 却没人报错"。

---

## 3. 验证铁律（守住质量的闸门）

### 后端

- 🔴 改完必**真导入**：`python -c "from backend.app.main import app"`。`py_compile` 只查语法，**查不出 `NameError`**（类体默认值导入期求值）。
- ⛔ **不引入 pytest**：`requirements.txt` 是完整 freeze（为异地复现）；沿用独立脚本 + 自制断言计数器 + `sys.exit(1)`。
- 🔴 新端点必写 `response_model=`（否则 `/docs` 响应体空白）。
- ✅ 端点验证优先 `TestClient(app)`：免端口占用、可 monkeypatch 造边界；起真服务仅作最终 curl 确认。
- 🔴 FastAPI 0.141.x + Starlette 1.6.x 的 `include_router` 在 `app.routes` 里是 `_IncludedRouter` 条目（非展开的 `APIRoute`）→ 判路由是否注册用 **TestClient 真发请求**，按 `r.path` 过滤会得 0（虚惊）。

### 前端

- 🔴 改完 `.vue`/`.ts` **必须 `npm run build`**（FastAPI 托管 `frontend/dist`）。
- ⚠️ grep dist 字符串**只证明构建通过，不证明浏览器生效**——交付前必须真实浏览器实测（见 §5）。
- **判定与渲染分离**：判定集中进 `src/modules/*.ts` 纯函数（node 断言），`.vue` 只渲染。「roi=null 被当 0 排序」「阈值内小额 delta 误染」页面上看正常，只有断言能抓。⚠️ 判定用的格式化函数**自己写**，别复用通用 formatter（历史降级契约：`null` 可能要显示 `--` 而非 `0.00`）。
- 🔴 **最浅文字只走令牌，别手写浅灰**（2026-10-24 用户反馈：「最浅的字体都快看不清了」）：
  - 令牌层已把最浅三档整体加深一档——`--color-text-4 #56657a` / `--color-text-5 #64748b`（原 #94a3b8）/ `--color-text-6 #6b7b90`（原 #c4ceda）；**改令牌即全站生效**，不必逐处手改。
  - 新增「最浅/次要/脚注/占位/说明」文字 → 一律 `var(--color-text-5)`；「禁用/无数据/placeholder」→ `var(--color-text-6)`；⛔ 不许再手写比 text-6 更浅的灰色。
  - ⛔ **文字令牌不得用于边框/分隔线**（加深后虚线会过重）→ 用 `--color-divider`（#c4ceda）/ `--color-divider-strong`（#94a3b8）/ `--color-border-*`。
  - 自检闸门：`npm run verify:tokens`（阶梯单调性 + 对比度下限 + 禁手写浅灰 + 禁文字令牌当边框），已并入 `npm run verify` 首步。

---

## 4. 部署与运维（避免"改了不生效"的鬼打墙）

- 🔴 **每晚 20:15 计划任务（`JDViz_DailyRefresh`，SYSTEM）跑的是工作区代码，不是某个 commit** → 未提交的半成品会被当晚任务直接吃到。改动要么尽快提交，要么当天别留半成品过夜。
- 🔴 **托盘服务不热重载**：服务进程已把模块载入 `sys.modules`，`POST /api/refresh` 仍在旧代码上跑。验证后端改动要么**重启服务**（启停权归用户），要么用**新进程**直跑（如 `python -m backend.jobs.run_batch`）。
- ⚠️ 托盘日志文件名在导入期按"启动日"定死 → 查"今天"的访问日志要按**服务启动日**找文件。
- 🔴 **organize 生产开关设在调度链路，不在服务进程**：开关 `JD_ORGANIZE ∈ off|dry-run|on`（默认 `dry-run`）写在 `scripts/refresh_daily.ps1` 的纯 ASCII 环境变量里（用户/交互式环境变量对 SYSTEM 任务无效）。详见 `docs/organize-go-live.md`。

---

## 5. 前端专项坑（浓缩要点）

- **涨跌配色先问"涨的是钱还是盈亏"**：成交红涨绿跌（中国习惯）；「加投/减投」无盈亏语义用中性色。
- **布局**：flex item 必须显式 `min-width:0`（默认 `auto=min-content` 撑开定宽列）；列宽用显式类名，⛔ 用 `:nth-child`（依赖"每行列数相同"隐藏前提）。
- **echarts 读不到 CSS 变量**（`var(--x)` 只生效于 CSS 属性，写进 SVG `fill` 静默失效）→ 用 `utils/cssVar.ts` 的 `cssVar()` 从 `:root` 读真实色值 + `withAlpha()`。SVG 三坑：①`getBBox()` 散点符号返退化值→用 `getBoundingClientRect()`；②只靠 `stroke` 认 markLine 会误计→还要匹配单段直线 `d`；③markLine label 一横一竖必打架→阈值数字放图下说明行。
- **跨浏览器布局炸弹**：「定高容器 + `align-items:flex-end` + 内容 `height:auto`」三者同现（Edge 与 Chrome 不一致）→ 修法：容器不定高 + `flex-start` + 显式 `line-height`（不用 `normal`）。headless 下差异常不复现，改中间量排查。
- **MonthCompareTable 特定口径**（2026-10-13 落地）：用 `rangeMap` 非 `rawRanges`；`target=min(各月天数)`，`trimToDays` 绝不反向延长；默认展开必补 `onMounted` 初始化（否则"开着但空白"）。改前 grep `utils/month.ts` + `DateRangePicker.vue`。
- **真浏览器实测要点**（Edge + CDP，⛔ 别用 agent-browser 的 Chromium）：视口设 1500×950；点击前 `scrollIntoView`；用 `data-testid` 区分表头与数据行；"看起来没变化"先 Ctrl+F5 排除缓存。

---

## 6. 不可逆动作最小安全流程（organize / 归档 / 迁移通用）

1. **前置指纹落盘**（源文件 sha256 + 大小 + 目录清单）。
2. **断言前置状态**（防重复执行；如断言 `_archive` 尚不存在）。
3. **执行**。
4. **后置逐份核对**：内容哈希 + 清单登记集合 == 实际集合 + 环境未被污染。
   - 「能撤回」要有前置基线才成立；备份必须在注入/改动**之前**拍；恢复的**唯一可信判据是逐字节比对**，⛔ 不靠"手工改回来"。
   - organize 回滚覆盖两区（`--rollback` 同时遍历归档与隔离清单）；目标已有同名→只报冲突不覆盖。
   - 论据：`docs/adr/0006-recycle-bin-cleanup.md`（回收站语义）。

### 6.1 推送到 public 远端（不可逆：推上去撤不回来）

**仓库 `hygrove/JDDataDisplay` 是 public**，业务数据一旦推上去就是公开的。

- ⛔ **业务数据禁入仓**：`ResourceData/`（业务 Excel/CSV/图）、`app.db`（含 POP 销售明细与库存业务数据）、`backend/app/data/` 运行时产物。`.gitignore` 已双保险（`ResourceData/` + `*.db` + `backend/app/data/`），`sample_data/` 是**脱敏样例**、刻意入库。
- 🔴 **核验只能看 `origin/main`，⛔ 不能用 `git log --all`**：`--all` 会把**本地未推送的分支与 worktree** 一并算进来，命中 `ResourceData/*.xlsx` 极易误判"业务数据已入库"，白折腾甚至做出错误决策。正确判据：
  ```bash
  git log origin/main --diff-filter=A --name-only --pretty=format: | grep -i '^ResourceData'   # 必须为空
  git ls-tree -r origin/main --name-only | grep -ci '^ResourceData'                              # 必须为 0
  ```
- **推前扫描四件套**：①`.gitignore` 双保险复核 ②密钥正则（`api_key|secret|token|private key|sk-|ghp_`）③外网硬编码 IP（排除 `127.0.0.1`/`0.0.0.0`/`localhost`）④真实店铺名/手机号。
- **推前必过验证闸门**（§3）：后端真导入 + 后端单测 + 前端 `npm run build` + `npm run verify:tokens`（纯静态项）。红灯不推。
- ⚠️ **并行跑「前端 build」与「后端真导入」会假报** `RuntimeError: Directory 'frontend/dist/assets' does not exist`——build 会清空重写 `dist/assets`，撞上后端导入读目录。**不是真 bug**，串行重跑即过。验证项之间要么串行，要么后端导入放最后。

---

## 7. 协作纪律（代码可维护）

- **注释**：中文注释、英文标识符；类/公开函数必写文档注释（功能/参数/返回/异常/示例）；复杂逻辑加「为什么」。
- ⛔ **禁决策残留**：方案 A/B 只用一个，清掉另一个的痕迹（别留"本来想用 B"的注释）。
- **产出默认单文件**（除非用户明确要拆分）。
- **技术讲解/文档**：逆向挖隐式契约，不照抄；铁律带真实错误数字比抽象原则有效；长提示词关键约束重复三次防后段稀释。
- **评估先行**：接到功能/项目需求先出可行性、难度分级、主流 vs 用户方案差距、备选路线、演进趋势五步评估，再动手。
- **issue 追踪**：每个非平凡改动在 `.scratch/<feature>/` 开 issue 文件（状态行 `Status:`），不使用 GitHub Issues。

---

## 附录 A：环境坑（Windows 专属，标注实测版本）

- **回收站（ctypes，实测 Win）**：`SHFileOperationW` + `FOF_ALLOWUNDO` **成功**送回收站时返回码是 `2`（`ERROR_FILE_NOT_FOUND`）→ ⛔ 成功判据**不能看返回码**，要看"原位已消失 **且** `SHQueryRecycleBinW` 条目数/容量增加"。调用前**预检**回收站可用性（无回收站的卷上 `FOF_ALLOWUNDO` 退化为永久删除）。⛔ 不引 `send2trash`/`winshell`（破坏 freeze 复现）。
- **PowerShell 静默失败**（exit 0 但输出空）→ 进程操作用 Bash 直连 `tasklist`/`taskkill`。
- **TaskStop 只杀 launcher，child 残留** → ⛔ 别按镜像名批量结束（会误杀 `msedgewebview2`）→ 按 PID 逐个 `taskkill //PID <pid> //F`。
- **npm 在 Windows 是 `.cmd`** → `subprocess.run(["npm",...])` 会 `FileNotFoundError`；用 `shutil.which("npm")`。
- **vite proxy 必须写 `127.0.0.1`**：`dns.lookup('localhost')` 返 `[::1,127.0.0.1]` 且 Node20+ verbatim 不回落，uvicorn 只监听纯 IPv4 → ECONNREFUSED 整站 502。`curl localhost` 正常会误导，必须用 `node -e` 测。
- **后端静态前缀与 vite proxy 同步列举** `/api` + `/images` + `/thumbs`（漏 `/thumbs` → dev 图片全裂、生产正常，极具迷惑性）。
- **8000 常占** → `netstat -ano|findstr :8000` 拿 PID → `Stop-Process -Force`。
- **Edit 工具对多行中文易报"内容不一致"** → 改用 Python `io.open(...).read().replace(old,new)` + `assert s.count(old)==1`。
- **`.ps1`/`.vbs` 须纯 ASCII**（中文注释 GBK 失败）；`.pyw` 无此坑。

## 附录 B：验证脚本清单（改对应模块后必跑）

- **organize 全套**：`backend/jobs/verify_organize*.py`（`make-fixtures`/`snapshot`/`diff`/`selftest`/`env`/`accept <源根>` 等子命令；`accept ResourceData` 一键闸门）。
- **库存快照淘汰**：`backend/jobs/verify_inventory_retention.py`（`python -m backend.jobs.verify_inventory_retention`）——8 组场景：保留集判定（含落后店降级）、损坏文件不删、同店同日多文件不删、健康检查门控、执行后三集合相等、幂等、**中断自愈**（④-1/④-2 后崩溃重跑收敛）、dry-run 零副作用。全程在临时目录造 fixture，⛔ 不碰生产数据。
- **前端**：`frontend/verify_month_compare.mjs`、`frontend/verify_agg.mjs`（`npm run verify:month` / `verify:agg`，需 8000 在跑）、`frontend/verify_text_tokens.mjs`（`npm run verify:tokens`，纯静态，**不需 8000**；守最浅文字可读性）。
- **后端自愈**：改完 `python -c "from backend.app.main import app"`。
- ⚠️ `verify_all.py` 与 `verify_promo_*` 已在 `78eb10f` 删除，**不要引用**。

> ⚠️ **库存模块的测试需要真实数据**：`backend/tests/test_inventory_api.py` 读 `config.DB_PATH` 指向的库，库为空时 34 个用例会大面积失败（`no such table: inventory_daily`）——那**不是**代码回归，是环境缺数据。跑之前先设 `JD_DB_PATH` 指向有数据的库。

## 附录 C：不要做的（历史撤销项）

- **`/promo` 推广预算优化已撤销**（`e3b3f27` 加入、`78eb10f` 删除），当前树不含。若重新启用，先 `git show e3b3f27` 恢复 spec 与 4 份 ADR，⛔ 别凭记忆重写。
- **数据真相铁律优先于"省时间"叙事**：organize 实测**净 +1.15s**（判定侧要先全量读源算 key），唯一价值是目录可读性，⛔ 别拿性能推销它。
- ⛔ **不要动 POP 长表 `daily_detail`**：它是销售明细长表、只增不减（§1.1），**不参与**库存快照的保留策略（§1.5）。库存按「每店保留最新一份」淘汰旧快照（ADR-0015）时，**`daily_detail` 一行都不许删**——两套表语义，别把快照表的删旧策略套到长表上，也别把长表的只增不减套到快照表上。
