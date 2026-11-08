# 技术栈分层裁决：脚手架用 fullstack-vf，数据层不用 ORM，前端组件库用 Element Plus

> **编号说明（2026-11-06 合并时补记）**：本文在 `feature/element-plus-unification` 分支上原编号 `0019`；合入 `main` 时与先一步提交的 `0019-ops-daily-baseline.md` 撞号。按「先进入 `main` 者保留编号」顺延为 `0020`，文件名同步调整；分支历史里的旧编号不再作为引用依据。

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

引入 Element Plus 2.14.7，但**业务组件不直接写 `el-*`**，一律经 `frontend/src/components/ui/` 的语义包装组件（`AppButton` / `AppNumberInput` / `AppSelect` / `AppTag` / `AppCheckbox` / `AppDialog`）。

**理由**：
- Element Plus 自带 `--el-*` 主题变量。若放任它用默认值，页面会同时存在「本项目令牌」与「EP 默认值」两套颜色源——正是手册 §6.2 反对的「多处各写一份」。故在 `style.css` 建立**单向映射层**：`--el-color-primary: var(--color-brand)`，使「改一个 `--color-brand` 即全站（含 EP 组件）生效」。
- 字号缩放（`--font-scale`，手册 §1.1）必须作用到 EP 组件上，否则用户调字号时自研控件变了、EP 控件不变，页面出现半大半小。故 EP 的字号变量也写成 `calc(Npx * var(--font-scale))`。
- 语义包装层让「EP 是实现细节」这条边界变硬：EP 换版本、换库时业务组件不动。

**EP 替代不了的部分（保留自研）**：
- 三张核心表格（模块矩阵 / 库存矩阵 / 补货清单）是原生 `<table>` + `position: sticky` 多列冻结 + 虚拟滚动。EP 的 `el-table` 不支持虚拟滚动，`el-table-v2` 是另一套 API（需重写 row render）。**这三张表不迁移**。
- `DateRangePicker.vue` 的月份网格视图 + 「跳到最新/最早数据年」快捷跳转，`el-date-picker` 的 `daterange` 覆盖不了。**暂不迁移**；后续若月份面板退化为纯日期区间，可换用 `el-date-picker` + `el-popover`。
- `MonthCompareTable.vue` 是 CSS Grid（非 table）布局，EP 无对应组件。**不迁移**。
- `MetricIcon.vue`（手写 SVG 图标）/ `CountUp.vue`（滚动计数）是业务专属，EP 无对应。**不迁移**。

**自建弹层已收敛到 `el-dialog`**：`SettingsControl.vue`（设置面板）与 `RestockPanel.vue`（销速算法说明）原先各自实现 `Teleport` + 遮罩 + Esc 监听 + 关闭按钮。现统一经 `AppDialog` 包装 `el-dialog`，由 EP 提供焦点陷阱、滚动锁、遮罩关闭与 `destroy-on-close`；两处手写壳层样式（`.set-overlay` / `.rp-modal-box` 等）随之删除。业务侧保留原有 `data-testid`，既有端到端断言不受影响。

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

- **新增机器闸门**：`frontend/verify_element_plus.mjs`（`npm run verify:ep`）把约定变成断言——① 业务组件不直接写 `el-*` 标签；② 业务组件不直引 `--el-*` 变量；③ `--el-*` 的唯一定义处是 `style.css` 映射层；④ 收敛层必须导出全部语义组件；⑤ 收敛层与 `App.vue` 用到的 `el-*` 必须已注册且有样式引入。已并入 `npm run verify` 链条。
- **校验脚本扩容一处**：`verify_text_tokens.mjs` 新增「深底声明」机制（`ON_DARK_BG` 表）。此前 AI 头像上的 `#a5f3fc`（压在深蓝渐变底上）被按白底算对比度而误判为「太浅」——这是**改动前就存在的既有问题**，本次顺带修复：新增按真实底色校验的路径，而非放宽阈值。
- **产物体积**：首次引入时 `index.css` 从 5KB 增至 144KB（EP 必要样式）。2026-11-03 清理未使用组件的注册与样式、并迁入 `el-dialog` 后，`index.css` 为 97.07KB（gzip 14.66KB）。EP 按需引入已生效——未使用的 carousel/upload/tree 等样式均未进入产物。
- **新增依赖**：`element-plus@2.14.7`。`@element-plus/icons-vue@2.3.2` 是 EP 自身的传递依赖，项目未直接使用。
- **注册清单已收敛**：只注册 `ElButton` / `ElCheckbox` / `ElConfigProvider` / `ElDialog` / `ElInputNumber` / `ElOption` / `ElSelect` / `ElTag`，与 `components/ui/` 一一对应。原先为「后续迁移预留」的 `ElTable` / `ElPagination` / `ElTooltip` / `ElPopover` / `ElDatePicker` / `ElIcon` 已移除——按本 ADR 的结论，核心表格与日期选择器不迁移，留着只会成为僵尸依赖。
- **验证方式**：`npm run build`（`vue-tsc -b` 零类型错误）+ `npm run verify`（10 + 18853 + 138 + 84 + 99 + 31 + 36 + 14 = 19265 项断言全绿）。既有断言一条未挂，说明 EP 替换未破坏任何既有行为。

## 维护修订（2026-11-03）

- **修复漏注册**：`AppNumberInput` 使用 `<el-input-number>`，但 `main.ts` 漏注册 `ElInputNumber`、也漏引其样式。Vue 不会在构建期报错，运行时只渲染成未知元素——已补注册与样式。
- **修复失效覆盖**：`AppButton` / `AppTag` 的样式覆盖引用了 EP 2.14 不存在的类名（`.el-button__inner` / `.el-tag__content`），字号缩放实际未生效。改为按 EP 真实类名与变量覆盖；`--el-tag-font-size` 统一在 `style.css` 映射层处理。
- **闸门扩容**：`verify:ep` 新增第 ⑤ 条——收敛层与 `App.vue` 用到的每个 `el-*` 必须在 `main.ts` 注册且有样式引入。所有检查先剥离注释，避免注释里的示例误报。
- **修复令牌映射被 EP 覆盖（关键）**：`style.css` 的 `--el-*` 映射原写在 `:root`，与 EP `base.css` 的 `:root` 同特异性；EP 样式后加载，导致映射整体失效（实测 primary 仍是 EP 默认 `#409eff`、font-base 仍是 `14px`、overlay 仍是 `#00000080`）。改为 `:root:root`（特异性 0,2,0）后，实测 primary 恢复 `#e1251b`、font-base 为 `calc(13px * var(--font-scale))`、overlay 为项目令牌。**这是映射层能否生效的前提，不是可选优化**。
- **弹层迁移到 `AppDialog`**：`SettingsControl.vue` 与 `RestockPanel.vue` 的自建遮罩层改为经 `AppDialog` 包装 `el-dialog`。因为 `el-dialog` 被 teleport 到 `body`，`AppDialog` 内对其外壳（`.el-dialog` / `__header` / `__body`）的样式必须用 `:global()` 才能命中；直接写在 scoped 样式里会静默失效。此前两处 modal 的 Esc 监听、滚动锁、遮罩点击关闭均由 EP 接管，业务组件删除对应手写逻辑。
- **已知遗留**：`RestockPanel.vue` 中 `.rp-param input, .rp-param select` 的尺寸规则在参数区改用 `AppNumberInput` / `AppSelect` 后不再命中，参数区高度由 EP 组件自身尺寸决定。若后续需要精确复刻原 16px/34px 参数栏，应在 `AppNumberInput` / `AppSelect` 上加尺寸变体，而不是回写裸选择器。

## 维护修订（2026-11-08）：日期**筛选控件**局部迁移，收敛层扩至 7 个组件

- **决策的部分修订**：上文 Consequences 写的「核心表格与日期选择器不迁移」**只修订其中一半**。2026-11-08 用户看完五项静态预览后拍板 ①B，把店铺运营数据页 `OpsDailyView.vue` 筛选栏的「起始日期 + 结束日期」两个原生 `<input type="date">` 合并成一个整体控件，选型为 EP `el-date-picker type="daterange"`。
- **边界（必须守住）**：迁移的是**一个筛选栏区间输入**，不是核心表格。`ElTable` / `ElPagination` / `ElTooltip` / `ElPopover` / `ElIcon` **仍不注册、不迁移**；指标矩阵继续用原生 `<table>` + sticky + `v-memo`，每页 50 行的性能契约不变。原结论对「核心表格」依旧完全成立。
- **为什么改口**：① 两个独立日期框在视觉与交互上是「两个控件」，用户明确要「一个整体」；② EP daterange 在一个日历弹层内框选起止、天然保证起 ≤ 止，省掉原先 `:min="from"` / `:max="to"` 的双向联动；③ 项目已有收敛层与 `verify:ep` 机器闸门，新增一个语义包装的边际成本远低于自研双日历。评审时已提示过 ①B 会撞 `verify:ep` 的 `el-*` 黑名单，解法不是加例外，而是**把控件放进收敛层**（这正是该闸门设计的目的）。
- **落地**：新建 `components/ui/AppDateRangePicker.vue`（第 7 个语义包装），业务页只认 `AppDateRangePicker` 与 `[from, to] | null` 字符串数组，用 `dateRange` computed 桥接既有 `from` / `to` ref，避免为换控件把 `baseQuery` / `previousRange` / 导出文件名一整串改成数组下标；`main.ts` 注册 `ElDatePicker` + 引入 `date-picker/style/css`；`verify:ep` ④ 导出检查数组 6 → 7，⑤ 自动校验注册与样式（实测 149/0）。
- **代价（如实登记）**：新增懒加载 chunk `DateRangePicker-*.js` 18.75 kB（gzip 7.35 kB）+ 同名 CSS 9.81 kB（gzip 1.87 kB），不进首屏包。这是「一个筛选控件换一个整体交互」付出的体积，接受并监控。
- **验证**：`npm run build` ✅、`npm run verify` 全链 ✅、真浏览器（8014 隔离 DB + Edge CDP）**16/16**，含「全区间把新日期推回控件」与「换日期后真的重查数据」两条交互断言。详见 `.scratch/ops-daily/SPEC.md` v1.5 §12 与工单 08。

## 维护修订（2026-11-08 16:45，D9）：筛选栏第二个控件迁移，收敛层 `AppSelect` 增强

- **边界不变**：核心表格仍不迁移（`ElTable` / `ElPagination` / `ElTooltip` / `ElPopover` / `ElIcon` 仍不注册）；指标矩阵继续原生 `<table>` + sticky + `v-memo`，每页 50 行性能契约不变。本次迁移的是**筛选栏的第二个控件**——店铺下拉从原生 `<select>` 换成收敛层 `AppSelect`（EP `el-select`）。
- **动机**：17 家店名字相近，原生 `<option>` 无法可靠加分组/徽章/底色（原生 `option` 的背景色渲染受浏览器与系统主题影响，本次未做跨浏览器实测，故不以此作为唯一依据），也无法搜索。收敛层是「EP 的唯一合法引用点」，增强它而不是在业务页直写 `el-*`，正是 11-08 ①B 补记里确立的解法。
- **落地与兼容**：`AppSelectOption` 新增**全部可选**的 `group` / `groupLabel` / `badge` / `tone`，props 新增可选 `filterable` / `noMatchText`；内部 `groups` computed 在无任何分组时返回 `null`，保持旧扁平渲染 ⇒ 库存页 `RestockPanel` 的参数下拉 **0 行为变化**。新增 `ElOptionGroup` 注册与 `option-group/style/css` 样式引入，由 `verify:ep` ⑤ 自动校验（149 → **151/0**）。
- **⚠️ teleport 带来的令牌位置修正**：下拉面板 teleport 到 `body`，既拿不到 `.ops-daily` 的局部 CSS 变量，也不受组件 scoped 样式管辖。故「明显底色」的 6 个语义令牌**新增到全局 `style.css :root`**（⛔ 只新增、不改任何既有值，全站品牌色零变化），选项内容样式用非 scoped `<style>` + `popper-class="app-select-popper"` 前缀限定，⛔ 不定义 `--el-*` 变量。这是对静态预览「只在 ops 页局部定义」措辞的如实修正，已登记工单 10 与 SPEC §14；⚠️ **D10（18:1x）后这组令牌只在鼠标悬停时铺整行**，常驻改为白底，见下节。
- **验证**：`npm run build` ✅（2292 modules）；`npm run verify` 全链 ✅；真浏览器 CDP **15/15**（滚动吸顶、分组与明显底色实测、搜索、换店铺真重查、点外部收起回归、控制台 0 错误）。详见工单 10。

## 维护修订（2026-11-08 18:1x，D10）：teleport 面板的「变量作用域 + hover 层级」

- **触发**：用户拿实测截图对比方案预览，指出常驻底色与预览不符且「鼠标移入店铺都没有提示」。根因不是配色，而是**把类型底色铺在 `el-option` 内部 div**：EP 的 hover 高亮加在父 `<li class="el-select-dropdown__item">` 上，被子层不透明底色完全遮住 ⇒ 静态断言与 `grep` 都发现不了，只能靠真浏览器 + 真实鼠标位移。
- **两条可复用的收敛层纪律**（本次新增，适用于所有 teleport 到 `body` 的 EP 浮层）：
  1. **要染整行，class 与变量必须挂在 `li` 上**：`el-option` 的 `class` 会透传到 `<li>`；CSS 自定义属性只向下继承，**父元素读不到子元素变量**，故 `--as-*` 定义在 `.as-host-*`（li）而非内部 div，色条 / 徽章靠继承取同一组值。
  2. **hover 规则的特定性必须压过 EP 自带 `.el-select-dropdown__item:hover` 的浅灰**（用 `.app-select-popper .el-select-dropdown__item.as-host:hover`，⛔ 不用 `!important`），否则「移入有提示」会静默退化成灰色而非语义色。
- **结果**：常驻改回预览的白底（色条 + 文字徽章仍带类型色，色弱 / 黑白打印可读），明显底色只在 hover 时铺整行；未知 tone 用 `var(--as-bg, var(--color-surface-2))` 降级中性浅灰，字典驱动 0 改码。库存页扁平 options 无 tone ⇒ 0 影响。
- **验证**：真浏览器 CDP **18/18**（`Input.dispatchMouseEvent` 的 `mouseMoved` 触发真实伪类状态，⛔ `Runtime.evaluate` 改不了 `:hover`）+ 常驻 / 自营 hover / POP hover 三态截图人工比对；`npm run verify` 全链 ✅（tokens 21362/0、ep 151/0）。详见 SPEC v1.8 §15 与工单 11。
