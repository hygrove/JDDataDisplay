# 库存手动刷新端点：写库边界、dry-run 与发布验收

**状态**：Accepted（2026-11-08 补记并验收）
**来源**：功能代码由 `feature/element-plus-unification` 分支的自动检查点 `54a0ec8` 带入，2026-11-06 随合并提交 `fd843c7` 进入 `main`。原始会话未留 spec / ADR，2026-11-08 推送前双轴审查把它标为「无 ADR 的在途半成品写库端点（可能阻断推送）」，本 ADR 补齐决策记录与验收证据。
**范围**：`POST /api/inventory/refresh`（`backend/app/inventory_refresh.py`）、前端 `RestockPanel.vue` / `InventoryView.vue` 的刷新按钮与状态胶囊、`frontend/verify_inventory_refresh.mjs`。
**不碰**：`daily_detail`（POP 单品长表）、`/api/refresh` 整条批处理链路、库存快照源文件（默认不删）。

## 背景与动机

1. **整条批处理太重**：`POST /api/refresh` 会跑 `run_batch.run_once()`——源表整理 → POP 明细摄入 → 库存摄入 → ERP 映射 → 缩略图 → 快照淘汰 → SPU 图片同步。其中 POP 明细摄入要解析全部销售明细表，是「慢」的主因，而库存页根本不需要它。用户明确要求「只刷新库存数据，单品数据不管」。
2. **写库端点必须与纯查询模块分开**：`inventory_api.py` 每个端点都只读库、拼响应。把摄入 / 删快照混进去会让「哪些端点会写库」这件事不再一眼可见，故独立成 `inventory_refresh.py`，在 `main.py` 单独注册。
3. **它此前缺少发布级记录**：代码、前端与真浏览器脚本都在，但没有 ADR、没有 spec，也没有可复现的验收结论，导致推送前无法判断它是不是半成品。

## 决策

### 一、只跑库存四步，且顺序不可换

| 步骤 | 作用 | 失败分级 |
|---|---|---|
| `plan_keep_set()` | 只读探测该保留哪些快照，**不删任何东西** | 失败则退回全量摄入，不阻断刷新 |
| `ingest_inventory_to_db()` | 增量摄入，文件级 sha256 门控（内容没变就跳过） | **失败 = 整次刷新失败**（数据没更新，页面刷新了也是旧的） |
| `build_inv_thumbs()` | 新 SKU 缩略图，幂等（产物已存在且不比源图旧则跳过） | 只告警，不阻断 |
| `retention.run()` | 库存快照淘汰（ADR-0015：每店保留最新一份） | 只告警，不阻断——旧快照多留几天，库存数据仍正确 |

前两步顺序不能换：先探测保留集再摄入，否则会把已被判定「该淘汰」的旧快照重新写回库。

### 二、不可逆动作默认 dry-run

第 4 步会**删源文件 + 删库里的行**，是不可逆动作（ADR-0002 / ai-rules §6）。因此复用项目既有 `resolve_mode()` 口径：默认 `dry-run`（只出保留 / 待删清单，不动手），真删必须显式设 `JD_INVENTORY_RETENTION=on`。「淘汰这一步确实跑了」与「真的删了」是两件事——dry-run 同样产出决策与报告供运维核对。**UI 上的一个按钮不得悄悄提升不可逆动作的权限级别。**

### 三、并发保护：进程内非阻塞锁，跨进程交给 SQLite

`_REFRESH_LOCK.acquire(blocking=False)`：第二次点击立刻回 **503**「正在刷新中」，⛔ 不排队（排队会把 HTTP 请求拖成长连接）。跨进程（与 23:15 计划任务撞车）进程内锁挡不住，靠 SQLite 自身写锁与批处理侧容错兜底；这是单文件嵌入式数据库的已知边界，不引入分布式锁。

### 四、响应契约与前端类型单一来源

`InventoryRefreshResult` 是 Pydantic `response_model`，14 个字段全部必填（失败也返回完整结构，`error` 非空串）；`frontend/src/types.ts` 的同名 interface 与之逐字段对齐。2026-11-06 合并时自动 merge 曾把两侧定义都拼进 `types.ts`，已删除 ops-daily 侧「端点尚不存在时按注释臆造」的可选字段占位版，**只保留与真实响应一致的这一份**。

### 五、保留合入 main，不剥离

曾评估「把整套功能从 `main` 回退以规避推送风险」。否决理由：用户已明确「所有分支都合并」；剥离会删掉另一会话的成果；且该功能在补齐 ADR 与真浏览器验收后已不再是不可判定状态。若将来要撤销，以 `fd843c7` 为基线单独回退相关文件，`feature/element-plus-unification` 分支仍保留。

## 发布验收（2026-11-08 实测，可复现）

- **隔离前提**：测试后端起在独立端口 **8013**，`JD_DB_PATH` 与 `JD_DATA_DIR` 指向 `.scratch/merge-20261106/verify-inventory/`，淘汰保持默认 dry-run；托盘 8000 主服务未触碰。
- `npm install --no-save --package-lock=false playwright`（系统 Edge channel，⛔ 不改 `package-lock.json`），`node frontend/verify_inventory_refresh.mjs` → **15 / 15 全通过**：按钮位置与配色、点击后状态胶囊、刷新中禁用 + 图标旋转、结果成功态、**真的发出 2 次 POST 且均 200**、刷新后数据重拉与「数据截至」标注、补货 Tab 同效。
- 两次刷新均返回「已是最新数据，源表内容未变化」——源表没动，摄入被 sha256 门控跳过，故**根本没有写库**；只生成了 828 个缩略图文件，全部落在隔离的 `JD_DATA_DIR`。
- **生产库零变化**：`backend/app/data/app.db` 与测试用副本 `sha256` 均为 `8480f881…fcd8`，字节完全一致。
  - ⚠️ 本次另发现：拿 11-06 18:10 的旧指纹基线比对时，6 张表显示「变化」，追查后确认是 **2026-11-07 23:15 计划任务**正常跑批（`processed_at = 2026-11-07T15:15:04Z`、`daily_detail` 950 → 980 行、`inventory_city_slow` 863 → 879 行），⛔ 与本次测试无关。已另存 11-08 新基线 `.scratch/merge-20261106/db-fingerprint-20261108.json`。
- 静态闸门：新端点带 `response_model=`；`main.py` 中 `inventory_refresh_router` 与 `ops_daily_router` 都挂在 SPA 回退路由之前；FastAPI 真导入 OK，OpenAPI 25 条路径含该端点。

## 后果与边界

- **接受**：生产 app 会暴露一个写库 POST 端点；它默认只做幂等摄入 + dry-run 淘汰，不会删源文件。
- **已知限制**：进程内锁挡不住跨进程并发；真删快照要显式环境变量，UI 不提供该开关；`verify_inventory_refresh.mjs` 需要可选 playwright 依赖与独立端口，**不在 `npm run verify` 链**（原因与运行前提已登记在 `docs/ai-rules/README.md` 附录 B）。
- **演进线**：若要求「刷新期间也能跨进程互斥」或「真删除在 UI 可见可审计」，需另立 ADR，把并发控制与权限模型一起裁决，⛔ 不在本次补丁里夹带。
