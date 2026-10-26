#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工单 04 的验证：整理器「隔离 + 哨兵」——形态异常文件搬进隔离区、被隔离文件的独有 key 触发 ERROR 级告警。

为什么需要它：
    隔离把「形态异常、无法安全判定」的文件移出源表目录（⛔ 不猜规则、不猜列位置）。
    但移走一个文件在这个仓库里是**无声的**：`daily_detail` 只增不减，被移走文件贡献的行
    不会消失，而是永久冻结成僵尸行 / 历史缺口（详见 `verify_organize.py` 开头）。所以隔离
    必须附带一道**哨兵**——把被隔离文件的数据 key 与**保留集**的 key 求差，只要存在某个 key
    仅由该文件持有，就升级为 ERROR 告警。这道哨兵是把「隔离了一份冗余文件」与「隔离了一份
    唯一数据源」区分开来的**唯一**手段（ADR-0005）。

    🔴 哨兵的 key 不能直接用判定层的 key：判定层把「提不出 key」的文件判为 quarantine——
    也就是说被隔离文件自己恰好是 **0 个 key**。若直接拿 0 个 key 求差，哨兵会永远沉默、
    形同虚设。所以哨兵另走一条**只读的宽松探针**（`organize.probe_file_keys`），用与生产
    同一批**声明列名**把能读到的行尽量还原成 key，**只为估计风险**——宁可误报，不可漏报。

覆盖工单 04 的 5 项验收（全部在临时目录的源根副本上执行）：
    1. 隔离搬移：形态异常文件被移进 `{root}/_quarantine/`（源表目录之外）、保持原文件名、
       write-ahead 清单落盘、源表目录里已不含它们、且⛔ 没有被猜规则处理
    2. full 夹具跑 `run_once()` **成功入库**（这是隔离搬移的收口验证：以前会 ColumnNotFoundError）
    3. 哨兵 ERROR：持有独有 key 的形态异常文件 → 必须告警（点名文件 + 独有 key 数量 + 样例）
    4. 哨兵静默：独有 key 都能在保留集里找到的形态异常文件 → **不得**报警
    5. 隔离区不参与归档区 60 天清理 + 报告含「隔离」「哨兵告警」两段
    另覆盖：幂等、dry-run 零副作用、单文件失败不中断、真实目录 dry-run 指纹零改动。

风格：沿用 `verify_organize_execute.py`——独立脚本 + 复用 `verify_organize.Checker` + sys.exit(1)。
⛔ 不引入 pytest。⛔ 不在真实 `ResourceData` 上执行搬移（除 `realdryrun` 子命令的只读 dry-run）。

用法（必须用项目 venv 的解释器）：
    .venv/Scripts/python.exe backend/jobs/verify_organize_quarantine.py selftest
    .venv/Scripts/python.exe backend/jobs/verify_organize_quarantine.py realdryrun ResourceData
"""
from __future__ import annotations

import argparse
import gc
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# 把仓库根目录塞进 sys.path：直接 `python backend/jobs/verify_organize_quarantine.py` 也能导入 backend 包
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.jobs import config  # noqa: E402
from backend.jobs import db as jobdb  # noqa: E402
from backend.jobs import organize, run_batch  # noqa: E402
from backend.jobs import verify_organize as vo  # noqa: E402
from backend.jobs import verify_organize_decision as vod  # noqa: E402

TABLE_DIR_NAME = config.POP_TABLE_DIR_NAME
WHITELIST_FILE = "店铺spu登记信息.xlsx"

# 固定批次时间：让归档子目录名与清单路径可预期（不依赖「今天」）。
BATCH_NOW = datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc)
BATCH = "2026-10-12"

# full 夹具的固定决策数（工单 02 已固化：keep12 / archive5 / quarantine2 / skip1）。
EXPECT_KEEP = 12
EXPECT_ARCHIVE = 5
EXPECT_QUARANTINE_FULL = 2
EXPECT_SKIP = 1

# full 夹具里两份形态异常文件（工单 01 生成）
MALFORMED_NO_SPU = "钻芯旗舰店_商品明细_2025-03-01_2025-03-30.xlsx"        # 缺 SPU 列 → ColumnNotFoundError
MALFORMED_BAD_DATE = "钻芯旗舰店_商品明细_2025-03-01_2025-03-30_日期异常.xlsx"  # 时间 2025/03/01 → 过滤后 0 行
# 缺 SPU 列那份读不出任何 key，日期异常那份的宽松探针能还原出 (1001, 2025-03-01..05)。
# 与保留集（钻芯旗舰店明细 keep：01-01..03-02）求差 → 独有 (1001, 2025-03-03..05) 共 3 个。
EXPECT_BAD_DATE_UNIQUE = 3


def _fresh_copy(base: Path, dest: Path) -> Path:
    """把夹具母本整棵拷一份到 dest（每个验收项各用独立副本，互不污染）。"""
    shutil.copytree(base, dest)
    return dest


def _table_dir(root: Path) -> Path:
    return root / TABLE_DIR_NAME


def _qdir(root: Path) -> Path:
    return root / organize.QUARANTINE_DIR_NAME


def _qmanifest(root: Path) -> dict:
    return json.loads(organize._quarantine_manifest_path(root).read_text(encoding="utf-8"))


def _write_covered_malformed(table: Path, name: str) -> None:
    """写一份「形态异常、但探针能还原出的 key 全在保留集里」的明细夹具。

    为什么要有它：哨兵必须区分两种沉默——①「压根读不出 key」（如缺 SPU 列）与
    ②「读得出 key、但这些 key 别处有来源」。只有 ② 才真正证明哨兵**不是因为没 key 才沉默**。
    这份文件用 `2025/01/05` 这类**生产日期正则不认**的形态（于是被判隔离），但日期落在
    保留集 ①「01-01..01-30」窗口内、SPU 1001 又在白名单里 → 宽松探针还原出的 key 全被保留集覆盖。
    """
    dates = ["2025/01/05", "2025/01/06", "2025/01/07"]
    rows = [[d, 1001, "商品1001", "三级类目A"] + [1.0] * 9 + [3.5] for d in dates]
    vo._write_xlsx(table / name, vo.DETAIL_HEADER, rows)


# ==========================================================================
# 证据 1：隔离搬移（write-ahead 清单 / 源表目录外 / 原文件名 / 不被猜规则）
# ==========================================================================

def evidence_quarantine_move(base: Path, tmp: Path, ck: vo.Checker) -> Path:
    """在 full 夹具副本上真执行一次，检查隔离搬移 + 清单，并顺手取回报告供哨兵断言。

    返回：执行后的副本根目录（供后续幂等复用）。
    """
    src = _fresh_copy(base, tmp / "qmove")
    table = _table_dir(src)
    qdir = _qdir(src)
    qman_path = organize._quarantine_manifest_path(src)

    decisions = organize.derive_decisions(src)
    qnames = sorted(d["filename"] for d in decisions if d["decision"] == organize.QUARANTINE)
    anames = sorted(d["filename"] for d in decisions if d["decision"] == organize.ARCHIVE)
    keep = sorted(d["filename"] for d in decisions if d["decision"] == organize.KEEP)
    ck.check(len(qnames) == EXPECT_QUARANTINE_FULL,
             f"full 夹具待隔离份数 == {EXPECT_QUARANTINE_FULL}（实际 {len(qnames)}）", str(qnames))
    ck.check(len(anames) == EXPECT_ARCHIVE, f"full 夹具待归档份数 == {EXPECT_ARCHIVE}")

    # 探针：搬第一个**隔离**文件之前，整批隔离条目是否已 planned 落盘（write-ahead）
    trace: dict = {}

    def spy_move(s, d):
        if "first_q_status" not in trace and Path(d).parent == qdir:
            man = json.loads(qman_path.read_text(encoding="utf-8"))
            trace["first_q_status"] = [e["status"] for e in man["entries"]]
        organize.move_file(s, d)

    report = organize.organize_files(src, mode=organize.MODE_ON, now=BATCH_NOW, move=spy_move)

    print("--- 证据 1：隔离搬移 ---")
    print(f"隔离的 {len(qnames)} 份：{qnames}")
    print(f"报告：quarantined.planned={report['quarantined']['planned']} "
          f"moved={report['quarantined']['moved']} failed={report['quarantined']['failed']}")
    print(f"隔离区：{report['quarantined']['dir']}")

    # ① 隔离区在源表目录之外、保持原文件名
    ck.check(table not in qdir.resolve().parents and qdir.resolve() != table.resolve(),
             "隔离区位于源表目录之外（批处理 glob 读不到）")
    ck.check(qdir.is_dir(), "隔离区目录已生成")
    missing = [n for n in qnames if not (qdir / n).is_file()]
    lingering = [n for n in qnames if (table / n).exists()]
    ck.check(not missing, "隔离区里每一份都在（保持原文件名）", str(missing))
    ck.check(not lingering, "源表目录里已不含被隔离文件", str(lingering))
    ck.check(len(report["quarantined"]["moves"]) == len(qnames)
             and all(m["status"] == organize.STATUS_DONE for m in report["quarantined"]["moves"]),
             "报告里隔离搬移全部 done", str(report["quarantined"]["moves"]))

    # ② write-ahead：搬第一个隔离文件之前，整批隔离条目已经 planned 落盘
    first = trace.get("first_q_status", [])
    ck.check(len(first) == len(qnames) and set(first) == {organize.STATUS_PLANNED},
             "write-ahead：搬第一个隔离文件前，整批隔离条目已 planned 落盘",
             f"首个隔离搬动时刻的清单状态={first}")

    # ③ 隔离清单：与归档清单各自独立，字段含 原路径/隔离后路径/理由/批次时间/状态
    qman = _qmanifest(src)
    fields = {"source_rel", "quarantine_rel", "reason", "batch_time", "status"}
    ck.check(all(fields <= set(e) for e in qman["entries"]),
             "隔离清单每条含 原路径/隔离后路径/理由/批次时间/状态",
             f"字段样例={sorted(qman['entries'][0])}")
    ck.check(all(e["status"] == organize.STATUS_DONE for e in qman["entries"]),
             "隔离搬完后整批状态为 done", str([e["status"] for e in qman["entries"]]))
    ck.check(all(e["quarantine_rel"].startswith(f"{organize.QUARANTINE_DIR_NAME}/")
                 for e in qman["entries"]), "清单的隔离后相对路径落在 _quarantine/ 下")
    ck.check(organize._quarantine_manifest_path(src).is_file()
             and not organize._manifest_path(src, BATCH) == organize._quarantine_manifest_path(src),
             "隔离清单与归档清单各自独立（不同路径）")

    # ④ 源表目录里剩下的 = 保留集 + 跳过 + ~$ 锁文件（用与批处理同一套枚举，含推广 csv）
    remaining = sorted(p.name for p in organize._iter_source_files(table))
    skipped = sorted(d["filename"] for d in decisions if d["decision"] == organize.SKIP)
    ck.check(set(remaining) == set(keep) | set(skipped),
             "源表目录里只剩下保留集 + 跳过文件",
             f"多出={sorted(set(remaining) - set(keep) - set(skipped))[:3]}；"
             f"缺={sorted((set(keep) | set(skipped)) - set(remaining))[:3]}")

    # ⑤ ⛔ 不被猜规则处理：形态异常文件没有出现在任何其它决策里（它们既没被归档、也没被当数据读）
    ck.check(not any(n in anames for n in qnames), "形态异常文件不会被拿去归档（⛔ 不猜规则）")
    return src


# ==========================================================================
# 证据 2：full 夹具跑 run_once() 成功入库（隔离搬移的收口验证）
# ==========================================================================

def evidence_run_once_full(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """full 夹具（含「缺 SPU 列」那份）经 run_once() 必须成功入库；并在另一库比对逐格不变。

    ⚠️ 全程把 config 的产出目录 / DB 路径 / 源根都指向临时副本，⛔ 绝不触发真实目录的批处理。
    以前（工单 04 之前）full 夹具直接跑 run_once 会因「缺 SPU 列」的 ColumnNotFoundError 崩溃，
    现在 organize 先把形态异常文件搬进隔离区 → 摄入只读保留集 → 成功（03 Comments #4）。
    """
    src = _fresh_copy(base, tmp / "ronce")
    data_dir = tmp / "ronce_data"
    saved = {k: getattr(config, k) for k in
             ("POP_SOURCE_DIR", "DATA_DIR", "IMAGES_DIR", "THUMBS_DIR")}
    saved_db = jobdb.DB_PATH
    saved_mode = config.ORGANIZE_MODE
    snap_full = None
    try:
        config.POP_SOURCE_DIR = src
        config.DATA_DIR = data_dir
        config.IMAGES_DIR = data_dir / "images"
        config.THUMBS_DIR = config.IMAGES_DIR / "thumbs"
        jobdb.DB_PATH = data_dir / "app.db"
        config.ORGANIZE_MODE = "on"       # 真搬（隔离 + 归档），否则 full 夹具会崩
        status = run_batch.run_once()
        rows = jobdb.query_long_table()
        snap_full = vo.snapshot(jobdb.DB_PATH, tmp / "snap_full.json")
    finally:
        jobdb.DB_PATH = saved_db
        config.ORGANIZE_MODE = saved_mode
        for k, v in saved.items():
            setattr(config, k, v)

    # 对照组：clean 夹具（不含「缺 SPU 列」那份）直接摄入的库
    clean = tmp / "fx_clean"
    vo.make_fixtures(clean, clean=True)
    db_clean = tmp / "clean.db"
    vo._ingest_into(clean, db_clean)
    snap_clean = vo.snapshot(db_clean, tmp / "snap_clean.json")

    diffs = vo.diff_snapshots(snap_clean, snap_full)
    cell_diffs = [d for d in diffs if d["kind"] != "processed_file"]

    print("--- 证据 2：full 夹具 run_once() 成功入库 ---")
    print(f"success={status.success}；BatchStatus.message = {status.message}")
    print(f"库内行数={len(rows)}；与 clean 夹具库比对：格级/行级差异={len(cell_diffs)}")

    ck.check(status.success is True, "full 夹具跑 run_once() 成功（隔离搬移的收口）", status.message)
    ck.check(len(rows) > 0, "入库确有数据（不是空对空的假绿）", f"rows={len(rows)}")
    ck.check((src / organize.QUARANTINE_DIR_NAME).is_dir()
             and not (_table_dir(src) / MALFORMED_NO_SPU).exists(),
             "run_once 期间形态异常文件被搬进隔离区（源表目录里已无它）")
    ck.check(snap_full["row_count"] == snap_clean["row_count"] and not cell_diffs,
             "隔离不改变数据：与 clean 夹具库逐格一致（形态异常文件本就贡献 0 行）",
             "; ".join(vo.format_diff(d) for d in cell_diffs[:2])[:300])


# ==========================================================================
# 证据 3 + 4：哨兵（有独有 key → ERROR；独有 key 都能在保留集找到 → 静默）
# ==========================================================================

def evidence_sentinel_fires(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """持有独有 key 的形态异常文件（日期异常那份）必须触发 ERROR 告警。"""
    src = _fresh_copy(base, tmp / "sentinel_on")
    files = organize.collect_file_keys(src)
    decisions = organize.decide(files, organize.load_contributing_shops(src))
    alerts = organize.compute_sentinel_alerts(src, decisions, files)
    by_name = {a["filename"]: a for a in alerts}

    # 探针证据：先证明「日期异常那份确实能还原出独有 key」（而不是因为没 key 才沉默）
    probe = organize.probe_file_keys(_table_dir(src) / MALFORMED_BAD_DATE, "钻芯旗舰店", organize.KIND_DETAIL)
    keep_keys: set = set()
    for d in decisions:
        if d["decision"] == organize.KEEP and d["shop"] == "钻芯旗舰店" and d["kind"] == organize.KIND_DETAIL:
            keep_keys |= files[d["filename"]]["keys"]
    unique = sorted(probe - keep_keys)

    print("--- 证据 3：哨兵 ERROR（持有独有 key 的形态异常文件）---")
    print(f"探针从 {MALFORMED_BAD_DATE} 还原出 {len(probe)} 个 key；"
          f"其中独有 {len(unique)} 个，样例 {unique[:3]}")
    for a in alerts:
        print(f"  [ERROR] {a['filename']}：独有 key {a['unique_count']} 个，样例 {a['unique_keys']}")

    ck.check(len(probe) > 0, "宽松探针能从形态异常文件还原出 key（哨兵不是因为没 key 才沉默）")
    ck.check(len(unique) > 0, "该文件确实持有保留集里找不到的独有 key")
    ck.check(MALFORMED_BAD_DATE in by_name
             and by_name[MALFORMED_BAD_DATE]["severity"] == "ERROR",
             "持有独有 key 的形态异常文件触发 ERROR 级告警", str(sorted(by_name)))
    if MALFORMED_BAD_DATE in by_name:
        a = by_name[MALFORMED_BAD_DATE]
        ck.check(a["unique_count"] == EXPECT_BAD_DATE_UNIQUE and a["unique_keys"] and a["shop"] == "钻芯旗舰店",
                 f"告警点名文件 + 独有 key 数量（{EXPECT_BAD_DATE_UNIQUE}）+ 样例 key", str(a))
    # 没有 key 可还原的那份（缺 SPU 列）不该告警
    ck.check(MALFORMED_NO_SPU not in by_name,
             "还原不出 key 的形态异常文件不告警（无从判定，不等同于有独有 key）")


def evidence_sentinel_silent(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """独有 key 都能在保留集里找到的形态异常文件 → 必须**静默**（无独有 key 不报警）。"""
    src = _fresh_copy(base, tmp / "sentinel_off")
    table = _table_dir(src)
    # 去掉自带的那份「日期异常」（它会真告警），换成一份 key 全被覆盖的形态异常文件
    (table / MALFORMED_BAD_DATE).unlink()
    covered = "钻芯旗舰店_商品明细_2025-01-05_2025-01-10_形态异常.xlsx"
    _write_covered_malformed(table, covered)

    files = organize.collect_file_keys(src)
    decisions = organize.decide(files, organize.load_contributing_shops(src))
    alerts = organize.compute_sentinel_alerts(src, decisions, files)

    got_decision = next((d for d in decisions if d["filename"] == covered), None)
    probe = organize.probe_file_keys(table / covered, "钻芯旗舰店", organize.KIND_DETAIL)
    keep_keys: set = set()
    for d in decisions:
        if d["decision"] == organize.KEEP and d["shop"] == "钻芯旗舰店" and d["kind"] == organize.KIND_DETAIL:
            keep_keys |= files[d["filename"]]["keys"]
    unique = sorted(probe - keep_keys)

    print("--- 证据 4：哨兵静默（独有 key 都能在保留集里找到）---")
    print(f"{covered}：决策={got_decision['decision'] if got_decision else '<缺失>'}；"
          f"探针还原 {len(probe)} 个 key，独有 {len(unique)} 个")
    print(f"告警数：{len(alerts)}")

    ck.check(got_decision is not None and got_decision["decision"] == organize.QUARANTINE,
             "这份形态异常文件确实被判隔离（生产日期正则不认 2025/01/xx）", str(got_decision))
    ck.check(len(probe) > 0 and not unique,
             "探针能还原出 key，但它们全被保留集覆盖（独有集为空）", f"unique={unique[:3]}")
    ck.check(alerts == [], "独有 key 都能在保留集里找到 → 哨兵不报警（隔离只是整理，不是事故）",
             str(alerts))


# ==========================================================================
# 证据 5：隔离区不参与归档区 60 天清理 + 报告数据段
# ==========================================================================

def evidence_no_cleanup_and_report(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """证明「隔离区永不入清理候选」，且报告里含「隔离」「哨兵告警」两段事实。"""
    src = _fresh_copy(base, tmp / "report")
    # 造两处目录：归档批次目录 + 隔离区各放一个「看起来该被清理」的老文件
    batch_dir = src / organize.ARCHIVE_DIR_NAME / BATCH
    batch_dir.mkdir(parents=True)
    (batch_dir / "old_archive.xlsx").write_text("x", encoding="utf-8")
    qdir = _qdir(src)
    qdir.mkdir(parents=True)
    (qdir / "old_quarantine.xlsx").write_text("x", encoding="utf-8")

    batches = organize.iter_archive_batches(src)
    names = [p.name for p in batches]

    print("--- 证据 5：隔离区不参与 60 天清理 + 报告数据 ---")
    print(f"iter_archive_batches 只枚举到：{names}；隔离区目录名={organize.QUARANTINE_DIR_NAME}")
    ck.check(names == [BATCH], "清理候选只含 _archive/ 下的批次目录", str(names))
    ck.check(organize.QUARANTINE_DIR_NAME not in names
             and not any(organize.QUARANTINE_DIR_NAME in p.parts for p in batches),
             "⛔ 隔离区永不入清理候选（结构上扫不到 _quarantine/）", str(names))
    ck.check(organize.QUARANTINE_DIR_NAME != organize.ARCHIVE_DIR_NAME
             and not str(qdir.resolve()).startswith(str(batch_dir.resolve())),
             "隔离区与归档区是两个互不包含的目录")

    # 报告数据：dry-run 下也应含两段事实（预演告警，便于过目）
    src2 = _fresh_copy(base, tmp / "report_run")
    rep = organize.organize_files(src2, mode=organize.MODE_DRY_RUN, now=BATCH_NOW)
    qsect = rep.get("quarantined")
    ck.check(isinstance(qsect, dict)
             and {"dir", "manifest", "planned", "moved", "failed", "moves"} <= set(qsect),
             "报告含「隔离」段（quarantined：dir/manifest/planned/moved/failed/moves）", str(qsect))
    ck.check(qsect["planned"] == EXPECT_QUARANTINE_FULL and qsect["moved"] == 0,
             "dry-run 下隔离段 planned 正确、moved=0（零副作用）", str(qsect))
    ck.check(isinstance(rep.get("sentinel_alerts"), list) and rep["sentinel_alerts"]
             and rep.get("sentinel_error") is True,
             "报告含「哨兵告警」段（sentinel_alerts + sentinel_error）",
             str(rep.get("sentinel_alerts"))[:200])
    ck.check(all({"severity", "filename", "shop", "kind", "unique_count", "unique_keys", "reason"}
                 <= set(a) for a in rep["sentinel_alerts"]),
             "每条哨兵告警带 严重级/文件/店铺/独有 key 数量/样例/理由",
             str(sorted(rep["sentinel_alerts"][0])) if rep["sentinel_alerts"] else "")


# ==========================================================================
# 证据 6：幂等
# ==========================================================================

def evidence_idempotent(qmove_src: Path, ck: vo.Checker) -> None:
    """在原本次执行过的副本上再跑一次 → 归档与隔离都必须零搬动，且不动任何文件。"""
    table = _table_dir(qmove_src)
    qdir = _qdir(qmove_src)
    fp_before = vod._fingerprint(table)
    qfp_before = vod._fingerprint(qdir)
    report = organize.organize_files(qmove_src, mode=organize.MODE_ON, now=BATCH_NOW)
    fp_after = vod._fingerprint(table)
    qfp_after = vod._fingerprint(qdir)

    print("--- 证据 6：幂等 ---")
    print(f"再跑一次：archive planned={report['planned']} moved={report['moved']}；"
          f"quarantine planned={report['quarantined']['planned']} "
          f"moved={report['quarantined']['moved']}")
    ck.check(report["moved"] == 0 and report["planned"] == 0,
             "紧接着再跑一次 → 归档零搬动（幂等）", f"{report['planned']}/{report['moved']}")
    ck.check(report["quarantined"]["moved"] == 0 and report["quarantined"]["planned"] == 0,
             "紧接着再跑一次 → 隔离零搬动（幂等）", str(report["quarantined"]))
    ck.check(fp_before == fp_after, "幂等运行没有改动源表目录（文件名 + mtime + 大小）")
    ck.check(qfp_before == qfp_after, "幂等运行没有改动隔离区")


# ==========================================================================
# 证据 7：dry-run 零副作用
# ==========================================================================

def evidence_dry_run(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """dry-run 必须不搬文件、不写清单、不生成任何目录、退出码 0；默认模式也必须是 dry-run。"""
    src = _fresh_copy(base, tmp / "dry")
    table = _table_dir(src)
    fp_before = vod._fingerprint(table)

    report = organize.organize_files(src, mode=organize.MODE_DRY_RUN, now=BATCH_NOW)
    fp_after = vod._fingerprint(table)
    archive_exists = (src / organize.ARCHIVE_DIR_NAME).exists()
    quarantine_exists = _qdir(src).exists()

    # CLI 层也验一遍：显式 --mode dry-run 与不给 --mode（默认）都必须零副作用、退出码 0
    cli = subprocess.run(
        [sys.executable, "-m", "backend.jobs.organize", "--source", str(src), "--mode", "dry-run"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    cli_default = subprocess.run(
        [sys.executable, "-m", "backend.jobs.organize", "--source", str(src)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")

    print("--- 证据 7：dry-run 零副作用 ---")
    print(f"进程内 dry-run：archive moved={report['moved']} quarantine moved={report['quarantined']['moved']}；"
          f"源表目录指纹一致={fp_before == fp_after}")
    print(f"CLI（--mode dry-run）退出码={cli.returncode}；CLI（默认）退出码={cli_default.returncode}")

    ck.check(report["moved"] == 0 and report["planned"] == EXPECT_ARCHIVE,
             f"dry-run 不搬归档（计划 {EXPECT_ARCHIVE}，实际 {report['moved']}）")
    ck.check(report["quarantined"]["moved"] == 0
             and report["quarantined"]["planned"] == EXPECT_QUARANTINE_FULL,
             f"dry-run 不搬隔离（计划 {EXPECT_QUARANTINE_FULL}，实际 {report['quarantined']['moved']}）")
    ck.check(fp_before == fp_after, "dry-run 前后源表目录指纹完全一致")
    ck.check(not archive_exists and not quarantine_exists, "dry-run 没有生成归档区 / 隔离区")
    ck.check(not organize._manifest_path(src, BATCH).exists()
             and not organize._quarantine_manifest_path(src).exists(), "dry-run 没有写任何清单")
    ck.check(cli.returncode == 0, "CLI --mode dry-run 退出码为 0", (cli.stderr or "")[:200])
    ck.check(cli_default.returncode == 0 and not (src / organize.ARCHIVE_DIR_NAME).exists()
             and not _qdir(src).exists(), "CLI 默认不给 --mode 时也零副作用（默认 dry-run）")


# ==========================================================================
# 证据 8：单文件失败不中断（隔离搬移阶段同样容错）
# ==========================================================================

def evidence_single_file_failure(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """一个隔离文件搬不动 → 记 failed + 原因、其余照常搬完、本批照常入库。"""
    src = _fresh_copy(base, tmp / "fail")
    table = _table_dir(src)
    # 让「日期异常」那份搬不动（它是合法 xlsx，留在原地不影响后续摄入），
    # 于是「缺 SPU 列」那份照常被隔离 → 摄入不会再撞 ColumnNotFoundError。
    victim = MALFORMED_BAD_DATE

    def flaky(s, d):
        # 模拟「文件被 Excel 占用 / 权限不足」：只对 victim 抛错，其余正常搬
        if Path(s).name == victim:
            raise PermissionError("simulated: 文件被占用，无法搬动")
        organize.move_file(s, d)

    report = organize.organize_files(src, mode=organize.MODE_ON, now=BATCH_NOW, move=flaky)
    qman = _qmanifest(src)
    vic_entry = next(e for e in qman["entries"] if Path(e["source_rel"]).name == victim)

    db = tmp / "fail.db"
    ingest = vo._ingest_into(src, db)

    print("--- 证据 8：单文件失败不中断（隔离阶段）---")
    print(f"victim={victim}：quarantine moved={report['quarantined']['moved']} "
          f"failed={report['quarantined']['failed']}；archive moved={report['moved']}")
    print(f"victim 隔离清单状态={vic_entry['status']}；原因={vic_entry['error']}")
    print(f"随后入库：changed={ingest.get('changed')} rows={ingest.get('rows')}")

    ck.check(report["quarantined"]["failed"] == 1
             and report["quarantined"]["moved"] == EXPECT_QUARANTINE_FULL - 1,
             "隔离阶段恰好 1 份失败、其余照常搬完（本批不中断）",
             f"moved={report['quarantined']['moved']} failed={report['quarantined']['failed']}")
    ck.check(report["moved"] == EXPECT_ARCHIVE,
             "隔离阶段失败不影响归档阶段（归档仍全部搬完）", f"archive moved={report['moved']}")
    ck.check(vic_entry["status"] == organize.STATUS_FAILED and "PermissionError" in vic_entry["error"],
             "失败文件在隔离清单里记为 failed 并带上真实原因", str(vic_entry))
    ck.check((table / victim).exists(), "失败文件留在源表目录原地（未被误移走）")
    ck.check(int(ingest.get("rows", 0)) > 0, "本批照常完成入库（隔离失败未拖垮）", str(ingest))


# ==========================================================================
# selftest
# ==========================================================================

def cmd_selftest(_args) -> int:
    """在临时目录里把证据 1–8 全跑一遍（每项用独立源根副本）。"""
    ck = vo.Checker("工单 04 自证：隔离搬移 + 哨兵告警（只报警不阻断）")
    tmp = Path(tempfile.mkdtemp(prefix="verify_organize_quarantine_"))
    try:
        base = tmp / "base"
        inv = vo.make_fixtures(base, clean=False)   # full 夹具：含两份形态异常文件
        ck.check(not vo.verify_fixture_oracle(base, inv), "full 夹具的 oracle 自检通过（底座可信）")

        qmove_src = evidence_quarantine_move(base, tmp, ck)
        evidence_run_once_full(base, tmp, ck)
        evidence_sentinel_fires(base, tmp, ck)
        evidence_sentinel_silent(base, tmp, ck)
        evidence_no_cleanup_and_report(base, tmp, ck)
        evidence_idempotent(qmove_src, ck)
        evidence_dry_run(base, tmp, ck)
        evidence_single_file_failure(base, tmp, ck)

        # 安全护栏自检：所有执行都发生在临时目录，没有污染真实数据源
        ck.check(config.POP_SOURCE_DIR.resolve() not in base.resolve().parents
                 and base.resolve() != config.POP_SOURCE_DIR.resolve(),
                 "所有执行都在临时目录的源根副本上，未触碰真实 ResourceData")
    finally:
        gc.collect()  # 兼容旧实现：万一还有未关闭的句柄，先触发循环 GC 再删
        shutil.rmtree(tmp, ignore_errors=True)
        ck.check(not tmp.exists(), "临时目录已清理")
    return ck.report()


# ==========================================================================
# realdryrun：真实数据只读 dry-run
# ==========================================================================

def cmd_realdryrun(args) -> int:
    """在真实源表目录上只跑 dry-run：贴统计并断言指纹零改动（⛔ 不搬任何文件）。"""
    ck = vo.Checker("工单 04 realdryrun：真实数据只读 dry-run（零副作用）")
    root = Path(args.source).expanduser().resolve()
    table = _table_dir(root)
    ck.check(table.is_dir(), f"数据源表目录存在：{table}")
    if not table.is_dir():
        return ck.report()

    root_before = sorted(p.name for p in root.glob("*"))
    fp_before = vod._fingerprint(table)
    archive_before = (root / organize.ARCHIVE_DIR_NAME).exists()
    quarantine_before = _qdir(root).exists()

    report = organize.organize_files(root, mode=organize.MODE_DRY_RUN, now=BATCH_NOW)

    fp_after = vod._fingerprint(table)
    root_after = sorted(p.name for p in root.glob("*"))
    archive_after = (root / organize.ARCHIVE_DIR_NAME).exists()
    quarantine_after = _qdir(root).exists()
    c = report["counts"]

    print(f"真实源表目录：{table}（{len(fp_before)} 个条目）")
    print(f"决策统计：keep {c[organize.KEEP]} / archive {c[organize.ARCHIVE]} / "
          f"quarantine {c[organize.QUARANTINE]} / skip {c[organize.SKIP]}"
          f"（计划归档 {report['planned']} 份、计划隔离 {report['quarantined']['planned']} 份）")
    print(f"哨兵告警：{len(report['sentinel_alerts'])} 条")
    print(f"源表目录指纹一致={fp_before == fp_after}；归档区前后：{archive_before} → {archive_after}；"
          f"隔离区前后：{quarantine_before} → {quarantine_after}")

    ck.check(fp_before == fp_after, "真实源表目录的文件名 + mtime + 大小指纹零改动")
    ck.check(root_before == root_after, "数据源根目录顶层条目零改动")
    ck.check(not archive_after and not archive_before, "dry-run 未生成归档区")
    ck.check(not quarantine_after and not quarantine_before, "dry-run 未生成隔离区")
    ck.check(report["moved"] == 0 and report["quarantined"]["moved"] == 0, "dry-run 搬动数为 0")
    ck.check(not organize._manifest_path(root, BATCH).exists()
             and not organize._quarantine_manifest_path(root).exists(), "dry-run 未写任何清单")
    return ck.report()


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    p = argparse.ArgumentParser(
        prog="verify_organize_quarantine.py",
        description="工单 04 验证：整理器隔离搬移 + 哨兵告警（只报警不阻断）。",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("selftest", help="在临时目录里跑证据 1–8")
    t.set_defaults(func=cmd_selftest)
    r = sub.add_parser("realdryrun", help="真实数据只读 dry-run（零副作用）")
    r.add_argument("source", nargs="?", default="ResourceData", help="真实数据源根目录")
    r.set_defaults(func=cmd_realdryrun)
    return p


def main(argv: list[str] | None = None) -> int:
    """脚本入口：解析子命令并返回退出码。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
