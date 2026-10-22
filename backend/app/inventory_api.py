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

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..jobs import db as jobdb
from ..jobs import inv_pics
from ..jobs.alert_rules import (
    ALERT,
    HEALTHY,
    INCOMING,
    DEFAULT_DAYS,
    DEFAULT_SAFETY,
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW,
    alert_status,
    clamp_params,
    is_offshelf,
    restock_qty,
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
    """
    pairs = {(r.get("date"), r.get("shop"), r.get("sku")) for r in rows}
    pairs = {p for p in pairs if p[0] and p[1] and p[2]}
    if not pairs:
        return {}
    conn = _conn()
    try:
        out: dict[tuple[str, str, str], dict[str, float]] = {}
        for d, s, k in pairs:
            cur = conn.execute(
                "SELECT city, slow_units FROM inventory_city_slow WHERE date=? AND shop=? AND sku=?",
                (d, s, k),
            )
            cities = {r["city"]: float(r["slow_units"] or 0) for r in cur.fetchall()}
            cities = {c: v for c, v in cities.items() if v > 0}
            if cities:
                out[(d, s, k)] = cities
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
    """
    data = _query_rows(shop, date)
    if keyword:
        kw = keyword.strip().lower()
        data = [
            d for d in data
            if kw in (str(d.get("sku") or "")).lower()
            or kw in (str(d.get("sku_name") or "")).lower()
            or kw in (str(d.get("spu") or "")).lower()
        ]
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

    data = _query_rows(shop, date)
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


def _row_city_values(row: dict, code: str, window: int) -> tuple[float, float, float, float]:
    """从宽表行取某城市的 (现货, 在途, 窗口出库, 窗口有货天数)。"""
    return (
        _num(row.get(f"{code}_stock")),
        _num(row.get(f"{code}_purchase")),
        _num(row.get(f"{code}_out_{window}")),
        _num(row.get(f"{code}_avail_{window}")),
    )


@router.get("/alerts/summary")
def get_alerts_summary(
    shop: Optional[str] = Query(None, description="店铺名；空=全部店铺"),
    date: Optional[str] = Query(None, description="快照日期；空=最新快照"),
) -> dict[str, Any]:
    """8 城预警汇总：每城阈值、红/黄/绿计数与建议补货合计（默认参数）。

    计数口径：下柜 SKU 完全排除；建议补货合计按默认参数（9 天 / 20% / 14 日）。
    返回的 cities 顺序与 CITIES（前端 meta.cities）一致，city=中文名、code=城市码。
    """
    thresholds = _threshold_map()
    conn = _conn()
    try:
        snap = date or _latest_date(conn)
    finally:
        conn.close()
    data = _query_rows(shop, snap)
    cities: list[dict[str, Any]] = []
    for city in CITIES:
        code = CITY_CODES[city]
        t = thresholds[code]
        red = yellow = green = 0
        restock_total = 0
        for row in data:
            if is_offshelf(row.get("shelf_status")):
                continue
            stock, purchase, out, avail = _row_city_values(row, code, DEFAULT_WINDOW)
            st = alert_status(t, stock, purchase)
            if st == ALERT:
                red += 1
            elif st == INCOMING:
                yellow += 1
            else:
                green += 1
            restock_total += restock_qty(out, avail, DEFAULT_DAYS, DEFAULT_SAFETY, stock, purchase)
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


@router.get("/restock")
def get_restock(
    shop: Optional[str] = Query(None, description="店铺名；空=全部店铺"),
    date: Optional[str] = Query(None, description="快照日期；空=最新快照"),
    days: Optional[float] = Query(None, description="覆盖天数；默认 9，域 [1,60]"),
    safety: Optional[float] = Query(None, description="安全系数；默认 0.2（20%）"),
    window: Optional[int] = Query(None, description="销速窗口天数；7/14/30，默认 14"),
) -> dict[str, Any]:
    """补货建议清单（按城市分组）：销速驱动目标库存 − 现货 − 在途。

    只返回建议量 > 0 的行；下柜 SKU 完全排除；合计行（subtotal/grand_total）
    与前端展示同口径。None/越界参数被夹回默认值；非数字串由 Query 类型
    验证拦下（422）。
    """
    d, s, w = clamp_params(days, safety, window)
    thresholds = _threshold_map()
    conn = _conn()
    try:
        snap = date or _latest_date(conn)
    finally:
        conn.close()
    data = _query_rows(shop, snap)
    groups: list[dict[str, Any]] = []
    grand_total = 0
    for city in CITIES:
        code = CITY_CODES[city]
        t = thresholds[code]
        rows: list[dict[str, Any]] = []
        subtotal = 0
        for row in data:
            if is_offshelf(row.get("shelf_status")):
                continue
            stock, purchase, out, avail = _row_city_values(row, code, w)
            qty = restock_qty(out, avail, d, s, stock, purchase)
            if qty <= 0:
                continue
            velocity = out / avail if avail > 0 else 0.0
            rows.append({
                "sku": row.get("sku"),
                "sku_name": row.get("sku_name"),
                "shop": row.get("shop"),
                "category": row.get("category"),
                "shelf_status": row.get("shelf_status"),
                "stock": stock,
                "purchase": purchase,
                "velocity": round(velocity, 2),
                "target": round(velocity * d * (1 + s)),
                "qty": qty,
                "status": alert_status(t, stock, purchase),
            })
            subtotal += qty
        if rows:
            rows.sort(key=lambda r: r["qty"], reverse=True)
            groups.append({
                "city": city,
                "code": code,
                "threshold": t,
                "rows": rows,
                "subtotal": subtotal,
            })
            grand_total += subtotal
    return {
        "date": snap,
        "params": {"days": d, "safety": s, "window": w},
        "groups": groups,
        "grand_total": grand_total,
    }
