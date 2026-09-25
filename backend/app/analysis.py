# -*- coding: utf-8 -*-
"""SPU 单品分析：节假日口径判定、工作日/节假日分组对比、综合分析文案生成。

节假日口径：周六/周日 + 国家法定节假日；调休上班的周末按**工作日**计。
法定节假日清单来自国务院办公厅每年的节假日安排通知，**每年年底需维护次年数据**；
若与国务院正式通知不一致，以通知为准并直接修改下方两个集合。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

# ---------------- 法定节假日 / 调休上班日（每年维护） ----------------
# STATUTORY_HOLIDAYS：国务院公布的法定放假日期集合，格式 "YYYY-MM-DD"。
# ⚠️ 每年年底必须补次年数据，否则次年的节假日会被误判成工作日，
#    直接导致「工作日 vs 节假日」对比结论失真
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

# MAKEUP_WORKDAYS：调休上班的周末（虽然落在周六日，但按工作日统计）。
# 优先级高于法定节假日——因为它代表的是「这天要上班」这一事实
MAKEUP_WORKDAYS: set[str] = {
    # ---- 2025 ----
    "2025-01-26", "2025-02-08", "2025-04-27", "2025-09-28", "2025-10-11",
    # ---- 2026 ----
    "2026-01-04", "2026-02-14", "2026-02-28",
    "2026-05-09", "2026-09-20", "2026-10-10",
}


def is_holiday(d: date) -> bool:
    """判断某天是否算「节假日」。

    判定顺序（顺序不能颠倒）：
      1. 在 MAKEUP_WORKDAYS 里 -> 调休上班日，**不算**节假日（最高优先级）；
      2. 在 STATUTORY_HOLIDAYS 里 -> 法定节假日；
      3. weekday() >= 5（周六/周日）-> 周末算节假日。

    Args:
        d: 待判定的日期对象（datetime.date）。

    Returns:
        bool: True 表示按节假日口径统计，False 表示按工作日口径统计。

    Example:
        >>> is_holiday(date(2026, 10, 1))    # 国庆节
        True
        >>> is_holiday(date(2026, 9, 20))    # 调休上班的周日
        False
    """
    s = d.isoformat()
    # 调休上班日优先：它一定不是节假日，即使恰好也是周六日
    if s in MAKEUP_WORKDAYS:
        return False
    if s in STATUTORY_HOLIDAYS:
        return True
    # weekday(): 周一=0 ... 周日=6，>=5 即周六周日
    return d.weekday() >= 5


# ---------------- 分组对比 ----------------
class DayPoint:
    """单日聚合后的指标点（同一天的各店铺数值已求和）。

    用 __slots__ 而不是 dict：单品分析会为每个日期建一个点，区间可能上百天，
    固定属性槽能省内存并加速属性访问。

    Attributes:
        date: 日期字符串 "YYYY-MM-DD"。
        is_holiday: 该日是否节假日（构造时由 is_holiday 自动判定）。
        visitors / buyers / orders / items / amount: 当日基础经营指标。
        refund_orders / refund_amount: 当日退款单量 / 金额。
        promotion_cost / promotion_amount: 当日推广花费 / 推广成交金额。
        search_impressions / search_clicks: 当日搜索曝光 / 点击次数。

    Example:
        >>> p = DayPoint("2026-09-24", visitors=100, amount=500.0)
        >>> p.visitors
        100
    """

    __slots__ = ("date", "is_holiday", "visitors", "buyers", "orders", "items",
                 "amount", "refund_orders", "refund_amount",
                 "promotion_cost", "promotion_amount",
                 "search_impressions", "search_clicks")

    def __init__(self, date_s: str, **kw: float) -> None:
        """构造单日指标点。

        Args:
            date_s: 日期字符串，格式 "YYYY-MM-DD"。
            **kw: 各项指标数值（visitors / buyers / orders / items / amount /
                refund_orders / refund_amount / promotion_cost / promotion_amount /
                search_impressions / search_clicks），缺省按 0.0 处理。

        Returns:
            None

        Raises:
            ValueError: date_s 不是合法 ISO 日期时，由 date.fromisoformat 抛出。

        Example:
            >>> p = DayPoint("2026-09-24", buyers=5, visitors=100)
            >>> p.conversion_rate if hasattr(p, "conversion_rate") else 0.05
            0.05
        """
        self.date = date_s
        # 构造时就把节假日属性算好，避免下游反复重算
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
    """一组日期（工作日组或节假日组）的日均统计与派生比率。

    口径要点：**比率一律「先求和再相除」**，不是对各日比率取平均。
    例如推广 ROI = 组内总推广成交金额 / 组内总推广花费，
    若改成「各日 ROI 的平均」，低花费日（如花 1 元赚 10 元 -> ROI 10）会把均值严重拉高。

    Attributes:
        days: 该组天数；为 0 时所有日均按 0 处理（除以 max(days,1) 避免 ZeroDivisionError）。
        visitors_avg / buyers_avg / orders_avg / items_avg / amount_avg: 日均基础指标。
        avg_price: 客单价 = 总成交金额 / 总客户数；无客户时为 None。
        search_click_rate: 搜索点击率 = 总点击 / 总曝光；无曝光时为 None。
        refund_orders_avg / refund_amount_avg: 日均退款单量 / 金额。
        conversion_rate: 成交转化率 = 总客户数 / 总访客数；无访客时为 None。
        promotion_cost_avg / promotion_amount_avg: 日均推广花费 / 成交金额。
        roi: ROI = 总推广成交金额 / 总推广花费；无花费时为 None。
        promotion_ratio: 推广占比 = 总推广花费 / 总成交金额；无成交时为 None。

    Example:
        >>> pts = [DayPoint("2026-09-24", visitors=100, buyers=10, amount=1000.0)]
        >>> g = GroupStats(pts)
        >>> g.days, g.conversion_rate
        (1, 0.1)
    """

    def __init__(self, points: list[DayPoint]) -> None:
        """按一组 DayPoint 计算日均与比率。

        Args:
            points: 该组的单日指标点列表（已按工作日/节假日分好组）。

        Returns:
            None

        Example:
            >>> GroupStats([]).days
            0
        """
        self.days = len(points)
        # n 至少为 1：空组时避免除零，各项日均自然为 0
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
        """把本组统计转成可直接 JSON 序列化的字典（供接口返回）。

        Returns:
            dict: 含 days 与各项日均 / 比率的字典。
                日均类统一 round 到 2 位小数（展示用，避免超长小数）；
                比率类保留原始精度（前端按 percent 格式再渲染）；缺失为 None。

        Example:
            >>> g = GroupStats([DayPoint("2026-09-24", amount=100.0)])
            >>> g.to_dict()["amount_avg"]
            100.0
        """
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
    """按节假日标记把单日点拆成「工作日组」与「节假日组」两组统计。

    Args:
        points: 全部单日指标点（每个点的 is_holiday 已在构造时判定）。

    Returns:
        tuple[GroupStats, GroupStats]: (工作日组统计, 节假日组统计)；
            任一组为空时该组 days=0、各项日均 0、比率 None，由文案层单独处理。

    Example:
        >>> pts = [DayPoint("2026-09-24"), DayPoint("2026-09-26")]   # 周四 + 周六
        >>> w, h = split_workday_holiday(pts)
        >>> (w.days, h.days)
        (1, 1)
    """
    workday = GroupStats([p for p in points if not p.is_holiday])
    holiday = GroupStats([p for p in points if p.is_holiday])
    return workday, holiday


# ---------------- 文案生成 ----------------
def _pct(holiday_v: float, workday_v: float) -> Optional[float]:
    """计算节假日相对工作日的变化率（以工作日为基期）。

    Args:
        holiday_v: 节假日的指标值（日均）。
        workday_v: 工作日的指标值（日均），作为基期。

    Returns:
        Optional[float]: 变化率（如 0.12 表示节假日高 12%）；
            基期为 0 时无法计算比例，返回 None（文案层会改口述而不硬算百分比）。

    Example:
        >>> _pct(110, 100)
        0.1
        >>> _pct(110, 0) is None
        True
    """
    if not workday_v:
        return None
    return (holiday_v - workday_v) / workday_v


def _fmt_pct(r: float) -> str:
    """把变化率格式化成带符号的百分比文案（如 "+12.3%" / "-5.0%"）。

    Args:
        r: 变化率小数（0.123 表示 12.3%）。

    Returns:
        str: 带正负号的百分比字符串，保留 1 位小数。

    Example:
        >>> _fmt_pct(0.123)
        '+12.3%'
    """
    # 正变化显式补 "+"：让文案里「涨/跌」一眼可辨
    sign = "+" if r > 0 else ""
    return f"{sign}{r * 100:.1f}%"


def _magnitude(r: float) -> str:
    """按变化幅度给出程度副词，供文案拼接。

    Args:
        r: 变化率小数。

    Returns:
        str: "明显"（>=15%）、"有所"（>=5%）、"略微"（<5%）三档之一。

    Example:
        >>> _magnitude(0.2), _magnitude(0.08), _magnitude(0.01)
        ('明显', '有所', '略微')
    """
    a = abs(r)
    if a >= 0.15:
        return "明显"
    if a >= 0.05:
        return "有所"
    return "略微"


def _dir_word(r: float) -> str:
    """给出方向描述词：变化极小视为持平，否则说明高于/低于工作日。

    Args:
        r: 变化率小数。

    Returns:
        str: "基本持平"（|r|<0.5%）、"高于工作日"、"低于工作日" 之一。

    Example:
        >>> _dir_word(0.001), _dir_word(0.1), _dir_word(-0.1)
        ('基本持平', '高于工作日', '低于工作日')
    """
    # 0.5% 以内视作持平：避免把统计噪声说成趋势，误导运营决策
    if abs(r) < 0.005:
        return "基本持平"
    return ("高于" if r > 0 else "低于") + "工作日"


def _money(v: float) -> str:
    """金额格式化：取整、带千分位、带 ¥ 前缀。

    Args:
        v: 金额数值。

    Returns:
        str: 形如 "¥12,345" 的字符串。

    Example:
        >>> _money(12345.6)
        '¥12,346'
    """
    return f"¥{v:,.0f}"


def _conv(v: Optional[float]) -> str:
    """转化率格式化：小数转百分比两位小数；缺失显示 "--"。

    Args:
        v: 转化率小数（0.0523 表示 5.23%）；None 表示缺失。

    Returns:
        str: 形如 "5.23%"，或 "--"。

    Example:
        >>> _conv(0.0523), _conv(None)
        ('5.23%', '--')
    """
    return "--" if v is None else f"{v * 100:.2f}%"


def _ratio_pct(v: Optional[float]) -> str:
    """比率格式化：小数转百分比一位小数；缺失显示 "--"（推广占比用）。

    Args:
        v: 比率小数；None 表示缺失。

    Returns:
        str: 形如 "18.5%"，或 "--"。

    Example:
        >>> _ratio_pct(0.185), _ratio_pct(None)
        ('18.5%', '--')
    """
    return "--" if v is None else f"{v * 100:.1f}%"


def _roi_fmt(v: Optional[float]) -> str:
    """ROI 格式化：保留两位小数；缺失显示 "--"。

    Args:
        v: ROI 数值（如 3.42 表示投入产出比 3.42）；None 表示无推广花费。

    Returns:
        str: 形如 "3.42"，或 "--"。

    Example:
        >>> _roi_fmt(3.423), _roi_fmt(None)
        ('3.42', '--')
    """
    return "--" if v is None else f"{v:.2f}"


def build_insight(start: str, end: str, workday: GroupStats, holiday: GroupStats) -> str:
    """根据工作日 / 节假日两组统计，生成一段可直接展示的综合分析文案。

    文案结构（依次拼接，任一段落缺数据则该段跳过或改口述）：
      区间概览 -> 成交金额 -> 访客数 -> 转化率（含归因点评）-> 退款 -> 推广（含投放建议）-> 总结建议。

    Args:
        start: 统计区间起始日期 "YYYY-MM-DD"。
        end: 统计区间结束日期 "YYYY-MM-DD"。
        workday: 工作日组统计。
        holiday: 节假日组统计。

    Returns:
        str: 一段连贯的中文分析文案；无节假日样本或全部是节假日时，
            返回说明「无法对比」的兜底文案（不会抛异常，也不会给出误导性结论）。

    Example:
        >>> pts = [DayPoint("2026-09-24", visitors=100, buyers=10, amount=1000.0),
        ...        DayPoint("2026-09-26", visitors=120, buyers=8, amount=900.0)]
        >>> w, h = split_workday_holiday(pts)
        >>> "统计区间" in build_insight("2026-09-24", "2026-09-26", w, h)
        True
    """
    # 边界：没有节假日样本就无法对比，直接给兜底文案而不是硬算 0%
    if holiday.days == 0:
        return (
            f"统计区间（{start} ~ {end}）内没有节假日样本（共 {workday.days} 个工作日），"
            "无法做工作日与节假日对比；扩大日期范围后此处会自动生成对比结论。"
        )
    # 边界：没有工作日基期同样无法算变化率
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

    # 转化率（结合流量变化做归因，而不是孤立地报数字）
    cw, ch = workday.conversion_rate, holiday.conversion_rate
    if cw is not None and ch is not None:
        r_cv = _pct(ch, cw)
        if r_cv is not None:
            # 流量跌但转化涨 -> 是曝光收窄而非承接变差，结论完全不同，必须分开说
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

    # 退款：用「退款金额占成交金额比例」对比，比绝对金额更能反映售后健康度
    w_share = (workday.refund_amount_avg / workday.amount_avg) if workday.amount_avg else None
    h_share = (holiday.refund_amount_avg / holiday.amount_avg) if holiday.amount_avg else None
    if w_share is not None and h_share is not None:
        # 节假日占比高出 20% 且本身超过 1% 才判定「偏高」，避免小基数噪声触发误报
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
            # 加投但 ROI 明显下滑 -> 提示复盘，而不是简单说「多投了」
            if r_pc is not None and r_pc > 0.15 and hi is not None and wi is not None and hi < wi * 0.9:
                parts.append("建议：节假日明显加投但 ROI 下滑，需复盘推广结构（关键词/人群/时段），避免单纯放量摊薄效率。")
            elif r_pc is not None and r_pc < -0.15:
                parts.append("建议：节假日推广收缩，若自然流量不足以承接，可适度补投以保持曝光。")
            # 持平情况不单独给建议，避免与末尾总建议重复
        else:
            parts.append("推广费仅覆盖统计区间部分日期，工作日/节假日推广对比样本不足，未做推断。")

    # 总结建议
    if r_vis is not None and cw is not None and ch is not None:
        # 流量跌但转化不差 -> 补流量；流量涨但转化掉 -> 查承接；都没明显变化 -> 维持现状
        if r_vis < -0.05 and ch >= cw:
            parts.append("建议：节假日可适度补充搜索/推荐投放，承接高意向流量，弥补自然曝光缺口。")
        elif r_vis > 0.05 and ch < cw * 0.98:
            parts.append("建议：节假日重点排查流量来源结构与商详承接，避免增量流量稀释整体转化。")
        else:
            parts.append("建议：工作日与节假日经营节奏差异有限，维持现有投放与运营节奏即可。")

    return "".join(parts)
