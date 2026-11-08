# -*- coding: utf-8 -*-
"""店铺运营数据 API：``/api/ops-daily/meta`` 与 ``/api/ops-daily/data``（只读）。

数据源（D2 用户拍板）：只读 ``ops_shop_daily`` 存量行 + ``ops_metric_dict`` 指标字典；
⛔ 不接日更 JSON、不跑月报 ETL、不造数、不留空态兜底。

设计要点：
- 指标列与公式的**唯一来源是 ``ops_metric_dict`` 表**，不是本模块常量。
  字典新增一项，meta 自动多一列、data 自动多一个值，前端无需改码；
  ``ops_daily.ATOMIC_KEYS`` 只用作「列名白名单」防 SQL 注入，不承载列定义。
- 派生值 = 当前聚合粒度内 ``SUM(分子)/SUM(分母)``。矩阵行是单行（SUM 退化为单值），
  ``summary``（KPI 卡）是筛选范围整体；两者同源不同粒度，**验收禁止互相比对**。
- NULL 与 0 分离：真实 0 返回 ``0.0``；分母为 0 / 全 NULL / 分子 NULL 返回 ``None``，
  由前端渲染 ``--``；接口绝不返回 NaN，也不用 0 伪装缺失。
- 只读：不写 ``ops_*`` 表、不建表、不触发 ETL。CRUD 仍只由 ``jobs/ops_daily.py``
  的 Python / CLI 层提供。
- 两个端点就是全部 API（SPEC §1）。导出**不另开端点**：``/data?all=true`` 用一次
  独立请求取筛选**全量行**，前端按当前勾选列及顺序拼 CSV 并加 UTF-8 BOM。
- ``/data`` 响应里的 ``monthly`` 数组是方案 C 月度趋势图的唯一数据源：按月份
  ``GROUP BY`` + 按 ``shop_type`` 拆分，**不新增第三个端点**、不受分页影响。
  只下发原子合计，环比百分比由前端纯函数现算，不落库、不预聚合。
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..jobs import ops_daily

router = APIRouter(prefix="/api/ops-daily")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 默认每页 50 行：与 SPEC §4.1 性能契约一致。⛔ 不要改成 200 —— 前端虽会
# 强传 PAGE_SIZE=50，但裸调 /data 的调用方（脚本 / 导出兜底 / 后续新页面）
# 不该拿到比契约更大的结果集，否则「页面耗时只与当前页行数相关」会破功。
_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 500
# 全量导出的硬上限：现有数据 1564 行，留足余量也防住误用导致的超大响应。
# 超过上限直接报错而不是静默截断——截断会破坏「导出 = 筛选全量行」的承诺。
_ALL_ROWS_CAP = 10_000


class OpsMetricOut(BaseModel):
    """基准指标字典的一项，meta 下发后前端据此动态建列。

    Fields:
        metric_key: 指标英文标识符，也是 data 行 ``values`` 的键。
        cn_name: 基准指标中文名（业务唯一叫法）。
        source_name: 源表字段名，仅供追溯改名，不参与展示。
        kind: ``atomic``（原子，可累加）或 ``derived``（派生，查询时重算）。
        db_column: 原子指标对应的 REAL 列名；派生指标为空串。
        formula: 派生公式（以原子列名表达），仅用于 tooltip，前端不计算。
        unit: 单位；源表未声明的为空串，不臆测。
        is_baseline: 是否为基准指标。
        sort_order: 字典排序号，前端列序以此为准。
        precision: 展示小数位（视图元数据，不参与计算、不落库）。
    """

    metric_key: str
    cn_name: str
    source_name: str
    kind: str
    db_column: str
    formula: str
    unit: str
    is_baseline: bool
    sort_order: int
    precision: int


class OpsMetricMeta(BaseModel):
    """``GET /meta`` 响应：字典 + 筛选项 + 数据截至日期。"""

    metrics: list[OpsMetricOut] = Field(default_factory=list)
    data_from: Optional[str] = None
    data_through: Optional[str] = None
    shops: list[str] = Field(default_factory=list)
    shop_types: list[str] = Field(default_factory=list)
    total_rows: int = 0


class OpsDailyRow(BaseModel):
    """一行店铺日粒度数据。

    ``values`` 的键集合 = ``ops_metric_dict.metric_key`` 全集（19 原子 + 5 派生），
    值只能是 float 或 None；None 表示缺失 / 分母为 0，前端渲染 ``--``。
    """

    date: str
    shop: str
    shop_type: str
    values: dict[str, Optional[float]] = Field(default_factory=dict)


class OpsMonthlyPoint(BaseModel):
    """一个月度汇总点：方案 C「月度销售额趋势」的数据源。

    Fields:
        month: ``YYYY-MM``，由 ``substr(date,1,7)`` 得到，升序返回。
        sales_amount: 该月销售额合计（全部店铺类型）。
        visitors: 该月访客数合计。
        by_type: ``{shop_type: 销售额合计}``；键取自库内 ``DISTINCT shop_type``，
            **不硬编码** self / pop。每个月都补齐全部键，缺失的给 None，
            前端画双折线时不会因为某月缺某类型而错位。

    @remarks
    只下发原子合计，不下发环比：环比是派生值，由前端纯函数按
    ``(本月 - 上月) / |上月|`` 现算（与「派生不落库」口径一致）。
    """

    month: str
    sales_amount: Optional[float] = None
    visitors: Optional[float] = None
    by_type: dict[str, Optional[float]] = Field(default_factory=dict)


class OpsDailyData(BaseModel):
    """``GET /data`` 响应：分页行 + 整体聚合 + 月度趋势点。

    Fields:
        total: 过滤后的总行数（不是当页行数）。
        page / page_size: 分页信息；``all=true`` 时 page_size 等于 total。
        rows: 当页（或全量）行。
        summary: 当前筛选范围**整体**的 19 原子合计 + 5 派生值，供 4 张 KPI 卡使用。
        monthly: 月度汇总点（方案 C 趋势图 + 月份间环比标注），不受分页影响。
        data_through: 库内最大日期，用于页面「数据截至」标注。
    """

    total: int
    page: int
    page_size: int
    rows: list[OpsDailyRow] = Field(default_factory=list)
    summary: dict[str, Optional[float]] = Field(default_factory=dict)
    monthly: list[OpsMonthlyPoint] = Field(default_factory=list)
    data_through: Optional[str] = None


def _conn() -> sqlite3.Connection:
    """打开运营库连接；调用方负责关闭（只读查询不建表、不写库）。"""
    return ops_daily.get_conn()


def _precision_for(unit: str) -> int:
    """按单位给出展示小数位。

    为什么在查询层算：``ops_metric_dict`` 没有 precision 列，为了「字典是唯一列来源」
    不改业务表；这里只产出视图元数据，不参与任何计算，也不写入数据库。
    百分比以小数返回（如 0.075），前端自行乘 100 后按精度展示。

    Args:
        unit: 字典里的单位。

    Returns:
        int: 小数位数；计数类（人 / 次 / 单）为 0，其余为 2。
    """
    return 0 if unit in ("人", "次", "单") else 2


def _metric_defs(
    conn: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], dict[str, tuple[str, str]]]:
    """从数据库字典读全部指标定义，并解析派生公式的分子 / 分母。

    公式只接受「两个合法原子列名用 / 相连」的形态；非法公式不猜测、不报错，
    该派生项在结果里给 None（前端渲染 ``--``），但字典列仍然下发。
    列名白名单用 ``ops_daily.ATOMIC_KEYS``（DDL 列名）校验，避免把任意字符串拼进 SQL。

    Args:
        conn: 只读数据库连接。

    Returns:
        tuple: (字典行 list[dict], 派生 key -> (分子列名, 分母列名))。

    Raises:
        HTTPException: 字典表缺失 / 为空时 503，避免前端拿到空列却以为没指标。
    """
    try:
        rows = [dict(r) for r in ops_daily.metric_dict_rows(conn)]
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=503, detail="运营指标字典不可用，请先导入运营数据"
        ) from exc
    if not rows:
        raise HTTPException(status_code=503, detail="运营指标字典为空，请先导入运营数据")

    whitelist = set(ops_daily.ATOMIC_KEYS)
    pairs: dict[str, tuple[str, str]] = {}
    for row in rows:
        if row["kind"] != "derived":
            continue
        parts = [p.strip() for p in row["formula"].split("/")]
        if len(parts) == 2 and all(p in whitelist for p in parts):
            pairs[row["metric_key"]] = (parts[0], parts[1])
    return rows, pairs


def _where(
    start: str, end: str, shop: Optional[str], shop_type: Optional[str]
) -> tuple[str, list[Any]]:
    """构造筛选 WHERE 子句与参数（全部参数化，值不拼进 SQL）。"""
    clauses = ["date >= ?", "date <= ?"]
    params: list[Any] = [start, end]
    if shop:
        clauses.append("shop = ?")
        params.append(shop)
    if shop_type:
        clauses.append("shop_type = ?")
        params.append(shop_type)
    return " WHERE " + " AND ".join(clauses), params


def _row_derived_exprs(pairs: dict[str, tuple[str, str]]) -> list[str]:
    """单行粒度的派生表达式：单行时 SUM 退化为单值，故直接相除。"""
    return [
        f"CASE WHEN {den} > 0 AND {num} IS NOT NULL "
        f"THEN {num} / {den} END AS {key}"
        for key, (num, den) in pairs.items()
    ]


def _summary_exprs(
    atomic_cols: list[str], pairs: dict[str, tuple[str, str]]
) -> list[str]:
    """整体粒度的聚合表达式：先对筛选范围求和，再相除。

    为什么不能逐行相除再平均：把每天的比率取平均 ≠ 整段区间的比率
    （1/1=100% 与 1/100=1% 平均是 50.5%，真实是 2/101≈2%）。
    """
    exprs = [f"SUM({col}) AS {col}" for col in atomic_cols]
    exprs.extend(
        f"CASE WHEN SUM({den}) > 0 THEN SUM({num}) / SUM({den}) END AS {key}"
        for key, (num, den) in pairs.items()
    )
    return exprs


def _atomic_cols(metrics: list[dict[str, Any]]) -> list[str]:
    """取字典里合法的原子列名（白名单校验，防 SQL 注入）。"""
    whitelist = set(ops_daily.ATOMIC_KEYS)
    return [
        m["db_column"]
        for m in metrics
        if m["kind"] == "atomic" and m["db_column"] in whitelist
    ]


def _values_from(
    raw: dict[str, Any], metrics: list[dict[str, Any]]
) -> dict[str, Optional[float]]:
    """把一行 SQL 结果映射成「metric_key -> 值」，保证键集合与字典完全一致。

    非法公式的派生项在 SELECT 里没有对应列，这里取到 None 而不是静默漏列。
    """
    values: dict[str, Optional[float]] = {}
    for metric in metrics:
        source_col = (
            metric["db_column"] if metric["kind"] == "atomic" else metric["metric_key"]
        )
        value = raw.get(source_col)
        values[metric["metric_key"]] = None if value is None else float(value)
    return values


def _monthly_points(
    conn: sqlite3.Connection, shop: Optional[str]
) -> list[OpsMonthlyPoint]:
    """按月份聚合销售额 / 访客数，并按 ``shop_type`` 拆分（方案 C 趋势图数据源）。

    为什么**不跟随日期区间**：趋势图看的是**全库月度走势**，而页面默认区间只有近
    30 天（2026-10-02~10-31）；若按 from/to 过滤，monthly 就只剩 1 个月份点 ⇒
    环比无从计算、首屏变成空态。日期筛选继续只作用于表格与 KPI 卡；该差异在前端
    副标题显式标注，避免被误认为「日期筛选失效」。「全区间」按钮也只影响表格 / KPI。
    店铺筛选**保留**：按单店查看时，图自然变成该店的月度走势。
    ``shop_type`` 筛选**忽略**：方案 C 的价值就是「自营 vs POP」结构对比；若跟随类型
    筛选，用户选了「自营」后 POP 折线会整条消失，图退化成方案 A。

    @remarks
    只下发原子合计，环比由前端纯函数现算。分组需扫库内全部行 ⇒ **SQL 扫描量随
    总行数线性增长**（诚实边界，不是与数据量解耦），但**返回行数恒为月份数**
    （当前 3 行、未来最多几十行），DOM 与响应体不受总量影响。演进线见 SPEC §4.1：
    行数 > 10 万 或 P95 > 300ms ⇒ month 生成列 / 独立月度汇总表，另立工单。

    Args:
        conn: 只读数据库连接。
        shop: 可选店铺筛选；None 表示全部店铺。

    Returns:
        list[OpsMonthlyPoint]: 按月份升序；每个点的 by_type 都含库内全部类型键。
    """
    where = ""
    params: list[Any] = []
    if shop:
        where = " WHERE shop = ?"
        params.append(shop)

    totals = conn.execute(
        "SELECT substr(date,1,7) AS month, "
        "SUM(sales_amount) AS sales_amount, SUM(visitors) AS visitors "
        f"FROM ops_shop_daily{where} GROUP BY month ORDER BY month",
        params,
    ).fetchall()
    by_type_rows = conn.execute(
        "SELECT substr(date,1,7) AS month, shop_type, "
        "SUM(sales_amount) AS amount "
        f"FROM ops_shop_daily{where} GROUP BY month, shop_type "
        "ORDER BY month, shop_type",
        params,
    ).fetchall()

    # 键集合取自实际数据，不写死 self/pop；将来新增店铺类型时前端自动多一条线。
    all_types = sorted({str(r["shop_type"]) for r in by_type_rows})
    per_month: dict[str, dict[str, Optional[float]]] = {}
    for r in by_type_rows:
        month = str(r["month"])
        buckets = per_month.setdefault(month, {t: None for t in all_types})
        buckets[str(r["shop_type"])] = (
            None if r["amount"] is None else float(r["amount"])
        )
    return [
        OpsMonthlyPoint(
            month=str(r["month"]),
            sales_amount=None if r["sales_amount"] is None else float(r["sales_amount"]),
            visitors=None if r["visitors"] is None else float(r["visitors"]),
            by_type=per_month.get(str(r["month"]), {t: None for t in all_types}),
        )
        for r in totals
    ]


@router.get("/meta", response_model=OpsMetricMeta)
def get_meta() -> OpsMetricMeta:
    """下发基准指标字典、可筛选店铺 / 类型与数据截至日期（只读）。

    Returns:
        OpsMetricMeta: 24 项字典（19 原子 + 5 派生）+ 筛选项 + ``data_through``。

    Raises:
        HTTPException: 字典或事实表不可用时 503。
    """
    with closing(_conn()) as conn:
        metrics, _ = _metric_defs(conn)
        try:
            bounds = conn.execute(
                "SELECT MIN(date) AS d_from, MAX(date) AS d_through FROM ops_shop_daily"
            ).fetchone()
            # 一次聚合同时取首尾日期，避免为「全区间」按钮再发一条 COUNT 之外的查询
            date_from, through = bounds["d_from"], bounds["d_through"]
            shops = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT shop FROM ops_shop_daily ORDER BY shop"
                )
            ]
            shop_types = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT shop_type FROM ops_shop_daily ORDER BY shop_type"
                )
            ]
            total = conn.execute("SELECT COUNT(*) FROM ops_shop_daily").fetchone()[0]
        except sqlite3.Error as exc:
            raise HTTPException(
                status_code=503, detail="运营数据表不可用，请先导入运营数据"
            ) from exc

    return OpsMetricMeta(
        metrics=[
            OpsMetricOut(
                metric_key=m["metric_key"],
                cn_name=m["cn_name"],
                source_name=m["source_name"],
                kind=m["kind"],
                db_column=m["db_column"],
                formula=m["formula"],
                unit=m["unit"],
                is_baseline=bool(m["is_baseline"]),
                sort_order=int(m["sort_order"]),
                precision=_precision_for(m["unit"]),
            )
            for m in metrics
        ],
        data_from=date_from,
        data_through=through,
        shops=shops,
        shop_types=shop_types,
        total_rows=int(total),
    )


@router.get("/data", response_model=OpsDailyData)
def get_data(
    start: str = Query(..., alias="from"),
    end: str = Query(..., alias="to"),
    shop: Optional[str] = Query(None),
    shop_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    all_rows: bool = Query(False, alias="all"),
) -> OpsDailyData:
    """按日期区间 / 店铺 / 类型查询店铺日粒度行与整体聚合（只读）。

    ``from`` / ``to`` 必填且为 ``YYYY-MM-DD``；``all=true`` 时忽略分页，用一次
    独立请求返回筛选**全量行**供导出使用（超过 10000 行直接报错，不静默截断）。

    Args:
        start: 起始日期（含），Query 别名 ``from``（Python 关键字不能作参数名）。
        end: 结束日期（含），Query 别名 ``to``。
        shop / shop_type: 可选精确筛选。``shop_type`` 的合法值来自库内
            ``DISTINCT shop_type``（由 ``/meta.shop_types`` 下发），⛔ 不在后端
            写死 self/pop —— 新增类型时前后端都应 0 改码；传入未知类型按普通
            筛选处理（返回空结果），不报 400。
        page / page_size: 服务端分页，复用项目既有 PagedRows 契约。
        all_rows: 导出模式开关，Query 别名 ``all``。

    Returns:
        OpsDailyData: 行 + 整体聚合（KPI 卡）+ 月度趋势点 + 数据截至日期。

    Raises:
        HTTPException: 参数非法 400；表不可用 503；全量超出上限 400。
    """
    if not _DATE_RE.match(start) or not _DATE_RE.match(end):
        raise HTTPException(status_code=400, detail="from / to 必须是 YYYY-MM-DD")
    if start > end:
        raise HTTPException(status_code=400, detail="from 不得晚于 to")
    # ⛔ 不写死 ("self", "pop")：类型集合的唯一来源是库内 DISTINCT shop_type，
    # /meta.shop_types 已下发给前端渲染下拉。未知类型只是筛不到行，不是参数错误。
    # 这样新增第 3 类时筛选侧与图表侧（by_type 动态键）行为一致，都无需改码。

    with closing(_conn()) as conn:
        metrics, pairs = _metric_defs(conn)
        atomic_cols = _atomic_cols(metrics)
        where, params = _where(start, end, shop, shop_type)
        try:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM ops_shop_daily{where}", params
                ).fetchone()[0]
            )
            through = conn.execute(
                "SELECT MAX(date) AS d FROM ops_shop_daily"
            ).fetchone()["d"]

            if all_rows and total > _ALL_ROWS_CAP:
                raise HTTPException(
                    status_code=400,
                    detail=f"全量导出共 {total} 行，超过 {_ALL_ROWS_CAP} 行上限，请缩小日期范围",
                )

            select_cols = ["date", "shop", "shop_type"] + atomic_cols
            select_cols += _row_derived_exprs(pairs)
            row_sql = (
                f"SELECT {', '.join(select_cols)} FROM ops_shop_daily{where} "
                "ORDER BY date, shop, shop_type"
            )
            row_params = list(params)
            effective_size = page_size
            if all_rows:
                # 导出模式：不加 LIMIT，一次取全量；page_size 回填真实行数，
                # 让前端能校验「导出行数 == total」。
                effective_size = total
            else:
                row_sql += " LIMIT ? OFFSET ?"
                row_params += [page_size, (page - 1) * page_size]
            raw_rows = [dict(r) for r in conn.execute(row_sql, row_params).fetchall()]

            summary_sql = (
                f"SELECT {', '.join(_summary_exprs(atomic_cols, pairs))} "
                f"FROM ops_shop_daily{where}"
            )
            summary_raw = dict(conn.execute(summary_sql, params).fetchone())
            # 月度趋势点与分页无关，翻页 / 全量导出请求也会带上（仅 3 行，成本可忽略）。
            # 前端每次筛选变化整体替换，翻页不会让图表闪一下或丢数据。
            monthly = _monthly_points(conn, shop)
        except sqlite3.Error as exc:
            raise HTTPException(
                status_code=503, detail="运营数据表不可用，请先导入运营数据"
            ) from exc

    return OpsDailyData(
        total=total,
        page=1 if all_rows else page,
        page_size=effective_size,
        rows=[
            OpsDailyRow(
                date=str(r["date"]),
                shop=str(r["shop"]),
                shop_type=str(r["shop_type"]),
                values=_values_from(r, metrics),
            )
            for r in raw_rows
        ],
        summary=_values_from(summary_raw, metrics),
        monthly=monthly,
        data_through=through,
    )
