# Spec · 推广预算优化分析（/promo）

- **状态**：待拆工单
- **日期**：2026-10-08
- **来源**：13 轮 grilling 决策（见 `CONTEXT.md` 与 `docs/adr/0001~0003`）
- **基线commit**：`289a529`

---

## 1. 目标与非目标

### 目标

回答运营的单一问题：**「总预算不变，这笔钱该怎么分才能让总成交最高？」**

产出三个数字：
1. **理论上限总成交** — 贪心最优解
2. **当前实际总成交**
3. **可优化空间** = 前者− 后者

以及达成率 = 当前 ÷ 理论上限。

⚠️ **反例（2026-10-08 实测补充，勿照字面实现）**：
上面两个公式隐含一个前提 —— **现状本身满足单品集中度上限 30%**，此时「现状」也是贪心的可行解，
公式成立。但真实数据 3 店中 **2 店不满足**（单品吃掉 100% 预算，投产比 6.49）：

```
cap        = 总预算 × 30%        # 把这品的预算削到 30%
理论上限    = cap × 6.49          = 1,600     ← 低于现状
当前实际    = 1000 × 6.49         = 6,490
可优化空间   = 上限 − 当前 = −4,890（负）      达成率 = 405%（> 100%）
```

这不是 bug，是**集中度约束的代价**：为了分散风险，必须接受总成交下降。
⛔ 页面若照字面渲染「可优化空间 −4,890 元」「达成率 405%」，运营会以为算法算错。

**约定**（已实现，后端判定、前端只渲染）：

| | 现状满足 cap | 现状违反 cap |
|---|---|---|
| 卡片标题 | 可优化空间 | 重新分配的成交差额 |
| 数字 | 原值 | **绝对值**（不带负号） |
| 配色 | 绿（收益） | 红（警示） |
| 副标题 | 理论上限 − 当前实际 | 按 30% 上限挪走预算后总成交会下降，这是分散风险的代价 |
| 额外提示 | — | 点名超限 SPU + 说明「现状本身不满足 30% 约束」 |

- 后端 `promo.detect_over_concentrated(allocations, cap)` → `PromoAnalysisOut.over_concentrated: Optional[str]`
- ⛔ **前端不得自行判断 `current > cap`** —— 那是业务口径，两处各写一遍必然漂移
- ⚠️ 达成率进度条必须**夹紧到 100%**（405% 会撑破卡片）

### 非目标（本期明确不做）

- ❌ 跨店对比（各店规模差异过大，横轴不可比）
- ❌ 拆分全站推广 / 非全站推广（源表已汇合，数据不可分）
- ❌ 记录推广执行历史（操作真实发生在京准通后台，本工具只读）
- ❌ 真实的边际 ROI 分析（需反事实数据，平台不提供）
- ❌ 手动拖拽模拟器

---

## 2. 术语纪律（硬约束）

| 禁止 | 必须用 | 原因 |
|---|---|---|
| ❌ 边际ROI | ✅ **投产比**（ROI） | 边际 ROI 需反事实数据，本项目算不出。`roi` 字段已存在，语义即投产比 |
| ❌ 盈亏 / 亏损 | ✅ 投产比< 1 的品 | 无毛利字段，无法判断盈亏 |
| ❌ 预测 / 预计达成 | ✅ 理论上限 / 保守估计 | 线性假设下不可达成，见 ADR-0002 |

**代码层面**：新字段、新接口、新文案均不得出现 `marginal` 字样。

---

## 3. 数据来源与口径

### 数据源

复用现有长表 `店铺 × SPU × 日期`，字段：
- `promotion_cost` — 推广花费
- `promotion_amount` — 推广成交金额（源表「总订单金额」= 推广带来的成交，全站+非全站已汇合）
- 逐日数据用于波动校验

### 口径纪律（项目铁律）

比率类一律**先求和再相除**：

```python
total_cost = Σ promotion_cost# 分子
total_amt  = Σ promotion_amount     # 分母
roi = total_amt / total_cost        # 分母 0 → None，前端显示 --
```

⛔ 禁止「先把每日 ROI 平均」。必须复用 `transforms.compute_metrics` 的既有口径，与 `frontend/src/metrics.ts:aggregateMetrics` 保持一致。

### 区间语义

**最近 30 天滚动窗口**，用于下一个预算周期。页面须显式标注此语义。

---

## 4. 算法

### 4.1 贪心优化（核心）

```
输入：某店铺、区间内各 SPU 的 cost(Σ花费) 与 roi(Σ成交/Σ花费)
  1. 按 roi 从高到低降序排序
  2. budget = Σ该店区间总花费          # 零和：总预算不变
  3. cap = budget × 30%# 单品集中度上限
  4. 依序分配：给该 SPU 分配 min(剩余预算, cap)
     → 预期成交 += 分配额 × roi
     → 剩余 -= 分配额；分配满 cap 则跳过
  5. 直到 budget 用尽
输出：每个 SPU 的建议分配额、理论上限总成交
```

**约束**：单品预算上限 **30%**（Q 决策，可配置为常量）。

### 4.2 回测折扣

```
理论上限总成交 → 标注「线性假设下的数学最优」
保守估计     = 理论上限 × 折扣系数
```

- 折扣系数第一期为**经验值 0.7**，须在代码中定义成常量 `BACKTEST_DISCOUNT`，并注释说明待替换为回测公式
- 回测算法（第二期实现）：取历史区间，假装不知其分配 → 按该区间真实花费与 roi 算贪心 → 与该区间实际总成交相比 → 比值即系数
- 回测**不需要人工记录**

### 4.3 波动校验

按逐日 roi 计算**变异系数** `CV = stddev / mean`，CV 超过阈值的 SPU 标注「不稳定，建议小步试投」。

不影响主算法（仍用区间汇总值决策），仅作为建议强度的修饰。

### 4.4 四象限（辅助视图）

- 横轴 `cost`（推广花费）、纵轴 `roi`（投产比）、气泡大小 `promotion_amount`
- 分档阈值用**分位数**（P50/P75 可切），不用固定值
- 四档：核心利润品 / 潜力股 / 吞金兽 / 试水品
- ⚠️ **仅用于解释算法为何这样分配，不是决策依据**

---

## 5. 页面结构

### 5.1 路由与入口

- 路由：`/promo`（顶级，与「模块」平级）
- **入口**：模块页工具条链接 → **不进侧边栏**（侧边栏是 manifest 数据驱动的 `v-for`，功能页无法接入）

### 5.2 两种模式

**看板模式**（默认）
- 工具条：日期区间（复用 `DateRangePicker`）+ 店铺选择 + 刷新
- 四象限散点图
- SPU 效率表（可排序）
- 三数字卡片：理论上限 / 当前实际 / 可优化空间

⚠️ 「可优化空间」卡片在 `over_concentrated` 非空时**必须换标题与配色**（见 §1 反例表）——
「负的可优化空间」不是收益也不是 bug，是约束的成本，措辞必须说清。

**优化模式**（点按钮展开）
- 分配方案表：当前分配 vs 建议分配 vs 差额
- 保守估计（理论上限 × 0.7）
- 达成率
- ⚠️ 大促期警示

### 5.3 图表

echarts 散点图，扩展现有 `GenericChart.vue`（新增 quadrant 预设），不引入新图表库。

---

## 6. API 设计

挂在现有 `api_router`（前缀 `/api`）上，**无需改 `main.py`**。

```
GET /api/promo/analysis?shop=&start=&end=
```

**响应模型**（Pydantic，进 `openapi.json` 自动文档）：

```python
class PromoSpu(BaseModel):
    spu: str
    cost: float              # Σ推广花费
    amount: float# Σ推广成交金额
    roi: float | None        # Σamount/Σcost，分母0→None
    profit: float            # Σamount - Σcost
    cost_share: float | None # 该店该日总花费为分母
    amount_share: float | None
    roi_cv: float | None          # 逐日 roi 变异系数
    unstable: bool
    suggested_cost: float         # 贪心建议分配额
    current_cost: float
    delta: float                  # suggested - current
    quadrant: str                  # core|potential|loser|trial
    advice: str                   # 建议文案

class PromoAnalysisOut(BaseModel):
    shop: str
    start: str
    end: str
    total_cost: float
    total_amount: float
    current_total_amount: float   # 当前分配下的总成交
    ideal_total_amount: float     # 贪心理论上限
    conservative_total: float# ideal × 0.7
    gap: float                    # ideal - current
    achievement_rate: float | None # current / ideal
    spus: list[PromoSpu]
    empty_reason: str | None      # 见 §7
```

⚠️ **新端点必须显式写 `response_model=`** —— 现有 7 个路由里有 3 个漏了，导致 `/docs` 里响应体空��。不要重复这个疏漏。

---

## 7. 空态策略（后端判定，前端只渲染）

后端算清空态原因，前端不重复判断逻辑：

| `empty_reason` | 触发条件 | 前端表现 |
|---|---|---|
| `no_data` | 该店区间无推广数据 | 「该店铺在此区间无推广数据」+ 提示换区间 |
| `too_few_spus` | 有推广数据的 SPU < 4 个 | 「数据量不足，无法分档优化」（+ 当前值仍可展示） |
| `all_zero` | 有 SPU 但全部 `cost` = 0 | 「全部推广花费为 0，无法计算投产比」 |

**注意**：`too_few_spus` 时贪心优化仍可执行（1 个 SPU 时结果就是它自己），仅四象限与分位数失效。前端应展示当前值 + 弱化四象限，而非全页报错。

---

## 8. 免责文案（硬要求，不可省）

三处必须出现，缺一不可：

1. **保本线说明**：投产比 < 1 的分界「未考虑商品毛利」，投产比 1.2 的品在毛利 20% 时可能实际亏损
2. **线性假设**：理论上限是「数学最优，非可达目标」
3. **大促风险**：「大促期流量结构会变，请勿直接照搬本方案」

---

## 9. 实施约束（复用既有资产）

| 复用 | 说明 |
|---|---|
| `api_router` | 前缀 `/api` 已设，新端点直接挂载 |
| `transforms.compute_metrics` | 口径唯一来源，禁自写聚合 |
| `DateRangePicker.vue` | 日期区间，勿重写 |
| `GenericTable.vue` | 表格，右侧 sticky 固定列 |
| `GenericChart.vue` | 新增 quadrant 预设，不引入新图表库 |
| `metrics.ts` 新增派生指标 | 须同步后端 `models.py` 字段 + `api.py:METRIC_KEYS`（项目既有踩坑） |
| design token | 全部用 `--color-*` / `--radius-*`，勿写死 hex |

**新增指标清单**：`promo_profit`（净收益）、`promo_cost_share`（花费占比）、`promo_amount_share`（成交占比）。
⚠️ `cost_share` 的分母是**该店当日全部 SPU 花费和**，须按「店+日」分组求和后再跨日**除以天数**——这是最易写错的一处。
⚠️ **只做到「跨日累加」是错的**（2026-10-08 实测修正）：裸累加的量纲是「天数倍」，67 天区间得到 `14.75`（= 1475%），前端无法当占比展示。实测 `Σ日占比 ÷ 67 = 0.2202`，与区间总花费占比 `0.2134` 同量级，验证了「除以天数」这一步。

---

## 10. 验收标准

- [ ] `/api/promo/analysis` 在 `/docs` 里有完整响应 Schema（无空白响应体）
- [ ] 比率类全部「先求和再相除」，与 `metrics.ts:aggregateMetrics` 数值一致
- [ ] 贪心分配后 `Σsuggested_cost == total_cost`（零和守恒）
- [ ] 无任何 SPU 的 `suggested_cost > total_cost × 0.3`
- [ ] `cost = 0` 的 SPU 不进排序、不产生除零
- [ ] 三种 `empty_reason` 各自触发正确文案，无 JS 报错
- [ ] 页面出现 §8 的三处免责文案
- [ ] 代码/文案中无 `marginal` 字样
- [ ] `npm run build` 通过（`vue-tsc -b && vite build`）
- [ ] `verify_promo.mjs` 断言覆盖：口径、零和守恒、集中度上限、空态分支