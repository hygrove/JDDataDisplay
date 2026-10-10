# -*- coding: utf-8 -*-
"""推广预算优化：贪心分配、回测折扣、达成率与缺口。

⚠️ 术语纪律（见 CONTEXT.md 与 docs/spec/promo-budget-optimization.md §2）：
   本模块**不存在**「边际 ROI」这个概念。边际 ROI 需要反事实数据（关掉某个 SPU 的
   推广会怎样），京准通不提供这种数据，本项目也算不出来。这里用的是**投产比**
   （ROI = 推广成交金额 ÷ 推广花费），它只回答「每元换回多少」，不能回答
   「多给一元能多赚多少」。因此所有输出用「理论上限 / 保守估计」，不用「预测」。

本模块是**纯函数集合**：无 IO、无全局可变状态、不读配置，可直接单测。
数据加载与聚合在 api.py 完成，本文件只接收已算好的数字。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import NamedTuple, Optional


# ---------------- 可调参数（具名常量，禁止散落魔法数字） ----------------

# 单品预算集中度上限：任一 SPU 的建议分配额 ≤ 总预算 × 此值。
# 30% 来自 13 轮 grilling 的 Q5 决策：要「建议分散」还是「允许集中」时选了对冲方案
# ——既不允许多个 SPU 各占 1/3，也不强求绝对平均。
CAP_RATIO: float = 0.30

# 回测折扣：保守估计 = 理论上限 × 此系数。
# ⚠️ **经验值，不是回测结果**。线性假设（加预算成交按比例放大）在高预算段不成立，
#    贪心解会把预算全压到少数几个高投产比的品上，放大效应明显衰减。
#    第二期用真实历史区间回测替换：取某历史区间 → 假装不知其分配 → 按该区间真实
#    cost/roi 跑贪心 → 与该区间实际总成交相比 → 比值即系数（详见 ADR-0002）。
BACKTEST_DISCOUNT: float = 0.70

# 波动校验的两档阈值。
#
# ⚠️ **为什么不用单一绝对阈值**：实测本项目真实数据（3 店 10 个 SPU、67 天），
#    逐日投产比的 CV 落在 0.456 ~ 2.438 之间，中位数约 0.65。若用绝对阈值 0.5，
#    会有 **8/10 个 SPU 被判为不稳定** —— 标签对 80% 的商品都是「不稳定」，
#    等于没有筛选力，也会让「波动较大」这句话在页面上失去意义。
#
# 改成**店内的相对分位**后，判据变成「比本店多数品更不稳」：
#    - 每家店稳定地只有约 30% 的品被标为不稳定，阈值含义不随店铺漂移；
#    - A 店整体波动大、B 店整体波动小时，两店的标注意图都合理。
#
# ⚠️ 这仍不是统计显著性检验，只是「店内相对波动排名」。若将来要回答
#    「这家店的波动是否异常」，需另引入店铺自身的历史 CV 基线做对比。
CV_ABSOLUTE_THRESHOLD: float = 1.0    # 兜底：CV 低于此值一律判稳定（店整体平稳时不被硬标）
CV_RELATIVE_QUANTILE: float = 0.70  # 店内 CV 的 P70：超过者判为不稳定
# 店内超过绝对阈值的品少于此数时，无法做「相对排名」，改为直接按绝对阈值判定。
# ⚠️ 少了这个分支会出现反直觉结果：只有1 个品超阈值时，它的 P70 分位线就是它自己，
#    「超过分位线」恒为 False —— 明明是店内最不稳的品，却被判为稳定。
CV_MIN_SAMPLES_FOR_RANKING: int = 3

# 四象限分档的分位数阈值。0.5 即中位数；改成 0.75 则分档更严（只有头部 25% 算高）。
# ⚠️ 用分位数而非固定值：固定 ROI 阈值跨店铺会失真——
#    A 店整体 ROI 高（所有品 ROI 都 > 3），B 店整体 ROI 低（都 < 1），
#    同一把尺子会把 A 店所有品判成「低投产比」，分档失去区分度。
QUADRANT_QUANTILE: float = 0.50

# 分档可信所需的最少品数。低于此数时分位数由个别样本决定，分档不可靠。
# ⚠️ 该阈值与 CAP_RATIO 推出的 ceil(1/cap_ratio)=4 数值相同但**含义不同**：
#    这里是「样本量不足以定分位数」，那里是「预算填不满」。两者碰巧相等，不要混用理由。
MIN_SPU_FOR_QUADRANT: int = 4


# 四象限分档的枚举值（前端按此渲染标签与颜色，不要在前端另写中文映射）
QUADRANT_CORE = "core"          # 核心利润品：高投产比 · 高花费
QUADRANT_POTENTIAL = "potential"  # 潜力股：高投产比 · 低花费
QUADRANT_LOSER = "loser"        # 吞金兽：低投产比 · 高花费
QUADRANT_TRIAL = "trial"        # 试水品：低投产比 · 低花费


@dataclass(frozen=True)
class PromoInput:
    """贪心算法的输入单元：一个 SPU 在某区间的推广汇总值。

    ⚠️ frozen=True：算法不应修改调用方的数据。误改会污染上层的原始聚合结果。

    Attributes:
        spu: SPU 编号。
        cost: 区间内 Σ推广花费（元）。
        roi: 区间投产比 = Σ推广成交金额 ÷ Σ推广花费；分母为 0 时为 None。
    """

    spu: str
    cost: float
    roi: Optional[float]


@dataclass(frozen=True)
class PromoAllocation:
    """贪心算法对单个 SPU 给出的分配结果。

    Attributes:
        spu: SPU 编号（与输入一一对应，包括被跳过的）。
        current_cost: 当前分配额（= 输入的 cost，用于算差额）。
        suggested_cost: 建议分配额。
        expected_amount: 按建议分配额推算的推广成交金额
            = suggested_cost × roi；roi 为 None 时为 None。
        skipped: True 表示因 cost ≤ 0 或 roi 为 None 而未参与分配。
        skip_reason: 跳过原因（未跳过时为 None），供 API 直接透传给前端。
    """

    spu: str
    current_cost: float
    suggested_cost: float
    expected_amount: Optional[float]
    skipped: bool = False
    skip_reason: Optional[str] = None


@dataclass(frozen=True)
class AllocationResult:
    """一次贪心分配的完整结果。

    Attributes:
        allocations: 每个 SPU 的分配结果（含被跳过的）。
        total_budget: 本次分配的总预算（= 输入中全部 cost 之和，零和）。
        cap: 单品分配上限（= total_budget × CAP_RATIO）。
        ideal_total_amount: 理论上限总成交 = Σ 各 SPU 的 expected_amount。
        conservative_total: 保守估计 = ideal_total_amount × BACKTEST_DISCOUNT。
        unallocated: 因单品上限封顶而**分不出去**的剩余预算。
            正常情况下 SPU 数足够多时为 0；SPU 太少时 > 0
            （如只有 1 个 SPU，它最多拿 30%，另外 70% 无处可去）。
    """

    allocations: list[PromoAllocation]
    total_budget: float
    cap: float
    ideal_total_amount: float
    conservative_total: float
    unallocated: float


def greedy_allocate(
    items: list[PromoInput],
    cap_ratio: float = CAP_RATIO,
    discount: float = BACKTEST_DISCOUNT,
) -> AllocationResult:
    """按投产比降序贪心分配推广预算（零和：总预算不变）。

    为什么要贪心而不是别的：
      目标函数是「Σ(分配额 × 各品投产比)」，这是一个**线性背包问题**。
      线性背包的贪心解在数学上就是最优解（把预算全给投产比最高的品），
      所以不需要动态规划。这里唯一的额外约束是「单品不能超过总预算的 cap_ratio」，
      它只是给贪心解套了个上界，处理方式仍是「依次填满、封顶就跳过」。

    算法步骤：
      1. 过滤 cost ≤ 0 或 roi 为 None 的 SPU（不分配、不参与排序）；
      2. 剩余按 roi 从高到低排序；
      3. 预算 = Σ 全部输入的 cost（**含被跳过的**——它们的花费仍是店里花出去的钱，
         不能凭空消失，否则「零和」对不上账）；
      4. 依序分配 min(剩余预算, cap)，累计预期成交 += 分配额 × roi；
      5. 预算用尽或全部分完为止。

    ⚠️ 步骤 3 的「含被跳过的」是最容易写错的一处：
       若预算只算参与分配的 SPU，roi 为 None 的品（通常是无推广或数据缺失）
       原有的花费就被踢出总额，总预算变小、建议分配额整体缩水，
       页面上会出现「建议总分配 < 实际总花费」的诡异现象。

    Args:
        items: 各 SPU 的推广汇总值；不必预先排序，函数内部会排。
        cap_ratio: 单品分配上限比例，默认 CAP_RATIO（0.30）。
        discount: 回测折扣，默认 BACKTEST_DISCOUNT（0.70）。

    Returns:
        AllocationResult: 完整分配结果；items 为空时各项均为 0 且 allocations 为空列表，
            不抛异常（前端空态由 api.py 判定 empty_reason 负责）。

    Raises:
        ValueError: cap_ratio 不在 (0, 1] 内时抛出——0 会让所有 SPU 分不到钱，
            >1 等于没有约束，两者都属调用方配置错误，应尽早暴露。

    Example:
        >>> r = greedy_allocate([PromoInput("A", 100.0, 5.0), PromoInput("B", 100.0, 2.0)])
        >>> r.total_budget, r.cap
        (200.0, 60.0)
        >>> [a.suggested_cost for a in r.allocations]
        [60.0, 60.0]
        >>> round(r.ideal_total_amount, 1)
        420.0
    """
    if not (0 < cap_ratio <= 1):
        raise ValueError(f"cap_ratio 必须在 (0, 1] 区间内，收到 {cap_ratio}")

    # 预算基数 = 全部输入的 cost 之和（含被跳过的），理由见 docstring 步骤 3
    total_budget = sum(it.cost for it in items)
    cap = total_budget * cap_ratio

    # 只给「有推广投入且投产比可算」的 SPU 分配；其余原样保留在结果里并标注原因
    candidates = [it for it in items if it.cost > 0 and it.roi is not None]
    skipped = {it.spu: _skip_reason(it) for it in items if it.cost <= 0 or it.roi is None}
    # 降序排 roi。⚠️ 这里 roi 已过滤掉 None，排序不会因 None 崩溃
    candidates.sort(key=lambda it: it.roi, reverse=True)  # type: ignore[arg-type]

    remaining = total_budget
    expected_total = 0.0
    results: list[PromoAllocation] = []
    for it in candidates:
        # 封顶后可能只剩 0 额度；此时给 0 而不是跳过，让前端能显示「建议 0 元」
        give = min(remaining, cap)
        if give < 0:  # 浮点累积误差理论上不该为负，兜底避免分出负数金额
            give = 0.0
        expected = give * it.roi if it.roi is not None else None  # type: ignore[operator]
        remaining -= give
        expected_total += expected or 0.0
        results.append(
            PromoAllocation(
                spu=it.spu,
                current_cost=it.cost,
                suggested_cost=give,
                expected_amount=expected,
            )
        )

    # 把跳过的 SPU 补回结果（建议额 0），保证返回值与输入**一一对应**，
    # 前端无需再按 spu 去 join 就能逐行渲染
    for it in items:
        if it.spu in skipped:
            results.append(
                PromoAllocation(
                    spu=it.spu,
                    current_cost=it.cost,
                    suggested_cost=0.0,
                    expected_amount=None,
                    skipped=True,
                    skip_reason=skipped[it.spu],
                )
            )

    return AllocationResult(
        allocations=results,
        total_budget=total_budget,
        cap=cap,
        ideal_total_amount=expected_total,
        conservative_total=expected_total * discount,
        # 浮点兜底：分配完后残下的可能是 -1e-13，负数会让前端显示成「-0.00」
        unallocated=max(0.0, remaining),
    )


def _skip_reason(it: PromoInput) -> str:
    """给出被跳过 SPU 的原因文案（供 API 透传，前端直接展示）。"""
    if it.cost <= 0:
        return "无推广花费"
    if it.roi is None:
        return "投产比无法计算"
    return "未知原因"


def compute_gap(
    current_total_amount: float, ideal_total_amount: float
) -> tuple[float, Optional[float]]:
    """计算「可优化空间」与「达成率」。

    ⚠️ **两者的取值范围都可能超出直觉，但这不是 bug，而是 cap 约束的数学后果**：
       当现状把大部分预算压在少数高投产比 SPU 上时（单品占比 > `CAP_RATIO`），
       **现状本身违反了集中度约束、不是贪心的可行解**，贪心必须把钱挪走，
       于是 ideal < current → gap 为负、达成率 > 1。
       真实数据 3 店中有 2 店命中（SPU 数 < 4 时尤其明显）。
       ⛔ 调用方**不要假定 gap >= 0**，须用 `detect_over_concentrated` 判定后按不同语义展示。

    Args:
        current_total_amount: 当前分配下的总推广成交金额。
        ideal_total_amount: 贪心理论上限总成交。

    Returns:
        tuple[float, Optional[float]]: (gap, achievement_rate)，
            gap = ideal − current；achievement_rate = current ÷ ideal，
            分母为 0 时返回 None（前端显示 `--`，而非 Infinity/NaN）。

    Raises:
        无。

    Example:
        >>> compute_gap(80.0, 100.0)
        (20.0, 0.8)
        >>> compute_gap(3334.62, 1600.39)  # 现状违反 cap 时会出现
        (-1734.2299999999998, 2.083629615281275)
    """
    gap = ideal_total_amount - current_total_amount
    rate = (current_total_amount / ideal_total_amount) if ideal_total_amount else None
    return gap, rate


def detect_over_concentrated(allocations: list[PromoAllocation], cap: float) -> Optional[str]:
    """判定「现状是否已违反单品集中度上限」，返回超限 SPU 的可读标识。

    为什么要单独判定：贪心解是**在 cap 约束下的最优**，而现状可能压根不在这个可行域里。
    这时 ideal < current、gap 为负、达成率 > 1，页面若照字面渲染成
    「可优化空间 −1,735 元」「达成率 208%」，运营会以为算法算错了。
    实际含义是「现状过度集中，按 30% 上限重新分配后总成交会下降」——
    这是**约束的成本**，不是收益，必须显式说清而不是藏起来。

    Args:
        allocations: 贪心分配结果（含每个 SPU 的 current_cost 与 suggested_cost）。
        cap: 单品预算上限（= total_cost × CAP_RATIO）。

    Returns:
        Optional[str]: 超限 SPU 的编号（多个时拼成可读列表）；无超限时返回 None。

    Example:
        >>> detect_over_concentrated([PromoAllocation("A", 1000.0, 300.0, 900.0, False, None)], 300.0)
        'A'
        >>> detect_over_concentrated([PromoAllocation("A", 250.0, 300.0, 900.0, False, None)], 300.0)
    """
    # 贪心对超 cap 的品一定只给到 cap（必须是 roi 最高的一批才会被削），
    # 所以「current > cap」就是「现状违反约束」的充分标志。
    over = [a.spu for a in allocations if a.current_cost > cap + 1e-6]
    if not over:
        return None
    if len(over) == 1:
        return over[0]
    # 多个品都超：拼成可读列表（最多列 5 个，避免文案过长）
    head = "、".join(over[:5])
    tail = f" 等 {len(over)} 个" if len(over) > 5 else ""
    return head + tail


# ---------------- 分位数（波动校验与四象限分档共用） ----------------
# ⚠️ 抽成独立函数而不是各写一遍：两个判据都用分位数，
#    插值方式不一致会导致「波动阈值」与「分档阈值」在同一天算出不同结果，
#    页面上的两个结论互相矛盾却找不到原因。

def _quantile(sorted_values: list[float], q: float) -> float:
    """线性插值分位数（与 numpy.percentile 默认 'linear' 方法一致）。

    为什么不直接用排序后取中间值：
      偶数个样本时「取中间」有两种约定（偏左/偏右），会让恰好落在阈值上的品
      归到不同档，用户看到「同样的数据换个店就变档」很难排查。
      线性插值规则唯一且连续。

    Args:
        sorted_values: 已升序排好的数值列表；不可为空。
        q: 分位数，取值 [0, 1]。

    Returns:
        float: 该分位数对应的值。

    Raises:
        ValueError: 列表为空或 q 不在 [0, 1] 时抛出。

    Example:
        >>> _quantile([1.0, 2.0, 3.0, 4.0], 0.5)   # 偶数个取中间两值均值
        2.5
        >>> _quantile([1.0, 2.0, 3.0], 0.5)          # 奇数个取正中
        2.0
    """
    if not sorted_values:
        raise ValueError("分位数不可对空序列计算")
    if not 0 <= q <= 1:
        raise ValueError(f"q 必须在 [0, 1] 内，收到 {q}")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[int(pos)]
    # 在相邻两值之间按小数部分线性插值
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


# ---------------- 波动校验（辅助信息，不影响主算法） ----------------
class DailyRoi(NamedTuple):
    """某 SPU 在某一日的推广汇总（仅波动校验所需的两项）。

    为什么不让 CV 直接收 floatroi：
      单日的投产比可能**无意义**——当日花费为 0 时 `amount/cost` 是除零，
      直接当 0 参与统计会凭空拉低均值、把正常品误判为不稳定。
      收 (cost, roi) 让「跳过不可用样本」这件事留在函数内部，调用方不必重复判断。
    """

    cost: float
    roi: Optional[float]


def compute_roi_cv(daily: list[DailyRoi]) -> Optional[float]:
    """计算逐日投产比的变异系数 CV（只算值，不做判定）。

    CV = 总体标准差 ÷ 均值，衡量**相对**波动幅度。
    用相对量而非标准差本身：不同 SPU 的 ROI 量级差异极大
    （有的品日 ROI 在 0.1 附近，有的在 8 附近），标准差直接横向比没有意义。

    ⚠️ 用**总体标准差**（除以 n）而非样本标准差（除以 n−1）：
      区间通常只有 30~60 天，n 很小，样本标准差会系统性高估 CV，
      结果是几乎所有品都被标成「不稳定」，这条校验就失去筛选作用。

    Args:
        daily: 该 SPU 的逐日 (花费, 投产比) 序列；
            cost <= 0 或 roi 为 None 的日会被跳过（当日投产比无意义，
            当成 0 参与统计会凭空拉低均值、把正常品误判为不稳定）。

    Returns:
        Optional[float]: CV 值；无法计算时返回 None，具体情形：
            - 有效样本 < 2 个（单点算不出标准差）
            - 均值 <= 0（投放无回报或数据异常，此时「相对波动」没有意义）
        返回 None 只代表**算不出**，不代表「稳定」或「不稳定」。

    Raises:
        无。

    Example:
        >>> compute_roi_cv([DailyRoi(10.0, 2.0)] * 5)
        0.0
        >>> compute_roi_cv([]) is None
        True
        >>> round(compute_roi_cv([DailyRoi(10.0, 1.0), DailyRoi(10.0, 3.0)]), 6)
        0.5
    """
    valid = [d.roi for d in daily if d.cost > 0 and d.roi is not None]
    if len(valid) < 2:
        return None
    mean = sum(valid) / len(valid)
    if mean <= 0:
        return None
    var = sum((r - mean) ** 2 for r in valid) / len(valid)
    return math.sqrt(var) / mean


def mark_unstable(
    cvs: list[Optional[float]],
    absolute_threshold: float = CV_ABSOLUTE_THRESHOLD,
    q: float = CV_RELATIVE_QUANTILE,
    min_samples: int = CV_MIN_SAMPLES_FOR_RANKING,
) -> list[bool]:
    """按「绝对兜底 + 店内相对分位」两档规则，标记哪些 SPU 波动过大。

    判定顺序（两级）：
      1. **CV 算不出来** → 一律 False。「算不出」不等于「不稳定」，
         否则所有无数据的品都会背上莫须有的警告。
      2. **CV < absolute_threshold** → False。即使它是店内最不稳的那个，
         绝对波动已经很小时标成「不稳定」纯属噪声。
      3. 剩下的按 **店内 CV 的 P70 分位**判定：超过分位线者才标 True。

    第3 步为什么要分位而不是固定值：见CV_ABSOLUTE_THRESHOLD 上方注释
    （真实数据上固定阈值会把 80% 的品标成不稳定）。

    Args:
        cvs: 各 SPU 的 CV 值（compute_roi_cv 的输出），含 None。
        absolute_threshold: 绝对兜底阈值，默认 CV_ABSOLUTE_THRESHOLD（1.0）。
        q: 店内相对分位，默认 CV_RELATIVE_QUANTILE（0.70）。
        min_samples: 做相对排名所需的最少候选数，默认 CV_MIN_SAMPLES_FOR_RANKING（3）；
            低于此数时退回「超绝对阈值即不稳」。

    Returns:
        list[bool]: 与 cvs 等长同序的 unstable 标记。

    Raises:
        ValueError: q 不在 [0, 1] 时抛出。

    Example:
        >>> mark_unstable([0.1, 0.2, 2.0, None])   # 仅 1 个候选 → 退回绝对阈值
        [False, False, True, False]
        >>> mark_unstable([1.2, 1.5, 1.8])         # 3 个候选 → 按 P70 分位
        [False, False, True]
        >>> mark_unstable([1.0, 1.0, 1.0])         # P70=1.0，无人「超过」分位线
        [False, False, False]
    """
    if not 0 <= q <= 1:
        raise ValueError(f"q 必须在 [0, 1] 内，收到 {q}")

    candidates = [c for c in cvs if c is not None and c >= absolute_threshold]
    if not candidates:
        return [False for _ in cvs]
    # 候选太少时分位数没有区分度（1 个候选的 P70 就是它自己），
    # 此时「超绝对阈值」本身就是唯一可用的信号，直接采用它。
    if len(candidates) < min_samples:
        return [bool(c is not None and c >= absolute_threshold) for c in cvs]
    thr = _quantile(sorted(candidates), q)
    # 阈值取分位线本身：恰好等于分位线者判稳定（严格「超过」才算不稳）
    return [bool(c is not None and c > thr) for c in cvs]


# ---------------- 四象限分档（辅助解释视图，非决策依据） ----------------


def compute_quadrant_thresholds(
    items: list[PromoInput],
    q: float = QUADRANT_QUANTILE,
) -> tuple[Optional[float], Optional[float]]:
    """算出四象限的两条分割线位置（投产比阈值、花费阈值）。

    ⚠️ **为什么必须由后端算、而不是让前端从 spus 重算一遍**：
       分割线是「分档结论的图示」。前端若自己实现分位数（哪怕公式看起来一样），
       一旦插值约定或样本筛选规则与后端有细微差异，就会出现
       「点被染成核心高效，但它明明在分割线下方」的自相矛盾画面，
       而且这种错误只在特定数据下偶发，极难排查。
       与 compute_metrics / metrics.ts:aggregateMetrics 同一纪律：
       **口径单一事实来源在��端，前端只渲染**。

    ⚠️ 阈值只用roi 不为 None 的品计算（与 classify_quadrant 保持一致）：
       无投产比的品若参与，会把阈值拉向 0，让所有正常品都落进「高投产比」档。

    ⚠️ 返回 (None, None) 而非抛异常：无可分档样本是**正常业务状态**
       （店铺没有推广花费），端点要照常返回 200 + empty_reason，不该 500。

    Args:
        items: 各 SPU 的推广汇总值（只读，不修改）。
        q: 分位数，默认 QUADRANT_QUANTILE（0.5 = 中位数）。

    Returns:
        tuple[Optional[float], Optional[float]]: (roi 阈值, cost 阈值)；
            无可分档样本时返回 (None, None)。

    Example:
        >>> items = [PromoInput("A", 100.0, 5.0), PromoInput("B", 100.0, 1.0),
        ...          PromoInput("C", 10.0, 4.0), PromoInput("D", 10.0, 0.5)]
        >>> compute_quadrant_thresholds(items)
        (2.5, 55.0)
        >>> compute_quadrant_thresholds([PromoInput("A", 0.0, None)])
        (None, None)
    """
    usable = [it for it in items if it.roi is not None]
    if not usable:
        return (None, None)
    roi_thr = _quantile(sorted(it.roi for it in usable), q)  # type: ignore[misc]
    cost_thr = _quantile(sorted(it.cost for it in usable), q)
    return (roi_thr, cost_thr)


def classify_quadrant(
    items: list[PromoInput],
    q: float = QUADRANT_QUANTILE,
) -> dict[str, str]:
    """按「投产比 × 花费规模」把 SPU 分到四象限。

    ⚠️ 阈值取**分位数**而非固定值：固定 ROI 阈值跨店铺会失真——
      A 店所有品 ROI 都 > 3、B 店所有品都 < 1，同一把尺子会把 A 店整体
      判成「低投产比」，分档就失去了区分度。分位数是**店内相对位置**，
      天然适配跨店比较。

    ⚠️ 分位数只用「roi 不为None 的品」计算：无投产比的品若参与，会把阈值
      拉向 0，让所有正常品都落进「高投产比」档。

    边界约定：**恰好等于阈值归入高档**（`>=` 而非 `>`）。
      这样 P50 上恰好有一个品时，它属于「高效的一半」，符合直觉。

    ⚠️ 品数 < MIN_SPU_FOR_QUADRANT 时分位数由个别样本决定，结果**不可信**。
      本函数照样返回分档（调用方可通过 `len(items)` 判断是否弱化展示），
      但**不要**基于这个分档做资金决策——它只是辅助解释视图。

    Args:
        items: 各 SPU 的推广汇总值（只读，不修改）。
        q: 分位数，默认 QUADRANT_QUANTILE（0.5 = 中位数）。

    Returns:
        dict[str, str]: spu → quadrant 枚举值（core / potential / loser / trial）；
            roi 为 None 的品不出现在结果里（调用方按「无法分档」处理）。
            items 为空时返回空字典。

    Raises:
        ValueError: q 不在 [0, 1] 时抛出（由 _quantile 抛出）。

    Example:
        >>> items = [PromoInput("A", 100.0, 5.0), PromoInput("B", 100.0, 1.0),
        ...          PromoInput("C", 10.0, 4.0), PromoInput("D", 10.0, 0.5)]
        >>> classify_quadrant(items)
        {'A': 'core', 'B': 'loser', 'C': 'potential', 'D': 'trial'}
    """
    # 只有 roi 可算的品参与分档与阈值计算
    usable = [it for it in items if it.roi is not None]
    if not usable:
        return {}

    # ⚠️ 阈值统一由 compute_quadrant_thresholds 计算（不重算一遍）：
    #    前端要在这两条线上画分割线，两处各算一次必然漂移。
    roi_thr, cost_thr = compute_quadrant_thresholds(usable, q)

    out: dict[str, str] = {}
    for it in usable:
        # 阈值相等时归高档（>=）
        hi_roi = it.roi >= roi_thr  # type: ignore[operator]
        hi_cost = it.cost >= cost_thr
        if hi_roi and hi_cost:
            out[it.spu] = QUADRANT_CORE
        elif hi_roi:
            out[it.spu] = QUADRANT_POTENTIAL
        elif hi_cost:
            out[it.spu] = QUADRANT_LOSER
        else:
            out[it.spu] = QUADRANT_TRIAL
    return out


def build_advice(
    quadrant: Optional[str],
    roi: Optional[float],
    unstable: bool = False,
    delta: Optional[float] = None,
) -> str:
    """生成一句 SPU 级建议文案。

    ⚠️ 文案受spec §2 术语硬约束：不得出现「边际」「预计达成」「亏损」等词。
       实际用的是「投产比」「建议」，因为项目算不出边际回报，也判断不了盈亏
       （数据源不含毛利）。

    ⚠️ 接收**已判定好的 unstable 标志**（mark_unstable 的输出）而不是 roi_cv 值：
       判定规则若在两处各写一遍，改了一处忘了另一处，
       页面会出现「表格标着不稳定、文案却没提波动」的自相矛盾。

    Args:
        quadrant: 四象限枚举值；None 表示无法分档（roi 不可算）。
        roi: 区间汇总投产比。
        unstable: 是否波动过大（由 mark_unstable 判定）。True 时追加波动提示。
        delta: 建议分配额 − 当前分配额（元）。正=加投、负=减投、None=不加判断。
            给了它，文案才能说清「加多少/减多少」而不只是「高/低」。

    Returns:
        str: 建议文案；无法分档时给出「先有数据再评估」类文案。

    Raises:
        无。

    Example:
        >>> build_advice("core", 5.0, unstable=False, delta=50.0)
        '投产比高于本店中位且花费规模较大。建议增加推广预算约 50 元。'
        >>> build_advice("loser", 0.8, unstable=True, delta=-200.0)
        '投产比低于本店中位且花费规模较大。建议削减推广预算约 200 元。逐日投产比波动较大，建议小步试投并密切观察。'
        >>> build_advice("core", 5.0, delta=-300.0)
        '投产比高于本店中位且花费规模较大。效率较好但已超单品预算上限，建议下调约 300 元以分散预算。'
        >>> build_advice(None, None)
        '该商品无有效推广数据，暂不评估。'
    """
    # 无投产比可算：不硬凑建议，直说数据不足
    if quadrant is None or roi is None:
        return "该商品无有效推广数据，暂不评估。"

    high_roi = quadrant in (QUADRANT_CORE, QUADRANT_POTENTIAL)
    high_cost = quadrant in (QUADRANT_CORE, QUADRANT_LOSER)

    # 主句：投产比 / 花费两个维度的判断
    roi_part = "高于本店中位" if high_roi else "低于本店中位"
    cost_part = "花费规模较大" if high_cost else "花费规模较小"
    parts = [f"投产比{roi_part}且{cost_part}"]

    # 建议动作：优先用 delta 的方向（更具体），退回到分档的默认动作
    #
    # ⚠️ **必须区分「为什么削减」**，否则文案会自相矛盾：
    #    「投产比高于本店中位」+「建议削减预算」看起来像前后打架，
    #    实则是两个不同的原因——效率高但已超单品集中度上限（被压缩），
    #    与效率低（该砍）。混为一谈会让运营误判算法在惩罚好品。
    if delta is None:
        parts.append("建议保持观察" if high_roi else "建议削减推广预算")
    elif delta > 0:
        parts.append(f"建议增加推广预算约 {delta:,.0f} 元")
    elif delta < 0:
        if high_roi:
            #效率好却减投 → 只能是触到单品预算上限
            parts.append(
                f"效率较好但已超单品预算上限，建议下调约 {abs(delta):,.0f} 元以分散预算"
            )
        else:
            parts.append(f"建议削减推广预算约 {abs(delta):,.0f} 元")
    else:
        parts.append("建议维持当前推广预算")

    # 波动提示：仅在不稳定时追加。措辞刻意用「小步试投」而非「不要投」——
    # 波动大只说明历史不稳，不代表未来一定差。
    if unstable:
        parts.append("逐日投产比波动较大，建议小步试投并密切观察")

    return "。".join(parts) + "。"