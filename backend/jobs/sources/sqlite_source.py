# -*- coding: utf-8 -*-
"""从 SQLite 还原统一长表的数据源（实现 sources/base.Source 协议）。

为什么需要它：
  批处理把 Excel 清洗后的干净长表写进 SQLite（见 jobs/db.py），
  下游 pipeline / transforms 不直接读 Excel，而是统一从 DB 取，
  这样「数据源」与「展示」解耦，且 DB 可随数据增长做增量更新，
  而不必每次都重新解析全部 Excel / CSV。

与 PopSpuExcelSource 的关系：
  PopSpuExcelSource 负责「读 Excel → 清洗 → 长表」并存库（写入方）；
  SqliteSource 负责「读 DB → 长表」喂给 transforms（读取方）。
  两者产出同一形状的 LONG_COLUMNS 长表，transforms 之后完全无感。

注：db_source.py 是 PostgreSQL 占位骨架（未来京准通直连时用），本文件专司 SQLite，
避免与未来的 PG 源冲突。
"""
from __future__ import annotations

import polars as pl

from ..db import query_long_table
from .base import LONG_COLUMNS, Source

# 数值列：从 DB 读出后显式转 float（DB 里的 REAL 在 Python 端可能是 int/None）
_NUMERIC = {
    "visitors", "buyers", "orders", "items", "amount",
    "search_impressions", "search_clicks",
    "promotion_cost", "promotion_amount",
    "refund_orders", "refund_amount",
}

# 空表 / 类型占位用的 schema：数值列 Float64，其余 String
_EMPTY_SCHEMA = {c: (pl.Float64 if c in _NUMERIC else pl.String) for c in LONG_COLUMNS}


class SqliteSource:
    """从 SQLite 读取 daily_detail，还原成与 Excel 源同构的统一长表。

    Attributes:
        name: 数据源标识，固定为 "sqlite"。

    Example:
        >>> src = SqliteSource()              # doctest: +SKIP
        >>> df = src.fetch()                  # doctest: +SKIP
        >>> set(df.columns) == set(LONG_COLUMNS)   # doctest: +SKIP
        True
    """

    name = "sqlite"

    def fetch(self) -> pl.DataFrame:
        """读出整张 daily_detail 并补齐 LONG_COLUMNS 缺失列，返回统一长表。

        Returns:
            pl.DataFrame: 列严格等于 LONG_COLUMNS 的长表；
                库为空（尚未跑过批处理）时返回 0 行、同构的空表，不抛异常。

        Example:
            >>> df = SqliteSource().fetch()    # doctest: +SKIP
            >>> df.height >= 0               # doctest: +SKIP
            True
        """
        rows = query_long_table()
        # 库为空：返回一个 0 行但列类型正确的空表，避免下游 select(LONG_COLUMNS) 报错
        if not rows:
            return pl.DataFrame(schema=_EMPTY_SCHEMA)

        df = pl.DataFrame(rows)
        # 补齐可能缺失的列，统一列顺序，保证与 Excel 源完全一致
        for col in LONG_COLUMNS:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).alias(col))
        # 数值列显式转 float（strict=False：脏值给 null 而非抛异常）
        for col in _NUMERIC:
            if col in df.columns:
                df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))
        return df.select(LONG_COLUMNS)
