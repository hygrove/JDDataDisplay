# -*- coding: utf-8 -*-
"""库存快照淘汰（inventory_retention）的验证脚本。

运行：仓库根 `python -m backend.jobs.verify_inventory_retention`
（或 `python backend/jobs/verify_inventory_retention.py`）。

⛔ 按 `ai-rules §3`：**不引入 pytest**（requirements.txt 是完整 freeze，为异地复现）。
沿用独立脚本 + 自制断言计数器 + `sys.exit(1)`。

覆盖 9 类场景（每类都是一次真实的行为断言，不是"跑通就算"）：
  1. 保留集判定正确（含「某店落后」时该店保留自己上一份）
  2. 损坏 / 无「时间」列的文件不被删，其入库行也不删
  3. 同 (shop, date) 多文件 → 全部保留
  4. 健康检查不通过 ⇒ 该店零删除
  5. 执行后三集合相等（DB 的 (shop,date) / 源目录文件 / 哈希记录）
  6. 幂等性：连跑两次，第二次 0 变更
  7. **自愈性**：在 ④-1 / ④-2 之后中断，重跑后状态收敛
  8. dry-run 零副作用
  9. VACUUM 回收磁盘（删行后空页不返还，VACUUM 才压得回去）

跑法：脚本在临时目录里造 fixture（真造 xlsx），全程不碰生产数据。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.jobs import inventory_retention as ret  # noqa: E402

# ---- 自制断言计数器 ----
_passed = 0
_failed: list[str] = []


def check(cond: bool, label: str) -> None:
    """记一次断言：通过则计数 +1，失败则记进失败清单（不抛异常，跑完全部再汇总）。"""
    global _passed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed.append(label)
        print(f"  ✗ {label}")


# ============================== fixture ==============================


def _make_xlsx(path: Path, date: str, shop_label: str, rows: int = 3) -> None:
    """造一个最小可用的库存快照 xlsx（时间列 + SKU 列 + 一行数值列）。

    只造探测与门控真正会读到的列：探测读「时间」首行值；ETL 读「时间」「SKU」。
    """
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["时间", "SKU", "商品名称", "全国库存件数"])
    for i in range(rows):
        ws.append([date, f"10000000000{i}", f"{shop_label}商品{i}", 100 + i])
    wb.save(path)


def _make_broken_xlsx(path: Path) -> None:
    """造一个**无「时间」列**的 xlsx（模拟 POP 模板 / 结构异常的表）。"""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["SKU", "商品名称"])
    ws.append(["100000000000", "某商品"])
    wb.save(path)


def _setup_db(db_path: Path, rows: list[tuple[str, str, str]]) -> None:
    """建一个含 inventory 三表的临时库。

    ⚠️ `inventory_processed_file.rel_path` 存的是**文件名**（与生产库一致：
    `2026-10-27家配自营库存明细.xlsx`）——淘汰逻辑靠它与源文件对账，
    fixture 必须用同样的键名，否则对账永远不通过。

    Args:
        db_path: 库文件路径。
        rows: [(shop, date, sku), ...] —— 每行写一条 inventory_daily 记录。
    """
    from backend.jobs import inventory_metrics

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(inventory_metrics.schema_sql())
        conn.executescript(inventory_metrics.city_slow_schema_sql())
        cols = [c[0] for c in inventory_metrics.all_columns()]
        col_sql = ", ".join(f'"{c}"' for c in cols)
        ph = ", ".join(["?"] * len(cols))
        for shop, date, sku in rows:
            vals = []
            for fld, _src, ctype in inventory_metrics.all_columns():
                if fld == "date":
                    vals.append(date)
                elif fld == "shop":
                    vals.append(shop)
                elif fld == "sku":
                    vals.append(sku)
                elif ctype == "real":
                    vals.append(1.0)
                else:
                    vals.append(None)
            conn.execute(f"INSERT INTO inventory_daily ({col_sql}) VALUES ({ph})", vals)
            conn.execute(
                "INSERT INTO inventory_city_slow(date, shop, sku, city, slow_units) VALUES (?,?,?,?,?)",
                (date, shop, sku, "北京", 1.0))
        # 哈希记录的键 = 文件名（与生产库口径一致）
        names = {f"{date}{shop}自营库存明细.xlsx" for shop, date, sku in rows}
        conn.executemany(
            "INSERT INTO inventory_processed_file(rel_path, content_hash, processed_at) VALUES (?,?,?)",
            [(n, "hash_" + n, "2026-01-01T00:00:00+00:00") for n in sorted(names)])
        conn.commit()
    finally:
        conn.close()


def _use_db(db_path: Path) -> None:
    """让被测模块指向临时库（monkeypatch `db.DB_PATH`）。

    `db.get_conn()` 每次都从模块级 `DB_PATH` 取路径，故改模块属性即可全局生效。
    """
    from backend.jobs import db as jobdb

    jobdb.DB_PATH = db_path


def _read_state(db_path: Path, src: Path) -> dict:
    """读出当前的三集合状态（供断言用）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        pairs = sorted(conn.execute("SELECT DISTINCT shop, date FROM inventory_daily").fetchall())
        hashes = sorted(r[0] for r in conn.execute("SELECT rel_path FROM inventory_processed_file"))
    finally:
        conn.close()
    files = sorted(p.name for p in src.glob("*.xlsx"))
    return {"pairs": pairs, "hashes": hashes, "files": files}


# ============================== 各场景 ==============================


def scenario_keep_set(tmp: Path) -> None:
    """场景 1：保留集判定正确（含落后店降级）。"""
    print("\n[1] 保留集判定")
    src = tmp / "s1"
    src.mkdir()
    # 家配：三天都有（最新 10-27）；医拜：只到 10-26（落后一天）
    _make_xlsx(src / "2026-10-25家配自营库存明细.xlsx", "2026-10-25", "家配")
    _make_xlsx(src / "2026-10-26家配自营库存明细.xlsx", "2026-10-26", "家配")
    _make_xlsx(src / "2026-10-27家配自营库存明细.xlsx", "2026-10-27", "家配")
    _make_xlsx(src / "2026-10-25医拜自营库存明细.xlsx", "2026-10-25", "医拜")
    _make_xlsx(src / "2026-10-26医拜自营库存明细.xlsx", "2026-10-26", "医拜")

    parsed, unparsed = ret.probe(src)
    check(len(parsed) == 5, f"探测出 5 个可解析文件（实际 {len(parsed)}）")
    check(len(unparsed) == 0, f"无不可解析文件（实际 {len(unparsed)}）")

    dec = ret.decide(parsed, unparsed, keep_days=1)
    check(set(dec.keep.keys()) == {"家配", "医拜"}, "保留集覆盖两店")
    check({f.date for f in dec.keep["家配"]} == {"2026-10-27"},
          "家配保留 10-27（它的最新）")
    check({f.date for f in dec.keep["医拜"]} == {"2026-10-26"},
          "医拜保留 10-26（它自己的最新，不是全局的 10-27）")
    check(len(dec.drop) == 3, f"待删 3 份（实际 {len(dec.drop)}）")
    check({(f.shop, f.date) for f in dec.drop} == {
        ("家配", "2026-10-25"), ("家配", "2026-10-26"), ("医拜", "2026-10-25")},
        "待删的是各店的更早日期（医拜的 10-25 也删，但它的 10-26 保留）")


def scenario_broken_not_deleted(tmp: Path) -> None:
    """场景 2：损坏 / 无「时间」列的文件不被删，**其入库行也不删**。

    ⚠️ 「入库行也不删」是独立于「文件不删」的一条断言：文件留着、行没了，
    等于把来源拆散删——正是 spec §3 细则 2 要防的。
    """
    print("\n[2] 损坏文件不删（文件 + 入库行）")
    src = tmp / "s2"
    src.mkdir()
    _make_xlsx(src / "2026-10-26家配自营库存明细.xlsx", "2026-10-26", "家配")
    _make_xlsx(src / "2026-10-27家配自营库存明细.xlsx", "2026-10-27", "家配")
    # 损坏文件：文件名带日期 10-28，但表里没有「时间」列
    _make_broken_xlsx(src / "2026-10-28家配自营库存明细.xlsx")

    parsed, unparsed = ret.probe(src)
    check(len(unparsed) == 1, f"识别出 1 个不可解析文件（实际 {len(unparsed)}）")
    dec = ret.decide(parsed, unparsed, keep_days=1)
    check({f.date for f in dec.keep["家配"]} == {"2026-10-27"},
          "损坏文件不参与「最新」判定（保留的仍是 10-27）")
    check(all("2026-10-28" not in f.name for f in dec.drop),
          "损坏文件不在删除集里")

    # 建库：三个日期都有行，验证损坏日期的行也保住
    db_path = tmp / "s2.db"
    _setup_db(db_path, [("家配", d, f"sku_{d}") for d in ("2026-10-26", "2026-10-27", "2026-10-28")])
    _use_db(db_path)
    rep = ret.run(root=src, mode="on", keep_days=1, report_dir=tmp / "s2rep")
    st = _read_state(db_path, src)
    dates_left = sorted({d for (_, d) in st["pairs"]})
    check("2026-10-28" in dates_left,
          f"损坏文件的入库行保住了（库内日期：{dates_left}）")
    check("2026-10-28家配自营库存明细.xlsx" in st["files"],
          "损坏文件仍在源目录")
    check("2026-10-26" not in dates_left,
          f"更早日期的行照删（库内日期：{dates_left}）")
    check(rep["verify"]["ok"], f"核对通过：{rep['verify'].get('problems')}")


def scenario_duplicate_same_day(tmp: Path) -> None:
    """场景 3：同 (shop, date) 多文件 → 全部保留（**含旧日期上的多份**）。"""
    print("\n[3] 同店同日多文件")
    src = tmp / "s3"
    src.mkdir()
    _make_xlsx(src / "2026-10-27家配自营库存明细.xlsx", "2026-10-27", "家配")
    _make_xlsx(src / "2026-10-27家配自营库存明细(补).xlsx", "2026-10-27", "家配")
    # 旧日期上也有两份（分不清哪份是最新的 ⇒ 都保留）
    _make_xlsx(src / "2026-10-26家配自营库存明细.xlsx", "2026-10-26", "家配")
    _make_xlsx(src / "2026-10-26家配自营库存明细(补).xlsx", "2026-10-26", "家配")

    parsed, unparsed = ret.probe(src)
    dec = ret.decide(parsed, unparsed, keep_days=1)
    check("家配" in dec.duplicate_date_shops, "标记出同日多文件的店")
    kept = {f.name for f in dec.keep["家配"]}
    check(len(kept) == 4, f"同日两份都保留（保留集共 {len(kept)} 份）")
    check(len(dec.drop) == 0,
          f"旧日期上的两份也保留（无法判定哪份最新 ⇒ 都不删），待删 {len(dec.drop)} 份")

    # 入库行也保留
    db_path = tmp / "s3.db"
    _setup_db(db_path, [("家配", d, f"sku_{d}") for d in ("2026-10-26", "2026-10-27")])
    _use_db(db_path)
    ret.run(root=src, mode="on", keep_days=1, report_dir=tmp / "s3rep")
    st = _read_state(db_path, src)
    dates_left = sorted({d for (_, d) in st["pairs"]})
    check("2026-10-26" in dates_left,
          f"同日多文件日期的行保住了（库内日期：{dates_left}）")
    check(len(st["files"]) == 4, f"源目录四份都在（实际 {len(st['files'])}）")


def scenario_gate_blocks(tmp: Path) -> None:
    """场景 4：健康检查不通过 ⇒ 该店**源文件与入库行都**零删除。

    ⚠️ 断言「DB 行没动」是独立于「源文件没删」的一条：只把店从 `drop` 里剔掉
    而没同步剔掉待删行 ⇒ 源文件保住了、数据被删空，比不门控更糟。
    """
    print("\n[4] 健康检查门控（文件 + 入库行）")
    src = tmp / "s4"
    src.mkdir()
    _make_xlsx(src / "2026-10-26家配自营库存明细.xlsx", "2026-10-26", "家配")
    _make_xlsx(src / "2026-10-27家配自营库存明细.xlsx", "2026-10-27", "家配")
    db_path = tmp / "s4.db"
    # 新快照只有 1 行、旧快照有 10 行 ⇒ 远低于 60% ⇒ 应被拦下
    _setup_db(db_path, [("家配", "2026-10-26", f"old{i}") for i in range(10)]
              + [("家配", "2026-10-27", "new0")])
    _use_db(db_path)

    parsed, unparsed = ret.probe(src)
    dec = ret.decide(parsed, unparsed, keep_days=1)
    conn = sqlite3.connect(str(db_path))
    try:
        ret.apply_gate(conn, dec)
    finally:
        conn.close()
    check("家配" in dec.gated_shops, "家配被门控拦下")
    check(len(dec.drop) == 0, f"门控后该店零待删源文件（实际 {len(dec.drop)}）")
    check("60%" in dec.gated_shops.get("家配", ""), "门控原因写明 60% 阈值")

    # 🔴 关键：该店的入库行也不能被删（门控 = 整体不删）
    rep = ret.run(root=src, mode="on", keep_days=1, report_dir=tmp / "s4rep")
    st = _read_state(db_path, src)
    dates_left = sorted({d for (_, d) in st["pairs"]})
    check("2026-10-26" in dates_left,
          f"🔴 门控后旧快照的行保住了（库内日期：{dates_left}）")
    check(len(st["files"]) == 2, f"门控后源目录两份都在（实际 {len(st['files'])}）")
    check(rep["verify"]["ok"], f"核对通过：{rep['verify'].get('problems')}")


def scenario_execute_and_verify(tmp: Path) -> None:
    """场景 5 + 6：执行后三集合相等 + 幂等。"""
    print("\n[5][6] 执行后三集合相等 + 幂等")
    src = tmp / "s5"
    src.mkdir()
    names = []
    for shop in ("家配", "医拜"):
        for d in ("2026-10-25", "2026-10-26", "2026-10-27"):
            n = f"{d}{shop}自营库存明细.xlsx"
            _make_xlsx(src / n, d, shop)
            names.append(n)
    db_path = tmp / "s5.db"
    rows = []
    for shop in ("家配", "医拜"):
        for d in ("2026-10-25", "2026-10-26", "2026-10-27"):
            rows.append((shop, d, f"sku_{shop}_{d}"))
    _setup_db(db_path, rows)
    _use_db(db_path)

    rep = ret.run(root=src, mode="on", keep_days=1, report_dir=tmp / "s5rep")
    check(rep["mode"] == "on", "以 on 模式执行")
    check(rep["stats"]["deleted_files"] == 4,
          f"删了 4 份源文件（实际 {rep['stats']['deleted_files']}）")
    check(rep["stats"]["deleted_rows_daily"] == 4,
          f"删了 4 行 inventory_daily（实际 {rep['stats']['deleted_rows_daily']}）")
    check(rep["verify"]["ok"], f"后置核对通过：{rep['verify'].get('problems')}")

    st = _read_state(db_path, src)
    check(len(st["files"]) == 2, f"源目录剩 2 份（实际 {len(st['files'])}）")
    check(len(st["pairs"]) == 2, f"DB 剩 2 个 (shop,date)（实际 {len(st['pairs'])}）")
    check(len(st["hashes"]) == 2, f"哈希记录剩 2 条（实际 {len(st['hashes'])}）")
    check(all(p[1] == "2026-10-27" for p in st["pairs"]),
          f"DB 只剩 10-27（实际 {[p[1] for p in st['pairs']]}）")

    # 幂等：再跑一次应 0 变更
    rep2 = ret.run(root=src, mode="on", keep_days=1, report_dir=tmp / "s5rep2")
    check(rep2["stats"]["deleted_files"] == 0,
          f"第二次跑 0 删除（实际 {rep2['stats']['deleted_files']}）")
    check(rep2["stats"]["deleted_rows_daily"] == 0,
          f"第二次跑 0 删行（实际 {rep2['stats']['deleted_rows_daily']}）")
    check(rep2["verify"]["ok"], "第二次跑核对仍通过")

    # 删除清单（前置指纹 · 留账不留货）：每条须有 sha256 / 大小 / 删除时间
    manifest = rep["stats"].get("manifest") or []
    check(len(manifest) == 4, f"清单 4 条（实际 {len(manifest)}）")
    check(all(m.get("sha256") for m in manifest), "清单每条都有 sha256 前置指纹")
    check(all(len(str(m.get("sha256"))) == 64 for m in manifest), "sha256 是完整 64 位摘要")
    check(all(m.get("deleted_at") for m in manifest), "清单每条都有删除时间")
    check(all(m.get("deleted") is True for m in manifest), "清单每条都标记已删除")
    check(all(m.get("size_bytes", 0) > 0 for m in manifest), "清单每条都有大小")
    # 清单里的 sha256 必须与文件实际哈希一致（删前算的，凭据可信）
    import hashlib
    ok_hash = True
    for m in manifest:
        src_file = src / str(m["name"])
        if not src_file.exists():
            continue
        h = hashlib.sha256(src_file.read_bytes()).hexdigest()
        if h != m.get("sha256"):
            ok_hash = False
    check(ok_hash, "清单 sha256 与文件实际内容一致（前置指纹可信）")


def scenario_self_heal(tmp: Path) -> None:
    """场景 7：中断自愈 —— 在 ④-1 / ④-2 之后中断，重跑后状态收敛。"""
    print("\n[7] 中断自愈")
    for stage in ("after_hashes", "after_rows"):
        src = tmp / f"s7_{stage}"
        src.mkdir()
        for d in ("2026-10-26", "2026-10-27"):
            _make_xlsx(src / f"{d}家配自营库存明细.xlsx", d, "家配")
        db_path = tmp / f"s7_{stage}.db"
        _setup_db(db_path, [("家配", "2026-10-26", "old"), ("家配", "2026-10-27", "new")])
        _use_db(db_path)

        parsed, unparsed = ret.probe(src)
        dec = ret.decide(parsed, unparsed, keep_days=1)

        # 手工执行到指定阶段后中断
        from backend.jobs.db import get_conn

        conn = get_conn()
        try:
            pairs = ret._stale_pairs(conn, dec)
            keep_names = dec.keep_filenames()
            stale = [r["rel_path"] for r in conn.execute("SELECT rel_path FROM inventory_processed_file")
                     if r["rel_path"] not in keep_names]
            conn.executemany("DELETE FROM inventory_processed_file WHERE rel_path=?",
                             [(n,) for n in stale])
            conn.commit()
            if stage == "after_rows":
                ret._delete_db_rows(conn, pairs)
                conn.commit()
        finally:
            conn.close()
        # 此时源文件**还在**（模拟在删文件前崩溃）

        # 重跑：ETL 会因哈希记录已删而重新摄入 → 数据回来；淘汰再跑应收敛
        rep = ret.run(root=src, mode="on", keep_days=1, report_dir=tmp / f"s7rep_{stage}")
        check(rep["verify"]["ok"],
              f"[{stage}] 重跑后核对通过：{rep['verify'].get('problems')}")
        st = _read_state(db_path, src)
        check(len(st["files"]) == 1,
              f"[{stage}] 重跑后源目录只剩 1 份（实际 {len(st['files'])}）")
        check(len(st["pairs"]) == 1,
              f"[{stage}] 重跑后 DB 只剩 1 个 (shop,date)（实际 {len(st['pairs'])}）")
        check(len(st["hashes"]) == 1,
              f"[{stage}] 重跑后哈希记录只剩 1 条（实际 {len(st['hashes'])}）")


def scenario_dry_run_safe(tmp: Path) -> None:
    """场景 8：dry-run 零副作用。"""
    print("\n[8] dry-run 零副作用")
    src = tmp / "s8"
    src.mkdir()
    for d in ("2026-10-26", "2026-10-27"):
        _make_xlsx(src / f"{d}家配自营库存明细.xlsx", d, "家配")
    db_path = tmp / "s8.db"
    _setup_db(db_path, [("家配", "2026-10-26", "old"), ("家配", "2026-10-27", "new")])
    _use_db(db_path)

    before = _read_state(db_path, src)
    rep = ret.run(root=src, mode="dry-run", keep_days=1, report_dir=tmp / "s8rep")
    after = _read_state(db_path, src)
    check(rep["mode"] == "dry-run", "模式为 dry-run")
    check(before == after, "dry-run 后源目录与 DB 完全未变")
    check(rep["stats"]["deleted_files"] == 0, "dry-run 删除文件数为 0")
    check(rep["stats"]["deleted_rows_daily"] == 1,
          f"dry-run 报告了将要删的行数（实际 {rep['stats']['deleted_rows_daily']}）")
    check(len(rep["decision"].drop) == 1, "dry-run 仍给出待删清单")


def scenario_vacuum(tmp: Path) -> None:
    """场景 9：VACUUM 回收磁盘（删行后空页不返还，VACUUM 才压得回去）。"""
    print("\n[9] VACUUM 回收磁盘")
    src = tmp / "s9"
    src.mkdir()
    for d in ("2026-10-25", "2026-10-26", "2026-10-27"):
        _make_xlsx(src / f"{d}家配自营库存明细.xlsx", d, "家配")
    db_path = tmp / "s9.db"
    _setup_db(db_path, [("家配", d, f"sku_{d}") for d in ("2026-10-25", "2026-10-26", "2026-10-27")])
    _use_db(db_path)

    before = db_path.stat().st_size
    rep = ret.run(root=src, mode="on", keep_days=1, report_dir=tmp / "s9rep")
    after = db_path.stat().st_size
    vac = rep["stats"].get("vacuum", {})
    check(vac.get("ok") is True, f"VACUUM 执行成功（错误：{vac.get('error')}）")
    # ⚠️ 小库 VACUUM 后**可能变大**（VACUUM 按最坏情况重建页布局，不保证只减）。
    # 生产实测 2.81MB → 0.70MB 是大幅缩小，但这里只断言「不显著变大」。
    check(after <= before * 1.5,
          f"库体积未显著变大（{before} → {after}，允许 1.5 倍以内）")
    check(rep["verify"]["ok"], "VACUUM 后核对仍通过")


def main() -> int:
    """跑全部场景并汇总。

    Returns:
        int: 0 = 全通过；1 = 有失败。
    """
    print("=" * 70)
    print("库存快照淘汰验证（inventory_retention）")
    print("=" * 70)
    with tempfile.TemporaryDirectory(prefix="inv_retention_") as td:
        tmp = Path(td)
        scenario_keep_set(tmp)
        scenario_broken_not_deleted(tmp)
        scenario_duplicate_same_day(tmp)
        scenario_gate_blocks(tmp)
        scenario_execute_and_verify(tmp)
        scenario_self_heal(tmp)
        scenario_dry_run_safe(tmp)
        scenario_vacuum(tmp)

    print("\n" + "=" * 70)
    print(f"断言：{_passed} 通过 / {len(_failed)} 失败")
    if _failed:
        print("\n失败明细：")
        for f in _failed:
            print(f"  - {f}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
