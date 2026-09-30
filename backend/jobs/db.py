# -*- coding: utf-8 -*-
"""SQLite 持久化层：单文件嵌入式数据库，承载时序明细长表与源文件哈希追踪。

设计要点（贴合项目「单文件数据库」诉求）：
- 用标准库 sqlite3（零新依赖），数据库文件落在 config.DB_PATH（默认 backend/app/data/app.db）；
- 该目录已被 .gitignore 忽略，故数据库**不进 git**，靠 run_batch 从 Excel 重建；
- 长表只存 16 个「可累加」原始列（见 sources/base.LONG_COLUMNS），
  比率指标（转化率 / 客单价 / ROI / 推广占比 / 搜索点击率）由下游 compute_metrics
  「先求和再相除」重算，绝不入库——否则把比率相加会得到错误结果；
- processed_file 表记录每个源文件的相对路径 + sha256，用于「增量」判断：
  所有源哈希都没变 → 跳过整段摄入；任一变化 → 重算整张干净长表并 upsert。

表结构：
  daily_detail(date, shop, spu, spu_name, category, visitors, buyers, orders,
              items, amount, search_impressions, search_clicks,
              promotion_cost, promotion_amount, refund_orders, refund_amount)
      UNIQUE(date, shop, spu)
  processed_file(rel_path PRIMARY KEY, content_hash, processed_at)
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .sources.base import LONG_COLUMNS

# DB_PATH：数据库文件位置；DATA_DIR 已被 .gitignore 忽略，所以 .db 不会进仓库
DB_PATH = config.DB_PATH

# 长表列（直接复用 sources/base.LONG_COLUMNS，保证与数据源、transforms 完全一致）
DETAIL_COLUMNS = LONG_COLUMNS

# 数值列（入库后从 DB 读出时需要显式转 float，避免 int/None 类型漂移）
_NUMERIC_COLUMNS = (
    "visitors", "buyers", "orders", "items", "amount",
    "search_impressions", "search_clicks",
    "promotion_cost", "promotion_amount",
    "refund_orders", "refund_amount",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_detail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    shop TEXT NOT NULL,
    spu TEXT NOT NULL,
    spu_name TEXT,
    category TEXT,
    visitors REAL,
    buyers REAL,
    orders REAL,
    items REAL,
    amount REAL,
    search_impressions REAL,
    search_clicks REAL,
    promotion_cost REAL,
    promotion_amount REAL,
    refund_orders REAL,
    refund_amount REAL,
    UNIQUE(date, shop, spu)
);
CREATE INDEX IF NOT EXISTS idx_dd_date ON daily_detail(date);
CREATE INDEX IF NOT EXISTS idx_dd_shop ON daily_detail(shop);

CREATE TABLE IF NOT EXISTS processed_file (
    rel_path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
"""


def get_conn() -> sqlite3.Connection:
    """打开（必要时建库）SQLite 连接，配置行工厂与一致性参数。

    Returns:
        sqlite3.Connection: 已配置的连接（行按列名访问、外键开启）。

    Side Effects:
        首次调用会在 DB_PATH.parent 建目录。
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    # 行工厂设为 Row：结果可用列名访问，例如 row["shop"]
    conn.row_factory = sqlite3.Row
    # 养成习惯：开启外键约束（本库表间无外键，但避免日后踩坑）
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema() -> None:
    """幂等建表：首次运行创建 daily_detail / processed_file 及索引；重复调用安全。

    Side Effects:
        在 DB_PATH 创建数据库文件与表（若已存在则什么都不做）。
    """
    conn = get_conn()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _sha256(path: Path) -> str:
    """计算文件 sha256 十六进制串（按块读取，避免大文件一次性占满内存）。

    Args:
        path: 待哈希的文件路径。

    Returns:
        str: 64 位十六进制摘要。
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_source_files(root: Path) -> list[Path]:
    """收集所有参与摄入的源文件（根目录两张配置表 + 数据源表目录里的明细/推广）。

    Args:
        root: 数据源根目录（默认 config.POP_SOURCE_DIR）。

    Returns:
        list[Path]: 源文件路径列表（已排序，便于复现性与调试）。
    """
    files: list[Path] = []
    # 根目录的两张「配置表」：白名单 + 名称映射（变更也应触发重算）
    for name in ("店铺spu登记信息.xlsx", "SPU商品名称映射表.xlsx"):
        p = root / name
        if p.is_file():
            files.append(p)
    # 数据源表目录：各店铺明细 xlsx + 推广 csv
    table_dir = root / config.POP_TABLE_DIR_NAME
    if table_dir.is_dir():
        for p in sorted(table_dir.glob("*.xlsx")):
            if not p.name.startswith("~$"):  # 跳过 Excel 锁文件
                files.append(p)
        for p in sorted(table_dir.glob("*推广数据*.csv")):
            if not p.name.startswith("~$"):
                files.append(p)
    return files


def _rel(root: Path, path: Path) -> str:
    """把绝对路径转成相对数据源根目录的 posix 形式，作为 processed_file 的键。"""
    return path.relative_to(root).as_posix()


def ingest_excel_to_db(root: Path | None = None) -> dict[str, object]:
    """把 Excel/CSV 源**增量**摄入 SQLite，供下游 DbSource 读取。

    增量策略（文件集级别）：
      1. 收集全部源文件并计算 sha256；
      2. 与 processed_file 中已存哈希比对；
      3. 哈希完全一致且 daily_detail 已有数据 → 直接返回「无需更新」（跳过整段摄入）；
      4. 否则用 PopSpuExcelSource 重算整张干净长表，INSERT OR REPLACE 写入，
         再刷新 processed_file 哈希。

    为什么「任一文件变化就整表重算」而不是逐文件增量：
      白名单 / 名称映射是全局过滤，改一个 SPU 登记可能影响所有行；
      逐文件增量需要行级溯源，复杂度高且易错。文件级哈希门控已能在
      「数据没变就跳过」上拿到增量收益，剩余情况用幂等 upsert 保证正确。

    Args:
        root: 数据源根目录；默认 config.POP_SOURCE_DIR。

    Returns:
        dict[str, object]: {changed, files, rows, reason} 便于日志与状态展示。
    """
    from .sources.excel_source import PopSpuExcelSource  # 延迟导入，避免循环依赖

    root = root or config.POP_SOURCE_DIR
    ensure_schema()
    files = _collect_source_files(root)
    if not files:
        return {"changed": False, "files": 0, "rows": 0, "reason": "no_source_files"}

    # 计算当前所有源文件的哈希
    hashes = {_rel(root, p): _sha256(p) for p in files}
    conn = get_conn()
    try:
        cur = conn.execute("SELECT rel_path, content_hash FROM processed_file")
        old = {r["rel_path"]: r["content_hash"] for r in cur.fetchall()}
    finally:
        conn.close()

    # 哈希完全一致且库里已有数据 → 跳过（真正的「增量」收益点）
    if hashes == old and _count_rows() > 0:
        return {"changed": False, "files": len(files), "rows": _count_rows(), "reason": "unchanged"}

    # 重算整张干净长表并 upsert 进库
    df = PopSpuExcelSource(root).fetch()
    rows = _upsert_long_table(df)

    # 刷新 processed_file（清空后重写，保持与当前源文件集一致）
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    try:
        conn.execute("DELETE FROM processed_file")
        conn.executemany(
            "INSERT OR REPLACE INTO processed_file(rel_path, content_hash, processed_at) VALUES (?,?,?)",
            [(k, v, now) for k, v in hashes.items()],
        )
        conn.commit()
    finally:
        conn.close()
    return {"changed": True, "files": len(files), "rows": rows, "reason": "ingested"}


def _count_rows() -> int:
    """返回 daily_detail 当前行数（库未建立时返回 0）。"""
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) AS c FROM daily_detail").fetchone()["c"]
    finally:
        conn.close()


def _upsert_long_table(df) -> int:
    """把 polars 长表 INSERT OR REPLACE 进 daily_detail（按 date,shop,spu 唯一键）。

    Args:
        df: 列集合等于 LONG_COLUMNS 的 polars DataFrame（来自 PopSpuExcelSource）。

    Returns:
        int: 写入（含替换）的行数；空表返回 0。

    幂等性说明：
        UNIQUE(date, shop, spu) 命中时 INSERT OR REPLACE 会整行替换，
        所以同一份数据反复跑结果一致，天然幂等。
    """
    if df.height == 0:
        return 0
    conn = get_conn()
    try:
        data = df.select(DETAIL_COLUMNS).to_dicts()
        col_list = ", ".join(DETAIL_COLUMNS)
        placeholders = ", ".join(["?"] * len(DETAIL_COLUMNS))
        sql = f"INSERT OR REPLACE INTO daily_detail ({col_list}) VALUES ({placeholders})"
        conn.executemany(sql, [[row.get(c) for c in DETAIL_COLUMNS] for row in data])
        conn.commit()
        return len(data)
    finally:
        conn.close()


def query_long_table() -> list[dict]:
    """读出整张干净长表（供 DbSource 还原成 polars DataFrame）。

    Returns:
        list[dict]: 每行一个 dict，键为 DETAIL_COLUMNS；库为空时返回空 list。
    """
    conn = get_conn()
    try:
        cur = conn.execute(f"SELECT {', '.join(DETAIL_COLUMNS)} FROM daily_detail")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
