# -*- coding: utf-8 -*-
"""POP 单品明细数据源。

目录约定（项目内固定路径）：
  {root}/数据源表目录/{店铺名}_商品明细_{起}_{止}.xlsx
  {root}/数据源表目录/{店铺名}_推广数据_{起}_{止}.csv
其中 {root} = POP_SOURCE_DIR（项目 ResourceData）。明细为 xlsx，推广为 csv。
fetch 会先读 xlsx 明细长表，再按 (shop, date, spu) 左连接 csv 推广，补充推广费/推广成交金额；
两份数据均做「去重（shop+date+spu, keep last）+ 白名单过滤」，逻辑完全一致。
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

from .. import config
from .base import LONG_COLUMNS

# 源表列名 -> 统一长表列名
COLUMN_MAP = {
    "时间": "date",
    "SPU": "spu",
    "SPU名称": "spu_name",
    "三级类目": "category",
    "商品访客数": "visitors",
    "成交客户数": "buyers",
    "成交单量": "orders",
    "成交商品件数": "items",
    "成交金额": "amount",
    "搜索曝光次数": "search_impressions",
    "搜索点击次数": "search_clicks",
    "取消及售后退款单量": "refund_orders",
    "取消及售后退款金额": "refund_amount",
    # 说明：「成交转化率」「客单价」「搜索点击率」为比率类派生指标，源表虽有同名列，
    # 但汇总时不能直接相加/取平均，统一由 transforms.compute_metrics 重算，故不入长表。
}

# 商品名称简化映射表（位于数据源根目录，非日期子目录）
NAME_MAP_FILE = "SPU商品名称映射表.xlsx"
# 店铺 SPU 登记信息（位于数据源根目录）：第一行店铺名作列头，其下每列是该店铺需要的 SPU
SPU_REGISTER_FILE = "店铺spu登记信息.xlsx"


def load_spu_name_map(root: Path | None = None) -> dict[str, str]:
    """读取 SPU 名称映射表，返回 {spu_code(str): 简化名称}。

    映射表结构：Sheet1 含 `spu` 与 `商品名称` 两列。
    找不到文件/读取失败时返回空字典（此时回退使用明细表原名称）。
    """
    src = (root or config.POP_SOURCE_DIR) / NAME_MAP_FILE
    if not src.is_file():
        return {}
    try:
        import openpyxl

        wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return {}
        header = [str(c).strip() if c is not None else "" for c in rows[0]]
        if "spu" not in header or "商品名称" not in header:
            return {}
        si, ni = header.index("spu"), header.index("商品名称")
        mapping: dict[str, str] = {}
        for r in rows[1:]:
            if not r or r[si] is None or r[ni] is None:
                continue
            key = str(r[si]).strip()
            val = str(r[ni]).strip()
            if key:
                mapping[key] = val
        return mapping
    except Exception:
        # 映射表是可选增强，任何异常都不应中断批处理
        return {}


def _norm_spu(v: object) -> str:
    """把 SPU 归一成干净字符串：去掉 Excel 数字可能带出的 .0 / 科学计数法。"""
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except (ValueError, OverflowError):
        pass
    return s


def load_spu_whitelist(root: Path | None = None) -> dict[str, set[str]]:
    """读取 店铺spu登记信息.xlsx，返回 {shop: {spu, ...}}。

    结构：第一行是店铺名称（列头），其下每列是该店铺需要的 SPU。
    找不到文件 / 读取失败时返回空 dict（此时不过滤，保留全部 SPU）。
    """
    src = (root or config.POP_SOURCE_DIR) / SPU_REGISTER_FILE
    if not src.is_file():
        return {}
    try:
        import openpyxl

        wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return {}
        header = [str(c).strip() if c is not None else "" for c in rows[0]]
        result: dict[str, set[str]] = {}
        for ri in range(1, len(rows)):
            row = rows[ri]
            for ci, shop in enumerate(header):
                if not shop:
                    continue
                val = row[ci] if ci < len(row) else None
                if val is None:
                    continue
                spu = _norm_spu(val)
                if spu:
                    result.setdefault(shop, set()).add(spu)
        return result
    except Exception:
        # 登记信息为可选裁剪，任何异常都不应中断批处理
        return {}


class PopSpuExcelSource:
    """读取固定「数据源表目录」下各店铺的 Excel 明细。"""

    name = "pop_spu_excel"

    def __init__(self, root: Path | None = None):
        self.root = root or config.POP_SOURCE_DIR

    def fetch(self) -> pl.DataFrame:
        # 固定文件夹：{root}/数据源表目录（不再遍历最新日期文件夹，保证稳定性）
        table_dir = self.root / config.POP_TABLE_DIR_NAME
        if not table_dir.is_dir():
            raise FileNotFoundError(f"数据源表目录不存在：{table_dir}")
        frames: list[pl.DataFrame] = []
        # 跳过 Excel 打开时生成的 ~$ 临时锁文件（不是有效工作簿）
        for path in sorted(p for p in table_dir.glob("*.xlsx") if not p.name.startswith("~$")):
            shop = path.name.split("_")[0]
            frames.append(self._read_one(path, shop))
        if not frames:
            raise FileNotFoundError(f"{table_dir} 下没有 xlsx 文件")
        df = pl.concat(frames, how="diagonal_relaxed")

        # —— 关联推广数据 CSV —— 按 (shop, date, spu) 左连接，补充 推广费/推广成交金额
        promo = self._fetch_promotion()
        if promo is not None:
            drop_cols = [c for c in ("promotion_cost", "promotion_amount") if c in df.columns]
            if drop_cols:
                df = df.drop(drop_cols)
            df = df.join(promo, on=["shop", "date", "spu"], how="left")

        # 补齐缺失列，统一列顺序
        for col in LONG_COLUMNS:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).alias(col))
        df = df.select(LONG_COLUMNS)

        # —— 清洗 1：去重 —— 多份明细表日期区间重叠会产生 (店铺, 日期, SPU) 重复行，
        # 以这三列作为唯一键，保留最后一次出现（文件名排序更靠后 / 日期区间更新的那份）。
        df = df.unique(subset=["shop", "date", "spu"], keep="last")

        # —— 清洗 2：多余数据过滤 —— 只保留「店铺spu登记信息.xlsx」里登记的 SPU
        whitelist = load_spu_whitelist(self.root)
        if whitelist:
            allowed = {
                (shop.strip(), _norm_spu(spu))
                for shop, spus in whitelist.items()
                for spu in spus
            }
            df = df.filter(
                pl.struct(["shop", "spu"]).map_elements(
                    lambda s: (s["shop"].strip(), _norm_spu(s["spu"])) in allowed,
                    return_dtype=pl.Boolean,
                )
            )

        # 优先使用名称映射表的简化名；映射表没有时保留明细表原名称
        name_map = load_spu_name_map(self.root)
        if name_map:
            map_df = pl.DataFrame(
                {"spu": list(name_map.keys()), "spu_name_map": list(name_map.values())},
                schema={"spu": pl.String, "spu_name_map": pl.String},
            )
            df = df.join(map_df, on="spu", how="left").with_columns(
                pl.when(pl.col("spu_name_map").is_not_null())
                .then(pl.col("spu_name_map"))
                .otherwise(pl.col("spu_name"))
                .alias("spu_name")
            ).drop("spu_name_map")
        return df

    def _read_one(self, path: Path, shop: str) -> pl.DataFrame:
        raw = pl.read_excel(path, sheet_id=0)
        # read_excel(sheet_id=0) 返回 {sheet_name: DataFrame}
        df = next(iter(raw.values())) if isinstance(raw, dict) else raw

        keep = [c for c in COLUMN_MAP if c in df.columns]
        df = df.select(keep).rename(COLUMN_MAP)

        # 剔除合计行 / 空行，只保留单日记录
        df = df.filter(
            pl.col("spu").is_not_null()
            & (pl.col("spu").cast(pl.String) != "合计")
            & pl.col("date").cast(pl.String).str.contains(r"^\d{4}-\d{2}-\d{2}$")
        )
        df = df.with_columns(
            pl.lit(shop).alias("shop"),
            pl.col("spu").cast(pl.String).map_elements(_norm_spu, return_dtype=pl.String),
            pl.col("date").cast(pl.String),
            pl.col("spu_name").cast(pl.String),
            pl.col("category").cast(pl.String),
        )
        # 指标列转数值（源表可能混入字符串/百分比/千分位）
        for col in ("visitors", "buyers", "orders", "items", "amount",
                    "search_impressions", "search_clicks",
                    "refund_orders", "refund_amount"):
            if col in df.columns:
                df = df.with_columns(
                    pl.col(col).cast(pl.String)
                    .str.replace_all(",", "")
                    .str.replace_all("%", "")
                    .cast(pl.Float64, strict=False)
                    .alias(col)
                )
        # 退款为空表示当天没有退款（JD 导出留空），按 0 处理，避免表格里大面积显示 --
        for col in ("refund_orders", "refund_amount"):
            if col in df.columns:
                df = df.with_columns(pl.col(col).fill_null(0).alias(col))
        return df

    # —— 推广数据 CSV ——
    # 列名 -> 统一长表列名（按列名选取，避免不同店铺文件列顺序不一致）
    PROMO_COL_MAP = {
        "日期": "date",
        "SPU ID": "spu",
        "花费": "promotion_cost",  # 页面卡片「推广费」
        "总订单金额": "promotion_amount",  # 页面卡片「推广成交金额」
        # 「投产比」无需落库：ROI 由 transforms 按 推广成交金额/推广费 计算（= 投产比）
    }

    def _read_promo_one(self, path: Path, shop: str) -> pl.DataFrame | None:
        """读单个推广 CSV，归一成 [shop, date, spu, promotion_cost, promotion_amount]。"""
        raw = None
        for enc in ("utf-8-sig", "gbk", "utf-8"):
            try:
                raw = pl.read_csv(path, encoding=enc, infer_schema_length=0)
                break
            except Exception:
                raw = None
        if raw is None or raw.height == 0:
            return None
        keep = [c for c in self.PROMO_COL_MAP if c in raw.columns]
        if not keep:
            return None
        df = raw.select(keep).rename({c: self.PROMO_COL_MAP[c] for c in keep})
        df = df.filter(
            pl.col("spu").is_not_null()
            & (pl.col("spu").cast(pl.String) != "合计")
        )
        # 日期 YYYYMMDD -> YYYY-MM-DD；SPU 归整（与明细表一致，保证 JOIN 命中）
        df = df.with_columns(
            pl.lit(shop).alias("shop"),
            pl.col("date").cast(pl.String).map_elements(
                lambda d: f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                if (isinstance(d, str) and d.isdigit() and len(d) == 8)
                else (str(d) if d is not None else None),
                return_dtype=pl.String,
            ).alias("date"),
            pl.col("spu").cast(pl.String).map_elements(_norm_spu, return_dtype=pl.String).alias("spu"),
        )
        for col in ("promotion_cost", "promotion_amount"):
            if col in df.columns:
                df = df.with_columns(
                    pl.col(col).cast(pl.String)
                    .str.replace_all(",", "")
                    .str.replace_all("%", "")
                    .cast(pl.Float64, strict=False)
                    .alias(col)
                )
        return df

    def _fetch_promotion(self) -> pl.DataFrame | None:
        """读全部 *_推广数据_*.csv，去重 + 白名单过滤后返回可 JOIN 的推广长表。"""
        table_dir = self.root / config.POP_TABLE_DIR_NAME
        if not table_dir.is_dir():
            return None
        frames = [
            self._read_promo_one(p, p.name.split("_")[0])
            for p in sorted(q for q in table_dir.glob("*推广数据*.csv") if not q.name.startswith("~$"))
        ]
        frames = [f for f in frames if f is not None]
        if not frames:
            return None
        df = pl.concat(frames, how="diagonal_relaxed")
        for col in ("shop", "date", "spu", "promotion_cost", "promotion_amount"):
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).alias(col))
        # 去重：多份推广表日期区间重叠（如钻芯旗舰店两份），按 (shop,date,spu) 保留最后出现的
        df = df.unique(subset=["shop", "date", "spu"], keep="last")
        # 白名单过滤（与明细完全一致）
        whitelist = load_spu_whitelist(self.root)
        if whitelist:
            allowed = {
                (s.strip(), _norm_spu(sp))
                for s, sps in whitelist.items()
                for sp in sps
            }
            df = df.filter(
                pl.struct(["shop", "spu"]).map_elements(
                    lambda s: (s["shop"].strip(), _norm_spu(s["spu"])) in allowed,
                    return_dtype=pl.Boolean,
                )
            )
        return df
