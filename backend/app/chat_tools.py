# -*- coding: utf-8 -*-
"""AI 对话的数据接地层：查询工具 schema、执行分发、结果压缩、数据概览。

ADR-0016 的落地：工具是后端既有查询函数的**只读包装**——POP 与库存两套
聚合逻辑分别复用 api / inventory_api 的路由函数，口径与页面同源（SSOT），
模型只决定"调哪个、传什么参数"，执行与口径都在项目代码里。

只读铁律：本模块**绝不写**任何业务表；包装函数捕获一切异常并转成
``{"error": ...}`` 结果——查询失败 / 空库对模型而言只是"拿到的数据
说没有"，绝不炸断对话流。

Token 纪律（成本控制）：
- 明细类工具默认小页（10 条）且硬顶（20 条），模型要更多自己翻页；
- 浮点统一四舍五入（金额 2 位 / 比率 4 位），截断图片路径等大而无用的字段；
- 每个工具的返回都是"够回答问题的最小数据面"，不是接口全量转储。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import HTTPException

from ..jobs.inventory_metrics import CITY_CODES
from . import api as pop_api
from . import inventory_api as inv_api
from .api import DayMetric

# ---------------------------------------------------------------------------
# 通用小工具：数值规整 / 参数夹取
# ---------------------------------------------------------------------------


def _round_floats(obj: Any, ndigits: int = 2) -> Any:
    """递归四舍五入所有 float（省 token；金额 2 位已远超业务精度）。"""
    if obj is None or isinstance(obj, (bool, str, int)):
        return obj
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: _round_floats(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_floats(v, ndigits) for v in obj]
    return obj


def _strip_keys(obj: Any, keys: tuple[str, ...] = ("image", "sku_pic", "erp_code")) -> Any:
    """递归剔除图片路径 / ERP 等对模型回答无用的键。"""
    if isinstance(obj, dict):
        return {k: _strip_keys(v, keys) for k, v in obj.items() if k not in keys}
    if isinstance(obj, list):
        return [_strip_keys(v, keys) for v in obj]
    return obj


def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    """把入参安全地转成区间内整数（模型给的可能是字符串 / None / 越界值）。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _opt_float(value: Any) -> Optional[float]:
    """可选数值参数：缺省 / 非法一律 None（端点会夹回默认值）。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_str(value: Any) -> Optional[str]:
    """可选字符串参数：空白视为缺省。"""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _default_module_id() -> str:
    """POP 默认模块（manifest 第一个）；空库时退回固定 ID 让端点报"暂无数据"。"""
    try:
        manifest = pop_api.get_manifest()
        if manifest.modules:
            return manifest.modules[0].module_id
    except Exception:  # noqa: BLE001 —— 空库 / 库异常都走兜底
        pass
    return "pop_spu_detail"


def _pop_module_range(module_id: str) -> tuple[Optional[str], Optional[str]]:
    """模块全区间（首日, 末日）；空库 / 模块未登记 / 查库异常返回 (None, None)。"""
    try:
        manifest = pop_api.get_manifest()
        for mod in manifest.modules:
            if mod.module_id == module_id and mod.date_range:
                return mod.date_range[0], mod.date_range[-1]
    except Exception:  # noqa: BLE001 —— 回显只是增强，取不到不阻断查询
        pass
    return None, None


def get_pop_data_end() -> Optional[str]:
    """POP 全部模块里的最新日期（"本月至今"的锚）；空库 / 异常返回 None。"""
    try:
        manifest = pop_api.get_manifest()
        ends = [mod.date_range[-1] for mod in manifest.modules if mod.date_range]
        return max(ends) if ends else None
    except Exception:  # noqa: BLE001 —— 锚点只是增强，取不到退化为"未知"
        return None


def _queried_range(
    module_id: str, start: Optional[str], end: Optional[str]
) -> list[Optional[str]]:
    """实际查询区间的回显：漏传日期 = 模块全区间。

    manifest 取不到时退化为 [None, None]（区间未知，但不阻断查询本身）。
    与 SpuAnalysisOut 自带的 date_range（实际有数据的日期）语义不同：
    这里回显的是**查询口径**，供模型核对是否等于用户所问区间。
    """
    full_start, full_end = _pop_module_range(module_id)
    return [start or full_start, end or full_end]


# ---------------------------------------------------------------------------
# POP 指标区间聚合：与 MetricRecord / transforms 同一套「先求和再相除」
# ---------------------------------------------------------------------------

# 11 个可累加原始列（与 sources.base.LONG_COLUMNS 的口径说明一致）
_POP_SUM_KEYS = (
    "visitors", "buyers", "orders", "items", "amount",
    "search_impressions", "search_clicks",
    "promotion_cost", "promotion_amount",
    "refund_orders", "refund_amount",
)


def _pop_interval_metrics(days: list[DayMetric]) -> dict[str, Any]:
    """把逐日指标聚合为区间指标。

    ⚠️ 比率一律「先分别累加分子分母再相除」（ai-rules §2 SSOT），
    与 MetricRecord 各字段的定义逐条对应——绝不逐日求平均。
    """
    sums = {k: 0.0 for k in _POP_SUM_KEYS}
    for d in days:
        m = d.metrics
        for k in _POP_SUM_KEYS:
            sums[k] += getattr(m, k, None) or 0.0

    def ratio(num: float, den: float) -> Optional[float]:
        return round(num / den, 4) if den else None

    return {
        **{k: round(v, 2) for k, v in sums.items()},
        "conversion_rate": ratio(sums["buyers"], sums["visitors"]),
        "avg_price": ratio(sums["amount"], sums["buyers"]),
        "search_click_rate": ratio(sums["search_clicks"], sums["search_impressions"]),
        "roi": ratio(sums["promotion_amount"], sums["promotion_cost"]),
        "promotion_ratio": ratio(sums["promotion_cost"], sums["amount"]),
    }


# ---------------------------------------------------------------------------
# 工具实现（全部只读；异常统一由 execute_tool 兜底转 error 结果）
# ---------------------------------------------------------------------------


def _tool_pop_module_summary(args: dict[str, Any]) -> dict[str, Any]:
    """POP 模块汇总：店铺×日期趋势、TOP SPU、店铺区间汇总。"""
    module_id = _opt_str(args.get("module_id")) or _default_module_id()
    summary = pop_api.get_summary(module_id)
    data = _strip_keys(_round_floats(summary.model_dump(mode="json")))
    data.pop("module_id", None)
    data.pop("updated_at", None)
    # 汇总是全区间口径：显式回显，防止模型把全区间数字当"本月"引用
    data["queried_range"] = _queried_range(module_id, None, None)
    return data


def _tool_pop_rows(args: dict[str, Any]) -> dict[str, Any]:
    """POP 明细分页：按店铺 / 日期区间 / 关键词过滤，按指标排序。"""
    module_id = _opt_str(args.get("module_id")) or _default_module_id()
    start = _opt_str(args.get("start"))
    end = _opt_str(args.get("end"))
    result = pop_api.get_rows(
        module_id=module_id,
        shop=_opt_str(args.get("shop")),
        start=start,
        end=end,
        date=None,
        keyword=_opt_str(args.get("keyword")),
        sort_by=_opt_str(args.get("sort_by")),
        sort_order=_opt_str(args.get("sort_order")) or "desc",
        page=_clamp_int(args.get("page"), 1, 1, 1000),
        page_size=_clamp_int(args.get("page_size"), 10, 1, 20),
    )
    rows = []
    for r in result.rows:
        # 单日模式 days 仅 1 项；区间模式承载全部数据——统一从 days 聚合
        days = r.days or ([DayMetric(date=r.date, metrics=r.metrics)] if r.date else [])
        rows.append(
            {
                "shop": r.shop,
                "spu": r.spu,
                "spu_name": r.spu_name,
                "category": r.category,
                "dates": [days[0].date, days[-1].date] if days else [],
                "metrics": _pop_interval_metrics(days),
            }
        )
    return {
        # 回显实际查询区间：漏传日期=全区间，模型据此发现自己查错了口径
        "queried_range": _queried_range(module_id, start, end),
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "rows": rows,
    }


def _tool_pop_spu_analysis(args: dict[str, Any]) -> dict[str, Any]:
    """单品分析：某 SPU 逐日指标 + 工作日/节假日对比 + 综合分析文案。"""
    spu = _opt_str(args.get("spu"))
    if not spu:
        return {"error": "缺少参数 spu（SPU 编号）"}
    module_id = _opt_str(args.get("module_id")) or _default_module_id()
    start = _opt_str(args.get("start"))
    end = _opt_str(args.get("end"))
    out = pop_api.get_spu_analysis(
        module_id=module_id,
        spu=spu,
        shop=_opt_str(args.get("shop")),
        start=start,
        end=end,
    )
    data = _strip_keys(_round_floats(out.model_dump(mode="json")))
    data["queried_range"] = _queried_range(module_id, start, end)
    return data


def _tool_inventory_summary(args: dict[str, Any]) -> dict[str, Any]:
    """库存 KPI 汇总（现货 / 在途 / 滞销等全国口径）。"""
    return _round_floats(
        inv_api.get_summary(shop=_opt_str(args.get("shop")), date=_opt_str(args.get("date")))
    )


# 库存行投影：身份列 + 全国关键指标 + 8 城现货/滞销（转中文可读名）
_INV_ID_KEYS = ("date", "shop", "sku", "sku_name", "spu", "category", "seller", "shelf_status")
_INV_NAT_KEYS = ("nat_stock", "nat_stock_units", "nat_slow", "nat_unsalable")


def _project_inv_row(row: dict[str, Any]) -> dict[str, Any]:
    """把 184 列的库存宽表行压缩成模型需要的小字典。"""
    out: dict[str, Any] = {}
    for k in _INV_ID_KEYS:
        v = row.get(k)
        if v not in (None, ""):
            out[k] = v
    for k in _INV_NAT_KEYS:
        v = row.get(k)
        if v is not None:
            out[k] = _round_floats(v)
    for city, code in CITY_CODES.items():
        for suffix, label in (("stock", "现货库存"), ("slow", "滞销件数")):
            v = row.get(f"{code}_{suffix}")
            if v is not None:
                out[f"{city}{label}"] = _round_floats(v)
    if row.get("slow_cities"):
        out["slow_cities"] = row["slow_cities"]
    return out


def _tool_inventory_rows(args: dict[str, Any]) -> dict[str, Any]:
    """库存明细分页：按店铺 / 日期 / 关键词 / 类目过滤，按指标排序。"""
    result = inv_api.get_rows(
        shop=_opt_str(args.get("shop")),
        date=_opt_str(args.get("date")),
        keyword=_opt_str(args.get("keyword")),
        # 多选参数允许数组或逗号串（端点按逗号分隔解析）
        category=_opt_str(",".join(args["category"]) if isinstance(args.get("category"), list) else args.get("category")),
        seller=_opt_str(",".join(args["seller"]) if isinstance(args.get("seller"), list) else args.get("seller")),
        shelf_status=_opt_str(",".join(args["shelf_status"]) if isinstance(args.get("shelf_status"), list) else args.get("shelf_status")),
        sort_by=_opt_str(args.get("sort_by")),
        sort_order=_opt_str(args.get("sort_order")) or "desc",
        page=_clamp_int(args.get("page"), 1, 1, 1000),
        page_size=_clamp_int(args.get("page_size"), 10, 1, 20),
    )
    return {
        "total": result["total"],
        "page": result["page"],
        "page_size": result["page_size"],
        "rows": [_project_inv_row(r) for r in result["rows"]],
        "totals": _round_floats(result.get("totals") or {}),
        "city_totals": _round_floats(result.get("city_totals") or {}),
    }


# 补货行投影：保留建议量与判定依据，剔除图片 / ERP / 分组装配
_RESTOCK_KEYS = (
    "sku", "sku_name", "shop", "city", "category", "shelf_status",
    "stock", "purchase", "out", "velocity", "velocity_kind", "avail",
    "sellable_days", "lost_sales", "gap_flag", "target", "qty", "status",
)


def _tool_inventory_restock(args: dict[str, Any]) -> dict[str, Any]:
    """补货建议清单（默认参数 9 天 / 20% / 14 日窗，可覆盖）。"""
    result = inv_api.get_restock(
        shop=_opt_str(args.get("shop")),
        date=_opt_str(args.get("date")),
        keyword=_opt_str(args.get("keyword")),
        days=_opt_float(args.get("days")),
        safety=_opt_float(args.get("safety")),
        window=_opt_float(args.get("window")),
    )
    data = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    rows = [
        {k: _round_floats(r.get(k)) for k in _RESTOCK_KEYS if r.get(k) not in (None, "")}
        for r in data.get("rows", [])
    ][:30]
    return {
        "params": _round_floats(data.get("params") or {}),
        "totals": _round_floats(data.get("totals") or {}),
        "row_count": len(data.get("rows", [])),
        "rows": rows,
    }


def _tool_inventory_alerts(args: dict[str, Any]) -> dict[str, Any]:
    """8 城库存预警汇总：每城阈值、红/黄/绿计数、建议补货合计。"""
    return _round_floats(
        inv_api.get_alerts_summary(shop=_opt_str(args.get("shop")), date=_opt_str(args.get("date")))
    )


# ---------------------------------------------------------------------------
# 工具 schema（OpenAI function calling 格式，描述即模型的"使用说明书"）
# ---------------------------------------------------------------------------

_POP_MODULE_DESC = "POP 单品明细模块的 module_id，如 pop_spu_detail；缺省用默认模块"
_DATE_DESC = "日期 YYYY-MM-DD"

TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "pop_module_summary",
            "description": (
                "POP 销售模块汇总：店铺×日期逐日趋势（daily）、TOP SPU 榜单（top_spus）、"
                "店铺区间汇总（shop_totals）。适合回答「整体卖得怎么样」「哪家店铺最强」"
                "「TOP 商品是什么」类问题。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"module_id": {"type": "string", "description": _POP_MODULE_DESC}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pop_rows",
            "description": (
                "POP 销售明细分页查询：按店铺 / 日期区间 / 关键词（SPU 号或商品名）过滤，"
                "按指标排序后取一页。适合回答「某段时间哪些商品销售额最高 / 访客最多」"
                "「某个商品卖了多少」类问题。返回的 metrics 是区间合计（比率已按先求和"
                "再相除的正确口径算好）。返回的 queried_range 是本次实际查询的日期区间"
                "（未传 start/end 即全区间）——回答前必须核对它与用户所问区间一致。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "module_id": {"type": "string", "description": _POP_MODULE_DESC},
                    "shop": {"type": "string", "description": "店铺名；缺省=全部店铺"},
                    "start": {"type": "string", "description": f"起始日期，{_DATE_DESC}"},
                    "end": {"type": "string", "description": f"截止日期，{_DATE_DESC}"},
                    "keyword": {"type": "string", "description": "SPU 编号或商品名称的模糊搜索词"},
                    "sort_by": {
                        "type": "string",
                        "description": (
                            "排序指标：visitors 访客 / buyers 成交客户 / orders 订单 / "
                            "items 件数 / amount 成交金额 / promotion_cost 推广花费 / "
                            "refund_amount 退款金额 等，也可 spu / shop"
                        ),
                    },
                    "sort_order": {"type": "string", "enum": ["asc", "desc"]},
                    "page": {"type": "integer", "description": "页码，从 1 开始"},
                    "page_size": {"type": "integer", "description": "每页条数（最大 20）"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pop_spu_analysis",
            "description": (
                "单个 SPU 的深度分析：逐日指标、工作日/节假日对比、自动生成的分析结论"
                "（insight）。适合回答「这个商品最近表现如何 / 周末和平时差多少」。"
                "建议传 start/end 限定区间，避免全区间数据过大。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "spu": {"type": "string", "description": "SPU 编号（必填）"},
                    "module_id": {"type": "string", "description": _POP_MODULE_DESC},
                    "shop": {"type": "string", "description": "店铺名；缺省=该 SPU 全部店铺合计"},
                    "start": {"type": "string", "description": f"起始日期，{_DATE_DESC}"},
                    "end": {"type": "string", "description": f"截止日期，{_DATE_DESC}"},
                },
                "required": ["spu"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inventory_summary",
            "description": (
                "库存 KPI 汇总：现货库存总量、在途、滞销等全国口径合计。适合回答"
                "「现在库存总量多少」「滞销压了多少货」类问题。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "shop": {"type": "string", "description": "店铺/业务线名；缺省=全部"},
                    "date": {"type": "string", "description": f"快照日期，{_DATE_DESC}；缺省=每店各自最新"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inventory_rows",
            "description": (
                "库存明细分页查询：按店铺 / 快照日期 / 关键词（SKU / 商品名 / SPU）过滤，"
                "按指标排序。返回每行的全国现货 / 滞销与 8 城分城数据。适合回答"
                "「哪些 SKU 库存最高 / 滞销最严重」「某商品各城市库存分布」。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "shop": {"type": "string", "description": "店铺/业务线名；缺省=全部"},
                    "date": {"type": "string", "description": f"快照日期，{_DATE_DESC}；缺省=每店各自最新"},
                    "keyword": {"type": "string", "description": "SKU / 商品名称 / SPU 模糊搜索"},
                    "category": {"type": "string", "description": "三级类目，多个逗号分隔"},
                    "seller": {"type": "string", "description": "销售员，多个逗号分隔"},
                    "shelf_status": {"type": "string", "description": "上下柜状态，多个逗号分隔"},
                    "sort_by": {
                        "type": "string",
                        "description": "排序指标 key：nat_stock 全国现货 / nat_slow 全国滞销 / bj_stock 北京现货 等",
                    },
                    "sort_order": {"type": "string", "enum": ["asc", "desc"]},
                    "page": {"type": "integer", "description": "页码，从 1 开始"},
                    "page_size": {"type": "integer", "description": "每页条数（最大 20）"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inventory_restock",
            "description": (
                "补货建议清单：按销速与安全系数算出的每 SKU × 城市建议补货量"
                "（含销速可信度、缺货损失、预警状态）。适合回答「哪些货该补了 / "
                "每个城市补多少」。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "shop": {"type": "string", "description": "店铺/业务线名；缺省=全部"},
                    "date": {"type": "string", "description": f"快照日期，{_DATE_DESC}；缺省=最新"},
                    "keyword": {"type": "string", "description": "SKU / 商品名称 / SPU 模糊搜索"},
                    "days": {"type": "number", "description": "覆盖天数，默认 9，范围 [1,60]"},
                    "safety": {"type": "number", "description": "安全系数，默认 0.2（20%）"},
                    "window": {"type": "integer", "description": "销速窗口天数：7/14/30，默认 14"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inventory_alerts",
            "description": (
                "8 城库存预警汇总：每城阈值、红/黄/绿 SKU 计数与建议补货合计。"
                "适合回答「哪个城市库存告急 / 预警最多」。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "shop": {"type": "string", "description": "店铺/业务线名；缺省=全部"},
                    "date": {"type": "string", "description": f"快照日期，{_DATE_DESC}；缺省=最新"},
                },
                "additionalProperties": False,
            },
        },
    },
]

# 工具名 → 实现的分发表（execute_tool 的唯一事实来源）
_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "pop_module_summary": _tool_pop_module_summary,
    "pop_rows": _tool_pop_rows,
    "pop_spu_analysis": _tool_pop_spu_analysis,
    "inventory_summary": _tool_inventory_summary,
    "inventory_rows": _tool_inventory_rows,
    "inventory_restock": _tool_inventory_restock,
    "inventory_alerts": _tool_inventory_alerts,
}

_TOOL_NAMES = tuple(_HANDLERS)


def execute_tool(name: str, args: Optional[dict[str, Any]]) -> dict[str, Any]:
    """执行一个查询工具，永远返回可 JSON 序列化的 dict（绝不抛出）。

    Args:
        name: 工具名（模型给出的 function name）。
        args: 模型给出的参数 dict；None / 非 dict 一律当空参。

    Returns:
        dict[str, Any]: 工具结果；查询失败 / 空库 / 参数错误时为
        ``{"error": "<面向模型的可读说明>"}``——模型据此向用户说明
        "没有数据"，而不是让对话流中断。
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"未知工具：{name}（可用：{' / '.join(_TOOL_NAMES)}）"}
    if not isinstance(args, dict):
        args = {}
    try:
        return handler(args)
    except HTTPException as e:
        # 端点主动抛的业务错误（404 暂无数据 / 模块未登记等）→ 转成模型可读结果
        return {"error": str(e.detail)}
    except Exception as e:  # noqa: BLE001 —— 接地层的存在意义：单工具失败不炸整条对话
        return {"error": f"查询失败：{type(e).__name__}: {e}"}


def build_data_overview() -> str:
    """构建系统提示词里的动态数据概览（每次对话请求现查，秒级）。

    Returns:
        str: 多行概览文本；数据未摄入时给出明确说明（AI 会如实告知用户
        "还没数据"，而不是编造）。任何异常都吞掉——概览只是增强，不承载正确性。
    """
    lines: list[str] = []
    try:
        manifest = pop_api.get_manifest()
        for mod in manifest.modules:
            dr = mod.date_range
            rng = f"{dr[0]} ~ {dr[-1]}" if len(dr) == 2 else "（无数据）"
            lines.append(
                f"- POP 销售模块「{mod.title}」：店铺 {('、'.join(mod.shops)) or '无'}；"
                f"日期 {rng}；{mod.spu_count} 个 SPU"
            )
    except Exception:  # noqa: BLE001
        lines.append("- POP 销售数据尚未摄入（空库）")
    try:
        meta = inv_api.get_meta()
        shops = meta.get("shops") or []
        if shops:
            shop_dates = meta.get("shop_dates") or {}
            sd = "、".join(f"{s} 截至 {d}" for s, d in shop_dates.items())
            lines.append(f"- 库存模块：业务线 {'、'.join(shops)}；每店最新快照：{sd or '未知'}")
        else:
            lines.append("- 库存数据尚未摄入（空库）")
    except Exception:  # noqa: BLE001
        lines.append("- 库存数据尚未摄入（空库）")
    return "\n".join(lines)
