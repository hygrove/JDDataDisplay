# -*- coding: utf-8 -*-
"""店铺日常运营数据（月报 JSON）→ SQLite 基准指标库。

职责边界：
  1. 把月报 JSON 规整成**店铺日粒度**的原子指标表 ``ops_shop_daily``；
  2. 把基准指标定义（含改名后的 4 个指标）写入 ``ops_metric_dict``；
  3. 为本表提供增删改查——本表**不是** ``daily_detail`` 那样的只增长表，
     允许更新与删除，且人工修订不会被下次导入覆盖；
  4. 5 个可计算指标（转化率 / 客单价 / PPC / ROI / JD联盟佣金比例）
     **不落库**，查询时按「先求和再相除」重算。

与既有表的隔离：本模块只创建 / 写入以 ``ops_`` 开头的表，
绝不读写 ``daily_detail`` 与 ``inventory_*``；导入事务前后会逐表核对
这些受保护表的内容指纹，不一致即回滚。

运行（仓库根）：
  python -m backend.jobs.ops_daily ensure
  python -m backend.jobs.ops_daily import --months 2026-08,2026-09,2026-10
  python -m backend.jobs.ops_daily list --date 2026-10-01
  python -m backend.jobs.ops_daily derived --start 2026-08-01 --end 2026-10-31 --group-by shop
  python -m backend.jobs.ops_daily update --id 1 --set sales_amount=1234.5
  python -m backend.jobs.ops_daily delete --id 1
  python -m backend.jobs.ops_daily restore --date 2026-10-01 --shop <店铺名> --shop-type self
  python -m backend.jobs.ops_daily dict
  python -m backend.jobs.ops_daily guard
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.jobs import config  # noqa: E402

# 源目录：月报 JSON 所在位置；日更文件（2026-11-04…）暂不纳入，见 ADR-0019
SOURCE_DIR: Path = config.OPS_DAILY_SOURCE_DIR

# 受保护表：本模块只做只读指纹核对，任何写入都视为事故
PROTECTED_TABLES = (
    "daily_detail",
    "inventory_daily",
    "inventory_city_slow",
    "inventory_processed_file",
    "erp_map",
    "alert_thresholds",
)

# 源 JSON 中的 5 个派生列，按基准指标约定一律不落库
DROPPED_SOURCE_FIELDS = ("转化率", "客单价", "PPC", "ROI", "佣金比例")
# 「日期」列是中文文本（如 "10月1日"），日期身份由键 "10月01日" + 文件名年份重建，故不留列
REDUNDANT_SOURCE_FIELDS = ("日期",)
# 4 个改名：旧名 → 基准指标名（字典与列名的唯一依据）
RENAMED_METRICS = {
    "成交额": "推广成交金额",
    "成交金额": "JD联盟成交金额",
    "佣金": "JD联盟佣金",
    "佣金比例": "JD联盟佣金比例",
}

_FILE_RE = re.compile(r"^(\d{4})-(\d{2})(POP|自营)店铺日常运营数据\.json$")
_DAY_KEY_RE = re.compile(r"^(\d{2})月(\d{2})日$")
_NULL_TOKENS = {"", "/", "-", "--", "—", "null", "None", "NULL"}


@dataclass(frozen=True)
class Metric:
    """一条基准指标定义。

    Attributes:
        key: 英文标识符。原子指标即数据库列名；派生指标为查询结果的键名。
        cn_name: 基准指标中文名（业务口径的唯一叫法）。
        source_name: 源 JSON 里的字段名；用于追溯改名与重算校验。
        kind: ``atomic``（可累加原始列）或 ``derived``（可计算、不落库）。
        formula: 派生指标的计算式（以 key 表达）；原子指标为空串。
        unit: 单位；源表未声明单位的留空串，不臆测。
    """

    key: str
    cn_name: str
    source_name: str
    kind: str
    formula: str = ""
    unit: str = ""


# 19 个原子列：25 个源字段 - 5 个派生 - 1 个冗余日期文本列
ATOMIC_METRICS: tuple[Metric, ...] = (
    Metric("sales_amount", "销售额", "销售额", "atomic", unit="元"),
    Metric("visitors", "访客数", "访客数", "atomic", unit="人"),
    Metric("buyers", "成交人数", "成交人数", "atomic", unit="人"),
    Metric("search_traffic", "搜索流量", "搜索流量", "atomic", unit="次"),
    Metric("compensation_amount", "赔付金额", "赔付金额", "atomic", unit="元"),
    Metric("refund_amount", "退款金额", "退款金额", "atomic", unit="元"),
    Metric("refund_duration", "退款时长", "退款时长", "atomic", unit=""),
    Metric("supplement_principal", "补单本金", "补单本金", "atomic", unit="元"),
    Metric("supplement_commission", "补单佣金", "补单佣金", "atomic", unit="元"),
    Metric("supplement_orders", "补单量", "补单量", "atomic", unit="单"),
    Metric("promo_clicks", "点击量", "点击量", "atomic", unit="次"),
    Metric("promo_order_amount", "推广成交金额", "成交额", "atomic", unit="元"),
    Metric("promo_cost", "花费", "花费", "atomic", unit="元"),
    Metric("jd_union_commission", "JD联盟佣金", "佣金", "atomic", unit="元"),
    Metric("jd_union_order_amount", "JD联盟成交金额", "成交金额", "atomic", unit="元"),
    Metric("service_consult_score", "客服咨询", "客服咨询", "atomic", unit="分"),
    Metric("logistics_score", "物流履约", "物流履约", "atomic", unit="分"),
    Metric("aftersale_score", "售后服务", "售后服务", "atomic", unit="分"),
    Metric("product_experience_score", "商品体验", "商品体验", "atomic", unit="分"),
)

# 5 个派生指标：分子 / 分母均以原子列 key 表达，分母为 0 或全 NULL 时结果为 NULL
DERIVED_METRICS: tuple[Metric, ...] = (
    Metric("conversion_rate", "转化率", "转化率", "derived", "buyers/visitors", "%"),
    Metric("avg_price", "客单价", "客单价", "derived", "sales_amount/buyers", "元"),
    Metric("ppc", "PPC", "PPC", "derived", "promo_cost/promo_clicks", "元"),
    Metric("roi", "ROI", "ROI", "derived", "promo_order_amount/promo_cost", "倍"),
    Metric(
        "jd_union_commission_rate",
        "JD联盟佣金比例",
        "佣金比例",
        "derived",
        "jd_union_commission/jd_union_order_amount",
        "%",
    ),
)

DERIVED_NUM_DEN = {m.key: tuple(m.formula.split("/")) for m in DERIVED_METRICS}
ATOMIC_KEYS = tuple(m.key for m in ATOMIC_METRICS)
ALL_METRICS = ATOMIC_METRICS + DERIVED_METRICS
SOURCE_FIELDS_EXPECTED = (
    tuple(m.source_name for m in ATOMIC_METRICS)
    + DROPPED_SOURCE_FIELDS
    + REDUNDANT_SOURCE_FIELDS
)
SHOP_TYPE_BY_TOKEN = {"自营": "self", "POP": "pop"}


def _utcnow() -> str:
    """返回 UTC ISO 时间戳（带时区），用于 created_at / updated_at。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str:
    """按块计算文件 sha256，避免大文件一次读入内存。"""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def schema_sql() -> str:
    """生成 ``ops_*`` 三张表 + 索引 + 指标字典种子数据的 DDL（幂等）。

    Returns:
        str: 可直接交给 ``conn.executescript`` 的 SQL。
    """
    atomic_cols = ",\n".join(f"    {m.key} REAL" for m in ATOMIC_METRICS)
    dict_rows = ",\n".join(
        "("
        f"'{m.key}', '{m.cn_name}', '{m.source_name}', '{m.kind}', "
        f"'{m.key if m.kind == 'atomic' else ''}', '{m.formula}', "
        f"'{m.unit}', 1, {i}"
        ")"
        for i, m in enumerate(ALL_METRICS, start=1)
    )
    return f"""
CREATE TABLE IF NOT EXISTS ops_shop_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    shop TEXT NOT NULL,
    shop_type TEXT NOT NULL CHECK (shop_type IN ('self', 'pop')),
{atomic_cols},
    source_file TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    is_manual INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (date, shop, shop_type)
);
CREATE INDEX IF NOT EXISTS idx_ops_date ON ops_shop_daily(date);
CREATE INDEX IF NOT EXISTS idx_ops_shop ON ops_shop_daily(shop);
CREATE INDEX IF NOT EXISTS idx_ops_type ON ops_shop_daily(shop_type);

CREATE TABLE IF NOT EXISTS ops_metric_dict (
    metric_key TEXT PRIMARY KEY,
    cn_name TEXT NOT NULL,
    source_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('atomic', 'derived')),
    db_column TEXT NOT NULL,
    formula TEXT NOT NULL,
    unit TEXT NOT NULL,
    is_baseline INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL
);
INSERT OR IGNORE INTO ops_metric_dict
    (metric_key, cn_name, source_name, kind, db_column, formula, unit, is_baseline, sort_order)
VALUES
{dict_rows};

CREATE TABLE IF NOT EXISTS ops_ingest_log (
    rel_path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    rows INTEGER NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_row_tombstone (
    date TEXT NOT NULL,
    shop TEXT NOT NULL,
    shop_type TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    PRIMARY KEY (date, shop, shop_type)
);
"""


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    """打开 SQLite 连接（必要时建库文件）。

    Args:
        db_path: 数据库路径；None 时用 ``config.DB_PATH``。

    Returns:
        sqlite3.Connection: 行按列名访问、外键开启的连接。
    """
    target = Path(db_path) if db_path else config.DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """幂等建表并写入基准指标字典；重复调用不改动既有数据行。

    ⚠️ 必须放在 ``with conn:`` 事务**之外**调用：``executescript()`` 会隐式
    提交当前事务，塞进事务里会让「导入失败即整体回滚」的保证失效。

    Args:
        conn: 已打开的 SQLite 连接（由调用方负责关闭）。

    Returns:
        None。

    Raises:
        sqlite3.Error: 建表或字典写入失败时由 SQLite 抛出，不做吞异常。

    Example:
        >>> with get_conn(tmp_db) as conn:  # doctest: +SKIP
        ...     pass
        >>> conn = get_conn(tmp_db)        # doctest: +SKIP
        >>> ensure_schema(conn)           # doctest: +SKIP
        >>> conn.close()                  # doctest: +SKIP
    """
    conn.executescript(schema_sql())
    conn.commit()


def parse_number(raw: Any) -> float | None:
    """把源 JSON 的字符串值清洗成数值。

    ``/``、空串等占位符 → ``None``（没抓到，必须保留为 SQL NULL）；
    ``0`` / ``0.00`` → ``0.0``（真实的零，与缺失语义不同）；
    百分号 → 小数（如 ``"7.50%"`` → ``0.075``）。

    Args:
        raw: 源 JSON 的单元格值，通常是字符串。

    Returns:
        float | None: 数值或 NULL 语义。

    Raises:
        ValueError: 出现无法解析且不是占位符的脏值时抛出，不静默吞掉。
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("￥", "").replace("¥", "")
    if text in _NULL_TOKENS:
        return None
    is_pct = text.endswith("%")
    if is_pct:
        text = text[:-1].strip()
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"无法解析的数值: {raw!r}") from exc
    return value / 100.0 if is_pct else value


def discover_monthly_files(src_dir: Path, months: Iterable[str] | None = None) -> list[Path]:
    """列出符合月报命名约定的 JSON 文件。

    只认 ``YYYY-MM(POP|自营)店铺日常运营数据.json``；日更格式
    （``2026-11-04…``）会被显式跳过，不猜测其结构（见 ADR-0019）。

    Args:
        src_dir: 源目录。
        months: 形如 ``["2026-08", "2026-10"]`` 的月份过滤；None 表示全部。

    Returns:
        list[Path]: 按文件名排序的月报文件。

    Raises:
        FileNotFoundError: 源目录不存在。
    """
    if not src_dir.is_dir():
        raise FileNotFoundError(f"运营数据目录不存在: {src_dir}")
    wanted = set(months) if months else None
    found: list[Path] = []
    for path in sorted(src_dir.glob("*.json")):
        match = _FILE_RE.match(path.name)
        if not match:
            continue
        if wanted and f"{match.group(1)}-{match.group(2)}" not in wanted:
            continue
        found.append(path)
    return found


def parse_monthly_file(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """解析单个月报 JSON，产出可直接入库的行 + 数据质量报告。

    日期身份由「文件名年份 + 日期键月份日」重建为 ``YYYY-MM-DD``；
    源里的「日期」文本列不带年份且零填充不一致，故不采信。

    Args:
        path: 月报 JSON 路径。

    Returns:
        tuple: ``(rows, quality)``。每 row 含 date/shop/shop_type/19 个原子列/
        source_file/content_hash；quality 记录派生列塌成 0 的行数与重算吻合度。

    Raises:
        ValueError: 文件名、顶层键、日期键月份或字段集合不符合约定时抛出。
    """
    match = _FILE_RE.match(path.name)
    if not match:
        raise ValueError(f"非月报文件名: {path.name}")
    year, month, token = match.group(1), match.group(2), match.group(3)
    shop_type = SHOP_TYPE_BY_TOKEN[token]
    payload = json.loads(path.read_text(encoding="utf-8"))
    root_key = f"{year}-{month}店铺日常运营数据"
    if list(payload.keys()) != [root_key]:
        raise ValueError(f"{path.name}: 顶层键应为 {root_key!r}，实为 {list(payload.keys())}")

    content_hash = _sha256_file(path)
    now = _utcnow()
    rows: list[dict[str, Any]] = []
    # 质量报告：源派生列为 0/缺失但原子列可算的行，用于暴露「假零」
    quality = {
        "rows": 0,
        "derived_zero_but_computable": {m.key: 0 for m in DERIVED_METRICS},
        "derived_rechecked": {m.key: 0 for m in DERIVED_METRICS},
        "derived_mismatch": {m.key: 0 for m in DERIVED_METRICS},
    }

    for shop, days in payload[root_key].items():
        for day_key, raw_row in days.items():
            day_match = _DAY_KEY_RE.match(day_key)
            if not day_match:
                raise ValueError(f"{path.name}: 异常日期键 {day_key!r}")
            if day_match.group(1) != month:
                raise ValueError(f"{path.name}: 日期键月份 {day_key} 与文件名月份不一致")
            missing = set(SOURCE_FIELDS_EXPECTED) - set(raw_row)
            if missing:
                raise ValueError(f"{path.name}/{shop}/{day_key}: 缺字段 {sorted(missing)}")

            row: dict[str, Any] = {
                "date": f"{year}-{month}-{day_match.group(2)}",
                "shop": shop,
                "shop_type": shop_type,
                "source_file": path.name,
                "content_hash": content_hash,
                "created_at": now,
                "updated_at": now,
            }
            for metric in ATOMIC_METRICS:
                row[metric.key] = parse_number(raw_row[metric.source_name])
            rows.append(row)
            quality["rows"] += 1

            # 只在内存里核对源派生值，绝不写入数据库
            for derived in DERIVED_METRICS:
                num_key, den_key = DERIVED_NUM_DEN[derived.key]
                numerator, denominator = row[num_key], row[den_key]
                if numerator is None or denominator in (None, 0):
                    continue
                expected = numerator / denominator
                original = parse_number(raw_row[derived.source_name])
                if original in (None, 0):
                    if abs(expected) > 1e-9:
                        quality["derived_zero_but_computable"][derived.key] += 1
                else:
                    quality["derived_rechecked"][derived.key] += 1
                    if abs(original - expected) > max(0.01, abs(expected) * 0.01):
                        quality["derived_mismatch"][derived.key] += 1
    return rows, quality


def protected_fingerprint(conn: sqlite3.Connection) -> dict[str, str | None]:
    """给受保护表拍内容指纹，用于证明导入没有碰单品表与库存表。

    逐表按 rowid 顺序取全行并哈希；表不存在时记 None。

    Args:
        conn: 已打开的数据库连接。

    Returns:
        dict[str, str | None]: 表名 → sha256 或 None。
    """
    result: dict[str, str | None] = {}
    for table in PROTECTED_TABLES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            result[table] = None
            continue
        rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
        blob = repr([tuple(r) for r in rows]).encode("utf-8")
        result[table] = hashlib.sha256(blob).hexdigest()
    return result


def _upsert_row(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """写入一行；同 key 存在且未被人工修订时更新，人工行保持不变。

    ``created_at`` 只在首次插入时写入；只有当原子值或来源**真的变了**
    才更新 ``updated_at``——否则二次导入会产生「全表被改过」的假象。
    ``is_manual`` 列不随 ETL 改写，人工修订因此能跨次导入存活。
    """
    columns = ("date", "shop", "shop_type") + ATOMIC_KEYS + (
        "source_file", "content_hash", "created_at", "updated_at",
    )
    placeholders = ", ".join("?" for _ in columns)
    col_sql = ", ".join(columns)
    update_cols = [c for c in columns if c not in ("date", "shop", "shop_type", "created_at")]
    set_sql = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
    # IS NOT 是 SQLite 的 NULL 安全比较：NULL 与 NULL 视为相同，NULL 与 5 视为不同。
    # 用它判断「是否有实质变化」，比 <> 安全（<> 遇 NULL 会得到 NULL 而非真值）。
    compare_cols = ATOMIC_KEYS + ("source_file", "content_hash")
    changed_sql = " OR ".join(
        f"ops_shop_daily.{c} IS NOT excluded.{c}" for c in compare_cols
    )
    sql = (
        f"INSERT INTO ops_shop_daily ({col_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT(date, shop, shop_type) DO UPDATE SET {set_sql} "
        f"WHERE ops_shop_daily.is_manual = 0 AND ({changed_sql})"
    )
    conn.execute(sql, tuple(row[c] for c in columns))


def import_monthly(
    conn: sqlite3.Connection,
    src_dir: Path = SOURCE_DIR,
    months: Iterable[str] | None = None,
    guard: bool = True,
) -> dict[str, Any]:
    """导入月报文件到 ``ops_shop_daily``（幂等，且受保护表零影响）。

    整个导入在一个事务内完成：前置拍受保护表指纹 → upsert → 后置核对，
    任一受保护表内容变化就抛错回滚。删除墓碑中的 key 不会被重新导入。

    Args:
        conn: 数据库连接。
        src_dir: 月报目录。
        months: 月份过滤；None 表示全部月报文件。
        guard: 是否执行受保护表前后指纹核对（验证脚本的临时库可关掉）。

    Returns:
        dict: 文件数、行数、质量报告、指纹核对结论。

    Raises:
        RuntimeError: 受保护表指纹发生变化时抛出（事务已回滚）。
        ValueError: 源数据不符合约定时抛出（事务已回滚）。
    """
    files = discover_monthly_files(src_dir, months)
    before = protected_fingerprint(conn) if guard else {}
    # executescript 会隐式 COMMIT，必须放在导入事务之外，否则整段导入不再原子。
    ensure_schema(conn)
    total = {
        "files": [p.name for p in files],
        "rows": 0,
        "quality": {},
        "protected_tables_unchanged": None,
    }
    with conn:
        tombstones = {
            (r["date"], r["shop"], r["shop_type"])
            for r in conn.execute("SELECT date, shop, shop_type FROM ops_row_tombstone")
        }
        for path in files:
            rows, quality = parse_monthly_file(path)
            inserted = 0
            for row in rows:
                key = (row["date"], row["shop"], row["shop_type"])
                if key in tombstones:
                    continue
                _upsert_row(conn, row)
                inserted += 1
            conn.execute(
                "INSERT INTO ops_ingest_log(rel_path, content_hash, rows, ingested_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(rel_path) DO UPDATE SET "
                "content_hash=excluded.content_hash, rows=excluded.rows, "
                "ingested_at=excluded.ingested_at",
                (path.name, _sha256_file(path), inserted, _utcnow()),
            )
            total["quality"][path.name] = quality
            total["rows"] += inserted
        if guard:
            after = protected_fingerprint(conn)
            if before != after:
                raise RuntimeError("受保护表内容发生变化，导入已回滚")
            total["protected_tables_unchanged"] = True
    return total


# ---- 增删改查 ----

def list_rows(
    conn: sqlite3.Connection,
    date: str | None = None,
    shop: str | None = None,
    shop_type: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    """按条件查询明细行（查）。

    Args:
        conn: 数据库连接。
        date / shop / shop_type: 精确匹配条件。
        start / end: 日期闭区间边界。
        limit: 最多返回行数。

    Returns:
        list[sqlite3.Row]: 按日期、店铺排序的行。
    """
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (("date", date), ("shop", shop), ("shop_type", shop_type)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    if start:
        clauses.append("date >= ?")
        params.append(start)
    if end:
        clauses.append("date <= ?")
        params.append(end)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM ops_shop_daily{where} ORDER BY date, shop, shop_type LIMIT ?"
    params.append(int(limit))
    return conn.execute(sql, params).fetchall()


def get_row(conn: sqlite3.Connection, row_id: int) -> sqlite3.Row | None:
    """按主键取一行（CRUD 的「查」），返回含 ``is_manual`` 等元数据的完整行。

    Args:
        conn: 已打开且已建表的 SQLite 连接。
        row_id: ``ops_shop_daily.id`` 主键。

    Returns:
        sqlite3.Row | None: 找到则返回该行；不存在返回 ``None``（不抛异常，
            便于调用方用 ``is None`` 判断而不是捕获异常）。

    Raises:
        sqlite3.Error: 表不存在或连接不可用时由 SQLite 抛出。

    Example:
        >>> row = get_row(conn, 1)       # doctest: +SKIP
        >>> row["shop"] if row else None # doctest: +SKIP
    """
    return conn.execute("SELECT * FROM ops_shop_daily WHERE id = ?", (row_id,)).fetchone()


def insert_row(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    """人工新增一行（增），自动标记为人工数据以免被下次导入覆盖。

    Args:
        conn: 数据库连接。
        payload: 必须含 date / shop / shop_type；``metrics`` 为原子指标字典。

    Returns:
        int: 新行 id。

    Raises:
        ValueError: 缺少身份字段或出现非原子指标键时抛出。
        sqlite3.IntegrityError: 同 (date, shop, shop_type) 已存在时抛出。
    """
    for required in ("date", "shop", "shop_type"):
        if not payload.get(required):
            raise ValueError(f"缺少身份字段: {required}")
    metrics = payload.get("metrics", {})
    unknown = set(metrics) - set(ATOMIC_KEYS)
    if unknown:
        raise ValueError(f"只允许写入原子指标，非法键: {sorted(unknown)}")
    now = _utcnow()
    row: dict[str, Any] = {
        "date": payload["date"],
        "shop": payload["shop"],
        "shop_type": payload["shop_type"],
        "source_file": "manual",
        "content_hash": "manual",
        "is_manual": 1,
        "created_at": now,
        "updated_at": now,
    }
    row.update({key: None for key in ATOMIC_KEYS})
    row.update(metrics)
    columns = tuple(row)
    sql = (
        f"INSERT INTO ops_shop_daily ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})"
    )
    with conn:
        cur = conn.execute(sql, tuple(row[c] for c in columns))
    return int(cur.lastrowid)


def update_row(conn: sqlite3.Connection, row_id: int, changes: dict[str, Any]) -> bool:
    """修改一行的原子指标（改），并把该行标记为人工数据。

    Args:
        conn: 数据库连接。
        row_id: 目标行主键。
        changes: 原子指标 key → 新值（允许 None 表示置空）。

    Returns:
        bool: 是否命中并更新了行。

    Raises:
        ValueError: 出现非原子指标键时抛出。
    """
    unknown = set(changes) - set(ATOMIC_KEYS)
    if unknown:
        raise ValueError(f"只允许修改原子指标，非法键: {sorted(unknown)}")
    if not changes:
        return False
    set_sql = ", ".join(f"{k} = ?" for k in changes)
    params = list(changes.values()) + [_utcnow(), row_id]
    with conn:
        cur = conn.execute(
            f"UPDATE ops_shop_daily SET {set_sql}, is_manual = 1, updated_at = ? WHERE id = ?",
            params,
        )
    return cur.rowcount > 0


def delete_row(
    conn: sqlite3.Connection,
    row_id: int | None = None,
    date: str | None = None,
    shop: str | None = None,
    shop_type: str | None = None,
) -> bool:
    """删除一行（删），同时登记墓碑，防止下次导入把它「复活」。

    Args:
        conn: 数据库连接。
        row_id: 主键；给出时优先按主键定位。
        date / shop / shop_type: 未按主键定位时的三元组身份。

    Returns:
        bool: 是否删除了行。

    Raises:
        ValueError: 既没有主键也没有完整三元组时抛出。
    """
    if row_id is not None:
        row = get_row(conn, row_id)
        if row is None:
            return False
        date, shop, shop_type = row["date"], row["shop"], row["shop_type"]
    elif not all((date, shop, shop_type)):
        raise ValueError("删除需要 --id，或同时给出 --date/--shop/--shop-type")
    with conn:
        cur = conn.execute(
            "DELETE FROM ops_shop_daily WHERE date = ? AND shop = ? AND shop_type = ?",
            (date, shop, shop_type),
        )
        if cur.rowcount:
            conn.execute(
                "INSERT INTO ops_row_tombstone(date, shop, shop_type, deleted_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(date, shop, shop_type) "
                "DO UPDATE SET deleted_at = excluded.deleted_at",
                (date, shop, shop_type, _utcnow()),
            )
    return cur.rowcount > 0


def restore_row(conn: sqlite3.Connection, date: str, shop: str, shop_type: str) -> bool:
    """撤销删除墓碑：下次导入会重新把该 ``(date, shop, shop_type)`` 写回库。

    只删墓碑记录，**不会立刻把数据行插回去**；恢复发生在下一次
    ``import_monthly()``。这样「撤销删除」与「重新摄入」共用同一条幂等路径。

    Args:
        conn: 已打开的 SQLite 连接。
        date: 日期，``YYYY-MM-DD``。
        shop: 店铺名。
        shop_type: 店铺类型（库内 ``DISTINCT shop_type`` 之一）。

    Returns:
        bool: 确实删掉了墓碑返回 ``True``；本来就没有墓碑返回 ``False``。

    Raises:
        sqlite3.Error: 写库失败时由 SQLite 抛出，``with conn:`` 会回滚。

    Example:
        >>> restore_row(conn, "2026-10-01", "示例店", "self")  # doctest: +SKIP
        True
    """
    with conn:
        cur = conn.execute(
            "DELETE FROM ops_row_tombstone WHERE date = ? AND shop = ? AND shop_type = ?",
            (date, shop, shop_type),
        )
    return cur.rowcount > 0


def derived_rows(
    conn: sqlite3.Connection,
    group_by: tuple[str, ...] = ("date",),
    start: str | None = None,
    end: str | None = None,
    shop: str | None = None,
    shop_type: str | None = None,
) -> list[dict[str, Any]]:
    """按「先求和再相除」重算 5 个派生指标（查，不落库）。

    为什么必须先求和：把每天的比率取平均 ≠ 整段区间的比率
    （例：1/1=100% 与 1/100=1% 平均是 50.5%，真实是 2/101≈2%）。

    Args:
        conn: 数据库连接。
        group_by: 分组维度白名单，只能取 date / shop / shop_type。
        start / end / shop / shop_type: 可选过滤。

    Returns:
        list[dict]: 每组一行，含分组键、5 个派生值与各自的分子分母合计。

    Raises:
        ValueError: group_by 含非法维度时抛出（防 SQL 注入）。
    """
    allowed = {"date", "shop", "shop_type"}
    if not group_by or set(group_by) - allowed:
        raise ValueError(f"group_by 只能是 date/shop/shop_type 的组合，实为 {group_by}")
    clauses: list[str] = []
    params: list[Any] = []
    if start:
        clauses.append("date >= ?")
        params.append(start)
    if end:
        clauses.append("date <= ?")
        params.append(end)
    if shop:
        clauses.append("shop = ?")
        params.append(shop)
    if shop_type:
        clauses.append("shop_type = ?")
        params.append(shop_type)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    selects = list(group_by)
    # 同一原子列可能既是某指标的分子又是另一指标的分母；先去重再生成 SUM，
    # 避免结果集出现重复列名（dict 化时会被静默覆盖）。
    needed = sorted({col for pair in DERIVED_NUM_DEN.values() for col in pair})
    selects.extend(f"SUM({col}) AS sum_{col}" for col in needed)
    for metric in DERIVED_METRICS:
        num, den = DERIVED_NUM_DEN[metric.key]
        selects.append(
            f"CASE WHEN SUM({den}) > 0 THEN SUM({num}) / SUM({den}) END AS {metric.key}"
        )
    sql = (
        f"SELECT {', '.join(selects)} FROM ops_shop_daily{where} "
        f"GROUP BY {', '.join(group_by)} ORDER BY {', '.join(group_by)}"
    )
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def metric_dict_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """读取库内基准指标字典，按 ``sort_order`` 升序（列的唯一事实来源）。

    前端列数、列名、单位、公式与精度全部由这里下发，⛔ 调用方不得另写一份
    列清单或硬编码 24 这个数字。

    Args:
        conn: 已打开且已建表的 SQLite 连接。

    Returns:
        list[sqlite3.Row]: 字典行列表（当前 24 行 = 19 原子 + 5 派生）；
            表为空时返回空列表，不抛异常。

    Raises:
        sqlite3.Error: 表不存在或连接不可用时由 SQLite 抛出。

    Example:
        >>> rows = metric_dict_rows(conn)                    # doctest: +SKIP
        >>> [r["metric_key"] for r in rows if r["kind"] == "derived"]  # doctest: +SKIP
        ['conversion_rate', 'avg_price', 'ppc', 'roi', 'jd_union_commission_rate']
    """
    return conn.execute(
        "SELECT * FROM ops_metric_dict ORDER BY sort_order"
    ).fetchall()


def snapshot_guard(
    conn: sqlite3.Connection, db_path: Path, src_dir: Path, months: list[str]
) -> dict[str, Any]:
    """生成导入前置指纹清单（供 §6 最小安全流程落盘）。

    Returns:
        dict: 库文件 sha256/大小、受保护表内容指纹、每个源文件 sha256。
    """
    files = discover_monthly_files(src_dir, months)
    return {
        "captured_at": _utcnow(),
        "db_path": str(db_path),
        "db_sha256": _sha256_file(db_path) if db_path.exists() else None,
        "db_size": db_path.stat().st_size if db_path.exists() else 0,
        "protected_tables": protected_fingerprint(conn),
        "source_files": {p.name: _sha256_file(p) for p in files},
    }


def _print_rows(rows: Iterable[sqlite3.Row]) -> None:
    """把查询行按 JSON 逐行打印，便于命令行与脚本消费。"""
    for row in rows:
        print(json.dumps(dict(row), ensure_ascii=False, default=str))


def _build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(description="店铺日常运营数据：基准指标入库与增删改查")
    parser.add_argument("--db", default=str(config.DB_PATH), help="SQLite 路径（默认生产库）")
    parser.add_argument("--src", default=str(SOURCE_DIR), help="月报 JSON 目录")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ensure", help="幂等建表并写入基准指标字典")

    p_import = sub.add_parser("import", help="导入月报文件")
    p_import.add_argument("--months", default="", help="逗号分隔月份，如 2026-08,2026-09")
    p_import.add_argument("--no-guard", action="store_true", help="关闭受保护表指纹核对（仅测试用）")
    p_import.add_argument("--no-backup", action="store_true", help="跳过备份（仅测试用）")
    p_import.add_argument("--guard-file", default="", help="前置指纹清单落盘路径")

    p_list = sub.add_parser("list", help="查询明细行")
    p_list.add_argument("--date")
    p_list.add_argument("--start")
    p_list.add_argument("--end")
    p_list.add_argument("--shop")
    p_list.add_argument("--shop-type", choices=("self", "pop"))
    p_list.add_argument("--limit", type=int, default=100)

    p_get = sub.add_parser("get", help="按主键取一行")
    p_get.add_argument("--id", type=int, required=True)

    p_insert = sub.add_parser("insert", help="人工新增一行（JSON 字符串）")
    p_insert.add_argument("--json", required=True)

    p_update = sub.add_parser("update", help="修改原子指标")
    p_update.add_argument("--id", type=int, required=True)
    p_update.add_argument("--set", action="append", default=[], dest="sets")

    p_delete = sub.add_parser("delete", help="删除一行并登记墓碑")
    p_delete.add_argument("--id", type=int)
    p_delete.add_argument("--date")
    p_delete.add_argument("--shop")
    p_delete.add_argument("--shop-type", choices=("self", "pop"))

    p_restore = sub.add_parser("restore", help="撤销删除墓碑")
    p_restore.add_argument("--date", required=True)
    p_restore.add_argument("--shop", required=True)
    p_restore.add_argument("--shop-type", choices=("self", "pop"), required=True)

    p_derived = sub.add_parser("derived", help="重算 5 个派生指标（先求和再相除）")
    p_derived.add_argument("--group-by", default="date", help="逗号分隔：date,shop,shop_type")
    p_derived.add_argument("--start")
    p_derived.add_argument("--end")
    p_derived.add_argument("--shop")
    p_derived.add_argument("--shop-type", choices=("self", "pop"))

    sub.add_parser("dict", help="打印基准指标字典")
    sub.add_parser("guard", help="打印受保护表内容指纹")
    return parser


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Returns:
        int: 0 成功；1 失败（异常信息打印到 stderr）。
    """
    args = _build_parser().parse_args(argv)
    db_path = Path(args.db)
    src_dir = Path(args.src)
    months = [m.strip() for m in args.months.split(",") if m.strip()] or None
    conn = get_conn(db_path)
    try:
        if args.command == "ensure":
            ensure_schema(conn)
            print(f"已确保 ops_* 表与基准指标字典存在：{db_path}")

        elif args.command == "import":
            wanted = months or sorted({p.name[:7] for p in discover_monthly_files(src_dir)})
            before = snapshot_guard(conn, db_path, src_dir, wanted)
            guard_path = Path(args.guard_file) if args.guard_file else (
                db_path.parent / f"ops_daily_guard_{datetime.now():%Y%m%d-%H%M%S}.json"
            )
            guard_path.parent.mkdir(parents=True, exist_ok=True)
            guard_path.write_text(
                json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"前置指纹已落盘：{guard_path}")

            backup_path = None
            if not args.no_backup and db_path.exists():
                backup_path = db_path.with_name(
                    f"{db_path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}"
                )
                shutil.copy2(db_path, backup_path)
                print(f"已备份数据库：{backup_path}")

            result = import_monthly(
                conn, src_dir, months, guard=not args.no_guard
            )
            print(json.dumps({
                "files": result["files"],
                "rows": result["rows"],
                "protected_tables_unchanged": result["protected_tables_unchanged"],
                "backup": str(backup_path) if backup_path else None,
                "假零统计": {
                    name: sum(q["derived_zero_but_computable"].values())
                    for name, q in result["quality"].items()
                },
            }, ensure_ascii=False, indent=2))

        elif args.command == "list":
            _print_rows(list_rows(conn, args.date, args.shop, args.shop_type,
                                  args.start, args.end, args.limit))

        elif args.command == "get":
            row = get_row(conn, args.id)
            print(json.dumps(dict(row), ensure_ascii=False) if row else "null")

        elif args.command == "insert":
            row_id = insert_row(conn, json.loads(args.json))
            print(f"已新增行 id={row_id}")

        elif args.command == "update":
            changes: dict[str, Any] = {}
            for item in args.sets:
                key, _, value = item.partition("=")
                changes[key.strip()] = parse_number(value) if value.strip() != "NULL" else None
            print(f"已更新：{update_row(conn, args.id, changes)}")

        elif args.command == "delete":
            print(f"已删除：{delete_row(conn, args.id, args.date, args.shop, args.shop_type)}")

        elif args.command == "restore":
            print(f"已撤销墓碑：{restore_row(conn, args.date, args.shop, args.shop_type)}")

        elif args.command == "derived":
            group_by = tuple(x.strip() for x in args.group_by.split(",") if x.strip())
            rows = derived_rows(conn, group_by, args.start, args.end, args.shop, args.shop_type)
            for row in rows:
                print(json.dumps(row, ensure_ascii=False))

        elif args.command == "dict":
            _print_rows(metric_dict_rows(conn))

        elif args.command == "guard":
            print(json.dumps(protected_fingerprint(conn), ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - CLI 需要把异常原样暴露给调度日志
        print(f"[ops_daily] 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
