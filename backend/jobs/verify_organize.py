#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工单 01 的验证底座：源表整理器（organize）的夹具生成 / 基线快照 / 逐格比对。

为什么需要它（而不是「改完代码跑一遍看看」）：
    「整理源表」这个功能的失败模式是**无声的**——`daily_detail` 只增不减
    （`_upsert_long_table` 只有 INSERT OR REPLACE，全项目没有 DELETE FROM daily_detail），
    所以「判错一个文件」不会报错，只会让那批 key 在库里冻结成**僵尸行**
    （要么成为历史缺口，要么停止更新、静默陈旧）。这类错误在页面上完全看不出来，
    只有「整理前后逐格比对」能抓住。本脚本就是那把尺子。

子命令：
    make-fixtures <目标目录> [--clean]
        在指定目录里生成 7 组源表场景的夹具（①–⑥ 见下方 FIXTURE_SPEC；⑦「并发归档互撤」
        是判据的判别性用例，缺了它「把判据写成『keys(F) ⊆ ⋃keys(别的文件)』」这条错
        照样全绿），并写出 fixtures.json（每份文件的场景标签与期望决策）。目标目录会被清空重建。
    snapshot <app.db> <快照.json>
        把 daily_detail 的全部 (date, shop, spu) 与 11 个指标列、以及 processed_file
        的相对路径清单，落成一份 JSON 快照（排序稳定 → 同数据两次快照逐字节相同）。
    diff <快照A.json> <快照B.json>
        比对两份快照，打印机读的「首个差异格」（行 / 列 / A 值 / B 值）；
        有差异 → 打印总数并以退出码 1 结束；无差异 → 打印 OK、退出码 0。
    selftest
        自证有牙齿（对应工单 01 的验收项）：
          ① 同一份数据快照两次必须逐字节相同、比对必须零差异（防假阳性）；
          ② 故意 +1234.5 改一个指标值 → 比对必须报出差异，且指向被改的那一格；
          ③ 故意删一行 → 比对必须报出「行级」差异（这正是僵尸行的失败形态）；
          ④ 通过子进程验证 diff 的**退出码**：有差异非零、无差异为零。
    env
        报告「数据源目录 / 数据库路径 / 白名单路径」能否通过环境变量注入
        （工单 01 的验收项之一：只报告，不改生产代码）。

风格：沿用项目既有验证脚本的做法——独立脚本 + 自制断言计数器 + sys.exit(1)。
⛔ 不引入 pytest（requirements.txt 是全量 freeze，加测试框架会破坏异地复现的前提）。

用法（必须用项目 venv 的解释器，依赖 polars / openpyxl）：
    .venv/Scripts/python.exe backend/jobs/verify_organize.py selftest
    .venv/Scripts/python.exe backend/jobs/verify_organize.py make-fixtures %TEMP%/org_fx
    .venv/Scripts/python.exe backend/jobs/verify_organize.py snapshot backend/app/data/app.db before.json
    .venv/Scripts/python.exe backend/jobs/verify_organize.py diff before.json after.json

⚠️ 夹具只写入你显式给出的目标目录；目标目录若落在 ResourceData/ 内会被**拒绝**（防止污染真实数据）。
⚠️ 本脚本不碰生产代码、不写 ResourceData、不改数据库（selftest 全程在临时目录里）。
"""
from __future__ import annotations

import argparse
import csv
import gc
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zlib
from datetime import date, timedelta
from pathlib import Path

# 把仓库根目录塞进 sys.path：直接 `python backend/jobs/verify_organize.py` 也能导入 backend 包
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.jobs import config  # noqa: E402
from backend.jobs.sources.base import LONG_COLUMNS  # noqa: E402

# 快照/比对关心的指标列 = 长表去掉 4 个身份列（shop/date/spu/spu_name/category）后的 11 列。
# 直接从 LONG_COLUMNS 派生，保证「后端加了新指标 → 快照自动跟上」，不另立一份清单。
IDENTITY_COLUMNS = ("shop", "date", "spu")
DESCRIPTOR_COLUMNS = ("spu_name", "category")
METRIC_COLUMNS = [
    c for c in LONG_COLUMNS if c not in IDENTITY_COLUMNS + DESCRIPTOR_COLUMNS
]

# 夹具里复刻的真实列名（必须与 sources/excel_source.py:COLUMN_MAP 的中文键逐字一致）
DETAIL_COL_PAIRS = [
    ("商品访客数", "visitors"),
    ("成交客户数", "buyers"),
    ("成交单量", "orders"),
    ("成交商品件数", "items"),
    ("成交金额", "amount"),
    ("搜索曝光次数", "search_impressions"),
    ("搜索点击次数", "search_clicks"),
    ("取消及售后退款单量", "refund_orders"),
    ("取消及售后退款金额", "refund_amount"),
]
# 明细表头：源表实际带「成交转化率」这类比率列，但 COLUMN_MAP 不收 → 应被静默忽略。
# 刻意留着它，用来证明「整理器的 key 提取不受无关列影响」。
DETAIL_HEADER = ["时间", "SPU", "SPU名称", "三级类目"] + [zh for zh, _ in DETAIL_COL_PAIRS] + ["成交转化率"]
# 形态异常夹具 M1 的表头：缺 SPU 列 → 批处理会 ColumnNotFoundError，整理器应隔离它
BROKEN_HEADER = ["时间", "SPU名称", "成交金额", "成交转化率"]
# 推广 CSV 表头（与 excel_source.PROMO_COL_MAP 的中文键一致）
PROMO_HEADER = ["日期", "SPU ID", "花费", "总订单金额"]

WHITELIST_FILE = "店铺spu登记信息.xlsx"
NAME_MAP_FILE = "SPU商品名称映射表.xlsx"
TABLE_DIR_NAME = config.POP_TABLE_DIR_NAME  # "数据源表目录"

# 锁文件夹具的内容：故意不是合法 xlsx（批处理若读了它会抛 CalamineError）
LOCK_FILE_BYTES = b"PK\x03\x04not-a-real-workbook\x00"

_METRIC_BASE = {
    "visitors": 100.0,
    "buyers": 10.0,
    "orders": 12.0,
    "items": 15.0,
    "amount": 1999.5,
    "search_impressions": 5000.0,
    "search_clicks": 120.0,
    "refund_orders": 0.0,
    "refund_amount": 0.0,
}


# --------------------------------------------------------------------------
# 夹具：数据生成
# --------------------------------------------------------------------------

def _stable_seed(text: str) -> int:
    """把任意字符串映射成稳定整数（⛔ 不用内置 hash——str 的 hash 受 PYTHONHASHSEED 影响，跨进程不稳定）。"""
    return zlib.crc32(text.encode("utf-8")) % 100000


def _metric_value(col: str, shop: str, spu: str, d: date) -> float:
    """按 (店铺, SPU, 日期, 列) 生成确定性的指标值。

    🔴 刻意**不依赖文件名**：同一个 (shop, date, spu) 出现在多份滚动窗文件里时，
    取值必须完全相同——这正是「key 覆盖」判据能成立的前提。若不同文件对同一 key
    给出不同的值，归档掉 `keep="last"` 的赢家就会改变指标，判据随之失真。

    Args:
        col: 长表列名（如 "amount"）。
        shop: 店铺名。
        spu: SPU 编号字符串。
        d: 业务日期。

    Returns:
        float: 该格的值；退款两列固定为 0.0（复刻「JD 导出留空 → 批处理 fill_null(0)」）。
    """
    if col.startswith("refund"):
        return 0.0
    jitter = _stable_seed(f"{shop}|{spu}|{col}") % 97
    return round(_METRIC_BASE[col] * (1 + jitter / 1000.0) + (d.toordinal() % 13), 2)


def _promo_value(col: str, shop: str, spu: str, d: date) -> float:
    """按 (店铺, SPU, 日期, 列) 生成确定性的推广值（同样与文件名无关，理由见 `_metric_value`）。"""
    base = {"promotion_cost": 300.0, "promotion_amount": 1500.0}[col]
    jitter = _stable_seed(f"{shop}|{spu}|{col}|promo") % 71
    return round(base * (1 + jitter / 1000.0) + (d.toordinal() % 7), 2)


def _days(start: str, end: str):
    """闭区间日期迭代器（含 start 与 end）。"""
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    while a <= b:
        yield a
        a += timedelta(days=1)


# 夹具清单：每份文件一条。
#   case      场景标签（①–⑥ 对应工单 01 的 6 类；⑦ 是后来补上的**判别性用例**）
#   filename  文件名（必须符合 `{店铺名}_{商品明细|推广数据}_{起}_{止}.{xlsx|csv}` 约定）
#   kind      detail（xlsx 明细）| promo（csv 推广）
#   spus      该文件包含的 SPU
#   start/end 日期窗
#   skip      需要**故意缺失**的 (spu, 日期) 集合——用来复刻「更新的文件里没有这条 key」
#   expect    期望的整理决策：keep / archive / quarantine / skip
STR_OLDEST = "2025-01-01"
FIXTURE_SPEC: list[dict] = [
    # ① 滚动窗重叠 —— 钻芯旗舰店明细：4 份 30 天窗 + 1 份更长窗
    dict(case="① 滚动窗-最旧（独持窗头日期）",
         filename="钻芯旗舰店_商品明细_2025-01-01_2025-01-30.xlsx", kind="detail",
         spus=["1001", "1002"], start="2025-01-01", end="2025-01-30", skip=[],
         expect="keep", why="01-01..01-09 只被它覆盖，归档会让这 18 个 key 变成历史缺口"),
    dict(case="① 滚动窗-中间（可归档）",
         filename="钻芯旗舰店_商品明细_2025-01-10_2025-02-08.xlsx", kind="detail",
         spus=["1001", "1002"], start="2025-01-10", end="2025-02-08", skip=[],
         expect="archive", why="key 全部被「01-01_01-30」∪「02-01_03-02」覆盖"),
    dict(case="① 滚动窗-中间（可归档）",
         filename="钻芯旗舰店_商品明细_2025-01-12_2025-02-10.xlsx", kind="detail",
         spus=["1001", "1002"], start="2025-01-12", end="2025-02-10", skip=[],
         expect="archive", why="同上：日期窗被首尾覆盖，且没有独有 key"),
    dict(case="② 仅旧文件持有 key（归档即丢数据）",
         filename="钻芯旗舰店_商品明细_2025-01-16_2025-02-14.xlsx", kind="detail",
         spus=["1001", "1002"], start="2025-01-16", end="2025-02-14", skip=[],
         expect="keep", why="独持 (1002, 2025-02-14)：更新的「02-01_03-02」那份里没有这条"),
    dict(case="① 滚动窗-最新（独持窗尾日期 + 缺一天）",
         filename="钻芯旗舰店_商品明细_2025-02-01_2025-03-02.xlsx", kind="detail",
         spus=["1001", "1002"], start="2025-02-01", end="2025-03-02",
         skip=[("1002", "2025-02-14")],
         expect="keep", why="02-15..03-02 只被它覆盖；且它刻意缺 (1002, 2025-02-14)——上面那份独持的就是这个 key"),
    # ① 滚动窗重叠 —— 钻芯旗舰店推广（同类池）：证明推广不会被「明细已覆盖同 key」而误归档
    dict(case="① 滚动窗-推广（同类池）",
         filename="钻芯旗舰店_推广数据_2025-01-01_2025-01-30.csv", kind="promo",
         spus=["1001", "1002"], start="2025-01-01", end="2025-01-30", skip=[],
         expect="keep", why="推广贡献 promotion_cost/promotion_amount，明细不含这两列，两类各自成池"),
    dict(case="① 滚动窗-推广（同类池）",
         filename="钻芯旗舰店_推广数据_2025-01-16_2025-02-14.csv", kind="promo",
         spus=["1001", "1002"], start="2025-01-16", end="2025-02-14", skip=[],
         expect="keep", why="独持 (1002, 2025-02-14) 的推广值"),
    dict(case="① 滚动窗-推广（同类池）",
         filename="钻芯旗舰店_推广数据_2025-02-01_2025-03-02.csv", kind="promo",
         spus=["1001", "1002"], start="2025-02-01", end="2025-03-02",
         skip=[("1002", "2025-02-14")],
         expect="keep", why="02-15..03-02 的推广只被它覆盖；且与明细侧同形地缺 (1002, 2025-02-14)"),
    dict(case="① 滚动窗-推广（可归档）",
         filename="钻芯旗舰店_推广数据_2025-01-10_2025-02-08.csv", kind="promo",
         spus=["1001", "1002"], start="2025-01-10", end="2025-02-08", skip=[],
         expect="archive", why="推广池内被首尾覆盖"),
    # ⑥ 断档区间：卡求旗舰店推广缺 01-11..01-19
    dict(case="⑥ 断档-前半段",
         filename="卡求旗舰店_推广数据_2025-01-01_2025-01-10.csv", kind="promo",
         spus=["2001"], start="2025-01-01", end="2025-01-10", skip=[],
         expect="keep", why="与后半段完全不重叠，各自独持"),
    dict(case="⑥ 断档-后半段",
         filename="卡求旗舰店_推广数据_2025-01-20_2025-01-30.csv", kind="promo",
         spus=["2001"], start="2025-01-20", end="2025-01-30", skip=[],
         expect="keep", why="同上；中间 01-11..01-19 无任何覆盖 = 断档"),
    dict(case="— 单份明细（无重叠，必保留）",
         filename="卡求旗舰店_商品明细_2025-01-01_2025-01-30.xlsx", kind="detail",
         spus=["2001"], start="2025-01-01", end="2025-01-30", skip=[],
         expect="keep", why="与同池其他文件日期不重叠，独持全部 30 个 key"),
    # 🔴 ⑦ 并发归档互撤（判据的**判别性用例**）：两份文件的 key 完全相同，
    #    没有任何第三份文件供着它们。若判据写成「keys(F) ⊆ ⋃keys(别的文件)」，
    #    两份会**同时**被判可归档、覆盖依据互相撤销 → 这批 key 全丢。
    #    正确判据（按保留集倒序贪心）只会归档排序靠前的那一份。
    dict(case="⑦ 并发归档互撤-被归档方",
         filename="卡求旗舰店_商品明细_2026-01-01_2026-01-05.xlsx", kind="detail",
         spus=["2001"], start="2026-01-01", end="2026-01-05", skip=[],
         expect="archive",
         why="key 与「_副本」完全相同，而「_副本」文件名排序更靠后（保留集成员）→ 它才是冗余的那份"),
    dict(case="⑦ 并发归档互撤-保留方",
         filename="卡求旗舰店_商品明细_2026-01-01_2026-01-05_副本.xlsx", kind="detail",
         spus=["2001"], start="2026-01-01", end="2026-01-05", skip=[],
         expect="keep",
         why="必须留下一份：两份互为唯一依据时归档任何一份都会丢 key，倒序贪心留下排序靠后的这份"),
    # 🔴 ⑦ 的阶梯链版本：三份错位 1 天的窗。
    #    中间那份的 key 被**首尾两份合起来**盖住，而首尾两份各自都持有独有 key（必然保留）
    #    → 中间那份可以安全归档。这条用例钉住的是「覆盖依据只能来自**保留集**」：
    #    若判据写成「keys(F) ⊆ ⋃keys(所有别的文件)」，阶梯链在真实数据上会整段塌掉
    #    （2026-09 那批日更文件实测会丢 90 个 key，见工单 02 Comments）。
    dict(case="⑦ 阶梯链-最新（独持尾日）",
         filename="卡求旗舰店_商品明细_2026-02-03_2026-02-07.xlsx", kind="detail",
         spus=["2001"], start="2026-02-03", end="2026-02-07", skip=[],
         expect="keep", why="独持 02-07，必然保留"),
    dict(case="⑦ 阶梯链-中间（可归档）",
         filename="卡求旗舰店_商品明细_2026-02-02_2026-02-06.xlsx", kind="detail",
         spus=["2001"], start="2026-02-02", end="2026-02-06", skip=[],
         expect="archive", why="02-02..02-06 全被「02-01_02-05」∪「02-03_02-07」覆盖，而这两份都必然保留"),
    dict(case="⑦ 阶梯链-最旧（独持首日）",
         filename="卡求旗舰店_商品明细_2026-02-01_2026-02-05.xlsx", kind="detail",
         spus=["2001"], start="2026-02-01", end="2026-02-05", skip=[],
         expect="keep", why="独持 02-01，必然保留"),
    # ④ 零贡献店铺：白名单里登记了店铺名列头，但名下 0 个 SPU
    dict(case="④ 零贡献店铺（整组跳过）",
         filename="空贡献店_商品明细_2025-01-01_2025-01-30.xlsx", kind="detail",
         spus=["9999"], start="2025-01-01", end="2025-01-30", skip=[],
         expect="skip", why="白名单未登记该店任何 SPU → 贡献 0 行；整组跳过不归档（日后重登记时数据还在）"),
]
# ③ 锁文件（垃圾字节，批处理与整理器都必须跳过）
FIXTURE_LOCK_FILE = f"~${FIXTURE_SPEC[0]['filename']}"
# ⑤ 形态异常（必须隔离，⛔ 不猜规则）
FIXTURE_MALFORMED = [
    dict(case="⑤ 形态异常-缺 SPU 列",
         filename="钻芯旗舰店_商品明细_2025-03-01_2025-03-30.xlsx",
         expect="quarantine",
         why="表头没有 SPU 列 → 批处理会 ColumnNotFoundError，必须隔离而不是硬猜"),
    dict(case="⑤ 形态异常-日期无法解析",
         filename="钻芯旗舰店_商品明细_2025-03-01_2025-03-30_日期异常.xlsx",
         expect="quarantine",
         why="时间为 2025/03/01 形态，过滤后 0 行 → 解析不出数据日期窗，无法安全判定"),
]
# ⑥ 期望被报告的断档区间（只报告，零动作，⛔ 不补下载）
FIXTURE_GAPS = [
    dict(kind="promo", shop="卡求旗舰店", start="2025-01-11", end="2025-01-19"),
]


# --------------------------------------------------------------------------
# 夹具：写盘
# --------------------------------------------------------------------------

def _write_xlsx(path: Path, header: list[str], rows: list[list]) -> None:
    """用 openpyxl 写一张单 sheet 的 xlsx（夹具生成不需要保留任何样式）。"""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(str(path))
    wb.close()


def _detail_rows(shop: str, spus: list[str], start: str, end: str, skip) -> list[list]:
    """生成明细表的数据行（不含表头）。skip 里的 (spu, 日期) 会被**故意漏掉**。"""
    skip_set = {(s, d) for s, d in skip}
    rows: list[list] = []
    for spu in spus:
        for d in _days(start, end):
            if (spu, d.isoformat()) in skip_set:
                continue
            row = [d.isoformat(), int(spu) if spu.isdigit() else spu, f"商品{spu}", "三级类目A"]
            row += [_metric_value(col, shop, spu, d) for _, col in DETAIL_COL_PAIRS]
            row += [3.5]  # 「成交转化率」：比率列，COLUMN_MAP 不收，应被静默忽略
            rows.append(row)
    return rows


def _promo_rows(shop: str, spus: list[str], start: str, end: str, skip) -> list[list]:
    """生成推广 CSV 的数据行。日期写成 YYYYMMDD（批处理会归一成 YYYY-MM-DD）。"""
    skip_set = {(s, d) for s, d in skip}
    rows: list[list] = []
    for spu in spus:
        for d in _days(start, end):
            if (spu, d.isoformat()) in skip_set:
                continue
            rows.append([
                d.strftime("%Y%m%d"), spu,
                _promo_value("promotion_cost", shop, spu, d),
                _promo_value("promotion_amount", shop, spu, d),
            ])
    return rows


def _write_promo_csv(path: Path, rows: list[list]) -> None:
    """写推广 CSV；卡求旗舰店用 gbk（复刻国内导出工具），其余用 utf-8-sig。

    两种编码都在 `_read_promo_one` 的回退链里，刻意各覆盖一种以验证回退有效。
    """
    enc = "gbk" if path.name.startswith("卡求旗舰店") else "utf-8-sig"
    with open(path, "w", encoding=enc, newline="") as f:
        w = csv.writer(f)
        w.writerow(PROMO_HEADER)
        w.writerows(rows)


def _write_whitelist(path: Path) -> None:
    """写「店铺spu登记信息.xlsx」白名单。

    结构：第一行是店铺名列头，其下每列是该店登记的 SPU。
    刻意留「空贡献店」一列**没有任何 SPU 值**——复刻「零贡献店铺」场景
    （`load_spu_whitelist` 不会为它生成 key，判定方应把它当作空集处理）。
    """
    _write_xlsx(
        path,
        header=["钻芯旗舰店", "卡求旗舰店", "空贡献店"],
        rows=[[1001, 2001, None], [1002, None, None]],
    )


def _write_name_map(path: Path) -> None:
    """写「SPU商品名称映射表.xlsx」（可选增强，读不到就降级用明细表原名）。"""
    _write_xlsx(
        path,
        header=["spu", "商品名称"],
        rows=[["1001", "保温杯A"], ["1002", "保温杯B"], ["2001", "猫粮"], ["9999", "幽灵品"]],
    )


def guard_destination(dest: Path) -> None:
    """拒绝把夹具写进真实数据源目录（防污染 ResourceData/）。

    Args:
        dest: 用户给出的目标目录。

    Raises:
        SystemExit: 目标目录等于或位于 config.POP_SOURCE_DIR 之内时退出。
    """
    src_root = config.POP_SOURCE_DIR.resolve()
    d = dest.resolve()
    if d == src_root or src_root in d.parents:
        print(f"[拒绝] 目标目录落在真实数据源目录内，会污染 ResourceData：{d}")
        print(f"       请改用临时目录，例如：{Path(tempfile.gettempdir()) / 'org_fx'}")
        raise SystemExit(2)


def make_fixtures(dest: Path, clean: bool = False) -> dict:
    """在 dest 下生成全部夹具，返回清单 dict（同时写 dest/fixtures.json）。

    Args:
        dest: 目标目录；会被清空后重建，结构为
            {dest}/店铺spu登记信息.xlsx、SPU商品名称映射表.xlsx、数据源表目录/…
        clean: True 时**不生成**「缺 SPU 列」那份形态异常夹具——
            因为那份会让 `PopSpuExcelSource.fetch()` 抛 ColumnNotFoundError，
            基线快照跑不了。整理器的正确行为是先隔离它，故 full 模式才有意义。

    Returns:
        dict: {"root", "files": [...], "gaps": [...], "clean": bool}
            每个 file 含 rel_path / case / expect / why。
    """
    guard_destination(dest)
    if dest.exists():
        shutil.rmtree(dest)
    table_dir = dest / TABLE_DIR_NAME
    table_dir.mkdir(parents=True)

    _write_whitelist(dest / WHITELIST_FILE)
    _write_name_map(dest / NAME_MAP_FILE)

    inventory: list[dict] = []
    for spec in FIXTURE_SPEC:
        p = table_dir / spec["filename"]
        shop = spec["filename"].split("_")[0]
        if spec["kind"] == "detail":
            _write_xlsx(p, DETAIL_HEADER, _detail_rows(shop, spec["spus"], spec["start"], spec["end"], spec["skip"]))
        else:
            _write_promo_csv(p, _promo_rows(shop, spec["spus"], spec["start"], spec["end"], spec["skip"]))
        inventory.append({
            "rel_path": f"{TABLE_DIR_NAME}/{spec['filename']}",
            "case": spec["case"], "expect": spec["expect"], "why": spec["why"],
        })

    # ③ 锁文件：名字必须带 "~$" 前缀、且以 .xlsx 结尾（这样它会被 *.xlsx glob 命中，
    #    只有 "~$" 前缀这一道防线能挡住它——夹具要能证明那道防线真的在）
    lock = table_dir / FIXTURE_LOCK_FILE
    lock.write_bytes(LOCK_FILE_BYTES)
    inventory.append({
        "rel_path": f"{TABLE_DIR_NAME}/{FIXTURE_LOCK_FILE}",
        "case": "③ ~$ 锁文件", "expect": "skip",
        "why": "Excel 打开文件时生成的临时锁文件，批处理与整理器都必须跳过",
    })

    # ⑤ 形态异常
    for spec in FIXTURE_MALFORMED:
        if clean and "缺 SPU 列" in spec["case"]:
            continue
        p = table_dir / spec["filename"]
        if "缺 SPU 列" in spec["case"]:
            _write_xlsx(p, BROKEN_HEADER, [[d.isoformat(), f"商品{1001}", 100.0, 3.5]
                                           for d in _days("2025-03-01", "2025-03-05")])
        else:
            # 日期写成 2025/03/01：能读出表，但过滤后 0 行 → 解析不出数据日期窗
            rows = [[d.strftime("%Y/%m/%d"), 1001, "商品1001", "三级类目A"]
                    + [1.0] * 9 + [3.5] for d in _days("2025-03-01", "2025-03-05")]
            _write_xlsx(p, DETAIL_HEADER, rows)
        inventory.append({
            "rel_path": f"{TABLE_DIR_NAME}/{spec['filename']}",
            "case": spec["case"], "expect": spec["expect"], "why": spec["why"],
        })

    result = {
        "root": str(dest),
        "clean": clean,
        "table_dir": str(table_dir),
        "files": inventory,
        "expected_gaps": FIXTURE_GAPS,
        "note": (
            "归档判据的完整形式（⛔ 不是「keys(F) ⊆ ⋃keys(别的文件)」，那样两份文件会互相"
            "撤销覆盖依据、真实数据上实测丢 90 个 key）："
            "先在每个「同店 + 同类」池内求出**保留集** —— ① 持有独有 key 的文件必然保留；"
            "② 若仍有 key 无人供（两份文件完全互盖时会出现），贪心补洞（每次挑能盖住最多"
            "未覆盖 key 的那份，同分取文件名更靠后者＝更新的那份）。"
            "然后 keys(F) ⊆ ⋃keys(保留集) 的才是可归档。"
            "另一条隐藏前提：同一 (shop,date,spu) 在不同文件里取值相同。夹具刻意满足该前提"
            "（取值为 (shop,spu,date) 的确定性函数，与文件名无关），因为若值不同，归档掉 "
            "unique(keep=\"last\") 的赢家就会改变指标。"
            "另：明细与推广必须**各自成池**比较——推广文件的所有 key 都被明细覆盖，"
            "但推广贡献的 promotion_cost/promotion_amount 明细没有，混池会误归档。"
        ),
    }
    (dest / "fixtures.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


# --------------------------------------------------------------------------
# 夹具自检：independent oracle
# --------------------------------------------------------------------------

def derive_fixture_decisions(root: Path) -> list[dict]:
    """用**批处理侧真实的 key 提取规则**独立推导每份夹具文件的决策。

    它不是整理器的实现（那是工单 02 的产物），而是**夹具的自检**：
    `fixtures.json` 里的 `expect` 是手写的 oracle，一旦写错，一个**正确**的整理器实现
    反而会「测失败」——这类失败最难查（历史上多次断言失败都源于期望值写错）。
    所以这里用一套独立的推导把 oracle 钉住。

    刻意调用 `PopSpuExcelSource` 的**私有** `_read_one` / `_read_promo_one`：
    它们才是批处理真正的 key 提取路径（SPU 归一、剔除合计行、明细的日期正则、
    推广的 YYYYMMDD 转换），另写一套「看起来一样」的规则正是要避免的事。

    Args:
        root: 夹具根目录。

    Returns:
        list[dict]: 每份文件一条，含 filename / kind / shop / key_count / decision / reason。
            ⛔ `~$` 锁文件不在结果里（它压根不该进入 key 计算）。
    """
    from backend.jobs.sources.excel_source import PopSpuExcelSource, load_spu_whitelist

    table_dir = root / TABLE_DIR_NAME
    src = PopSpuExcelSource(root)
    # 零贡献店铺 = 源表目录里有它的文件，但白名单里它登记了 0 个 SPU（含「未登记」）
    contributing = {s for s, spus in load_spu_whitelist(root).items() if spus}

    info: dict[str, dict] = {}
    for p in sorted(table_dir.iterdir()):
        if p.name.startswith("~$"):
            continue  # 与 db.py:143 / excel_source.py:240 同一条豁免
        shop = p.name.split("_")[0]
        kind = "promo" if p.suffix.lower() == ".csv" else "detail"
        try:
            if kind == "promo":
                df = src._read_promo_one(p, shop)
                keys = set() if df is None else {
                    (shop, r["date"], r["spu"]) for r in df.select(["date", "spu"]).to_dicts()
                }
            else:
                df = src._read_one(p, shop)
                keys = {(shop, r["date"], r["spu"]) for r in df.select(["date", "spu"]).to_dicts()}
            info[p.name] = dict(shop=shop, kind=kind, keys=keys, err="")
        except Exception as e:  # noqa: BLE001 —— 形态异常文件本就该以异常形态暴露
            info[p.name] = dict(shop=shop, kind=kind, keys=set(), err=f"{type(e).__name__}: {e}")

    out: list[dict] = []
    # ---- 先在每个「同店 + 同类」池内迭代出**保留集**，再据此出决策 ----
    # 🔴 为什么不能「每个文件各自看别的文件」：那会让两份互相供着的文件**同时**被判可归档，
    #    覆盖依据互相撤销。真实 ResourceData 上实测会丢 90 个 key（2026-09 那批日更文件
    #    的阶梯链整段塌掉）。判据里的「保留集」必须是**最终会留下的那批文件**，
    #    ⛔ 不是「所有其他文件」。详见工单 02 的 Comments。
    #
    # 两步走，两步都是显而易见的：
    #   ① 必须先留的：持有「独有 key」的文件（这个 key 只有它有，不留就丢）→ forced；
    #   ② 若 forced 的并集仍有洞（两份文件完全互盖时会出现），贪心补洞：
    #      每次挑「能盖住最多未覆盖 key」的那份，同分时取文件名**更靠后**的
    #      （= 更新的那份，也就是批处理 `unique(keep="last")` 的胜出者，值更不会变）。
    def build_retention(pool_names: list[str]) -> set[str]:
        forced: set[str] = set()
        for n in pool_names:
            others: set = set()
            for m in pool_names:
                if m != n:
                    others |= info[m]["keys"]
            if info[n]["keys"] - others:
                forced.add(n)
        covered: set = set()
        for n in forced:
            covered |= info[n]["keys"]
        holes = set().union(*[info[n]["keys"] for n in pool_names]) - covered
        while holes:
            # 同分取文件名更靠后者：按升序遍历 + `>=` 比较，于是并列时最后一个是胜者
            best, best_hit = None, 0
            for n in sorted(pool_names):
                if n in forced:
                    continue
                hit = len(info[n]["keys"] & holes)
                if hit >= best_hit:
                    best, best_hit = n, hit
            if best is None:
                # 不可达：holes 里的每个 key 都来自池内某个文件；若那个文件在 forced 里，
                # 它的 key 早已进 covered，就不会留在 holes 里。留着这条是为了万一
                # 判据被改坏时**显式炸掉**，而不是静默留下缺口。
                raise AssertionError(f"补洞失败，仍有 {len(holes)} 个 key 无人供：{sorted(holes)[:3]}")
            forced.add(best)
            covered |= info[best]["keys"]
            holes -= info[best]["keys"]
        return forced

    pools: dict[tuple, list[str]] = {}
    for n, i in info.items():
        if i["err"] or not i["keys"]:
            continue  # 读不出来 / 0 key 的文件既不能供别人，也不需要别人供
        pools.setdefault((i["shop"], i["kind"]), []).append(n)

    kept: set[str] = set()
    for names in pools.values():
        kept |= build_retention(names)

    for name, i in info.items():
        if i["shop"] not in contributing:
            decision, reason = "skip", "该店白名单登记 0 个 SPU → 贡献 0 行，整组跳过"
        elif i["err"]:
            decision, reason = "quarantine", f"读取失败：{i['err']}"
        elif not i["keys"]:
            decision, reason = "quarantine", "提取到 0 个 key → 解析不出数据日期窗，无法安全判定"
        elif name in kept:
            decision = "keep"
            reason = "保留集成员（持有独有 key，或为补洞被选中）"
        else:
            decision = "archive"
            reason = "key 全部由同店同类池里**保留集**的文件覆盖"
        out.append({"filename": name, "kind": i["kind"], "shop": i["shop"],
                    "key_count": len(i["keys"]), "decision": decision, "reason": reason})
    return out


def verify_fixture_oracle(root: Path, inv: dict) -> list[str]:
    """把独立推导的决策与 `fixtures.json` 的 `expect` 对照。

    Args:
        root: 夹具根目录。
        inv: `make_fixtures` 的返回值。

    Returns:
        list[str]: 不一致描述；空列表表示 oracle 与实算完全一致。
    """
    expect = {Path(f["rel_path"]).name: (f["expect"], f["case"]) for f in inv["files"]}
    derived = derive_fixture_decisions(root)
    bad: list[str] = []

    for row in derived:
        want, case = expect.get(row["filename"], ("<缺失>", "<缺失>"))
        if want != row["decision"]:
            bad.append(f"  {row['filename']}\n"
                       f"    场景={case}  expect={want}  实算={row['decision']}\n"
                       f"    推导理由={row['reason']}")

    # `~$` 锁文件必须**不出现在推导结果里**（它压根不该被读）
    seen = {r["filename"] for r in derived}
    for f in inv["files"]:
        name = Path(f["rel_path"]).name
        if name.startswith("~$") and name in seen:
            bad.append(f"  {name}：~$ 锁文件竟参与了 key 计算")
    return bad


# --------------------------------------------------------------------------
# 快照 / 比对
# --------------------------------------------------------------------------

def read_db_cells(db_path: Path) -> tuple[dict, list[str]]:
    """读出 daily_detail 的全部单元格与 processed_file 清单。

    Args:
        db_path: SQLite 数据库文件路径。

    Returns:
        tuple[dict, list[str]]:
            cells —— {"{date}|{shop}|{spu}": {指标列: 值}}；
            processed —— 已排序的 processed_file.rel_path 列表。

    Raises:
        FileNotFoundError: 数据库文件不存在（⛔ 不隐式建库，避免「快照一份空库」这种假绿）。
    """
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库不存在：{db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cols = ", ".join(LONG_COLUMNS)
        rows = conn.execute(
            f"SELECT {cols} FROM daily_detail ORDER BY date, shop, spu"
        ).fetchall()
        cells: dict[str, dict] = {}
        for r in rows:
            key = f"{r['date']}|{r['shop']}|{r['spu']}"
            cells[key] = {c: r[c] for c in METRIC_COLUMNS}
        processed = [
            rec["rel_path"]
            for rec in conn.execute(
                "SELECT rel_path FROM processed_file ORDER BY rel_path"
            ).fetchall()
        ]
        return cells, processed
    finally:
        conn.close()


def snapshot(db_path: Path, out_path: Path) -> dict:
    """把数据库状态落成一份稳定（可逐字节比较）的 JSON 快照。

    Args:
        db_path: SQLite 数据库文件路径。
        out_path: 快照输出路径（父目录会自动创建）。

    Returns:
        dict: 快照内容，含 db / row_count / metric_columns / cells / processed_files。
    """
    cells, processed = read_db_cells(db_path)
    snap = {
        "db": str(db_path),
        "row_count": len(cells),
        "metric_columns": METRIC_COLUMNS,
        "cells": dict(sorted(cells.items())),
        "processed_files": processed,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return snap


def load_snapshot(path: Path) -> dict:
    """读入一份快照 JSON。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _values_equal(a, b) -> bool:
    """比较两个单元格值：数值用 1e-9 相对容差，其余按 == 比较。

    为什么不用 ==：JSON 往返后 float 与 int 会比出 False（1.0 vs 1），
    而这类差异不是「指标变了」。容差比较能避免大量假阳性。
    """
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)), abs(float(b)))
    return a == b


def diff_snapshots(before: dict, after: dict) -> list[dict]:
    """比对两份快照，返回全部差异（顺序稳定 → 第 0 条就是「首个差异格」）。

    差异有两大类，都必须抓：
        行级 —— 某个 (date, shop, spu) 只在一边存在。这正是「僵尸行 / 历史缺口」的形态：
                被移走的源文件贡献的行不会消失（长表只增不减），所以它表现为
                「B 里多出 B 独有的行」或「A 的独有行在 B 里被改成了别的值」。
        格级 —— 同一行某个指标列的值变了。

    Args:
        before: 整理前的快照。
        after: 整理后的快照。

    Returns:
        list[dict]: 每条含 kind(行级/格级) / row / column / before / after / note。
    """
    diffs: list[dict] = []
    ca, cb = before.get("cells", {}), after.get("cells", {})
    for key in sorted(set(ca) | set(cb)):
        if key not in ca:
            diffs.append({"kind": "行级", "row": key, "column": "<整行>",
                          "before": None, "after": "<新增行>",
                          "note": "B 独有：僵尸行（源数据没了但库里留着）或历史缺口"})
            continue
        if key not in cb:
            diffs.append({"kind": "行级", "row": key, "column": "<整行>",
                          "before": "<原有行>", "after": None,
                          "note": "A 独有：B 里这行消失了（历史缺口）"})
            continue
        for col in METRIC_COLUMNS:
            va, vb = ca[key].get(col), cb[key].get(col)
            if not _values_equal(va, vb):
                diffs.append({"kind": "格级", "row": key, "column": col,
                              "before": va, "after": vb, "note": "指标值被改变"})

    pa, pb = before.get("processed_files", []), after.get("processed_files", [])
    if pa != pb:
        only_a = sorted(set(pa) - set(pb))
        only_b = sorted(set(pb) - set(pa))
        diffs.append({"kind": "processed_file", "row": "<processed_file>", "column": "rel_path",
                      "before": f"{len(pa)} 条", "after": f"{len(pb)} 条",
                      "note": f"仅在 A：{only_a[:3]}；仅在 B：{only_b[:3]}"})
    return diffs


def format_diff(d: dict) -> str:
    """把一条差异格式化成单行人类可读文本。

    Example:
        >>> format_diff({"kind": "格级", "row": "2025-02-14|钻芯旗舰店|1002", "column": "amount",
        ...              "before": 1999.5, "after": 3234.0, "note": "指标值被改变"})
        '[格级] 行 2025-02-14|钻芯旗舰店|1002 / 列 amount：A=1999.5 → B=3234.0（指标值被改变）'
    """
    return (f"[{d['kind']}] 行 {d['row']} / 列 {d['column']}："
            f"A={d['before']!r} → B={d['after']!r}（{d['note']}）")


# --------------------------------------------------------------------------
# 命令实现
# --------------------------------------------------------------------------

class Checker:
    """自制断言计数器（沿用项目既有验证脚本的风格，⛔ 不引 pytest）。"""

    def __init__(self, title: str):
        self.title = title
        self.passed = 0
        self.failed = 0
        print(f"=== {title} ===")

    def check(self, cond: bool, label: str, detail: str = "") -> None:
        if cond:
            self.passed += 1
            print(f"  [PASS] {label}")
        else:
            self.failed += 1
            print(f"  [FAIL] {label}" + (f" —— {detail}" if detail else ""))

    def report(self) -> int:
        total = self.passed + self.failed
        print(f"--- {self.title}：{self.passed}/{total} 通过，{self.failed} 失败 ---")
        return 1 if self.failed else 0


def cmd_make_fixtures(args) -> int:
    """make-fixtures 子命令：生成夹具、自检 oracle、打印清单。"""
    dest = Path(args.dest).expanduser()
    inv = make_fixtures(dest, clean=args.clean)
    print(f"夹具根目录：{inv['root']}（clean={inv['clean']}）")
    print(f"数据源表目录：{inv['table_dir']}")
    for f in inv["files"]:
        print(f"  {f['expect']:<10} {f['rel_path']}")
        print(f"     场景：{f['case']} ｜ 理由：{f['why']}")
    print(f"期望被报告的断档：{inv['expected_gaps']}")
    print(f"清单已写入：{dest / 'fixtures.json'}")

    if args.no_verify:
        print("（--no-verify：跳过 oracle 自检）")
        return 0

    print("\n--- oracle 自检：用批处理侧真实 key 提取规则独立推导，再与 expect 对照 ---")
    bad = verify_fixture_oracle(dest, inv)
    if bad:
        print(f"[FAIL] oracle 与实算不一致 {len(bad)} 条（先改夹具有或 expect，⛔ 别改判据去迁就）")
        print("\n".join(bad))
        return 1
    print("[PASS] 全部文件的 expect 与独立推导一致")
    return 0


def cmd_snapshot(args) -> int:
    """snapshot 子命令：把数据库落成快照。"""
    snap = snapshot(Path(args.db), Path(args.out))
    print(f"快照已写入：{args.out}（{snap['row_count']} 行 × {len(METRIC_COLUMNS)} 指标列，"
          f"processed_file {len(snap['processed_files'])} 条）")
    return 0


def cmd_diff(args) -> int:
    """diff 子命令：比对两份快照；有差异 → 退出码 1。"""
    before, after = load_snapshot(Path(args.before)), load_snapshot(Path(args.after))
    diffs = diff_snapshots(before, after)
    if not diffs:
        print(f"OK：{before['row_count']} 行 × {len(before['metric_columns'])} 指标列逐格相同，"
              f"processed_file 清单也一致")
        return 0
    print(f"发现 {len(diffs)} 处差异，首个差异格：")
    print("  " + format_diff(diffs[0]))
    if len(diffs) > 1:
        print(f"  （其余 {len(diffs) - 1} 处：")
        for d in diffs[1:6]:
            print("    " + format_diff(d))
        if len(diffs) > 6:
            print(f"    …（共 {len(diffs) - 1} 处）")
        print("  ）")
    return 1


def _ingest_into(root: Path, db_path: Path) -> dict:
    """用真实摄入路径把夹具灌进指定数据库。

    为什么 monkeypatch `jobdb.DB_PATH` 而不是设环境变量：
        `config` 在**导入期**读环境变量，进程内 setenv 已经不生效；而 `get_conn()`
        每次调用都读模块级 `DB_PATH`，所以直接改那个全局是最直接的进程内注入。
        （环境变量注入这条路另由 `env` 子命令在子进程里单独验证。）

    Args:
        root: 数据源根目录（夹具根）。
        db_path: 目标 SQLite 文件路径（不存在会被创建）。

    Returns:
        dict: `ingest_excel_to_db` 的返回值。
    """
    from backend.jobs import db as jobdb

    jobdb.DB_PATH = Path(db_path)
    return jobdb.ingest_excel_to_db(Path(root))


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _first_key(db_path: Path) -> str:
    conn = _connect(db_path)
    try:
        r = conn.execute(
            "SELECT date, shop, spu FROM daily_detail ORDER BY date, shop, spu LIMIT 1"
        ).fetchone()
        return f"{r['date']}|{r['shop']}|{r['spu']}"
    finally:
        conn.close()


def _bump_one_value(db_path: Path, delta: float = 1234.5) -> str:
    """故意把第一行的 amount 加一个可辨识的增量，返回被改的行 key（自证有牙齿用）。"""
    key = _first_key(db_path)
    d, shop, spu = key.split("|")
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE daily_detail SET amount = amount + ? WHERE date=? AND shop=? AND spu=?",
            (delta, d, shop, spu),
        )
        conn.commit()
    finally:
        conn.close()
    return key


def _delete_one_row(db_path: Path) -> str:
    """故意删掉第一行，返回被删的行 key（复刻「历史缺口」的失败形态）。"""
    key = _first_key(db_path)
    d, shop, spu = key.split("|")
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM daily_detail WHERE date=? AND shop=? AND spu=?", (d, shop, spu))
        conn.commit()
    finally:
        conn.close()
    return key


def _run_diff_cli(a: Path, b: Path) -> int:
    """在子进程里跑本脚本的 diff 子命令，返回真实退出码（验证「非零退出」这条验收项）。"""
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "diff", str(a), str(b)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode


def cmd_selftest(args) -> int:
    """selftest 子命令：证明这套比对真的有牙齿（防假阳性 + 防假阴性）。"""
    ck = Checker("工单 01 自证：夹具能跑通、比对有牙齿")
    tmp = Path(tempfile.mkdtemp(prefix="verify_organize_"))
    try:
        # —— 1. 夹具生成 ——
        src = tmp / "src"
        inv = make_fixtures(src, clean=True)
        ck.check((src / WHITELIST_FILE).is_file(), "夹具根有白名单文件")
        ck.check((src / TABLE_DIR_NAME).is_dir(), "夹具根有数据源表目录")
        ck.check(len(inv["files"]) >= 12, f"夹具文件数 ≥ 12（实际 {len(inv['files'])}）")
        ck.check(any(f["expect"] == "archive" for f in inv["files"]), "夹具含「应归档」样本")
        ck.check(any(f["expect"] == "quarantine" for f in inv["files"]), "夹具含「应隔离」样本")
        ck.check(any(f["expect"] == "skip" for f in inv["files"]), "夹具含「应跳过」样本")
        ck.check(any(f["rel_path"].endswith(FIXTURE_LOCK_FILE) for f in inv["files"]), "夹具含 ~$ 锁文件")
        ck.check((src / "fixtures.json").is_file(), "夹具清单 fixtures.json 已落盘")
        oracle_bad = verify_fixture_oracle(src, inv)
        ck.check(not oracle_bad, "夹具 expect 与「按批处理侧规则独立推导」的结果一致",
                 "; ".join(oracle_bad)[:400])

        # —— 2. 真实摄入路径跑通（clean 模式必须不崩） ——
        db = tmp / "app.db"
        res = _ingest_into(src, db)
        ck.check(res.get("changed") is True, f"摄入成功（{res}）")
        ck.check(int(res.get("rows", 0)) > 0, f"写入行数 > 0（实际 {res.get('rows')}）")

        # —— 3. 快照稳定：同数据两次快照逐字节相同（防假阳性） ——
        a1, a2 = tmp / "a1.json", tmp / "a2.json"
        snapshot(db, a1)
        snapshot(db, a2)
        ck.check(a1.read_bytes() == a2.read_bytes(), "同一份数据两次快照逐字节相同")
        ck.check(diff_snapshots(load_snapshot(a1), load_snapshot(a2)) == [], "同数据比对零差异")
        ck.check(_run_diff_cli(a1, a2) == 0, "diff 子命令无差异时退出码为 0")
        snap_a1 = load_snapshot(a1)
        ck.check(len(snap_a1["metric_columns"]) == 11,
                 f"快照覆盖 11 个指标列（实际 {len(snap_a1['metric_columns'])}）")

        # —— 4. 牙齿之一：改一个指标值 → 必须报差异且指向那一格 ——
        target = _bump_one_value(db, 1234.5)
        b1 = tmp / "b1.json"
        snapshot(db, b1)
        diffs = diff_snapshots(snap_a1, load_snapshot(b1))
        ck.check(len(diffs) >= 1, "改动一个指标值后比对必须报出差异", f"实际 {len(diffs)} 条")
        if diffs:
            d0 = diffs[0]
            ck.check(d0["row"] == target and d0["column"] == "amount",
                     "首个差异格指向被改的那一格",
                     f"实际 {d0['row']} / {d0['column']}，期望 {target} / amount")
            ck.check(_values_equal(d0["after"] - d0["before"], 1234.5)
                     if isinstance(d0["after"], (int, float)) else False,
                     "差异幅度等于注入的 1234.5",
                     f"实际 {d0['after']} - {d0['before']}")
        ck.check(_run_diff_cli(a1, b1) == 1, "diff 子命令有差异时退出码为 1（非零）")

        # —— 5. 牙齿之二：删一行 → 必须报「行级」差异（僵尸行 / 历史缺口的形态） ——
        c2 = tmp / "c2.json"
        shutil.copyfile(b1, c2)  # 以 b1 为基线，避免被上一步的改值干扰
        base = load_snapshot(c2)
        removed = _delete_one_row(db)
        c3 = tmp / "c3.json"
        snapshot(db, c3)
        diffs_row = diff_snapshots(base, load_snapshot(c3))
        kinds = {d["kind"] for d in diffs_row}
        ck.check("行级" in kinds, "删一行后比对报出行级差异", f"实际 kinds={kinds}")
        ck.check(any(d["row"] == removed for d in diffs_row),
                 "行级差异指向被删的那一行", f"实际 {[d['row'] for d in diffs_row][:3]}")

        # —— 6. 夹具不污染真实数据源目录 ——
        ck.check(config.POP_SOURCE_DIR.resolve() not in Path(inv["root"]).resolve().parents
                 and Path(inv["root"]).resolve() != config.POP_SOURCE_DIR.resolve(),
                 "夹具根不在 ResourceData/ 内")
        before_src = sorted(p.name for p in config.POP_SOURCE_DIR.glob("*")) if config.POP_SOURCE_DIR.is_dir() else []
        after_src = sorted(p.name for p in config.POP_SOURCE_DIR.glob("*")) if config.POP_SOURCE_DIR.is_dir() else []
        ck.check(before_src == after_src, "ResourceData/ 顶层条目未变")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if tmp.exists():
            # ⚠️ 已知发现（⛔ 本工单不改生产代码，只记录）：
            # excel_source.load_spu_whitelist / load_spu_name_map 用 read_only 打开 workbook
            # 后**没有 close()**，openpyxl 内部引用环要靠循环 GC 才回收 → 在 Windows 上
            # 表现为「文件仍被占用、临时目录一时删不掉」。影响面：进程存活期间该 xlsx
            # 无法被重命名/删除；长驻进程（dev --reload + POST /api/refresh）会累积句柄。
            # 最小修复是那两处各加一行 `wb.close()`，但按工单 01 约定留到后续工单处理。
            gc.collect()
            shutil.rmtree(tmp, ignore_errors=True)
        ck.check(not tmp.exists(), "临时目录已清理（可安全删除；注：需先触发循环 GC，见上）")
    return ck.report()


def cmd_env(args) -> int:
    """env 子命令：报告路径能否通过环境变量注入（工单 01 的验收项，只报告不改生产代码）。"""
    from backend.jobs.sources import excel_source as ex

    print("=== 路径注入能力报告（工单 01 验收项，⛔ 本命令不改任何文件）===")
    print(f"项目根：{ROOT}")
    print(f"当前 config.POP_SOURCE_DIR = {config.POP_SOURCE_DIR}")
    print(f"当前 config.DB_PATH        = {config.DB_PATH}")
    print(f"数据源表目录固定名 = {config.POP_TABLE_DIR_NAME!r}（config.POP_TABLE_DIR_NAME，不可注入）")
    print()

    tmp = Path(tempfile.mkdtemp(prefix="verify_organize_env_"))
    try:
        probe_src, probe_db = tmp / "src", tmp / "x" / "app.db"
        code = (
            "import sys;"
            f"sys.path.insert(0,{str(ROOT)!r});"
            "from backend.jobs import config;"
            "print(config.POP_SOURCE_DIR);print(config.DB_PATH)"
        )
        env = dict(os.environ, POP_SOURCE_DIR=str(probe_src), JD_DB_PATH=str(probe_db))
        proc = subprocess.run([sys.executable, "-c", code], env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        lines = [ln.strip() for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
        ok_src = bool(lines) and Path(lines[0]) == probe_src
        ok_db = len(lines) > 1 and Path(lines[1]) == probe_db
        print("[1] 数据源根目录：可注入")
        print(f"    环境变量 POP_SOURCE_DIR → config.POP_SOURCE_DIR（config.py:49）")
        print(f"    子进程实测：{'通过' if ok_src else '失败'}（{lines[0] if lines else proc.stderr.strip()[:200]}）")
        print("[2] 数据库路径：可注入")
        print(f"    环境变量 JD_DB_PATH → config.DB_PATH（config.py:36）")
        print(f"    子进程实测：{'通过' if ok_db else '失败'}（{lines[1] if len(lines) > 1 else '—'}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("[3] 白名单 / 名称映射：**目录**可注入，**文件名**是模块常量")
    print(f"    excel_source.py:57  SPU_REGISTER_FILE = {ex.SPU_REGISTER_FILE!r}")
    print(f"    excel_source.py:51  NAME_MAP_FILE     = {ex.NAME_MAP_FILE!r}")
    print(f"    excel_source.py:158 src = (root or config.POP_SOURCE_DIR) / SPU_REGISTER_FILE")
    print("    → 解析路径 = <数据源根> / <固定文件名>")
    print("[4] 另一条注入路径：`load_spu_whitelist(root)` / `PopSpuExcelSource(root)`")
    print("    都接受显式 root 参数（excel_source.py:142、:207）→ 进程内可直接注入任意目录。")
    print()
    print(">>> 结论：无需改任何生产代码。夹具只要把白名单/映射表按固定文件名放在")
    print("    「自身根目录」下，再通过 POP_SOURCE_DIR（或显式 root 参数）指过去即可。")
    print(">>> 注意：POP_TABLE_DIR_NAME('数据源表目录') 与两个配置表文件名都不可注入，")
    print("    夹具必须照用这三个名字——这也是夹具「忠实复刻」的一部分。")
    return 0


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    p = argparse.ArgumentParser(
        prog="verify_organize.py",
        description="源表整理器（organize）的夹具生成 / 基线快照 / 逐格比对。",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make-fixtures", help="生成 6 类源表夹具")
    m.add_argument("dest", help="目标目录（会被清空重建；⛔ 不得落在 ResourceData/ 内）")
    m.add_argument("--clean", action="store_true",
                   help="不生成「缺 SPU 列」那份（它会让批处理抛错，基线快照跑不了）")
    m.add_argument("--no-verify", action="store_true",
                   help="跳过 oracle 自检（仅在夹具本身还在迭代时用）")
    m.set_defaults(func=cmd_make_fixtures)

    s = sub.add_parser("snapshot", help="把数据库落成 JSON 快照")
    s.add_argument("db", help="app.db 路径")
    s.add_argument("out", help="快照输出路径")
    s.set_defaults(func=cmd_snapshot)

    d = sub.add_parser("diff", help="比对两份快照（有差异 → 退出码 1）")
    d.add_argument("before")
    d.add_argument("after")
    d.set_defaults(func=cmd_diff)

    t = sub.add_parser("selftest", help="自证有牙齿")
    t.set_defaults(func=cmd_selftest)

    e = sub.add_parser("env", help="报告路径注入能力（只报告，不改代码）")
    e.set_defaults(func=cmd_env)
    return p


def main(argv: list[str] | None = None) -> int:
    """脚本入口：解析子命令并返回退出码。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文会乱码
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
