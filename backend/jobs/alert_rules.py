# -*- coding: utf-8 -*-
"""库存预警与补货建议的纯计算规则（唯一事实来源，无 IO，可独立测试）。

预警三档（城市级阈值，逐 店铺×SKU×城市 判定）：
  healthy   现货 ≥ 阈值
  incoming  现货 < 阈值 ≤ 现货 + 采购未到货（在途可补足）
  alert     现货 + 采购未到货 < 阈值（在途也填不上窟窿）
下柜 SKU 完全排除：不预警、不补货。

补货量（销速驱动目标库存）：
  velocity = 可信日均销速（三级估计器，见 credible_velocity）
  target   = velocity × 覆盖天数 × (1 + 安全系数)
  qty      = max(0, ceil(target) − 现货 − 在途)；qty = 0 的行不进补货清单

可信销速三级估计器（2026-10-24 方案，ADR-0010；修正"有货天数过少时
out/avail 把积压爆发当常态"的销速失真，例：14 日窗仅 1 天有货出 66 件 →
直算 66 件/天，目标库存被顶到 ~713 件）：
  L0 有货天数 ≤ 0      → 0（无销速依据，宁可不建议，不凭空估）
  L1 有货天数 ≥ 6      → 窗口出库 ÷ 窗口有货天数（样本量够，直接测得）
  L2 0 < 有货天数 < 6  → 收缩估算：
       v_ref    参照销速 = 30 日窗出库 ÷ max(30 日窗有货天数, 15)——分母垫底
                防参照窗自己也长期断货时踩同一个坑；参照窗无量则退摊薄下界
       v_short' min(窗口出库÷有货天数, v_ref × 3)——突发封顶：积压集中释放
                最多按参照的 3 倍计（v_ref = 0 时跳过封顶）
       w        = 有货天数 ÷ 6——可信度权重（精算 credibility 思想：
                样本越少越信历史基准）
       v        = w · v_short' + (1−w) · v_ref

默认参数（2026-10-21 grilling 拍板；可信销速参数 2026-10-24 拍板；
库存缺口关注阈值 2026-10-26 拍板）：
  覆盖天数 9（每周采购一次：7 天周期 + 到货延迟缓冲）
  安全系数 0.2；销速窗口 14 日（用 avail 修正缺货期导致的销速低估）
  可信门槛 6 天（2026-10-26 用户拍板：原 7 天）；突发封顶 3 倍；参照窗固定 30 日
  缺货损失下限 5 件（低于它不值得重点关注）
"""
from __future__ import annotations

import math

# 三档状态
HEALTHY = "healthy"
INCOMING = "incoming"
ALERT = "alert"

# 补货算法默认参数
DEFAULT_DAYS = 9
DEFAULT_SAFETY = 0.2
DEFAULT_WINDOW = 14
ALLOWED_WINDOWS = (7, 14, 30)

# 可信销速参数（三级估计器，见模块 docstring 与 ADR-0010）
CRED_MIN_DAYS = 6  # 可信最少有货天数（2026-10-26 用户拍板：原 7 → 6）：不足则销速收缩估算
BURST_CAP = 3.0  # 突发封顶倍数：短窗销速最多放大参照销速的 3 倍
REF_WINDOW = 30  # 参照窗天数：收缩估算的历史基准，固定 30 日

# 库存缺口关注判定（双锚：覆盖率 + 估算缺货损失；见 stockout_gap 与 ADR-0011）
GAP_LOSS_MIN = 5.0  # 估算缺货损失下限（件）：低于它不值得重点关注
GAP_COVERAGE = 0.5  # 覆盖率门槛：缺货时间超过一半 → 缺口关注
GAP_FLAG = "关注"  # 关注档位文案（2026-10-26 用户拍板：断货/缺口两档合并为一个「关注」）

# 销速估计方式（补货行的 velocity_kind；前端/导出据此标"收缩估算"）
VELOCITY_NONE = "none"  # 有货天数 ≤ 0：无销速依据 → 0
VELOCITY_MEASURED = "measured"  # 有货天数 ≥ 可信门槛：直接测得
VELOCITY_SHRUNK = "shrunk"  # 有货天数不足可信门槛：收缩估算

# 城市阈值默认值（首次建表 seed；0 = 该城不检测）
DEFAULT_THRESHOLD = 10.0


def is_offshelf(shelf_status: object) -> bool:
    """下柜判定：状态文本含「下」即视为下柜（兼容「已下柜」等写法）。

    与前端 shelfTag 的 includes("下") 口径一致。
    """
    return "下" in str(shelf_status or "")


def alert_status(threshold: float, stock: float, purchase: float) -> str:
    """三档预警判定：healthy / incoming / alert。

    Args:
        threshold: 城市阈值（0 时任何现货 ≥ 0 都算健康 = 不检测）。
        stock: 现货库存。
        purchase: 采购未到货（在途）。

    Returns:
        str: 三档之一；下柜排除由调用方先做（本函数不感知上下柜）。
    """
    if stock >= threshold:
        return HEALTHY
    if stock + purchase >= threshold:
        return INCOMING
    return ALERT


def reference_velocity(out_ref: float, avail_ref: float, ref_window: int) -> float:
    """参照销速：参照窗出库 ÷ 分母垫底后的有货天数。

    分母垫底 = max(avail_ref, ceil(ref_window/2))——参照窗自己也长期断货时，
    直接 out/avail 会把积压爆发当常态（与被修正的短窗是同一个坑），所以无论
    实际有货几天，最多按半个参照窗摊薄。参照窗完全无量（出库、有货全 0）时
    返回 0，由 credible_velocity 决定是否跳过封顶。

    Args:
        out_ref: 参照窗出库量（如 30 日出库）。
        avail_ref: 参照窗有货天数（如 30 日有货天数）。
        ref_window: 参照窗天数（REF_WINDOW = 30）。

    Returns:
        float: 参照销速（件/天）；无依据时 0。
    """
    if avail_ref > 0:
        return out_ref / max(avail_ref, math.ceil(ref_window / 2))
    if out_ref > 0:
        # 有出库却记 0 天有货（源数据矛盾）：退摊薄下界，比 0 略可信
        return out_ref / ref_window
    return 0.0


def credible_velocity(
    out: float,
    avail: float,
    out_ref: float = 0.0,
    avail_ref: float = 0.0,
    ref_window: int = REF_WINDOW,
) -> tuple[float, str]:
    """可信日均销速：三级估计器（L0 无依据 / L1 直接测得 / L2 收缩估算）。

    Args:
        out: 计算窗出库量（如近 14 日出库）。
        avail: 计算窗有货天数（如 14 日有货天数）。
        out_ref: 参照窗出库量（30 日出库；窗口选 30 日时与 out 相同，天然自洽）。
        avail_ref: 参照窗有货天数（30 日有货天数）。
        ref_window: 参照窗天数（默认 REF_WINDOW = 30）。

    Returns:
        tuple[float, str]: (销速 件/天, 估计方式)。
        估计方式 ∈ {VELOCITY_NONE, VELOCITY_MEASURED, VELOCITY_SHRUNK}。

    Examples:
        >>> v, kind = credible_velocity(66, 1, 80, 5)  # 14 日窗 1 天有货出 66
        >>> round(v, 2), kind  # 参照 80/15≈5.3，封顶 min(66,16)=16，w=1/7
        (6.86, 'shrunk')
    """
    if avail <= 0:
        return 0.0, VELOCITY_NONE
    if avail >= CRED_MIN_DAYS:
        return out / avail, VELOCITY_MEASURED
    v_short = out / avail
    v_ref = reference_velocity(out_ref, avail_ref, ref_window)
    if v_ref > 0:
        # 突发封顶：补货日的积压集中释放不是常态，最多按参照的 BURST_CAP 倍计；
        # 参照为 0（新品无历史）时不封顶，退化为纯可信度加权
        v_short = min(v_short, v_ref * BURST_CAP)
    w = avail / CRED_MIN_DAYS
    return w * v_short + (1 - w) * v_ref, VELOCITY_SHRUNK


def stockout_gap(
    avail: float,
    window: int,
    velocity: float,
    velocity_ref: float = 0.0,
    loss_min: float = GAP_LOSS_MIN,
) -> tuple[str, float]:
    """库存缺口关注判定：返回 (关注档位, 估算缺货损失件数)。

    两个锚（缺一不可）：
      coverage = 有货天数 ÷ 窗口天数 —— 缺货时间占比（缺口有多大）
      损失 L   = 日销速 × (窗口天数 − 有货天数) —— 缺货期被压住的需求（值不值得管）

    档位（先过损失门槛，再看覆盖率）：
      "关注"  coverage < 50%：窗口里过半时间没货
      ""      其余不关注

    档位只有一档（2026-10-26 用户拍板，原「断货 / 缺口」两档合并为一个「关注」）：
    严重程度不再用不同文案区分，而是由说明文字里的"缺货天数 / 损失件数"直接量化。

    损失口径：有货天数 > 0 用修正后销速；**有货天数 = 0 时用参照销速**——
    整窗缺货的损失不是 0，而是被完全截断的需求（这正是参照销速的用途）。
    与"有货天数 = 0 → 不建议补货"策略无关：那条管的是要不要建议补货，
    这条管的是损失有多大。

    Args:
        avail: 窗口有货天数。
        window: 窗口天数（7/14/30）。
        velocity: 修正后销速（件/天）。
        velocity_ref: 参照销速（件/天；仅在 avail = 0 时用作损失率）。
        loss_min: 损失下限（件），低于它不关注。

    Returns:
        tuple[str, float]: (档位, 估算缺货损失件数)；档位 ∈ {"", "关注"}。

    Examples:
        >>> stockout_gap(1, 14, 6.43)
        ('关注', 83.6)
    """
    if window <= 0:
        return "", 0.0
    rate = velocity if avail > 0 else velocity_ref
    loss = rate * max(0.0, window - avail)
    if loss < loss_min:
        return "", round(loss, 1)
    coverage = avail / window
    if coverage < GAP_COVERAGE:
        return GAP_FLAG, round(loss, 1)
    return "", round(loss, 1)


# 销速算法说明对话框的演示算例（2026-10-26）：数字由真实函数算出，不复制公式
EXAMPLE_OUT = 66  # 14 日窗出库
EXAMPLE_AVAIL = 1  # 14 日窗有货天数
EXAMPLE_WINDOW = 14  # 示例窗口
EXAMPLE_OUT_REF = 80  # 30 日参照窗出库
EXAMPLE_AVAIL_REF = 5  # 30 日参照窗有货天数


def velocity_example(days: float = DEFAULT_DAYS, safety: float = DEFAULT_SAFETY) -> dict[str, float]:
    """销速算法演示算例：用真实函数算出对话框展示的那组数字。

    场景：14 日窗只有 1 天有货、这天出了 66 件；30 日参照窗出 80 件、有货 5 天。
    返回直算值、上下两个界、三步收缩的中间量与最终建议量——前端对话框只渲染，
    不自己算，避免前后端各写一份公式（口径漂移）。

    Args:
        days: 覆盖天数（默认 9）。
        safety: 安全系数（默认 0.2）。

    Returns:
        dict[str, float]: 算例各中间量（键见实现）。
    """
    v_raw = EXAMPLE_OUT / EXAMPLE_AVAIL  # 直算（高估上界）
    v_floor = EXAMPLE_OUT / EXAMPLE_WINDOW  # 摊薄下界
    v_ref = reference_velocity(EXAMPLE_OUT_REF, EXAMPLE_AVAIL_REF, REF_WINDOW)
    v_capped = min(v_raw, v_ref * BURST_CAP)
    w = EXAMPLE_AVAIL / CRED_MIN_DAYS
    v = w * v_capped + (1 - w) * v_ref
    return {
        "out": float(EXAMPLE_OUT),
        "avail": float(EXAMPLE_AVAIL),
        "window": float(EXAMPLE_WINDOW),
        "out_ref": float(EXAMPLE_OUT_REF),
        "avail_ref": float(EXAMPLE_AVAIL_REF),
        "v_raw": round(v_raw, 2),
        "v_floor": round(v_floor, 2),
        "v_ref": round(v_ref, 2),
        "v_capped": round(v_capped, 2),
        "w": round(w, 3),
        "v": round(v, 2),
        "target_raw": math.ceil(v_raw * days * (1 + safety)),
        "target": math.ceil(v * days * (1 + safety)),
    }


def restock_qty(
    velocity: float,
    days: float,
    safety: float,
    stock: float,
    purchase: float,
) -> int:
    """建议补货量：目标库存 − 现货 − 在途，向上取整、不为负。

    Args:
        velocity: 可信日均销速（件/天；由 credible_velocity 算得后传入，
            展示与计算共用同一个销速，不在两处各算一遍）。
        days: 覆盖天数（默认 9）。
        safety: 安全系数（默认 0.2 = 20%）。
        stock: 现货库存。
        purchase: 采购未到货（在途）。

    Returns:
        int: ≥ 0 的整数建议量；0 表示无需补货（调用方过滤即可）。
    """
    target = velocity * days * (1 + safety)
    return max(0, math.ceil(target) - int(stock) - int(purchase))


def clamp_params(days: object, safety: object, window: object) -> tuple[float, float, int]:
    """把参数夹回合法域（None/缺失/越界回默认）。

    说明：HTTP 层的非数字串（如 "abc"）会被 FastAPI Query 类型验证先拦下
    （422），到不了这里；本函数兜的是 None（未传）与越界值（如 days=999、
    window=13），供内部调用与单测使用。

    Returns:
        tuple[float, float, int]: (days, safety, window)。
    """
    try:
        d = float(days)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        d = DEFAULT_DAYS
    if not (1 <= d <= 60):
        d = DEFAULT_DAYS
    try:
        s = float(safety)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        s = DEFAULT_SAFETY
    if not (0 <= s <= 5):
        s = DEFAULT_SAFETY
    try:
        w = int(window)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        w = DEFAULT_WINDOW
    if w not in ALLOWED_WINDOWS:
        w = DEFAULT_WINDOW
    return d, s, w
