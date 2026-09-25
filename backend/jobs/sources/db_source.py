# -*- coding: utf-8 -*-
"""PostgreSQL 数据源（可扩展骨架 / 占位实现）。

用途：
方案里的京准通推广数据（推广花费 promotion_cost、推广成交金额 promotion_amount）
如果后续能从数据库直读，就在这里实现 fetch()，产出的长表会与 Excel 源
按 (shop, date, spu) 做 JOIN 补齐推广相关字段。

当前状态：
默认不启用——只有同时配置了 PG_DSN 和 query 时 available() 才返回 True，
否则 pipeline 会跳过本源，不会因缺少数据库而中断整条批处理。
"""
from __future__ import annotations

import polars as pl

from .. import config
from .base import LONG_COLUMNS


class PostgresSource:
    """从 PostgreSQL 读取业务数据，规整成统一长表的数据源。

    Attributes:
        name: 数据源标识，固定为 "postgres"。
        dsn: PostgreSQL 连接串；为 None 时回退到 config.PG_DSN。
        query: 查询 SQL，需产出统一长表所需的列（至少包含 LONG_COLUMNS 中的业务列）。

    Example:
        >>> src = PostgresSource(
        ...     dsn="postgresql://user:pwd@host:5432/db",
        ...     query="SELECT shop, date, spu, promotion_cost FROM promo_daily",
        ... )
        >>> src.available()
        True
    """

    name = "postgres"

    def __init__(self, dsn: str | None = None, query: str | None = None):
        """初始化数据库数据源。

        Args:
            dsn: PostgreSQL 连接串；可选，传 None 时使用 config.PG_DSN（环境变量）。
            query: 查询 SQL；可选，需产出统一长表的列。两个参数都不传时本源不可用。

        Returns:
            None
        """
        # dsn 允许调用方显式传入（便于测试），否则统一走环境变量配置
        self.dsn = dsn or config.PG_DSN
        # query 必须由调用方给出：不同业务的取数 SQL 差异很大，框架侧不内置默认查询
        self.query = query

    def available(self) -> bool:
        """判断本数据源是否可用（是否已完整配置）。

        Returns:
            bool: dsn 与 query 都非空才返回 True；否则 False，pipeline 会跳过本源。

        Example:
            >>> PostgresSource().available()   # 未配置任何环境变量
            False
        """
        return bool(self.dsn and self.query)

    def fetch(self) -> pl.DataFrame:
        """执行 SQL 并把结果规整为统一长表。

        Returns:
            pl.DataFrame: 列顺序与内容严格等于 LONG_COLUMNS 的长表。

        Raises:
            RuntimeError: 未配置 dsn 或 query（即 available() 为 False）时抛出，
                提示调用方先配置 PG_DSN 与 query。
            Exception: polars.read_database 透传的数据库连接 / SQL 语法 / 权限错误。

        Example:
            >>> src = PostgresSource("postgresql://u:p@h/db", "SELECT shop, date, spu FROM t")
            >>> df = src.fetch()   # 返回含 LONG_COLUMNS 全部列的长表
        """
        # 前置校验放在执行 SQL 之前：避免带着空 query 去连库，报错更难定位
        if not self.available():
            raise RuntimeError("PostgresSource 未配置 PG_DSN 或 query")

        df = pl.read_database(self.query, self.dsn)

        # 补列：真实 SQL 往往只 SELECT 关心的字段，缺失列统一补 None，
        # 保证下游永远拿到结构一致的长表（列不齐会在 select 时直接报 KeyError）
        for col in LONG_COLUMNS:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).alias(col))

        # 只保留长表列，丢弃 SQL 里可能多查出来的辅助字段，避免污染下游
        return df.select(LONG_COLUMNS)
