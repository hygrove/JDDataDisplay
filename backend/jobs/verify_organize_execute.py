#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工单 03 的验证：整理「执行层」——真搬文件、write-ahead 清单、单文件容错、幂等、可回滚、钩入批处理。

为什么需要它：
    判定（工单 02）只保证「决策算得对」；这一步才是**动文件**的地方。而「动错文件」在这
    仓库里的后果是**无声的**——`daily_detail` 只增不减，被搬走文件贡献的行不会消失，而是永久
    冻结成僵尸行 / 历史缺口（详见 `verify_organize.py` 开头的说明）。所以执行层的每一项都必须
    用「外部可观察的行为」钉死：文件到底在不在预期位置、`daily_detail` 是否逐格不变、
    清单写了什么、崩溃 / 失败 / 回滚后状态对不对。

    🔴 逐格不变这一项必须用**两个各自新建的库**比对（整理前全量摄入 → 库 A；整理后只留保留集
    摄入 → 库 B）。若在同一个库上前后各摄入一次，`daily_detail` 只增不减会**掩盖**丢 key：
    丢掉的 key 那行还留在库里，差异看起来是 0 —— 这正是这个仓库最危险的那类假绿。

九项证据（与工单 03 的验收项一一对应，全部在临时目录的源根副本上执行）：
    1. 真执行：冗余文件被搬进归档区（源表目录外、按批次日期分目录、保持原文件名），保留集仍在原地
    2. `daily_detail` 逐格不变（两库比对；唯一允许的差异是 processed_file 记账条数）
    3. 幂等：紧接着再跑一次 → 零搬动
    4. dry-run 零副作用：源表目录指纹不变、没有生成清单、没有冒出归档区/隔离区、退出码 0
    5. 单文件失败不中断：某文件搬不动 → 记 failed + 原因、其余照常搬完、本批照常入库
    6. 回滚：按批次 / 按文件搬回原位、清单状态改 rolled_back；重名冲突时不覆盖
    7. 整理失败不拖垮批处理：monkeypatch 让整理抛异常 → run_once() 仍 success=True 并完成入库
    8. 白名单读不到 → 零归档（安全属性）
    9. 真实数据 dry-run（只读）：贴 keep/archive/quarantine/skip 统计，并断言源表目录指纹零改动

风格：沿用 `verify_organize.py`——独立脚本 + 复用 `verify_organize.Checker` + sys.exit(1)。
⛔ 不引入 pytest。⛔ 不在真实 `ResourceData` 上执行搬移（除 `realdryrun` 子命令的只读 dry-run）。

用法（必须用项目 venv 的解释器）：
    .venv/Scripts/python.exe backend/jobs/verify_organize_execute.py selftest
    .venv/Scripts/python.exe backend/jobs/verify_organize_execute.py realdryrun ResourceData
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

# 把仓库根目录塞进 sys.path：直接 `python backend/jobs/verify_organize_execute.py` 也能导入 backend 包
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

# clean 夹具里应被归档的份数（工单 02 已固化：keep12 / archive5 / quarantine1 / skip1）。
EXPECT_ARCHIVE = 5


def _fresh_copy(base: Path, dest: Path) -> Path:
    """把夹具母本整棵拷一份到 dest（每个验收项各用独立副本，互不污染）。"""
    shutil.copytree(base, dest)
    return dest


def _table_dir(root: Path) -> Path:
    return root / TABLE_DIR_NAME


def _manifest_of(root: Path) -> dict:
    p = organize._manifest_path(root, BATCH)
    return json.loads(p.read_text(encoding="utf-8"))


# ==========================================================================
# 证据 1 + 2：真执行搬移 + daily_detail 逐格不变
# ==========================================================================

def evidence_execute_and_nochange(base: Path, tmp: Path, ck: vo.Checker) -> Path:
    """在副本上真执行一次，检查搬移结果 + 清单，并证明 daily_detail 逐格不变。

    返回：执行后的副本根目录（供后续幂等 / 回滚复用）。
    """
    src = _fresh_copy(base, tmp / "exec")
    table = _table_dir(src)
    archive_dir = src / organize.ARCHIVE_DIR_NAME / BATCH
    manifest_path = organize._manifest_path(src, BATCH)

    decisions = organize.derive_decisions(src)
    archived = sorted(d["filename"] for d in decisions if d["decision"] == organize.ARCHIVE)
    keep = sorted(d["filename"] for d in decisions if d["decision"] == organize.KEEP)
    # 工单 04 起，「on」模式的同一次运行**也**把 quarantine 文件搬进隔离区（源表目录之外）。
    # 于是它们同样退出摄入 → processed_file 的记账条数按「归档 + 隔离」一起减少。
    quarantined = sorted(d["filename"] for d in decisions if d["decision"] == organize.QUARANTINE)
    ck.check(len(archived) == EXPECT_ARCHIVE,
             f"夹具待归档份数 == {EXPECT_ARCHIVE}（实际 {len(archived)}）", str(archived))

    # —— 整理前：全量摄入进库 A ——
    db_a = tmp / "before.db"
    vo._ingest_into(src, db_a)
    snap_a = vo.snapshot(db_a, tmp / "snap_before.json")

    # —— 真执行（并挂一个「写入即读清单」的探针，钉住 write-ahead 顺序）——
    trace: dict = {}

    def spy_move(s, d):
        if "first_move_manifest_statuses" not in trace:
            man = json.loads(manifest_path.read_text(encoding="utf-8"))
            trace["first_move_manifest_statuses"] = [e["status"] for e in man["entries"]]
        organize.move_file(s, d)

    report = organize.organize_files(src, mode=organize.MODE_ON, now=BATCH_NOW, move=spy_move)

    print("--- 证据 1：真执行 ---")
    print(f"报告：mode={report['mode']} planned={report['planned']} moved={report['moved']} "
          f"failed={report['failed']} archive_dir={report['archive_dir']}")
    print(f"归档的 {len(archived)} 份：{archived}")
    print(f"源表目录里剩下（{len(keep)} 份 keep + 隔离/跳过）："
          f"{sorted(p.name for p in table.glob('*.xlsx'))[:6]} …")

    # ① 归档区在源表目录之外、按批次分目录、保持原文件名
    ck.check(table not in archive_dir.parents, "归档区位于源表目录之外")
    ck.check(archive_dir.name == BATCH, f"归档按批次日期分目录（{archive_dir.name}）")
    ck.check(report["moved"] == EXPECT_ARCHIVE,
             f"搬动 {EXPECT_ARCHIVE} 份（实际 {report['moved']}）")
    missing = [n for n in archived if not (archive_dir / n).is_file()]
    lingering = [n for n in archived if (table / n).exists()]
    ck.check(not missing, "归档区里每一份都在（原文件名保留）", str(missing))
    ck.check(not lingering, "源表目录里已不含被归档文件", str(lingering))
    still_kept = [n for n in keep if not (table / n).exists()]
    ck.check(not still_kept, "保留集仍全部留在原地", str(still_kept))

    # ② write-ahead：搬第一个文件之前，整批已经写成 planned 落盘
    first = trace.get("first_move_manifest_statuses", [])
    ck.check(len(first) == EXPECT_ARCHIVE and set(first) == {organize.STATUS_PLANNED},
             "write-ahead：搬第一个文件前，整批已 planned 落盘",
             f"首个搬动时刻的清单状态={first}")

    # ③ 清单内容：每条含原路径 / 归档路径 / 理由 / 批次时间 / 状态；最终为 done
    man = _manifest_of(src)
    sample = man["entries"][:2]
    print("清单片段（前 2 条）：")
    print("  " + json.dumps(sample, ensure_ascii=False, indent=2).replace("\n", "\n  "))
    fields = {"source_rel", "archive_rel", "reason", "batch_time", "status"}
    ck.check(all(fields <= set(e) for e in man["entries"]),
             "清单每条含 原路径/归档路径/理由/批次时间/状态", f"字段样例={sorted(man['entries'][0])}")
    ck.check(all(e["status"] == organize.STATUS_DONE for e in man["entries"]),
             "搬完后整批状态为 done", str([e["status"] for e in man["entries"]]))
    ck.check(all(e["archive_rel"].startswith(f"{organize.ARCHIVE_DIR_NAME}/{BATCH}/")
                 for e in man["entries"]), "清单的归档后相对路径落在本批次目录下")

    # ④ 清单容错：损坏 / 半截的清单必须能被读出来，而不是抛异常
    corrupt = tmp / "corrupt_manifest.json"
    corrupt.write_text('{"batch": "x", "entries": [ {"a"', encoding="utf-8")  # 半截 JSON
    got = organize.read_manifest(corrupt)
    ck.check(isinstance(got, dict) and got.get("corrupt") is True,
             "半截 / 损坏的清单被容错读出（不抛异常）", str(got)[:160])

    # —— 证据 2：整理后再摄入进**另一个新库** B，两库逐格比对 ——
    db_b = tmp / "after.db"
    vo._ingest_into(src, db_b)
    snap_b = vo.snapshot(db_b, tmp / "snap_after.json")
    diffs = vo.diff_snapshots(snap_a, snap_b)
    cell_diffs = [d for d in diffs if d["kind"] != "processed_file"]
    pf = [d for d in diffs if d["kind"] == "processed_file"]

    print("--- 证据 2：daily_detail 逐格不变（两库比对）---")
    print(f"库 A 行数={snap_a['row_count']}；库 B 行数={snap_b['row_count']}；"
          f"格级/行级差异={len(cell_diffs)}")
    if cell_diffs:
        for d in cell_diffs[:3]:
            print("  " + vo.format_diff(d))
    print(f"processed_file 记账差异：{pf[0]['note'] if pf else '<无>'}")

    ck.check(snap_a["row_count"] > 0 and snap_a["row_count"] == snap_b["row_count"],
             f"整理前后行数相同且有数据（{snap_a['row_count']} 行）")
    ck.check(not cell_diffs, "整理前后 daily_detail 逐格不变（0 处行级/格级差异）",
             "; ".join(vo.format_diff(d) for d in cell_diffs[:3])[:400])
    ck.check(len(pf) == 1 and len(snap_b["processed_files"]) == len(snap_a["processed_files"]) - EXPECT_ARCHIVE - len(quarantined),
             "唯一差异是 processed_file 条数按预期减少（归档 + 隔离，规格写明的记账变化）",
             f"A={len(snap_a['processed_files'])} B={len(snap_b['processed_files'])} "
             f"（应减 {EXPECT_ARCHIVE}+{len(quarantined)}）")
    return src


# ==========================================================================
# 证据 3：幂等
# ==========================================================================

def evidence_idempotent(src: Path, ck: vo.Checker) -> None:
    """在原本次执行过的副本上再跑一次 → 必须零搬动，且不动任何文件。"""
    table = _table_dir(src)
    fp_before = vod._fingerprint(table)
    report = organize.organize_files(src, mode=organize.MODE_ON, now=BATCH_NOW)
    fp_after = vod._fingerprint(table)
    print("--- 证据 3：幂等 ---")
    print(f"再跑一次：planned={report['planned']} moved={report['moved']}；"
          f"源表目录指纹一致={fp_before == fp_after}")
    ck.check(report["moved"] == 0 and report["planned"] == 0,
             "紧接着再跑一次 → 零搬动（幂等）", f"report={report['planned']}/{report['moved']}")
    ck.check(fp_before == fp_after, "幂等运行没有改动源表目录（文件名 + mtime + 大小）")


# ==========================================================================
# 证据 4：dry-run 零副作用
# ==========================================================================

def evidence_dry_run(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """dry-run 必须不搬文件、不写清单、退出码 0；默认模式也必须是 dry-run。"""
    src = _fresh_copy(base, tmp / "dry")
    table = _table_dir(src)
    fp_before = vod._fingerprint(table)

    report = organize.organize_files(src, mode=organize.MODE_DRY_RUN, now=BATCH_NOW)
    fp_after = vod._fingerprint(table)
    archive_exists = (src / organize.ARCHIVE_DIR_NAME).exists()
    quarantine_exists = (src / "_quarantine").exists()

    # CLI 层也验一遍：显式 --mode dry-run 与**不给 --mode**（默认）都必须零副作用、退出码 0
    cli = subprocess.run(
        [sys.executable, "-m", "backend.jobs.organize", "--source", str(src), "--mode", "dry-run"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    cli_default = subprocess.run(
        [sys.executable, "-m", "backend.jobs.organize", "--source", str(src)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")

    print("--- 证据 4：dry-run 零副作用 ---")
    print(f"进程内 dry-run：moved={report['moved']}；源表目录指纹一致={fp_before == fp_after}；"
          f"_archive 是否出现={archive_exists}")
    print(f"CLI（--mode dry-run）退出码={cli.returncode}；CLI（默认，不给 --mode）退出码={cli_default.returncode}")
    ck.check(report["moved"] == 0 and report["planned"] == EXPECT_ARCHIVE,
             f"dry-run 不搬文件（计划 {EXPECT_ARCHIVE}，实际搬 {report['moved']}）")
    ck.check(fp_before == fp_after, "dry-run 前后源表目录指纹完全一致")
    ck.check(not archive_exists and not quarantine_exists, "dry-run 没有生成归档区 / 隔离区")
    ck.check(not organize._manifest_path(src, BATCH).exists(), "dry-run 没有写清单文件")
    ck.check(cli.returncode == 0, "CLI --mode dry-run 退出码为 0", (cli.stderr or "")[:200])
    ck.check(cli_default.returncode == 0, "CLI 默认（不给 --mode）退出码为 0", (cli_default.stderr or "")[:200])
    ck.check(not (src / organize.ARCHIVE_DIR_NAME).exists(), "CLI 默认不给 --mode 时也零副作用（默认 dry-run）")


# ==========================================================================
# 证据 5：单文件失败不中断
# ==========================================================================

def evidence_single_file_failure(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """一个文件搬不动 → 记 failed + 原因、其余照常搬完、本批照常入库。"""
    src = _fresh_copy(base, tmp / "fail")
    table = _table_dir(src)
    decisions = organize.derive_decisions(src)
    archived = sorted(d["filename"] for d in decisions if d["decision"] == organize.ARCHIVE)
    victim = archived[0]

    def flaky(s, d):
        # 模拟「文件被 Excel 占用 / 权限不足」：只对 victim 抛错，其余正常搬
        if s.name == victim:
            raise PermissionError("simulated: 文件被占用，无法搬动")
        organize.move_file(s, d)

    report = organize.organize_files(src, mode=organize.MODE_ON, now=BATCH_NOW, move=flaky)
    man = _manifest_of(src)
    vic_entry = next(e for e in man["entries"] if Path(e["source_rel"]).name == victim)

    # 失败不中断：本批照常入库
    db = tmp / "fail.db"
    ingest = vo._ingest_into(src, db)

    print("--- 证据 5：单文件失败不中断 ---")
    print(f"victim={victim}：moved={report['moved']} failed={report['failed']}")
    print(f"victim 清单状态={vic_entry['status']}；原因={vic_entry['error']}")
    print(f"随后入库：changed={ingest.get('changed')} rows={ingest.get('rows')}")

    ck.check(report["failed"] == 1 and report["moved"] == EXPECT_ARCHIVE - 1,
             "恰好 1 份失败、其余照常搬完（本批不中断）",
             f"moved={report['moved']} failed={report['failed']}")
    ck.check(vic_entry["status"] == organize.STATUS_FAILED and "PermissionError" in vic_entry["error"],
             "失败文件在清单里记为 failed 并带上真实原因", str(vic_entry))
    ck.check((table / victim).exists(), "失败文件留在源表目录原地（未被误移走）")
    ck.check(int(ingest.get("rows", 0)) > 0, "本批照常完成入库（失败未拖垮）", str(ingest))


# ==========================================================================
# 证据 6：回滚
# ==========================================================================

def evidence_rollback(exec_src: Path, base: Path, tmp: Path, ck: vo.Checker) -> None:
    """按文件回滚 1 份、按批次回滚其余；再单独验重名冲突不覆盖。"""
    table = _table_dir(exec_src)
    archive_dir = exec_src / organize.ARCHIVE_DIR_NAME / BATCH
    decisions = organize.derive_decisions(exec_src)
    # 此刻 exec 副本里已被搬走的 5 份，用清单拿（derive_decisions 只看得到保留集）
    entries = _manifest_of(exec_src)["entries"]
    names = [Path(e["source_rel"]).name for e in entries]
    one, rest = names[0], names[1:]

    rb_one = organize.rollback(exec_src, one)
    man_after_one = _manifest_of(exec_src)
    st_one = next(e["status"] for e in man_after_one["entries"] if Path(e["source_rel"]).name == one)
    rb_batch = organize.rollback(exec_src, BATCH)

    print("--- 证据 6：回滚 ---")
    print(f"按文件回滚 {one}：restored={rb_one['restored']}，清单状态={st_one}")
    print(f"按批次回滚 {BATCH}：restored={rb_batch['restored']}；"
          f"回位后表目录里原 5 份是否都在="
          f"{all((table / n).exists() for n in names)}")

    ck.check(rb_one["restored"] == 1 and st_one == organize.STATUS_ROLLED_BACK,
             "按单文件回滚：文件回原位、清单状态改 rolled_back")
    ck.check((table / one).exists(), "按文件回滚后该文件确实回到源表目录")
    ck.check(rb_batch["restored"] == len(rest), f"按批次回滚其余 {len(rest)} 份",
             f"restored={rb_batch['restored']}")
    ck.check(all((table / n).exists() for n in names), "回滚后 5 份全部回到原位")
    ck.check(all(e["status"] == organize.STATUS_ROLLED_BACK for e in _manifest_of(exec_src)["entries"]),
             "批次清单里所有条目状态都成了 rolled_back")
    ck.check(not any(archive_dir.glob("*.xlsx")), "回滚后归档区已无残留文件")

    # —— 重名冲突：目标位置已有同名文件时 ⛔ 不覆盖，报冲突 ——
    src2 = _fresh_copy(base, tmp / "conflict")
    table2 = _table_dir(src2)
    organize.organize_files(src2, mode=organize.MODE_ON, now=BATCH_NOW)
    conflict_name = names[0]
    (table2 / conflict_name).write_text("OCCUPIED-BY-SOMEONE-ELSE", encoding="utf-8")
    rb_c = organize.rollback(src2, conflict_name)
    print(f"冲突回滚 {conflict_name}：restored={rb_c['restored']} conflicts={rb_c['conflicts']}")
    ck.check(rb_c["conflicts"] == 1 and rb_c["restored"] == 0,
             "目标已有同名文件 → 报冲突、不搬回", str(rb_c))
    ck.check((table2 / conflict_name).read_text(encoding="utf-8") == "OCCUPIED-BY-SOMEONE-ELSE",
             "冲突时⛔ 不覆盖既有文件")
    ck.check((src2 / organize.ARCHIVE_DIR_NAME / BATCH / conflict_name).exists(),
             "冲突时归档副本仍留在归档区（数据不丢）")


# ==========================================================================
# 证据 7：整理失败不拖垮批处理
# ==========================================================================

def evidence_hook_does_not_break_batch(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """monkeypatch 让整理抛异常 → run_once() 仍 success=True 并完成入库。

    ⚠️ 全程把 config 的产出目录 / DB 路径 / 源根都指向临时副本，
    ⛔ 绝不触发真实目录的批处理。
    """
    src = _fresh_copy(base, tmp / "hook")
    data_dir = tmp / "hook_data"
    saved = {k: getattr(config, k) for k in
             ("POP_SOURCE_DIR", "DATA_DIR", "MODULES_DIR", "IMAGES_DIR", "THUMBS_DIR")}
    saved_db = jobdb.DB_PATH
    saved_of = organize.organize_files

    def boom(*_a, **_k):
        raise RuntimeError("simulated: 整理崩了")

    try:
        config.POP_SOURCE_DIR = src
        config.DATA_DIR = data_dir
        config.MODULES_DIR = data_dir / "modules"
        config.IMAGES_DIR = data_dir / "images"
        config.THUMBS_DIR = config.IMAGES_DIR / "thumbs"
        jobdb.DB_PATH = data_dir / "app.db"
        organize.organize_files = boom
        status = run_batch.run_once()
        db_has_rows = jobdb.DB_PATH.is_file() and jobdb.query_long_table() != []
    finally:
        organize.organize_files = saved_of
        jobdb.DB_PATH = saved_db
        for k, v in saved.items():
            setattr(config, k, v)

    print("--- 证据 7：整理失败不拖垮批处理 ---")
    print(f"monkeypatch 整理抛异常后：success={status.success}；DB 有数据={db_has_rows}")
    print(f"status.message = {status.message}")
    ck.check(status.success is True, "整理抛异常时 run_once() 仍返回 success=True", status.message)
    ck.check("整理失败" in status.message, "整理失败被记进 status.message", status.message)
    ck.check(db_has_rows, "整理失败后批处理仍完成入库（库里确有数据）")


# ==========================================================================
# 证据 8：白名单读不到 → 零归档
# ==========================================================================

def evidence_no_whitelist(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """白名单缺失 → 贡献店铺为空 → 全部 skip → 一个文件都不归档（安全属性）。"""
    src = _fresh_copy(base, tmp / "nowl")
    (src / WHITELIST_FILE).unlink()
    contributing = organize.load_contributing_shops(src)
    report = organize.organize_files(src, mode=organize.MODE_ON, now=BATCH_NOW)
    counts = report["counts"]

    print("--- 证据 8：白名单读不到 → 零归档 ---")
    print(f"contributing_shops={contributing}；counts={counts}；moved={report['moved']}")
    ck.check(contributing == set(), "白名单读不到 → 贡献店铺集合为空")
    ck.check(report["moved"] == 0 and report["planned"] == 0,
             "白名单读不到 → 零归档（一个文件都没搬）")
    ck.check(counts[organize.ARCHIVE] == 0 and counts[organize.KEEP] == 0,
             "白名单读不到 → 没有 keep / archive（全部走 skip 分支）", str(counts))
    ck.check(not (src / organize.ARCHIVE_DIR_NAME).exists(), "白名单读不到时不生成归档区")


# ==========================================================================
# selftest
# ==========================================================================

def cmd_selftest(_args) -> int:
    """在临时目录里把证据 1–8 全跑一遍（每项用独立源根副本）。"""
    ck = vo.Checker("工单 03 自证：执行层（搬移 / 清单 / 容错 / 幂等 / 回滚 / 钩入批处理）")
    tmp = Path(tempfile.mkdtemp(prefix="verify_organize_execute_"))
    try:
        base = tmp / "base"
        inv = vo.make_fixtures(base, clean=True)
        ck.check(not vo.verify_fixture_oracle(base, inv), "clean 夹具的 oracle 自检通过（底座可信）")

        exec_src = evidence_execute_and_nochange(base, tmp, ck)
        evidence_idempotent(exec_src, ck)
        evidence_dry_run(base, tmp, ck)
        evidence_single_file_failure(base, tmp, ck)
        evidence_rollback(exec_src, base, tmp, ck)
        evidence_hook_does_not_break_batch(base, tmp, ck)
        evidence_no_whitelist(base, tmp, ck)

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
# 证据 9：真实数据 dry-run（只读）
# ==========================================================================

def cmd_realdryrun(args) -> int:
    """在真实源表目录上只跑 dry-run：贴统计并断言文件指纹零改动（⛔ 不搬任何文件）。"""
    ck = vo.Checker("工单 03 证据 9：真实数据 dry-run（只读，零副作用）")
    root = Path(args.source).expanduser().resolve()
    table = _table_dir(root)
    ck.check(table.is_dir(), f"数据源表目录存在：{table}")
    if not table.is_dir():
        return ck.report()

    root_before = sorted(p.name for p in root.glob("*"))
    fp_before = vod._fingerprint(table)
    archive_before = (root / organize.ARCHIVE_DIR_NAME).exists()

    report = organize.organize_files(root, mode=organize.MODE_DRY_RUN, now=BATCH_NOW)

    fp_after = vod._fingerprint(table)
    root_after = sorted(p.name for p in root.glob("*"))
    archive_after = (root / organize.ARCHIVE_DIR_NAME).exists()
    c = report["counts"]

    print(f"真实源表目录：{table}（{len(fp_before)} 个条目）")
    print(f"决策统计：keep {c[organize.KEEP]} / archive {c[organize.ARCHIVE]} / "
          f"quarantine {c[organize.QUARANTINE]} / skip {c[organize.SKIP]}"
          f"（计划搬动 {report['planned']} 份，dry-run 实际搬 {report['moved']}）")
    print(f"源表目录指纹一致={fp_before == fp_after}；归档区前后是否出现："
          f"{archive_before} → {archive_after}")

    ck.check(fp_before == fp_after, "真实源表目录的文件名 + mtime + 大小指纹零改动")
    ck.check(root_before == root_after, "数据源根目录顶层条目零改动")
    ck.check(not archive_after and not archive_before, "dry-run 未生成归档区")
    ck.check(report["moved"] == 0, "dry-run 搬动数为 0")
    ck.check(not organize._manifest_path(root, BATCH).exists(), "dry-run 未写清单")
    return ck.report()


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    p = argparse.ArgumentParser(
        prog="verify_organize_execute.py",
        description="工单 03 验证：整理执行层的搬移 / 清单 / 容错 / 幂等 / 回滚 / 钩入批处理。",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("selftest", help="在临时目录里跑证据 1–8")
    t.set_defaults(func=cmd_selftest)
    r = sub.add_parser("realdryrun", help="真实数据只读 dry-run（证据 9）")
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
