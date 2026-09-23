# -*- coding: utf-8 -*-
"""PostgreSQL 数据源示例（占位）。

方案确认数据源包含数据库，这里给出可扩展骨架：
配置 PG_DSN 环境变量后，实现 fetch() 查询并返回统一长表即可。
例如后续京准通推广数据（推广费/推广成交金额）可从 DB 读取，
与 Excel 源按 (shop, date, spu) JOIN 补齐 promotion_cost / promotion_amount。
"""
from __future__ import annotations

import polars as pl

from .. import config
from .base import LONG_COLUMNS


class PostgresSource:
    name = "postgres"

    def __init__(self, dsn: str | None = None, query: str | None = None):
        self.dsn = dsn or config.PG_DSN
        self.query = query  # SQL，需产出统一长表的列

    def available(self) -> bool:
        return bool(self.dsn and self.query)

    def fetch(self) -> pl.DataFrame:
        if not self.available():
            raise RuntimeError("PostgresSource 未配置 PG_DSN 或 query")
        df = pl.read_database(self.query, self.dsn)
        for col in LONG_COLUMNS:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).alias(col))
        return df.select(LONG_COLUMNS)
