# -*- coding: utf-8 -*-
"""数据 API：manifest / 模块分页明细 / summary / 状态 / 手动刷新。

设计要点：
- 明细 JSON 是层级结构（店铺->spu->日期->指标），这里加载后扁平化为行缓存，
  按 shop/date/keyword/sort 过滤分页，每页 200-500 行，绝不整模块推给前端；
- 模块配置 MODULE_CONFIGS 驱动前端展示字段，新增模块时在这里加配置即可。
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

# 指标 key -> 是否参与排序/展示。前后端字段对齐的唯一事实来源之一。
METRIC_KEYS = [
    "visitors", "buyers", "orders", "items", "amount", "avg_price",
    "search_impressions", "search_clicks", "search_click_rate",
    "promotion_cost", "promotion_amount",
    "conversion_rate", "roi", "promotion_ratio",
    "refund_orders", "refund_amount",
]


class DayMetric(BaseModel):
    """单 SPU 在某一日（区间模式下）的指标快照。"""

    date: str
    metrics: MetricRecord


class RowOut(BaseModel):
    """扁平化的表格行（层级 JSON 展开）。

    单日模式：days 仅含 1 项，date/metrics 同步保留（days[0]）以便前端直接取用；
    区间（范围）模式：days 为该 SPU 在所选日期区间内的逐日数据，区间内每一天都
    占一列（含没有任何数据的日期，对应 metrics 全为 None），方便前端按「指标×日期」
    铺成矩阵。date/metrics 在区间模式下为空串 / 全 None，前端改用 days。
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
    total: int
    page: int
    page_size: int
    rows: list[RowOut]


# ---------------- SPU 单品分析 ----------------
class SpuDailyPoint(BaseModel):
    date: str
    is_holiday: bool
    metrics: MetricRecord


class CompareGroupOut(BaseModel):
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
_lock = threading.RLock()  # 可重入：flatten_module 内部会再调用 load_module
_module_cache: dict[str, ModuleData] = {}
_summary_cache: dict[str, ModuleSummary] = {}
_flat_cache: dict[str, list[RowOut]] = {}


def _data_path(name: str) -> Path:
    p = config.DATA_DIR / name
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"数据文件不存在：{name}，请先运行批处理")
    return p


def load_module(module_id: str) -> ModuleData:
    if module_id not in _module_cache:
        with _lock:
            if module_id not in _module_cache:
                raw = _data_path(f"modules/{module_id}.json").read_text(encoding="utf-8")
                _module_cache[module_id] = ModuleData.model_validate_json(raw)
    return _module_cache[module_id]


def load_summary(module_id: str) -> ModuleSummary:
    if module_id not in _summary_cache:
        with _lock:
            if module_id not in _summary_cache:
                raw = _data_path(f"modules/{module_id}.summary.json").read_text(encoding="utf-8")
                _summary_cache[module_id] = ModuleSummary.model_validate_json(raw)
    return _summary_cache[module_id]


def flatten_module(module_id: str) -> list[RowOut]:
    """层级结构 -> 行列表（缓存）。"""
    if module_id not in _flat_cache:
        with _lock:
            if module_id not in _flat_cache:
                data = load_module(module_id)
                rows: list[RowOut] = []
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
    with _lock:
        _module_cache.clear()
        _summary_cache.clear()
        _flat_cache.clear()


# ---------------- 路由 ----------------
@router.get("/manifest", response_model=Manifest)
def get_manifest() -> Manifest:
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
    all_rows = flatten_module(module_id)
    all_dates = sorted({r.date for r in all_rows})

    # 解析有效日期区间：start+end 优先，其次兼容单日 date
    lo = start or date
    hi = end or date

    if lo and hi:
        date_list = [d for d in all_dates if lo <= d <= hi]
    elif lo:
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
        kw = keyword.strip().lower()
        rows = [r for r in rows if kw in r.spu.lower() or (r.spu_name and kw in r.spu_name.lower())]

    # 按 (shop, spu) 聚合为「逐日」实体
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
        if r.spu_name:
            g["spu_name"] = r.spu_name
        if r.category:
            g["category"] = r.category

    # 排序：单日取 days[0]，区间取区间求和；缺失值（None）整体挪到最后
    if sort_by:
        if sort_by in METRIC_KEYS:

            def metric_val(g: dict) -> Optional[float]:
                if len(date_list) <= 1:
                    m = g["dates"].get(date_list[0]) if date_list else None
                    return (m and getattr(m, sort_by)) or 0
                return sum((d and getattr(d, sort_by)) or 0 for d in g["dates"].values())

            seq = sorted(groups.values(), key=lambda g: metric_val(g) or 0, reverse=(sort_order == "desc"))
            seq = sorted(seq, key=lambda g: metric_val(g) is None)
        else:
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
    return load_summary(module_id)


# 单品分析接口参与按日期聚合的「可累加」原始指标
# （转化率 / 客单价 / 搜索点击率 / ROI / 推广占比等派生指标需先求和再相除，在下面重算）
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
    rows = [r for r in flatten_module(module_id) if r.spu == spu]
    if shop:
        rows = [r for r in rows if r.shop == shop]
    if not rows:
        raise HTTPException(status_code=404, detail=f"未找到 SPU：{spu}")

    # 同一 SPU 可能分布在多个店铺：按日期汇总（先求和再算转化率）
    by_date: dict[str, dict[str, float]] = {}
    for r in rows:
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
    p = config.DATA_DIR / "status.json"
    if not p.exists():
        return {"last_run_at": None, "success": False, "message": "尚未运行过批处理"}
    return json.loads(p.read_text(encoding="utf-8"))


@router.post("/refresh")
def refresh() -> dict[str, Any]:
    """手动触发重跑批处理（同步执行，完成后清缓存）。"""
    from ..jobs.run_batch import run_once

    status = run_once()
    clear_caches()
    return json.loads(status.model_dump_json())
