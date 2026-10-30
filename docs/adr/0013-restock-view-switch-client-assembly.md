# 补货清单视图切换改为「一次取全 + 客户端装配」

补货工作台的城市视图（`restock-view-city`）与 SKU 视图（`restock-view-sku`）此前是**两个后端响应**：`/api/inventory/restock?view=city|sku`。两者其实出自**同一次计算**（`_restock_payload`），区别只是装配方式——`view=city` 返回按城市分组（只含建议量 > 0 的行），`view=sku` 返回平铺长表（保留建议量为 0 的行）。接口按 `view` 只返回其中一半，**把另一半直接丢掉**；前端切换视图时再把整轮计算重做一遍。本次改为：接口一次返回两种装配（`rows` + `groups`），前端切视图只做本地装配、**不再请求后端**。

## 背景与动机

- **切换的等待全是白等**：实测 `_restock_payload` 重算（DB 取数 + 每 SKU×8 城循环算销速/建议量）耗时 **227.9ms**，而序列化 + 传输 + 框架开销合计仅 ~21ms——**重算占整请求耗时 100%**。切一次视图 = 重算一遍完全相同的结果。
- **两种装配本来就同源**：`city_groups` 的行是 `rows` 的子集（同一批 dict，只过滤 `qty > 0` 并重排），后端注释自己写明"两个视图共用同一份计算，只是装配方式不同"。既然同源，就没有理由分两次请求。
- **切换期间无任何提示**：`RestockPanel.vue` 的加载提示条件是 `v-if="loading && !data"`，而切换时旧数据仍在（`data` 非 null），`!data` 恒假 → 提示**永远不出现**。用户盯着旧表格等一个黑盒。
- **前端已就位**：两种装配的数据早已一次性到达前端，`view` 只是个本地布尔值——切视图本就不需要网络往返。

## Considered Options

- **维持现状（切视图重拉）**：每切一次浪费 228ms 重算 + 一次往返，且需靠遮罩掩盖。
- **后端给 `_restock_payload` 加内存缓存（按 shop/date/keyword/days/safety/window 键）**：命中缓存可省掉 228ms 重算，**但仍留一次网络往返**，且引入"缓存何时失效"这个新的 bug 面（参数 / 店铺 / 日期一变就要考虑失效，漏了就是显示旧数）。
- **一次取全 + 客户端装配（选定）**：接口恒返回两种装配，前端 `view` 切换只切 `computed`/模板分支。**零网络、零重算**，且不引入任何缓存失效逻辑——所有计算仍只在 `_restock_payload` 一处发生，SSOT 不动。
- **预取两种视图各存一份（两个请求）**：本质是"选定的方案"用两次请求实现，比一次给全多一次往返，不取。

## Consequences

- **接口契约变更（有意）**：`/api/inventory/restock` **移除 `view` 查询参数**，响应恒含 `rows`（平铺长表）+ `groups`（城市分组）+ `params` + `grand_total` + `sku_count`；`RestockCityResponse` / `RestockSkuResponse` 两个模型合并为 `RestockListModel`。旧的 `?view=sku` 请求不会报错（FastAPI 忽略未知查询参数），只是被忽略——**不会 422，也不会打穿**。
- **响应体积**：从 66KB(raw) / 7.3KB(gzip) 涨到 858KB / 46.3KB（比 `view=sku` 现状只多 **+8% / +16%**——因为 `groups` 只是 `rows` 的子集，JSON 无法引用，重复了一小部分）。首屏请求 228ms → 271ms（+43ms，多序列化 1562 行）；**换来每次切换 228ms → 0ms**。
- **前后端类型同步**：`types.ts` 的 `RestockResponse` / `RestockSkuResponse` 合并为 `RestockListResponse`；`api.ts` 的 `fetchRestockList` 去掉 `view` 参数；`RestockPanel.vue` 删除 `watch(view) → load()`，`empty` 状态改 `computed`（旧写法只认响应里的 `view` 字段，切视图不会重判），模板改用**本地 `view` ref** 决定渲染哪张表。
- **⚠️ 部署必须重启后端**：托盘服务不热重载。新前端（不发 `view`、读 `data.rows`）+ 旧后端（只返回 `groups`）= SKU 视图直接空表/报错。**改后端必须重启服务**，否则页面会坏。
- **遗留：SKU 视图 1562 行的 DOM 构建成本 ~1s**（真实浏览器实测，切到 SKU 视图 1035ms，与网络无关——此时已是零请求）。这是大表格本身的成本，本次未解决。真要根治只能上**虚拟滚动**（只渲染可视区的行），把 1s 压到几十毫秒。
- **踩过的坑：别用 `v-show` 常驻大表**。试过让 SKU 表切走时不卸载（`v-show`）以求"第二次切换 0ms"——实测**反而更慢**：1562 行的表只要还挂在 DOM 里，浏览器每次重排都要为 3.9 万个元素重算样式，把「切回城市视图」从 157ms 拖到 391ms。已回退为 `v-if`（切走即卸载），并把这条结论写进代码注释防止被"优化"回去。
- **顺手修的真问题**：补货面板的缩略图**漏了 `loading="lazy"`**（矩阵表 `InventoryMatrixTable.vue` 一直有）——SKU 视图会同时抢 1562 张图。已补 `loading="lazy" decoding="async"`。
- **验证方式**：
  - 后端：`backend/tests/test_inventory_api.py` 105 条全绿（新增 `test_response_carries_both_assemblies` 锁住"响应必须同时带两种装配"这条契约；`test_two_views_are_the_same_calculation` 改为在**单次响应内**断言两装配同源）；真导入 `python -c "from backend.app.main import app"`。
  - 前端：`npm run build`（含 `vue-tsc` 类型检查）+ `npm run verify`（67 项）+ **真浏览器验收**（`frontend/.scratch/restock-view-switch/verify_view_switch.mjs`，用项目内 `playwright` 驱动系统 Edge）：13 项全绿，含「切视图零网络请求」「切回城市视图 151ms」「参数变化时遮罩出现并消失」「控制台无报错」。
  - 测量脚本：`.scratch/restock-view-switch/measure.py`（体积与耗时构成）。
