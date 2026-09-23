# -*- coding: utf-8 -*-
"""SPU 单品分析：节假日口径、工作日/节假日对比、综合分析文案生成。

节假日口径：周六/周日 + 国家法定节假日；调休上班的周末按工作日计。
法定节假日清单来自国务院办公厅每年的节假日安排通知，**每年年底需维护次年数据**；
若与国务院正式通知不一致，以通知为准并直接修改下方两个集合。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

# ---------------- 法定节假日 / 调休上班日（每年维护） ----------------
STATUTORY_HOLIDAYS: set[str] = {
    # ---- 2025 ----
    "2025-01-01",
    "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
    "2025-02-01", "2025-02-02", "2025-02-03", "2025-02-04",
    "2025-04-04", "2025-04-05", "2025-04-06",
    "2025-05-01", "2025-05-02", "2025-05-03", "2025-05-04", "2025-05-05",
    "2025-05-31", "2025-06-01", "2025-06-02",
    "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-04",
    "2025-10-05", "2025-10-06", "2025-10-07", "2025-10-08",
    # ---- 2026 ----
    "2026-01-01", "2026-01-02", "2026-01-03",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-02-20", "2026-02-21", "2026-02-22",
    "2026-04-04", "2026-04-05", "2026-04-06",
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    "2026-06-19", "2026-06-20", "2026-06-21",
    "2026-09-25", "2026-09-26", "2026-09-27",
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
    "2026-10-05", "2026-10-06", "2026-10-07",
}

# 调休上班的周末（按工作日统计）
MAKEUP_WORKDAYS: set[str] = {
    # ---- 2025 ----
    "2025-01-26", "2025-02-08", "2025-04-27", "2025-09-28", "2025-10-11",
    # ---- 2026 ----
    "2026-01-04", "2026-02-14", "2026-02-28",
    "2026-05-09", "2026-09-20", "2026-10-10",
}


def is_holiday(d: date) -> bool:
    """节假日 = 法定节假日 ∪ 周末 − 调休上班日。"""
    s = d.isoformat()
    if s in MAKEUP_WORKDAYS:
        return False
    if s in STATUTORY_HOLIDAYS:
        return True
    return d.weekday() >= 5


# ---------------- 分组对比 ----------------
class DayPoint:
    """单日聚合后的指标（数值均已按店铺求和）。"""

    __slots__ = ("date", "is_holiday", "visitors", "buyers", "orders", "items",
                 "amount", "refund_orders", "refund_amount",
                 "promotion_cost", "promotion_amount",
                 "search_impressions", "search_clicks")

    def __init__(self, date_s: str, **kw: float) -> None:
        self.date = date_s
        self.is_holiday = is_holiday(date.fromisoformat(date_s))
        self.visitors = kw.get("visitors", 0.0)
        self.buyers = kw.get("buyers", 0.0)
        self.orders = kw.get("orders", 0.0)
        self.items = kw.get("items", 0.0)
        self.amount = kw.get("amount", 0.0)
        self.refund_orders = kw.get("refund_orders", 0.0)
        self.refund_amount = kw.get("refund_amount", 0.0)
        self.promotion_cost = kw.get("promotion_cost", 0.0)
        self.promotion_amount = kw.get("promotion_amount", 0.0)
        self.search_impressions = kw.get("search_impressions", 0.0)
        self.search_clicks = kw.get("search_clicks", 0.0)


class GroupStats:
    """一组日期（工作日或节假日）的日均统计。"""

    def __init__(self, points: list[DayPoint]) -> None:
        self.days = len(points)
        n = max(self.days, 1)
        sv = sum(p.visitors for p in points)
        sb = sum(p.buyers for p in points)
        so = sum(p.orders for p in points)
        si = sum(p.search_impressions for p in points)
        sc = sum(p.search_clicks for p in points)
        amt = sum(p.amount for p in points)
        self.visitors_avg = sv / n
        self.buyers_avg = sb / n
        self.orders_avg = so / n
        self.items_avg = sum(p.items for p in points) / n
        self.search_impressions_avg = si / n
        self.amount_avg = amt / n
        # 客单价 / 搜索点击率：先求和再相除，与明细表、模块汇总口径一致
        self.avg_price: Optional[float] = (amt / sb) if sb else None
        self.search_click_rate: Optional[float] = (sc / si) if si else None
        self.refund_orders_avg = sum(p.refund_orders for p in points) / n
        self.refund_amount_avg = sum(p.refund_amount for p in points) / n
        self.conversion_rate: Optional[float] = (sb / sv) if sv else None
        # 推广维度：比率用「先求和再相除」而非日均比率平均，避免被低基数日期稀释
        pc = sum(p.promotion_cost for p in points)
        pa = sum(p.promotion_amount for p in points)
        self.promotion_cost_avg = pc / n
        self.promotion_amount_avg = pa / n
        self.roi: Optional[float] = (pa / pc) if pc else None
        self.promotion_ratio: Optional[float] = (pc / amt) if amt else None

    def to_dict(self) -> dict:
        return {
            "days": self.days,
            "visitors_avg": round(self.visitors_avg, 2),
            "buyers_avg": round(self.buyers_avg, 2),
            "orders_avg": round(self.orders_avg, 2),
            "items_avg": round(self.items_avg, 2),
            "amount_avg": round(self.amount_avg, 2),
            "avg_price": self.avg_price,
            "search_impressions_avg": round(self.search_impressions_avg, 2),
            "search_click_rate": self.search_click_rate,
            "refund_orders_avg": round(self.refund_orders_avg, 2),
            "refund_amount_avg": round(self.refund_amount_avg, 2),
            "conversion_rate": self.conversion_rate,
            "promotion_cost_avg": round(self.promotion_cost_avg, 2),
            "promotion_amount_avg": round(self.promotion_amount_avg, 2),
            "roi": self.roi,
            "promotion_ratio": self.promotion_ratio,
        }


def split_workday_holiday(points: list[DayPoint]) -> tuple[GroupStats, GroupStats]:
    workday = GroupStats([p for p in points if not p.is_holiday])
    holiday = GroupStats([p for p in points if p.is_holiday])
    return workday, holiday


# ---------------- 文案生成 ----------------
def _pct(holiday_v: float, workday_v: float) -> Optional[float]:
    """节假日相对工作日的变化率；基期为 0 时无法计算返回 None。"""
    if not workday_v:
        return None
    return (holiday_v - workday_v) / workday_v


def _fmt_pct(r: float) -> str:
    sign = "+" if r > 0 else ""
    return f"{sign}{r * 100:.1f}%"


def _magnitude(r: float) -> str:
    a = abs(r)
    if a >= 0.15:
        return "明显"
    if a >= 0.05:
        return "有所"
    return "略微"


def _dir_word(r: float) -> str:
    if abs(r) < 0.005:
        return "基本持平"
    return ("高于" if r > 0 else "低于") + "工作日"


def _money(v: float) -> str:
    return f"¥{v:,.0f}"


def _conv(v: Optional[float]) -> str:
    return "--" if v is None else f"{v * 100:.2f}%"


def _ratio_pct(v: Optional[float]) -> str:
    return "--" if v is None else f"{v * 100:.1f}%"


def _roi_fmt(v: Optional[float]) -> str:
    return "--" if v is None else f"{v:.2f}"


def build_insight(start: str, end: str, workday: GroupStats, holiday: GroupStats) -> str:
    """根据工作日/节假日两组统计生成一段综合分析文案。"""
    if holiday.days == 0:
        return (
            f"统计区间（{start} ~ {end}）内没有节假日样本（共 {workday.days} 个工作日），"
            "无法做工作日与节假日对比；扩大日期范围后此处会自动生成对比结论。"
        )
    if workday.days == 0:
        return (
            f"统计区间（{start} ~ {end}）内全部为节假日（共 {holiday.days} 天），"
            "缺少工作日基期，无法对比；建议扩大日期范围。"
        )

    parts: list[str] = [
        f"统计区间（{start} ~ {end}）内共 {workday.days} 个工作日、{holiday.days} 个节假日。"
    ]

    # 成交金额
    r_amt = _pct(holiday.amount_avg, workday.amount_avg)
    if r_amt is None:
        parts.append("工作日成交金额基期为 0，成交金额维度无法对比。")
    else:
        parts.append(
            f"工作日日均成交金额 {_money(workday.amount_avg)}，节假日日均 {_money(holiday.amount_avg)}，"
            f"节假日{_dir_word(r_amt)}（{_fmt_pct(r_amt)}，{_magnitude(r_amt)}波动）。"
        )

    # 访客数
    r_vis = _pct(holiday.visitors_avg, workday.visitors_avg)
    if r_vis is not None:
        parts.append(
            f"流量端：工作日日均访客 {workday.visitors_avg:,.0f}，节假日日均 {holiday.visitors_avg:,.0f}"
            f"（{_fmt_pct(r_vis)}）。"
        )

    # 转化率
    cw, ch = workday.conversion_rate, holiday.conversion_rate
    if cw is not None and ch is not None:
        r_cv = _pct(ch, cw)
        if r_cv is not None:
            if r_cv > 0.02 and r_vis is not None and r_vis < -0.05:
                comment = "转化率逆势走高，说明节假日到访用户购买意向更明确，流量回落主要来自曝光收窄而非承接能力下降"
            elif r_cv < -0.02 and r_vis is not None and r_vis > 0.05:
                comment = "转化未能跟上流量涨幅，节假日流量质量或页面承接存在优化空间"
            elif abs(r_cv) <= 0.02:
                comment = "转化水平基本稳定，工作日与节假日用户意向差异不大"
            else:
                comment = "转化率与流量同向波动，属正常经营节奏"
            parts.append(
                f"转化率：工作日 {_conv(cw)}，节假日 {_conv(ch)}（{_fmt_pct(r_cv)}），{comment}。"
            )

    # 退款
    w_share = (workday.refund_amount_avg / workday.amount_avg) if workday.amount_avg else None
    h_share = (holiday.refund_amount_avg / holiday.amount_avg) if holiday.amount_avg else None
    if w_share is not None and h_share is not None:
        if h_share > w_share * 1.2 and h_share > 0.01:
            after = "节假日退款金额占比偏高，需关注节假日订单的售后与发货时效"
        else:
            after = "退款占比在工作日与节假日之间基本持平，售后表现稳定"
        parts.append(
            f"退款端：工作日日均退款 {workday.refund_orders_avg:.1f} 单 / {_money(workday.refund_amount_avg)}"
            f"（占成交金额 {w_share * 100:.1f}%），节假日 {holiday.refund_orders_avg:.1f} 单 / "
            f"{_money(holiday.refund_amount_avg)}（{h_share * 100:.1f}%），{after}。"
        )

    # 推广
    wc, hc = workday.promotion_cost_avg, holiday.promotion_cost_avg
    if (wc or 0) or (hc or 0):
        wr, hr = workday.promotion_ratio, holiday.promotion_ratio
        wi, hi = workday.roi, holiday.roi
        if wc and hc:
            r_pc = _pct(hc, wc)
            parts.append(
                f"推广端：工作日日均推广费 {_money(wc)}（推广占比 {_ratio_pct(wr)}、ROI {_roi_fmt(wi)}），"
                f"节假日日均 {_money(hc)}（推广占比 {_ratio_pct(hr)}、ROI {_roi_fmt(hi)}），"
                f"节假日推广费{_dir_word(r_pc)}（{_fmt_pct(r_pc) if r_pc is not None else ''}）。"
            )
            if r_pc is not None and r_pc > 0.15 and hi is not None and wi is not None and hi < wi * 0.9:
                parts.append("建议：节假日明显加投但 ROI 下滑，需复盘推广结构（关键词/人群/时段），避免单纯放量摊薄效率。")
            elif r_pc is not None and r_pc < -0.15:
                parts.append("建议：节假日推广收缩，若自然流量不足以承接，可适度补投以保持曝光。")
            # 持平情况不单独给建议，避免与末尾总建议重复
        else:
            parts.append("推广费仅覆盖统计区间部分日期，工作日/节假日推广对比样本不足，未做推断。")

    # 建议
    if r_vis is not None and cw is not None and ch is not None:
        if r_vis < -0.05 and ch >= cw:
            parts.append("建议：节假日可适度补充搜索/推荐投放，承接高意向流量，弥补自然曝光缺口。")
        elif r_vis > 0.05 and ch < cw * 0.98:
            parts.append("建议：节假日重点排查流量来源结构与商详承接，避免增量流量稀释整体转化。")
        else:
            parts.append("建议：工作日与节假日经营节奏差异有限，维持现有投放与运营节奏即可。")

    return "".join(parts)
