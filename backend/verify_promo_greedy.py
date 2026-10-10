# -*- coding: utf-8 -*-
"""推广预算优化算法验证（纯逻辑，不依赖数据文件 / 不依赖服务在线）。

与前端 verify_promo.mjs 同构：独立脚本 + 自制断言计数器，不引入 pytest 等新依赖
（项目 requirements.txt 是完整 freeze，加测试框架会破坏「异地复现」的前提）。

运行：``.venv\\Scripts\\python backend\\verify_promo_greedy.py``

覆盖（工单 02）：
  - 零和守恒：Σ suggested_cost + unallocated == total_budget（浮点容差内）
  - 集中度上限：任何 SPU 的 suggested_cost ≤ total_budget × cap_ratio
  - 单调性：投产比更高的 SPU 分配额 ≥ 更低的
  - 跳过规则：cost ≤ 0 或 roi 为 None 的品不分配、不参与排序，但仍计入预算基数
  - 边界：空输入 / 单个 SPU / 全部投产比相同 / cap 约束填不满预算 / 负投产比
  - 双数字：conservative_total == ideal × BACKTEST_DISCOUNT
  - 缺口与达成率：分母 0 时返回 None 而非 Infinity
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

# 允许直接 ``python backend/verify_promo_greedy.py`` 运行（把项目根塞进 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.promo import (  # noqa: E402
    BACKTEST_DISCOUNT,
    CAP_RATIO,
    MIN_SPU_FOR_QUADRANT,
    QUADRANT_CORE,
    QUADRANT_LOSER,
    QUADRANT_POTENTIAL,
    QUADRANT_TRIAL,
    CV_ABSOLUTE_THRESHOLD,
    CV_MIN_SAMPLES_FOR_RANKING,
    DailyRoi,
    PromoInput,
    build_advice,
    classify_quadrant,
    compute_gap,
    compute_quadrant_thresholds,
    detect_over_concentrated,
    compute_roi_cv,
    greedy_allocate,
    mark_unstable,
)

# 浮点容差：金额加总有累积误差，1e-6 元足够严格又不脆弱
EPS = 1e-6

_pass = 0
_fail = 0


def ok(cond: bool, label: str) -> None:
    """记录一条断言结果；失败时打印上下文，便于定位。"""
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print(f"FAIL {label}")


def near(actual: float, expected: float, label: str, eps: float = EPS) -> None:
    """浮点近似断言。"""
    ok(abs(actual - expected) <= eps, f"{label}（期望 {expected}，实际 {actual}）")


def eq(actual, expected, label: str) -> None:
    """严格相等断言。"""
    ok(actual == expected, f"{label}（期望 {expected!r}，实际 {actual!r}）")


def mk(spu: str, cost: float, roi) -> PromoInput:
    """构造 PromoInput 的语法糖。"""
    return PromoInput(spu=spu, cost=cost, roi=roi)


def test_basic_greedy() -> None:
    """基础贪心：高投产比优先 + 封顶。"""
    # 3 个品、预算 300、cap 90。各品 cap 都是 90，填满 3×90=270，剩 30 无处可去
    r = greedy_allocate([mk("A", 100.0, 5.0), mk("B", 100.0, 3.0), mk("C", 100.0, 1.0)])
    eq([a.suggested_cost for a in r.allocations], [90.0, 90.0, 90.0], "三个品都封顶 90")
    near(r.total_budget, 300.0, "总预算 = Σcost")
    near(r.cap, 90.0, "cap = 总预算 × 0.30")
    # 理论上限 = 90×5 + 90×3 + 90×1 = 810
    near(r.ideal_total_amount, 810.0, "理论上限 = Σ(分配额 × roi)")
    near(r.conservative_total, 810.0 * BACKTEST_DISCOUNT, "保守估计 = 理论上限 × 折扣")
    near(r.unallocated, 30.0, "封顶导致的未分配预算被显式记录")
    near(sum(a.suggested_cost for a in r.allocations), 270.0, "分配额之和 = 3×cap")


def test_zero_sum() -> None:
    """零和守恒：SPU 数足够（5 个，5×0.3=1.5>1）时预算必须分完。"""
    items = [mk("A", 100.0, 5.0), mk("B", 80.0, 4.0), mk("C", 60.0, 3.0),
             mk("D", 40.0, 2.0), mk("E", 20.0, 1.0)]
    r = greedy_allocate(items)
    near(sum(a.suggested_cost for a in r.allocations), 300.0, "5 个品时零和守恒成立")
    near(r.unallocated, 0.0, "预算分完时 unallocated 为 0")
    eq(len(r.allocations), 5, "分配结果与输入一一对应")
    # cap=90：A→90；剩 210；B→90；剩 120；C→90；剩 30；D→30；剩 0；E→0
    eq([a.suggested_cost for a in r.allocations], [90.0, 90.0, 90.0, 30.0, 0.0],
       "预算耗尽后靠后的品拿 0（不是被跳过）")
    eq(r.allocations[-1].skipped, False, "拿到 0 元的品不算 skipped")


def test_cap_ratio() -> None:
    """集中度上限：逐项校验，不只抽查。"""
    items = [mk("A", 500.0, 9.0), mk("B", 300.0, 5.0), mk("C", 200.0, 3.0)]
    r = greedy_allocate(items)
    for a in r.allocations:
        ok(a.suggested_cost <= r.cap + EPS,
           f"{a.spu} 分配额 {a.suggested_cost} 不得超过 cap {r.cap}")
    # ⚠️ 3 个品 × 0.30 = 0.9 < 1，cap 约束下**必然填不满预算**（3×300=900 < 1000）。
    #    这不是 bug：fill_rate = 1/0.30 ≈ 3.34，即至少 4 个品才能分完预算。
    #    恰好与 spec §7 的 too_few_spus 阈值（< 4 个品）重合 —— 数据量不足的店铺
    #    正好也是预算填不满的店铺，提示逻辑可以复用同一判据。
    near(sum(a.suggested_cost for a in r.allocations), 900.0, "三品时各自拿满 cap")
    near(r.unallocated, 100.0, "三品填不满预算，未分配额被显式记录而非静默丢失")
    near(sum(a.suggested_cost for a in r.allocations) + r.unallocated, r.total_budget,
         "三品时仍满足「分配额 + 未分配额 == 总预算」")
    # 判据：填满预算所需的最少品数 = ceil(1 / cap_ratio) = ceil(3.34) = 4
    need = math.ceil(1 / CAP_RATIO)
    eq(need, 4, "填满预算所需最少品数 = ceil(1/cap_ratio) = 4")
    ok(3 < need, "3 个品少于所需品数，恰好落在「填不满」的一侧")


def test_skip_rule() -> None:
    """跳过规则：cost≤0 / roi=None 不分配，但**仍计入预算基数**。

    这是最隐蔽的坑：若预算基数只算「参与分配」的品，被跳过品原花的钱就凭空消失，
    页面上会出现「建议总分配 < 实际总花费」。
    """
    items = [
        mk("A", 100.0, 5.0),
        mk("ZERO", 0.0, None),      # 无花费 → 跳过
        mk("NEG", -50.0, 3.0),      # 负花费（退款冲抵）→ 跳过
        mk("NOROI", 80.0, None),    # 花费有但投产比算不出（分母 0）→ 跳过
    ]
    r = greedy_allocate(items)
    # 预算基数 = 100 + 0 + (-50) + 80 = 130（负数按代数和计入，真实反映净花费）
    near(r.total_budget, 130.0, "预算基数含 cost 为 0 / 负 / roi 为 None 的品")
    by = {a.spu: a for a in r.allocations}
    eq(by["ZERO"].skipped, True, "cost=0 的品被跳过")
    eq(by["NEG"].skipped, True, "cost<0 的品被跳过")
    eq(by["NOROI"].skipped, True, "roi=None 的品被跳过")
    eq(by["A"].skipped, False, "正常品参与分配")
    eq(len(r.allocations), 4, "跳过的品也补回结果，保证与输入一一对应")
    eq(by["ZERO"].skip_reason, "无推广花费", "cost=0 的跳过原因文案")
    eq(by["NOROI"].skip_reason, "投产比无法计算", "roi=None 的跳过原因文案")
    # A 独占 cap = 130 × 0.3 = 39，剩余 91 无处可去
    near(by["A"].suggested_cost, 39.0, "有效品拿满 cap")
    near(r.unallocated, 91.0, "无有效品可承接的预算记入 unallocated")


def test_monotonicity() -> None:
    """单调性 + 输入顺序无关性。"""
    # 注意不是「严格递减」：cap 封顶会让高投产比的品拿到相同额度
    items = [mk("HIGH", 100.0, 8.0), mk("MID", 100.0, 4.0), mk("LOW", 100.0, 1.0)]
    r = greedy_allocate(items)
    by = {a.spu: a for a in r.allocations}
    ok(by["HIGH"].suggested_cost >= by["MID"].suggested_cost, "高投产比分配额 ≥ 中")
    ok(by["MID"].suggested_cost >= by["LOW"].suggested_cost, "中投产比分配额 ≥ 低")
    # 输入顺序打乱后结果必须一致（函数内部排序，不依赖调用方顺序）
    r2 = greedy_allocate([mk("LOW", 100.0, 1.0), mk("HIGH", 100.0, 8.0), mk("MID", 100.0, 4.0)])
    by2 = {a.spu: a for a in r2.allocations}
    eq([by2["HIGH"].suggested_cost, by2["MID"].suggested_cost, by2["LOW"].suggested_cost],
       [by["HIGH"].suggested_cost, by["MID"].suggested_cost, by["LOW"].suggested_cost],
       "输入顺序不影响分配结果")


def test_boundaries() -> None:
    """边界：空输入 / 单品 / 全部同等 / 全零 / 负投产比 / cap_ratio 越界。"""
    # 空输入
    r = greedy_allocate([])
    eq(r.allocations, [], "空输入返回空分配列表")
    near(r.total_budget, 0.0, "空输入总预算为 0")
    near(r.ideal_total_amount, 0.0, "空输入理论上限为 0")
    near(r.unallocated, 0.0, "空输入未分配为 0")

    # 单个 SPU：只能拿 30%，70% 无处可去 —— spec §7 的 too_few_spus 分支依赖此行为
    r1 = greedy_allocate([mk("ONLY", 100.0, 5.0)])
    near(r1.allocations[0].suggested_cost, 30.0, "单个 SPU 只拿 30%")
    near(r1.unallocated, 70.0, "单个 SPU 时 70% 无处可去")
    near(r1.ideal_total_amount, 150.0, "单个 SPU 的理论上限 = 30×5")

    # 全部投产比相同：分配额仍须守恒（不因 roi 相等而走特殊分支）
    # 4 个品各 100 → 总预算 400、cap 120。A→120 剩 280；B→120 剩 160；C→120 剩 40；D→40
    same = [mk(f"S{i}", 100.0, 3.0) for i in range(4)]
    r2 = greedy_allocate(same)
    near(sum(a.suggested_cost for a in r2.allocations), 400.0, "四个品时零和守恒成立")
    eq([a.suggested_cost for a in r2.allocations], [120.0, 120.0, 120.0, 40.0],
       "同等条件下按 cap 顺序填满，最后一个承接余额")

    # 全部无推广花费
    r3 = greedy_allocate([mk("A", 0.0, None), mk("B", 0.0, None)])
    near(r3.total_budget, 0.0, "全零花费时总预算为 0")
    near(r3.ideal_total_amount, 0.0, "全零花费时理论上限为 0")
    ok(all(a.skipped for a in r3.allocations), "全零花费时全部跳过")

    # 负投产比（罕见但不应崩溃）。2 个品 × 0.30 = 0.6 < 1 → 必然填不满：
    # 总预算 200、cap 60，POS→60、NEG→60，剩 80 无处可去
    r4 = greedy_allocate([mk("NEG", 100.0, -0.5), mk("POS", 100.0, 2.0)])
    eq([a.suggested_cost for a in r4.allocations], [60.0, 60.0],
       "负投产比排在末位，cap 封顶后仍各拿 60")
    near(sum(a.suggested_cost for a in r4.allocations) + r4.unallocated, r4.total_budget,
         "含负投产比时仍满足零和（分配 + 未分配 == 总预算）")
    near(r4.unallocated, 80.0, "两品填不满，未分配 80")
    near(r4.ideal_total_amount, 60 * 2.0 + 60 * -0.5, "负投产比拉低理论上限（预期行为）")

    # cap_ratio 越界必须报错（0 会让所有品分不到钱，>1 等于无约束）
    try:
        greedy_allocate([mk("A", 100.0, 5.0)], cap_ratio=0)
        ok(False, "cap_ratio=0 应抛 ValueError")
    except ValueError:
        ok(True, "cap_ratio=0 抛 ValueError")
    try:
        greedy_allocate([mk("A", 100.0, 5.0)], cap_ratio=1.5)
        ok(False, "cap_ratio=1.5 应抛 ValueError")
    except ValueError:
        ok(True, "cap_ratio=1.5 抛 ValueError")

    # 上界 1.0 合法：退化为「无上限的纯贪心」
    r5 = greedy_allocate([mk("A", 100.0, 5.0), mk("B", 100.0, 2.0)], cap_ratio=1.0)
    near(r5.allocations[0].suggested_cost, 200.0, "cap_ratio=1 时高投产比独吞全部预算")
    near(r5.allocations[1].suggested_cost, 0.0, "cap_ratio=1 时低投产比拿 0")


def test_gap_and_rate() -> None:
    """缺口与达成率。"""
    gap, rate = compute_gap(80.0, 100.0)
    near(gap, 20.0, "gap = 理想 − 当前")
    near(rate, 0.8, "达成率 = 当前 ÷ 理想")
    # 分母 0 → None（前端显示 --），绝不返回 Infinity
    gap0, rate0 = compute_gap(0.0, 0.0)
    near(gap0, 0.0, "理想为 0 时 gap 为 0")
    eq(rate0, None, "理想为 0 时达成率为 None 而非 Infinity")
    # 当前超过理想（数据异常时可能出现）→ gap 为负，不做特殊处理
    gapn, raten = compute_gap(120.0, 100.0)
    near(gapn, -20.0, "当前超理想时 gap 为负")
    near(raten, 1.2, "当前超理想时达成率 > 1")


def test_random_zero_sum() -> None:
    """随机数据零和守恒（20 组，固定种子可复现）。

    手写用例只覆盖设计者想到的形状；随机化能撞出「凑巧通过」的边界。
    """
    rng = random.Random(20261008)
    all_conserved = True
    all_capped = True
    all_income = True
    for i in range(20):
        n = rng.randint(1, 12)
        items = [
            PromoInput(
                spu=f"R{i}_{j}",
                cost=round(rng.uniform(0, 500), 2),
                roi=(round(rng.uniform(0, 10), 3) if rng.random() > 0.15 else None),
            )
            for j in range(n)
        ]
        r = greedy_allocate(items)
        allocated = sum(a.suggested_cost for a in r.allocations)
        if abs(allocated + r.unallocated - r.total_budget) > 1e-6:
            all_conserved = False
        if any(a.suggested_cost > r.cap + 1e-6 for a in r.allocations):
            all_capped = False
        if any(a.suggested_cost < -1e-9 for a in r.allocations):
            all_income = False
    ok(all_conserved, "20 组随机数据：分配额 + 未分配额 == 总预算（零和守恒）")
    ok(all_capped, "20 组随机数据：无任何 SPU 超过 cap")
    ok(all_income, "20 组随机数据：无负数分配额（浮点误差未泄漏）")


def test_never_worse_than_current() -> None:
    """下界约束：**现状满足 cap 时**，理论上限 ≥ 当前实际。

    WARNING 前提必须写清：贪心解是**在 cap 约束下**的最优，只有当现状本身满足
    cap（每个品花费 <= 总预算 x 30%）时，现状才是一个可行解、贪心才必然不劣于它。
    现状违反 cap 时（单品吃掉大部分预算），贪心必须把钱挪走 -> ideal < current。
    那不是 bug，是**约束的成本**；语义由 test_over_concentrated_* 覆盖。
    （原 docstring 写「现状本身就是一个可行解」，漏了这个前提：真实数据 3 店有 2 店不满足。）
    """
    # 4 个品各占 25% 预算（< 30% 上限）→ 现状可行，贪心不劣于现状
    items = [mk("A", 100.0, 1.0), mk("B", 100.0, 1.0), mk("C", 100.0, 1.0),
             mk("D", 100.0, 1.0)]
    r = greedy_allocate(items)
    current = sum(it.cost * (it.roi or 0) for it in items)
    ok(r.ideal_total_amount >= current - EPS,
       f"现状可行时理论上限 {r.ideal_total_amount} 不低于当前实际 {current}")
    gap, rate = compute_gap(current, r.ideal_total_amount)
    ok(gap >= -EPS, "现状可行时可优化空间不会为负")
    ok(rate is not None and rate <= 1.0 + EPS, "现状可行时达成率不超过 1")
    eq(detect_over_concentrated(r.allocations, r.cap), None, "现状可行时无超限 SPU")


def test_over_concentrated_reversed_order() -> None:
    """现状违反 cap 时：gap 为负、达成率 > 1，且必须被 detect_over_concentrated 捕获。

    真实数据场景（卡求旗舰店 1 个 SPU 花掉 100% 预算、投产比 6.49）：
    cap 把预算削到 30% → 理论上限必然低于现状。这**不是 bug**，
    但页面若照字面渲染「可优化空间 −1,735 元」运营会以为算法算错了，
    故必须有明确标志让前端改文案。
    """
    items = [mk("X", 1000.0, 6.49)]
    r = greedy_allocate(items)
    current = 1000.0 * 6.49
    gap, rate = compute_gap(current, r.ideal_total_amount)

    ok(r.ideal_total_amount < current,
       f"现状违反 cap 时理论上限 {r.ideal_total_amount:.2f} 低于现状 {current:.2f}")
    ok(gap < 0, f"可优化空间为负（实际 {gap:.2f}）")
    ok(rate is not None and rate > 1.0, f"达成率大于 1（实际 {rate:.3f}）")
    eq(detect_over_concentrated(r.allocations, r.cap), "X",
       "detect_over_concentrated 捕获到超限 SPU")


def test_over_concentrated_multiple() -> None:
    """多个 SPU 都超 cap 时返回顿号连接的列表；均分时返回 None。"""
    # 3 品各花 500，总预算 1500 → cap = 450，三个都超
    items = [mk("A", 500.0, 2.0), mk("B", 500.0, 1.5), mk("C", 500.0, 1.0)]
    r = greedy_allocate(items)
    got = detect_over_concentrated(r.allocations, r.cap)
    ok(got is not None and "A" in got and "B" in got and "C" in got,
       f"多个超限 SPU 全部列出（实际 {got}）")

    # 8 品各花 1000，总预算 8000 → cap = 2400，都不超
    items8 = [mk(f"P{i}", 1000.0, 2.0) for i in range(8)]
    r8 = greedy_allocate(items8)
    eq(detect_over_concentrated(r8.allocations, r8.cap), None, "均分 8 品时无超限")


def test_over_concentrated_cap_boundary() -> None:
    """边界：current 恰好等于 cap 时不算超限（> cap 而非 ≥ cap）。"""
    items = [mk("A", 300.0, 2.0), mk("B", 200.0, 1.0), mk("C", 500.0, 1.0)]
    r = greedy_allocate(items)
    # 总预算 1000 → cap = 300；A 恰好 300（= cap，不算超），只有 500 > 300 的 C 被捕获
    eq(detect_over_concentrated(r.allocations, r.cap), "C",
       "恰好等于 cap 的品不算超限，只有 C 被捕获")


def test_roi_cv() -> None:
    """波动校验第一步：CV 值计算。"""
    # 常量序列 → CV = 0
    same = [DailyRoi(10.0, 2.0) for _ in range(5)]
    near(compute_roi_cv(same), 0.0, "常量序列的 CV 为 0")

    # 明显波动 → CV = 1.0（均值 1.5，总体标准差 1.5）
    near(compute_roi_cv([DailyRoi(10.0, 0.0), DailyRoi(10.0, 3.0)]), 1.0,
         "两日相差悬殊时 CV = 1.0")

    # 空/单点 → 算不出，返回 None（不是 0）
    eq(compute_roi_cv([]), None, "空序列返回 None")
    eq(compute_roi_cv([DailyRoi(10.0, 2.0)]), None, "单点算不出标准差，返回 None")

    # cost=0 的日被跳过：只留两日有效（roi 均 2）→ CV=0
    near(compute_roi_cv([DailyRoi(10.0, 2.0), DailyRoi(10.0, 2.0), DailyRoi(0.0, 0.0)]), 0.0,
         "cost=0 的日被跳过，不参与 CV 计算")
    # roi=None 的日同样跳过
    near(compute_roi_cv([DailyRoi(10.0, 2.0), DailyRoi(10.0, 2.0), DailyRoi(10.0, None)]), 0.0,
         "roi 为 None 的日被跳过")

    # 均值 <= 0 → 算不出（相对波动没有意义）
    eq(compute_roi_cv([DailyRoi(10.0, -1.0), DailyRoi(10.0, -3.0)]), None, "均值 < 0 时返回 None")
    eq(compute_roi_cv([DailyRoi(10.0, 0.0), DailyRoi(10.0, 0.0)]), None, "均值为 0 时返回 None")

    # ⚠️ 用总体标准差（除以 n）而非样本标准差（除以 n−1）
    # 样本标准差会系统性高估 CV，在真实数据上会把几乎所有品标成不稳定
    cv3 = compute_roi_cv([DailyRoi(10.0, 1.0), DailyRoi(10.0, 2.0), DailyRoi(10.0, 3.0)])
    near(cv3, 0.816496580927726 / 2.0, "使用总体标准差（除以 n）而非样本标准差")
    sample_var = (((1 - 2) ** 2 + 0 + 1) / 2)
    ok(cv3 < math.sqrt(sample_var) / 2.0, "同数据下本实现严格小于样本标准差口径")


def test_mark_unstable() -> None:
    """波动校验第二步：两级判定（绝对兜底 + 店内相对分位）。"""
    # 全部低于绝对阈值 → 无人被判不稳定（即使它是店内最不稳的）
    eq(mark_unstable([0.1, 0.2, 0.3]), [False, False, False],
       "全部低于绝对阈值时一律稳定")
    # CV 算不出来 → False（算不出 ≠ 不稳定）
    eq(mark_unstable([None, None]), [False, False], "CV 不可算时判稳定")
    eq(mark_unstable([None, 2.0]), [False, True], "部分不可算时不影响其他项")

    # ⚠️ 候选不足 min_samples 时退回绝对阈值判定
    # 少这个分支会出现：1 个候选时它的 P70 分位线就是自己，「超过」恒为 False
    eq(mark_unstable([2.0]), [True], "单候选超绝对阈值时判不稳定（退回绝对判定）")
    eq(mark_unstable([0.5]), [False], "单候选未超绝对阈值时判稳定")
    eq(mark_unstable([2.0, 3.0]), [True, True], "2 个候选时退回绝对判定，两者都超阈值即都标")
    eq(mark_unstable([0.5, 3.0]), [False, True], "2 个候选时只标超阈值者")

    # 候选足够时按P70 相对分位：相对最不稳的那部分才标
    got = mark_unstable([1.0, 1.1, 1.2, 2.0, 3.0, 0.5])
    eq(got[-1], False, "低于绝对阈值的品即使 CV 也不为0，仍判稳定")
    ok(sum(got) >= 1, "候选足够时至少标出最不稳的品")

    # 关键性质：真实数据上 CV 分布很散（实测 0.456~2.438），
    # 用绝对阈值 0.5 会把 8/10 个品标成不稳定 —— 那样等于没筛选。
    # 改用店内分位后，被标记的比例应当显著低于 50%
    real_cvs = [0.456, 0.503, 0.632, 1.425, 1.683, 1.806, 2.438, 0.536, 0.589, 1.118]
    flags = mark_unstable(real_cvs)
    ratio = sum(flags) / len(flags)
    ok(ratio <= 0.5, f"店内相对分位法下被标记比例 {ratio:.0%} ≤ 50%（绝对阈值法会是 80%）")
    ok(ratio > 0, "店内相对分位法仍能标出部分不稳的品")

    # 阈值可传参
    eq(mark_unstable([0.5], absolute_threshold=0.4, min_samples=2), [True],
       "调低绝对阈值后原本稳定的品被判不稳定")
    # 非法 q 报错
    for bad in (-0.1, 1.1):
        try:
            mark_unstable([1.0, 2.0], q=bad)
            ok(False, f"q={bad} 应抛 ValueError")
        except ValueError:
            ok(True, f"q={bad} 抛 ValueError")



def test_quadrants() -> None:
    """四象限分档：四档各一组 + 边界约定。"""
    # 构造四象限各一个：roi/cost 都高、都低、roi高cost低、roi低cost高
    # roi 排序：5.0(A,C) > 1.0(B,D)；P50 = (2.5+2.5)/2 = 2.5 → A,C 为高roi
    # cost 排序：100(A,B) > 10(C,D)；P50 = 55 → A,B 为高cost
    items = [
        mk("A", 100.0, 5.0),   # 高roi 高cost → core
        mk("B", 100.0, 1.0),   # 低roi 高cost → loser
        mk("C", 10.0, 4.0),    # 高roi 低cost → potential
        mk("D", 10.0, 0.5),    # 低roi 低cost → trial
    ]
    got = classify_quadrant(items)
    eq(got["A"], QUADRANT_CORE, "高投产比 + 高花费 → 核心利润品")
    eq(got["B"], QUADRANT_LOSER, "低投产比 + 高花费 → 吞金兽")
    eq(got["C"], QUADRANT_POTENTIAL, "高投产比 + 低花费 → 潜力股")
    eq(got["D"], QUADRANT_TRIAL, "低投产比 + 低花费 → 试水品")
    eq(len(got), 4, "四个品全部得到分档")

    # 恰好落在 P50 上的元素归入高档（>= 约定）
    # roi = [1,2,3,4] → P50 = 2.5；cost 同理。让 B 的 roi/cost 恰为 2.5
    border = [mk("A", 100.0, 4.0), mk("B", 2.5, 2.5), mk("C", 1.0, 1.0)]
    got2 = classify_quadrant(border)
    eq(got2["B"], QUADRANT_CORE, "恰好等于两个 P50 阈值 → 归入高档（>= 约定）")

    # roi 为 None 的品不参与分档，也不影响阈值
    with_none = [mk("A", 100.0, 5.0), mk("B", 100.0, 1.0),
                 mk("C", 10.0, 4.0), mk("D", 10.0, 0.5), mk("E", 999.0, None)]
    got3 = classify_quadrant(with_none)
    eq("E" in got3, False, "roi=None 的品不出现在分档结果中")
    eq(got3["A"], QUADRANT_CORE, "roi=None 的高花费品不拉低分档结果（阈值只由有效品算）")

    # 全部 roi=None → 空字典
    eq(classify_quadrant([mk("A", 100.0, None), mk("B", 50.0, None)]), {},
       "全部 roi 不可算时返回空字典")

    # 空输入
    eq(classify_quadrant([]), {}, "空输入返回空字典")

    # q 可切：改用 P75 会让分档更严（高花费档人数减少）
    items5 = [mk(f"S{i}", float((i + 1) * 10), float(i + 1)) for i in range(5)]
    p50 = classify_quadrant(items5, q=0.5)
    p75 = classify_quadrant(items5, q=0.75)
    n_core50 = sum(1 for v in p50.values() if v == QUADRANT_CORE)
    n_core75 = sum(1 for v in p75.values() if v == QUADRANT_CORE)
    ok(n_core75 < n_core50, f"P75 切法比 P50 更严（core: {n_core50} → {n_core75}）")

    # 非法 q 报错
    for bad in (-0.1, 1.1):
        try:
            classify_quadrant(items5, q=bad)
            ok(False, f"q={bad} 应抛 ValueError")
        except ValueError:
            ok(True, f"q={bad} 抛 ValueError")

    # 分档阈值 MIN_SPU_FOR_QUADRANT 的语义：品数少于它时分档不可信
    eq(MIN_SPU_FOR_QUADRANT, 4, "分档可信所需最少品数为 4")


def test_quadrant_thresholds() -> None:
    """分割线阈值：与分档同源、只由有效品计算、无样本返回 (None, None)。"""
    items = [
        mk("A", 100.0, 5.0),
        mk("B", 100.0, 1.0),
        mk("C", 10.0, 4.0),
        mk("D", 10.0, 0.5),
    ]
    # roi 排序 0.5/1/4/5 → P50 = (1+4)/2 = 2.5；cost 排序 10/10/100/100 → P50 = 55
    eq(compute_quadrant_thresholds(items), (2.5, 55.0), "P50 阈值 = 线性插值中位数")

    # 关键约束：前端要在这两条线上画分割线。若阈值与分档各算一遍，
    # 会出现「点被染成高效档却在分割线下方」。这里直接验证两者一致。
    roi_thr, cost_thr = compute_quadrant_thresholds(items)
    got = classify_quadrant(items)
    for it in items:
        hi_roi = it.roi >= roi_thr
        hi_cost = it.cost >= cost_thr
        expect = (QUADRANT_CORE if hi_roi and hi_cost
                  else QUADRANT_POTENTIAL if hi_roi
                  else QUADRANT_LOSER if hi_cost
                  else QUADRANT_TRIAL)
        eq(got[it.spu], expect, f"{it.spu} 的分档与「点在线哪一侧」完全一致")

    # roi=None 的品不参与阈值（否则阈值被拉向 0，全落进高档）
    mixed = items + [mk("E", 999.0, None)]
    eq(compute_quadrant_thresholds(mixed), (2.5, 55.0),
       "roi=None 的高花费品不影响阈值")

    # 无可分档样本 → (None, None)，不抛异常（端点要照常 200 + empty_reason）
    eq(compute_quadrant_thresholds([mk("A", 0.0, None)]), (None, None),
       "全部 roi 不可算时返回 (None, None)")
    eq(compute_quadrant_thresholds([]), (None, None), "空输入返回 (None, None)")

    # 单个样本：分位数退化为它自己（这正是 too_few_spus 要弱化展示的原因）
    eq(compute_quadrant_thresholds([mk("A", 3448.0, 4.83)])[0], 4.83,
       "单样本时阈值 = 该样本自身")


def test_advice() -> None:
    """建议文案：术语合规 + 分档措辞 + 波动提示。"""
    banned = ("边际", "marginal", "亏损", "预计", "预测")
    cases = [
        (QUADRANT_CORE, 5.0, False, 50.0),
        (QUADRANT_CORE, 5.0, True, 50.0),      # 不稳定
        (QUADRANT_POTENTIAL, 4.0, False, None),  # 无 delta
        (QUADRANT_LOSER, 0.8, False, -200.0),
        (QUADRANT_LOSER, 0.8, True, -200.0),    # 不稳定
        (QUADRANT_TRIAL, 0.3, False, 0.0),       # delta = 0
        (None, None, False, None),              # 无法分档
    ]
    for quad, roi, unst, delta in cases:
        text = build_advice(quad, roi, unstable=unst, delta=delta)
        ok(bool(text), f"分档 {quad} 生成了非空文案")
        for word in banned:
            ok(word not in text, f"文案不含禁用词「{word}」（{text}）")
        ok(text.endswith("。"), f"文案以句号收尾（{text}）")

    # 不稳定时必须出现波动提示；稳定时不得出现
    unstable_text = build_advice(QUADRANT_CORE, 5.0, unstable=True)
    ok("波动" in unstable_text, "不稳定时追加波动提示")
    stable_text = build_advice(QUADRANT_CORE, 5.0, unstable=False)
    ok("波动" not in stable_text, "稳定时不加波动提示")

    # delta 的方向决定动词
    ok("增加" in build_advice(QUADRANT_POTENTIAL, 4.0, delta=100.0), "delta>0 → 增加")
    ok("削减" in build_advice(QUADRANT_LOSER, 0.5, delta=-100.0), "delta<0 → 削减")
    ok("维持" in build_advice(QUADRANT_CORE, 5.0, delta=0.0), "delta=0 → 维持")
    ok("约 100 元" in build_advice(QUADRANT_POTENTIAL, 4.0, delta=100.0),
       "文案带上具体金额")
    ok("约 100 元" in build_advice(QUADRANT_LOSER, 0.5, delta=-100.0),
       "减投时取绝对值，不出现负号金额")

    # ⚠️ 高投产比却被减投时，文案必须说明原因是「超上限」而非「效率差」，
    #否则会出现「投产比高于中位…建议削减」这种看起来自相矛盾的表述，
    # 让运营误以为算法在惩罚好品
    capped = build_advice(QUADRANT_CORE, 5.0, delta=-300.0)
    ok("超单品预算上限" in capped, "高投产比却减投时说明原因是超上限")
    ok("削减推广预算" not in capped, "该情形下不用「削减推广预算」这个说法")
    low_roi_cut = build_advice(QUADRANT_LOSER, 0.5, delta=-300.0)
    ok("削减推广预算" in low_roi_cut, "低投产比减投时明说「削减」")
    ok("超单品预算上限" not in low_roi_cut, "低投产比减投不提上限")

    # 无法分档时直说数据不足，不硬凑建议
    no_data = build_advice(None, None)
    ok("无有效推广数据" in no_data, "无法分档时说明数据不足")
    for word in banned:
        ok(word not in no_data, f"数据不足文案也不含禁用词「{word}」")


def main() -> int:
    """依次执行全部测试组并汇总结果。"""
    print("=== 推广预算贪心算法验证 ===\n")
    for fn in (
        test_basic_greedy,
        test_zero_sum,
        test_cap_ratio,
        test_skip_rule,
        test_monotonicity,
        test_boundaries,
        test_gap_and_rate,
        test_random_zero_sum,
        test_never_worse_than_current,
        test_over_concentrated_reversed_order,
        test_over_concentrated_multiple,
        test_over_concentrated_cap_boundary,
        test_roi_cv,
        test_mark_unstable,
        test_quadrants,
        test_quadrant_thresholds,
        test_advice,
    ):
        fn()
    print(f"\n通过 {_pass} 项，失败 {_fail} 项")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())