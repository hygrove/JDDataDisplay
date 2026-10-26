# POP 主模块从 JSON 预物化迁移到 DB 直连

POP 主模块（`pop_spu_detail`）原链路：批处理摄入 Excel → SQLite `daily_detail` → `pipeline.run_all()` 经 `SqliteSource` 读库生成 `data/modules/*.json` + `manifest.json` 落盘 → FastAPI `api.py` 读这些 JSON 文件、内存扁平化后经 `/api/*` 返回。本次改为与库存模块相同的 **DB 直连模式**：批处理只负责摄入与图片产物，API 每次请求直接经 `SqliteSource` 读 `daily_detail`，指标计算复用 `transforms.compute_metrics` / `build_summary`（SSOT 不动），不再生成任何 JSON 中间文件。

## 背景与动机

- **双写与缓存失效是事故温床**：JSON 与 DB 是两份事实，`/api/refresh` 后必须 `clear_caches()`，漏调即"刷新了但页面没变"（ai-rules §4 专门警告）。库存模块自上线即 DB 直连，零此类事故。
- **数据规模撑得起直查**：`daily_detail` 当前 820 行，每次请求整表拉取 + polars 派生比率在毫秒级，与库存"数百行、查询即返回"同量级。
- **单一事实来源**：DB 成为唯一事实，`db_source.py`（PostgreSQL 占位）注释的"DB 作为下游统一取数处"方向就此闭环。
- **口径 SSOT 复用**：API 直查后**不另算一份比率**——`get_rows` 扁平行、`get_summary` 汇总、`/spu/*/analysis` 聚合全部走 `transforms.compute_metrics`（先求和再相除），与原 JSON 路径是同一个函数，杜绝"单日对、汇总不对"。

## Considered Options

- **保留 JSON 预物化（现状）**：读多写少时服务最廉价，但双写/缓存失效类 bug 无法根除，且 `modules/*.json` 随店铺×SPU×天数无界膨胀（`fake_stress.json` 已 13MB）。
- **DB 直连 + API 层缓存**：加一层 TTL/事件失效缓存可省重复拉取，但重新引入"失效时机"这个 bug 面；当前规模下缓存收益为零。**留作数据量增长后的演进选项，不现在引入。**
- **DB 直连、无缓存（选定，库存同款）**：请求路径 = `SqliteSource().fetch()` → `transforms` → 响应；新鲜度天然等于库内数据（摄入完成即生效），无任何失效逻辑。

## Consequences

- **确定性修复（改造中挖出的旧隐患）**：polars `group_by` / `unique` 默认行序随线程调度漂移——**同一进程内连调两次结果都不同**（实测 `build_summary` 的 `top_spus` 顺序 5 次调用 3 种排列）。旧的「批处理时冻结 JSON」恰好掩盖了这一点；改为每请求重算后必须钉死顺序。修法：`transforms.aggregate` 的 `group_by` 与 `build_summary` 的 `unique`/`group_by` 三处加 `maintain_order=True`（值不变，行序 = 首现顺序）。由此 `top_spus` 的跨店铺顺序与旧冻结值不同（旧值本身是不可复现的哈希产物，既非首现序也非排序；该字段前端零消费，仅 `types.ts` 声明），基线比对对该字段按 `(shop, spu)` 归一后通过。
- **`/api/refresh` 不再需要清缓存**：`api.py` 的三份内存缓存（`_module_cache` / `_summary_cache` / `_flat_cache`）与 `clear_caches()` 整体删除，锁与缓存代码 ~120 行蒸发。
- **`pipeline.py` 收缩为「图片同步 + 模块注册表」**：`run_module` / `run_all` / JSON 落盘删除；保留 `sync_images()`（原 `_load_image_map` 的同步职责）与 `default_registry()`（模块元数据，manifest 与批处理状态共用）。`transforms.build_module_data` 与 `models.ShopNode/SpuNode/ModuleData`（层级 JSON 专属结构）随之删除——**前端 types.ts 里的对应 TS 类型是历史对齐产物，前端零改动、契约不变，不在本次清理范围**。
- **批处理产物只剩**：`app.db`（数据）、`images/`（SPU 原图 + 缩略图）、`status.json`（运行状态）。`data/modules/*.json` 与 `data/manifest.json` 为陈旧产物，验证通过后删除；`config.MODULES_DIR` 常量移除（两份 `verify_organize*.py` 的 monkeypatch 元组同步去掉该键）。
- **时间戳语义变化（有意）**：`/api/manifest` 的 `generated_at` / `modules[].updated_at` 与 `/summary` 的 `updated_at` 原为"批处理生成 JSON 的时刻"，现改为 `MAX(processed_file.processed_at)`——"数据最后一次摄入的时刻"，比请求时刻诚实。前端仅在 `types.ts` 声明这些字段、无视图消费，无感知。
- **空库语义（有意）**：未跑批处理（`daily_detail` 为空）时 manifest/rows/summary 返回 404"暂无数据"，与旧"数据文件不存在"同为 404，前端错误展示路径不变；未知 `module_id` 返回 404"模块不存在"。
- **规模天花板**：数据涨到数万行后，每请求全量拉取 + 扁平化会到百毫秒级——届时按上方"演进选项"加 API 层缓存或 SQL 侧聚合，不回头改 JSON。
- **验证方式**：`.scratch/pop-db-direct/verify_pop_parity.py`（改造前后 TestClient 基线逐字段比对，剔除时间戳字段）+ 真导入 + `verify_organize_execute.py`（跑全链 run_once 的沙盒夹具，覆盖新批处理链）。
