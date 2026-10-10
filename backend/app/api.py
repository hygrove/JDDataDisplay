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
from fastapi.responses import Response
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
    "promotion_cost", "promotion_amount", "promo_profit",
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


def _build_rows(
    module_id: str,
    shop: Optional[str],
    start: Optional[str],
    end: Optional[str],
    date: Optional[str],
    keyword: Optional[str],
    sort_by: Optional[str],
    sort_order: str,
) -> tuple[list[RowOut], list[str]]:
    """按区间 / 店铺 / 关键词过滤并聚合为「逐日」行（不分页）。

    抽离自 get_rows 的核心逻辑：导出接口需要**全量**（不受前端无限滚动分页影响），
    故把「过滤 + 按 (shop,spu) 聚合 + 排序 + 构造 RowOut」的部分复用出来，
    返回 (行列表, 区间内日期升序列表)。

    Args:
        module_id: 模块标识。
        shop / start / end / date / keyword / sort_by / sort_order: 语义与 get_rows 完全一致。

    Returns:
        tuple[list[RowOut], list[str]]: (过滤聚合后的全量行, 区间内日期升序列表)。

    Raises:
        HTTPException: 404 —— 模块数据文件不存在（由 flatten_module 抛出）。

    Example:
        >>> rows, dates = _build_rows("pop_spu_detail", None, "2026-09-01", "2026-09-24", None, None, "amount", "desc")
        >>> len(rows) >= 0
        True
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
    return out_rows, date_list


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
    """模块分页明细：复用 _build_rows 取全量，再按页码切片返回当页。

    Args / Returns / Raises 同 _build_rows；区别仅在于本函数按 page/page_size 切片。

    Example:
        GET /api/module/pop_spu_detail/rows?start=2026-09-01&end=2026-09-24&sort_by=amount&page=1
    """
    out_rows, _ = _build_rows(module_id, shop, start, end, date, keyword, sort_by, sort_order)
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


# ---------------- 数据导出（xlsx）----------------
class ExportMetricAgg(BaseModel):
    """导出指标口径：与前端 metrics.ts 的 MetricSpec.agg 对齐。

    Fields:
        kind: "sum" 直接求和；"ratio" 先分别累加分子分母再相除。
        num: ratio 类型的分子指标 key；sum 类型时为 None。
        den: ratio 类型的分母指标 key；sum 类型时为 None。
    """

    kind: str
    num: Optional[str] = None
    den: Optional[str] = None


class ExportMetricSpec(BaseModel):
    """前端传来的单个导出指标定义（key + 中文标题 + 口径）。

    Fields:
        key: 指标 key，需与 MetricRecord 字段名一致。
        title: 中文展示名（作导出表头）。
        agg: 区间汇总口径（见 ExportMetricAgg）。
    """

    key: str
    title: str
    agg: ExportMetricAgg


class ExportRequest(BaseModel):
    """导出请求体：过滤条件 + 要导出的指标清单。

    Fields:
        shop / start / end / keyword / sort_by / sort_order: 与 get_rows 同语义。
        metrics: 前端按「指标配置唯一事实来源」传入的有序指标清单（Q3=B 即全部 15 项）。

    Example:
        >>> ExportRequest(metrics=[ExportMetricSpec(key="amount", title="成交金额",
        ...     agg=ExportMetricAgg(kind="sum"))]).metrics[0].key
        'amount'
    """

    shop: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    keyword: Optional[str] = None
    sort_by: Optional[str] = None
    sort_order: str = "desc"
    metrics: list[ExportMetricSpec]


def _resolve_thumb(image: Optional[str]) -> Optional[Path]:
    """把 RowOut.image（"/images/xxx.png" 形式）解析成本地缩略图 / 原图文件路径。

    优先用批处理预生成的 120 尺寸缩略图（webp→avif 兜底），体积远小于原图；
    都不存在时退回原图目录；都没有则返回 None（导出时该 SPU 不嵌图）。

    Args:
        image: 图片相对路径，如 "/images/100123.png"；空返回 None。

    Returns:
        Optional[Path]: 可用于 openpyxl 嵌入的本地图片路径；找不到时 None。
    """
    if not image:
        return None
    stem = Path(image).stem
    for ext in ("webp", "avif"):
        p = config.THUMBS_DIR / "120" / f"{stem}.{ext}"
        if p.exists():
            return p
    orig = config.IMAGES_DIR / Path(image).name
    return orig if orig.exists() else None


def _build_export_workbook(
    rows: list[RowOut], date_list: list[str], specs: list[ExportMetricSpec]
) -> bytes:
    """构造「每 SPU 一块」的 xlsx 字节流（区间模式布局，与样本表结构一致）。

    布局（与用户提供的单品测试表对齐）：
      - 全局表头：A 图片 / B 商品名称 / C spu / D 店铺 / E 类目 / F 指标 / G 总计 / H… 日期；
      - 每个 SPU 占一块：A 列图片纵向合并；B-E 重复该 SPU 元信息；
        F 为指标名；H… 为该指标逐日值；G 为区间总计
        （求和类=日值之和，比率类=分子分母总量相除=口径A）；块间留一空行。
    数值统一存真实数值（货币符号不要），保证可在 Excel 内排序 / 求和；
    仅当比率类分母为 0（无数据）时总计与缺失日单元格留空。

    Args:
        rows: _build_rows 产出的全量行（每个 SPU 一行，days 承载逐日数据）。
        date_list: 区间日期升序列表（列 H… 的来源）。
        specs: 前端传入的有序指标清单（决定导出列与口径）。

    Returns:
        bytes: 完整 xlsx 文件的字节流（内存中生成，无需落盘）。

    Example:
        >>> _build_export_workbook([], [], [])  # doctest: +SKIP
        b'PK\\x03\\x04...'
    """
    import io

    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.drawing.spreadsheet_drawing import (
        AnchorMarker,
        OneCellAnchor,
        XDRPositiveSize2D,
    )
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter
    from openpyxl.utils.units import pixels_to_EMU

    wb = Workbook()
    ws = wb.active
    ws.title = "单品数据"

    # 全局表头
    ws.cell(1, 1, "图片")
    ws.cell(1, 2, "商品名称")
    ws.cell(1, 3, "spu")
    ws.cell(1, 4, "店铺")
    ws.cell(1, 5, "类目")
    ws.cell(1, 6, "指标")
    # 总计列表头：显式说明「橙色 = 总量平均值」，让读者不必翻文档即懂配色含义
    ws.cell(1, 7, "总计(橙色为总量平均值）")
    for j, d in enumerate(date_list):
        ws.cell(1, 8 + j, d)

    # 列宽
    ws.column_dimensions["A"].width = 18
    for col in ("B", "C", "D", "E", "F", "G"):
        ws.column_dimensions[col].width = 16
    for j in range(len(date_list)):
        ws.column_dimensions[get_column_letter(8 + j)].width = 12

    def _num(v: object) -> Optional[float]:
        """把值规整成 float（非数字返回 None）。"""
        return v if isinstance(v, (int, float)) else None

    def _round(v: object) -> object:
        """把数值保留 2 位小数；整数仍保持整数（避免 12.0 这类尾随 .0）。

        Args:
            v: 任意值（数字 / None / 其他）。

        Returns:
            object: 数字则保留 2 位（整数去尾随 .0），否则原样返回。
        """
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            r = round(float(v), 2)
            return int(r) if r == int(r) else r
        return v

    # 比率类总计的标记色（橙）：作为单元格**字体颜色**，与求和类（默认黑）区分，
    # 一眼看出「这是总量平均值」。不用底色填充，避免整格铺色显得沉重、压住数字。
    RATIO_TOTAL_COLOR = "E26B0A"
    # B-E 元信息列统一居中（水平 + 垂直）。按用户决策「只合并 A 列」，
    # B-E 每行都保留独立单元格，因此仍可在 Excel 内排序 / 筛选。
    META_ALIGN = Alignment(horizontal="center", vertical="center")

    r = 2  # 第 1 行是表头，数据从第 2 行起
    for row in rows:
        block_start = r
        n = len(specs)
        block_end = block_start + n - 1
        block_rows = n  # 本块行数（= 指标数），用于计算图片垂直居中偏移
        # 图片列纵向合并（跨整个 SPU 指标块）；B-E 不合并，保持可排序
        ws.merge_cells(start_row=block_start, start_column=1, end_row=block_end, end_column=1)
        img_path = _resolve_thumb(row.image)
        if img_path:
            try:
                img = XLImage(str(img_path))
                # 缩放到约 120px 高（不放大）：避免大原图撑爆行高
                target = 120
                scale = (target / img.height) if img.height else 1.0
                if scale > 1:
                    scale = 1.0
                w = int(round(img.width * scale))
                h = int(round(img.height * scale))
                # 关键：必须显式设置图片宽高与锚点 ext。openpyxl 的 OneCellAnchor
                # 在缺省 ext 时会写成 XDRPositiveSize2D(0, 0)，落盘后图片尺寸为 0，
                # Excel 中完全不可见——这正是上一版“图片没成功”的根因（仅数到图片数
                # 非零不足以证明可见，必须核对锚点 ext 尺寸字段）。
                img.width = w
                img.height = h
                # 图片在合并区内「垂直居中」：Excel 的图片是浮层 drawing，锚点只认
                # 单元格 + 偏移，没有 valign 属性，因此要自己算出往下挪多少：
                # 块高 = 行数 × 默认行高（15pt ≈ 20px），减去图片高，再除以 2 即为
                # 顶部偏移；负数（图片比块高）按 0 处理，避免图片溢出到上一块。
                default_row_px = 20.0
                block_px = block_rows * default_row_px
                offset_px = max(0.0, (block_px - h) / 2.0)
                img.anchor = OneCellAnchor(
                    _from=AnchorMarker(
                        col=0,
                        row=block_start - 1,
                        colOff=0,
                        rowOff=pixels_to_EMU(offset_px),
                    ),
                    ext=XDRPositiveSize2D(pixels_to_EMU(w), pixels_to_EMU(h)),
                )
                ws.add_image(img)
            except Exception:
                # 单张图嵌入失败不阻断整份导出
                pass

        for i, spec in enumerate(specs):
            rr = block_start + i
            # B-E 重复该 SPU 元信息（与样本表一致），仅居中、不合并
            for col, val in (
                (2, row.spu_name or ""),
                (3, row.spu),
                (4, row.shop),
                (5, row.category or ""),
            ):
                cell = ws.cell(rr, col, val)
                cell.alignment = META_ALIGN
            ws.cell(rr, 6, spec.title)
            # 逐日值：求和类缺失日补 0，比率类缺失日留空
            for j, d in enumerate(date_list):
                dm = row.days[j].metrics if j < len(row.days) else None
                v = _num(getattr(dm, spec.key, None)) if dm else None
                cell = ws.cell(rr, 8 + j)
                cell.value = _round(0.0 if (v is None and spec.agg.kind == "sum") else v)
            # 总计列
            if spec.agg.kind == "sum":
                total = 0.0
                for d in row.days:
                    nv = _num(getattr(d.metrics, spec.key, None))
                    if nv is not None:
                        total += nv
                ws.cell(rr, 7, _round(total))
            else:
                num = 0.0
                den = 0.0
                for d in row.days:
                    nv = _num(getattr(d.metrics, spec.agg.num, None))
                    dv = _num(getattr(d.metrics, spec.agg.den, None))
                    if nv is not None:
                        num += nv
                    if dv is not None:
                        den += dv
                # 口径A：总量相除（分母为 0 时无数据，留空而非 0）；结果保留 2 位。
                # 比率类总计是「分子分母总量相除」得到的加权平均（非逐日算术平均），
                # 故整格数字用橙色字体标记区分，配合表头「总计(橙色为总量平均值）」提示读者。
                cell = ws.cell(rr, 7, _round(num / den) if den else None)
                cell.font = Font(color=RATIO_TOTAL_COLOR)
        r = block_end + 2  # 块间留一空行分隔

    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


@router.post("/module/{module_id}/export")
def export_module(module_id: str, body: ExportRequest) -> Response:
    """导出当前筛选条件下的数据为 xlsx（后端全量生成，openpyxl 构造）。

    与页面展示同口径：复用 _build_rows 取全量（不受前端分页影响），按前端传入的指标清单
    铺成「每 SPU 一块 + 图片合并 + 总计列」的表格（详见 _build_export_workbook）。

    Args:
        module_id: 模块标识（路径参数）。
        body: 过滤条件 + 指标清单（见 ExportRequest）。

    Returns:
        Response: xlsx 二进制流（application/vnd.openxmlformats-officedocument.spreadsheetml.sheet）。

    Raises:
        HTTPException: 404 —— 所选范围内没有可导出的数据。

    Example:
        POST /api/module/pop_spu_detail/export
    """
    out_rows, date_list = _build_rows(
        module_id, body.shop, body.start, body.end, None, body.keyword, body.sort_by, body.sort_order
    )
    if not out_rows:
        raise HTTPException(status_code=404, detail="所选范围内没有可导出的数据")
    data = _build_export_workbook(out_rows, date_list, body.metrics)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=export.xlsx"},
    )


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


# ---------------- 推广预算优化（/promo） ----------------
# ⚠️ 本节术语纪律（见 CONTEXT.md）：一律「投产比」，⛔ 不出现「边际ROI」。
#    边际 ROI 需反事实数据（关掉某 SPU 推广会怎样），平台不提供，本项目算不出来。

# 空态原因枚举。前端按这些值渲染，不要用「有数据/没数据」的布尔表达——
# 三种空态的**处置方式不同**（提示换区间 / 弱化展示 / 提示无法计算），
# 一个布尔会把「部分可用」误判成「不可用」。
EMPTY_NO_DATA = "no_data"
EMPTY_TOO_FEW_SPU = "too_few_spus"
EMPTY_ALL_ZERO = "all_zero"

# 免责文案（spec §8）。⛔ 不得出现「边际」「亏损」「预测」等词——
# 投产比 = 成交金额 ÷ 花费，分子是收入不是利润；数据源不含毛利字段，无法判断盈亏；
# 「理论上限」是线性假设下的数学最优，不是可达目标（更不是预测）。
#
# ⚠️ **必须是恰好 3 条的序列，不是单串**：spec §8 把三处列为硬要求且「缺一不可」，
#   而单串拼接后无法逐条断言（前两处挤在一起时，删掉第2 条仍能 grep 到第 1、3 条的
#   关键词，断言照样绿）。结构化成 list 后，「恰好 3 条」本身就可断言。
#   ⚛️ 相应地⛔ 不保留单串兼容字段 —— 那会让同一组文案有两个事实来源，
#   改一边忘另一边必然漂移（同 metrics.ts / compute_metrics 纪律）。
PROMO_DISCLAIMERS: tuple[str, ...] = (
    # ① 线性假设：理论上限不是可达目标，更不是预测
    "理论上限基于「加预算成交按同比例放大」的线性假设，是数学最优而非可达目标。",
    # ② 保本线说明：投产比 1 的分界未计商品毛利
    "投产比 1 是保本线，但该判断未计入商品毛利 —— 投产比 1.2 的品在毛利只有 20% 时，"
    "实际仍可能不赚钱。",
    # ③ 大促风险：流量结构会变，方案不可照搬
    "大促期流量结构会变，请勿直接照搬本方案。",
)

# 各条文案的稳定标识，供前端定位与测试断言（⛔ 不要用文案内容当 key）。
DISCLAIMER_KINDS: tuple[str, ...] = ("linearity", "breakeven", "campaign")


class PromoSpu(BaseModel):
    """推广分析里的单个 SPU 条目。

    ⚠️ `roi` / `cost_share` / `amount_share` / `roi_cv` 的分母为 0 时都是 None
       （前端显示 `--`），**不返回 0**：0 会被误当成「投产比为 0 的真实值」，
       让该品被误判成吞金兽。

    Fields:
        spu: SPU 编号。
        spu_name: 商品名称，可为空。
        image: 图片访问路径，可为空。
        current_cost: 区间内 Σ推广花费（元）。
        current_amount: 区间内 Σ推广成交金额（元）。
        roi: 投产比 = Σ成交 ÷ Σ花费（先求和再相除）；分母 0 时 None。
        profit: 推广净收益 = Σ成交 − Σ花费。
        cost_share: **日均**推广花费占比 = Σ(该品当日花费 ÷ 该店当日总花费) ÷ 天数，∈ [0,1]。
        amount_share: 同上，按推广成交金额计。
        ⚠️ 这两个是 spec §9「按店+日分组求和后再跨日累加」那一步的**收尾**：
           裸累加的量纲是「天数倍」（67 天区间会得到 14.75 = 1475%），
           必须再除以天数才是可展示的占比。
        roi_cv: 逐日投产比的变异系数；无法计算时 None（详见 promo.compute_roi_cv）。
        unstable: 波动过大（由 promo.mark_unstable 的两级规则判定）。
        suggested_cost: 贪心算法给出的建议分配额。
        delta: 建议 − 当前 = suggested_cost − current_cost（正=加投、负=减投）。
        quadrant: 四象限分档枚举（core/potential/loser/trial）；roi 不可算时 None。
        advice: 一句话建议（后端生成，前端直接展示，勿在前端另写一份）。
        skipped: True 表示未参与分配（无推广花费 / 投产比无法计算）。
        skip_reason: 未参与分配的原因文案。
    """

    spu: str
    spu_name: Optional[str] = None
    image: Optional[str] = None
    current_cost: float
    current_amount: float
    roi: Optional[float] = None
    profit: float
    cost_share: Optional[float] = None
    amount_share: Optional[float] = None
    roi_cv: Optional[float] = None
    unstable: bool = False
    suggested_cost: float
    delta: float
    quadrant: Optional[str] = None
    advice: str
    skipped: bool = False
    skip_reason: Optional[str] = None


class PromoAnalysisOut(BaseModel):
    """推广预算优化分析的完整返回体。

    ⚠️ `empty_reason` 是**后端判定的空态原因**，前端只负责渲染、不重复判断：
       | 值 | 含义 | 前端表现 |
       |---|---|---|
       | no_data | 该店区间无推广数据 | 提示换区间 |
       | too_few_spus | 有推广数据的 SPU < 4 个 | **弱化展示**（非报错）：贪心仍可执行，仅四象限不可信 |
       | all_zero | 有 SPU 但全部花费为 0 | 提示投产比无法计算 |
       | None | 数据完整 | 正常展示 |

       ⚠️ `too_few_spus` 最容易被误当成错误处理掉——它其实是「部分可用」：
         贪心分配在只有 1 个 SPU 时照常工作（结果就是它自己，只是填不满预算），
         所以响应照常返回全部数字，只由前端弱化四象限。

    Fields:
        shop / start / end: 本次分析实际使用的店铺与区间。
        date_range: 数据中实际命中的日期区间 [最早日, 最晚日]（可能窄于请求区间）。
        total_cost / total_amount: 区间内该店的 Σ推广花费 / Σ推广成交金额。
        current_total_amount: 当前分配下的推广成交总额（= total_amount）。
        ideal_total_amount: 贪心理论上限总成交（线性假设下的数学最优，不可达成）。
        conservative_total: 保守估计 = ideal × BACKTEST_DISCOUNT。
        cap: 单品预算上限 = total_cost × CAP_RATIO。
        unallocated: 因单品上限封顶而分不出去的预算（SPU 数少时 > 0）。
        gap: 可优化空间 = ideal − current。
        achievement_rate: 达成率 = current ÷ ideal；分母 0 时 None。
        quadrant_reliable: False 表示 SPU 数不足，分档不可信，前端应弱化四象限。
        over_concentrated: 现状已超单品集中度上限（30%）的 SPU 标识；None = 现状可行。
            非 None 时 gap < 0 且 achievement_rate > 1 —— 含义是「重新分配会让总成交下降」。
        spus: 各 SPU 明细（按建议分配额降序，便于前端直接渲染决策表）。
        empty_reason: 空态原因；数据完整时为 None。
        disclaimers: 免责文案（spec §8 三处硬要求），前端必须**逐条**展示；
            单串版本已删除（两处文案二选一必然漂移）。
    """

    shop: str
    start: str
    end: str
    date_range: list[str] = Field(default_factory=list)
    total_cost: float = 0.0
    total_amount: float = 0.0
    current_total_amount: float = 0.0
    ideal_total_amount: float = 0.0
    conservative_total: float = 0.0
    cap: float = 0.0
    unallocated: float = 0.0
    gap: float = 0.0
    achievement_rate: Optional[float] = None
    quadrant_reliable: bool = True
    # 四象限两条分割线的位置（投产比阈值、花费阈值，均为店内分位数 P50）。
    # 必须由后端给出：前端要在图上画线，若自己从 spus 重算分位数，
    # 插值约定一旦有差就会出现「点被染成高效档却在分割线下方」的自相矛盾。
    # 无可分档样本时为 None，前端不画线。
    roi_threshold: Optional[float] = None
    cost_threshold: Optional[float] = None
    # ⚠️ 现状违反单品集中度上限的 SPU 标识（多个用「、」连接）；None = 现状本身可行。
    # 非 None 时 gap 必然为负、achievement_rate 必然 > 1（见 promo.compute_gap 的 docstring），
    # 前端据此改文案与配色——⛔ 不要照字面渲染成「可优化空间 −1,735 元」。
    over_concentrated: Optional[str] = None
    spus: list[PromoSpu] = Field(default_factory=list)
    empty_reason: Optional[str] = None
    disclaimers: list[str] = Field(default_factory=lambda: list(PROMO_DISCLAIMERS))


@router.get("/promo/analysis", response_model=PromoAnalysisOut)
def get_promo_analysis(
    shop: Optional[str] = Query(None, description="店铺名；空=数据中第一家店"),
    start: Optional[str] = Query(None, description="起始日期 YYYY-MM-DD；空=数据最早日"),
    end: Optional[str] = Query(None, description="截止日期 YYYY-MM-DD；空=数据最新日"),
    module_id: str = Query("pop_spu_detail", description="模块标识（推广数据所在模块）"),
) -> PromoAnalysisOut:
    """推广预算优化：贪心分配方案 + 三数字 + 分档 + 建议。

    实现的分层（每层职责单一，可分别验证）：
      1. 从已缓存的扁平行（_flat_cache）过滤出「该店 + 区间」的行 —— 不重跑批处理；
      2. 汇总到 SPU 粒度，算派生指标 —— 比率一律「先求和再相除」，与
         transforms.compute_metrics / metrics.ts:aggregateMetrics 口径一致；
      3. 逐日 CV + 店内分位 → 稳定性标记（promo.compute_roi_cv / mark_unstable）；
      4. 贪心分配（promo.greedy_allocate）—— 零和：总预算不变；
      5. 四象限分档（promo.classify_quadrant）+ 文案（promo.build_advice）；
      6. 空态判定在本函数内完成，前端只渲染。

    Args:
        shop: 店铺名；None 表示取数据中第一家（页面上总有店可看，不返回 400）。
        start: 起始日期；None 表示不限（取数据最早日）。
        end: 截止日期；None 表示不限（取数据最新日）。
        module_id: 模块标识，默认唯一的推广明细模块。

    Returns:
        PromoAnalysisOut: 完整分析结果；无数据时返回带 empty_reason 的空壳
            （HTTP 仍为 200 —— 空态是正常业务状态，不是错误）。

    Raises:
        HTTPException: 404 —— 模块数据文件不存在（由 flatten_module 抛出）。

    Example:
        GET /api/promo/analysis?shop=钻芯旗舰店
    """
    from .promo import (
        build_advice,
        classify_quadrant,
        compute_quadrant_thresholds,
        compute_gap,
        compute_roi_cv,
        greedy_allocate,
        mark_unstable,
        detect_over_concentrated,
        DailyRoi,
        PromoInput,
        MIN_SPU_FOR_QUADRANT,
    )

    all_rows = flatten_module(module_id)
    if shop:
        rows = [r for r in all_rows if r.shop == shop]
    else:
        # 不指定店铺时取第一家：页面首屏不该因为没选店就空白或报错
        shops = sorted({r.shop for r in all_rows})
        if not shops:
            return PromoAnalysisOut(shop="", start=start or "", end=end or "",
                                    empty_reason=EMPTY_NO_DATA)
        shop = shops[0]
        rows = [r for r in all_rows if r.shop == shop]

    # 日期过滤（YYYY-MM-DD 字符串字典序即时间序，可直接比较）
    if start:
        rows = [r for r in rows if r.date >= start]
    if end:
        rows = [r for r in rows if r.date <= end]
    dates = sorted({r.date for r in rows})
    if not dates:
        return PromoAnalysisOut(shop=shop, start=start or "", end=end or "",
                                empty_reason=EMPTY_NO_DATA)

    # ---- 2. 汇总到 SPU 粒度 ----
    # 按 (spu) 分组累加**原始可累加指标**；比率随后统一重算，
    # 不可对逐日比率求平均（项目铁律，见 transforms.compute_metrics 的 docstring）。
    acc: dict[str, dict] = {}
    for r in rows:
        g = acc.setdefault(r.spu, {
            "cost": 0.0, "amount": 0.0,
            "name": None, "image": None,
            "daily": [],  # (cost, roi) —— 供 CV 用
            "share_num": 0.0, "share_den": 0.0,
        })
        c = r.metrics.promotion_cost or 0.0
        a = r.metrics.promotion_amount or 0.0
        g["cost"] += c
        g["amount"] += a
        if r.spu_name:
            g["name"] = r.spu_name
        if r.image:
            g["image"] = r.image
        # 逐日投产比：分母为 0 的日子无意义，交给 compute_roi_cv 内部跳过
        g["daily"].append(DailyRoi(cost=c, roi=r.metrics.roi))

    # ---- 占比指标：分母是该店「当日」全部 SPU 的花费和 ----
    # ⚠️ 必须先按日聚合，不能用区间总花费做分母：
    #    那会把「9 月 1 日占全季 20%」算成「每天都占全季 20%」。
    # ⚠️⚠️ 按日算完还要**除以天数**再对外，否则量纲是「天数倍」：
    #    67 天的区间裸累加会得到 14.75 = 1475%，前端没法当占比展示。
    #    除完是「日均占比」（约等于区间总占比，但不受单日异常值放大）。
    day_total: dict[str, dict[str, float]] = {}
    for r in rows:
        d = day_total.setdefault(r.date, {"cost": 0.0, "amount": 0.0})
        d["cost"] += r.metrics.promotion_cost or 0.0
        d["amount"] += r.metrics.promotion_amount or 0.0
    for r in rows:
        g = acc[r.spu]
        dc = day_total[r.date]["cost"]
        da = day_total[r.date]["amount"]
        # 分母为 0 时该日无占比（跳过，不记 0 —— 0 会被当成「占比真的为 0」）
        if dc > 0:
            g["share_num"] += (r.metrics.promotion_cost or 0.0) / dc
        if da > 0:
            g["share_den"] += (r.metrics.promotion_amount or 0.0) / da
    n_days = len(dates) or 1

    # ---- 3. 构造算法输入 ----
    items = [
        PromoInput(spu=s, cost=g["cost"], roi=(g["amount"] / g["cost"]) if g["cost"] > 0 else None)
        for s, g in acc.items()
    ]
    spus_in_order = [it.spu for it in items]

    # ---- 3b. 波动校验：先算全部 CV，再统一判定 ----
    # ⚠️ 必须分两步：店内的 CV 分位线要看完全部 SPU 的 CV 才能确定，
    #    边算边判会让先算的品与后算的品用不同的阈值线。
    cvs = [compute_roi_cv(acc[s]["daily"]) for s in spus_in_order]
    flags = mark_unstable(cvs)
    cv_by_spu = dict(zip(spus_in_order, cvs))
    unstable_by_spu = dict(zip(spus_in_order, flags))

    # ---- 4. 贪心分配 ----
    result = greedy_allocate(items)
    alloc_by_spu = {a.spu: a for a in result.allocations}

    # ---- 5. 分档 + 文案 ----
    quadrants = classify_quadrant(items)
    # 分割线位置与分档同源（同一个 compute_quadrant_thresholds，见 promo.py 的说明）
    roi_thr, cost_thr = compute_quadrant_thresholds(items)

    out_spus: list[PromoSpu] = []
    for it in items:
        g = acc[it.spu]
        a = alloc_by_spu[it.spu]
        quad = quadrants.get(it.spu)
        unstable = unstable_by_spu[it.spu]
        out_spus.append(PromoSpu(
            spu=it.spu,
            spu_name=g["name"],
            image=g["image"],
            current_cost=it.cost,
            current_amount=g["amount"],
            roi=it.roi,
            profit=g["amount"] - it.cost,
            # 日均占比 = Σ(该品当日花费 / 该店当日总花费) ÷ 天数 ∈ [0,1]
            cost_share=g["share_num"] / n_days,
            amount_share=g["share_den"] / n_days,
            roi_cv=cv_by_spu[it.spu],
            unstable=unstable,
            suggested_cost=a.suggested_cost,
            delta=a.suggested_cost - it.cost,
            quadrant=quad,
            advice=build_advice(quad, it.roi, unstable=unstable,
                                delta=a.suggested_cost - it.cost),
            skipped=a.skipped,
            skip_reason=a.skip_reason,
        ))

    # 按建议分配额降序：运营最关心「谁该多给钱」，且与贪心分配顺序一致
    out_spus.sort(key=lambda x: (-x.suggested_cost, -x.current_cost))

    # ---- 6. 空态判定 ----
    # ⚠️ 顺序有讲究：先判「完全无推广数据」，再判「全部花费为 0」，
    #    最后判「SPU 数不足」。三者可同时成立时，应报**最致命**的那个
    #    （无任何行→ all_zero → too_few_spus）。
    empty_reason: Optional[str] = None
    has_promo = any(s.current_cost > 0 or s.current_amount > 0 for s in out_spus)
    if not has_promo:
        empty_reason = EMPTY_ALL_ZERO
    elif len(out_spus) < MIN_SPU_FOR_QUADRANT:
        # ⚠️ 不是致命错误：贪心仍可执行（1 个 SPU 时结果就是它自己，只是填不满预算），
        #    仅四象限与分位数不可信。响应照常返回全部数字。
        empty_reason = EMPTY_TOO_FEW_SPU

    # 当前实际总成交 = 参与分配的那些品的成交之和
    # ⚠️ 必须与 ideal 同口径：ideal 只累加了非跳过品，若这里用全部品的成交，
    #    跳过品（无推广花费，成交额通常也不来自推广）会把达成率压到 100% 以下，
    #    页面显示「可优化空间 > 0」却「达成率 < 1」之外还暗示有未计入的收益。
    current_total = sum(s.current_amount for s in out_spus if not s.skipped)
    gap, rate = compute_gap(current_total, result.ideal_total_amount)
    over_conc = detect_over_concentrated(result.allocations, result.cap)

    return PromoAnalysisOut(
        shop=shop,
        start=dates[0],
        end=dates[-1],
        date_range=[dates[0], dates[-1]],
        total_cost=result.total_budget,
        total_amount=sum(s.current_amount for s in out_spus),
        current_total_amount=sum(s.current_amount for s in out_spus),
        ideal_total_amount=result.ideal_total_amount,
        conservative_total=result.conservative_total,
        cap=result.cap,
        unallocated=result.unallocated,
        gap=gap,
        achievement_rate=rate,
        # SPU 数不足时分档由个别样本决定，明确告诉前端不可信
        quadrant_reliable=(len(out_spus) >= MIN_SPU_FOR_QUADRANT),
        roi_threshold=roi_thr,
        cost_threshold=cost_thr,
        over_concentrated=over_conc,
        spus=out_spus,
        empty_reason=empty_reason,
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
