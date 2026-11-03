# 技术栈分层裁决：脚手架用 fullstack-vf，数据层不用 ORM，前端组件库用 Element Plus

本文裁决三处技术选型冲突。背景：项目要与 `fullstack-vf` 脚手架技能（`~/.workbuddy/skills/fullstack-vf/`）对齐，以支持「用 AI 快速搭出同类项目并到处复用」的目标；同时引入 Element Plus 作为前端组件库。

## 背景与动机

- **脚手架技能的默认栈与本项目实际栈不一致**：`fullstack-vf` 默认 `SQLModel + Alembic + PostgreSQL` + `Element Plus` + `openapi-typescript`。本项目改造前的实际栈是「手写 SQL + SQLite 单文件」+ 无组件库 + 手工对齐前端类型。两套并存会让 AI 在新项目里左右为难——这正是架构手册 §7.3 警告的「决策残留」。
  （背景段记录的是**引入 Element Plus 之前**的快照；本文 §决策·二 已裁决引入 EP 2.14.7 并经收敛层接入。读本文做新项目选型时以 §决策 段为准。）
- **ORM 与长表设计冲突**：本项目的事实表刻意只存可累加原始列（比率查询时算，见手册 §3.2），摄入路径是「整表重算 + 幂等 upsert」。ORM 在此处的抽象（对象-关系映射、增量更新、级联）不但用不上，反而会让人忍不住把派生比率写进表里——那正是手册标注的统计正确性命根子。
- **组件库空白有真实代价**：项目手写 72 个 `<button>`、20 个 `<input>/<select>`，其中 `DateRangePicker.vue` 单文件 1033 行（自建日历 + 月份面板 + 年份跳转）。通用交互是组件库的主场，这部分自研属于重复造轮子。

## 决策

### 一、分层裁决：脚手架管通用骨架，本项目 Skill 管数据层

`fullstack-vf` 负责**通用骨架**（目录结构、CORS 装配、路由守卫、Pinia setup store、typegen 流水线），本项目的架构 Skill 负责**数据层与铁律**（长表 + 手写 SQL + 幂等 upsert），并提供三个闸门脚本——对应手册两处 🔴 致命项（§2.1 静态挂载顺序、§3.2 比率不入库）加一条新增的可观测性下限（裸 print）。

**数据层不用 ORM**：不引入 SQLModel 的 `table=True` 模型、不引入 Alembic 迁移。事实表 schema 变更走「重建 + 幂等 upsert」而非迁移脚本。

**理由**：
- 本项目的事实表是「每次跑批整表重算」，不是「线上业务表增量演进」。迁移脚本解决的是「生产库不能停机改结构」的问题，而这里的事实表随时可重建——重建比迁移更简单也更安全。
- ORM 会诱导把派生值写进表（`conversion_rate` 这类），直接违反手册 §3.2 的「比率不入库」铁律。**保持手写 SQL，是这条铁律的结构性保障**，不是省事。
- `fullstack-vf` 的 SQLModel 在此处仍有一半价值：**用它的 Pydantic 模型做 API 请求/响应 schema**（校验 + 序列化一处定义），只是不碰 DB 映射。

**边界**：新项目若要支持「线上业务表 + 多用户写入 + 结构演进」，应重新裁决——那时 Alembic 是必需的。本裁决的适用前提是「批处理重建事实表」。

### 二、前端组件库：Element Plus，但必须经收敛层

引入 Element Plus 2.14.7，但**业务组件不直接写 `el-*`**，一律经 `frontend/src/components/ui/` 的语义包装组件（`AppButton` / `AppNumberInput` / `AppSelect` / `AppTag` / `AppCheckbox`）。

**理由**：
- Element Plus 自带 `--el-*` 主题变量。若放任它用默认值，页面会同时存在「本项目令牌」与「EP 默认值」两套颜色源——正是手册 §6.2 反对的「多处各写一份」。故在 `style.css` 建立**单向映射层**：`--el-color-primary: var(--color-brand)`，使「改一个 `--color-brand` 即全站（含 EP 组件）生效」。
- 字号缩放（`--font-scale`，手册 §1.1）必须作用到 EP 组件上，否则用户调字号时自研控件变了、EP 控件不变，页面出现半大半小。故 EP 的字号变量也写成 `calc(Npx * var(--font-scale))`。
- 语义包装层让「EP 是实现细节」这条边界变硬：EP 换版本、换库时业务组件不动。

**EP 替代不了的部分（保留自研）**：
- 三张核心表格（模块矩阵 / 库存矩阵 / 补货清单）是原生 `<table>` + `position: sticky` 多列冻结 + 虚拟滚动。EP 的 `el-table` 不支持虚拟滚动，`el-table-v2` 是另一套 API（需重写 row render）。**这三张表不迁移**。
- `DateRangePicker.vue` 的月份网格视图 + 「跳到最新/最早数据年」快捷跳转，`el-date-picker` 的 `daterange` 覆盖不了。**暂不迁移**；后续若月份面板退化为纯日期区间，可换用 `el-date-picker` + `el-popover`。
- `MonthCompareTable.vue` 是 CSS Grid（非 table）布局，EP 无对应组件。**不迁移**。
- `MetricIcon.vue`（手写 SVG 图标）/ `CountUp.vue`（滚动计数）是业务专属，EP 无对应。**不迁移**。

### 三、删掉 `GenericTable.vue`（零引用死代码）

`frontend/src/components/GenericTable.vue`（337 行）**全仓零引用**——只有 `utils/virtual-list.ts` 的一句注释提到它。已删除。

**理由**：它用 div 网格 + `translateY` 绝对定位实现虚拟滚动，与现存三张表用的「占位行」方案冲突（后者保住原生表格列宽布局，见 `virtual-list.ts` 顶部注释）。留着它等于给后人一个「表格可以这么写」的错误样板。相关注释已同步修正。

## Considered Options

- **全量引入 Element Plus 并迁移三张核心表格**：❌ 拒绝。虚拟滚动 + 多列 sticky 冻结在 EP 中无等价物，迁移要么放弃虚拟滚动（十万行卡死），要么用 `el-table-v2` 重写全部渲染逻辑（收益远低于成本）。这与手册 §1.7「大数据量列表必抄分页 + 虚拟滚动」相悖。
- **不用组件库，继续自研全部控件**：❌ 拒绝。72 个手写 button、20 个手写 input/select，每个都要自己实现焦点态、禁用态、键盘可达性——通用交互是组件库最成熟的部分，自研纯属重复造轮子。
- **用 Naive UI 替代 Element Plus**：❌ 拒绝（2026-11-03 评估）。Naive UI 的 Vue3 原生与 TS 完整体验确实更现代，但本项目已按用户决定采用 Element Plus，且 `fullstack-vf` 脚手架默认也是 Element Plus——**保持与脚手架一致**的价值高于组件库的细微差异（两者都能通过 `--el-*` / `--n-*` 变量映射到本项目令牌）。
- **组件库 + 散落的 `el-*` 直用**：❌ 拒绝。那会让「按钮尺寸/圆角」的规格散落到几十个文件，正是本次要消除的漂移源。收敛层 + `verify:ep` 闸门是唯一可维护的形态。
- **ORM（SQLModel + Alembic）**：❌ 拒绝，理由见上。

## Consequences

- **新增机器闸门**：`frontend/verify_element_plus.mjs`（`npm run verify:ep`）把三条约定变成断言——① 业务组件不直接写 `el-*` 标签；② 业务组件不直引 `--el-*` 变量；③ `--el-*` 的唯一定义处是 `style.css` 映射层。已并入 `npm run verify` 链条。
- **校验脚本扩容一处**：`verify_text_tokens.mjs` 新增「深底声明」机制（`ON_DARK_BG` 表）。此前 AI 头像上的 `#a5f3fc`（压在深蓝渐变底上）被按白底算对比度而误判为「太浅」——这是**改动前就存在的既有问题**，本次顺带修复：新增按真实底色校验的路径，而非放宽阈值。
- **产物体积**：首次引入时 `index.css` 从 5KB 增至 144KB（EP 必要样式）。2026-11-03 清理掉未使用组件的注册与样式引入后，`index.css` 为 91.9KB（gzip 13.6KB）。EP 按需引入已生效——未使用的 carousel/dialog/upload/tree 等样式均未进入产物。
- **新增依赖**：`element-plus@2.14.7`。`@element-plus/icons-vue@2.3.2` 是 EP 自身的传递依赖，项目未直接使用。
- **注册清单已收敛**：只注册 `ElButton` / `ElCheckbox` / `ElConfigProvider` / `ElInputNumber` / `ElOption` / `ElSelect` / `ElTag`，与 `components/ui/` 一一对应。原先为「后续迁移预留」的 `ElTable` / `ElPagination` / `ElTooltip` / `ElPopover` / `ElDatePicker` / `ElIcon` 已移除——按本 ADR 的结论，核心表格与日期选择器不迁移，留着只会成为僵尸依赖。
- **验证方式**：`npm run build`（`vue-tsc -b` 零类型错误）+ `npm run verify`（18860 + 113 + 84 + 99 + 31 + 36 + 14 项断言全绿）。既有断言一条未挂，说明 EP 替换未破坏任何既有行为。

## 维护修订（2026-11-03）

- **修复漏注册**：`AppNumberInput` 使用 `<el-input-number>`，但 `main.ts` 漏注册 `ElInputNumber`、也漏引其样式。Vue 不会在构建期报错，运行时只渲染成未知元素——已补注册与样式。
- **修复失效覆盖**：`AppButton` / `AppTag` 的样式覆盖引用了 EP 2.14 不存在的类名（`.el-button__inner` / `.el-tag__content`），字号缩放实际未生效。改为按 EP 真实类名与变量覆盖；`--el-tag-font-size` 统一在 `style.css` 映射层处理。
- **闸门扩容**：`verify:ep` 新增第 ⑤ 条——收敛层与 `App.vue` 用到的每个 `el-*` 必须在 `main.ts` 注册且有样式引入。所有检查先剥离注释，避免注释里的示例误报。
