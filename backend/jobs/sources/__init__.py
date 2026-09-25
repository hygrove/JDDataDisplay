# -*- coding: utf-8 -*-
"""数据源包：把不同来源的数据规整成「统一长表」。

新增数据源：
  1. 在本包下新建模块，实现 sources/base.py 里的 Source 协议（name + fetch）；
  2. fetch() 返回列集合等于 LONG_COLUMNS 的 polars.DataFrame（缺失列补 None）；
  3. 在 pipeline.default_registry() 里把新源挂到对应模块。

现有数据源：
  excel_source  POP 单品明细（读固定「数据源表目录」下的 xlsx 明细 + csv 推广）
  db_source     PostgreSQL 占位骨架（配置 PG_DSN 与 query 后可用）
"""
