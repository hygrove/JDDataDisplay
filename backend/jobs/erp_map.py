# -*- coding: utf-8 -*-
"""ERP 编码映射：库存 SKU → ERP 编码。

数据来源是数据源根目录下的 `ERP编码表.xlsx`（静态对照表，与库存宽表无关：
库存宽表的行是「日期 × 店铺 × SKU」快照，ERP 映射不随日期变）。
本模块只负责「读源表 → 写映射表 → 供查询」，不参与任何补货计算。

关键语义（见 docs/adr/0009-erp-mapping-separate-table.md）：
- **缺失映射不写行**。有行 = 已登记，无行 = 未登记；「未登记」与「ERP 编码是 0」
  永远是两件事，库里绝不把缺失写成 0。
- 页面把未登记显示成「--」，导出时写 0（下游模板占位）——差异是有意的。
- 本表是源表的镜像：源表删掉的映射，库里也不留（否则库里存的是假事实）。
  它由源表随时可重建，源表始终只读不动。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import config

# 源表文件名（位于数据源根目录，与「店铺spu登记信息.xlsx」同级）
ERP_MAP_FILE_NAME = "ERP编码表.xlsx"

# 批量查 ERP 编码时的分片大小（SQLite 变量数上限 999，留足余量）
_QUERY_CHUNK = 400

# 断崖护栏：源表有效映射少于库里现有条数的这个比例时，只增不删。
# 源表条数骤减多半意味着表被改坏/写错了，此时删光已有映射比留着旧映射危险得多。
_SHRINK_GUARD_RATIO = 0.5

# 结构性护栏：解析出的有效映射少于源表有效行数的这个比例时，本次不写入。
# 典型的触发场景是源表列角色颠倒（SKU 位全是 ERP 编码形态）。
_PARSE_GUARD_RATIO = 0.5


def erp_map_schema_sql() -> str:
    """生成 erp_map 建表 SQL（幂等，可重复执行）。

    Returns:
        str: 建表语句（IF NOT EXISTS，重复执行安全）。
    """
    return """
CREATE TABLE IF NOT EXISTS erp_map (
    sku       TEXT PRIMARY KEY,   -- 库存 SKU（与 inventory_daily.sku 逐字符相等）
    erp_code  TEXT NOT NULL       -- ERP 编码；无映射则该 SKU 无行
);
"""


def _normalize_sku(value: Any) -> str:
    """把单元格里的 SKU 规整成字符串键。

    Excel 常把长数字读成 float（`100149827444.0`），必须去掉 `.0` 后缀，
    否则键与库存宽表的 SKU 对不上，映射永远命中不了。

    库存 SKU 是数字串或平台编码（如 `EMG4418927463633`），但**不含连字符**；
    而 ERP 编码几乎都含连字符（256 行里 255 行）。因此 SKU 位出现连字符即说明
    源表列角色识别反了，这种行一律丢弃——宁可当成未登记，也不能造出对不上的映射。

    Args:
        value: 单元格原始值（数字 / 字符串 / None）。

    Returns:
        str: 规整后的 SKU 文本；空值或含连字符的值返回空串。

    Example:
        >>> _normalize_sku(100149827444.0)
        '100149827444'
        >>> _normalize_sku("ZX-600GZDCXDSROJ")
        ''
    """
    if value is None:
        return ""
    if isinstance(value, float):
        text = str(int(value)) if value.is_integer() else str(value).strip()
    else:
        text = str(value).strip()
        if text.endswith(".0") and text[:-2].isdigit():
            text = text[:-2]
    if not text or "-" in text:
        return ""
    return text


def _normalize_code(value: Any) -> str:
    """把单元格里的 ERP 编码规整成字符串。

    与 SKU 同理，Excel 可能把编码读成 float（`12345.0`），去掉 `.0` 后缀。

    Args:
        value: 单元格原始值。

    Returns:
        str: 规整后的编码；空值返回空串。

    Example:
        >>> _normalize_code(12345.0)
        '12345'
    """
    if value is None:
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value).strip()
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _header_indexes(header: Sequence[Any]) -> tuple[int, int]:
    """从表头里定位 SKU 列与 ERP 编码列。

    SKU 列取表头里含 "sku" 的列，ERP 列取表头里含 "erp" 的列（大小写不敏感）；
    识别不到时按「第一列 = SKU、第二列 = ERP 编码」处理。

    Args:
        header: 表头单元格序列。

    Returns:
        tuple[int, int]: (SKU 列下标, ERP 编码列下标)。
    """
    names = [str(h).strip().lower() if h is not None else "" for h in header]
    sku_idx = next((i for i, h in enumerate(names) if "sku" in h), 0)
    erp_idx = next((i for i, h in enumerate(names) if "erp" in h), 1)
    return sku_idx, erp_idx


def parse_erp_pairs(rows: Iterable[Sequence[Any]]) -> dict[str, str]:
    """把 ERP 编码表的行解析成 {sku: erp_code}。

    表头识别：SKU 列取表头里含 "sku" 的列，ERP 列取表头里含 "erp" 的列
    （大小写不敏感）；识别不到时按「第一列 = SKU、第二列 = ERP 编码」处理。
    空 ERP 值、空 SKU 一律跳过——它们代表"未登记"，不写进映射。
    **自指编码（ERP 编码 == SKU）与含连字符的 SKU 一律丢弃**：那只会是列角色
    识别走偏的产物，假编码会被下游当真值用（ADR-0009）。

    Args:
        rows: 源表行序列，首行是表头。

    Returns:
        dict[str, str]: SKU → ERP 编码；同一 SKU 多次出现时后者覆盖前者。

    Example:
        >>> parse_erp_pairs([("sku", "ERP编码"), (1001, "JD-10C-PKCTO")])
        {'1001': 'JD-10C-PKCTO'}
    """
    data = list(rows)
    if not data:
        return {}
    sku_idx, erp_idx = _header_indexes(data[0])
    pairs: dict[str, str] = {}
    for row in data[1:]:
        if len(row) <= max(sku_idx, erp_idx):
            continue
        sku = _normalize_sku(row[sku_idx])
        code = _normalize_code(row[erp_idx])
        if not sku or not code:
            continue
        if code == sku:
            continue  # 自指 = 列角色识别走偏，不写进库
        pairs[sku] = code
    return pairs


def count_valid_source_rows(rows: Iterable[Sequence[Any]]) -> int:
    """数源表里 SKU 与编码单元格都非空的数据行数（不含表头）。

    用于结构性护栏：解析出的映射数远少于源表有效行数，说明表被改坏了
    （典型：列角色颠倒），此时不该往库里写任何东西。

    Args:
        rows: 源表行序列，首行是表头。

    Returns:
        int: 有效数据行数。
    """
    data = list(rows)
    if not data:
        return 0
    sku_idx, erp_idx = _header_indexes(data[0])
    count = 0
    for row in data[1:]:
        if len(row) <= max(sku_idx, erp_idx):
            continue
        if not str(row[sku_idx] or "").strip() or not str(row[erp_idx] or "").strip():
            continue
        count += 1
    return count


def read_erp_rows(path: Path) -> list[tuple]:
    """读 ERP 编码表的全部行（含表头）。

    Args:
        path: ERP 编码表路径（应为 .xlsx）。

    Returns:
        list[tuple]: 源表行序列。

    Raises:
        FileNotFoundError: 文件不存在。
        Exception: openpyxl 读取失败（源表损坏 / 被 Excel 占用）时向上抛出，
            由调用方决定是跳过还是中断。
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        return [tuple(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def read_erp_pairs(path: Path) -> dict[str, str]:
    """读 ERP 编码表 xlsx，返回 {sku: erp_code}。

    Args:
        path: ERP 编码表路径（应为 .xlsx）。

    Returns:
        dict[str, str]: SKU → ERP 编码。

    Example:
        >>> pairs = read_erp_pairs(Path("ResourceData/ERP编码表.xlsx"))
        >>> len(pairs) > 0
        True
    """
    return parse_erp_pairs(read_erp_rows(path))


def upsert_erp_codes(conn: sqlite3.Connection, pairs: dict[str, str]) -> int:
    """幂等写入映射（同一 SKU 覆盖）。

    Args:
        conn: SQLite 连接。
        pairs: SKU → ERP 编码。

    Returns:
        int: 写入条数。

    Example:
        >>> conn = sqlite3.connect(":memory:")
        >>> conn.executescript(erp_map_schema_sql())
        >>> upsert_erp_codes(conn, {"1001": "A"})
        1
    """
    if not pairs:
        return 0
    conn.executemany(
        "INSERT INTO erp_map (sku, erp_code) VALUES (?, ?) "
        "ON CONFLICT(sku) DO UPDATE SET erp_code = excluded.erp_code",
        list(pairs.items()),
    )
    return len(pairs)


def lookup_erp_codes(conn: sqlite3.Connection, skus: Iterable[Any]) -> dict[str, str]:
    """批量查 ERP 编码。

    Args:
        conn: SQLite 连接。
        skus: SKU 列表（可含 None / 空串，会被忽略）。

    Returns:
        dict[str, str]: 仅包含**已登记**的 SKU；未登记的 SKU 不出现在结果里。

    Example:
        >>> conn = sqlite3.connect(":memory:")
        >>> conn.executescript(erp_map_schema_sql())
        >>> upsert_erp_codes(conn, {"1001": "A"})
        1
        >>> lookup_erp_codes(conn, ["1001", "1002"])
        {'1001': 'A'}
    """
    keys = list(dict.fromkeys(k for k in (_normalize_sku(s) for s in skus) if k))
    if not keys:
        return {}
    found: dict[str, str] = {}
    for i in range(0, len(keys), _QUERY_CHUNK):
        chunk = keys[i : i + _QUERY_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT sku, erp_code FROM erp_map WHERE sku IN ({placeholders})", chunk
        ).fetchall()
        for r in rows:
            found[str(r[0])] = str(r[1])
    return found


def ingest_erp_map(
    root: Path | None = None, conn: sqlite3.Connection | None = None
) -> dict[str, Any]:
    """把 ERP 编码表摄入 erp_map（幂等，本表是源表的镜像）。

    源表缺失时**不动库**（返回 changed=False），避免一次误操作抹掉全部映射；
    源表存在但解析不出有效映射时同样不动库。
    源表条数**断崖式减少**（少于库里现有条数的一半）时只增不删——那多半是
    源表被改坏了，此时删光已有映射比留着旧映射危险得多。

    Args:
        root: 数据源根目录；None 表示用 config.POP_SOURCE_DIR。
        conn: SQLite 连接；None 表示自开连接（用 config.DB_PATH）。

    Returns:
        dict[str, Any]: {"changed": bool, "rows": int, "removed": int, "reason": str}。
        reason 取值：ok / source_missing / source_empty / shrink_guard。

    Raises:
        Exception: openpyxl 读取源表失败（文件损坏 / 被 Excel 占用）时向上抛出，
            由调用方决定跳过还是中断。

    Example:
        >>> result = ingest_erp_map()
        >>> result["reason"] in {"ok", "source_missing", "source_empty", "shrink_guard"}
        True
    """
    base = Path(root) if root is not None else config.POP_SOURCE_DIR
    path = base / ERP_MAP_FILE_NAME
    if not path.is_file():
        return {"changed": False, "rows": 0, "removed": 0, "reason": "source_missing"}
    rows = read_erp_rows(path)
    pairs = parse_erp_pairs(rows)
    if not pairs:
        # 源表读出来是空的：宁可什么都不做，也不要把库里已有的映射全删掉
        return {"changed": False, "rows": 0, "removed": 0, "reason": "source_empty"}
    raw_count = count_valid_source_rows(rows)
    if raw_count and len(pairs) < raw_count * _PARSE_GUARD_RATIO:
        # 结构性护栏：解析出的映射数远少于源表有效行数 → 表多半被改坏了
        # （典型是列角色颠倒），此时往库里写就是写假映射。
        print(
            f"[erp_map] 源表解析异常（有效行 {raw_count} → 有效映射 {len(pairs)}），"
            "本次不写入（源表可能已损坏或列角色颠倒）"
        )
        return {"changed": False, "rows": 0, "removed": 0, "reason": "parse_guard"}
    owned = conn is None
    if owned:
        from .db import get_conn

        conn = get_conn()
    try:
        conn.executescript(erp_map_schema_sql())
        before = _current_state(conn)
        reason = "ok"
        removed = 0
        if len(pairs) < len(before) * _SHRINK_GUARD_RATIO:
            reason = "shrink_guard"
            print(
                f"[erp_map] 源表条数骤减（库内 {len(before)} → 源表 {len(pairs)}），"
                "跳过删除已有映射（源表可能已损坏）"
            )
        else:
            removed = _remove_unlisted(conn, pairs)
        upsert_erp_codes(conn, pairs)
        after = _current_state(conn)
        conn.commit()
    finally:
        if owned:
            conn.close()
    return {
        "changed": after != before,
        "rows": len(pairs),
        "removed": removed,
        "reason": reason,
    }


def _current_state(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """取当前映射表的全量内容（用于判断"真的变了"）。

    Args:
        conn: SQLite 连接。

    Returns:
        set[tuple[str, str]]: (sku, erp_code) 集合。
    """
    return {
        (str(r[0]), str(r[1]))
        for r in conn.execute("SELECT sku, erp_code FROM erp_map").fetchall()
    }


def _remove_unlisted(conn: sqlite3.Connection, pairs: dict[str, str]) -> int:
    """删掉源表里已不存在的映射（镜像语义），返回删除条数。

    Args:
        conn: SQLite 连接。
        pairs: 当前源表的全量映射。

    Returns:
        int: 删除条数。
    """
    existing = {
        str(r[0]) for r in conn.execute("SELECT sku FROM erp_map").fetchall()
    }
    stale = existing - set(pairs)
    if stale:
        conn.executemany("DELETE FROM erp_map WHERE sku = ?", [(s,) for s in stale])
    return len(stale)
