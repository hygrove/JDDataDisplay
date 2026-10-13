#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工单 07 的验证：整理器「隔离文件回滚通道」——误隔离的文件能像误归档的一样，用同一条
`rollback()` 命令精确撤回。

为什么需要它：
    归档区有回滚通道而隔离区没有，是**不对称**的：隔离恰恰是最可能误判的一类
    （形态判定偏保守、哨兵探针刻意宽松），越容易误判的一侧反而越难撤回。工单 04 建立隔离区后，
    误隔离的文件只能人工从 `_quarantine/` 拖回去——人工操作没有清单状态记录、事后无法审计。
    本工单让 `rollback()` 同时遍历归档清单与隔离清单，两者形状同构（同一套 `_plan_moves` /
    `_run_moves` 产出），唯一区别是目的地字段名（`archive_rel` vs `quarantine_rel`）。

    最危险的一类假绿是「回滚隔离文件时**动了归档清单**」——归档清单是那批归档**唯一**的
    追溯与回滚依据（ADR-0002），被悄悄改写会让它再也说不清哪些文件动过。所以本脚本把
    「两份清单互不干扰」用**逐字节比对**钉死：回滚隔离后归档清单字节不变，反之亦然。

覆盖工单 07 的验收（全部在临时目录的源根副本上执行）：
    1. 隔离回滚：按文件名把隔离文件搬回原位，隔离清单条目状态改 `rolled_back`
    2. 三种匹配方式：批次目录名（只动归档）/ 文件名或相对路径 / 空串（归档 ∪ 隔离全部）
    3. 两份清单互不干扰（逐字节）：回滚隔离 → 归档清单字节不变；回滚归档 → 隔离清单字节不变
    4. 不覆盖：目标已有同名文件 → 报冲突、两侧文件都在、隔离清单状态不改
    5. 返回结构区分来源：每条 entry 带 `source`；`by_source` 给出分项计数
    另覆盖：未匹配到条目（`not_found`）时不误报、且不写任何清单。

风格：沿用 `verify_organize_execute.py` / `verify_organize_quarantine.py`——独立脚本 +
复用 `verify_organize.Checker` + `sys.exit(1)`。⛔ 不引入 pytest。
⛔ 全程在临时目录的源根副本上执行，绝不触碰真实 `ResourceData`。

用法（必须用项目 venv 的解释器）：
    .venv/Scripts/python.exe backend/jobs/verify_organize_rollback.py selftest
"""
from __future__ import annotations

import argparse
import gc
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# 把仓库根目录塞进 sys.path：直接 `python backend/jobs/verify_organize_rollback.py` 也能导入 backend 包
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.jobs import config  # noqa: E402
from backend.jobs import organize  # noqa: E402
from backend.jobs import verify_organize as vo  # noqa: E402

TABLE_DIR_NAME = config.POP_TABLE_DIR_NAME

# 固定批次时间：让归档子目录名与清单路径可预期（不依赖「今天」）。
BATCH_NOW = datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc)
BATCH = "2026-10-12"

# full 夹具的固定决策数（工单 02 已固化：keep12 / archive5 / quarantine2 / skip1）。
EXPECT_ARCHIVE = 5
EXPECT_QUARANTINE = 2

# full 夹具里两份形态异常文件（工单 01 生成）——会被隔离的两份。
Q_NAME = "钻芯旗舰店_商品明细_2025-03-01_2025-03-30_日期异常.xlsx"


def _fresh_copy(base: Path, dest: Path) -> Path:
    """把夹具母本整棵拷一份到 dest（每个验收项各用独立副本，互不污染）。"""
    shutil.copytree(base, dest)
    return dest


def _table_dir(root: Path) -> Path:
    return root / TABLE_DIR_NAME


def _qdir(root: Path) -> Path:
    return root / organize.QUARANTINE_DIR_NAME


def _qmanifest_path(root: Path) -> Path:
    return organize._quarantine_manifest_path(root)


def _amani_path(root: Path) -> Path:
    return organize._manifest_path(root, BATCH)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _entry_status(manifest: dict, name: str) -> str:
    """按文件名（`source_rel` 末段）取清单条目的状态；找不到返回空串。"""
    for e in manifest.get("entries", []):
        if Path(e.get("source_rel", "")).name == name:
            return e.get("status", "")
    return ""


def _run_on(base: Path, tmp: Path, name: str) -> Path:
    """在全新副本上跑一次 `mode=on`（真归档 + 真隔离），返回该副本根目录。"""
    src = _fresh_copy(base, tmp / name)
    report = organize.organize_files(src, mode=organize.MODE_ON, now=BATCH_NOW)
    assert report["moved"] == EXPECT_ARCHIVE, report["moved"]
    assert report["quarantined"]["moved"] == EXPECT_QUARANTINE, report["quarantined"]["moved"]
    return src


# ==========================================================================
# 证据 1：按文件名回滚隔离文件（回原地 + 状态 rolled_back + 归档清单字节不变）
# ==========================================================================

def evidence_quarantine_by_file(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """回滚一份隔离文件：文件回原位、隔离清单状态改 rolled_back、归档清单逐字节不变。"""
    src = _run_on(base, tmp, "q_by_file")
    table = _table_dir(src)
    qdir = _qdir(src)
    qman = _qmanifest_path(src)
    aman = _amani_path(src)

    ck.check(not (table / Q_NAME).exists(), f"前置：隔离文件已离开源表目录（{Q_NAME}）")
    ck.check((qdir / Q_NAME).is_file(), "前置：隔离文件在 _quarantine/ 里")
    arch_bytes_before = aman.read_bytes()
    q_bytes_before = qman.read_bytes()

    rb = organize.rollback(src, Q_NAME)

    print("--- 证据 1：按文件名回滚隔离文件 ---")
    print(f"回滚 {Q_NAME}：restored={rb['restored']} 来源={rb['by_source']} 条目={rb['entries']}")

    ck.check(rb["restored"] == 1 and rb["conflicts"] == 0 and rb["missing"] == 0,
             "按文件名回滚隔离文件：恰好搬回 1 份、无冲突无缺失", str(rb))
    ck.check(rb["entries"] and rb["entries"][0]["source"] == "quarantine",
             "返回条目标明来源为 quarantine", str(rb["entries"]))
    ck.check(rb["by_source"]["quarantine"]["restored"] == 1
             and rb["by_source"]["archive"]["restored"] == 0,
             "by_source 分项：隔离 1 / 归档 0", str(rb["by_source"]))
    ck.check((table / Q_NAME).is_file(), "隔离文件回到源表目录原位")
    ck.check(not (qdir / Q_NAME).exists(), "隔离区已无该文件残留")
    ck.check(_entry_status(_load(qman), Q_NAME) == organize.STATUS_ROLLED_BACK,
             "隔离清单条目状态改为 rolled_back")
    ck.check(aman.read_bytes() == arch_bytes_before,
             "🔴 回滚隔离后**归档清单逐字节不变**（两份清单互不干扰）")
    ck.check(qman.read_bytes() != q_bytes_before,
             "隔离清单确实被改写了（不是两份都没动）")


# ==========================================================================
# 证据 2：三种匹配方式（批次名 → 只动归档 / 相对路径 / 空串 → 全部）
# ==========================================================================

def evidence_match_modes(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """分别验批次名、相对路径、空串三种匹配方式。"""
    # ---- 2a 按批次名：只回滚该批次归档，隔离区不动 ----
    src = _run_on(base, tmp, "q_by_batch")
    table = _table_dir(src)
    qdir = _qdir(src)
    aman = _amani_path(src)
    qman = _qmanifest_path(src)
    arch_names = [Path(e["source_rel"]).name for e in _load(aman)["entries"]]
    q_bytes_before = qman.read_bytes()

    rb = organize.rollback(src, BATCH)

    print("--- 证据 2a：按批次目录名回滚（只动归档）---")
    print(f"回滚 {BATCH}：restored={rb['restored']} by_source={rb['by_source']}")
    ck.check(rb["restored"] == EXPECT_ARCHIVE, f"按批次名回滚 {EXPECT_ARCHIVE} 份归档",
             f"restored={rb['restored']}")
    ck.check(rb["by_source"]["quarantine"]["restored"] == 0,
             "按批次名回滚**不碰**隔离区（隔离无批次概念）", str(rb["by_source"]))
    ck.check(all((table / n).exists() for n in arch_names), "归档 5 份全部回到原位")
    ck.check((qdir / Q_NAME).is_file(), "隔离文件仍留在隔离区（未被批次回滚牵连）")
    ck.check(qman.read_bytes() == q_bytes_before,
             "🔴 按批次回滚归档后**隔离清单逐字节不变**（两份清单互不干扰）")

    # ---- 2b 按相对路径：回滚一份隔离文件（source_rel 全路径） ----
    src = _run_on(base, tmp, "q_by_relpath")
    table = _table_dir(src)
    rel = f"{TABLE_DIR_NAME}/{Q_NAME}"
    rb = organize.rollback(src, rel)
    print("--- 证据 2b：按相对路径回滚隔离文件 ---")
    print(f"回滚 {rel}：restored={rb['restored']} 来源={rb['entries']}")
    ck.check(rb["restored"] == 1 and rb["entries"][0]["source"] == "quarantine",
             "按相对路径命中隔离清单并搬回 1 份", str(rb))
    ck.check((table / Q_NAME).is_file(), "按相对路径回滚后隔离文件回到原位")

    # ---- 2c 空串：归档全部批次 + 隔离清单全部未回滚条目 ----
    src = _run_on(base, tmp, "q_all")
    table = _table_dir(src)
    aman = _amani_path(src)
    qman = _qmanifest_path(src)
    rb = organize.rollback(src, "")
    print("--- 证据 2c：空串回滚（归档 ∪ 隔离）---")
    print(f"空串回滚：restored={rb['restored']} by_source={rb['by_source']}")
    ck.check(rb["restored"] == EXPECT_ARCHIVE + EXPECT_QUARANTINE,
             f"空串回滚 {EXPECT_ARCHIVE + EXPECT_QUARANTINE} 份（归档 {EXPECT_ARCHIVE} + 隔离 {EXPECT_QUARANTINE}）",
             f"restored={rb['restored']}")
    ck.check(rb["by_source"]["archive"]["restored"] == EXPECT_ARCHIVE
             and rb["by_source"]["quarantine"]["restored"] == EXPECT_QUARANTINE,
             "by_source 分项：归档 5 / 隔离 2", str(rb["by_source"]))
    ck.check(all(e["status"] == organize.STATUS_ROLLED_BACK
                 for e in _load(aman)["entries"]),
             "归档清单所有条目状态都成了 rolled_back")
    ck.check(all(e["status"] == organize.STATUS_ROLLED_BACK
                 for e in _load(qman)["entries"]),
             "隔离清单所有条目状态都成了 rolled_back")
    ck.check(not any((src / organize.ARCHIVE_DIR_NAME / BATCH).glob("*.xlsx")),
             "空串回滚后归档区已无残留文件")
    ck.check(not any(_qdir(src).glob("*.xlsx")), "空串回滚后隔离区已无残留文件")


# ==========================================================================
# 证据 3：不覆盖（目标已有同名文件 → 报冲突、两侧都在、状态不改）
# ==========================================================================

def evidence_no_overwrite(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """目标原位已有同名文件时：报冲突、⛔ 不覆盖、隔离清单状态不改。"""
    src = _run_on(base, tmp, "q_conflict")
    table = _table_dir(src)
    qdir = _qdir(src)
    qman = _qmanifest_path(src)
    sentinel = "OCCUPIED-BY-SOMEONE-ELSE"
    (table / Q_NAME).write_text(sentinel, encoding="utf-8")
    q_bytes_before = qman.read_bytes()

    rb = organize.rollback(src, Q_NAME)

    print("--- 证据 3：不覆盖（同名冲突）---")
    print(f"冲突回滚 {Q_NAME}：restored={rb['restored']} conflicts={rb['conflicts']} "
          f"by_source={rb['by_source']}")
    ck.check(rb["conflicts"] == 1 and rb["restored"] == 0,
             "目标已有同名文件 → 报冲突、不搬回", str(rb))
    ck.check(rb["by_source"]["quarantine"]["conflicts"] == 1,
             "by_source 把冲突记在 quarantine 名下", str(rb["by_source"]))
    ck.check((table / Q_NAME).read_text(encoding="utf-8") == sentinel,
             "🔴 冲突时⛔ 不覆盖既有文件")
    ck.check((qdir / Q_NAME).is_file(), "冲突时隔离副本仍留在隔离区（数据不丢）")
    ck.check(qman.read_bytes() == q_bytes_before,
             "冲突未搬移 → 隔离清单状态不改（逐字节不变）")


# ==========================================================================
# 证据 4：未匹配到条目 → not_found，且不写任何清单
# ==========================================================================

def evidence_not_found(base: Path, tmp: Path, ck: vo.Checker) -> None:
    """给一个两份清单都没有的名字：not_found=True、无条目、两份清单都不被写。"""
    src = _run_on(base, tmp, "q_notfound")
    aman = _amani_path(src)
    qman = _qmanifest_path(src)
    a_before, q_before = aman.read_bytes(), qman.read_bytes()

    rb = organize.rollback(src, "根本不存在的文件.xlsx")

    print("--- 证据 4：未匹配到条目 ---")
    print(f"rollback(不存在)：not_found={rb['not_found']} entries={rb['entries']}")
    ck.check(rb["not_found"] is True and rb["entries"] == [] and rb["restored"] == 0,
             "未匹配到任何条目 → not_found=True、零条目", str(rb))
    ck.check(aman.read_bytes() == a_before and qman.read_bytes() == q_before,
             "未匹配时不改写任何清单（两份都逐字节不变）")


# ==========================================================================
# selftest
# ==========================================================================

def cmd_selftest(_args) -> int:
    """在临时目录里把证据 1–4 全跑一遍（每项用独立源根副本）。"""
    ck = vo.Checker("工单 07 自证：隔离文件回滚通道（两份清单互不干扰）")
    tmp = Path(tempfile.mkdtemp(prefix="verify_organize_rollback_"))
    try:
        base = tmp / "base"
        inv = vo.make_fixtures(base, clean=False)   # full 夹具：含 5 归档 + 2 隔离
        ck.check(not vo.verify_fixture_oracle(base, inv), "full 夹具的 oracle 自检通过（底座可信）")

        evidence_quarantine_by_file(base, tmp, ck)
        evidence_match_modes(base, tmp, ck)
        evidence_no_overwrite(base, tmp, ck)
        evidence_not_found(base, tmp, ck)

        # 安全护栏自检：所有执行都发生在临时目录，没有污染真实数据源
        ck.check(config.POP_SOURCE_DIR.resolve() not in base.resolve().parents
                 and base.resolve() != config.POP_SOURCE_DIR.resolve(),
                 "所有执行都在临时目录的源根副本上，未触碰真实 ResourceData")
    finally:
        gc.collect()  # 兼容旧实现：万一还有未关闭的句柄，先触发循环 GC 再删
        shutil.rmtree(tmp, ignore_errors=True)
        ck.check(not tmp.exists(), "临时目录已清理")
    return ck.report()


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    p = argparse.ArgumentParser(
        prog="verify_organize_rollback.py",
        description="工单 07 验证：整理器隔离文件回滚通道（归档 ∪ 隔离，两份清单互不干扰）。",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("selftest", help="在临时目录里跑证据 1–4")
    t.set_defaults(func=cmd_selftest)
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
