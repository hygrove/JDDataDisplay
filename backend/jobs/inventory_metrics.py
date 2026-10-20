# -*- coding: utf-8 -*-
"""库存模块指标配置（唯一事实来源）。

ETL（inventory.py）与建表（db.ensure_inventory_schema）共用本模块，
绝不手写 182 列，避免漂移。

城市白名单：北京/武汉/上海/广州/成都/西安/德州/沈阳（精确等值匹配；
衍生前缀「广州补货B」「上海B仓(商)京东自营渠道」自然不命中）。

数值列（全国 15 + 8 城 × 20 = 175 个 REAL）缺失或空值写 0；
文本列（9 个身份列）缺失/空值写 NULL。
"""
from __future__ import annotations

# ---- 城市（固定白名单）----
CITIES = ["北京", "武汉", "上海", "广州", "成都", "西安", "德州", "沈阳"]
CITY_CODES = {
    "北京": "bj", "武汉": "wh", "上海": "sh", "广州": "gz",
    "成都": "cd", "西安": "xa", "德州": "dz", "沈阳": "sy",
}

# 每城 20 个指标后缀：(中文后缀, 字段代码) —— 顺序即展示顺序
CITY_SUFFIXES: list[tuple[str, str]] = [
    ("现货库存", "stock"),
    ("采购未到货", "purchase"),
    ("订单预定", "order"),
    ("数量周转", "turnover"),
    ("滞销件数", "slow"),
    ("不动销库存件数", "unsalable"),
    ("长库龄库存件数", "long_age"),
    ("0-7天库存数量", "age_0_7"),
    ("16-30天库存数量", "age_16_30"),
    ("91-120天库存数量", "age_91_120"),
    ("151-180天库存数量", "age_151_180"),
    ("昨日出库商品件数", "out_y"),
    ("近7日出库商品件数", "out_7"),
    ("近14日出库商品件数", "out_14"),
    ("近30日出库商品件数", "out_30"),
    ("月至今出库商品件数", "out_mtd"),
    ("7日有货天数", "avail_7"),
    ("14日有货天数", "avail_14"),
    ("30日有货天数", "avail_30"),
    ("预计可售天数", "sellable_days"),
]

# 全国 15 项：(中文源列名, 字段代码)
NATIONAL: list[tuple[str, str]] = [
    ("全国库存件数", "nat_stock_units"),
    ("全国现货库存", "nat_stock"),
    ("全国数量周转", "nat_turnover"),
    ("全国滞销件数", "nat_slow"),
    ("全国不动销库存件数", "nat_unsalable"),
    ("全国长库龄库存件数", "nat_long_age"),
    ("全国0-7天库存数量", "nat_age_0_7"),
    ("全国16-30天库存数量", "nat_age_16_30"),
    ("全国91-120天库存数量", "nat_age_91_120"),
    ("全国151-180天库存数量", "nat_age_151_180"),
    ("全国昨日出库商品件数", "nat_out_y"),
    ("全国近7日出库商品件数", "nat_out_7"),
    ("全国近14日出库商品件数", "nat_out_14"),
    ("全国近30日出库商品件数", "nat_out_30"),
    ("全国月至今出库商品件数", "nat_out_mtd"),
]

# 身份（文本）列：(源列名, 字段代码, 展示标题)；时间→date、SKU→sku 单独处理，
# erp_code 恒 NULL 无源列
IDENTITY: list[tuple[str | None, str, str]] = [
    ("商品名称", "sku_name", "商品名称"),
    ("SPU编码", "spu", "SPU编码"),
    ("三级类目", "category", "三级类目"),
    ("上下柜状态", "shelf_status", "上下柜状态"),
    ("销售员名称", "seller", "销售员名称"),
]


def all_columns() -> list[tuple[str, str | None, str]]:
    """返回全部 184 个 DB 列：(db_field, source_header, col_type)。

    col_type ∈ {"text", "real"}。ETL 与建表共用，绝不手工列 182 列。
    """
    cols: list[tuple[str, str | None, str]] = []
    cols.append(("date", "时间", "text"))        # 时间列取单值，存为 date
    cols.append(("shop", None, "text"))          # 由文件名解析
    cols.append(("sku", "SKU", "text"))           # 主键，浮点需清洗
    for src, fld, _title in IDENTITY:
        cols.append((fld, src, "text"))
    cols.append(("erp_code", None, "text"))       # 预留，恒 NULL
    for _src, fld in NATIONAL:
        cols.append((fld, _src, "real"))
    for city in CITIES:
        code = CITY_CODES[city]
        for suffix, scode in CITY_SUFFIXES:
            cols.append((f"{code}_{scode}", f"{city}{suffix}", "real"))
    return cols


def source_to_field() -> dict[str, str]:
    """源表头名 → DB 字段名 的映射（ETL 投影用）。"""
    mapping: dict[str, str] = {}
    for fld, src, _t in all_columns():
        if src:
            mapping[src] = fld
    return mapping


def numeric_fields() -> list[str]:
    """全部数值（REAL）字段名，用于「缺失/空值写 0」。"""
    return [fld for fld, _s, t in all_columns() if t == "real"]


def schema_sql() -> str:
    """生成 inventory_daily + inventory_processed_file 的建表 SQL（幂等）。"""
    col_defs = ",\n".join(f'    "{fld}" {"REAL" if t == "real" else "TEXT"}'
                          for fld, _s, t in all_columns())
    return f"""
CREATE TABLE IF NOT EXISTS inventory_daily (
{col_defs},
    PRIMARY KEY (date, shop, sku)
);
CREATE INDEX IF NOT EXISTS idx_inv_date ON inventory_daily(date);
CREATE INDEX IF NOT EXISTS idx_inv_shop ON inventory_daily(shop);

CREATE TABLE IF NOT EXISTS inventory_processed_file (
    rel_path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
"""


def build_metrics() -> list[dict]:
    """构建前端指标配置（配置驱动，不写死）。

    每个指标：
      key            唯一标识（per_city 用 city_<suffix_code>，national/identity 用字段名）
      title          展示标题
      kind           "identity" | "per_city" | "national"
      fields         该指标涉及的 DB 字段列表（identity/national 长度 1；per_city 长度 8，按 CITIES 顺序）
      default_visible 开页是否可见
      locked         是否锁定（身份列恒可见、不参与勾选）
      aggregatable   合计行是否对该指标求和（不合计则显 "—"）
    """
    metrics: list[dict] = []

    # 身份列（锁定，始终可见）
    for _src, fld, title in IDENTITY:
        metrics.append({
            "key": fld, "title": title, "kind": "identity",
            "fields": [fld], "default_visible": True, "locked": True, "aggregatable": False,
        })

    # 全国指标：仅 全国滞销件数 默认可见，其余 14 项进勾选区
    national_default = {"nat_slow"}
    for _src, fld in NATIONAL:
        metrics.append({
            "key": fld, "title": _src, "kind": "national",
            "fields": [fld], "default_visible": fld in national_default,
            "locked": False, "aggregatable": False,
        })

    # 每城指标：（后缀中文, 后缀代码, 展示标题, 默认可见, 合计行求和）
    per_city_spec: list[tuple[str, str, str, bool, bool]] = [
        ("现货库存", "stock", "现货库存", True, True),
        ("昨日出库商品件数", "out_y", "昨日出库", True, True),
        ("采购未到货", "purchase", "采购未到货", True, True),
        ("数量周转", "turnover", "数量周转", True, False),
        ("预计可售天数", "sellable_days", "预计可售天数", True, False),
        ("近7日出库商品件数", "out_7", "近7日出库", True, True),
        ("近14日出库商品件数", "out_14", "近14日出库", True, True),
        ("近30日出库商品件数", "out_30", "近30出库", True, True),
        ("滞销件数", "slow", "滞销件数", False, True),
        ("不动销库存件数", "unsalable", "不动销库存件数", False, True),
        ("月至今出库商品件数", "out_mtd", "月至今出库", False, True),
        ("订单预定", "order", "订单预定", False, True),
        ("长库龄库存件数", "long_age", "长库龄库存件数", False, True),
        ("0-7天库存数量", "age_0_7", "0-7天库存", False, True),
        ("16-30天库存数量", "age_16_30", "16-30天库存", False, True),
        ("91-120天库存数量", "age_91_120", "91-120天库存", False, True),
        ("151-180天库存数量", "age_151_180", "151-180天库存", False, True),
        ("7日有货天数", "avail_7", "7日有货天数", False, True),
        ("14日有货天数", "avail_14", "14日有货天数", False, True),
        ("30日有货天数", "avail_30", "30日有货天数", False, True),
    ]
    for _suffix, scode, title, dv, agg in per_city_spec:
        fields = [f"{CITY_CODES[c]}_{scode}" for c in CITIES]
        metrics.append({
            "key": f"city_{scode}", "title": title, "kind": "per_city",
            "fields": fields, "default_visible": dv, "locked": False, "aggregatable": agg,
        })
    return metrics


# 模块级常量（一次性构建，避免重复计算）
METRICS = build_metrics()
ALL_FIELD_NAMES = [fld for fld, _s, _t in all_columns()]
NUMERIC_FIELDS = numeric_fields()
