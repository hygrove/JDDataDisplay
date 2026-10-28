# -*- coding: utf-8 -*-
"""库存管理 API：/api/inventory/meta、/rows、/summary、/slow-cities、/distributions。

/distributions 是「结构分布卡」的数据源：SKU 覆盖、上下柜、三级类目、销售员
四个维度，全部带店铺拆分；统计单位为去重后的 (店铺, SKU)。

数据直接查 inventory_daily（SQLite 宽表），不经 JSON 中转；
单日快照按 (shop, date) 过滤，规模小（数百行），查询即返回。

字段与 inventory_metrics 对齐：前端用 meta.metrics 动态构建矩阵，
绝不把指标写死在前端。

/rows 的每行按需附加两个可选字段：
  - slow_cities：全城市滞销明细（仅 >0 的城市）；
  - sku_pic：库存 SKU 商品图访问路径（形如 "/inv-pics/jpzy/100080186911.jpg"），
    仅当该 SKU 在 ResourceData/inventory_sku_pic 下命中图片时存在，无图行不返回。
    由后端建索引后下发，前端不猜路径、不做多目录试探。
"""
from __future__ import annotations

import io
import sqlite3
from typing import Any, Literal, Optional, Union
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from . import matrix_export
from . import restock_export
from ..jobs import db as jobdb
from ..jobs import erp_map as erp_map_mod
from ..jobs import inv_pics
from ..jobs.alert_rules import (
    ALERT,
    ALLOWED_WINDOWS,
    HEALTHY,
    INCOMING,
    DEFAULT_DAYS,
    DEFAULT_SAFETY,
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW,
    BURST_CAP,
    CRED_MIN_DAYS,
    REF_WINDOW,
    VELOCITY_NONE,
    alert_status,
    clamp_params,
    credible_velocity,
    is_offshelf,
    reference_velocity,
    restock_qty,
    stockout_gap,
    velocity_example,
)
from ..jobs.inventory_metrics import CITIES, CITY_CODES, METRICS

router = APIRouter(prefix="/api/inventory")


class InvRow(BaseModel):
    """单条库存行（列名 → 值）。"""
    model_config = {"extra": "allow"}

    sku: Optional[str] = None
    sku_name: Optional[str] = None


def _conn():
    return jobdb.get_conn()


def _filter_keyword(data: list[dict], keyword: Optional[str]) -> list[dict]:
    """按关键词过滤行：SKU / 商品名称 / SPU 任一命中即算命中。

    关键词口径是**矩阵表与补货清单共用的唯一实现**（两个端点调用同一个函数），
    避免"矩阵能搜到、补货搜不到"这类口径漂移。

    Args:
        data: 库存行列表。
        keyword: 关键词；空 = 不过滤。

    Returns:
        list[dict]: 命中关键词的行（顺序保持不变）。
    """
    if not keyword or not keyword.strip():
        return data
    kw = keyword.strip().lower()
    return [
        d for d in data
        if kw in (str(d.get("sku") or "")).lower()
        or kw in (str(d.get("sku_name") or "")).lower()
        or kw in (str(d.get("spu") or "")).lower()
    ]


def _query_rows(shop: Optional[str], date: Optional[str], fields: Optional[tuple[str, ...]] = None) -> list[dict]:
    """按 (shop, date) 拉取库存行（返回 dict 列表）；fields 指定时只投影这些列。

    为什么需要列投影：inventory_daily 是 184 列宽表，SELECT * 全列拉回实测
    618ms（2353 行）；只拉 8 个身份列降到 8.5ms。各端点按需传 fields，
    只有 /rows 与两个导出必须全列（见各调用点注释）。

    Args:
        shop: 店铺过滤；None = 全部店铺。
        date: 快照日期；None = 全部日期。
        fields: 要查询的列名元组；None = SELECT *（全列，旧行为）。

    Returns:
        list[dict]: 行列表；fields 指定时每行只含这些键。
    """
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
        cols = "*" if fields is None else ", ".join(f'"{f}"' for f in fields)
        sql = f"SELECT {cols} FROM inventory_daily"
        if where:
            sql += " WHERE " + " AND ".join(where)
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# 身份列（关键词过滤 / 聚合 / 行下发共用的最小列集）。
_IDENTITY_FIELDS: tuple[str, ...] = (
    "date", "shop", "sku", "sku_name", "spu", "category", "seller", "shelf_status",
)


# /summary 的 KPI 指标 key（inventory_metrics 的 key）。
# 投影列（_summary_fields）与响应装配（get_summary）都由它派生——
# KPI 增删只改这一处，列与响应自动跟上，不会漂移。
_SUMMARY_KEYS: tuple[str, ...] = (
    "city_stock", "city_slow", "city_purchase", "city_out_30", "nat_slow",
)


def _summary_fields() -> tuple[str, ...]:
    """/summary 的投影列：KPI 指标的 fields + 身份列（total_skus 用行数，不依赖列）。"""
    fields = set(_IDENTITY_FIELDS)
    for m in METRICS:
        if m["key"] in _SUMMARY_KEYS:
            fields.update(m["fields"])
    return tuple(sorted(fields))


def _alert_fields() -> tuple[str, ...]:
    """预警 / 补货共用的投影列：8 城 × {stock, purchase, out_w, avail_w, sellable_days} + 身份列。

    ⚠️ 与 _row_city_values 的取数公式同源：那边改用新字段（或 alert_rules 的
    ALLOWED_WINDOWS / REF_WINDOW 变更），这里必须同步——投影缺列不会报错，
    而是 get 返回 None → _num 归 0 → 预警 / 补货量静默算错。列名直接从
    alert_rules 常量派生，就是为了删掉「两处手写同一份清单」的漂移面。
    """
    fields = set(_IDENTITY_FIELDS)
    windows = sorted(set(ALLOWED_WINDOWS) | {REF_WINDOW})
    for code in CITY_CODES.values():
        for suf in ("stock", "purchase", "sellable_days"):
            fields.add(f"{code}_{suf}")
        for w in windows:
            fields.add(f"{code}_out_{w}")
            fields.add(f"{code}_avail_{w}")
    return tuple(sorted(fields))


def _split_multi(value: Optional[str]) -> set[str]:
    """把逗号分隔的多选参数拆成集合（去空白、去空串）。

    空参数 = 不限制（返回空集合，调用方直接跳过过滤）。
    """
    if not value:
        return set()
    return {v.strip() for v in value.split(",") if v.strip()}


def _filter_multi(data: list[dict], field: str, wanted: set[str]) -> list[dict]:
    """按「字段值 ∈ 选中集合」过滤（多选，OR 关系）。

    选中 "未标注" 时，额外匹配该字段为空的行——概览卡里空值统一显示为
    「未标注」，前端选项名与行内原值对不上，需要在这里兜住。
    """
    if not wanted:
        return data
    out: list[dict] = []
    for d in data:
        v = str(d.get(field) or "")
        if v in wanted or (not v and "未标注" in wanted):
            out.append(d)
    return out


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


def _sort_value_fn(m: dict) -> Any:
    """按指标构造「行的排序值」函数；无可用值返回 None（= 缺失档）。

    - per_city：8 城有限数值之和，与单元格末行「【合计】」同口径（`city_stock`
      即「现货库存」，前端点它表头时排序的就是这个合计值）；
    - national：单字段数值；
    - identity：文本（非空字符串）。
    布尔值按 int 计入（SQLite 里就是 0/1）；None / 空串 / 非数值一律算缺失。
    """
    if m["kind"] == "per_city":
        fields = m["fields"]

        def per_city(d: dict) -> Optional[float]:
            s = 0.0
            has = False
            for f in fields:
                v = d.get(f)
                if isinstance(v, (int, float)):
                    s += float(v)
                    has = True
            return s if has else None

        return per_city

    fld = m["fields"][0]
    if m["kind"] == "identity":

        def identity(d: dict) -> Optional[str]:
            v = d.get(fld)
            return str(v) if v is not None and v != "" else None

        return identity

    def national(d: dict) -> Optional[float]:
        v = d.get(fld)
        return float(v) if isinstance(v, (int, float)) else None

    return national


def _sort_rows(data: list[dict], sort_by: Optional[str], sort_order: Optional[str]) -> list[dict]:
    """对**全量**行排序（在分页切片之前调用，保证排序作用于整个结果集而非当页）。

    sort_by 传指标 key（inventory_metrics 的 key，非 DB 字段名）；未知 key 直接
    返回原序（不报错，前端传错也不会 500）。

    缺失档（空值 / 空串 / 脏数据）恒排末尾，升降序都一样——与前端表格里的
    「— 不参与排序」口径一致；`list.sort` 稳定，同值行保持原有相对次序。
    """
    if not sort_by:
        return data
    m = next((x for x in METRICS if x["key"] == sort_by), None)
    if m is None:
        return data
    value_of = _sort_value_fn(m)
    present: list[tuple[Any, dict]] = []
    missing: list[dict] = []
    for d in data:
        v = value_of(d)
        if v is None:
            missing.append(d)
        else:
            present.append((v, d))
    present.sort(key=lambda t: t[0], reverse=(sort_order or "desc").lower() != "asc")
    return [d for _, d in present] + missing


def _slow_cities_for(rows: list[dict]) -> dict[tuple[str, str, str], dict[str, float]]:
    """按 (date, shop, sku) 批量取全城市滞销明细（只含 >0 的城市）。

    数据来自 inventory_city_slow 长表（约 70 个城市/仓，不限 8 城白名单），
    供前端在「全国滞销件数」列里按行展示城市拆分。

    查询按 (date, shop) 分组、每组一条 ``sku IN (...)``：原来逐行点查，
    当页 50 行就是 50 条 SQL；首屏单日多店场景现在只有几条（走
    idx_inv_city_slow_lookup 的 (date, shop, sku) 前缀）。sku 数受
    page_size ≤ 500 限制，远低于 SQLite 999 参数上限，无需分批。
    """
    pairs = {(r.get("date"), r.get("shop"), r.get("sku")) for r in rows}
    pairs = {p for p in pairs if p[0] and p[1] and p[2]}
    if not pairs:
        return {}
    by_ds: dict[tuple[str, str], set[str]] = {}
    for d, s, k in pairs:
        by_ds.setdefault((d, s), set()).add(k)
    conn = _conn()
    try:
        out: dict[tuple[str, str, str], dict[str, float]] = {}
        for (d, s), skus in sorted(by_ds.items()):
            sku_list = sorted(skus)
            placeholders = ",".join("?" * len(sku_list))
            cur = conn.execute(
                "SELECT sku, city, slow_units FROM inventory_city_slow "
                f"WHERE date=? AND shop=? AND sku IN ({placeholders})",
                (d, s, *sku_list),
            )
            for r in cur.fetchall():
                v = float(r["slow_units"] or 0)
                if v > 0:
                    out.setdefault((d, s, r["sku"]), {})[r["city"]] = v
        return out
    finally:
        conn.close()


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
    category: Optional[str] = Query(None, description="三级类目；多个用逗号分隔，空=不限"),
    seller: Optional[str] = Query(None, description="销售员；多个用逗号分隔，空=不限"),
    shelf_status: Optional[str] = Query(None, description="上下柜状态；多个用逗号分隔，空=不限"),
    sort_by: Optional[str] = Query(None, description="排序指标 key（inventory_metrics 的 key）；空=不排序"),
    sort_order: str = Query("desc", description="排序方向：desc（默认） / asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """库存分页明细：按 shop/date/keyword/类目/销售员/上下柜过滤、排序、分页，并附带合计行。

    类目 / 销售员 / 上下柜三个维度是信息列的筛选条件（多选，逗号分隔）。

    排序发生在**分页之前**：作用于当前筛选范围内的全部行（不是当页 50 条），
    sort_by 取指标 key，`city_stock`（现货库存）按该行 8 城合计排序，
    与单元格末行「【合计】」同口径；缺失值恒排末尾。合计行取自全量数据，不受排序影响。

    ⚠️ 本端点必须**全列**查询（不传 fields 投影）：前端「指标配置」勾选切换
    不重新发请求（行数据要承载全部可勾选指标的字段），且合计 / 排序需要
    aggregatable 指标的全部 fields。响应体瘦身靠 GZip（184 列 JSON 实测
    181KB → 11KB），不靠裁列。
    """
    data = _query_rows(shop, date)
    data = _filter_keyword(data, keyword)
    data = _filter_multi(data, "category", _split_multi(category))
    data = _filter_multi(data, "seller", _split_multi(seller))
    data = _filter_multi(data, "shelf_status", _split_multi(shelf_status))
    data = _sort_rows(data, sort_by, sort_order)
    totals = _compute_totals(data)
    city_totals = _compute_city_totals(data)
    total = len(data)
    start = (page - 1) * page_size
    page_rows = data[start:start + page_size]
    # 行内全城市滞销明细（>0 才挂，减小响应体）
    slow_map = _slow_cities_for(page_rows)
    for r in page_rows:
        cities = slow_map.get((r.get("date"), r.get("shop"), r.get("sku")))
        if cities:
            r["slow_cities"] = cities
        # 库存 SKU 商品图：命中才挂（无图行不带该字段，前端渲染「暂无图片」占位）
        pic = inv_pics.pic_path(r.get("sku"))
        if pic:
            r["sku_pic"] = pic
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": page_rows,
        "totals": totals,
        "city_totals": city_totals,
    }


@router.get("/slow-cities")
def get_slow_cities(
    shop: Optional[str] = Query(None, description="店铺名；空=全部店铺"),
    date: Optional[str] = Query(None, description="快照日期 YYYY-MM-DD；空=全部日期"),
) -> dict[str, Any]:
    """全城市滞销聚合（不限于 8 城）：按当前筛选汇总每个城市的滞销件数，降序。

    core=true 表示该城市属于 8 城白名单（前端可高亮）。
    """
    conn = _conn()
    try:
        where: list[str] = []
        params: list[str] = []
        if shop:
            where.append("shop=?")
            params.append(shop)
        if date:
            where.append("date=?")
            params.append(date)
        sql = "SELECT city, SUM(slow_units) AS units FROM inventory_city_slow"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY city ORDER BY units DESC"
        cur = conn.execute(sql, params)
        cities = [
            {"city": r["city"], "units": float(r["units"] or 0), "core": r["city"] in CITIES}
            for r in cur.fetchall()
        ]
    finally:
        conn.close()
    return {"cities": cities}


@router.get("/summary")
def get_summary(
    shop: Optional[str] = Query(None),
    date: Optional[str] = Query(None),
) -> dict[str, Any]:
    """当前筛选范围的 KPI 聚合（供顶部卡片展示）。"""
    # 窄投影：KPI 只用 _SUMMARY_KEYS 指标的 fields（184 列全拉实测 618ms → 约 50ms）
    data = _query_rows(shop, date, _summary_fields())
    totals = _compute_totals(data)
    # 取聚合后可读的 KPI（数值已四舍五入）；key 清单见 _SUMMARY_KEYS
    def _num(key: str) -> float:
        v = totals.get(key)
        return v if isinstance(v, (int, float)) else 0.0

    vals = {key: _num(key) for key in _SUMMARY_KEYS}
    return {
        "total_skus": len(data),
        "stock": vals["city_stock"],          # 现货库存（8 城求和）
        "slow": vals["city_slow"],            # 滞销件数（8 城求和）
        "purchase": vals["city_purchase"],    # 采购未到货（8 城求和）
        "out_30": vals["city_out_30"],        # 近30日出库（8 城求和）
        "nat_slow": vals["nat_slow"],         # 全国滞销件数
    }


@router.get("/distributions")
def get_distributions(
    shop: Optional[str] = Query(None, description="店铺名；空=全部店铺"),
    date: Optional[str] = Query(None, description="快照日期 YYYY-MM-DD；空=全部日期"),
    keyword: Optional[str] = Query(None, description="SKU / 商品名称 / SPU 模糊搜索"),
) -> dict[str, Any]:
    """结构分布：SKU / 上下柜 / 三级类目 / 销售员 四个维度（含店铺拆分）。

    统计口径：以 (店铺, SKU) 为最小单位去重（多日期快照按最新一条取值），
    避免同一 SKU 因多天快照被重复计数；分母 = 当前筛选范围。

    Returns:
        dict[str, Any]: 见 InventoryDistributions 前端类型定义。
    """
    from collections import Counter, defaultdict

    # 窄投影：本端点只做身份列维度的聚合（keyword 过滤 / 四维度 Counter 全在
    # 身份列上），184 列全拉纯属浪费（实测 618ms → 8.5ms）。
    data = _query_rows(shop, date, _IDENTITY_FIELDS)
    if keyword:
        kw = keyword.strip().lower()
        data = [
            d for d in data
            if kw in (str(d.get("sku") or "")).lower()
            or kw in (str(d.get("sku_name") or "")).lower()
            or kw in (str(d.get("spu") or "")).lower()
        ]
    # 按日期升序遍历 → 同 (店铺, SKU) 后写覆盖先写，最终保留最新快照的属性
    data = sorted(data, key=lambda d: str(d.get("date") or ""))

    base: dict[tuple[str, str], dict[str, Any]] = {}
    for d in data:
        sku = str(d.get("sku") or "")
        if not sku:
            continue
        base[(d.get("shop") or "", sku)] = {
            "status": d.get("shelf_status"),
            "category": d.get("category"),
            "seller": d.get("seller"),
        }

    # ---- 店铺顺序（按首次出现）----
    shop_order: list[str] = []
    for d in data:
        s = d.get("shop") or ""
        if s and s not in shop_order:
            shop_order.append(s)

    sku_by_shop: Counter = Counter()
    status_by_shop: dict[str, Counter] = defaultdict(Counter)
    cat_by_shop: dict[str, Counter] = defaultdict(Counter)
    seller_by_shop: dict[str, Counter] = defaultdict(Counter)
    status_total: Counter = Counter()
    cat_total: Counter = Counter()
    cat_shops: dict[str, set] = defaultdict(set)
    seller_total: Counter = Counter()
    seller_shops: dict[str, set] = defaultdict(set)

    all_skus: set[str] = set()
    for (s, sku), rec in base.items():
        all_skus.add(sku)
        sku_by_shop[s] += 1
        st = rec["status"] or "未标注"
        status_by_shop[s][st] += 1
        status_total[st] += 1
        ct = rec["category"] or "未标注"
        cat_by_shop[s][ct] += 1
        cat_total[ct] += 1
        cat_shops[ct].add(s)
        se = rec["seller"] or "未标注"
        seller_by_shop[s][se] += 1
        seller_total[se] += 1
        seller_shops[se].add(s)

    total_sku = len(all_skus)
    shop_sku_total = sum(sku_by_shop.values())

    def _pct(part: int, whole: int) -> float:
        return round(part / whole, 4) if whole else 0.0

    # ---- 店铺明细（SKU 覆盖 / 上下柜 / 销售员）----
    shop_items: list[dict[str, Any]] = []
    for s in shop_order:
        seller_cnt = len(seller_by_shop.get(s, {}))
        shop_items.append({
            "shop": s,
            "sku_count": sku_by_shop.get(s, 0),
            "sku_share": _pct(sku_by_shop.get(s, 0), shop_sku_total),
            "shelf": dict(status_by_shop.get(s, {})),
            "shelf_total": sum(status_by_shop.get(s, {}).values()),
            "seller_count": seller_cnt,
            "sellers": [
                {"seller": name, "sku_count": cnt}
                for name, cnt in sorted(
                    seller_by_shop.get(s, {}).items(), key=lambda kv: (-kv[1], kv[0])
                )
            ],
        })

    # ---- 类目（按 SKU 数降序，含每店拆分）----
    category_items = [
        {
            "category": name,
            "sku_count": cat_total[name],
            "shop_count": len(cat_shops.get(name, set())),
            "by_shop": {s: cat_by_shop.get(s, {}).get(name, 0) for s in shop_order},
        }
        for name in sorted(cat_total, key=lambda k: (-cat_total[k], k))
    ]

    # ---- 销售员（按 SKU 数降序，含每店拆分）----
    seller_items = [
        {
            "seller": name,
            "sku_count": seller_total[name],
            "shop_count": len(seller_shops.get(name, set())),
            "by_shop": {s: seller_by_shop.get(s, {}).get(name, 0) for s in shop_order},
        }
        for name in sorted(seller_total, key=lambda k: (-seller_total[k], k))
    ]

    return {
        "scope": {
            "shops": shop_order,
            "sku_total": total_sku,
            "sku_rows": shop_sku_total,
            "detail_rows": len(data),
        },
        "sku": {
            "total": total_sku,
            "by_shop": [
                {"shop": it["shop"], "sku_count": it["sku_count"], "share": it["sku_share"]}
                for it in shop_items
            ],
        },
        "shelf": {
            "total": dict(status_total),
            "by_shop": [
                {"shop": it["shop"], "shelf": it["shelf"], "total": it["shelf_total"]}
                for it in shop_items
            ],
        },
        "category": {
            "count": len(category_items),
            "items": category_items,
        },
        "seller": {
            "person_count": len(seller_items),
            "person_times": sum(it["seller_count"] for it in shop_items),
            "items": seller_items,
            "by_shop": [
                {"shop": it["shop"], "seller_count": it["seller_count"], "sellers": it["sellers"]}
                for it in shop_items
            ],
        },
    }


# ============================== 库存预警与补货建议 ==============================
# 城市级阈值 + 三档预警（healthy/incoming/alert）+ 销速驱动补货清单。
# 计算规则全部在 backend.jobs.alert_rules（纯函数、有单测），本文件只做 IO 组装。

# 城市码 → 中文名（CITIES 展示顺序）
CODE_TO_CITY = {v: k for k, v in CITY_CODES.items()}


class ThresholdUpdate(BaseModel):
    """PUT /alerts/thresholds 请求体：城市码 → 阈值（只提交要改的城市即可）。"""

    thresholds: dict[str, float]


def _latest_date(conn) -> Optional[str]:
    """inventory_daily 最新快照日期（无数据返回 None）。"""
    r = conn.execute("SELECT MAX(date) AS d FROM inventory_daily").fetchone()
    return r["d"] if r and r["d"] else None


def _threshold_map() -> dict[str, float]:
    """读 8 城阈值（城市码 → 阈值）；表缺行（老库）回默认值，保证恒有 8 项。"""
    conn = _conn()
    try:
        try:
            rows = conn.execute("SELECT city, threshold FROM alert_thresholds").fetchall()
            m = {r["city"]: float(r["threshold"]) for r in rows}
        except Exception:
            m = {}
    finally:
        conn.close()
    for code in CITY_CODES.values():
        m.setdefault(code, DEFAULT_THRESHOLD)
    return m


def _num(v: Any) -> float:
    """行内数值字段安全取数：None/文本 → 0（ETL 缺失写 0，防御二次保险）。"""
    return float(v) if isinstance(v, (int, float)) else 0.0


def _row_city_values(
    row: dict, code: str, window: int
) -> tuple[float, float, float, float, float, float]:
    """从宽表行取某城市的 (现货, 在途, 窗口出库, 窗口有货天数, 30日参照出库, 30日参照有货天数)。

    30 日参照供可信销速收缩估算用（alert_rules.credible_velocity）：窗口选 30 日时
    参照与窗口是同一对字段，配合参照分母垫底逻辑天然自洽，无需特判。
    """
    return (
        _num(row.get(f"{code}_stock")),
        _num(row.get(f"{code}_purchase")),
        _num(row.get(f"{code}_out_{window}")),
        _num(row.get(f"{code}_avail_{window}")),
        _num(row.get(f"{code}_out_{REF_WINDOW}")),
        _num(row.get(f"{code}_avail_{REF_WINDOW}")),
    )


def _sku_pics(rows: list[dict]) -> dict[str, str]:
    """取这些行涉及 SKU 的商品图路径（命中图片索引的才有）。

    商品图是展示层的辅助产物：没有图不影响补货判定，只是行里没有 sku_pic。

    Args:
        rows: 库存行列表（每行含 sku）。

    Returns:
        dict[str, str]: SKU → 商品图访问路径；无图的 SKU 不在结果里。
    """
    skus = [str(r.get("sku") or "") for r in rows]
    if not skus:
        return {}
    return {sku: pic for sku in skus if (pic := inv_pics.pic_path(sku))}


def _erp_codes(rows: list[dict]) -> dict[str, str]:
    """取这些行涉及 SKU 的 ERP 编码映射。

    只有**已登记**的 SKU 才会出现在结果里；未登记的返回缺失，
    前端据此显示「--」（库里绝不把缺失写成 0，见 ADR-0009）。

    ERP 映射是展示层的附加信息：映射表缺失或损坏时返回空映射并告警，
    绝不让整个补货清单挂掉。

    Args:
        rows: 库存行列表（每行含 sku）。

    Returns:
        dict[str, str]: SKU → ERP 编码。
    """
    skus = [str(r.get("sku") or "") for r in rows]
    if not skus:
        return {}
    conn = _conn()
    try:
        return erp_map_mod.lookup_erp_codes(conn, skus)
    except sqlite3.Error as e:
        if "no such table" in str(e).lower():
            # 服务没重启 / 还没跑过批处理 → 表不存在。这是部署问题，不是"没登记"，
            # 必须说清楚，否则整列「--」会被误读成"所有人都没登记"。
            print("[erp_map] ERP 映射表不存在（服务未重启或尚未跑过批处理）——补货清单的 ERP 编码列显示「--」")
        else:
            print(f"[erp_map] ERP 编码查询失败（补货清单将显示「--」）：{type(e).__name__}: {e}")
        return {}
    except Exception as e:  # noqa: BLE001 —— 映射表异常不该拖垮补货清单
        print(f"[erp_map] ERP 编码查询失败（补货清单将显示「--」）：{type(e).__name__}: {e}")
        return {}
    finally:
        conn.close()


@router.get("/alerts/summary")
def get_alerts_summary(
    shop: Optional[str] = Query(None, description="店铺名；空=全部店铺"),
    date: Optional[str] = Query(None, description="快照日期；空=最新快照"),
) -> dict[str, Any]:
    """8 城预警汇总：每城阈值、红/黄/绿计数与建议补货合计（默认参数）。

    计数口径：下柜 SKU 完全排除；建议补货合计按默认参数（9 天 / 20% / 14 日），
    销速与补货清单同口径（有货天数不足可信门槛时按 30 日参照收缩，见 alert_rules）。
    返回的 cities 顺序与 CITIES（前端 meta.cities）一致，city=中文名、code=城市码。
    """
    thresholds = _threshold_map()
    conn = _conn()
    try:
        snap = date or _latest_date(conn)
    finally:
        conn.close()
    # 窄投影：预警计算只碰 8 城的 stock/purchase/out_w/avail_w（见 _alert_fields）
    data = _query_rows(shop, snap, _alert_fields())
    cities: list[dict[str, Any]] = []
    for city in CITIES:
        code = CITY_CODES[city]
        t = thresholds[code]
        red = yellow = green = 0
        restock_total = 0
        for row in data:
            if is_offshelf(row.get("shelf_status")):
                continue
            stock, purchase, out, avail, out_ref, avail_ref = _row_city_values(row, code, DEFAULT_WINDOW)
            st = alert_status(t, stock, purchase)
            if st == ALERT:
                red += 1
            elif st == INCOMING:
                yellow += 1
            else:
                green += 1
            v, _kind = credible_velocity(out, avail, out_ref, avail_ref)
            restock_total += restock_qty(v, DEFAULT_DAYS, DEFAULT_SAFETY, stock, purchase)
        cities.append({
            "city": city,
            "code": code,
            "threshold": t,
            "red": red,
            "yellow": yellow,
            "green": green,
            "restock_total": restock_total,
        })
    return {
        "date": snap,
        "cities": cities,
        "params": {"days": DEFAULT_DAYS, "safety": DEFAULT_SAFETY, "window": DEFAULT_WINDOW},
    }


@router.put("/alerts/thresholds")
def put_alert_thresholds(body: ThresholdUpdate) -> dict[str, Any]:
    """批量保存城市阈值（UPSERT，服务重启不丢；提交的城市码必须是 8 城之一）。

    阈值钳制：负数按 0 处理（0 = 该城不检测）。返回规范化后的全量阈值。
    """
    from datetime import datetime, timezone

    wanted = {c: max(0.0, float(v)) for c, v in body.thresholds.items() if c in CITY_CODES.values()}
    if not wanted:
        raise HTTPException(422, "thresholds 至少包含一个合法城市码（bj/wh/sh/gz/cd/xa/dz/sy）")
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.executemany(
            "INSERT INTO alert_thresholds (city, threshold, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(city) DO UPDATE SET threshold=excluded.threshold, updated_at=excluded.updated_at",
            [(c, v, now) for c, v in wanted.items()],
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "thresholds": _threshold_map()}


def _restock_long_rows(
    data: list[dict],
    thresholds: dict[str, float],
    days: float,
    safety: float,
    window: int,
    erp_codes: dict[str, str],
    pics: dict[str, str],
) -> list[dict[str, Any]]:
    """算出 (SKU × 城市) 粒度的补货行。

    行的含义与城市视图**完全一致**（同一套阈值与公式），区别只有两点：
    保留建议量为 0 的行、跳过完全没有数据的城市——这样 SKU 视图能看全
    "哪城缺、哪城不缺"，而不是把同一个 SKU 拆散到 8 个城市组里。

    Args:
        data: 库存行列表（已按店铺 / 日期 / 关键词过滤）。
        thresholds: 每城阈值。
        days: 覆盖天数。
        safety: 安全系数。
        window: 销速窗口天数。
        erp_codes: SKU → ERP 编码。
        pics: SKU → 商品图路径。

    Returns:
        list[dict[str, Any]]: 长表行（SKU × 城市），含 out（窗口出库）、avail（窗口有货天数）
        与 velocity_kind（销速估计方式 none/measured/shrunk：收缩估算的行前端标 ≈）。
    """
    rows: list[dict[str, Any]] = []
    for row in data:
        if is_offshelf(row.get("shelf_status")):
            continue
        sku = str(row.get("sku") or "")
        for city in CITIES:
            code = CITY_CODES[city]
            stock, purchase, out, avail, out_ref, avail_ref = _row_city_values(row, code, window)
            if stock <= 0 and purchase <= 0 and out <= 0:
                continue  # 该城没有任何数据，不占一行
            velocity, velocity_kind = credible_velocity(out, avail, out_ref, avail_ref)
            qty = restock_qty(velocity, days, safety, stock, purchase)
            # 缺口关注：整窗缺货（有货天数 0）时损失率改用参照销速——损失不是 0，
            # 而是被完全截断的需求
            v_ref = reference_velocity(out_ref, avail_ref, REF_WINDOW)
            gap_flag, lost_sales = stockout_gap(avail, window, velocity, v_ref)
            rows.append({
                "sku": row.get("sku"),
                "sku_name": row.get("sku_name"),
                "shop": row.get("shop"),
                "city": city,
                "category": row.get("category"),
                "shelf_status": row.get("shelf_status"),
                "spu": row.get("spu"),
                "seller": row.get("seller"),
                "erp_code": erp_codes.get(sku),
                "sku_pic": pics.get(sku),
                "sellable_days": row.get(f"{code}_sellable_days"),
                "stock": stock,
                "purchase": purchase,
                "out": out,
                "velocity": round(velocity, 2),
                "avail": avail,
                "velocity_kind": velocity_kind,
                "gap_flag": gap_flag,
                "lost_sales": lost_sales,
                "target": round(velocity * days * (1 + safety)),
                "qty": qty,
                "status": alert_status(thresholds[code], stock, purchase),
            })
    return rows


def _restock_payload(
    shop: Optional[str],
    date: Optional[str],
    keyword: Optional[str],
    days: Optional[float],
    safety: Optional[float],
    window: Optional[int],
) -> dict[str, Any]:
    """算出补货清单的全部数据（两个视图与导出共用同一份计算）。

    @remarks
    导出与清单查询共用这一个函数，是"所见即所得"的根本保证：
    参数夹取、阈值、销速、建议量、行范围只有一处实现。

    Args:
        shop: 店铺过滤；None = 全部店铺。
        date: 快照日期；None = 最新快照。
        keyword: 关键词（SKU / 商品名称 / SPU）。
        days: 覆盖天数；None / 越界 → 默认值。
        safety: 安全系数；None / 越界 → 默认值。
        window: 销速窗口天数；None / 越界 → 默认值。

    Returns:
        dict[str, Any]: {"date", "params", "rows"(长表), "city_groups", "grand_total", "sku_count"}。
    """
    d, s, w = clamp_params(days, safety, window)
    thresholds = _threshold_map()
    conn = _conn()
    try:
        snap = date or _latest_date(conn)
    finally:
        conn.close()
    # 窄投影：补货计算与预警同一套取数口径（_alert_fields 与 _row_city_values 同源）
    data = _filter_keyword(_query_rows(shop, snap, _alert_fields()), keyword)
    rows = _restock_long_rows(data, thresholds, d, s, w, _erp_codes(data), _sku_pics(data))
    order = {city: i for i, city in enumerate(CITIES)}
    sku_rows = sorted(rows, key=lambda r: (str(r["sku"]), order.get(str(r["city"]), len(order))))
    groups: list[dict[str, Any]] = []
    grand_total = 0
    for city in CITIES:
        code = CITY_CODES[city]
        crows = [r for r in rows if r["city"] == city and r["qty"] > 0]
        if not crows:
            continue
        crows.sort(key=lambda r: r["qty"], reverse=True)
        subtotal = sum(r["qty"] for r in crows)
        groups.append({
            "city": city,
            "code": code,
            "threshold": thresholds[code],
            "rows": crows,
            "subtotal": subtotal,
        })
        grand_total += subtotal
    return {
        "date": snap,
        "params": {
            "days": d,
            "safety": s,
            "window": w,
            # 销速算法阈值与演示算例：前端「销速算法说明」对话框原样展示，
            # 数字由后端真实函数算出（不复制公式），阈值只有一个来源
            "cred_min": CRED_MIN_DAYS,
            "burst_cap": BURST_CAP,
            "ref_window": REF_WINDOW,
            "algo_example": velocity_example(d, s),
        },
        "rows": sku_rows,
        "city_groups": groups,
        "grand_total": grand_total,
        "sku_count": len({str(r["sku"]) for r in sku_rows}),
    }


class RestockRowModel(BaseModel):
    """补货行（SKU × 城市粒度，两个视图与导出共用同一套装配）。"""

    model_config = {"extra": "allow"}

    sku: Optional[str] = None
    sku_name: Optional[str] = None
    shop: Optional[str] = None
    city: Optional[str] = None
    category: Optional[str] = None
    shelf_status: Optional[str] = None
    spu: Optional[str] = None
    seller: Optional[str] = None
    erp_code: Optional[str] = None
    sku_pic: Optional[str] = None
    sellable_days: Optional[float] = None
    stock: float = 0
    purchase: float = 0
    out: float = 0
    velocity: float = 0
    avail: float = 0
    velocity_kind: str = VELOCITY_NONE
    gap_flag: str = ""
    lost_sales: float = 0
    target: int = 0
    qty: int = 0
    status: str = "healthy"


class RestockParamsModel(BaseModel):
    """本次计算实际生效的参数（服务端夹取后的结果）。"""

    days: float
    safety: float
    window: int
    cred_min: int = CRED_MIN_DAYS
    burst_cap: float = BURST_CAP
    ref_window: int = REF_WINDOW
    algo_example: Optional[dict[str, float]] = None


class RestockCityGroupModel(BaseModel):
    """城市分组（城市视图的组：只含有建议量 > 0 的行）。"""

    city: str
    code: str
    threshold: float
    rows: list[RestockRowModel]
    subtotal: int = 0


class RestockCityResponse(BaseModel):
    """城市视图响应（view=city）。"""

    view: Literal["city"] = "city"
    date: Optional[str] = None
    params: RestockParamsModel
    groups: list[RestockCityGroupModel] = []
    grand_total: int = 0


class RestockSkuResponse(BaseModel):
    """SKU 视图响应（view=sku）：平铺长表。"""

    view: Literal["sku"] = "sku"
    date: Optional[str] = None
    params: RestockParamsModel
    rows: list[RestockRowModel] = []
    grand_total: int = 0
    sku_count: int = 0


@router.get("/restock", response_model=Union[RestockCityResponse, RestockSkuResponse])
def get_restock(
    shop: Optional[str] = Query(None, description="店铺名；空=全部店铺"),
    date: Optional[str] = Query(None, description="快照日期；空=最新快照"),
    keyword: Optional[str] = Query(None, description="SKU / 商品名称 / SPU 模糊搜索（口径与矩阵表一致）"),
    view: str = Query("city", description="视图：city=按城市分组 / sku=平铺长表（同一份计算的两种装配）"),
    days: Optional[float] = Query(None, description="覆盖天数；默认 9，域 [1,60]"),
    safety: Optional[float] = Query(None, description="安全系数；默认 0.2（20%）"),
    window: Optional[int] = Query(None, description="销速窗口天数；7/14/30，默认 14"),
) -> Union[RestockCityResponse, RestockSkuResponse]:
    """补货建议清单：销速驱动目标库存 − 现货 − 在途。

    两个视图共用同一份计算（_restock_payload），只是装配方式不同：
    - view=city：按城市分组，组内按建议量降序，每组小计 + 8 城总计（只含建议量 > 0 的行）；
    - view=sku：平铺长表（每行一个 SKU × 城市），按 SKU 编码升序、同 SKU 按城市白名单顺序，
      保留建议量为 0 的行，便于看全"哪城缺、哪城不缺"。

    下柜 SKU 完全排除；合计口径与前端展示一致。None/越界参数被夹回默认值；
    非数字串由 Query 类型验证拦下（422）。关键词过滤与矩阵表共用同一套口径。
    每行含 avail（窗口有货天数）与 velocity_kind（销速估计方式）：有货天数不足
    可信门槛时销速按 30 日参照收缩估算（kind=shrunk），防积压爆发把销速顶上天。
    """
    v = view if view in ("city", "sku") else "city"
    payload = _restock_payload(shop, date, keyword, days, safety, window)
    if v == "sku":
        return {
            "view": "sku",
            "date": payload["date"],
            "params": payload["params"],
            "rows": payload["rows"],
            "grand_total": payload["grand_total"],
            "sku_count": payload["sku_count"],
        }
    return {
        "view": "city",
        "date": payload["date"],
        "params": payload["params"],
        "groups": payload["city_groups"],
        "grand_total": payload["grand_total"],
    }


@router.api_route(
    "/restock/export",
    methods=["GET", "POST"],
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
            "description": "补货清单 xlsx（含「按城市」与「按SKU」两个 sheet + 商品图）",
        }
    },
)
def export_restock(
    shop: Optional[str] = Query(None, description="店铺名；空=全部店铺"),
    date: Optional[str] = Query(None, description="快照日期；空=最新快照"),
    keyword: Optional[str] = Query(None, description="SKU / 商品名称 / SPU 模糊搜索（口径与矩阵表一致）"),
    days: Optional[float] = Query(None, description="覆盖天数；默认 9，域 [1,60]"),
    safety: Optional[float] = Query(None, description="安全系数；默认 0.2（20%）"),
    window: Optional[int] = Query(None, description="销速窗口天数；7/14/30，默认 14"),
) -> Response:
    """导出补货清单（xlsx，两个 sheet + 商品图嵌入）。

    @remarks
    - 同时支持 GET 与 POST：GET 供浏览器**原生下载**（<a href> 直连，文件名取自
      Content-Disposition），POST 供程序化调用（测试、脚本）。参数全在查询串里，
      两种方法语义完全一致。
    - 两个 sheet：「按城市」（分组头 + 小计 + 总计）与「按SKU」（明细长表）；
    - 商品图嵌进单元格区域（openpyxl 只支持 png/jpeg/gif，因此用原图 jpg 而不是 avif 缩略图）；
    - ERP 编码缺失时写 0（下游模板占位；页面显示「--」）；
    - 加 no-store：导出是"此刻的快照"，绝不能被浏览器缓存成旧文件；
    - **失败也返回附件**（错误说明 txt + 500）：前端用 <a href> 触发下载，若这里返回
      普通 JSON 错误，浏览器会把当前工作台页面导航到错误页上，用户的筛选状态全丢。
      返回附件则浏览器照样下载，页面纹丝不动。
    """
    try:
        payload = _restock_payload(shop, date, keyword, days, safety, window)
        content = restock_export.build_workbook(payload)
    except Exception as e:  # noqa: BLE001 —— 导出失败要给出可读说明，而不是 500 页面
        message = f"补货清单导出失败：{type(e).__name__}: {e}\n"
        filename = f"补货导出失败_{date or '最新'}.txt"
        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        return Response(
            content=message.encode("utf-8"),
            status_code=500,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": disposition, "Cache-Control": "no-store"},
        )
    filename = f"补货清单_{payload['date']}.xlsx"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "no-store",
        },
    )


def _resolve_export_metrics(metrics: Optional[str]) -> list[dict]:
    """把查询串里的指标 key 解析成 meta 顺序的指标列表（矩阵导出的列定义）。

    Args:
        metrics: 逗号分隔的指标 key（前端「指标配置」勾选）；空 = 用后端默认勾选
            （default_visible 且非锁定，与矩阵表开屏列一致）。

    Returns:
        list[dict]: 指标定义（保持 METRICS 顺序，不含 identity——身份列由导出内置）。
        非法 / 锁定 key 直接忽略：勾选态存前端 localStorage，版本漂移时不能让导出挂掉。
    """
    if not metrics or not metrics.strip():
        return [m for m in METRICS if not m["locked"] and m["default_visible"]]
    wanted = {k.strip() for k in metrics.split(",") if k.strip()}
    return [m for m in METRICS if m["key"] in wanted and not m["locked"]]


def _matrix_export_payload(
    shop: Optional[str],
    date: Optional[str],
    keyword: Optional[str],
    category: Optional[str],
    seller: Optional[str],
    shelf_status: Optional[str],
    sort_by: Optional[str],
    sort_order: str,
    metrics: Optional[str],
) -> dict[str, Any]:
    """装配矩阵导出 payload：筛选/排序口径与 /rows **完全一致**，只是不分页。

    @remarks
    合计（totals / city_totals）取全量筛选结果，与矩阵表 tfoot 同源；
    ERP 编码走映射表（与补货清单同一函数），库里不写 0、导出写 0（下游模板占位）。

    ⚠️ 与 /rows 同理必须**全列**查询：导出指标列由用户勾选动态决定
    （metrics 参数），裁列会在换勾选后导出空列。导出不在首屏路径，
    全列扫描成本可接受。
    """
    data = _query_rows(shop, date)
    data = _filter_keyword(data, keyword)
    data = _filter_multi(data, "category", _split_multi(category))
    data = _filter_multi(data, "seller", _split_multi(seller))
    data = _filter_multi(data, "shelf_status", _split_multi(shelf_status))
    data = _sort_rows(data, sort_by, sort_order)
    return {
        "rows": data,
        "metrics": _resolve_export_metrics(metrics),
        "cities": CITIES,
        "totals": _compute_totals(data),
        "city_totals": _compute_city_totals(data),
        "erp_codes": _erp_codes(data),
    }


@router.api_route(
    "/matrix/export",
    methods=["GET", "POST"],
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
            "description": "库存矩阵 xlsx（含「按SKU」与「按城市」两个 sheet + 商品图；指标列随指标配置勾选变化）",
        }
    },
)
def export_matrix(
    shop: Optional[str] = Query(None, description="店铺名；空=全部店铺"),
    date: Optional[str] = Query(None, description="快照日期 YYYY-MM-DD；空=全部日期"),
    keyword: Optional[str] = Query(None, description="SKU / 商品名称 / SPU 模糊搜索（口径与矩阵表一致）"),
    category: Optional[str] = Query(None, description="三级类目；多个用逗号分隔，空=不限"),
    seller: Optional[str] = Query(None, description="销售员；多个用逗号分隔，空=不限"),
    shelf_status: Optional[str] = Query(None, description="上下柜状态；多个用逗号分隔，空=不限"),
    sort_by: Optional[str] = Query(None, description="排序指标 key（口径与矩阵表一致）；空=不排序"),
    sort_order: str = Query("desc", description="排序方向：desc（默认） / asc"),
    metrics: Optional[str] = Query(
        None, description="逗号分隔的指标 key（指标配置勾选）；空=后端默认勾选（default_visible）",
    ),
) -> Response:
    """导出库存矩阵（xlsx，两个 sheet + 商品图嵌入，指标列随勾选变化）。

    @remarks
    - 同时支持 GET 与 POST：GET 供浏览器**原生下载**（<a href> 直连，文件名取自
      Content-Disposition），POST 供程序化调用（测试、脚本）。参数全在查询串里。
    - 筛选 / 排序口径与 /rows 一致但**不分页**：导出的是当前筛选下的全量矩阵行。
    - 指标列由 `metrics` 决定（前端「指标配置」勾选）；身份列恒导出。
    - 失败也返回附件（错误说明 txt + 500）：前端用 <a href> 触发下载，普通 JSON
      错误会把当前页面导航走，筛选状态全丢（与补货导出同一约定）。
    """
    try:
        payload = _matrix_export_payload(
            shop, date, keyword, category, seller, shelf_status, sort_by, sort_order, metrics,
        )
        content = matrix_export.build_workbook(payload)
    except Exception as e:  # noqa: BLE001 —— 导出失败要给出可读说明，而不是 500 页面
        message = f"库存矩阵导出失败：{type(e).__name__}: {e}\n"
        filename = f"矩阵导出失败_{date or '最新'}.txt"
        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        return Response(
            content=message.encode("utf-8"),
            status_code=500,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": disposition, "Cache-Control": "no-store"},
        )
    filename = f"库存矩阵_{date or '最新'}.xlsx"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "no-store",
        },
    )
