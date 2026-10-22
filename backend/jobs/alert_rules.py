# -*- coding: utf-8 -*-
"""库存预警与补货建议的纯计算规则（唯一事实来源，无 IO，可独立测试）。

预警三档（城市级阈值，逐 店铺×SKU×城市 判定）：
  healthy   现货 ≥ 阈值
  incoming  现货 < 阈值 ≤ 现货 + 采购未到货（在途可补足）
  alert     现货 + 采购未到货 < 阈值（在途也填不上窟窿）
下柜 SKU 完全排除：不预警、不补货。

补货量（销速驱动目标库存）：
  velocity = 窗口出库量 ÷ 窗口有货天数（有货天数 = 0 视为无销速依据 → 0）
  target   = velocity × 覆盖天数 × (1 + 安全系数)
  qty      = max(0, ceil(target) − 现货 − 在途)；qty = 0 的行不进补货清单

默认参数（2026-10-21 grilling 拍板）：
  覆盖天数 9（每周采购一次：7 天周期 + 到货延迟缓冲）
  安全系数 0.2；销速窗口 14 日（用 avail 修正缺货期导致的销速低估）
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


def effective_velocity(out: float, avail: float) -> float:
    """有效日均销速：窗口出库 ÷ 窗口有货天数。

    有货天数 ≤ 0（一直缺货、无销速依据）→ 0：宁可不建议，不凭空估。
    """
    if avail <= 0:
        return 0.0
    return out / avail


def restock_qty(
    out: float,
    avail: float,
    days: float,
    safety: float,
    stock: float,
    purchase: float,
) -> int:
    """建议补货量：目标库存 − 现货 − 在途，向上取整、不为负。

    Args:
        out: 窗口出库量（如近 14 日出库）。
        avail: 窗口有货天数（如 14 日有货天数）。
        days: 覆盖天数（默认 9）。
        safety: 安全系数（默认 0.2 = 20%）。
        stock: 现货库存。
        purchase: 采购未到货（在途）。

    Returns:
        int: ≥ 0 的整数建议量；0 表示无需补货（调用方过滤即可）。
    """
    target = effective_velocity(out, avail) * days * (1 + safety)
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
