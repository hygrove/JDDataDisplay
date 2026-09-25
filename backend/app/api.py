# -*- coding: utf-8 -*-
"""数据 API：manifest / 模块分页明细 / summary / 单品分析 / 状态 / 手动刷新。

设计要点：
- 明细 JSON 是层级结构（店铺->spu->日期->指标），这里加载后**扁平化为行缓存**，
  按 shop / date / keyword / sort 过滤分页，每页 200-500 行，**绝不整模块推给前端**；
- 三份缓存（模块 / 汇总 / 扁平行）都用「双重检查加锁」懒加载，
  `/api/refresh` 重跑批处理后统一 clear_caches() 失效；
- 新增模块时在这里加配置即可（路径参数 module_id 驱动，无需改路由）。
"""
from __future__ import annotations

import json
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..jobs import config
from ..jobs.models import Manifest, MetricRecord, ModuleData, ModuleSummary
from .analysis import DayPoint, build_insight, split_workday_holiday

router = APIRouter(prefix="/api")

# METRIC_KEYS：参与排序 / 展示的指标 key 全集。
# 前后端字段对齐的唯一事实来源之一——前端 frontend/src/metrics.ts 的 key 必须与之对应，
# 排序参数 sort_by 只接受这里列出的 key，避免把任意属性名拼进 getattr 造成意外行为
METRIC_KEYS = [
    "visitors", "buyers", "orders", "items", "amount", "avg_price",
    "search_impressions", "search_clicks", "search_click_rate",
    "promotion_cost", "promotion_amount",
    "conversion_rate", "roi", "promotion_ratio",
    "refund_orders", "refund_amount",
]


class DayMetric(BaseModel):
    """单 SPU 在某一日的指标快照（区间模式下逐日数组的一项）。

    Fields:
        date: 日期 "YYYY-MM-DD"。
        metrics: 当日的 MetricRecord；该日无数据时所有字段为 None。

    Raises:
        pydantic.ValidationError: metrics 不是合法 MetricRecord 时抛出。

    Example:
        >>> DayMetric(date="2026-09-24", metrics=MetricRecord(amount=100.0)).date
        '2026-09-24'
    """

    date: str
    metrics: MetricRecord


class RowOut(BaseModel):
    """扁平化的表格行（由层级 JSON 展开而来）。

    两种模式（由前端所选日期区间决定）：
      单日模式：days 仅含 1 项，date / metrics 同步保留（等于 days[0]），
               便于前端直接取用而不必再解一层数组；
      区间模式：days 为该 SPU 在所选区间内**逐日**的数据，区间里每一天都占一列
               （包括完全没有数据的日期，其 metrics 全为 None），
               方便前端铺成「指标 × 日期」矩阵。此时 date 为空串、metrics 全 None。

    Fields:
        shop: 店铺名。
        spu: SPU 编号。
        spu_name: 商品名称，可为空。
        category: 类目，可为空。
        image: 图片访问路径，可为空。
        date: 单日模式下的日期；区间模式为空串。
        metrics: 单日模式下的指标；区间模式为空 MetricRecord。
        days: 逐日指标数组（区间模式承载全部数据）。

    Raises:
        pydantic.ValidationError: 字段类型不匹配时抛出。

    Example:
        >>> row = RowOut(shop="钻芯旗舰店", spu="100123", date="2026-09-24")
        >>> row.days
        []
    """

    shop: str
    spu: str
    spu_name: Optional[str] = None
    category: Optional[str] = None
    image: Optional[str] = None
    date: str = ""
    metrics: MetricRecord = Field(default_factory=MetricRecord)
    days: list[DayMetric] = Field(default_factory=list)


class PagedRows(BaseModel):
    """分页结果包装（供前端无限滚动 / 翻页消费）。

    Fields:
        total: 过滤后的总条数（不是当页条数）。
        page: 当前页码，从 1 开始。
        page_size: 每页条数。
        rows: 当页行列表。

    Example:
        >>> PagedRows(total=0, page=1, page_size=300, rows=[]).total
        0
    """

    total: int
    page: int
    page_size: int
    rows: list[RowOut]


# ---------------- SPU 单品分析 ----------------
class SpuDailyPoint(BaseModel):
    """单品分析里的单日点：日期 + 是否节假日 + 当日指标。

    Fields:
        date: 日期 "YYYY-MM-DD"。
        is_holiday: 该日是否按节假日口径统计（供前端区分工作日/节假日）。
        metrics: 当日指标（已按该 SPU 的各店铺求和，比率已重算）。

    Example:
        >>> SpuDailyPoint(date="2026-09-26", is_holiday=True, metrics=MetricRecord()).is_holiday
        True
    """

    date: str
    is_holiday: bool
    metrics: MetricRecord


class CompareGroupOut(BaseModel):
    """工作日组 / 节假日组的日均统计输出（对应 analysis.GroupStats.to_dict()）。

    Fields:
        days: 该组天数。
        *_avg: 各项日均（visitors / buyers / orders / items / amount /
            search_impressions / refund_orders / refund_amount / promotion_cost / promotion_amount）。
        avg_price / search_click_rate / conversion_rate / roi / promotion_ratio:
            派生比率，「先求和再相除」得出；分母为 0 时为 None。

    Example:
        >>> g = CompareGroupOut(days=5, visitors_avg=100.0, buyers_avg=10.0,
        ...                     orders_avg=12.0, amount_avg=1000.0,
        ...                     refund_orders_avg=0.0, refund_amount_avg=0.0, conversion_rate=0.1)
        >>> g.conversion_rate
        0.1
    """

    days: int
    visitors_avg: float
    buyers_avg: float
    orders_avg: float = 0
    items_avg: float = 0
    amount_avg: float
    avg_price: Optional[float] = None
    search_impressions_avg: float = 0
    search_click_rate: Optional[float] = None
    refund_orders_avg: float
    refund_amount_avg: float
    conversion_rate: Optional[float]
    promotion_cost_avg: Optional[float] = None
    promotion_amount_avg: Optional[float] = None
    roi: Optional[float] = None
    promotion_ratio: Optional[float] = None


class SpuAnalysisOut(BaseModel):
    """单品分析接口的完整返回体。

    Fields:
        spu: SPU 编号。
        spu_name: 商品名称，可为空。
        category: 类目，可为空。
        image: 图片访问路径，可为空。
        shops: 该 SPU 出现的店铺名列表（未指定 shop 时可能多个）。
        date_range: 实际统计区间 [最早日, 最晚日]。
        daily: 逐日指标点列表。
        workday: 工作日组统计。
        holiday: 节假日组统计。
        insight: 自动生成的综合分析文案。

    Example:
        >>> out = SpuAnalysisOut(spu="100123", shops=["钻芯旗舰店"], date_range=["2026-09-24", "2026-09-24"],
        ...                      daily=[], workday=CompareGroupOut(days=1, visitors_avg=0, buyers_avg=0,
        ...                      orders_avg=0, amount_avg=0, refund_orders_avg=0, refund_amount_avg=0,
        ...                      conversion_rate=None), holiday=CompareGroupOut(days=0, visitors_avg=0,
        ...                      buyers_avg=0, orders_avg=0, amount_avg=0, refund_orders_avg=0,
        ...                      refund_amount_avg=0, conversion_rate=None), insight="")
        >>> out.spu
        '100123'
    """

    spu: str
    spu_name: Optional[str] = None
    category: Optional[str] = None
    image: Optional[str] = None
    shops: list[str]
    date_range: list[str]
    daily: list[SpuDailyPoint]
    workday: CompareGroupOut
    holiday: CompareGroupOut
    insight: str


# ---------------- 数据加载（带缓存，手动刷新时失效）----------------
# _lock 必须是**可重入**锁（RLock）：flatten_module 内部会再调用 load_module，
# 若用普通 Lock 会在同一线程内二次加锁时死锁
_lock = threading.RLock()
_module_cache: dict[str, ModuleData] = {}
_summary_cache: dict[str, ModuleSummary] = {}
_flat_cache: dict[str, list[RowOut]] = {}


def _data_path(name: str) -> Path:
    """校验并返回数据文件的绝对路径，文件不存在时转成 404。

    Args:
        name: 相对 DATA_DIR 的文件名（如 "manifest.json"、"modules/pop_spu_detail.json"）。

    Returns:
        Path: 存在的数据文件绝对路径。

    Raises:
        HTTPException: 404 —— 文件不存在时抛出，提示「请先运行批处理」，
            避免下游抛 FileNotFoundError 变成 500，让调用方误以为是服务故障。

    Example:
        >>> _data_path("manifest.json")  # doctest: +SKIP
        WindowsPath('E:/JDDataDisplay/backend/app/data/manifest.json')
    """
    p = config.DATA_DIR / name
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"数据文件不存在：{name}，请先运行批处理")
    return p


def load_module(module_id: str) -> ModuleData:
    """加载模块层级明细 JSON（带缓存）。

    双重检查加锁：先无锁读一次（快路径），未命中才加锁并在锁内再查一次，
    避免并发请求重复解析同一个大 JSON。

    Args:
        module_id: 模块标识。

    Returns:
        ModuleData: 该模块的层级数据（店铺->SPU->日期->指标）。

    Raises:
        HTTPException: 404 —— 对应模块的 JSON 文件不存在。
        pydantic.ValidationError: JSON 结构与 ModuleData 定义不符时抛出。

    Example:
        >>> data = load_module("pop_spu_detail")   # doctest: +SKIP
        >>> data.module_id                         # doctest: +SKIP
        'pop_spu_detail'
    """
    if module_id not in _module_cache:
        with _lock:
            if module_id not in _module_cache:
                raw = _data_path(f"modules/{module_id}.json").read_text(encoding="utf-8")
                _module_cache[module_id] = ModuleData.model_validate_json(raw)
    return _module_cache[module_id]


def load_summary(module_id: str) -> ModuleSummary:
    """加载模块汇总 JSON（带缓存），加锁与缓存策略同 load_module。

    Args:
        module_id: 模块标识。

    Returns:
        ModuleSummary: 该模块的图表汇总数据。

    Raises:
        HTTPException: 404 —— 汇总文件不存在。
        pydantic.ValidationError: JSON 结构与 ModuleSummary 定义不符时抛出。

    Example:
        >>> s = load_summary("pop_spu_detail")   # doctest: +SKIP
        >>> s.module_id                          # doctest: +SKIP
        'pop_spu_detail'
    """
    if module_id not in _summary_cache:
        with _lock:
            if module_id not in _summary_cache:
                raw = _data_path(f"modules/{module_id}.summary.json").read_text(encoding="utf-8")
                _summary_cache[module_id] = ModuleSummary.model_validate_json(raw)
    return _summary_cache[module_id]


def flatten_module(module_id: str) -> list[RowOut]:
    """把层级结构（店铺->SPU->日期）扁平化成「一行 = 一个 SPU 一天」的列表（带缓存）。

    为什么要扁平化：
      前端要按 shop / date / keyword 过滤并排序分页，层级结构无法直接做这件事；
      扁平成行后所有过滤排序都是简单的列表推导，且结果可缓存复用。

    Args:
        module_id: 模块标识。

    Returns:
        list[RowOut]: 扁平行列表；每行含 shop / spu / 名称 / 类目 / 图片 / date / metrics。

    Raises:
        HTTPException: 404 —— 模块明细文件不存在（由 load_module 抛出）。

    Example:
        >>> rows = flatten_module("pop_spu_detail")   # doctest: +SKIP
        >>> len(rows) >= 0                            # doctest: +SKIP
        True
    """
    if module_id not in _flat_cache:
        with _lock:
            if module_id not in _flat_cache:
                data = load_module(module_id)
                rows: list[RowOut] = []
                # 三层展开：店铺 -> SPU -> 日期，每天产出一行
                for shop_node in data.shops.values():
                    for spu_node in shop_node.spus.values():
                        for d, m in spu_node.dates.items():
                            rows.append(RowOut(
                                shop=shop_node.shop, spu=spu_node.spu,
                                spu_name=spu_node.spu_name, category=spu_node.category,
                                image=spu_node.image, date=d, metrics=m,
                            ))
                _flat_cache[module_id] = rows
    return _flat_cache[module_id]


def clear_caches() -> None:
    """清空全部数据缓存（模块 / 汇总 / 扁平行）。

    调用时机：`/api/refresh` 重跑批处理完成后必须调用，
    否则接口会继续返回旧数据，表现为「刷新了但页面没变」。

    Args:
        无。

    Returns:
        None

    Example:
        >>> clear_caches()
    """
    with _lock:
        _module_cache.clear()
        _summary_cache.clear()
        _flat_cache.clear()


# ---------------- 路由 ----------------
@router.get("/manifest", response_model=Manifest)
def get_manifest() -> Manifest:
    """获取模块清单（前端首屏第一个请求）。

    Returns:
        Manifest: 含生成时间与各模块的店铺列表、日期区间、SPU 数等元信息。

    Raises:
        HTTPException: 404 —— manifest.json 不存在（未跑过批处理）。

    Example:
        GET /api/manifest
    """
    raw = _data_path("manifest.json").read_text(encoding="utf-8")
    return Manifest.model_validate_json(raw)


@router.get("/module/{module_id}/rows", response_model=PagedRows)
def get_rows(
    module_id: str,
    shop: Optional[str] = Query(None, description="店铺名；空=全部店铺"),
    start: Optional[str] = Query(None, description="起始日期 YYYY-MM-DD（与 end 构成区间）"),
    end: Optional[str] = Query(None, description="截止日期 YYYY-MM-DD（与 start 构成区间）"),
    date: Optional[str] = Query(None, description="【兼容旧调用】单日 YYYY-MM-DD；与 start/end 二选一"),
    keyword: Optional[str] = Query(None, description="SPU 号或名称模糊搜索"),
    sort_by: Optional[str] = Query(None, description="指标 key 或 spu/shop"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(300, ge=1, le=500),
) -> PagedRows:
    """模块分页明细：按区间 / 店铺 / 关键词过滤，按指标排序后分页。

    Args:
        module_id: 模块标识（路径参数）。
        shop: 店铺名过滤；None 表示全部店铺。
        start: 区间起始日期；与 end 配对使用，优先级高于 date。
        end: 区间截止日期；与 start 配对使用。
        date: 【兼容旧调用】单日日期；仅传它时等价于 start=end=date。
        keyword: SPU 号或名称的模糊关键词（大小写不敏感）。
        sort_by: 排序字段，取值为 METRIC_KEYS 之一或 "spu" / "shop"；None 表示不排序。
        sort_order: 排序方向，"asc" 或 "desc"，默认 "desc"。
        page: 页码，从 1 开始。
        page_size: 每页条数，1~500，默认 300。

    Returns:
        PagedRows: 含 total（过滤后总数）与当页 rows。
            区间模式下每行的 days 铺满区间内每一天（缺失日指标为 None）。

    Raises:
        HTTPException: 404 —— 模块数据文件不存在（由 flatten_module 抛出）。

    Example:
        GET /api/module/pop_spu_detail/rows?start=2026-09-01&end=2026-09-24&sort_by=amount&page=1
    """
    all_rows = flatten_module(module_id)
    all_dates = sorted({r.date for r in all_rows})

    # 解析有效日期区间：start+end 优先，其次兼容单日 date（旧前端只传 date）
    lo = start or date
    hi = end or date

    if lo and hi:
        date_list = [d for d in all_dates if lo <= d <= hi]
    elif lo:
        # 只给了单日：该日必须在数据里，否则返回空区间而不是全区间
        date_list = [lo] if lo in all_dates else []
    else:
        date_list = all_dates  # 全区间

    # 行级过滤（范围 / 店铺 / 关键词）
    rows = all_rows
    if lo and hi:
        rows = [r for r in rows if lo <= r.date <= hi]
    elif lo:
        rows = [r for r in rows if r.date == lo]
    if shop:
        rows = [r for r in rows if r.shop == shop]
    if keyword:
        # 关键词统一转小写做包含匹配，避免中英文大小写差异导致搜不到
        kw = keyword.strip().lower()
        rows = [r for r in rows if kw in r.spu.lower() or (r.spu_name and kw in r.spu_name.lower())]

    # 按 (shop, spu) 聚合为「逐日」实体：同一 SPU 的多天数据合并成一行多列
    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        g = groups.get((r.shop, r.spu))
        if g is None:
            g = {
                "shop": r.shop,
                "spu": r.spu,
                "spu_name": r.spu_name,
                "category": r.category,
                "image": r.image,
                "dates": {},
            }
            groups[(r.shop, r.spu)] = g
        g["dates"][r.date] = r.metrics
        # 名称/类目以最后一次非空值为准（同一 SPU 不同日期导出可能不一致）
        if r.spu_name:
            g["spu_name"] = r.spu_name
        if r.category:
            g["category"] = r.category

    # 排序：单日取 days[0] 的值，区间取区间求和；缺失值（None）整体挪到最后
    if sort_by:
        if sort_by in METRIC_KEYS:

            def metric_val(g: dict) -> Optional[float]:
                if len(date_list) <= 1:
                    m = g["dates"].get(date_list[0]) if date_list else None
                    # `and ... or 0`：把 None 与 0 都归一成 0，避免排序时 None 与数字比较报错
                    return (m and getattr(m, sort_by)) or 0
                return sum((d and getattr(d, sort_by)) or 0 for d in g["dates"].values())

            seq = sorted(groups.values(), key=lambda g: metric_val(g) or 0, reverse=(sort_order == "desc"))
            # 二次稳定排序把「完全取不到值」的组挪到末尾，无论升降序
            seq = sorted(seq, key=lambda g: metric_val(g) is None)
        else:
            # 非指标字段（spu / shop 等字符串）直接按属性排
            seq = sorted(
                groups.values(),
                key=lambda g: getattr(g, sort_by, "") or "",
                reverse=(sort_order == "desc"),
            )
        groups_list = seq
    else:
        groups_list = list(groups.values())

    # 构造输出（区间内每一天都铺一列，缺数据日指标为 None）
    out_rows: list[RowOut] = []
    for g in groups_list:
        days = [DayMetric(date=d, metrics=g["dates"].get(d, MetricRecord())) for d in date_list]
        single = len(date_list) == 1
        out_rows.append(
            RowOut(
                shop=g["shop"],
                spu=g["spu"],
                spu_name=g["spu_name"],
                category=g["category"],
                image=g["image"],
                # 区间模式下 date / metrics 置空，前端改读 days
                date=date_list[0] if single else "",
                metrics=(days[0].metrics if single else MetricRecord()),
                days=days,
            )
        )

    total = len(out_rows)
    start_idx = (page - 1) * page_size
    return PagedRows(total=total, page=page, page_size=page_size, rows=out_rows[start_idx : start_idx + page_size])


@router.get("/module/{module_id}/summary", response_model=ModuleSummary)
def get_summary(module_id: str) -> ModuleSummary:
    """获取模块汇总（图表用：店铺×日期趋势、TOP SPU、店铺汇总）。

    Args:
        module_id: 模块标识（路径参数）。

    Returns:
        ModuleSummary: 汇总数据（带缓存）。

    Raises:
        HTTPException: 404 —— 汇总文件不存在。

    Example:
        GET /api/module/pop_spu_detail/summary
    """
    return load_summary(module_id)


# _ANALYSIS_SUM_KEYS：单品分析按日期聚合时参与「可累加」求和的原始指标。
# （转化率 / 客单价 / 搜索点击率 / ROI / 推广占比等派生指标必须先求和再相除，在下面重算）
_ANALYSIS_SUM_KEYS = ("visitors", "buyers", "orders", "items", "amount",
                      "search_impressions", "search_clicks",
                      "refund_orders", "refund_amount",
                      "promotion_cost", "promotion_amount")


@router.get("/module/{module_id}/spu/{spu}/analysis", response_model=SpuAnalysisOut)
def get_spu_analysis(
    module_id: str,
    spu: str,
    shop: Optional[str] = Query(None, description="店铺名；空=该 SPU 全部店铺合计"),
    start: Optional[str] = Query(None, description="起始日期 YYYY-MM-DD；空=数据最早日期"),
    end: Optional[str] = Query(None, description="截止日期 YYYY-MM-DD；空=数据最新日期"),
) -> SpuAnalysisOut:
    """单品分析：某 SPU 在选定区间内的逐日指标 + 工作日/节假日对比 + 综合分析文案。

    实现说明（性能）：
      这里**不是**重跑批处理，而是在已缓存的扁平行（_flat_cache）上做按需聚合，
      所以切区间 / 切 SPU 的响应是毫秒级；真正的重算只在 /api/refresh 时发生。

    Args:
        module_id: 模块标识（路径参数）。
        spu: SPU 编号（路径参数）。
        shop: 店铺名；None 表示该 SPU 跨全部店铺合计。
        start: 起始日期；None 表示不限（取数据最早日）。
        end: 截止日期；None 表示不限（取数据最新日）。

    Returns:
        SpuAnalysisOut: 逐日指标、工作日组、节假日组、分析文案。

    Raises:
        HTTPException: 404 —— 该 SPU 不存在，或在所选日期范围内没有数据。

    Example:
        GET /api/module/pop_spu_detail/spu/100123/analysis?start=2026-09-18&end=2026-09-24
    """
    rows = [r for r in flatten_module(module_id) if r.spu == spu]
    if shop:
        rows = [r for r in rows if r.shop == shop]
    if not rows:
        raise HTTPException(status_code=404, detail=f"未找到 SPU：{spu}")

    # 同一 SPU 可能分布在多个店铺：按日期汇总各原始指标（比率随后统一重算）
    by_date: dict[str, dict[str, float]] = {}
    for r in rows:
        # 日期是 YYYY-MM-DD 字符串，字典序即时间序，可直接比较
        if start and r.date < start:
            continue
        if end and r.date > end:
            continue
        acc = by_date.setdefault(r.date, {k: 0.0 for k in _ANALYSIS_SUM_KEYS})
        for k in _ANALYSIS_SUM_KEYS:
            acc[k] += getattr(r.metrics, k) or 0
    if not by_date:
        raise HTTPException(status_code=404, detail=f"SPU {spu} 在所选日期范围内没有数据")

    dates = sorted(by_date)
    points = [DayPoint(d, **by_date[d]) for d in dates]
    # 逐日重算派生比率：保持与模块页、明细表完全一致的「先求和再相除」口径
    daily = [
        SpuDailyPoint(
            date=p.date,
            is_holiday=p.is_holiday,
            metrics=MetricRecord(
                visitors=p.visitors,
                buyers=p.buyers,
                orders=p.orders,
                items=p.items,
                amount=p.amount,
                search_impressions=p.search_impressions,
                search_clicks=p.search_clicks,
                promotion_cost=p.promotion_cost,
                promotion_amount=p.promotion_amount,
                refund_orders=p.refund_orders,
                refund_amount=p.refund_amount,
                conversion_rate=(p.buyers / p.visitors) if p.visitors else None,
                avg_price=(p.amount / p.buyers) if p.buyers else None,
                search_click_rate=(p.search_clicks / p.search_impressions) if p.search_impressions else None,
                roi=(p.promotion_amount / p.promotion_cost) if p.promotion_cost else None,
                promotion_ratio=(p.promotion_cost / p.amount) if p.amount else None,
            ),
        )
        for p in points
    ]

    workday, holiday = split_workday_holiday(points)
    # 元信息（名称/类目/图片）取第一行即可：同一 SPU 各店铺各日期的这些字段是一致的
    first = rows[0]
    return SpuAnalysisOut(
        spu=spu,
        spu_name=first.spu_name,
        category=first.category,
        image=first.image,
        shops=sorted({r.shop for r in rows}),
        date_range=[dates[0], dates[-1]],
        daily=daily,
        workday=CompareGroupOut(**workday.to_dict()),
        holiday=CompareGroupOut(**holiday.to_dict()),
        insight=build_insight(dates[0], dates[-1], workday, holiday),
    )


@router.get("/status")
def get_status() -> dict[str, Any]:
    """获取上次批处理运行状态（供前端展示「上次更新时间 / 是否成功」）。

    Returns:
        dict[str, Any]: status.json 的内容；文件不存在时返回
            {"last_run_at": None, "success": False, "message": "尚未运行过批处理"}，
            刻意不抛 404——「没跑过」是正常初始状态，不应让前端报错。

    Example:
        GET /api/status
    """
    p = config.DATA_DIR / "status.json"
    if not p.exists():
        return {"last_run_at": None, "success": False, "message": "尚未运行过批处理"}
    return json.loads(p.read_text(encoding="utf-8"))


@router.post("/refresh")
def refresh() -> dict[str, Any]:
    """手动触发重跑批处理（同步执行，完成后清缓存）。

    Returns:
        dict[str, Any]: 本次批处理的 BatchStatus 内容（success / message / 耗时等）。

    Raises:
        无（run_once 内部已捕获异常并转成失败状态返回）。

    Side Effects:
        同步执行完整批处理（耗时可能数十秒），完成后清空全部数据缓存。

    Example:
        POST /api/refresh
    """
    # 延迟导入：避免仅启动 API 服务时就加载整个批处理依赖链（polars / openpyxl 等）
    from ..jobs.run_batch import run_once

    status = run_once()
    # 必须清缓存，否则接口继续返回旧数据，表现为「刷新了但页面没变」
    clear_caches()
    return json.loads(status.model_dump_json())
