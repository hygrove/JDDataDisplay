# 前端 UI 地图（UI Map）

> 用途：前端越来越复杂后，用「看得到的中文文案 / 截图」描述要改的元素经常失真，AI 改错位置。
> 本文件把 **路由 → 页面 → 组件 → 关键元素** 逐一登记，并给每个关键元素一个稳定的
> `data-testid` 身份证。以后描述改动只需说 `data-testid="xxx"`，AI 能直接定位，不再靠猜。

---

## 0. 怎么用这份地图

1. **描述改动时直接给 testid**：「把 `module-toolbar` 里的搜索框占位文案改一下」比
   「上面工具栏那个搜索框」精确得多，AI 能 `grep` 到唯一位置。
2. **在浏览器里验证某个元素在哪**：
   ```js
   // DevTools Console 直接查
   document.querySelector('[data-testid="module-spu-grid"]')
   // 查一类（如所有指标行）
   document.querySelectorAll('[data-testid="module-metric-row"]')
   ```
3. **截图沟通时**：右键元素 → 检查 → 在 Elements 面板里看它的 `data-testid` 属性，
   把那个值贴给 AI 即可。
4. **新增元素时**：若它是一个「对用户有意义 / 以后可能要改」的区块，顺手加一个
   `data-testid`（命名见第 4 节），并回来更新本文件对应表格。

---

## 1. 路由 → 页面

| 路由（router.ts） | 页面组件 | 中文名 | 说明 |
| --- | --- | --- | --- |
| `/` | `App.vue` 主页（home） | 模块列表 / 落地页 | 左侧导航 + 右主区骨架，无独立业务内容 |
| `/module/:moduleId` | `views/ModuleView.vue` | 模块单品页 | 单日网格卡 / 区间整行矩阵 |
| `/module/:moduleId/spu/:spu` | `views/SpuAnalysisView.vue` | 单品分析页 | 指标卡 + 趋势图 + 工作日vs节假日 + 综合分析 + 月份对比 |

> 三路由全部为 SPA（history 模式），由 `App.vue` 左导航切换，主区 `#app-main` 渲染对应视图。

---

## 2. 全局骨架（App.vue）

| data-testid | 元素 / 位置 | 中文标签 | 备注 |
| --- | --- | --- | --- |
| `app-shell` | 最外层 `.layout` | 应用整体容器 | 左右两栏布局根 |
| `app-sider` | 左侧 `<aside>` | 左侧导航栏 | 业务模块 + 店铺两级导航 |
| `app-nav-modules` | 业务模块导航 `.nav-section` | 业务模块导航 | 一级（模块列表） |
| `app-nav-shops` | 店铺导航 `.nav-section.grow`（有 moduleId 时才显示） | 店铺导航 | 二级（当前模块下的店铺），`v-if` 条件渲染 |
| `app-main` | 右侧 `<main>` | 主内容区 | 所有页面视图挂载点 |
| `app-loading-overlay` | 路由级加载遮罩 `.nav-loading`（`v-if="ui.navigating"`） | 路由切换加载遮罩 | 仅导航过程中短暂出现 |

---

## 3. 模块单品页（ModuleView.vue）

共享组件：`MetricConfigPanel`（指标配置）、`DateRangePicker`（日期范围）。

| data-testid | 元素 / 位置 | 中文标签 | 备注 |
| --- | --- | --- | --- |
| `module-toolbar` | 顶部工具条 `.toolbar` | 工具条 | 日期范围 + 搜索 + 状态 + 指标配置 + 刷新/导出 |
| `module-search` | 搜索输入框 `.search` | 搜索框 | 按 SPU 号 / 商品名过滤，400ms 防抖 |
| `module-refresh-btn` | 手动刷新按钮（红） | 手动刷新 | 重新拉批处理最新数据 |
| `module-export-btn` | 导出按钮（深灰） | 导出数据 | 导出 xlsx（全量、与页面同口径） |
| `module-spu-grid` | 单日模式容器 `.spu-grid` | 单品网格 | 一个 SPU 一张卡（`!isRange` 时显示） |
| `module-spu-card` | 单品卡 `.spu-card`（v-for） | 单品卡 | 一张卡 = 一个 SPU；多张同名 testid，用 `querySelectorAll` 取 |
| `module-metric-row` | 卡内指标行 `.metric-row`（v-for） | 指标行 | 单日模式下每个指标一行（名 + 值） |
| `module-spu-rows` | 区间模式容器 `.spu-rows` | 单品整行列表 | 每个 SPU 占一整行（`isRange` 时显示） |
| `module-spu-row` | 单品行 `.spu-row`（v-for） | 单品行 | 左侧固定商品信息 + 右侧指标×日期矩阵 |
| `module-spu-matrix` | 指标矩阵表 `.spu-matrix` | 指标矩阵 | 表头=日期列，首列=指标名，缺失显示 `--` |
| `module-empty-state` | 空态 `.empty-state`（两处共用） | 空态提示 | 加载中 / 无数据两种分支，同名 testid |

> 指标卡 / 明细列展示哪些指标，由 `stores/metrics.ts` 持久化（与单品分析页共用）。

---

## 4. 单品分析页（SpuAnalysisView.vue）

共享组件：`DateRangePicker`、`MetricConfigPanel`、`MonthCompareTable`、`GenericChart`。

| data-testid | 元素 / 位置 | 中文标签 | 备注 |
| --- | --- | --- | --- |
| `spu-topbar` | 顶部条 `.topbar` | 顶部条 | 返回 + 日期范围 + 指标配置 |
| `spu-back-btn` | 返回按钮 `.back-btn` | 返回列表 | 回到模块页 |
| `spu-hero` | 商品信息头 `.hero` | 商品信息头 | 图片 + 名称 + SPU + 店铺 + 类目 + 数据区间 |
| `spu-cards` | 指标卡区 `.cards` | 指标卡区 | 单日=单值；区间=求和+平均值两块 |
| `spu-insight` | 综合分析 `.insight` | 综合分析 | 后端生成的文字结论 |
| `spu-charts` | 趋势图区 `.charts` | 趋势图区 | 3 张双轴/单轴 echarts（含推广占比警戒线） |
| `spu-compare-box` | 工作日vs节假日 `.compare-box` | 工作日vs节假日 | 4 项固定对比（金额/转化率/推广占比/ROI）纵向条形图 |
| `spu-month-compare` | 月份对比块（见下，组件根） | 月份对比 | 默认收起，点开展开多月份横向对比 |

---

## 5. 月份对比（MonthCompareTable.vue）

嵌在单品分析页 `spu-month-compare` 块内，自身整块就是这个 testid。

| data-testid | 元素 / 位置 | 中文标签 | 备注 |
| --- | --- | --- | --- |
| `spu-month-compare` | 组件根 `.mcompare` | 月份对比块 | 折叠头 + 月份 chips + 对比表 + 时间轴大图 |

> 细节（折叠头、月份 chips、展开时间轴等）目前未细分 testid；如需精确到某行/某月，
> 后续可在 `mc-head` / `mc-chip` / `mc-exp` 上补锚点并回写本表。

---

## 6. 指标配置面板（MetricConfigPanel.vue）

作为子组件出现在 `module-toolbar` 与 `spu-topbar` 内，两处共用同一份配置。

| data-testid | 元素 / 位置 | 中文标签 | 备注 |
| --- | --- | --- | --- |
| `metric-config` | 组件根 `.mc` | 指标配置（容器） | 按钮 + 下拉面板整体 |
| `metric-config-btn` | 触发按钮 `.mc-btn` | 指标配置按钮 | 显示「已选 n/总数」 |
| `metric-config-panel` | 下拉面板 `.mc-panel`（`v-if="open"`） | 指标配置面板 | 展开后才存在 |
| `metric-config-group` | 分组 `.mc-group`（v-for） | 指标分组 | 明细 / 推广 / 退款等 |
| `metric-config-item` | 勾选项 `.mc-item`（v-for） | 指标勾选项 | 一个复选框 = 一个指标；多项目同名 testid |

---

## 7. 日期范围选择器（DateRangePicker.vue）

出现在 `module-toolbar` 与 `spu-topbar` 内。

| data-testid | 元素 / 位置 | 中文标签 | 备注 |
| --- | --- | --- | --- |
| `date-range` | 组件根 `.drp` | 日期范围（容器） | 触发按钮 + 快捷按钮组 |
| `date-range-trigger` | 触发按钮 `.drp-trigger` | 日期范围触发按钮 | 显示「起 ~ 止」或「全区间」 |
| `date-range-quick` | 快捷按钮组 `.drp-quick` | 快捷按钮组 | 近一周 / 当月 / 上月 / 月份 |
| `date-range-month-panel` | 月份面板 `.drp-mpop`（`v-if="monthOpen"`） | 月份面板 | 翻年 + 月份网格，无数据月份置灰 |
| `date-range-calendar` | 日历面板 `.drp-pop`（`v-if="open"`） | 日历面板 | 双月视图，点起点→终点自动应用 |

> 两个面板（日历 / 月份）互斥，由 `utils/overlay.ts` 全局唯一展开。

---

## 8. testid 命名约定

- 格式：`页面或组件前缀` + `-` + `语义`，全小写、连字符分隔，例如 `module-spu-grid`。
- 前缀对应关系：
  - `app-*` → 全局骨架（App.vue）
  - `module-*` → 模块单品页（ModuleView）
  - `spu-*` → 单品分析页（SpuAnalysisView / MonthCompareTable）
  - `metric-config*` → 指标配置面板
  - `date-range*` → 日期范围选择器
- **v-for 列表项用同一 testid 命名整类**（如 `module-spu-card`、`metric-config-item`），
  定位单个实例时用 `querySelectorAll(...)[i]` 或配合其内的稳定文案/数据。
- 加锚点零侵入（Vue scoped 下只多一个属性，不影响样式与逻辑），新增有意义区块时务必补。

---

## 9. 同步维护清单

- 前端源码：`frontend/src/{App.vue, views/ModuleView.vue, views/SpuAnalysisView.vue,
  components/MetricConfigPanel.vue, components/DateRangePicker.vue, components/MonthCompareTable.vue}`
- 构建产物验证：`npm run build` 后 `grep "data-testid" dist/assets/*.js` 应能看到全部锚点。
- 本文件与上面的源码**任一改动需同步更新**：新增/重命名 testid 时改第 2~8 节表格。
