# -*- coding: utf-8 -*-
"""Polars 清洗与聚合：统一长表 -> 层级 JSON / summary。

Polars 上手要点（详见 README）：
  - DataFrame 是不可变风格，with_columns/filter/group_by 都返回新表；
  - 表达式 API：pl.col("a").sum().over("b") 这类写法代替 pandas 的逐行操作；
  - lazy（LazyFrame）可做查询优化，本数据量用 eager 已足够。
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
    """安全除法：分母为 0 或空 -> None。"""
    return pl.when(b.is_not_null() & (b != 0)).then(a / b).otherwise(None)


def compute_metrics(df: pl.DataFrame) -> pl.DataFrame:
    """基于原始指标补全计算指标。

    比率/客单价一律按「先求和（或本行）再相除」计算，因此本函数作用在明细行、
    按店铺/日期聚合、按多日区间聚合时结果的口径都一致：
      成交转化率 = 成交客户数 / 商品访客数
      客单价     = 成交金额 / 成交客户数
      搜索点击率 = 搜索点击次数 / 搜索曝光次数
      ROI        = 推广成交金额 / 推广花费
      推广占比   = 推广花费 / 成交金额
    """
    return df.with_columns(
        safe_div(pl.col("buyers"), pl.col("visitors")).alias("conversion_rate"),
        safe_div(pl.col("promotion_amount"), pl.col("promotion_cost")).alias("roi"),
        safe_div(pl.col("promotion_cost"), pl.col("amount")).alias("promotion_ratio"),
        safe_div(pl.col("amount"), pl.col("buyers")).alias("avg_price"),
        safe_div(pl.col("search_clicks"), pl.col("search_impressions")).alias("search_click_rate"),
    )


def aggregate(df: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
    """按维度聚合（可累加指标先求和，比率类再由 compute_metrics 重算）。"""
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
    """长表 -> 层级结构：店铺 -> spu -> 日期 -> 指标。"""
    df = compute_metrics(df)
    shops: dict[str, ShopNode] = {}
    for row in df.iter_rows(named=True):
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
        # 同一 (shop, spu) 多日期时名称可能变化，保留最新非空名称
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
    dates = sorted(df["date"].unique().to_list())
    from datetime import datetime, timezone

    return ModuleData(
        module_id=module_id,
        updated_at=datetime.now(timezone.utc),
        date_range=[dates[0], dates[-1]] if dates else [],
        shops=shops,
    )


def build_summary(module_id: str, df: pl.DataFrame, image_map: dict[str, str], top_n: int = 10) -> ModuleSummary:
    """长表 -> 图表用 summary：店铺×日期趋势、店铺内 TOP SPU、店铺汇总。"""
    from datetime import datetime, timezone

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
    # 这样 summary 文件只有一个，避免每店铺一个文件。

    spu_df = aggregate(df, ["shop", "spu"])
    meta = df.group_by(["shop", "spu"]).agg(
        pl.col("spu_name").drop_nulls().last(),
    )
    spu_df = spu_df.join(meta, on=["shop", "spu"], how="left")
    top: list[TopSpu] = []
    for shop in spu_df["shop"].unique().to_list():
        part = spu_df.filter(pl.col("shop") == shop).sort("amount", descending=True).head(top_n)
        for r in part.iter_rows(named=True):
            top.append(TopSpu(
                spu=r["spu"], spu_name=r.get("spu_name"), shop=shop,
                image=image_map.get(r["spu"]),
                amount=r["amount"] or 0, visitors=r["visitors"] or 0,
                buyers=r["buyers"] or 0, items=r["items"] or 0,
            ))

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
