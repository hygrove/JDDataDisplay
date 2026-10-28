# -*- coding: utf-8 -*-
"""库存快照淘汰：按「每店保留最新一份」永久删除更早的库存快照。

设计要点（与 ADR-0015 一致）：
- **保留集 K**：对每个店铺，取它在源目录中**可解析**的快照文件里最大的**表内「时间」日期**。
  按 per-shop 而非全局日期——某店漏下载时该店保留自己上一份，⛔ 不被删空
  （历史时点商家后台导不出来，删了不可再生）。
- **日期以表内为准**：文件名的 `2026-10-18` 只是约定，xlsx 里的「时间」列才是真相。
- **损坏文件不删**：解析不出日期 / 无「时间」列 / 首行时间为空的文件，既不参与「最新」判定，
  也不删除，其入库行同样保留——无法证明其数据在别处有来源，就不许移走（同 ADR-0001 精神）。
- **同 (shop, date) 多文件不删**：无法判定「哪一份才是最新」时，删除任何一份都可能丢数据。
- **删「保留集的补集」而非「待删文件对应的行」**：源文件被手工删掉但 DB 里还留着行的
  **孤儿行**也必须清掉，否则它们会永远躺在库里（而 DB 里会积起永远删不掉的日期）。
  ⛔ 但只动「有保留集的店」——某店全部文件都不可解析时，不动它的任何行。
- **删除顺序不可换**：哈希记录 → 入库行 → 源文件。哈希记录是 ETL 的增量门控开关，
  先删它等于关闭门控，于是任何中断点下次跑批都会重新摄入该文件、重建数据行，自动收敛。
  反序会出事：先删文件后删行 ⇒ 中断后留下**僵尸行**（DB 有行、源文件没了、哈希记录还在
  ⇒ ETL 不重建 ⇒ 幽灵数据，零报错）；哈希记录最后删 ⇒ **静默丢数**（文件还在但被跳过）。
- **真删**：os.remove，不进回收站、不归档、不留副本。库存快照是商家后台**当前时点**的导出，
  历史某天的快照无法回溯补下载 ⇒ 删掉即该时点永久消失（与 ADR-0006 的归档区清理语义不同，
  见 ADR-0015 的边界说明）。留痕只有清单（文件名 / sha256 / 大小 / 时间），留的是账不是货。

与批处理的配合（**不需要**改 ETL）：
淘汰跑在库存 ETL **之后**。删掉旧源文件后，ETL 的 `glob("*.xlsx")` 自然只看到保留集，
于是每晚自动只解析 3 份文件而不是 25 份——删除动作本身就完成了提速，不需要给 ETL
加「只摄入保留集」的开关（那会让「决策」与「门控」被迫分处 ETL 前后两段，反而更复杂）。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config

# ---- 执行模式（与 organize 同款三态；⛔ 非法值回退安全侧）----
MODE_OFF = "off"
MODE_DRY_RUN = "dry-run"
MODE_ON = "on"
VALID_MODES = (MODE_OFF, MODE_DRY_RUN, MODE_ON)

# ---- 门控常量 ----
# 健康检查阈值：新快照行数 / 旧快照行数 的下限。低于它 ⇒ 该店本轮不删任何东西。
# 为什么用比例不用绝对值：家配 202 行 / 医拜 118 行 / 卡求 76 行，量级差异大，
# 固定绝对阈值要么误杀小店、要么拦不住大店的断崖式下跌。
GATE_ROW_RATIO = 0.6

# 报告落盘目录名（与 organize 同类的位置：`{source_root 的同级}/logs/inventory-retention/`）
REPORT_DIR_NAME = "inventory-retention"


def resolve_mode(explicit: str | None = None) -> str:
    """解析执行模式：显式参数 > 环境变量 `JD_INVENTORY_RETENTION` > 默认 `dry-run`。

    ⚠️ 默认 `dry-run` 与 organize 同理：这是**不可逆**动作（删文件 + 删数据行），
    真删必须显式开启。⛔ 非法值一律回退 `dry-run`（安全侧），绝不因拼错模式而误删。

    Args:
        explicit: 显式模式（通常来自 CLI `--mode`）；None 表示按环境变量解析。

    Returns:
        str: `off` / `dry-run` / `on` 之一。

    Example:
        >>> resolve_mode("on")
        'on'
        >>> resolve_mode("bogus")     # 非法值 → 安全侧默认
        'dry-run'
    """
    raw = explicit if explicit is not None else getattr(config, "INVENTORY_RETENTION_MODE", MODE_DRY_RUN)
    raw = (raw or "").strip().lower()
    return raw if raw in VALID_MODES else MODE_DRY_RUN


def resolve_keep_days(explicit: int | None = None) -> int:
    """解析保留天数：显式参数 > 环境变量 `JD_INVENTORY_KEEP_DAYS` > 默认 1。

    Args:
        explicit: 显式天数；None 表示按环境变量解析。

    Returns:
        int: 每店保留最近 N 天的快照；⛔ 小于 1 一律回退到 1（保留集永远至少有一天）。
    """
    raw = explicit if explicit is not None else getattr(config, "INVENTORY_KEEP_DAYS", 1)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 1
    return n if n >= 1 else 1


def _date_from_filename(name: str) -> list[tuple[str, str]]:
    """从文件名解析出 (shop, date) 列表。

    文件名约定：`{日期}{店名}自营库存明细.xlsx`（见 ai-rules §1.4 与 ETL 的
    `_parse_shop`）。用于给**不可解析的文件**定位它的日期——它没进保留集，
    但它的入库行必须跟着它一起保留（spec §3 细则 2）。

    ⚠️ 只认严格前缀格式 `YYYY-MM-DD`；认不出就返回空列表（宁可不保护，
    也不能把日期猜错——猜错会误删别人的行）。

    Args:
        name: 文件名（含扩展名）。

    Returns:
        list[tuple[str, str]]: [(店铺, 日期), ...]；认不出格式时为空列表。
    """
    import re

    from .inventory import _parse_shop

    m = re.match(r"^(\d{4}-\d{2}-\d{2})", name)
    if not m:
        return []
    return [(_parse_shop(name), m.group(1))]


# ============================== 数据结构 ==============================


@dataclass
class SnapshotFile:
    """一个可解析的库存快照文件（探测结果）。"""

    path: Path
    shop: str
    date: str          # 表内「时间」列的日期（真相），非文件名里的日期
    size: int
    sha256: str

    @property
    def name(self) -> str:
        """文件名（= `inventory_processed_file.rel_path` 的键）。"""
        return self.path.name


@dataclass
class UnparsedFile:
    """一个无法解析的源文件（损坏 / 无「时间」列 / POP 模板等）。

    它**既不参与「最新」判定，也不会被删除**——连同其入库行一并保留。
    """

    path: Path
    reason: str
    size: int


@dataclass
class Decision:
    """淘汰决策：保留集 K 与删除集 D。"""

    keep: dict[str, list[SnapshotFile]] = field(default_factory=dict)   # shop -> 保留的文件
    drop: list[SnapshotFile] = field(default_factory=list)             # 待删的文件
    unparsed: list[UnparsedFile] = field(default_factory=list)         # 不参与判定也不删
    keep_days: int = 1
    # 门控结果：被拦下的店铺（该店本轮不删任何东西）
    gated_shops: dict[str, str] = field(default_factory=dict)
    # 门控命中店铺的**全部**日期（库里有、但本轮一律不删的行）
    # ⚠️ 必须在 apply_gate 里填充：那时才知道该店在库里有哪些日期。
    gated_shop_dates: dict[str, set[str]] = field(default_factory=dict)
    # 判定期发现的「同日多文件」店铺（全部保留）
    duplicate_date_shops: dict[str, list[str]] = field(default_factory=dict)

    def keep_dates_by_shop(self) -> dict[str, set[str]]:
        """每店保留的日期集合（= 保留集 K 的日期维度）。"""
        out: dict[str, set[str]] = {}
        for shop, files in self.keep.items():
            out[shop] = {f.date for f in files}
        return out

    def keep_filenames(self) -> set[str]:
        """保留集里的文件名集合。"""
        return {f.name for files in self.keep.values() for f in files}

    def protected_pairs(self) -> set[tuple[str, str]]:
        """受保护的 (shop, date) 对——它们的**入库行**一律不删。

        三条来源（各自对应一条 spec 细则）：
        1. 门控命中的店 → 该店所有日期（spec §4「宁多留，不可删空」）；
        2. 不可解析文件的日期 → 其入库行保留（spec §3 细则 2）；
        3. 同店同日多文件的日期 → 该日期全部文件保留（spec §3 细则 3）。
        """
        out: set[tuple[str, str]] = set()
        # 门控命中的店：**所有**日期都受保护（含非保留日的旧日期）。
        # ⚠️ 不能只看 `keep`——门控是在 decide 之后应用的，`drop` 里那些旧日期
        #    的文件已被剔除，从 keep/drop 都读不到「该店原本有哪些日期」，
        #    故这里从**保留集的店**推导 + 显式记录门控时该店的全部日期。
        for shop in self.gated_shops:
            out |= {(shop, d) for d in self.gated_shop_dates.get(shop, set())}
        # 不可解析文件：文件名里带日期前缀的（`{日期}{店名}自营库存明细.xlsx`），
        # 其对应日期的入库行保留——文件在，行也必须在。
        for u in self.unparsed:
            for shop, date in _date_from_filename(u.path.name):
                out.add((shop, date))
        # 同店同日多文件：该日期受保护
        for shop, dates in self.duplicate_date_shops.items():
            for d in dates:
                out.add((shop, d))
        return out


# ============================== 探测 ==============================


def _sha256(path: Path) -> str:
    """计算文件 sha256 十六进制串（按块读取，避免大文件一次性占满内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _probe_file(path: Path) -> tuple[str, str] | None:
    """探测单个 xlsx 的 (shop, 表内日期)；不可解析时返回 None。

    ⚠️ 只读表头 + 首个非空数据行，⛔ **不**全量解析 2000+ 列宽表——探测要覆盖目录里
    每个文件，全量解析会把批处理时间拉长一个数量级。

    日期取「时间」列**首个非空数据行**的值（与 ETL `inventory._parse_file` 同源口径：
    那里也是取首行日期、再用它作为整份文件的日期）。

    Args:
        path: xlsx 文件路径。

    Returns:
        tuple[str, str] | None: (店铺, 日期)；无法解析返回 None。
    """
    import openpyxl

    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if not header:
            wb.close()
            return None
        header = list(header)
        if "时间" not in header:
            wb.close()
            return None
        time_idx = header.index("时间")
        date = ""
        for raw in rows:
            if raw is None or all(v is None for v in raw):
                continue
            v = raw[time_idx] if time_idx < len(raw) else None
            if v is None or str(v).strip() == "":
                continue
            if isinstance(v, datetime):
                date = v.strftime("%Y-%m-%d")
            else:
                s = str(v).strip()
                date = s[:10] if len(s) >= 10 and s[4] == "-" and s[7] == "-" else s
            if date:
                break
        wb.close()
    except Exception:
        return None
    if not date:
        return None
    # 店铺名从文件名解析（与 ETL `inventory._parse_shop` 同源）
    from .inventory import _parse_shop

    return _parse_shop(path.name), date


def probe(root: Path) -> tuple[list[SnapshotFile], list[UnparsedFile]]:
    """探测源目录下的全部库存快照文件。

    Args:
        root: 库存数据源目录。

    Returns:
        tuple[list[SnapshotFile], list[UnparsedFile]]: (可解析文件, 不可解析文件)。
    """
    parsed: list[SnapshotFile] = []
    unparsed: list[UnparsedFile] = []
    for p in sorted(Path(root).glob("*.xlsx")):
        if p.name.startswith("~$"):  # Excel 锁文件
            continue
        got = _probe_file(p)
        if got is None:
            unparsed.append(UnparsedFile(
                path=p, reason="无法解析日期（无「时间」列或首行时间为空）", size=p.stat().st_size))
            continue
        shop, date = got
        parsed.append(SnapshotFile(path=p, shop=shop, date=date,
                                   size=p.stat().st_size, sha256=_sha256(p)))
    return parsed, unparsed


# ============================== 决策 ==============================


def decide(parsed: list[SnapshotFile], unparsed: list[UnparsedFile], keep_days: int) -> Decision:
    """算出保留集 K 与删除集 D。

    规则：
    1. 每店按日期排序，保留最近 `keep_days` 个**不同日期**；
    2. 同一 (shop, date) 有多个文件 ⇒ 该日期的**全部**文件都保留（无法判定哪份最新），
       ⚠️ **不论该日期是否在保留集内**——多份同名日期的文件本就分不清哪份是最新的，
       删任何一份都可能丢数据，故连旧日期上的多份也一并保留（spec §3 细则 3）；
    3. 不可解析文件既不参与判定也不删。

    Args:
        parsed: 探测出的可解析文件。
        unparsed: 探测出的不可解析文件。
        keep_days: 每店保留最近 N 天的快照（≥1）。

    Returns:
        Decision: 决策结果。
    """
    by_shop: dict[str, list[SnapshotFile]] = {}
    for f in parsed:
        by_shop.setdefault(f.shop, []).append(f)

    keep: dict[str, list[SnapshotFile]] = {}
    drop: list[SnapshotFile] = []
    dup_shops: dict[str, list[str]] = {}

    for shop, files in by_shop.items():
        # 同 (shop, date) 多文件 → 标记该日期全部保留
        date_count: dict[str, int] = {}
        for f in files:
            date_count[f.date] = date_count.get(f.date, 0) + 1
        dup_dates = sorted(d for d, c in date_count.items() if c > 1)
        if dup_dates:
            dup_shops[shop] = dup_dates

        # 日期倒序（新的在前）→ 取前 keep_days 个不同日期为保留日期
        dates = sorted({f.date for f in files}, reverse=True)
        keep_dates = set(dates[:keep_days])

        for f in files:
            # 同店同日多文件 ⇒ 该日期全部保留（**不论是否在保留集内**）
            if f.date in keep_dates or f.date in dup_dates:
                keep.setdefault(shop, []).append(f)
            else:
                drop.append(f)

    return Decision(keep=keep, drop=drop, unparsed=unparsed,
                    keep_days=keep_days, duplicate_date_shops=dup_shops)


# ============================== 门控 ==============================


def _shop_row_counts(conn) -> dict[str, dict[str, int]]:
    """按 (shop, date) 统计 `inventory_daily` 的行数。

    ⚠️ 不依赖连接的 `row_factory`：用下标取值，这样直连 `sqlite3.connect`（默认
    tuple 行）与 `db.get_conn()`（Row 行）都能用——否则换个调用方就炸。

    Returns:
        dict[str, dict[str, int]]: {shop: {date: 行数}}。
    """
    out: dict[str, dict[str, int]] = {}
    for shop, date, c in conn.execute(
            "SELECT shop, date, COUNT(*) AS c FROM inventory_daily GROUP BY shop, date"):
        out.setdefault(shop, {})[date] = int(c)
    return out


def apply_gate(conn, decision: Decision) -> Decision:
    """健康检查：新快照行数不足 ⇒ 该店本轮**不删任何东西**。

    判据：新快照（保留集里最新那个日期）的行数 ≥ 该店上一份快照行数 × `GATE_ROW_RATIO`。

    为什么必须有：ETL 的 `_parse_file` 对损坏文件**静默返回 None**，不报错。
    若新快照损坏而我们照删旧快照，库存页会直接空。⇒ **宁多留，不可删空**。

    Args:
        conn: SQLite 连接。
        decision: 待门控的决策（原地修改 `gated_shops` 与 `drop`）。

    Returns:
        Decision: 门控后的决策（同一个对象）。
    """
    counts = _shop_row_counts(conn)
    for shop, files in decision.keep.items():
        shop_counts = counts.get(shop, {})
        new_date = max((f.date for f in files), default=None)   # 保留集里最新的日期 = 新快照
        if new_date is None:
            continue
        new_rows = shop_counts.get(new_date, 0)
        older = [d for d in shop_counts if d < new_date]          # 库里的更早快照
        old_date = max(older) if older else None
        old_rows = shop_counts.get(old_date, 0) if old_date else 0
        # 判据 1：新快照在库里至少 1 行（**无条件**检查——即使没有更早快照可比，
        # 「新快照 0 行」本身就是「不该删旧」的充分理由）
        if new_rows <= 0:
            decision.gated_shops[shop] = (
                f"新快照 {new_date} 在库内 0 行（ETL 静默跳过或摄入失败）→ 整体不删")
        # 判据 2：新快照行数 ≥ 旧快照行数 × 阈值（无更早快照时无从比较，跳过）
        elif old_date is not None and old_rows > 0 and new_rows < old_rows * GATE_ROW_RATIO:
            decision.gated_shops[shop] = (
                f"新快照 {new_date} 仅 {new_rows} 行 < 旧快照 {old_date} 的 {old_rows} 行 "
                f"的 {int(GATE_ROW_RATIO * 100)}% → 整体不删")
    if decision.gated_shops:
        gated = set(decision.gated_shops)
        # 门控 = 该店**整体不删** ⇒ 它的所有文件都回到保留集（源文件留着 + 入库行留着）。
        # ⚠️ 必须在这一步把文件搬回 keep：否则它们既不在 keep（被当"多余"）也不在
        #    spared（不在判定名单里），核对逻辑会误报，而哈希记录也会被误删。
        gated_files = [f for f in decision.drop if f.shop in gated]
        decision.drop = [f for f in decision.drop if f.shop not in gated]
        for f in gated_files:
            decision.keep.setdefault(f.shop, []).append(f)
        # 记录门控店在库里的**全部**日期：这些日期的行本轮一律不删
        # （⚠️ 不能只看 keep —— 旧日期的文件已从 drop 剔除，从 keep/drop 都读不全）
        for shop in gated:
            decision.gated_shop_dates[shop] = set(counts.get(shop, {}))
    return decision


# ============================== 执行 ==============================


def _db_dates_by_shop(conn, table: str) -> dict[str, set[str]]:
    """读某表里已有的 (shop, date) 集合。

    ⚠️ 不依赖连接的 `row_factory`（用下标取值），直连 sqlite3 与 `db.get_conn()` 都能用。

    Args:
        conn: SQLite 连接。
        table: 表名（内部常量，非外部输入）。

    Returns:
        dict[str, set[str]]: {shop: {date, ...}}。
    """
    out: dict[str, set[str]] = {}
    for shop, date in conn.execute(f"SELECT DISTINCT shop, date FROM {table}"):
        out.setdefault(shop, set()).add(date)
    return out


def _stale_pairs(conn, decision: Decision) -> list[tuple[str, str]]:
    """算出「该删的行」的 (shop, date) 列表 = 保留集补集 − 受保护集合。

    ⛔ 减去**受保护集合**——这三条各自对应一个 spec 细则，漏掉任何一条都会
    「文件留着、行没了」或「门控形同虚设」：

    1. **门控命中的店**（`gated_shops`）：健康检查不通过 ⇒ 该店**整体不删**
       （spec §4「宁多留，不可删空」）。⚠️ 门控只把店从 `drop`（源文件）里剔掉时，
       若这里不跟着剔，该店的入库行照删 ⇒ 源文件保住了、数据被删空，比不门控更糟。
    2. **不可解析文件的日期**（`unparsed`）：无法解析日期的文件**其入库行也保留**
       （spec §3 细则 2）——文件在、行没了，等于把来源拆散删。
    3. **同店同日多文件的日期**（`duplicate_date_shops`）：该日期的全部文件都保留
       （spec §3 细则 3）⇒ 它们的入库行也保留。

    ⛔ 另：只处理**有保留集的店**——某店全部文件都不可解析时，它的行一概不动
    （无法证明哪份才是最新）。

    Args:
        conn: SQLite 连接。
        decision: 决策结果。

    Returns:
        list[tuple[str, str]]: 待删的 (shop, date) 对。
    """
    keep_dates = decision.keep_dates_by_shop()
    protected = decision.protected_pairs()
    pairs: list[tuple[str, str]] = []
    # 只查一张表：inventory_daily 与 inventory_city_slow 的 (shop,date) 集合一致
    # （两者由同一份源文件同批写入），取一张算即可。
    for shop, dates in _db_dates_by_shop(conn, "inventory_daily").items():
        keep = keep_dates.get(shop)
        if not keep:
            continue          # 该店无保留集 → 不动
        for d in sorted(dates - keep):
            if (shop, d) in protected:
                continue      # 受保护 → 不动
            pairs.append((shop, d))
    return pairs


def _delete_db_rows(conn, pairs: list[tuple[str, str]]) -> tuple[int, int]:
    """删除给定 (shop, date) 的入库行。

    Args:
        conn: SQLite 连接。
        pairs: 待删的 (shop, date) 对。

    Returns:
        tuple[int, int]: (inventory_daily 删除行数, inventory_city_slow 删除行数)。
    """
    n_daily = 0
    n_slow = 0
    for shop, date in pairs:
        cur = conn.execute("DELETE FROM inventory_daily WHERE shop=? AND date=?", (shop, date))
        n_daily += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        cur = conn.execute("DELETE FROM inventory_city_slow WHERE shop=? AND date=?", (shop, date))
        n_slow += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    return n_daily, n_slow


def _build_manifest(drop: list[SnapshotFile], dry_run: bool) -> list[dict[str, object]]:
    """构建删除清单（前置指纹落盘，`ai-rules §6` 步骤 1）。

    每条含：文件名 / 店铺 / 日期 / **sha256** / 大小（字节）/ 删除时间 /
    是否已删除 / 失败原因。

    ⚠️ sha256 是**删除前**算的（`probe()` 阶段已算好）——事后无法重算（文件已没了），
    所以它就是「删的确实是这份、这份内容是什么」的唯一凭据。**留账不留货**：
    清单里只有哈希与元数据，⛔ 不含任何快照内容。

    Args:
        drop: 待删文件列表。
        dry_run: True 时不标删除时间（尚未删）。

    Returns:
        list[dict[str, object]]: 删除清单条目。
    """
    now = datetime.now(timezone.utc).isoformat()
    out: list[dict[str, object]] = []
    for f in drop:
        out.append({
            "name": f.name,
            "shop": f.shop,
            "date": f.date,
            "sha256": f.sha256,
            "size_bytes": f.size,
            "planned_at": now,
            "deleted": False,
            "deleted_at": "" if dry_run else now,
            "error": "",
        })
    return out


def execute(decision: Decision, dry_run: bool) -> dict[str, object]:
    """执行删除（**顺序不可换**，见模块 docstring 的自愈论证）。

    顺序：哈希记录 → 入库行 → 源文件。
    哈希记录最先删 = 提前关闭 ETL 的增量门控，于是任何中断点下次跑批都能自愈。

    哈希记录按「**不在保留集里**」删（而不是只删待删文件的记录）：这样连
    「源文件早被手工删掉、哈希记录还留着」的孤儿记录也一并清掉，
    不然它们会让一个**后来重新出现的同名文件**被误判为「已处理过且内容未变」而跳过摄入。

    ⚠️ **受保护的文件（损坏文件 / 同店同日多份）也在保留集里** ⇒ 它们的哈希记录
    不会被删 ⇒ 下轮 ETL 仍认得「已处理过、内容未变」而跳过摄入 ⇒ 其入库行
    不会被重建覆盖。⛔ 这与「删哈希记录 = 关门控」的自愈机制互为表里，**两者必须一致**。

    前置指纹（`ai-rules §6` 步骤 1）：每份待删文件的 sha256 + 大小在**删除前**
    落进 `stats["manifest"]`，事后可逐份核对「删的确实是这份、这份内容是什么」。

    Args:
        decision: 已过门控的决策。
        dry_run: True 时只报告不删。

    Returns:
        dict[str, object]: 执行统计 {deleted_files, deleted_rows_daily,
            deleted_rows_slow, deleted_hashes, manifest, failed}。
    """
    from .db import get_conn

    drop = decision.drop
    stats: dict[str, object] = {
        "deleted_files": 0,
        "deleted_rows_daily": 0,
        "deleted_rows_slow": 0,
        "deleted_hashes": 0,
        "manifest": [],
        "failed": [],
    }
    if not drop:
        return stats

    conn = get_conn()
    try:
        pairs = _stale_pairs(conn, decision)
        # 保留集 ∪ 故意不删的文件（损坏文件 / POP 模板）——它们的哈希记录也留着：
        # 文件还在源目录，哈希记录就该在，否则下轮 ETL 会当它「未处理过」而重摄入。
        keep_names = decision.keep_filenames() | {u.path.name for u in decision.unparsed}
        hash_rows = [n for (n,) in conn.execute("SELECT rel_path FROM inventory_processed_file")]
        stale_hashes = [n for n in hash_rows if n not in keep_names]

        if dry_run:
            n_daily = conn.execute(
                "SELECT COUNT(*) AS c FROM inventory_daily WHERE "
                + " OR ".join(["(shop=? AND date=?)"] * len(pairs)),
                [v for p in pairs for v in p],
            ).fetchone()["c"] if pairs else 0
            n_slow = conn.execute(
                "SELECT COUNT(*) AS c FROM inventory_city_slow WHERE "
                + " OR ".join(["(shop=? AND date=?)"] * len(pairs)),
                [v for p in pairs for v in p],
            ).fetchone()["c"] if pairs else 0
            stats.update(deleted_rows_daily=n_daily, deleted_rows_slow=n_slow,
                         deleted_hashes=len(stale_hashes),
                         manifest=_build_manifest(drop, dry_run=True))
            return stats

        # ④-1 哈希记录先删（门控开关）
        if stale_hashes:
            cur = conn.executemany(
                "DELETE FROM inventory_processed_file WHERE rel_path=?",
                [(n,) for n in stale_hashes],
            )
            stats["deleted_hashes"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            conn.commit()

        # ④-2 再删入库行
        n_daily, n_slow = _delete_db_rows(conn, pairs)
        stats["deleted_rows_daily"] = n_daily
        stats["deleted_rows_slow"] = n_slow
        conn.commit()
    finally:
        conn.close()

    # ④-3 最后删源文件（真删，不进回收站）——逐个失败不影响其余
    #     删除清单逐条记录 sha256 / 大小 / 删除时间（审计用：留账不留货）
    manifest = _build_manifest(drop, dry_run=False)
    for entry, f in zip(manifest, drop):
        try:
            os.remove(f.path)
            stats["deleted_files"] = int(stats["deleted_files"]) + 1
            entry["deleted"] = True
            entry["deleted_at"] = datetime.now(timezone.utc).isoformat()
        except OSError as e:
            entry["deleted"] = False
            entry["error"] = f"{type(e).__name__}: {e}"
            stats["failed"].append(f"{f.name}: {type(e).__name__}: {e}")
    stats["manifest"] = manifest

    # ④-4 回收磁盘：SQLite 删行后空页不返还操作系统，文件大小不变
    # （实测 2.81MB → 删行后仍 2.81MB，freelist_count=531）。VACUUM 重写整个库
    # 把空页压掉 → 实测 0.70MB，耗时 15ms。
    # ⛔ 失败只记录、不抛出：VACUUM 是纯优化，不做它只是不还磁盘，⛔ 不该让整批失败。
    # ⛔ 需要临时空间（约为库大小）并短暂持锁，故放在批处理尾（非并发窗口）。
    stats["vacuum"] = _vacuum()

    return stats


def _vacuum() -> dict[str, object]:
    """跑一次 VACUUM 回收磁盘空间。

    ⚠️ VACUUM 需要**独占**锁：若服务进程正持有读连接，可能报 `database is locked`。
    那时降级为「只记录不执行」——不还磁盘不影响正确性，⛔ 绝不能因此中断批处理。

    ⚠️ VACUUM **不保证只减**：它按最坏情况重建页布局，小库可能反而变大
    （实测 45KB → 53KB）。生产库收益显著（2.81MB → 0.70MB），
    但「一定会变小」是错的假设，故本函数只如实记录前后体积，不做承诺。

    Returns:
        dict[str, object]: {ok, before_mb, after_mb, error}。
    """
    from .db import DB_PATH, get_conn

    out: dict[str, object] = {"ok": False, "before_mb": 0.0, "after_mb": 0.0, "error": ""}
    try:
        out["before_mb"] = round(DB_PATH.stat().st_size / 1024 / 1024, 2) if DB_PATH.exists() else 0.0
    except OSError:
        pass
    conn = get_conn()
    try:
        # ⛔ 不能在自己的事务里 VACUUM（SQLite 会报错），先提交再执行
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
        out["ok"] = True
    except Exception as e:  # noqa: BLE001 —— VACUUM 失败只降级，不外抛
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        conn.close()
    try:
        out["after_mb"] = round(DB_PATH.stat().st_size / 1024 / 1024, 2) if DB_PATH.exists() else 0.0
    except OSError:
        pass
    return out


# ============================== 核对 ==============================


def verify(decision: Decision, root: Path,
          manifest: list[dict[str, object]] | None = None) -> dict[str, object]:
    """后置核对（`ai-rules §6` 步骤 4）：逐份核对，不只比对集合名。

    核对四件事：
    1. DB 的 (shop, date) 集合 == 保留集；
    2. 源目录文件集合 == 保留集文件 ∪ 故意保留的文件；
    3. 哈希记录集合 == 保留集文件集合；
    4. **逐份核对删除清单**（若给了 manifest）：每份声称已删的文件**确实不在磁盘上**，
       且清单里记的 sha256 非空（它就是「删的确实是这份」的唯一凭据）。

    Args:
        decision: 已执行的决策。
        root: 库存数据源目录。
        manifest: 删除清单（`execute` 的 stats["manifest"]）；None 表示跳过第 4 项。

    Returns:
        dict[str, object]: {ok, db_dates, db_pairs, disk_files, hash_files,
            expected_dates, expected_pairs, problems}。
    """
    from .db import get_conn

    conn = get_conn()
    try:
        db_dates = sorted({d for (d,) in conn.execute("SELECT DISTINCT date FROM inventory_daily")})
        db_pairs = sorted({
            (shop, d) for shop, d in conn.execute("SELECT DISTINCT shop, date FROM inventory_daily")
        })
        hash_files = sorted({
            n for (n,) in conn.execute("SELECT rel_path FROM inventory_processed_file")
        })
    finally:
        conn.close()

    disk_files = sorted(p.name for p in Path(root).glob("*.xlsx") if not p.name.startswith("~$"))
    expected_dates = sorted({f.date for fs in decision.keep.values() for f in fs})
    expected_pairs = sorted({(f.shop, f.date) for fs in decision.keep.values() for f in fs})
    keep_names = decision.keep_filenames()
    # 故意不删的文件（不可解析 / 同店同日多份）不参与「多余文件」判定：
    # 它们按设计就该留在源目录（POP 模板就是这一类），算作多余是误报。
    spared_names = {u.path.name for u in decision.unparsed}

    problems: list[str] = []
    # 期望的 DB (shop,date) 集合 = 保留集 ∪ **受保护集合中库里确实存在的**
    # （门控命中的店 / 损坏文件 / 同店同日多份 —— 它们的行按 spec 必须留着）
    # ⚠️ 受保护集合要**与库里的实际 (shop,date) 取交集**：POP 模板这类文件
    # 从未被 ETL 摄入过（无「时间」列），库里压根没有它的行 ⇒ 不该进期望集合，
    # 否则核对会误报「期望有却没有」。
    db_pairs_set = set(db_pairs)
    protected = decision.protected_pairs() & db_pairs_set
    expected_pairs_all = sorted(set(expected_pairs) | protected)
    expected_dates_all = sorted({d for (_, d) in expected_pairs_all})
    if db_pairs != expected_pairs_all:
        problems.append(f"DB 的 (shop,date) 集合 {db_pairs} ≠ 期望 {expected_pairs_all}")
    if db_dates != expected_dates_all:
        problems.append(f"DB 的日期集合 {db_dates} ≠ 期望日期 {expected_dates_all}")
    # 哈希记录集合 = 保留集文件名 ∪ 故意不删的**已被摄入过**的文件名
    # ⚠️ POP 模板这类文件从未被 ETL 摄入（无「时间」列）⇒ 库里从来没有它的哈希记录，
    #   不该进期望集合（否则核对误报「期望有却没有」）。
    ingested_spared = {n for n in spared_names if n in set(hash_files)}
    expected_hash_names = keep_names | ingested_spared
    if set(hash_files) != expected_hash_names:
        problems.append(
            f"哈希记录集合 {sorted(set(hash_files) - expected_hash_names)} 多出 / "
            f"{sorted(expected_hash_names - set(hash_files))} 缺失")
    # 「多余文件」判定：不在保留集、不在故意保留名单里，且**不属于门控店**的才算多余
    # （门控店的旧日期文件被 apply_gate 从 drop 剔除 ⇒ 不在 keep 也不在 spared，
    #   但它们按设计就该留着）
    file_shop = {f.name: shop for shop, fs in decision.keep.items() for f in fs}
    for u in decision.unparsed:
        for shop, _d in _date_from_filename(u.path.name):
            file_shop[u.path.name] = shop
    extra = [
        n for n in disk_files
        if n not in keep_names and n not in spared_names
        and file_shop.get(n) not in decision.gated_shops
    ]
    if extra:
        problems.append(f"源目录仍有多余文件 {extra}")
    # 故意保留的文件必须**还在**源目录（不可解析 / 同店同日多份）——
    # 少了一个说明删除动作越界，动了本该留下的文件。
    missing_spared = [n for n in spared_names if n not in disk_files]
    if missing_spared:
        problems.append(f"应保留的文件消失了 {missing_spared}")

    # 逐份核对删除清单（§6 步骤 4）：声称已删的必须确实不在磁盘上，
    # 且清单里必须有 sha256（删后无法重算，它是「删的确实是这份」的唯一凭据）。
    if manifest is not None:
        still_there = [m["name"] for m in manifest
                       if m.get("deleted") and m["name"] in disk_files]
        if still_there:
            problems.append(f"清单声称已删但文件仍在 {still_there}")
        no_hash = [m["name"] for m in manifest if not m.get("sha256")]
        if no_hash:
            problems.append(f"清单条目缺 sha256 前置指纹 {no_hash}")

    return {
        "ok": not problems,
        "db_dates": db_dates,
        "db_pairs": [list(p) for p in db_pairs],
        "disk_files": disk_files,
        "hash_files": hash_files,
        "expected_dates": expected_dates,
        "expected_pairs": [list(p) for p in expected_pairs],
        "problems": problems,
    }


# ============================== 报告 ==============================


def _default_report_dir(root: Path) -> Path:
    """默认报告目录：`{source_root 的同级}/logs/inventory-retention/`。

    与 organize 的 `_default_report_dir` 同构：生产里 source_root=`ResourceData/库存数据表`，
    其同级是 `ResourceData`，故报告落在 `ResourceData/logs/inventory-retention/`。
    """
    return Path(root).resolve().parent / "logs" / REPORT_DIR_NAME


def render_report(rep: dict[str, object]) -> str:
    """渲染人读报告（Markdown）。

    Args:
        rep: 报告数据（`run` 的返回值）。

    Returns:
        str: Markdown 报告文本。
    """
    mode = rep.get("mode", MODE_DRY_RUN)
    dec: Decision = rep["decision"]  # type: ignore[assignment]
    stats = rep.get("stats", {})
    ver = rep.get("verify", {})

    lines: list[str] = []
    lines.append("# 库存快照淘汰报告")
    lines.append("")
    lines.append(f"- 模式：**{mode}**" + ("（只出清单，不动任何东西）" if mode == MODE_DRY_RUN else ""))
    lines.append(f"- 源目录：`{rep.get('source')}`")
    lines.append(f"- 保留天数：每店 {dec.keep_days} 天")
    lines.append(f"- 生成时间：{rep.get('generated_at')}")
    lines.append("")

    lines.append("## 1. 保留集（每店最新一份）")
    lines.append("")
    if dec.keep:
        lines.append("| 店铺 | 保留日期 | 文件 |")
        lines.append("|---|---|---|")
        for shop, files in sorted(dec.keep.items()):
            for f in sorted(files, key=lambda x: x.date, reverse=True):
                lines.append(f"| {shop} | {f.date} | {f.name} |")
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## 2. 将删除（源文件 + 入库行）")
    lines.append("")
    if dec.drop:
        lines.append("| 店铺 | 日期 | 文件 | 大小 |")
        lines.append("|---|---|---|---|")
        for f in sorted(dec.drop, key=lambda x: (x.shop, x.date)):
            lines.append(f"| {f.shop} | {f.date} | {f.name} | {f.size / 1024:.0f}KB |")
        lines.append("")
        lines.append(f"合计 **{len(dec.drop)}** 份文件；入库行 `inventory_daily` "
                     f"**{stats.get('deleted_rows_daily', 0)}** 行、"
                     f"`inventory_city_slow` **{stats.get('deleted_rows_slow', 0)}** 行、"
                     f"哈希记录 **{stats.get('deleted_hashes', 0)}** 条。")
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## 3. 健康检查门控")
    lines.append("")
    if dec.gated_shops:
        for shop, reason in sorted(dec.gated_shops.items()):
            lines.append(f"- ⚠️ **{shop}**：{reason}")
    else:
        lines.append("- 全部通过（新快照行数 ≥ 旧快照的 60%）")
    lines.append("")

    lines.append("## 4. 不参与判定、也不删除")
    lines.append("")
    if dec.unparsed:
        for u in dec.unparsed:
            lines.append(f"- `{u.path.name}` — {u.reason}")
    else:
        lines.append("（无）")
    lines.append("")

    if dec.duplicate_date_shops:
        lines.append("## 5. 同店同日多文件（全部保留，待人工判断）")
        lines.append("")
        for shop, dates in sorted(dec.duplicate_date_shops.items()):
            lines.append(f"- {shop}：{', '.join(dates)}")
        lines.append("")

    lines.append("## 5. 删除清单（前置指纹 · 留账不留货）")
    lines.append("")
    manifest = stats.get("manifest") or []
    if manifest:
        lines.append("| 文件 | 店铺 | 日期 | 大小 | sha256（前 16 位） | 删除时间 |")
        lines.append("|---|---|---|---|---|---|")
        for m in manifest:
            sha = str(m.get("sha256") or "")
            lines.append(
                f"| `{m.get('name')}` | {m.get('shop')} | {m.get('date')} | "
                f"{int(m.get('size_bytes') or 0) / 1024:.0f}KB | "
                f"`{sha[:16]}` | {m.get('deleted_at') or '—'} |")
        lines.append("")
        lines.append(f"共 **{len(manifest)}** 份。完整清单（含完整 sha256）见同名 `.json` 报告。")
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## 6. 执行结果与后置核对")
    lines.append("")
    lines.append(f"- 删除文件：**{stats.get('deleted_files', 0)}** 份")
    if stats.get("failed"):
        lines.append(f"- ⚠️ 删除失败：{stats.get('failed')}")
    vac = stats.get("vacuum") or {}
    if vac:
        if vac.get("ok"):
            lines.append(f"- VACUUM 回收磁盘：{vac.get('before_mb')}MB → **{vac.get('after_mb')}MB**")
        else:
            lines.append(f"- ⚠️ VACUUM 未执行（不影响正确性，只是不还磁盘）：{vac.get('error')}")
    if ver:
        lines.append(f"- 三集合核对：{'✅ 一致' if ver.get('ok') else '❌ 不一致'}")
        for p in ver.get("problems", []):
            lines.append(f"  - {p}")
    return "\n".join(lines) + "\n"


def write_report(rep: dict[str, object], report_dir: Path | None = None) -> tuple[Path, Path]:
    """把报告落盘（人读 md + 机读 json）。

    Args:
        rep: 报告数据。
        report_dir: 落盘目录；None 表示用默认 `{source_root 的同级}/logs/inventory-retention/`。

    Returns:
        tuple[Path, Path]: (md 路径, json 路径)。
    """
    src = Path(str(rep["source"]))
    out_dir = Path(report_dir) if report_dir is not None else _default_report_dir(src)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    md_path = out_dir / f"report-{stamp}.md"
    json_path = out_dir / f"report-{stamp}.json"
    md_path.write_text(render_report(rep), encoding="utf-8")
    json_path.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return md_path, json_path


# ============================== 主流程 ==============================


def plan_keep_set(root: Path | None = None, keep_days: int | None = None) -> set[str]:
    """只算保留集（**不删任何东西**）——供 ETL 决定「只摄入哪些文件」。

    用法（ADR-0015）：批处理里先问「该保留哪些」，ETL 只摄入这些文件，
    省掉对"马上就要被删的旧文件"的全量解析（家配单份 1.66MB / 2000+ 列宽表）。
    然后再跑 `run()` 执行淘汰。

    ⚠️ 探测失败的文件（损坏 / 无「时间」列）**不在保留集里** ⇒ ETL 不会摄入它们
    （它们本来也摄入不了——ETL 的 `_parse_file` 对无「时间」列同样返回 None）。

    Args:
        root: 库存数据源目录；None = `config.INVENTORY_SOURCE_DIR`。
        keep_days: 每店保留天数；None = 按环境变量（默认 1）。

    Returns:
        set[str]: 保留集里的**文件名**集合（空集表示无文件可摄入）。
    """
    root = Path(root or config.INVENTORY_SOURCE_DIR)
    parsed, unparsed = probe(root)
    decision = decide(parsed, unparsed, resolve_keep_days(keep_days))
    return decision.keep_filenames()


def run(root: Path | None = None, mode: str | None = None,
        keep_days: int | None = None, apply_now: bool = False,
        report_dir: Path | None = None) -> dict[str, object]:
    """跑一次库存快照淘汰（探测 → 决策 → 门控 → 执行 → 核对）。

    Args:
        root: 库存数据源目录；None = `config.INVENTORY_SOURCE_DIR`。
        mode: 执行模式；None = 按环境变量解析（默认 `dry-run`）。
        keep_days: 每店保留天数；None = 按环境变量（默认 1）。
        apply_now: True 时等价于 `mode=on`（真删）；用于 CLI 的 `--apply`。
        report_dir: 报告落盘目录；None = 默认位置。

    Returns:
        dict[str, object]: 报告数据（含 decision / stats / verify）。
    """
    from .db import ensure_inventory_schema, get_conn

    root = Path(root or config.INVENTORY_SOURCE_DIR)
    resolved = resolve_mode("on" if apply_now else mode)
    days = resolve_keep_days(keep_days)

    rep: dict[str, object] = {
        "mode": resolved,
        "source": str(root),
        "keep_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if resolved == MODE_OFF:
        rep["decision"] = Decision(keep_days=days)
        rep["stats"] = {}
        rep["verify"] = {}
        return rep

    ensure_inventory_schema()
    parsed, unparsed = probe(root)
    decision = decide(parsed, unparsed, days)

    if decision.drop:
        conn = get_conn()
        try:
            apply_gate(conn, decision)
        finally:
            conn.close()

    dry_run = resolved == MODE_DRY_RUN
    stats = execute(decision, dry_run=dry_run)
    rep["decision"] = decision
    rep["stats"] = stats
    if not dry_run:
        rep["verify"] = verify(decision, root, manifest=stats.get("manifest") or [])
    write_report(rep, report_dir)
    return rep


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：`python -m backend.jobs.inventory_retention [--apply] [--source DIR] [--keep-days N]`。

    ⛔ 默认 `dry-run`（只出报告，不删任何东西）；`--apply` 才真删。

    Returns:
        int: 退出码（0 = 成功，1 = 后置核对不一致）。
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="库存快照淘汰（每店保留最新一份，旧的永久删除）")
    parser.add_argument("--source", default=None,
                        help="库存数据源目录；默认 config.INVENTORY_SOURCE_DIR")
    parser.add_argument("--keep-days", type=int, default=None,
                        help="每店保留最近 N 天；默认 1")
    parser.add_argument("--mode", default=None, choices=list(VALID_MODES),
                        help="执行模式；默认按环境变量 JD_INVENTORY_RETENTION")
    parser.add_argument("--apply", action="store_true", help="真删（⛔ 跳过 dry-run 保护）")
    parser.add_argument("--out", default=None, help="报告落盘目录；默认 logs/inventory-retention/")
    args = parser.parse_args(argv)

    root = Path(args.source) if args.source else config.INVENTORY_SOURCE_DIR
    rep = run(root=root, mode=args.mode, keep_days=args.keep_days, apply_now=args.apply,
              report_dir=Path(args.out) if args.out else None)
    print(render_report(rep))
    ver = rep.get("verify") or {}
    return 0 if ver.get("ok", True) else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
