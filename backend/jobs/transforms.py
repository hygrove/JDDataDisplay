# -*- coding: utf-8 -*-
"""Polars 清洗与聚合：统一长表 -> 层级 JSON（明细）与 summary（图表汇总）。

Polars 上手要点（详见 README）：
  - DataFrame 是不可变风格，with_columns / filter / group_by 都返回**新表**，不会原地改；
  - 表达式 API：pl.col("a").sum().over("b") 这类写法代替 pandas 的逐行操作；
  - lazy（LazyFrame）可做查询优化，本数据量用 eager 已足够。

⚠️ 本模块是整个指标口径的**后端事实来源**，与前端 frontend/src/metrics.ts 对应。
  任何比率指标都必须走 compute_metrics 的「先求和再相除」路径，禁止在别处另算一份，
  否则会出现「单日对、汇总不对」的经典口径事故。
"""
from __future__ import annotations

import polars as pl

from .models import (
    DailySummary,
    MetricRecord,
    ModuleData,
    ModuleSummary,
    ShopNode,
    ShopSummary,
    SpuNode,
    TopSpu,
)


def safe_div(a: pl.Expr, b: pl.Expr) -> pl.Expr:
    """安全除法：分母为 0 / 空 / null 时返回 None，而不是 Infinity 或 NaN。

    为什么要这样：
      直接写 a / b 在分母为 0 时会得到 inf 或 NaN，序列化成 JSON 后前端会显示成
      「Infinity / null 混乱」甚至让图表崩掉。统一退化成 None，前端按「缺失」展示 `--`。

    Args:
        a: 分子表达式（Polars Expr）。
        b: 分母表达式（Polars Expr）。

    Returns:
        pl.Expr: 一个条件表达式——分母非空且非 0 时求 a / b，否则产出 null。

    Raises:
        无（构造表达式本身不抛异常，异常在 DataFrame 求值时才可能产生）。

    Example:
        >>> df = pl.DataFrame({"x": [10, 10], "y": [2, 0]})
        >>> df.with_columns(safe_div(pl.col("x"), pl.col("y")).alias("r"))["r"].to_list()
        [5.0, None]
    """
    return pl.when(b.is_not_null() & (b != 0)).then(a / b).otherwise(None)


def compute_metrics(df: pl.DataFrame) -> pl.DataFrame:
    """基于原始可累加指标，补全部派生比率指标（新增 5 列）。

    设计要点——「先求和再相除」：
      本函数只做「本行的分子 / 本行的分母」。因为上游 aggregate() 是**先对分子分母各自求和**
      再调用本函数，所以同一段逻辑复用在「明细行 / 店铺汇总 / 单日汇总 / 多日区间汇总」时，
      四层口径天然一致。如果把比率在明细行算好再求和，汇总值就会失真（经典错误）。

      成交转化率 = 成交客户数 / 商品访客数
      客单价     = 成交金额 / 成交客户数
      搜索点击率 = 搜索点击次数 / 搜索曝光次数
      ROI        = 推广成交金额 / 推广花费
      推广占比   = 推广花费 / 成交金额

    Args:
        df: 至少含 buyers / visitors / amount / promotion_cost / promotion_amount /
            search_clicks / search_impressions 列的 DataFrame。

    Returns:
        pl.DataFrame: 原表基础上新增 conversion_rate / roi / promotion_ratio /
            avg_price / search_click_rate 五列的新表（原表不被修改）。

    Raises:
        polars.exceptions.ColumnNotFoundError: 缺少上述必需列时抛出。

    Example:
        >>> df = pl.DataFrame({"buyers": [5], "visitors": [100], "amount": [500.0],
        ...                    "promotion_cost": [50.0], "promotion_amount": [200.0],
        ...                    "search_clicks": [10.0], "search_impressions": [1000.0]})
        >>> out = compute_metrics(df)
        >>> float(out["conversion_rate"][0])
        0.05
    """
    return df.with_columns(
        # 成交转化率：衡量「访客里有多少人真的下单」，是商品页健康度首要指标
        safe_div(pl.col("buyers"), pl.col("visitors")).alias("conversion_rate"),
        # ROI：推广投入产出比，>1 表示推广赚回成本
        safe_div(pl.col("promotion_amount"), pl.col("promotion_cost")).alias("roi"),
        # 推广占比：推广花费占成交金额的比例，过高说明过度依赖付费流量
        safe_div(pl.col("promotion_cost"), pl.col("amount")).alias("promotion_ratio"),
        # 客单价：成交金额 / 成交客户数
        safe_div(pl.col("amount"), pl.col("buyers")).alias("avg_price"),
        # 搜索点击率：搜索曝光里被点开的比例，反映主图/标题吸引力
        safe_div(pl.col("search_clicks"), pl.col("search_impressions")).alias("search_click_rate"),
    )


def aggregate(df: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
    """按给定维度聚合：可累加指标求和，比率类再由 compute_metrics 重算。

    为什么比率要重算而不是对已有比率求平均：
      5% 和 50% 的平均是 27.5%，但真实汇总口径是「总客户数 / 总访客数」，两者完全不同。
      所以这里只累加原始指标，把比率的活交回 compute_metrics。

    Args:
        df: 统一长表（见 sources/base.py 的 LONG_COLUMNS）。
        keys: 分组维度列名列表，如 ["shop", "date"]、["shop", "spu"]、["shop"]。

    Returns:
        pl.DataFrame: 按 keys 分组聚合后的表，含全部原始指标求和列与重算后的比率列。

    Raises:
        polars.exceptions.ColumnNotFoundError: keys 中的列不存在，或缺少可累加指标列时抛出。

    Example:
        >>> df = pl.DataFrame({"shop": ["A", "A"], "date": ["2026-09-24", "2026-09-24"],
        ...                    "amount": [100.0, 200.0], "buyers": [1.0, 2.0], "visitors": [10.0, 20.0],
        ...                    "orders": [1.0, 2.0], "items": [1.0, 2.0],
        ...                    "search_impressions": [0.0, 0.0], "search_clicks": [0.0, 0.0],
        ...                    "promotion_cost": [0.0, 0.0], "promotion_amount": [0.0, 0.0],
        ...                    "refund_orders": [0.0, 0.0], "refund_amount": [0.0, 0.0]})
        >>> aggregate(df, ["shop", "date"])["amount"][0]
        300.0
    """
    # 只累加「可累加」的原始指标；比率不在 agg 之列（理由见函数 docstring）
    out = df.group_by(keys).agg(
        pl.col("visitors").sum(),
        pl.col("buyers").sum(),
        pl.col("orders").sum(),
        pl.col("items").sum(),
        pl.col("amount").sum(),
        pl.col("search_impressions").sum(),
        pl.col("search_clicks").sum(),
        pl.col("promotion_cost").sum(),
        pl.col("promotion_amount").sum(),
        pl.col("refund_orders").sum(),
        pl.col("refund_amount").sum(),
    )
    return compute_metrics(out)


def build_module_data(module_id: str, df: pl.DataFrame, image_map: dict[str, str]) -> ModuleData:
    """长表 -> 层级结构（店铺 -> SPU -> 日期 -> 指标），产出模块明细 JSON 的本体。

    Args:
        module_id: 模块标识，写入 ModuleData.module_id。
        df: 统一长表；函数内部会先调用 compute_metrics 补全派生指标。
        image_map: 「SPU 号 -> 图片 URL」映射；查不到时 image 为 None（前端显示「暂无图片」）。

    Returns:
        ModuleData: 含 shops 层级结构与 date_range（[最早日, 最晚日]，无数据时为空列表）。

    Raises:
        polars.exceptions.ColumnNotFoundError: 长表缺少 shop / spu / date 等必需列时抛出。
        pydantic.ValidationError: 某行数据类型不符合 MetricRecord 定义时抛出。

    Example:
        >>> data = build_module_data("pop_spu_detail", df, {"100123": "/images/100123.png"})
        >>> data.module_id
        'pop_spu_detail'
    """
    # 先补全派生指标，保证写进 JSON 的每一行都带齐比率字段
    df = compute_metrics(df)
    shops: dict[str, ShopNode] = {}
    for row in df.iter_rows(named=True):
        # setdefault 保证「同店铺同 SPU」在多日期下复用同一个节点，而不是每天新建一个
        shop = shops.setdefault(row["shop"], ShopNode(shop=row["shop"]))
        spu = shop.spus.setdefault(
            row["spu"],
            SpuNode(
                spu=row["spu"],
                spu_name=row.get("spu_name"),
                category=row.get("category"),
                image=image_map.get(row["spu"]),
            ),
        )
        # 同一 (shop, spu) 多日期时名称可能变化（如改标题），保留最新非空名称，
        # 否则后出现的空值会把前面已得到的名称覆盖成 None
        if row.get("spu_name"):
            spu.spu_name = row["spu_name"]
        spu.dates[row["date"]] = MetricRecord(
            visitors=row.get("visitors"),
            buyers=row.get("buyers"),
            orders=row.get("orders"),
            items=row.get("items"),
            amount=row.get("amount"),
            avg_price=row.get("avg_price"),
            search_impressions=row.get("search_impressions"),
            search_clicks=row.get("search_clicks"),
            search_click_rate=row.get("search_click_rate"),
            promotion_cost=row.get("promotion_cost"),
            promotion_amount=row.get("promotion_amount"),
            conversion_rate=row.get("conversion_rate"),
            roi=row.get("roi"),
            promotion_ratio=row.get("promotion_ratio"),
            refund_orders=row.get("refund_orders"),
            refund_amount=row.get("refund_amount"),
        )
    # date_range 取排序后的首尾：数据源里日期是字符串 YYYY-MM-DD，字典序即时间序
    dates = sorted(df["date"].unique().to_list())
    from datetime import datetime, timezone

    return ModuleData(
        module_id=module_id,
        updated_at=datetime.now(timezone.utc),
        date_range=[dates[0], dates[-1]] if dates else [],
        shops=shops,
    )


def build_summary(module_id: str, df: pl.DataFrame, image_map: dict[str, str], top_n: int = 10) -> ModuleSummary:
    """长表 -> 图表用 summary：店铺×日期趋势、各店铺 TOP SPU、店铺汇总（饼图）。

    Args:
        module_id: 模块标识。
        df: 统一长表。
        image_map: 「SPU 号 -> 图片 URL」映射，用于 TOP SPU 榜单配图。
        top_n: 每个店铺保留的 TOP SPU 条数，默认 10。

    Returns:
        ModuleSummary: 含 daily（逐日汇总）、top_spus（TOP 榜）、shop_totals（店铺汇总）。

    Raises:
        polars.exceptions.ColumnNotFoundError: 长表缺少必需列时抛出。

    Example:
        >>> s = build_summary("pop_spu_detail", df, {}, top_n=5)
        >>> len(s.top_spus) <= 5
        True
    """
    from datetime import datetime, timezone

    # 趋势：按「店铺+日期」聚合；排序保证画出来的折线按时间单调，不会出现来回跳的点
    daily_df = aggregate(df, ["shop", "date"]).sort(["shop", "date"])
    daily = [
        DailySummary(date=f'{r["shop"]}|{r["date"]}', **{k: r.get(k) for k in (
            "visitors", "buyers", "orders", "items", "amount",
            "avg_price", "search_impressions", "search_clicks", "search_click_rate",
            "promotion_cost", "promotion_amount",
            "conversion_rate", "roi", "promotion_ratio",
            "refund_orders", "refund_amount")})
        for r in daily_df.iter_rows(named=True)
    ]
    # date 字段携带店铺前缀（shop|date），前端按当前店铺过滤后拆分；
    # 这样 summary 文件只有一个，避免每店铺一个文件（文件数随店铺线性膨胀）。

    # TOP SPU：先按「店铺+SPU」聚合出区间累计值，再单独取每个 SPU 的名称
    spu_df = aggregate(df, ["shop", "spu"])
    # 名称用 drop_nulls().last()：同一 SPU 多行里取最后一个非空名称，避免被空值覆盖
    meta = df.group_by(["shop", "spu"]).agg(
        pl.col("spu_name").drop_nulls().last(),
    )
    spu_df = spu_df.join(meta, on=["shop", "spu"], how="left")
    top: list[TopSpu] = []
    for shop in spu_df["shop"].unique().to_list():
        # 按成交金额倒序取前 top_n：运营最关心「哪个品卖得最好」
        part = spu_df.filter(pl.col("shop") == shop).sort("amount", descending=True).head(top_n)
        for r in part.iter_rows(named=True):
            top.append(TopSpu(
                spu=r["spu"], spu_name=r.get("spu_name"), shop=shop,
                image=image_map.get(r["spu"]),
                # `or 0` 兜底：聚合结果可能为 None（该 SPU 无成交记录），榜单里按 0 展示
                amount=r["amount"] or 0, visitors=r["visitors"] or 0,
                buyers=r["buyers"] or 0, items=r["items"] or 0,
            ))

    # 店铺汇总（饼图用）：按店铺聚合后按金额倒序，占比大的店排前面
    shop_df = aggregate(df, ["shop"]).sort("amount", descending=True)
    shop_totals = [
        ShopSummary(shop=r["shop"], amount=r["amount"] or 0,
                    visitors=r["visitors"] or 0, buyers=r["buyers"] or 0,
                    items=r["items"] or 0)
        for r in shop_df.iter_rows(named=True)
    ]

    return ModuleSummary(
        module_id=module_id,
        updated_at=datetime.now(timezone.utc),
        daily=daily, top_spus=top, shop_totals=shop_totals,
    )
