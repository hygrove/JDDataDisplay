# -*- coding: utf-8 -*-
"""统一数据源接口（协议）与统一长表列定义。

设计要点：
所有数据源（Excel / CSV / 数据库）都把各自格式**先规整成同一张长表**再交给下游，
这样 transforms 与 pipeline 不需要关心「数据是从哪来的」，新增数据源只要实现 Source 协议即可。

新增数据源步骤：
1. 在 sources/ 下新建模块，定义类并满足下面的 Source 协议（实现 name 与 fetch）；
2. fetch() 返回 polars.DataFrame，列至少包含 LONG_COLUMNS（缺失的列补 None）；
3. 在 pipeline 里把新源加入源列表即可。
"""
from __future__ import annotations

from typing import Protocol

import polars as pl


class Source(Protocol):
    """数据源协议：把任意来源的数据规整成「统一长表」。

    约定输出列（缺失的列补 None）：
      shop: str              店铺名
      date: str              日期，格式 YYYY-MM-DD
      spu: str               SPU 编号
      spu_name: str | None   商品名称（缺失时下游会降级回退到 SPU 号）
      category: str | None   类目
      visitors / buyers / orders / items / amount: float | None
                             商品访客数 / 成交客户数 / 成交单量 / 成交商品件数 / 成交金额
      search_impressions / search_clicks: float | None
                             搜索曝光次数 / 搜索点击次数
      promotion_cost / promotion_amount: float | None
                             推广花费 / 推广成交金额
      refund_orders / refund_amount: float | None
                             取消及售后退款单量 / 取消及售后退款金额

    注意：
      派生指标（avg_price / search_click_rate / conversion_rate / roi / promotion_ratio）
      **不在**长表列中，统一由 transforms.compute_metrics 按「先求和再相除」计算。
      这么做是为了保证「单日明细 / 店铺汇总 / 日期汇总 / 多日区间汇总」四层口径完全一致，
      如果在这里就把比率算好，后续任何一层再相加都会得出错误的比率。

    示例实现：
        class CsvSource:
            name = "csv"
            def fetch(self) -> pl.DataFrame:
                df = pl.read_csv("data.csv")
                for col in LONG_COLUMNS:
                    if col not in df.columns:
                        df = df.with_columns(pl.lit(None).alias(col))
                return df.select(LONG_COLUMNS)
    """

    # name：数据源标识，用于日志与错误定位（如 "excel"、"postgres"）
    name: str

    def fetch(self) -> pl.DataFrame:
        """拉取并规整数据，返回统一长表。

        Returns:
            pl.DataFrame: 列集合等于 LONG_COLUMNS 的长表；数值列允许为 None（表示该项缺失）。

        Raises:
            由具体实现决定。实现方通常在「源文件不存在 / 格式非法 / 鉴权失败」时抛出，
            建议抛 RuntimeError 或自定义异常，并带上数据源 name 便于定位。
        """
        ...


# 统一长表的目标列（transforms 与 pipeline 共用）。
# 只放「可累加」的原始指标；比率 / 客单价等派生指标统一由 transforms.compute_metrics 生成，
# 理由同上：可累加的原始值才能逐层求和，比率必须「先求和再相除」。
LONG_COLUMNS = [
    "shop", "date", "spu", "spu_name", "category",
    "visitors", "buyers", "orders", "items", "amount",
    "search_impressions", "search_clicks",
    "promotion_cost", "promotion_amount",
    "refund_orders", "refund_amount",
]
