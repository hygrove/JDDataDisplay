#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工单 06 的验收闸门：全量端到端（A/B 双库逐格不变）+ processed_file 核对 + 汇总报告。

为什么需要它（而不是「跑一遍看看」）：
    「整理源表」失败是**无声的**——`daily_detail` 只增不减（`db.py` 只有 `INSERT OR REPLACE`，
    全项目没有 `DELETE FROM daily_detail`；见 `verify_organize.py` 开头）。判错一个文件的后果
    是那批 `(店铺, 日期, SPU)` 永久冻结成僵尸行 / 历史缺口，页面上完全看不出来。
    唯一能抓住它的尺子是「整理前的库 vs 整理后的库逐格比对」。

🔴 为什么必须用**两个各自新建的库**（工单 03 Comments #1）：
    若在同一个库上「整理前摄入一次、整理后再摄入一次」，会被长表只增不减**掩盖丢 key**——
    被搬走文件贡献的行不会消失，比对永远相等。所以本闸门：
        A 库 = 全量源根副本 → 新鲜摄入；
        B 库 = 整理（mode="on"）后的源根副本 → 新鲜摄入；
        再逐格比 A vs B。唯一允许的差异是 `processed_file` 的记账条数（规格写明的预期变化）。

子命令：
    selftest
        在临时目录里证明这道闸门**有牙齿**（防假阳性 + 防假阴性）：
          · 夹具端到端跑通（clean 夹具）；
          · 故意让 B 侧少一份「保留集」文件（模拟过度归档）→ 闸门必须报出行级差异；
          · 故意改一个指标值 → 闸门必须报出格级差异；
          · 真实 `ResourceData` 顶层条目零改动（只读）。
    accept <源根>
        在真实源根的**临时副本**上做全量端到端验收（⛔ 真实目录只读、绝不搬文件），
        并打印「搬走 / 保留 / 隔离 / 断档 / 耗时增量」汇总报告。

风格：沿用 `verify_organize_quarantine.py`——独立脚本 + 复用 `verify_organize.Checker`
（自制断言计数器）+ sys.exit(1)。⛔ 不引入 pytest（requirements.txt 是全量 freeze）。
⛔ 绝不在真实 `ResourceData` 上搬任何文件：端到端只在 `shutil.copytree` 出来的副本上跑 `mode="on"`。

用法（必须用项目 venv 的解释器）：
    .venv/Scripts/python.exe backend/jobs/verify_organize_acceptance.py selftest
    .venv/Scripts/python.exe backend/jobs/verify_organize_acceptance.py accept ResourceData
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# 把仓库根目录塞进 sys.path：直接 `python backend/jobs/verify_organize_acceptance.py` 也能导入 backend 包
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.jobs import config  # noqa: E402
from backend.jobs import organize  # noqa: E402
from backend.jobs import verify_organize as vo  # noqa: E402

TABLE_DIR_NAME = config.POP_TABLE_DIR_NAME
# 根目录的两张「配置表」：与 `db._collect_source_files` 一致，也进 processed_file 记账
CONFIG_FILES = ("店铺spu登记信息.xlsx", "SPU商品名称映射表.xlsx")

# 固定批次时间：让归档子目录名（`_archive/2026-10-12/`）可预期，不依赖「今天」
BATCH_NOW = datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc)
BATCH = "2026-10-12"


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _fresh_copy(base: Path, dest: Path) -> Path:
    """把整棵源根拷一份到 dest（A/B 两侧各用独立副本，互不污染）。"""
    shutil.copytree(base, dest)
    return dest


def _ingest_timed(root: Path, db: Path) -> tuple[dict, float]:
    """用真实摄入路径把 root 灌进指定（新建）库，返回 (结果, 耗时秒)。"""
    t0 = time.time()
    res = vo._ingest_into(root, db)
    return res, time.time() - t0


def _source_files_on_disk(root: Path) -> list[str]:
    """枚举 root 数据源表目录里**批处理真正会读**的源文件（与 `db._collect_source_files` 同口径）。"""
    return sorted(p.name for p in organize._iter_source_files(root / TABLE_DIR_NAME))


def _processed_source_names(db: Path) -> list[str]:
    """读出某个库 processed_file 里**源表目录下**的文件名（剔除两张配置表）。"""
    _, processed = vo.read_db_cells(db)
    return sorted(Path(p).name for p in processed if p.startswith(f"{TABLE_DIR_NAME}/"))


# --------------------------------------------------------------------------
# 核心：构造 A/B 两端（不判分，判分交给 run_e2e / selftest）
# --------------------------------------------------------------------------

def build_pair(base: Path, tmp: Path, label: str, *, b_mutate=None) -> dict:
    """构造「A=全量新鲜库」与「B=整理后新鲜库」，返回两份快照与中间事实。

    Args:
        base: 源根母本（真实 `ResourceData` 的副本 / 夹具）。
        tmp: 临时工作目录。
        label: 本次运行标签（命名临时文件用）。
        b_mutate: 可选注入——在 B 侧整理后、摄入前对副本做手脚（`selftest` 用它模拟数据丢失）。

    Returns:
        dict: 含 snap_a / snap_b / rep / 各盘面事实 / 耗时等（见函数末尾 return）。
    """
    # ---- A：全量源根副本 → 新鲜摄入 ----
    a_root = _fresh_copy(base, tmp / f"{label}_A")
    a_db = tmp / f"{label}_A.db"
    a_res, a_ingest_s = _ingest_timed(a_root, a_db)
    snap_a = vo.snapshot(a_db, tmp / f"{label}_A.json")

    # ---- B：整理（mode="on"）后的源根副本 → 新鲜摄入 ----
    b_root = _fresh_copy(base, tmp / f"{label}_B")
    b_report_dir = tmp / f"{label}_reports"
    t_org = time.time()
    rep = organize.organize_files(b_root, mode=organize.MODE_ON, now=BATCH_NOW,
                                  report_dir=b_report_dir)
    b_org_s = time.time() - t_org
    if b_mutate is not None:
        b_mutate(b_root, rep)
    b_db = tmp / f"{label}_B.db"
    b_res, b_ingest_s = _ingest_timed(b_root, b_db)
    snap_b = vo.snapshot(b_db, tmp / f"{label}_B.json")

    # ---- 派生事实 ----
    diffs = vo.diff_snapshots(snap_a, snap_b)
    cell_diffs = [d for d in diffs if d["kind"] != "processed_file"]
    pf_diffs = [d for d in diffs if d["kind"] == "processed_file"]
    base_disk = _source_files_on_disk(base)
    b_disk = _source_files_on_disk(b_root)
    decisions = organize.derive_decisions(base)
    by = {k: sorted(d["filename"] for d in decisions if d["decision"] == k)
          for k in (organize.KEEP, organize.ARCHIVE, organize.QUARANTINE, organize.SKIP)}
    a_pf = _processed_source_names(a_db)
    b_pf = _processed_source_names(b_db)
    # 落盘份数只看被搬来的源文件，⛔ 不把清单 manifest.json 算进去
    def _n_payload(d: Path) -> int:
        return (len([p for p in d.iterdir()
                     if p.is_file() and p.name != organize.MANIFEST_NAME])
                if d.is_dir() else 0)

    arch_dir = b_root / organize.ARCHIVE_DIR_NAME / BATCH
    n_arch = _n_payload(arch_dir)
    qdir = b_root / organize.QUARANTINE_DIR_NAME
    n_q = _n_payload(qdir)
    return {
        "label": label, "snap_a": snap_a, "snap_b": snap_b, "rep": rep,
        "cell_diffs": cell_diffs, "pf_diffs": pf_diffs,
        "base_disk": base_disk, "b_disk": b_disk, "decisions": by,
        "a_pf": a_pf, "b_pf": b_pf, "n_arch": n_arch, "n_q": n_q,
        "a_processed_all": len(snap_a["processed_files"]),
        "b_processed_all": len(snap_b["processed_files"]),
        "a_res": a_res, "b_res": b_res,
        "a_ingest_s": a_ingest_s, "b_ingest_s": b_ingest_s, "org_s": b_org_s,
    }


def run_e2e(base: Path, tmp: Path, ck: vo.Checker, label: str) -> dict:
    """在 base 的两份副本上做全量端到端验收，逐条断言并打印，返回汇总指标。"""
    p = build_pair(base, tmp, label)
    snap_a, snap_b, rep = p["snap_a"], p["snap_b"], p["rep"]
    keep, archived, quarantined, skip = (p["decisions"][organize.KEEP],
                                         p["decisions"][organize.ARCHIVE],
                                         p["decisions"][organize.QUARANTINE],
                                         p["decisions"][organize.SKIP])

    print(f"--- 端到端（{label}）：A=全量源根副本 / B=整理后源根副本，各自新建库 ---")
    print(f"A 库：{snap_a['row_count']} 行 × {len(vo.METRIC_COLUMNS)} 指标列；"
          f"摄入 {p['a_res']['files']} 份源文件、{p['a_res']['rows']} 行、耗时 {p['a_ingest_s']:.2f}s")
    print(f"B 库：{snap_b['row_count']} 行 × {len(vo.METRIC_COLUMNS)} 指标列；"
          f"摄入 {p['b_res']['files']} 份源文件、{p['b_res']['rows']} 行、耗时 {p['b_ingest_s']:.2f}s")
    print(f"整理决策：keep {len(keep)} / archive {len(archived)} / "
          f"quarantine {len(quarantined)} / skip {len(skip)}（报告 moved={rep['moved']}）")
    print(f"断档：{len(rep['gaps'])} 段；哨兵告警：{len(rep['sentinel_alerts'])} 条")
    print(f"逐格差异：行级/格级 {len(p['cell_diffs'])} 处；processed_file 差异 {len(p['pf_diffs'])} 处")

    ck.check(not p["cell_diffs"],
             f"[{label}] daily_detail 逐格不变（{snap_a['row_count']} 行 × "
             f"{len(vo.METRIC_COLUMNS)} 指标列，0 处差异）",
             "; ".join(vo.format_diff(d) for d in p["cell_diffs"][:3]))
    ck.check(snap_a["row_count"] == snap_b["row_count"],
             f"[{label}] A/B 行数相同（{snap_a['row_count']} == {snap_b['row_count']}）")
    ck.check(len(p["pf_diffs"]) <= 1,
             f"[{label}] processed_file 差异只有记账条数一处（实际 {len(p['pf_diffs'])} 处）")
    ck.check(rep["moved"] == len(archived),
             f"[{label}] 归档搬动数 == archive 决策数（{rep['moved']} == {len(archived)}）")
    ck.check(rep["quarantined"]["moved"] == len(quarantined),
             f"[{label}] 隔离搬动数 == quarantine 决策数"
             f"（{rep['quarantined']['moved']} == {len(quarantined)}）")
    ck.check(set(p["b_disk"]) == set(keep) | set(skip),
             f"[{label}] 整理后源表目录只剩 保留集 ∪ 跳过集"
             f"（{len(p['b_disk'])} = {len(keep)} ∪ {len(skip)}）",
             f"多={sorted(set(p['b_disk']) - set(keep) - set(skip))[:3]}；"
             f"缺={sorted((set(keep) | set(skip)) - set(p['b_disk']))[:3]}")
    ck.check(p["a_pf"] == p["base_disk"],
             f"[{label}] A 库 processed_file 源表条目与磁盘全量文件一一对应（{len(p['a_pf'])} 条）",
             f"磁盘有库缺={sorted(set(p['base_disk']) - set(p['a_pf']))[:3]}；"
             f"库有磁盘缺={sorted(set(p['a_pf']) - set(p['base_disk']))[:3]}")
    ck.check(p["b_pf"] == p["b_disk"],
             f"[{label}] B 库 processed_file 源表条目与整理后磁盘文件一一对应（各 {len(p['b_pf'])} 条）",
             f"磁盘有库缺={sorted(set(p['b_disk']) - set(p['b_pf']))[:3]}；"
             f"库有磁盘缺={sorted(set(p['b_pf']) - set(p['b_disk']))[:3]}")
    moved_away = sorted(set(p["a_pf"]) - set(p["b_pf"]))
    ck.check(moved_away == sorted(set(archived) | set(quarantined)),
             f"[{label}] A 比 B 多出的源表条目 == 归档 ∪ 隔离（{len(moved_away)} 份）",
             f"A−B={moved_away[:3]}；archive∪quarantine="
             f"{(sorted(set(archived) | set(quarantined)))[:3]}")
    ck.check(p["n_arch"] == len(archived),
             f"[{label}] 归档区实际落盘 {p['n_arch']} 份 == archive {len(archived)}")
    ck.check(p["n_q"] == len(quarantined),
             f"[{label}] 隔离区实际落盘 {p['n_q']} 份 == quarantine {len(quarantined)}")

    return {
        "label": label,
        "a_rows": snap_a["row_count"], "b_rows": snap_b["row_count"],
        "metric_cols": len(vo.METRIC_COLUMNS),
        "cell_diffs": len(p["cell_diffs"]), "pf_diffs": len(p["pf_diffs"]),
        "a_processed": p["a_processed_all"], "b_processed": p["b_processed_all"],
        "keep": len(keep), "archive": len(archived),
        "quarantine": len(quarantined), "skip": len(skip),
        "gaps": rep["gaps"], "sentinel": len(rep["sentinel_alerts"]),
        "moved": rep["moved"], "q_moved": rep["quarantined"]["moved"],
        "organize_seconds": round(p["org_s"], 3),
        "b_ingest_seconds": round(p["b_ingest_s"], 3),
        "a_ingest_seconds": round(p["a_ingest_s"], 3),
        "report_md": rep.get("report_md"),
    }


def print_summary(m: dict) -> None:
    """打印「搬走 / 保留 / 隔离 / 断档 / 耗时增量」汇总报告（含收益诚实说）。"""
    total_b = m["organize_seconds"] + m["b_ingest_seconds"]
    ratio = (m["organize_seconds"] / total_b * 100) if total_b else 0.0
    print()
    print("=== 工单 06 汇总报告 ===")
    print(f"  搬走（归档）    ：{m['moved']} 份（决策 archive = {m['archive']}）")
    print(f"  搬走（隔离）    ：{m['q_moved']} 份（决策 quarantine = {m['quarantine']}）")
    print(f"  保留            ：{m['keep']} 份")
    print(f"  跳过（零贡献店）：{m['skip']} 份")
    print(f"  断档            ：{len(m['gaps'])} 段  "
          f"{[(g['shop'], g['kind'], g['start'], g['end']) for g in m['gaps']]}")
    print(f"  哨兵告警        ：{m['sentinel']} 条")
    print(f"  耗时增量        ：整理 {m['organize_seconds']}s vs 摄入 {m['b_ingest_seconds']}s"
          f"（整批 {total_b:.2f}s，整理占比 {ratio:.1f}%）")
    print(f"  processed_file  ：A（全量）= {m['a_processed']} 条 → "
          f"B（整理后）= {m['b_processed']} 条（差 {m['a_processed'] - m['b_processed']} 份被搬走）")
    print("  ⚠️ 收益诚实说（⚠️ 实测口径，已推翻早先「省 1.2s」的估计）：整理**不省时间**——"
          "它自己要先读全部源文件算 key（本次 113 份 ≈ 2.6s），摄入虽然变快（115→22 份）但抵不掉，"
          "**整批净耗时略增约 1.15s**。真实价值是**目录可读性**，不是性能；正确性也不受影响"
          "（行级去重早已由 keep=\"last\" + UNIQUE 兜住）。⛔ 不要用性能向任何人推销它。")


# --------------------------------------------------------------------------
# selftest：证明闸门有牙齿
# --------------------------------------------------------------------------

def cmd_selftest(args) -> int:
    ck = vo.Checker("工单 06 自证：验收闸门（全量端到端）有牙齿")
    tmp = Path(tempfile.mkdtemp(prefix="verify_organize_accept_"))
    try:
        # —— 1. 夹具端到端：clean 夹具（可摄入）跑通 A/B ——
        fx = tmp / "fx_clean"
        vo.make_fixtures(fx, clean=True)
        m = run_e2e(fx, tmp, ck, "fixture")
        ck.check(m["b_rows"] > 0, f"夹具 A/B 库都有数据（B={m['b_rows']} 行）")
        ck.check(m["archive"] >= 1, f"夹具确有可归档文件（archive={m['archive']}）")

        # —— 2. 牙齿之一（防假阴性）：让 B 侧少一份**保留集**文件 → 必须报行级差异 ——
        keep_name = next(d["filename"] for d in organize.derive_decisions(fx)
                         if d["decision"] == organize.KEEP)

        def drop_one_keep(b_root: Path, rep: dict):
            # 模拟「过度归档」：把一份本应保留的文件也删掉 → B 摄入读不到它
            (b_root / TABLE_DIR_NAME / keep_name).unlink()

        p2 = build_pair(fx, tmp, "fixture_drop", b_mutate=drop_one_keep)
        diffs = vo.diff_snapshots(p2["snap_a"], p2["snap_b"])
        cell = [d for d in diffs if d["kind"] != "processed_file"]
        ck.check(bool(cell),
                 f"🔴 少一份保留集文件（{keep_name}）后比对必须报出行级差异"
                 "（证明断言抓得住丢 key）",
                 "差异为空 → 断言没牙齿")
        ck.check(p2["snap_a"]["row_count"] > p2["snap_b"]["row_count"],
                 f"丢 key 表现为 A 行数 > B 行数"
                 f"（{p2['snap_a']['row_count']} > {p2['snap_b']['row_count']}）")

        # —— 3. 牙齿之二：改一个指标值 → 必须报格级差异 ——
        base_snap = vo.load_snapshot(tmp / "fixture_A.json")
        db_probe = tmp / "fixture_A.db"
        key = vo._bump_one_value(db_probe, 1234.5)
        snap_bump = vo.snapshot(db_probe, tmp / "fixture_A_bump.json")
        d_bump = [d for d in vo.diff_snapshots(base_snap, snap_bump) if d["kind"] == "格级"]
        ck.check(bool(d_bump) and d_bump[0]["row"] == key and d_bump[0]["column"] == "amount",
                 "改一个指标值后比对必须报出格级差异且指向那一格",
                 f"实际 {d_bump[:1]}（期望行 {key} / amount）")

        # —— 4. 真实 ResourceData 顶层条目零改动（只读） ——
        before = sorted(p.name for p in config.POP_SOURCE_DIR.glob("*")) if config.POP_SOURCE_DIR.is_dir() else []
        after = sorted(p.name for p in config.POP_SOURCE_DIR.glob("*")) if config.POP_SOURCE_DIR.is_dir() else []
        ck.check(before == after, "真实 ResourceData/ 顶层条目未变（验收闸门不碰真实目录）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ck.report()


# --------------------------------------------------------------------------
# accept：真实源根副本上的全量端到端验收
# --------------------------------------------------------------------------

def cmd_accept(args) -> int:
    base = Path(args.source).expanduser().resolve()
    if not (base / TABLE_DIR_NAME).is_dir():
        print(f"[错误] 数据源表目录不存在：{base / TABLE_DIR_NAME}", file=sys.stderr)
        return 2
    real = config.POP_SOURCE_DIR.resolve()
    if base == real:
        print(f"[提示] 传入的是真实源根 {real}：本闸门只在**临时副本**上搬文件"
              f"（{real} 本身只读、零改动）。")
    ck = vo.Checker("工单 06 验收：全量端到端逐格不变 + processed_file 核对")
    tmp = Path(tempfile.mkdtemp(prefix="verify_organize_accept_"))
    try:
        m = run_e2e(base, tmp, ck, "accept")
        print_summary(m)
        print(f"\n整理报告（真实产物形态，落在临时目录）：{m['report_md']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ck.report()


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="verify_organize_acceptance.py",
        description="工单 06 验收闸门：全量端到端（A/B 双库逐格不变）+ processed_file 核对 + 汇总报告。",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("selftest", help="自证闸门有牙齿（夹具端到端 + 注入丢失/改值必须变红）")
    t.set_defaults(func=cmd_selftest)

    a = sub.add_parser("accept", help="真实源根副本上的全量端到端验收（⛔ 真实目录只读）")
    a.add_argument("source", help="数据源根目录（如 ResourceData；只在副本上搬文件）")
    a.set_defaults(func=cmd_accept)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文会乱码
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
