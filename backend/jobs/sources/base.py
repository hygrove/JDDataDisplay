# -*- coding: utf-8 -*-
"""统一数据源接口。新增数据源（新报表、数据库表）时实现 Source 协议即可。"""
from __future__ import annotations

from typing import Protocol

import polars as pl


class Source(Protocol):
    """数据源协议：返回统一长表。

    约定输出列（缺失的列补 None）：
      shop: str          店铺名
      date: str          YYYY-MM-DD
      spu: str
      spu_name: str | None
      category: str | None
      visitors / buyers / items / amount: float | None
      orders（成交单量）/ search_impressions（搜索曝光次数）/ search_clicks（搜索点击次数）: float | None
      promotion_cost / promotion_amount: float | None
      refund_orders / refund_amount: float | None

派生指标（avg_price / search_click_rate / conversion_rate / roi / promotion_ratio）
不在长表列中，统一由 transforms.compute_metrics 按「先求和再相除」计算。
    """

    name: str

    def fetch(self) -> pl.DataFrame:
        ...


# 统一长表的目标列（transforms 与 pipeline 共用）
# 只放「可累加」的原始指标；比率/客单价等派生指标由 transforms.compute_metrics 统一算出，
# 这样行的明细值、店铺汇总、日期汇总、多日区间汇总口径完全一致。
LONG_COLUMNS = [
    "shop", "date", "spu", "spu_name", "category",
    "visitors", "buyers", "orders", "items", "amount",
    "search_impressions", "search_clicks",
    "promotion_cost", "promotion_amount",
    "refund_orders", "refund_amount",
]
