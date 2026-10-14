#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工单 05 的验证：整理器的「五段报告 + 断档检测 + 60 天回收站清理」。

为什么需要它：
    工单 05 之前，整理器只把**结构化事实**算进 `organize_files()` 的返回值（工单 03/04 的
    产物），没有人读报告、也没有清理。这一步补上两层：
      · **五段报告**（归档 / 隔离 / 断档 / 哨兵告警 / 回收站清理）——人读 md + 机读 json，
        两者**内容一致**。它是无人值守运行里唯一的「事后可追溯」载体（ADR-0002）；
      · **断档检测**——日期轴上没人覆盖的区间被列出，且**零动作**（⛔ 不补下载、不搬文件）；
      · **60 天回收站清理**——归档批次满 60 天（按归档日期起算）逐个送进系统回收站
        （⛔ 不是硬删，ADR-0006），回收站不可用时降级为「只报告」。

    🔴 两条最容易被写假的验收项，本脚本用**可断言的形式**钉死：
      · 「五段齐全」== 报告文档里 `sections` 是**恰好 5 项的结构化列表**，每段关键词互不相同；
        ⛔ 不是把五段拼成一整块字符串——拼串时删掉第 2 段、首尾关键词仍能 grep 到，断言照样绿。
      · 「md 与 json 内容一致」== `md 磁盘字节 == render_report(json.loads(磁盘 json))`，
        ⛔ 不靠肉眼。

    🔴 「回收站不可用」用**注入恒抛异常的假回收站**验证降级路径——⛔ 不真去造一个坏卷；
      并断言文件**一个都没删**、批处理照常完成。

覆盖工单 05 的 6 项验收（全部在临时目录的源根副本上执行）：
    1. 五段报告齐全、顺序固定、人读 md + 机读 json 内容一致
    2. 断档：已知缺口被列出，且**零动作**（文件没搬、决策没改）
    3. 60 天边界：59 天保留 / 60、61 天进回收站（口径 `>= 60`）
    4. 回收站不可用 → 降级为「只在报告里列出」、⛔ 不静默失败、⛔ 不硬删、⛔ 不中断
    5. 报告提示回收站容量驱逐风险
    6. 隔离区不被清理（靠 `iter_archive_batches` 的结构性保证 + 断言论证）
    另覆盖：正向回收站调用路径、整批 write-ahead、报告落在 `report-` 前缀（不覆盖 02 材料）、
    真实目录只读 dry-run 指纹零改动。

风格：沿用 `verify_organize_quarantine.py`——独立脚本 + 复用 `verify_organize.Checker` +
sys.exit(1)。⛔ 不引入 pytest。⛔ 不在真实 `ResourceData` 上执行任何搬移（除 `realdryrun`
子命令的只读 dry-run，且报告写到临时目录）。

用法（必须用项目 venv 的解释器）：
    .venv/Scripts/python.exe backend/jobs/verify_organize_report.py selftest
    .venv/Scripts/python.exe backend/jobs/verify_organize_report.py realdryrun ResourceData
    .venv/Scripts/python.exe backend/jobs/verify_organize_report.py recyclebin   # 真实回收站探针
"""
from __future__ import annotations

import argparse
import gc
import json
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# 把仓库根目录塞进 sys.path：直接 `python backend/jobs/verify_organize_report.py` 也能导入 backend 包
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.jobs import config  # noqa: E402
from backend.jobs import organize  # noqa: E402
from backend.jobs import verify_organize as vo  # noqa: E402
from backend.jobs import verify_organize_decision as vod  # noqa: E402

TABLE_DIR_NAME = config.POP_TABLE_DIR_NAME

# 固定批次时间：让报告文件名与归档子目录名可预期（不依赖「今天」）。
BATCH_NOW = datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc)
BATCH = "2026-10-12"

# full 夹具的固定决策数（工单 02 已固化：keep12 / archive5 / quarantine2 / skip1）。
EXPECT_KEEP = 12
EXPECT_ARCHIVE = 5
EXPECT_QUARANTINE_FULL = 2
EXPECT_SKIP = 1

# full 夹具里已知的断档（工单 01 的 FIXTURE_GAPS 固化）：卡求旗舰店推广缺 01-11..01-19。
KNOWN_GAP = {"shop": "卡求旗舰店", "kind": organize.KIND_PROMO,
             "start": "2025-01-11", "end": "2025-01-19", "days": 9}


def _fresh_copy(base: Path, dest: Path) -> Path:
    """把夹具母本整棵拷一份到 dest（每个验收项各用独立副本，互不污染）。"""
    shutil.copytree(base, dest)
    return dest


def _table_dir(root: Path) -> Path:
    return root / TABLE_DIR_NAME


def _report_dir(root: Path) -> Path:
    """报告默认目录：源根同级 `logs/organize/`（生产 = 项目根 logs/organize）。"""
    return organize._default_report_dir(root)


def _read_report(root: Path) -> tuple[str, dict, Path, Path]:
    """读回磁盘上的 md + 机读 json，返回 (md 文本, json 字典, md 路径, json 路径)。"""
    d = _report_dir(root)
    md_path = d / f"report-{BATCH}.md"
    json_path = d / f"report-{BATCH}.json"
    md = md_path.read_text(encoding="utf-8")
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    return md, doc, md_path, json_path


def _seed_table(root: Path) -> None:
    """给只测清理/报告的源根造一个空的「数据源表目录」（organize_files 要求它存在）。"""
    (root / TABLE_DIR_NAME).mkdir(parents=True, exist_ok=True)


def _make_batch(root: Path, batch_day: date, files=("归档样本.xlsx",)) -> Path:
    """在归档区造一个批次目录，里面放几份「数据文件」+ 一份 manifest.json。"""
    bd = root / organize.ARCHIVE_DIR_NAME / batch_day.isoformat()
    bd.mkdir(parents=True, exist_ok=True)
    for name in files:
        (bd / name).write_text("archived-bytes", encoding="utf-8")
    (bd / organize.MANIFEST_NAME).write_text(
        json.dumps({"batch": batch_day.isoformat(), "entries": []}, ensure_ascii=False),
        encoding="utf-8")
    return bd


def _recording_bin(calls: list):
    """造一个「假回收站」：记录调用、把文件从原位置删掉（模拟进回收站），返回该函数。"""
    def _bin(p: Path) -> None:
        calls.append(Path(p).name)
        Path(p).unlink()   # 送进回收站 = 原位置不再有它（但可还原）
    return _bin


def _raising_bin(_p: Path) -> None:
    """造一个「恒抛异常」的假回收站，模拟网络卷 / 回收站不可用。"""
    raise OSError("simulated: 该卷没有回收站（网络驱动器 / API 失败）")


# ==========================================================================
# 证据 1 + 2 + 3：五段报告齐全 / md 与 json 一致 / 结构可断言
# ==========================================================================

def evidence_report_five_sections(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """真跑一次 on（在临时副本上），检查五段报告齐全、顺序固定、md 与 json 逐字节一致。"""
    src = _fresh_copy(base, tmp / "report_on")
    calls: list = []
    report = organize.organize_files(src, mode=organize.MODE_ON, now=BATCH_NOW,
                                     recycle=_recording_bin(calls))
    md, doc, md_path, json_path = _read_report(src)
    sections = doc["sections"]
    keys = [s["key"] for s in sections]
    keywords = [s["keyword"] for s in sections]

    print("--- 证据 1/2/3：五段报告 ---")
    print(f"报告 md  ：{md_path}")
    print(f"报告 json：{json_path}")
    print(f"段 keys  ：{keys}")
    print(f"段关键词 ：{keywords}")
    print("--- 渲染片段（完整 md）---")
    print(md)
    print("--- 渲染片段结束 ---")

    # ① 报告文件落在 report- 前缀下，⛔ 不覆盖工单 02 的人工过目材料
    ck.check(md_path.is_file() and json_path.is_file(), "人读 md + 机读 json 都已落盘")
    ck.check(md_path.name == f"report-{BATCH}.md" and json_path.name == f"report-{BATCH}.json"
             and md_path.name != f"{BATCH}.md" and json_path.name != f"decisions-{BATCH}.json",
             "报告用 report- 前缀，⛔ 不覆盖 02 的 {日期}.md / decisions-{日期}.json")

    # ② 五段齐全、顺序固定（结构化列表，⛔ 不是一整块字符串）
    ck.check(isinstance(sections, list) and len(sections) == 5,
             f"报告恰好 5 段（实际 {len(sections)}）", str(keys))
    ck.check(tuple(keys) == organize.REPORT_SECTION_KEYS,
             "五段顺序固定 = 归档 / 隔离 / 断档 / 哨兵告警 / 回收站清理", str(keys))

    # ③ 每段关键词**互不相同**（5 个段 = 5 个独立关键词）
    ck.check(len(set(keywords)) == 5, "五段关键词互不相同", str(keywords))
    # 每个关键词都真的出现在渲染出来的 md 里（否则「段被渲染」就是假的）
    missing_kw = [k for k in keywords if k not in md]
    ck.check(not missing_kw, "每段关键词都出现在渲染的 md 里", str(missing_kw))

    # ④ 🔴 结构敏感性：删掉第 2 段后，md 段数变 4、且「隔离区」关键词消失。
    #     若五段是被拼成一整块字符串，这个断言就会失效（首尾关键词仍能 grep 到）。
    doc_no2 = dict(doc)
    doc_no2["sections"] = [s for s in sections if s["key"] != "quarantine"]
    md_no2 = organize.render_report(doc_no2)
    ck.check(md_no2.count("## [") == 4 and "隔离区" not in md_no2 and md_no2 != md,
             "🔴 删掉「隔离」段后：md 段数 5→4、隔离区关键词消失（证明是结构化分段，不是拼串）",
             f"段数={md_no2.count('## [')}")

    # ⑤ 🔴 md 与 json 内容一致：磁盘 md 逐字节 == render(json.loads(磁盘 json))
    ck.check(md == organize.render_report(json.loads(json_path.read_text(encoding="utf-8"))),
             "🔴 磁盘 md 逐字节 == render_report(json.loads(磁盘 json))（内容一致，非肉眼）")

    # ⑥ 归档 / 隔离两段带上了真实搬动事实
    ck.check(report["moved"] == EXPECT_ARCHIVE and report["quarantined"]["moved"] == EXPECT_QUARANTINE_FULL,
             f"on 模式真搬：归档 {EXPECT_ARCHIVE} 份 + 隔离 {EXPECT_QUARANTINE_FULL} 份",
             f"archive={report['moved']} quarantine={report['quarantined']['moved']}")
    ck.check(calls == [], "本批次今天刚归档、不可能超期 → 清理不调用回收站（与归档互不干扰）")


# ==========================================================================
# 证据 4：断档（已知缺口 + 零动作）
# ==========================================================================

def evidence_gaps_pure(ck: vo.Checker) -> None:
    """断档纯函数：手算缺口必须与 find_gaps 完全一致（不靠夹具、不靠肉眼）。"""
    files = {"a.csv": {"shop": "S", "kind": "promo",
                       "keys": {("S", "2025-01-01", "1"), ("S", "2025-01-03", "1"),
                                ("S", "2025-01-09", "1")}}}
    decisions = [{"filename": "a.csv", "shop": "S", "kind": "promo", "decision": organize.KEEP}]
    got = organize.find_gaps(files, decisions)
    want = [{"shop": "S", "kind": "promo", "start": "2025-01-02", "end": "2025-01-02", "days": 1},
            {"shop": "S", "kind": "promo", "start": "2025-01-04", "end": "2025-01-08", "days": 5}]
    print("--- 证据 4a：断档纯函数手算 ---")
    print(f"有 01-01/03/09 三个日期 → 期望缺 01-02 与 01-04..01-08；实得 {got}")
    ck.check(got == want, "断档区间与手算一致（含单日 01-02 与区间 01-04..01-08）", str(got))

    # 被隔离 / 被跳过文件不参与断档（它们不在保留集里）
    files2 = {"q.xlsx": {"shop": "S", "kind": "detail", "keys": set()}}
    ck.check(organize.find_gaps(files2, [{"filename": "q.xlsx", "shop": "S", "kind": "detail",
                                          "decision": organize.QUARANTINE}]) == [],
             "被隔离文件（不在保留集）不产生断档")


def evidence_gaps_report_and_zero_action(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """完整夹具的 dry-run 报告里必须列出已知断档；且断档检测**零动作**。"""
    src = _fresh_copy(base, tmp / "gaps")
    table = _table_dir(src)
    fp_before = vod._fingerprint(table)
    decisions_before = organize.derive_decisions(src)
    names_before = sorted(p.name for p in table.iterdir())
    root_before = sorted(p.name for p in src.iterdir())

    report = organize.organize_files(src, mode=organize.MODE_DRY_RUN, now=BATCH_NOW)

    fp_after = vod._fingerprint(table)
    decisions_after = organize.derive_decisions(src)
    names_after = sorted(p.name for p in table.iterdir())
    root_after = sorted(p.name for p in src.iterdir())
    gaps = report["gaps"]

    print("--- 证据 4b：断档进报告 + 零动作 ---")
    for g in gaps:
        print(f"  断档：{g['shop']} / {g['kind']}：{g['start']} ~ {g['end']}（{g['days']} 天）")

    ck.check(any(g == KNOWN_GAP for g in gaps),
             f"完整夹具里已知断档被列出：{KNOWN_GAP['shop']}推广 {KNOWN_GAP['start']}~{KNOWN_GAP['end']}",
             str(gaps))
    ck.check(report["moved"] == 0 and report["planned"] == EXPECT_ARCHIVE
             and report["cleanup"]["recycled"] == 0,
             "dry-run 下：归档 0 搬动、清理 0 进回收站")
    ck.check(fp_before == fp_after and names_before == names_after,
             "断档检测零动作：源表目录指纹 + 文件名清单零改动")
    ck.check(root_before == root_after, "数据源根目录顶层条目零改动（断档没有偷偷补下载）")
    ck.check(decisions_before == decisions_after, "断档检测不改任何决策（决策清单前后逐条相同）")


# ==========================================================================
# 证据 5：60 天边界（59 保留 / 60、61 进回收站）
# ==========================================================================

def evidence_retention_boundary(tmp: Path, ck: vo.Checker) -> None:
    """造 59/60/61 天三个批次，验证边界口径是 `>= 60`（不是 `> 60`）。"""
    src = tmp / "retention"
    _seed_table(src)
    today = BATCH_NOW.date()
    ages = {59: today - timedelta(days=59), 60: today - timedelta(days=60),
            61: today - timedelta(days=61)}
    for age, day in ages.items():
        _make_batch(src, day, files=(f"归档_{age}天.xlsx",))

    calls: list = []
    ledger_first_status: dict = {}

    def spy_bin(p: Path) -> None:
        # 第一个进回收站的时刻：账本里整批应已 planned 落盘（write-ahead）
        if not ledger_first_status:
            man = organize.read_manifest(src / organize.ARCHIVE_DIR_NAME / organize.CLEANUP_MANIFEST_NAME)
            ledger_first_status["statuses"] = [e["status"] for e in (man or {}).get("entries", [])]
        calls.append(Path(p).name)
        Path(p).unlink()

    sec = organize.cleanup_expired(src, now=BATCH_NOW, recycle=spy_bin, dry_run=False)

    print("--- 证据 5：60 天边界（口径 age_days >= 60）---")
    print(f"批次（59/60/61 天）：{[ages[a].isoformat() for a in (59, 60, 61)]}")
    print(f"超期批次 = {sec['batches']}；候选 {sec['candidates']}；进回收站 {sec['recycled']}；"
          f"失败 {sec['failed']}")
    print(f"实际进回收站的：{calls}")
    print(f"账本首写状态：{ledger_first_status.get('statuses')}")

    ck.check(sec["batches"] == sorted([ages[60].isoformat(), ages[61].isoformat()]),
             "超期批次 = 60 天 + 61 天两个（59 天不算超期）", str(sec["batches"]))
    ck.check(sec["candidates"] == 2 and sec["recycled"] == 2 and sec["failed"] == 0,
             "候选 2、进回收站 2、失败 0", str({k: sec[k] for k in ('candidates', 'recycled', 'failed')}))
    ck.check(set(calls) == {"归档_60天.xlsx", "归档_61天.xlsx"},
             "进回收站的是 60 天与 61 天两批，59 天那份未动", str(calls))
    ck.check((src / organize.ARCHIVE_DIR_NAME / ages[59].isoformat() / "归档_59天.xlsx").is_file(),
             "59 天批次的文件保留在原归档目录")
    ck.check(not (src / organize.ARCHIVE_DIR_NAME / ages[60].isoformat() / "归档_60天.xlsx").exists()
             and not (src / organize.ARCHIVE_DIR_NAME / ages[61].isoformat() / "归档_61天.xlsx").exists(),
             "60 / 61 天批次的文件已离开归档目录（进了回收站）")
    ck.check(ledger_first_status.get("statuses", []) == [organize.STATUS_PLANNED] * 2,
             "write-ahead：第一个文件进回收站前，整批候选已 planned 落盘",
             str(ledger_first_status))
    ledger = organize.read_manifest(src / organize.ARCHIVE_DIR_NAME / organize.CLEANUP_MANIFEST_NAME)
    ck.check(ledger is not None and all(e["status"] == organize.STATUS_RECYCLED
                                        for e in ledger["entries"]),
             "清理账本记录了每个条目的最终状态 recycled", str(ledger))
    ck.check(not sec["degraded"], "回收站可用时 degraded=False")


# ==========================================================================
# 证据 6：回收站不可用 → 降级（只在报告里列出 / 不硬删 / 不中断）
# ==========================================================================

def evidence_degrade(tmp: Path, ck: vo.Checker) -> None:
    """注入恒抛异常的假回收站 → 报告出现降级说明、文件一个都没删、批处理照常完成。"""
    src = tmp / "degrade"
    _seed_table(src)
    old = BATCH_NOW.date() - timedelta(days=90)
    bd = _make_batch(src, old, files=("旧归档A.xlsx", "旧归档B.xlsx"))

    # ① 直接调 cleanup_expired：降级 + 文件原样
    sec = organize.cleanup_expired(src, now=BATCH_NOW, recycle=_raising_bin, dry_run=False)

    # ② 整条 organize_files 路径：报告里必须出现降级说明，且函数正常返回（不中断）
    src2 = tmp / "degrade_full"
    _seed_table(src2)
    _make_batch(src2, old, files=("旧归档C.xlsx",))
    calls_before = sorted(p.name for p in (src2 / organize.ARCHIVE_DIR_NAME / old.isoformat()).iterdir())
    report = organize.organize_files(src2, mode=organize.MODE_ON, now=BATCH_NOW,
                                     recycle=_raising_bin)
    md, doc, _, _ = _read_report(src2)
    calls_after = sorted(p.name for p in (src2 / organize.ARCHIVE_DIR_NAME / old.isoformat()).iterdir())

    print("--- 证据 6：回收站不可用 → 降级 ---")
    print(f"cleanup_expired：{ {k: sec[k] for k in ('candidates','recycled','failed','degraded')} }")
    print(f"organize_files：正常返回，cleanup.degraded={report['cleanup']['degraded']}")
    print(f"报告里是否含降级说明「{organize.CLEANUP_DEGRADED_NOTE}」："
          f"{organize.CLEANUP_DEGRADED_NOTE in md}")
    print(f"旧批次目录内容前后：{calls_before} → {calls_after}")

    ck.check(sec["degraded"] is True and sec["recycled"] == 0 and sec["failed"] == 2,
             "回收站不可用 → degraded=True、0 进回收站、2 项 deferred",
             str({k: sec[k] for k in ('recycled', 'failed', 'degraded')}))
    ck.check(all(e["status"] == organize.STATUS_DEFERRED for e in sec["entries"]),
             "所有待清理项记为 deferred（只在报告里列出）", str(sec["entries"]))
    ck.check((bd / "旧归档A.xlsx").is_file() and (bd / "旧归档B.xlsx").is_file(),
             "⛔ 降级时一个文件都没删（不硬删）")
    ck.check(report["cleanup"]["degraded"] is True
             and organize.CLEANUP_DEGRADED_NOTE in md,
             "报告里显式出现一行「降级：回收站不可用，本次仅报告」")
    ck.check(calls_before == calls_after, "降级时归档批次目录内容不变（文件仍在原地）")
    ck.check(report["moved"] == 0 and report["cleanup"]["recycled"] == 0,
             "降级不中断批处理：organize_files 正常返回、未误搬文件")


# ==========================================================================
# 证据 7：回收站容量驱逐风险提示
# ==========================================================================

def evidence_risk_note(tmp: Path, ck: vo.Checker) -> None:
    """报告第五段必须提示「回收站容量上限 → 可能挤出无关文件」（spec §Further Notes）。"""
    src = tmp / "risk"
    _seed_table(src)
    _make_batch(src, BATCH_NOW.date() - timedelta(days=100), files=("老.xlsx",))
    calls: list = []
    report = organize.organize_files(src, mode=organize.MODE_ON, now=BATCH_NOW,
                                     recycle=_recording_bin(calls))
    md, doc, _, _ = _read_report(src)
    cleanup_sec = next(s for s in doc["sections"] if s["key"] == "cleanup")

    print("--- 证据 7：回收站容量驱逐风险提示 ---")
    print(f"risk_note：{report['cleanup']['risk_note']}")

    ck.check(organize.RECYCLE_BIN_RISK_NOTE in md
             and any("容量上限" in ln or "永久删除" in ln for ln in cleanup_sec["lines"]),
             "报告「回收站清理」段含容量驱逐风险提示（超出上限会永久删除最旧条目）",
             str(cleanup_sec["lines"])[:300])
    ck.check("回收站" in cleanup_sec["keyword"], "风险提示落在「回收站清理」段下")


# ==========================================================================
# 证据 8：隔离区不被清理（结构性保证 + 断言论证）
# ==========================================================================

def evidence_quarantine_not_cleaned(tmp: Path, ck: vo.Checker) -> None:
    """清理只扫 `_archive/*`；`_quarantine/` 里的文件清理后**仍在原地**。"""
    src = tmp / "quarantine_scope"
    _seed_table(src)
    old = BATCH_NOW.date() - timedelta(days=90)
    _make_batch(src, old, files=("该清的.xlsx",))
    qdir = src / organize.QUARANTINE_DIR_NAME
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "等人处理的.xlsx").write_text("quarantined", encoding="utf-8")

    batches = organize.iter_archive_batches(src)
    names = [p.name for p in batches]
    plan = organize.plan_cleanup(src, now=BATCH_NOW)
    calls: list = []
    sec = organize.cleanup_expired(src, now=BATCH_NOW, recycle=_recording_bin(calls), dry_run=False)

    print("--- 证据 8：隔离区不被清理 ---")
    print(f"iter_archive_batches 只枚举到：{names}（隔离区目录名={organize.QUARANTINE_DIR_NAME}）")
    print(f"清理候选：{[p['source_rel'] for p in plan]}")
    print(f"隔离区文件清理后仍在：{(qdir / '等人处理的.xlsx').is_file()}")

    ck.check(names == [old.isoformat()],
             "清理候选只含 _archive/ 下的批次目录（隔离区不在其中）", str(names))
    ck.check(organize.QUARANTINE_DIR_NAME not in names
             and not any(organize.QUARANTINE_DIR_NAME in p.parts for p in batches),
             "⛔ 隔离区永不入清理候选（结构上扫不到 _quarantine/）", str(names))
    ck.check(all(not p["source_rel"].startswith(organize.QUARANTINE_DIR_NAME) for p in plan),
             "清理计划里没有隔离区文件", str(plan))
    ck.check(sec["recycled"] == 1 and calls == ["该清的.xlsx"],
             "只清理了归档批次里的文件（1 份），回收站未被隔离区文件调用", str(calls))
    ck.check((qdir / "等人处理的.xlsx").is_file(),
             "🔴 清理跑完后 _quarantine/ 里的文件仍在原地", str(sorted(p.name for p in qdir.iterdir())))


# ==========================================================================
# selftest
# ==========================================================================

def cmd_selftest(_args) -> int:
    """在临时目录里把证据 1–8 全跑一遍（每项用独立源根副本）。"""
    ck = vo.Checker("工单 05 自证：五段报告 + 断档 + 60 天回收站清理")
    tmp = Path(tempfile.mkdtemp(prefix="verify_organize_report_"))
    try:
        base = tmp / "base"
        inv = vo.make_fixtures(base, clean=False)   # full 夹具：含形态异常 + 断档 + 哨兵告警
        ck.check(not vo.verify_fixture_oracle(base, inv), "full 夹具的 oracle 自检通过（底座可信）")

        evidence_report_five_sections(base, tmp, ck)
        evidence_gaps_pure(ck)
        evidence_gaps_report_and_zero_action(base, tmp, ck)
        evidence_retention_boundary(tmp, ck)
        evidence_degrade(tmp, ck)
        evidence_risk_note(tmp, ck)
        evidence_quarantine_not_cleaned(tmp, ck)

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
    """在真实源表目录上只读 dry-run：产出五段报告并断言指纹零改动（⛔ 不搬任何文件）。

    报告落在**默认目录**（`logs/organize/report-{批次}.md` / `.json`，`logs/` 已被 git 忽略），
    这正是交付物要的路径；同时对 `ResourceData` 只读——文件名 / mtime / 大小指纹零改动、
    不生成 `_archive` / `_quarantine`、不写清单。并断言**不覆盖**工单 02 的人工过目材料
    （`logs/organize/2026-10-12.md` 与 `decisions-2026-10-12.json` 字节不变）。
    """
    ck = vo.Checker("工单 05 realdryrun：真实数据只读 dry-run（零副作用）")
    root = Path(args.source).expanduser().resolve()
    table = _table_dir(root)
    ck.check(table.is_dir(), f"数据源表目录存在：{table}")
    if not table.is_dir():
        return ck.report()

    # 工单 02 的人工过目材料：本工单⛔ 不得覆盖（用 report- 前缀区分）
    protected = [ROOT / "logs" / "organize" / "2026-10-12.md",
                 ROOT / "logs" / "organize" / "decisions-2026-10-12.json"]
    protected_before = {p: p.read_bytes() for p in protected if p.is_file()}

    root_before = sorted(p.name for p in root.glob("*"))
    fp_before = vod._fingerprint(table)
    archive_before = (root / organize.ARCHIVE_DIR_NAME).exists()
    quarantine_before = (root / organize.QUARANTINE_DIR_NAME).exists()

    report = organize.organize_files(root, mode=organize.MODE_DRY_RUN, now=BATCH_NOW)

    fp_after = vod._fingerprint(table)
    root_after = sorted(p.name for p in root.glob("*"))
    archive_after = (root / organize.ARCHIVE_DIR_NAME).exists()
    quarantine_after = (root / organize.QUARANTINE_DIR_NAME).exists()
    protected_after = {p: p.read_bytes() for p in protected if p.is_file()}
    c = report["counts"]
    md_text = Path(report["report_md"]).read_text(encoding="utf-8")
    doc = json.loads(Path(report["report_json"]).read_text(encoding="utf-8"))

    print(f"真实源表目录：{table}（{len(fp_before)} 个条目）")
    print(f"决策统计：keep {c[organize.KEEP]} / archive {c[organize.ARCHIVE]} / "
          f"quarantine {c[organize.QUARANTINE]} / skip {c[organize.SKIP]}"
          f"（计划归档 {report['planned']} 份、计划隔离 {report['quarantined']['planned']} 份）")
    print(f"断档：{len(report['gaps'])} 段；哨兵告警：{len(report['sentinel_alerts'])} 条；"
          f"回收站清理候选：{report['cleanup']['candidates']} 份")
    print(f"报告 md  ：{report['report_md']}")
    print(f"报告 json：{report['report_json']}")
    print(f"五段 keys：{[s['key'] for s in doc['sections']]}；md 段数={md_text.count('## [')}")
    print(f"md 与 json 一致："
          f"{md_text == organize.render_report(json.loads(Path(report['report_json']).read_text(encoding='utf-8')))}")
    print("--- 渲染片段（前 26 行）---")
    print("\n".join(md_text.splitlines()[:26]))
    print(f"ResourceData 顶层：{root_after}")

    ck.check(fp_before == fp_after, "真实源表目录的文件名 + mtime + 大小指纹零改动")
    ck.check(root_before == root_after, "数据源根目录顶层条目零改动（未生成 _archive/_quarantine）")
    ck.check(not archive_after and not archive_before, "dry-run 未生成归档区")
    ck.check(not quarantine_after and not quarantine_before, "dry-run 未生成隔离区")
    ck.check(report["moved"] == 0 and report["quarantined"]["moved"] == 0
             and report["cleanup"]["recycled"] == 0, "dry-run 搬动 / 清理数为 0")
    ck.check(not organize._manifest_path(root, BATCH).exists()
             and not organize._quarantine_manifest_path(root).exists(),
             "dry-run 未写任何清单")
    ck.check(len(doc["sections"]) == 5 and md_text.count("## [") == 5
             and md_text == organize.render_report(json.loads(
                 Path(report["report_json"]).read_text(encoding="utf-8"))),
             "真实数据上也产出了五段报告（恰好 5 段、md 与 json 一致）")
    ck.check(protected_before == protected_after
             and Path(report["report_md"]).name == f"report-{BATCH}.md",
             "⛔ 未覆盖工单 02 的人工过目材料（{日期}.md / decisions-{日期}.json 字节不变）")
    return ck.report()


# ==========================================================================
# recyclebin：真实回收站探针（把系统临时目录里的一个自造文件送进回收站）
# ==========================================================================

def cmd_recyclebin(_args) -> int:
    """真实调用一次 `to_recycle_bin`：证明生产用的回收站路径确实可用（不是只有假回收站在跑）。

    ⚠️ 会在系统临时目录造一个一次性文件并把它送进**回收站**（可从回收站还原），
    ⛔ 不会硬删、也不碰项目文件。
    """
    ck = vo.Checker("工单 05 探针：真实回收站 API（SHFileOperationW + FOF_ALLOWUNDO）")
    tmp = Path(tempfile.mkdtemp(prefix="verify_org_recycle_probe_"))
    victim = tmp / "_probe_to_recycle_bin.txt"
    victim.write_text("一次性回收站探针，可从回收站还原", encoding="utf-8")
    print(f"探针文件：{victim}（存在={victim.is_file()}）")

    ok = True
    err = ""
    try:
        organize.to_recycle_bin(victim)
    except Exception as e:  # noqa: BLE001 —— 探针把异常转成断言
        ok = False
        err = f"{type(e).__name__}: {e}"

    print(f"调用结果：{'成功' if ok else '失败'} {err}")
    ck.check(ok, "to_recycle_bin 真实调用未抛异常", err)
    ck.check(not victim.exists(), "文件已离开原位置（= 进了回收站，可从回收站还原）")
    gc.collect()
    shutil.rmtree(tmp, ignore_errors=True)
    return ck.report()


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    p = argparse.ArgumentParser(
        prog="verify_organize_report.py",
        description="工单 05 验证：五段报告 + 断档 + 60 天回收站清理。",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("selftest", help="在临时目录里跑证据 1–8")
    t.set_defaults(func=cmd_selftest)
    r = sub.add_parser("realdryrun", help="真实数据只读 dry-run（零副作用）")
    r.add_argument("source", nargs="?", default="ResourceData", help="真实数据源根目录")
    r.set_defaults(func=cmd_realdryrun)
    b = sub.add_parser("recyclebin", help="真实回收站探针（送一个系统临时文件进回收站）")
    b.set_defaults(func=cmd_recyclebin)
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
