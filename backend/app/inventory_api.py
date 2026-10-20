# -*- coding: utf-8 -*-
"""库存管理 API：/api/inventory/meta、/rows、/summary。

数据直接查 inventory_daily（SQLite 宽表），不经 JSON 中转；
单日快照按 (shop, date) 过滤，规模小（数百行），查询即返回。

字段与 inventory_metrics 对齐：前端用 meta.metrics 动态构建矩阵，
绝不把指标写死在前端。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..jobs import db as jobdb
from ..jobs.inventory_metrics import CITIES, METRICS, all_columns

router = APIRouter(prefix="/api/inventory")

_COL_NAMES = [c[0] for c in all_columns()]


class InvRow(BaseModel):
    """单条库存行（列名 → 值）。"""
    model_config = {"extra": "allow"}

    sku: Optional[str] = None
    sku_name: Optional[str] = None


def _conn():
    return jobdb.get_conn()


def _query_rows(shop: Optional[str], date: Optional[str]) -> list[dict]:
    """按 (shop, date) 拉取库存行（返回 dict 列表）。"""
    conn = _conn()
    try:
        where = []
        params: list[str] = []
        if shop:
            where.append("shop=?")
            params.append(shop)
        if date:
            where.append("date=?")
            params.append(date)
        sql = "SELECT * FROM inventory_daily"
        if where:
            sql += " WHERE " + " AND ".join(where)
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _compute_totals(data: list[dict]) -> dict[str, Optional[float]]:
    """按 meta.metrics 计算合计行：可累加指标求和，不合计显 None。"""
    totals: dict[str, Optional[float]] = {}
    for m in METRICS:
        if m["kind"] == "identity":
            totals[m["key"]] = None
        elif m["aggregatable"]:
            s = 0.0
            for d in data:
                for f in m["fields"]:
                    v = d.get(f)
                    if isinstance(v, (int, float)):
                        s += float(v)
            totals[m["key"]] = round(s, 2)
        else:
            totals[m["key"]] = None
    return totals


def _compute_city_totals(data: list[dict]) -> dict[str, list[float]]:
    """按「每城指标 → 每城求和」计算合计，供矩阵表每城子列底部合计使用。

    per_city 指标的 fields 按 CITIES 顺序排列，故下标 i 即对应 CITIES[i]。
    仅返回 per_city 指标（national/identity 不需要按城拆列）。
    """
    city_totals: dict[str, list[float]] = {}
    for m in METRICS:
        if m["kind"] != "per_city":
            continue
        sums = [0.0] * len(m["fields"])
        for d in data:
            for i, f in enumerate(m["fields"]):
                v = d.get(f)
                if isinstance(v, (int, float)):
                    sums[i] += float(v)
        city_totals[m["key"]] = [round(s, 2) for s in sums]
    return city_totals


@router.get("/meta")
def get_meta() -> dict[str, Any]:
    """获取指标配置 + 可选店铺/日期列表（前端首屏拉一次）。"""
    conn = _conn()
    try:
        shops = [r["shop"] for r in conn.execute(
            "SELECT DISTINCT shop FROM inventory_daily ORDER BY shop")]
        dates = [r["date"] for r in conn.execute(
            "SELECT DISTINCT date FROM inventory_daily ORDER BY date DESC")]
    finally:
        conn.close()
    return {"metrics": METRICS, "cities": CITIES, "shops": shops, "dates": dates}


@router.get("/rows")
def get_rows(
    shop: Optional[str] = Query(None, description="店铺名；空=全部店铺"),
    date: Optional[str] = Query(None, description="快照日期 YYYY-MM-DD；空=全部日期"),
    keyword: Optional[str] = Query(None, description="SKU / 商品名称 / SPU 模糊搜索"),
    sort_by: Optional[str] = Query(None, description="DB 字段名（必须是合法列名）"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """库存分页明细：按 shop/date/keyword 过滤、排序、分页，并附带合计行。"""
    data = _query_rows(shop, date)
    if keyword:
        kw = keyword.strip().lower()
        data = [
            d for d in data
            if kw in (str(d.get("sku") or "")).lower()
            or kw in (str(d.get("sku_name") or "")).lower()
            or kw in (str(d.get("spu") or "")).lower()
        ]
    if sort_by and sort_by in _COL_NAMES:
        data.sort(
            key=lambda d: (d.get(sort_by) is None, d.get(sort_by) or 0),
            reverse=(sort_order == "desc"),
        )
    totals = _compute_totals(data)
    city_totals = _compute_city_totals(data)
    total = len(data)
    start = (page - 1) * page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": data[start:start + page_size],
        "totals": totals,
        "city_totals": city_totals,
    }


@router.get("/summary")
def get_summary(
    shop: Optional[str] = Query(None),
    date: Optional[str] = Query(None),
) -> dict[str, Any]:
    """当前筛选范围的 KPI 聚合（供顶部卡片展示）。"""
    data = _query_rows(shop, date)
    totals = _compute_totals(data)
    # 取聚合后可读的 KPI（数值已四舍五入）
    def _num(key: str) -> float:
        v = totals.get(key)
        return v if isinstance(v, (int, float)) else 0.0

    return {
        "total_skus": len(data),
        "stock": _num("city_stock"),          # 现货库存（8 城求和）
        "slow": _num("city_slow"),            # 滞销件数（8 城求和）
        "purchase": _num("city_purchase"),    # 采购未到货（8 城求和）
        "out_30": _num("city_out_30"),        # 近30日出库（8 城求和）
        "nat_slow": _num("nat_slow"),         # 全国滞销件数
    }
