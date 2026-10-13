# -*- coding: utf-8 -*-
"""源表整理器（organize）——判定层：按 **key 覆盖** 产出每个源文件的「保留 / 归档 / 隔离 / 跳过」决策。

为什么需要它（What & Why）：
    源表目录按 30 天滚动窗反复下载——今天下到的与昨天下的有 29 天重叠，目录持续膨胀。
    但「删掉一个文件」在这个仓库里的后果是**无声的**：长表只增不减
    （`db.py` 只有 `INSERT OR REPLACE`，全项目没有 `DELETE FROM daily_detail`），
    被移走文件贡献的行不会消失，而是永久冻结在库里——要么成为历史缺口，
    要么停止更新、静默陈旧，全程没有任何报错（详见 `verify_organize.py` 开头的说明）。
    所以判定标准必须是「这份文件贡献的每个 `(店铺, 日期, SPU)` 都还有**最终保留下来**
    的文件供着」，也就是 **key 覆盖**；而不是「日期窗看起来被盖住了」，更不是
    「别的任意文件供着」（后者会让两份互相供着的文件同时被归档，见 `build_retention`）。

为什么判定是无 I/O 的纯函数：
    判定是这个功能里唯一「判错就丢数据」的部分，它必须能被直接 import 后断言，
    而不必先摆一整套目录 + 数据库。`decide()` 因此只吃「已经算好的 key 集合」，
    完全不碰文件系统；读盘与 key 提取放在薄壳 `collect_file_keys()` 里，且
    **复用批处理侧的真实规则**（`PopSpuExcelSource._read_one` / `._read_promo_one`）。
    ⛔ 绝不另写第二套「看起来一样」的 key 提取——两侧规则一旦漂移，
    「整理后指标逐格不变」这个前提就没了（`verify_organize_decision.py` 的
    跨层不变量就是用来钉住这件事的）。

口径（与 `docs/adr` 一致）：
    · 判据只在「**同店铺 + 同类**（明细 / 推广）」子池内比较。混池会让**所有推广文件**
      被误归档：推广的每个 key 都能在明细里找到，但推广贡献的 `promotion_cost` /
      `promotion_amount` 明细根本没有，归档即静默丢两列指标。
    · 判据取**全量** SPU，不受白名单过滤影响（白名单外的店铺日后被重新登记时数据还在）。
    · 白名单**只**用来识别「零贡献店铺」（登记 0 个 SPU）；命中的店铺整组跳过、不归档。
    · 日期窗**不参与**判定（预筛只能减少读取量，不得决定归档）。

    · 形态异常的源文件（读不出 / 提不出 key）**不被猜规则处理**，而是移进**隔离区**
      `{root}/_quarantine/`（同样在源表目录之外）。隔离区与归档区**各自独立**的清单，
      且⛔ 隔离区**永不参与**归档区的 60 天清理（它等的不是清扫机，是人）。
    · 被隔离文件若持有**独有 key**（该 key 在同店同类的**保留集**里找不到来源），
      那批数据会变成**历史缺口或静默陈旧**——触发 **ERROR 级哨兵告警**。
      ⚠️ 哨兵**只报警不阻断**：该批批处理照常完成入库（ADR-0005）。

本模块有两层：
    · **判定层**（`decide` / `build_retention` / `collect_file_keys`）——无 I/O 纯判定；
    · **执行层**（`organize_files` / `rollback` + 隔离搬移 + 五段报告 + 60 天回收站清理）——
      真搬文件、写 write-ahead 清单、可回滚；每跑一次产出一份**五段报告**
      （归档 / 隔离 / 断档 / 哨兵告警 / 回收站清理，见 `render_report`），人读 md + 机读 json
      内容一致；归档批次满 60 天（`RETENTION_DAYS`，按归档日期起算）由 `cleanup_expired`
      逐个送进系统**回收站**（⛔ 不是硬删），回收站不可用时降级为「只报告」。
    ⚠️ 执行层的安全默认是 **dry-run**（只出清单不搬文件）：首次归档是整个设计里规模最大的
    一次不可逆动作，必须先经人工过目（ADR-0002 / Q13）。只有显式 `--mode on`（或
    `JD_ORGANIZE=on`）才真正搬文件；`off` 则整段跳过。

哨兵的 key 从哪来（为什么需要一个「诊断探针」）：
    判定层把「提不出 key」的文件判为 `quarantine`——也就是说，**被隔离文件自己 0 个 key**
    （要么读不出，要么日期形态不合规被过滤成 0 行）。若直接把它的 0 个 key 拿去求差，
    哨兵将永远沉默、形同虚设。所以哨兵另走一条**只读的宽松探针**（`_probe_file_keys`）：
    它用与生产**同一批声明列名**（`COLUMN_MAP` / `PROMO_COL_MAP`，⛔ 不猜列位置）把能读到的
    行尽量还原出 key，**只为估计风险**。这与「⛔ 不猜规则」并不冲突——探针不参与入库、
    不改任何文件，而且它**偏保守地报**（宁可误报，不可漏报）：漏报会把「唯一数据源被隔离」
    变成无声的数据缺口，误报只是多一条供人一眼扫过的告警。

用法：
    >>> from backend.jobs.organize import decide
    >>> files = {"a.xlsx": {"shop": "S", "kind": "detail", "keys": {("S", "2025-01-01", "1")}}}
    >>> decide(files, {"S"})[0]["decision"]
    'keep'
    >>> decide(files, set())[0]["decision"]      # 店铺不在贡献集里 → 整组跳过
    'skip'
    >>> decide(files, {"S", "T"})[0]["key_count"]
    1

    # 命令行（默认 dry-run，绝不改任何文件）：
    #   python -m backend.jobs.organize --source ResourceData --out decisions.json
    # 真搬文件（⚠️ 需人工过目后显式开启）：
    #   python -m backend.jobs.organize --source ResourceData --mode on
    # 回滚一个批次 / 单个文件：
    #   python -m backend.jobs.organize --source ResourceData --rollback 2026-10-12
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config

# ---- 四态决策 ----
KEEP = "keep"              # 保留：属于同店同类池的**保留集**（独持 key，或为补洞被选中）
ARCHIVE = "archive"        # 归档：key 全部被同店同类池的**保留集**覆盖
QUARANTINE = "quarantine"  # 隔离：读不出来 / 提不出 key，无法安全判定
SKIP = "skip"              # 跳过：零贡献店铺（白名单登记 0 个 SPU），整组跳过

# LOCK_PREFIX：Excel 打开文件时生成的临时锁文件前缀。
# 与 `db.py:_collect_source_files`、`excel_source.py:fetch` 是同一条豁免，
# 三处必须一致：锁文件不是有效工作簿，读它会抛错。
LOCK_PREFIX = "~$"

# 决策清单要给人看，key 样例只留前 N 条（全量 key 意义不大，还会把清单撑爆）
SAMPLE_LIMIT = 5
COVER_SAMPLE_LIMIT = 3

KIND_DETAIL = "detail"
KIND_PROMO = "promo"


def load_contributing_shops(root: Path | None = None) -> set[str]:
    """读出「贡献店铺」集合 = 白名单里登记了 ≥1 个 SPU 的店铺。

    白名单**只**用于这件事：识别零贡献店铺（登记 0 个 SPU，或压根没被登记），
    它们的文件整组跳过、不归档——日后白名单改回来时数据还在。
    白名单**绝不**参与归档判定（判据取全量 SPU）。

    Args:
        root: 数据源根目录；可选，默认 `config.POP_SOURCE_DIR`。

    Returns:
        set[str]: 店铺名集合。白名单文件缺失 / 读取异常时
            `load_spu_whitelist` 返回空 dict → 结果是空集合
            （此时所有店铺都算「非贡献」，与「白名单缺失 = 不过滤」这一
            批处理侧语义相反，故调用方不应在无白名单时执行归档动作）。

    Example:
        >>> load_contributing_shops(Path("ResourceData"))  # doctest: +SKIP
        {'钻芯旗舰店', '卡求旗舰店'}
    """
    from .sources.excel_source import load_spu_whitelist  # 延迟导入：纯判定无需承担 polars 的导入成本

    return {shop for shop, spus in load_spu_whitelist(root).items() if spus}


def _iter_source_files(table_dir: Path) -> list[Path]:
    """枚举批处理**真正会读**的源文件，与 `db.py:_collect_source_files` 保持同一套规则。

    ⛔ 不枚举「目录里的所有东西」：批处理只 glob `*.xlsx` 与 `*推广数据*.csv`，
    别的文件（`README.md`、`.tmp`…）既不贡献数据也不该被「隔离」——把它们列进决策清单
    只会产出噪音告警。两侧枚举范围一致，才能保证「决策清单覆盖的正是会进库的文件集」。

    Args:
        table_dir: 数据源表目录（`{root}/数据源表目录`）。

    Returns:
        list[Path]: 已按文件名排序的源文件路径（`~$` 锁文件已剔除）。
    """
    out = [p for p in table_dir.glob("*.xlsx") if not p.name.startswith(LOCK_PREFIX)]
    out += [p for p in table_dir.glob("*推广数据*.csv") if not p.name.startswith(LOCK_PREFIX)]
    return sorted(out, key=lambda p: p.name)


def collect_file_keys(root: Path | None = None) -> dict[str, dict]:
    """扫描数据源表目录，为每个源文件算出它的 `(店铺, 日期, SPU)` key 集合。

    这是模块里**唯一**的 key 提取入口，它直接调用批处理侧的私有读取路径
    （`PopSpuExcelSource._read_one` / `._read_promo_one`）——那才是生产真正走的路径
    （SPU 归一 `_norm_spu`、剔除 `合计`/空 SPU 汇总行、明细的日期正则、
    推广的 `YYYYMMDD → YYYY-MM-DD`）。另写一套「看起来一样」的规则正是要避免的事。

    读取失败**不抛异常**：形态异常文件本就该以「提不出 key」的形态暴露出来，
    由 `decide()` 判为 `quarantine`（⛔ 不猜规则、不猜列位置）。

    Args:
        root: 数据源根目录；可选，默认 `config.POP_SOURCE_DIR`。

    Returns:
        dict[str, dict]: 文件名 -> {"shop": str, "kind": "detail"|"promo",
            "keys": set[tuple[shop, date, spu]], "error": str}。
            `error` 为空串表示读取成功；非空表示读取抛异常（此时 `keys` 必为空集，
            空集也正是 `decide()` 判 `quarantine` 的依据）。
            `~$` 锁文件**不出现在结果里**（压根不参与 key 计算）。

    Raises:
        FileNotFoundError: 数据源表目录不存在（继续跑只会产出一份空决策清单，
            不如显式报错让人去检查数据源）。

    Example:
        >>> files = collect_file_keys(Path("ResourceData"))          # doctest: +SKIP
        >>> sorted(files)[0]                                          # doctest: +SKIP
        '卡求旗舰店_商品明细_2026-08-19_2026-09-17.xlsx'
        >>> len(next(iter(files.values()))["keys"]) > 0                # doctest: +SKIP
        True
    """
    from .sources.excel_source import PopSpuExcelSource

    root = Path(root) if root is not None else config.POP_SOURCE_DIR
    table_dir = root / config.POP_TABLE_DIR_NAME
    if not table_dir.is_dir():
        raise FileNotFoundError(f"数据源表目录不存在：{table_dir}")

    src = PopSpuExcelSource(root)
    files: dict[str, dict] = {}
    for path in _iter_source_files(table_dir):
        # 文件名约定「{店铺名}_{商品明细|推广数据}_...」，取第一段作店铺名（与批处理一致）
        shop = path.name.split("_")[0]
        kind = KIND_PROMO if path.suffix.lower() == ".csv" else KIND_DETAIL
        entry: dict = {"shop": shop, "kind": kind, "keys": set(), "error": ""}
        try:
            if kind == KIND_PROMO:
                df = src._read_promo_one(path, shop)
                # `_read_promo_one` 对空文件 / 无登记列 / 编码全读不了都返回 None
                keys = set() if df is None else {
                    (shop, r["date"], r["spu"])
                    for r in df.select(["date", "spu"]).to_dicts()
                }
            else:
                df = src._read_one(path, shop)
                keys = {(shop, r["date"], r["spu"])
                        for r in df.select(["date", "spu"]).to_dicts()}
            entry["keys"] = keys
        except Exception as e:  # noqa: BLE001 —— 形态异常文件本就该以异常形态暴露
            entry["error"] = f"{type(e).__name__}: {e}"
        files[path.name] = entry
    return files


def _fmt_key(key: tuple) -> str:
    """把 `(shop, date, spu)` 元组格式化成给人看的字符串。"""
    return f"({key[0]}, {key[1]}, {key[2]})"


def _quarantine_reason(error: str) -> str:
    """拼出隔离理由：有异常就给异常，没有就说明是「提不出 key」。"""
    if error:
        return f"读取失败：{error} → 形态异常，隔离等人处理（⛔ 不猜规则）"
    return "提取到 0 个 key → 解析不出数据日期窗，无法安全判定"


def _cover_samples(keys_sorted: list[tuple], retained_others: dict[str, set]) -> list[dict]:
    """为归档文件抽几条「覆盖来源」样例：这个 key 由**保留集**里哪些文件供着。

    Args:
        keys_sorted: 当前文件的 key（已排序）。
        retained_others: 同店同类池里**属于保留集**且非自己的文件 -> 其 key 集合。
            ⛔ 刻意不用「所有别的文件」——那正是会互相撤销覆盖依据的错误依据。

    Returns:
        list[dict]: 最多 `COVER_SAMPLE_LIMIT` 条，每条 `{"key": [shop,date,spu], "sources": [...]}`。
            清单要能回答「凭什么说它能归档」，所以样例必须带上具体来源文件名。
    """
    samples: list[dict] = []
    for key in keys_sorted[:COVER_SAMPLE_LIMIT]:
        samples.append({
            "key": list(key),
            "sources": [name for name in sorted(retained_others) if key in retained_others[name]],
        })
    return samples


def build_retention(pool_names: list[str], files: dict[str, dict]) -> set[str]:
    """在一个「同店 + 同类」池内求出**最终会保留下来**的文件名集合（两步法，都显而易见）。

    为什么必须有这个函数（而不是「每份文件各自看别的文件」）：
        若把归档依据取成「`keys(F) ⊆ ⋃keys(别的文件)`」，两份互相供着的文件会**同时**
        被判可归档——A 因为 B 被判可归档、B 因为 A 被判可归档，覆盖依据互相撤销，
        两份一起搬走，这批 key 全丢。真实 `ResourceData` 上这不是理论风险：
        2026-08 下旬～10 月上旬那批日更文件形成**阶梯链**，实测整段塌掉、永久丢
        90 个 `(date, shop, spu)`。判据里的「保留集」必须是**最终会留下来的那批文件**，
        ⛔ 不是「所有其他文件」。

    两步（确定性、可复算）：
        ① forced（必然保留）＝持有**独有 key** 的文件——该 key 池内别的文件都不持有，
           不留就丢。
        ② 补洞：`holes = ⋃(池内所有 keys) − ⋃(forced 的 keys)`。两份文件完全互盖时
           holes 非空；每次挑「能盖住最多 holes 中 key」的那份并入保留集，重复到 holes
           为空。🔴 **同分时取文件名更靠后**的那份（＝更新的那份，即批处理
           `unique(keep="last")` 的胜出者，归档后值更不容易变）。

    收敛性：holes 里每个 key 都来自池内某个文件；若那个文件在 forced 里，它的 key 早已
    进 covered，不会留在 holes。故每轮必有某份非 forced 文件能盖住至少 1 个 hole。

    Args:
        pool_names: 同一「同店 + 同类」池内的文件名（调用方保证都已可读、key 非空）。
        files: 全量文件表（形状同 `decide` 的入参）。

    Returns:
        set[str]: 该池的保留集文件名（`pool_names` 的子集）。

    Raises:
        RuntimeError: 补洞时挑不出任何能盖住 hole 的文件（不可达）→ **显式抛错**，
            ⛔ 绝不静默留下缺口。

    Example:
        >>> same = {("S", "2026-01-01", "1")}
        >>> files = {n: {"shop": "S", "kind": "detail", "keys": set(same)}
        ...          for n in ("a.xlsx", "b.xlsx")}
        >>> sorted(build_retention(["a.xlsx", "b.xlsx"], files))   # 两份互盖 → 留文件名靠后者
        ['b.xlsx']
    """
    keys_of = {n: files[n]["keys"] for n in pool_names}

    # ① forced：持有独有 key 的文件（该 key 池内别的文件都不持有）
    forced: set[str] = set()
    for n in pool_names:
        others: set = set()
        for m in pool_names:
            if m != n:
                others |= keys_of[m]
        if keys_of[n] - others:
            forced.add(n)

    # ② 补洞：forced 盖不满全集时，贪心加文件直到 holes 为空
    covered: set = set()
    for n in forced:
        covered |= keys_of[n]
    holes: set = set()
    for n in pool_names:
        holes |= keys_of[n]
    holes -= covered
    while holes:
        # 同分取文件名更靠后者：按升序遍历 + `>=` 比较 → 并列时最后一个（名字最靠后）胜出
        best, best_hit = None, 0
        for n in sorted(pool_names):
            if n in forced:
                continue
            hit = len(keys_of[n] & holes)
            if hit >= best_hit:
                best, best_hit = n, hit
        if best is None or best_hit == 0:
            # 不可达：holes 里每个 key 都来自池内某个文件；若那个文件在 forced 里，
            # 它的 key 早已进 covered，就不会留在 holes 里。留着这条是为了万一判据被改坏时
            # **显式炸掉**，而不是静默留下缺口。
            raise RuntimeError(
                f"补洞失败：仍有 {len(holes)} 个 key 无人供，无法安全判定（池={sorted(pool_names)}）"
            )
        forced.add(best)
        covered |= keys_of[best]
        holes -= keys_of[best]
    return forced


def decide(files: dict[str, dict], contributing_shops: set[str]) -> list[dict]:
    """纯判定：为每个文件给出「保留 / 归档 / 隔离 / 跳过」决策。**无任何 I/O。**

    判定顺序（顺序本身是判据的一部分，不可调换）：
        1. `files[F]["shop"]` 不在 `contributing_shops` → `skip`
           （零贡献店铺整组跳过，**连读出来的 key 都不作数**——日后重新登记时数据还在）
        2. `keys(F)` 为空 → `quarantine`
           （读不出来 or 提不出 key，⛔ 不猜规则。注意必须排在保留集判定**之前**：
           空集是任何集合的子集，若先判覆盖，一个空文件会被判成「可归档」）
        3. 在每个「同 shop + 同 kind」池内先求出**保留集**（见 `build_retention`）：
           不属于保留集 → `archive`；属于保留集 → `keep`

    🔴 第 3 步的保留集是**最终会留下来的那批文件**，⛔ 不是「所有别的文件」。
    为什么不能只看别的文件：A 因为 B 被判可归档、B 因为 A 被判可归档 → 两份同时归档，
    覆盖依据互相撤销。真实数据上塌掉的是 2026-08 下旬～10 月上旬那批日更文件形成的
    阶梯链（实测丢 90 个 key）。详见 `build_retention` 的完整推导。

    🔴 「同 shop 且同 kind」是本判据成立的前提。**混池会静默丢两列指标**：
    推广文件的每个 key 都能在明细里找到，但推广贡献的 `promotion_cost` /
    `promotion_amount` 明细根本没有，混池会把**所有推广文件**判成可归档。

    「可读」= `keys` 非空。读失败的文件 key 为空集，既不参与覆盖、也不会被别人覆盖，
    它自己则被判隔离——这正是 `collect_file_keys` 用空集表示两种失败的原因。

    Args:
        files: `{文件名: {"shop": str, "kind": "detail"|"promo", "keys": set,
            "error": str}}`。`error` 可选（缺省视为 ""）。可读性只看 `keys` 是否非空。
        contributing_shops: 贡献店铺集合（见 `load_contributing_shops`）。

    Returns:
        list[dict]: 按文件名排序，每条至少含
            `filename` / `kind` / `shop` / `key_count` / `decision` / `reason`；
            `keep` 额外含 `unique_count` 与 `unique_keys`（相对**保留集里其他文件**而言
            独持 key 的**样例**，上限 `SAMPLE_LIMIT` 条，所以 `unique_count` 才是全量计数）；
            `archive` 额外含 `covered_by`（**保留集**里的覆盖来源样例）。

    Example:
        >>> k = ("S", "2025-01-01", "1")
        >>> files = {
        ...     "new.xlsx": {"shop": "S", "kind": "detail", "keys": {k}},
        ...     "old.xlsx": {"shop": "S", "kind": "detail", "keys": {k}},
        ...     "bad.xlsx": {"shop": "S", "kind": "detail", "keys": set(), "error": "Boom: x"},
        ... }
        >>> [(d["filename"], d["decision"]) for d in decide(files, {"S"})]
        [('bad.xlsx', 'quarantine'), ('new.xlsx', 'archive'), ('old.xlsx', 'keep')]
    """
    # 先在每个「同店 + 同类」池内求保留集；只把「店铺贡献、且 key 非空」的文件纳入池
    pools: dict[tuple[str, str], list[str]] = {}
    for name in sorted(files):
        info = files[name]
        if info["shop"] in contributing_shops and info["keys"]:
            pools.setdefault((info["shop"], info["kind"]), []).append(name)
    retained: dict[tuple[str, str], set[str]] = {
        pool_key: build_retention(names, files) for pool_key, names in pools.items()
    }

    out: list[dict] = []
    for name in sorted(files):
        info = files[name]
        shop, kind, keys = info["shop"], info["kind"], info["keys"]
        entry: dict = {
            "filename": name,
            "kind": kind,
            "shop": shop,
            "key_count": len(keys),
        }

        if shop not in contributing_shops:
            entry["decision"] = SKIP
            entry["reason"] = "该店白名单登记 0 个 SPU → 贡献 0 行；整组跳过，等它日后被重新登记"
        elif not keys:
            entry["decision"] = QUARANTINE
            entry["reason"] = _quarantine_reason(info.get("error", ""))
        else:
            kept_names = retained[(shop, kind)]
            if name in kept_names:
                # 独持 key = 自己持有、而**保留集里其他文件**都不持有的 key（不留就丢的那批）
                others_retained: set = set()
                for m in kept_names:
                    if m != name:
                        others_retained |= files[m]["keys"]
                unique = sorted(keys - others_retained)
                entry["decision"] = KEEP
                entry["unique_count"] = len(unique)
                entry["unique_keys"] = [list(k) for k in unique[:SAMPLE_LIMIT]]
                if unique:
                    entry["reason"] = (f"保留集成员：独持 {len(unique)} 个 key，"
                                       f"例如 {_fmt_key(unique[0])}")
                else:
                    entry["reason"] = ("保留集成员：补洞选中（保留集里别的文件合起来仍盖不住这些 key）")
            else:
                cover_from = {m: files[m]["keys"] for m in sorted(kept_names)}
                entry["decision"] = ARCHIVE
                entry["reason"] = (f"key 全部由同店同类池里**保留集**的 {len(kept_names)} 份文件覆盖"
                                   f"（⛔ 不是「所有别的文件」）")
                entry["covered_by"] = _cover_samples(sorted(keys), cover_from)
        out.append(entry)
    return out


def derive_decisions(root: Path | None = None) -> list[dict]:
    """薄 I/O 壳：读盘 + 算 key + 读白名单，然后交给纯函数 `decide()` 判定。

    Args:
        root: 数据源根目录；可选，默认 `config.POP_SOURCE_DIR`。

    Returns:
        list[dict]: `decide()` 的决策清单（按文件名排序）。
            日期窗**不参与**判定，所以这里也不需要把文件名里的日期解析出来。

    Raises:
        FileNotFoundError: 数据源表目录不存在（同 `collect_file_keys`）。

    Example:
        >>> len(derive_decisions(Path("ResourceData"))) > 0   # doctest: +SKIP
        True
    """
    root = Path(root) if root is not None else config.POP_SOURCE_DIR
    return decide(collect_file_keys(root), load_contributing_shops(root))


# ==========================================================================
# 哨兵：估计被隔离文件是否持有「独有 key」（只报警，不阻断）
# ==========================================================================
#
# 为什么需要一层「宽松探针」而不是直接用判定层的 key：
#   判定层把「提不出 key」的文件判为 quarantine —— 被隔离文件自己恰好是 0 个 key。
#   若直接拿 0 个 key 去和保留集求差，差集恒为空，哨兵永远不会响、形同虚设。
#   所以哨兵另起一条只读探针，用与生产同一批**声明列名**（COLUMN_MAP / PROMO_COL_MAP，
#   ⛔ 不猜列位置）把文件里能读出来的行尽量还原成 key，**只为估计风险**。
#   它与「⛔ 不猜规则」不冲突：探针不参与入库、不动任何文件；而且它偏保守地报
#   （宁可误报、不可漏报）—— 漏报 = 唯一的数据库源被隔离后无声丢数据，误报 = 多一条
#   供人一眼扫过的告警。

# 探针的宽松日期正则：`2025-03-01` / `2025/3/1` / `2025.03.01` / `2025年3月1日` 都能还原。
# ⛔ 这是**诊断估计**用的宽容，不是生产入库的日期口径（生产的口径见 excel_source._read_one）。
_PROBE_DATE_RE = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})")


def _probe_normalize_date(v) -> str | None:
    """把一个日期单元格尽量归一成 `YYYY-MM-DD`（诊断探针用；还原不出则返回 None）。

    与生产口径（`excel_source._read_one` 只认 `^\\d{4}-\\d{2}-\\d{2}$`）不同，
    这里**故意**多认几种形态（`/`、`.`、`年月日`、8 位 `YYYYMMDD`），因为哨兵要回答的是
    「这份文件里有没有别处没有的 key」——形态不合规正是它被判隔离的原因，若沿用严格口径，
    探针会一无所获、哨兵又哑了。

    Args:
        v: 单元格原值（str / int / float / datetime / date / None）。

    Returns:
        str | None: `YYYY-MM-DD` 字符串；完全还原不出时 None。
    """
    if v is None:
        return None
    # Excel 日期单元格读出来可能是 datetime / date 对象
    if hasattr(v, "year") and hasattr(v, "month") and hasattr(v, "day"):
        try:
            return f"{int(v.year):04d}-{int(v.month):02d}-{int(v.day):02d}"
        except Exception:  # noqa: BLE001 —— 极端脏值不该让探针崩
            return None
    s = str(v).strip()
    if not s:
        return None
    if s.isdigit() and len(s) == 8:  # 推广常见的 YYYYMMDD
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    m = _PROBE_DATE_RE.match(s)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


def _probe_detail_keys(path: Path, shop: str) -> set[tuple]:
    """诊断探针：尽力从一份**明细 xlsx** 还原出它可能贡献的 `(shop, date, spu)`。"""
    import polars as pl

    from .sources.excel_source import COLUMN_MAP, _norm_spu

    raw = pl.read_excel(path, sheet_id=0)
    df = next(iter(raw.values())) if isinstance(raw, dict) else raw
    # ⛔ 不猜列位置：用与生产**同一批声明列名**（`COLUMN_MAP` 的中文键），按名字反查源列。
    date_col = next((k for k, v in COLUMN_MAP.items() if v == "date"), None)
    spu_col = next((k for k, v in COLUMN_MAP.items() if v == "spu"), None)
    if not date_col or not spu_col or date_col not in df.columns or spu_col not in df.columns:
        return set()
    out: set[tuple] = set()
    for r in df.select([date_col, spu_col]).to_dicts():
        raw_spu = r.get(spu_col)
        spu = _norm_spu(raw_spu) if raw_spu is not None else ""
        if not spu or spu == "合计":  # 与生产同一条「剔除合计 / 空 SPU 汇总行」
            continue
        d = _probe_normalize_date(r.get(date_col))
        if d:
            out.add((shop, d, spu))
    return out


def _probe_promo_keys(path: Path, shop: str) -> set[tuple]:
    """诊断探针：尽力从一份**推广 csv** 还原出它可能贡献的 `(shop, date, spu)`。"""
    import polars as pl

    from .sources.excel_source import PopSpuExcelSource, _norm_spu

    raw = None
    # 与生产 _read_promo_one 相同的编码回退链
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            raw = pl.read_csv(path, encoding=enc, infer_schema_length=0)
            break
        except Exception:  # noqa: BLE001
            raw = None
    if raw is None or raw.height == 0:
        return set()
    # ⛔ 不猜列位置：用与生产同一批声明列名（`PROMO_COL_MAP` 的中文键）反查源列
    cmap = PopSpuExcelSource.PROMO_COL_MAP
    date_col = next((k for k, v in cmap.items() if v == "date"), None)
    spu_col = next((k for k, v in cmap.items() if v == "spu"), None)
    if not date_col or not spu_col or date_col not in raw.columns or spu_col not in raw.columns:
        return set()
    out: set[tuple] = set()
    for r in raw.select([date_col, spu_col]).to_dicts():
        raw_spu = r.get(spu_col)
        spu = _norm_spu(raw_spu) if raw_spu is not None else ""
        if not spu or spu == "合计":
            continue
        d = _probe_normalize_date(r.get(date_col))
        if d:
            out.add((shop, d, spu))
    return out


def probe_file_keys(path: Path, shop: str, kind: str) -> set[tuple]:
    """只读诊断探针：尽力还原一份文件可能贡献的 key 集合（哨兵专用）。

    它与判定层的 `collect_file_keys` 刻意分开：后者用**生产口径**，读不出就是空集
    （于是被判隔离）；本函数用**宽松口径**，专为「估计这份被隔离文件会不会丢数据」而设。
    ⛔ 探针只读、不搬文件、不写清单，**不参与**任何决策。

    Args:
        path: 文件路径。
        shop: 店铺名（通常由文件名推断）。
        kind: `KIND_DETAIL` | `KIND_PROMO`。

    Returns:
        set[tuple]: `(shop, date, spu)` 的估计集合；任何异常都退化为空集
            （探针宁可沉默也不抛，避免哨兵逻辑反过来拖垮批处理）。
    """
    try:
        if kind == KIND_PROMO:
            return _probe_promo_keys(path, shop)
        return _probe_detail_keys(path, shop)
    except Exception:  # noqa: BLE001 —— 探针是尽力而为，失败即空集
        return set()


def compute_sentinel_alerts(root: Path, decisions: list[dict],
                            files: dict[str, dict]) -> list[dict]:
    """哨兵：为每个**被隔离**文件算「独有 key」，命中则产出 ERROR 级告警。

    独有 key = 探针从该文件估计出的 key − **保留集**（同店同类的 keep 文件）的 key 并集。
    只要存在哪怕 1 个独有 key，这批 `(shop, date, spu)` 就会：
        · 从未入库过 → 变成**历史缺口**；
        · 已入库过 → **停止更新、静默陈旧**（还躺在聚合里，看起来有数据，其实再也不会变）。
    因此升级为 ERROR 告警。没有独有 key（它的数据别处有来源）则**不报警**——
    此时的隔离只是整理，不是事故。

    ⚠️ 只报警不阻断：本函数不改变任何决策、不搬任何文件。

    Args:
        root: 数据源根目录（探针要回到源表目录里读文件）。
        decisions: `decide()` 的返回（用于取隔离候选与保留集成员）。
        files: `collect_file_keys()` 的返回（保留集成员的 key 取自这里，不在探针里重算）。

    Returns:
        list[dict]: 按文件名排序，每条含
            `severity`（恒为 `"ERROR"`）/ `filename` / `shop` / `kind` /
            `unique_count`（独有 key 全量计数）/ `unique_keys`（样例，上限 `SAMPLE_LIMIT` 条）/
            `reason`。空列表表示没有被隔离文件持有独有 key。

    Example:
        >>> k = ("S", "2025-01-01", "1")                       # doctest: +SKIP
        >>> # 一份形态异常文件，探针可还原出的 key 在保留集里找不到 → 告警
        >>> compute_sentinel_alerts(root, decisions, files)     # doctest: +SKIP
    """
    root = Path(root)
    table_dir = root / config.POP_TABLE_DIR_NAME

    # 保留集：同店同类里判为 keep 的文件名 -> 它们的 key 并集（key 取自判定层，不重算）
    keep_names: dict[tuple[str, str], list[str]] = {}
    for d in decisions:
        if d["decision"] == KEEP:
            keep_names.setdefault((d["shop"], d["kind"]), []).append(d["filename"])
    keep_keys: dict[tuple[str, str], set] = {}
    for pool, names in keep_names.items():
        merged: set = set()
        for n in names:
            merged |= files.get(n, {}).get("keys", set())
        keep_keys[pool] = merged

    alerts: list[dict] = []
    for d in decisions:
        if d["decision"] != QUARANTINE:
            continue
        shop, kind = d["shop"], d["kind"]
        covered = keep_keys.get((shop, kind), set())
        probed = probe_file_keys(table_dir / d["filename"], shop, kind)
        unique = sorted(probed - covered)
        if not unique:
            continue  # 没有独有 key：它的数据别处有来源，隔离只是整理，不是事故
        alerts.append({
            "severity": "ERROR",
            "filename": d["filename"],
            "shop": shop,
            "kind": kind,
            "unique_count": len(unique),
            "unique_keys": [list(k) for k in unique[:SAMPLE_LIMIT]],
            "reason": (f"被隔离文件持有 {len(unique)} 个独有 key（同店同类保留集里找不到来源）"
                       f"→ 这些 (店铺, 日期, SPU) 会变成历史缺口或静默陈旧，需人工判断；"
                       f"例如 {_fmt_key(unique[0])}"),
        })
    return sorted(alerts, key=lambda a: a["filename"])


def build_decision_document(root: Path, decisions: list[dict]) -> dict:
    """把决策清单包成机读文档（dry-run 的产物）。

    ⚠️ 刻意**不放任何时间戳**：同一份源表的两次 dry-run 必须逐字节相同（幂等验收项），
    一个 `generated_at` 就会把它毁掉。

    Args:
        root: 数据源根目录。
        decisions: `decide()` 的返回值。

    Returns:
        dict: `{source, table_dir, dry_run, contributing_shops, counts, decisions}`。
            `counts` 恒含四个决策态（没有的记 0），便于下游直接取用而不必判 None。
    """
    counts = {KEEP: 0, ARCHIVE: 0, QUARANTINE: 0, SKIP: 0}
    for d in decisions:
        counts[d["decision"]] += 1
    return {
        "source": str(root),
        "table_dir": str(root / config.POP_TABLE_DIR_NAME),
        "dry_run": True,
        "contributing_shops": sorted(load_contributing_shops(root)),
        "counts": counts,
        "decisions": decisions,
    }


def render_document(document: dict) -> str:
    """把机读文档渲染成人类可读的 dry-run 报告文本。"""
    lines = [
        "=== 源表整理器 dry-run（⛔ 不搬移 / 不删除 / 不重命名任何文件）===",
        f"数据源根目录：{document['source']}",
        f"数据源表目录：{document['table_dir']}",
        f"贡献店铺（白名单登记 ≥1 个 SPU）：{', '.join(document['contributing_shops']) or '（无）'}",
        "",
    ]
    for d in document["decisions"]:
        extra = ""
        if d["decision"] == KEEP:
            # unique_keys 只是样例（上限 SAMPLE_LIMIT），全量计数在 unique_count 里
            extra = f" ｜ 独持 key 样例：{d['unique_keys']}"
        elif d["decision"] == ARCHIVE:
            # 覆盖来源是**按 key 取样**的：同一个文件的每个 key 可能由不同文件供着，
            # 所以这里给的是「样例 key 的来源并集」，精确到逐 key 的对照在 JSON 的 covered_by 里
            src = [s for sample in d["covered_by"] for s in sample["sources"]]
            extra = f" ｜ 样例 key 覆盖来源：{', '.join(dict.fromkeys(src))}"
        lines.append(f"  {d['decision']:<10} keys={d['key_count']:<4} {d['filename']}")
        lines.append(f"     理由：{d['reason']}{extra}")
    c = document["counts"]
    lines.append("")
    lines.append(f"--- 决策统计：keep {c[KEEP]} / archive {c[ARCHIVE]} / "
                 f"quarantine {c[QUARANTINE]} / skip {c[SKIP]}"
                 f"（共 {len(document['decisions'])} 个文件）---")
    return "\n".join(lines)


def dry_run(root: Path | None = None, out: Path | None = None) -> dict:
    """执行一次 dry-run：算决策、渲染到 stdout、（可选）落一份机读 JSON。

    ⛔ 全程只读；`out` 由调用方指定，**不得**落在源表目录内（否则就破坏了「零改动」）。

    Args:
        root: 数据源根目录；可选，默认 `config.POP_SOURCE_DIR`。
        out: 决策清单 JSON 的落盘路径；None 表示只打印不落盘。

    Returns:
        dict: `build_decision_document()` 的产物。

    Raises:
        FileNotFoundError: 数据源表目录不存在。
    """
    root = Path(root) if root is not None else config.POP_SOURCE_DIR
    document = build_decision_document(root, derive_decisions(root))
    print(render_document(document))
    if out is not None:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        # 尾随换行固定，保证两次 dry-run 逐字节相同
        out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        print(f"\n决策清单已写入：{out}")
    print("\n[dry-run] 未搬移任何文件；源表目录保持原样。")
    return document


# ==========================================================================
# 执行层：真搬文件（归档）——模式 / write-ahead 清单 / 单文件容错 / 幂等 / 回滚
# ==========================================================================
#
# 安全默认（ADR-0002 / Q13）：模式取自环境变量 `JD_ORGANIZE`，默认 `dry-run`——
#   只出清单不搬文件。真实目录上只有显式 `--mode on` 才会搬，因为「首次归档」是整个
#   设计里规模最大的一次不可逆动作，必须先由人工过目。
#
# 为什么清单要 **write-ahead**（先写意图再搬、搬完标完成）：
#   反过来先搬后写，一旦崩在中间就成了一笔糊涂账——再也说不清哪些文件动过。
#   这个顺序看起来多此一举，却是崩溃后还能精确续做的唯一依据（ADR-0002）。

# 三种模式。`off` 完全不整理；`dry-run` 只出决策清单；`on` 真搬文件。
MODE_OFF = "off"
MODE_DRY_RUN = "dry-run"
MODE_ON = "on"
VALID_MODES = (MODE_OFF, MODE_DRY_RUN, MODE_ON)

# 归档区目录名：位于**数据源根目录**下（源表目录之外）。
# 放在表目录之外，是因为批处理的 glob 虽然今天是非递归的，但依赖这一隐式前提很脆弱——
# 一旦枚举改成递归（rglob），归档就会被重新吸回数据管线，而且这种失败是**静默的**（ADR-0003）。
ARCHIVE_DIR_NAME = "_archive"
# 隔离区目录名：同样位于**数据源根目录**下（源表目录之外），文件按原名平铺。
# ⚠️ 与归档区**相互独立**：不按批次分子目录、有自己独立的清单；且
#   ⛔ 隔离区**永不参与**归档区的 60 天清理（ADR-0005/0006：隔离区里的文件是在等人判断的，
#   不是等清理的）。`iter_archive_batches()` 只枚举 `_archive/*`，结构上就扫不到隔离区。
QUARANTINE_DIR_NAME = "_quarantine"
MANIFEST_NAME = "manifest.json"

# 清单里每条的状态机：planned → done | failed ｜ done/failed → rolled_back。
STATUS_PLANNED = "planned"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_ROLLED_BACK = "rolled_back"


def resolve_mode(explicit: str | None = None) -> str:
    """解析整理模式：显式参数 > 环境变量 `JD_ORGANIZE` > 默认 `dry-run`。

    ⚠️ 默认是 `dry-run` 而不是 `on`：真实目录上「什么都不搬」必须是默认行为。
    非法值一律回退到 `dry-run`（安全侧），⛔ 绝不因拼错模式而误触发搬移。

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
    raw = explicit if explicit is not None else getattr(config, "ORGANIZE_MODE", MODE_DRY_RUN)
    raw = (raw or "").strip().lower()
    return raw if raw in VALID_MODES else MODE_DRY_RUN


def move_file(src: Path, dst: Path) -> None:
    """把文件从 `src` 搬到 `dst`；`dst` 已存在时**抛错拒绝覆盖**。

    抽成模块级函数是为了让「单文件失败不中断」这类用例可以在测试里 monkeypatch 它
    （模拟文件被占用 / 权限不足），而不必真的去锁文件。

    Args:
        src: 源文件路径。
        dst: 目标文件路径（父目录不存在时自动创建）。

    Raises:
        FileExistsError: `dst` 已存在（⛔ 绝不覆盖既有文件，避免悄悄吃掉别人的数据）。
        OSError: 源文件被占用 / 权限不足等底层失败（由调用方按「单文件失败」处理）。

    Example:
        >>> move_file(Path("a/x.xlsx"), Path("b/x.xlsx"))   # doctest: +SKIP
    """
    if dst.exists():
        raise FileExistsError(f"目标已存在，拒绝覆盖：{dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _archive_dir(root: Path, batch: str) -> Path:
    """某批次的归档目录：`{root}/_archive/{batch}/`（源表目录之外）。"""
    return root / ARCHIVE_DIR_NAME / batch


def _manifest_path(root: Path, batch: str) -> Path:
    """某批次清单的落盘路径：`{root}/_archive/{batch}/manifest.json`。"""
    return _archive_dir(root, batch) / MANIFEST_NAME


def _quarantine_dir(root: Path) -> Path:
    """隔离区目录：`{root}/_quarantine/`（源表目录之外，文件按原名平铺）。"""
    return root / QUARANTINE_DIR_NAME


def _quarantine_manifest_path(root: Path) -> Path:
    """隔离区清单的落盘路径：`{root}/_quarantine/manifest.json`。

    与归档清单**各自独立**：隔离区不按批次分目录，故只有一份清单；每条仍带自己的
    `batch_time`，便于追溯「哪一天隔离了谁」。
    """
    return _quarantine_dir(root) / MANIFEST_NAME


def iter_archive_batches(root: Path | None = None) -> list[Path]:
    """列出归档区下的**批次目录**（按名字排序），供工单 05 的 60 天清理遍历。

    ⛔ 隔离区 `_quarantine/` **永不入清理候选**——它不在 `_archive/` 之下，本函数只枚举
    `_archive/*`，结构上就扫不到它（ADR-0005/0006）。工单 05 实现清理时直接复用本函数，
    不要在别处另写一套目录枚举，以免把隔离区卷进来。

    Args:
        root: 数据源根目录；可选，默认 `config.POP_SOURCE_DIR`。

    Returns:
        list[Path]: 归档区下的批次目录路径（如 `{root}/_archive/2026-10-12`）；
            归档区不存在或为空时返回空列表。

    Example:
        >>> [p.name for p in iter_archive_batches(Path("ResourceData"))]  # doctest: +SKIP
        ['2026-10-12']
    """
    root = Path(root) if root is not None else config.POP_SOURCE_DIR
    arch = root / ARCHIVE_DIR_NAME
    if not arch.is_dir():
        return []
    return sorted((p for p in arch.iterdir() if p.is_dir()), key=lambda p: p.name)


def read_manifest(path: Path) -> dict | None:
    """容错读取清单：文件不存在返回 None；损坏 / 半截返回带 `corrupt` 标记的结构。

    🔴 绝不抛异常：清单是**事后追溯与回滚的唯一依据**（ADR-0002），
    损坏时也必须能被读出来（哪怕只是报告「它坏了」），而不是让调用方直接崩。

    Args:
        path: 清单文件路径。

    Returns:
        dict | None: 正常时为清单 dict；损坏时为
            `{"batch":..., "corrupt": True, "error":..., "entries": []}`；
            文件不存在时为 None。

    Example:
        >>> read_manifest(Path("nope/manifest.json")) is None
        True
    """
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 —— 半截 / 编码坏掉都算「清单损坏」
        return {"batch": p.parent.name, "corrupt": True,
                "error": f"{type(e).__name__}: {e}", "entries": []}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return {"batch": p.parent.name, "corrupt": True,
                "error": "结构不符（缺 entries 列表）", "entries": []}
    return data


def _write_manifest(path: Path, manifest: dict) -> None:
    """把清单 JSON 落盘（父目录自动创建，尾随换行固定）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _plan_moves(root: Path, candidates: list[dict], entries: list[dict],
                index: dict, now: datetime, batch: str, dest_rel_dir: str,
                rel_field: str) -> list[tuple[dict, Path, Path]]:
    """把一批候选文件规划成 write-ahead 条目（登记进清单的 entries / index）。

    归档与隔离**共用**这段语义——核心是「同一天重复运行按 `source_rel` 合并条目」
    （便于重试上一批失败的、而不是产生重复条目）。两侧唯一区别是目的地相对目录
    （`_archive/{batch}` vs `_quarantine`）与目的地字段名（`archive_rel` vs `quarantine_rel`）。

    Args:
        root: 数据源根目录。
        candidates: 决策清单里的候选条目（含 `filename` / `reason`）。
        entries: 目标清单的 entries 列表（就地追加 / 更新）。
        index: `{source_rel: entry}` 的既有条目索引（就地更新）。
        now: 批次时间。
        batch: 批次日期串（仅用于归档目的地路径，由调用方拼进 dest_rel_dir）。
        dest_rel_dir: 目的地相对目录（如 `_archive/2026-10-12` 或 `_quarantine`）。
        rel_field: 目的地字段名（`archive_rel` 或 `quarantine_rel`）。

    Returns:
        list[tuple[dict, Path, Path]]: 每条为 `(条目, 源绝对路径, 目的地绝对路径)`。
    """
    table_dir_name = config.POP_TABLE_DIR_NAME
    planned: list[tuple[dict, Path, Path]] = []
    for d in candidates:
        name = d["filename"]
        src_rel = f"{table_dir_name}/{name}"
        dest_rel = f"{dest_rel_dir}/{name}"
        entry = index.get(src_rel)
        if entry is None:
            entry = {"source_rel": src_rel, rel_field: dest_rel,
                     "reason": d["reason"], "batch_time": now.isoformat(),
                     "status": STATUS_PLANNED, "error": ""}
            entries.append(entry)
            index[src_rel] = entry
        else:
            entry.update({rel_field: dest_rel, "reason": d["reason"],
                          "batch_time": now.isoformat(),
                          "status": STATUS_PLANNED, "error": ""})
        planned.append((entry, root / src_rel, root / dest_rel))
    return planned


def _run_moves(manifest_path: Path, manifest: dict,
               planned: list[tuple[dict, Path, Path]], move) -> tuple[int, int]:
    """write-ahead 执行一段搬移：整批先落 `planned`，再逐个搬、每搬一次落一次盘。

    顺序不可颠倒：反过来先搬后写，一旦崩在中间就再也说不清哪些文件动过（ADR-0002）。
    单个文件搬不动时跳过它、标 `failed` + 原因、**本批继续跑完**（⛔ 不抛到上层）。

    Args:
        manifest_path: 该段搬移的清单路径。
        manifest: 清单 dict（entries 会被就地改写）。
        planned: `_plan_moves` 的产物。
        move: 搬移函数。

    Returns:
        tuple[int, int]: `(moved, failed)`。
    """
    moved = failed = 0
    if planned:
        # ① write-ahead：先把整批写成 planned 落盘，再动任何文件
        _write_manifest(manifest_path, manifest)
        for entry, src, dst in planned:
            try:
                move(src, dst)
                entry["status"] = STATUS_DONE
                entry["error"] = ""
                moved += 1
            except Exception as e:  # noqa: BLE001 —— 单文件失败不中断整批
                entry["status"] = STATUS_FAILED
                entry["error"] = f"{type(e).__name__}: {e}"
                failed += 1
            # ② 每搬成一个就落一次盘（崩溃后据此精确知道哪些没做完）
            _write_manifest(manifest_path, manifest)
    return moved, failed


# ==========================================================================
# 断档检测（只报告、零动作）
# ==========================================================================
#
# **为什么在「保留集」上算，而不是全量文件**：
#   保留集（`decide()` 判为 keep 的那批）是**最终会留下来**的文件，它的 key 并集
#   等于同店同类池里所有**可读**文件的 key 并集（见 `build_retention` 的两步法：
#   forced ∪ 补洞 恰好盖满 holes）。所以「在保留集上算断档」与「在全量可读文件上算」
#   等价，而口径更简单、更稳。
#   被**隔离**文件的日期会掉出保留集——但那是**哨兵**的职责（它专门盯「某段数据是否
#   只由被隔离文件供着」），不是断档的：断档回答的是「这些**还在参与计算**的文件里，
#   日期轴上哪里没人覆盖」，⛔ 不替哨兵回答「被隔离的那份是不是唯一来源」。
#   被**跳过**的零贡献店铺文件同样不入保留集——它们本来就不贡献指标，其日期不参与断档。

def _date_range(lo: date, hi: date):
    """闭区间日期迭代器（含 lo 与 hi）。"""
    cur = lo
    while cur <= hi:
        yield cur
        cur += timedelta(days=1)


def find_gaps(files: dict[str, dict], decisions: list[dict]) -> list[dict]:
    """按 `(店铺, 类型)` 找出保留集覆盖不到的日期区间（**只报告，零动作**）。

    口径：取该组**保留集文件**所含 key 的日期集合，从 min 到 max 逐日检查，
    把**连续缺失的日子**合并成一个区间。⛔ 不补下载、⛔ 不搬文件、⛔ 不改任何决策。

    为什么在保留集上算（而不是全量）：见本节开头说明——保留集已覆盖全部 key，两者等价；
    而被隔离文件的日期会掉出去，那是**哨兵**的职责，不是断档的。

    Args:
        files: `collect_file_keys()` 的返回（提供 keep 文件的 key）。
        decisions: `decide()` 的返回（用于挑出 keep 文件）。

    Returns:
        list[dict]: 每条 `{shop, kind, start, end, days}`，按 `(shop, kind, start)` 排序；
            空列表表示日期轴上没有被覆盖的空档。

    Example:
        >>> files = {
        ...     "a.csv": {"shop": "S", "kind": "promo",
        ...               "keys": {("S", "2025-01-01", "1"), ("S", "2025-01-03", "1")}},
        ... }
        >>> decisions = [{"filename": "a.csv", "shop": "S", "kind": "promo",
        ...               "decision": "keep"}]
        >>> find_gaps(files, decisions)
        [{'shop': 'S', 'kind': 'promo', 'start': '2025-01-02', 'end': '2025-01-02', 'days': 1}]
    """
    keep_names: dict[tuple[str, str], list[str]] = {}
    for d in decisions:
        if d["decision"] == KEEP:
            keep_names.setdefault((d["shop"], d["kind"]), []).append(d["filename"])

    gaps: list[dict] = []
    for (shop, kind), names in sorted(keep_names.items()):
        present: set[date] = set()
        for n in names:
            for key in files.get(n, {}).get("keys", set()):
                try:
                    present.add(date.fromisoformat(str(key[1])))
                except ValueError:
                    continue  # 日期不是 ISO 形态的 key 不参与断档（不猜）
        if not present:
            continue
        lo, hi = min(present), max(present)
        start: date | None = None
        prev: date | None = None
        for day in _date_range(lo, hi):
            if day in present:
                if start is not None and prev is not None:
                    gaps.append({"shop": shop, "kind": kind, "start": start.isoformat(),
                                 "end": prev.isoformat(), "days": (prev - start).days + 1})
                start, prev = None, None
            else:
                if start is None:
                    start = day
                prev = day
        if start is not None and prev is not None:
            gaps.append({"shop": shop, "kind": kind, "start": start.isoformat(),
                         "end": prev.isoformat(), "days": (prev - start).days + 1})
    return gaps


# ==========================================================================
# 回收站清理（60 天保留期）——报告第五段
# ==========================================================================
#
# 为什么是**回收站**而不是硬删（ADR-0006）：
#   这条清理无人值守地持续跑，需要一个不依赖人在场的兜底层；而源表是从商家后台
#   **可以重新下载**的产物，60 天前的数据早已被后续下载覆盖——被清掉的东西不是这世上
#   唯一的一份。但硬删不可逆，所以一律走系统回收站。
#
# 清理**只**遍历 `iter_archive_batches(root)`（只枚举 `_archive/*` 批次目录）：
#   `_quarantine/` 不在 `_archive/` 之下，结构上就扫不到——这就是「隔离区永不入清理」的
#   落地点（ADR-0005/0006）。⛔ 不要在别处另写目录枚举。

# RETENTION_DAYS：归档批次的保留天数，**按归档日期起算**（批次目录名 `YYYY-MM-DD`）。
# 边界口径：`age_days >= RETENTION_DAYS` 即视为「满 60 天」→ 清理。于是
#   59 天 → 保留；60 天 / 61 天 → 进回收站。取 `>=` 而非 `>` 的理由：批次日期是**日期粒度**，
#   「满 60 天」的自然含义是「第 60 天到了」；`>` 会把它拖到第 61 天才清，白等一天。
RETENTION_DAYS = 60

# 清理账本：写在 `{root}/_archive/cleanup.json`（**文件**，不是目录——不会被
# `iter_archive_batches` 当成批次）。与归档清单 `manifest.json` 各自独立，⛔ 不改后者结构。
CLEANUP_MANIFEST_NAME = "cleanup.json"

# 清理条目状态：planned → recycled | deferred。
# `deferred` = 回收站不可用（网络卷 / API 失败），本次只在报告里列出、文件原样保留。
STATUS_RECYCLED = "recycled"
STATUS_DEFERRED = "deferred"

# 回收站容量驱逐风险（spec §Further Notes 明确要求：必须写进报告，不能默默发生）。
RECYCLE_BIN_RISK_NOTE = (
    "⚠️ 回收站有容量上限，超出后 Windows 会永久删除回收站里最旧的条目——"
    "批量清理可能把更早的、与归档无关的文件一并挤出（这是本功能唯一波及归档区之外的副作用）。"
)

# 降级说明（回收站不可用时，报告里必须出现这一行，⛔ 不静默失败）
CLEANUP_DEGRADED_NOTE = "降级：回收站不可用，本次仅报告"

# SHFileOperationW 的常量（Win32）。
_FO_DELETE = 3
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMATION = 0x0010
_FOF_ALLOWUNDO = 0x0040
_FOF_NOERRORUI = 0x0400


def to_recycle_bin(path: Path) -> None:
    """把文件（或目录）移入 **Windows 系统回收站**——⛔ 不是硬删除（ADR-0006）。

    用 stdlib `ctypes` 直接调 Win32 `SHFileOperationW`，带 `FOF_ALLOWUNDO` 标志 →
    文件被送进回收站、可还原。⛔ 不引入 `send2trash` / `winshell`（`requirements.txt`
    是全量 freeze，加依赖会破坏异地复现）。

    抽成模块级函数是为了让调用方能像 `move=move_file` 一样**注入**替代实现：
    测试里注入「恒抛异常」的假回收站即可验证降级路径，而不必真的制造一个坏卷。

    🔴 为什么不能只看 `SHFileOperationW` 的返回码：实测（Windows 10）`FOF_ALLOWUNDO`
    成功把文件送进回收站时，本 API 仍会返回 **2（ERROR_FILE_NOT_FOUND）**。所以成功判据
    以「文件确实离开了原位置 **且** 回收站条目数/容量增加」为准——后者是关键：它把
    「进了回收站」与「因回收站不可用而被**永久删除**」区分开来。⛔ 一旦发现是硬删
    （原位没了但回收站没增），立即抛错，绝不把硬删当成功。

    Args:
        path: 要送进回收站的路径（文件或目录）。

    Raises:
        OSError: 非 Windows 平台；目标不存在；回收站不可用（预检失败，⛔ 在调用前就退出，
            避免触发 FOF_ALLOWUNDO 的「无回收站则永久删除」回退）；或调用后原位仍在 /
            回收站未增加（疑似硬删）。

    Example:
        >>> to_recycle_bin(Path("tmp/x.xlsx"))   # doctest: +SKIP
    """
    if os.name != "nt":
        raise OSError("回收站清理仅支持 Windows（本项目部署在 Windows）")

    import ctypes
    from ctypes import wintypes

    class _SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_ushort),          # FILEOP_FLAGS = WORD
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    class _SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("i64Size", ctypes.c_longlong),
                    ("i64NumItems", ctypes.c_longlong)]

    target = Path(path).resolve()
    if not target.exists():
        raise OSError(f"要回收的路径不存在：{target}")

    def _bin_stats() -> tuple[int, int] | None:
        """回收站 (总容量, 条目数)；查询失败返回 None（非本地卷 / 无回收站）。"""
        info = _SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
        rc = ctypes.windll.shell32.SHQueryRecycleBinW(target.drive or None, ctypes.byref(info))
        if rc != 0:
            return None
        return int(info.i64Size), int(info.i64NumItems)

    # 预检：回收站不可用（网络卷 / 被禁用）时**在调用前**退出——因为 FOF_ALLOWUNDO
    # 在无回收站的卷上的回退行为是**永久删除**，那正是我们要避免的。
    before = _bin_stats()
    if before is None:
        raise OSError(f"该卷没有可用回收站，拒绝清理（{target.drive or target.anchor}）")

    # pFrom 必须是「双 null 结尾」的多字符串（SHFileOperation 的约定）。
    p_from = ctypes.create_unicode_buffer(str(target) + "\0\0")
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = _FO_DELETE
    op.pFrom = ctypes.cast(p_from, wintypes.LPCWSTR)
    op.pTo = None
    op.fFlags = _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT | _FOF_NOERRORUI
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))

    if target.exists():
        raise OSError(f"送进回收站失败：文件仍在原地（SHFileOperationW 返回 {result}，"
                      f"fAnyOperationsAborted={bool(op.fAnyOperationsAborted)}）")
    after = _bin_stats()
    if after is not None and after[1] <= before[1] and after[0] <= before[0]:
        raise OSError("回收站条目数 / 容量未增加 → 文件疑似被**永久删除**而非回收"
                      f"（原位已消失，SHFileOperationW 返回 {result}）")


def _batch_date(name: str) -> date | None:
    """把批次目录名解析成日期；不是严格 `YYYY-MM-DD` 时返回 None（不猜、跳过）。"""
    try:
        return datetime.strptime(name, "%Y-%m-%d").date()
    except ValueError:
        return None


def expired_archive_batches(root: Path | None = None, *, now: datetime | None = None,
                            retention_days: int = RETENTION_DAYS) -> list[tuple[str, int]]:
    """列出**已超期**的归档批次：`[(批次名, 已存天数)]`。

    ⛔ 只遍历 `iter_archive_batches(root)`（`_archive/*` 批次目录），隔离区扫不到。
    批次名解析不出日期的一律跳过（不猜）。边界：`已存天数 >= retention_days` 才算超期。

    Args:
        root: 数据源根目录；可选，默认 `config.POP_SOURCE_DIR`。
        now: 参考时刻；可选，默认当前 UTC 时间。
        retention_days: 保留天数，默认 60。

    Returns:
        list[tuple[str, int]]: 超期批次（按名字排序）；无超期批次时为空列表。

    Example:
        >>> expired_archive_batches(Path("ResourceData"), now=datetime(2026, 10, 12))  # doctest: +SKIP
        [('2026-06-01', 133)]
    """
    root = Path(root) if root is not None else config.POP_SOURCE_DIR
    today = (now or datetime.now(timezone.utc)).date()
    out: list[tuple[str, int]] = []
    for batch in iter_archive_batches(root):
        d = _batch_date(batch.name)
        if d is None:
            continue
        age = (today - d).days
        if age >= retention_days:
            out.append((batch.name, age))
    return out


def plan_cleanup(root: Path | None = None, *, now: datetime | None = None,
                 retention_days: int = RETENTION_DAYS) -> list[dict]:
    """列出待清理项：超期批次目录下除清单外的每个文件。

    ⛔ `manifest.json` / `cleanup.json` 本身不清理（它们是审计线索，留下）。

    Args:
        root: 数据源根目录；可选，默认 `config.POP_SOURCE_DIR`。
        now: 参考时刻；可选，默认当前 UTC 时间。
        retention_days: 保留天数，默认 60。

    Returns:
        list[dict]: 每条 `{source_rel, batch, path(Path), age_days}`，按批次名 + 文件名排序。

    Example:
        >>> plan_cleanup(Path("ResourceData"), now=datetime(2026, 10, 12))   # doctest: +SKIP
        []
    """
    root = Path(root) if root is not None else config.POP_SOURCE_DIR
    items: list[dict] = []
    for name, age in expired_archive_batches(root, now=now, retention_days=retention_days):
        batch_dir = root / ARCHIVE_DIR_NAME / name
        if not batch_dir.is_dir():
            continue
        for p in sorted(batch_dir.iterdir(), key=lambda x: x.name):
            if p.is_file() and p.name not in (MANIFEST_NAME, CLEANUP_MANIFEST_NAME):
                items.append({
                    "source_rel": f"{ARCHIVE_DIR_NAME}/{name}/{p.name}",
                    "batch": name, "path": p, "age_days": age,
                })
    return items


def cleanup_expired(root: Path | None = None, *, now: datetime | None = None,
                    retention_days: int = RETENTION_DAYS, recycle=to_recycle_bin,
                    dry_run: bool = False) -> dict:
    """把超期归档批次里的文件**逐个**送进系统回收站（write-ahead + 降级 + 只报告）。

    流程（与归档搬移同一套 write-ahead 精神，⛔ 不改归档清单结构）：

        · `dry_run=True`：只**列**候选，不写账本、不动任何文件（零副作用）。
        · `dry_run=False`：① 把整批候选写成 `planned` 落 `_archive/cleanup.json` →
          ② 逐个 `recycle(path)` → ③ 每成一个就落一次盘（`recycled` / `deferred`）。

    **降级**：`recycle` 抛异常（网络卷 / 回收站不可用）时，该项记 `deferred` + 原因，
    ⛔ **不硬删**、⛔ **不中断**；若整批一个都没进成，返回段里 `degraded=True`，
    报告据此打出「降级：回收站不可用，本次仅报告」那一行。

    Args:
        root: 数据源根目录；可选，默认 `config.POP_SOURCE_DIR`。
        now: 参考时刻；可选，默认当前 UTC 时间。
        retention_days: 保留天数，默认 60。
        recycle: 回收站函数（默认 `to_recycle_bin`）；可注入以便测试降级路径。
        dry_run: True 时只列候选、零副作用。

    Returns:
        dict: 回收站清理段，含
            `dir` / `manifest` / `retention_days` / `batches` / `candidates` /
            `recycled` / `failed` / `deferred` / `degraded` / `dry_run` /
            `entries`（每条 `{source_rel, batch, status, error}`）/ `risk_note`。

    Example:
        >>> sec = cleanup_expired(Path("ResourceData"), now=datetime(2026, 10, 12))  # doctest: +SKIP
        >>> sec["candidates"]                                                        # doctest: +SKIP
        0
    """
    root = Path(root) if root is not None else config.POP_SOURCE_DIR
    now = now or datetime.now(timezone.utc)
    items = plan_cleanup(root, now=now, retention_days=retention_days)

    archive_root = root / ARCHIVE_DIR_NAME
    section: dict = {
        "dir": str(archive_root),
        "manifest": str(archive_root / CLEANUP_MANIFEST_NAME),
        "retention_days": retention_days,
        "batches": sorted({it["batch"] for it in items}),
        "candidates": len(items),
        "recycled": 0,
        "failed": 0,
        "deferred": 0,
        "degraded": False,
        "dry_run": dry_run,
        "entries": [],
        "risk_note": RECYCLE_BIN_RISK_NOTE,
    }
    if not items:
        section["entries"] = []
        return section

    entries = [{"source_rel": it["source_rel"], "batch": it["batch"],
                "status": STATUS_PLANNED, "error": ""} for it in items]

    if dry_run:
        section["entries"] = [dict(e) for e in entries]
        section["deferred"] = len(entries)   # 预演：待清理项全部「待处理」
        return section

    ledger = archive_root / CLEANUP_MANIFEST_NAME

    def _persist() -> None:
        _write_manifest(ledger, {"batch": "cleanup", "batch_time": now.isoformat(),
                                 "source_root": str(root),
                                 "retention_days": retention_days, "entries": entries})

    # ① write-ahead：整批先写成 planned 落盘，再动任何文件
    _persist()
    for it, entry in zip(items, entries):
        try:
            recycle(it["path"])
            entry["status"] = STATUS_RECYCLED
            entry["error"] = ""
            section["recycled"] += 1
        except Exception as e:  # noqa: BLE001 —— 单个文件失败不中断，也不硬删
            entry["status"] = STATUS_DEFERRED
            entry["error"] = f"{type(e).__name__}: {e}"
            section["failed"] += 1
        # ② 每成一个就落一次盘（崩溃后据此精确知道哪些没做完）
        _persist()

    section["entries"] = [dict(e) for e in entries]
    section["deferred"] = section["failed"]
    # 整批一个都没进成 → 回收站不可用 → 降级（报告里会显式写一行）
    if section["recycled"] == 0 and section["failed"] > 0:
        section["degraded"] = True
    return section


# ==========================================================================
# 五段报告：结构化事实 -> 人读 md + 机读 json（两者内容一致）
# ==========================================================================
#
# 🔴 「五段齐全」必须是**结构化的 5 个条目**，⛔ 不能拼成一整块字符串：
#   拼单串时删掉第 2 段、首尾关键词仍能 grep 到，断言照样绿。所以报告文档里
#   有一个 `sections` **列表**，恰好 5 项、顺序固定、每项一个**互不相同**的关键词。
#   `render_report(document)` 是纯函数，`md == render_report(json.loads(json))` 可断言。

# 五段的固定顺序（顺序本身是验收项的一部分）
REPORT_SECTION_KEYS = ("archive", "quarantine", "gaps", "sentinel", "cleanup")
# 每段的标题与「互不相同」的关键词（关键词用于断言某段确实被渲染出来了）
SECTION_TITLES = {
    "archive": "归档",
    "quarantine": "隔离",
    "gaps": "断档",
    "sentinel": "哨兵告警",
    "cleanup": "回收站清理",
}
SECTION_KEYWORDS = {
    "archive": "归档区",
    "quarantine": "隔离区",
    "gaps": "断档区间",
    "sentinel": "哨兵告警",
    "cleanup": "回收站",
}

# 报告落盘目录名：与批处理日志同类的位置（`{source_root 的同级}/logs/organize/`）。
# 生产里 source_root=`ResourceData`，其同级就是项目根 → 报告落在 `logs/organize/`。
REPORT_DIR_NAME = "organize"


def _default_report_dir(root: Path) -> Path:
    """默认报告目录：`{source_root 的同级}/logs/organize/`。

    与源表根同级（生产里 source_root=`ResourceData` → 报告落在项目根 `logs/organize/`），
    这样测试用临时源根时报告也自然落在临时目录里，⛔ 不污染真实 `logs/`。
    """
    return Path(root).resolve().parent / "logs" / REPORT_DIR_NAME


def _section(key: str, summary: str, lines: list[str]) -> dict:
    """构造一个报告段：`{key, title, keyword, summary, lines}`（结构化，⛔ 不是拼串）。"""
    return {"key": key, "title": SECTION_TITLES[key], "keyword": SECTION_KEYWORDS[key],
            "summary": summary, "lines": lines}


def _render_counts(counts: dict | None) -> str:
    """把四态计数渲染成一行；None 时给「—」。"""
    if not counts:
        return "—"
    return (f"keep {counts.get(KEEP, 0)} / archive {counts.get(ARCHIVE, 0)} / "
            f"quarantine {counts.get(QUARANTINE, 0)} / skip {counts.get(SKIP, 0)}")


def build_report_document(report: dict) -> dict:
    """把 `organize_files()` 的报告整理成「五段」结构化文档（渲染前的唯一事实来源）。

    五段顺序固定：`归档 / 隔离 / 断档 / 哨兵告警 / 回收站清理`。每段是
    `{key, title, keyword, summary, lines}` 一个**结构化条目**——⛔ 不拼成一整块字符串，
    否则「删掉第 2 段」这种破坏无法被断言抓住。

    ⚠️ 断档与哨兵两段的**结构化事实**由上游算好（`report["gaps"]` /
    `report["sentinel_alerts"]`，dry-run 下也有）；本函数只**渲染**，⛔ 不重算探针。

    Args:
        report: `organize_files()` 的返回。

    Returns:
        dict: `{batch, mode, dry_run, source, counts, seconds, sections}`；
            `sections` 是**恰好 5 项**的列表，顺序 = `REPORT_SECTION_KEYS`。
    """
    dry = bool(report.get("dry_run"))
    counts = report.get("counts")

    # —— 段 1：归档 ——
    a_lines = [
        f"归档区：{report.get('archive_dir', '')}",
        f"清单：{report.get('manifest', '')}",
        f"计划 {report.get('planned', 0)} 份，搬动 {report.get('moved', 0)} 份，"
        f"失败 {report.get('failed', 0)} 份",
    ]
    if dry:
        a_lines.append("（dry-run 预演：⛔ 未搬动任何文件、未写清单）")
    for m in report.get("moves", []):
        a_lines.append(f"  [{m['status']}] {m['source_rel']} → {m['archive_rel']}"
                       + (f"（{m['error']}）" if m.get("error") else ""))
    archive = _section(
        "archive", f"计划 {report.get('planned', 0)} 份、搬动 {report.get('moved', 0)} 份",
        a_lines)

    # —— 段 2：隔离 ——
    q = report.get("quarantined") or {}
    q_lines = [
        f"隔离区：{q.get('dir', '')}",
        f"清单：{q.get('manifest', '')}",
        f"计划 {q.get('planned', 0)} 份，搬动 {q.get('moved', 0)} 份，失败 {q.get('failed', 0)} 份",
        "（隔离区不参与归档区的 60 天清理：那里的文件在等人，不在等清扫机）",
    ]
    if dry:
        q_lines.append("（dry-run 预演：⛔ 未搬动任何文件、未写清单）")
    for m in q.get("moves", []):
        q_lines.append(f"  [{m['status']}] {m['source_rel']} → {m['quarantine_rel']}"
                       + (f"（{m['error']}）" if m.get("error") else ""))
    quarantine = _section(
        "quarantine", f"计划 {q.get('planned', 0)} 份、搬动 {q.get('moved', 0)} 份", q_lines)

    # —— 段 3：断档（只报告、零动作）——
    gaps = report.get("gaps") or []
    if gaps:
        g_summary = f"发现 {len(gaps)} 段未被覆盖的日期区间"
        g_lines = [f"  {g['shop']} / {g['kind']}：{g['start']} ~ {g['end']}（{g['days']} 天）"
                   for g in gaps]
    else:
        g_summary = "无断档"
        g_lines = ["  日期轴上没有被覆盖的空档"]
    g_lines.append("（零动作：⛔ 不补下载、⛔ 不搬文件、⛔ 不改任何决策，只报告）")
    gaps_sec = _section("gaps", g_summary, g_lines)

    # —— 段 4：哨兵告警 ——
    alerts = report.get("sentinel_alerts") or []
    if alerts:
        s_summary = f"ERROR × {len(alerts)}（被隔离文件持有保留集里找不到来源的独有 key）"
        s_lines = [
            f"  [ERROR] {a['filename']}：独有 key {a['unique_count']} 个，样例 {a['unique_keys']}"
            for a in alerts
        ]
    else:
        s_summary = "无告警"
        s_lines = ["  没有独有 key——被隔离文件的数据别处有来源，隔离只是整理"]
    s_lines.append("（只报警不阻断：本批批处理照常完成入库）")
    sentinel = _section("sentinel", s_summary, s_lines)

    # —— 段 5：回收站清理 ——
    c = report.get("cleanup") or {}
    c_lines = [
        f"回收站：{c.get('dir', '')}",
        f"账本：{c.get('manifest', '')}",
        f"保留期 {c.get('retention_days', RETENTION_DAYS)} 天（按归档批次日期起算，"
        f"已存天数 ≥ 保留期即清理）",
        f"超期批次：{c.get('batches', []) or '（无）'}；待清理 {c.get('candidates', 0)} 份；"
        f"已入回收站 {c.get('recycled', 0)} 份；失败 {c.get('failed', 0)} 份",
    ]
    if c.get("dry_run"):
        c_lines.append("（dry-run 预演：⛔ 未把任何文件送进回收站、未写账本）")
    for e in c.get("entries", []):
        if e.get("status") != STATUS_RECYCLED:
            c_lines.append(f"  [{e['status']}] {e['source_rel']}"
                           + (f"（{e['error']}）" if e.get("error") else ""))
    if c.get("degraded"):
        c_lines.append(CLEANUP_DEGRADED_NOTE)
    c_lines.append(c.get("risk_note", RECYCLE_BIN_RISK_NOTE))
    cleanup = _section(
        "cleanup", f"待清理 {c.get('candidates', 0)} 份、已入回收站 {c.get('recycled', 0)} 份",
        c_lines)

    return {
        "batch": report.get("batch", ""),
        "mode": report.get("mode", ""),
        "dry_run": dry,
        "source": report.get("source", ""),
        "counts": counts,
        "seconds": report.get("seconds", 0),
        "sections": [archive, quarantine, gaps_sec, sentinel, cleanup],
    }


def render_report(document: dict) -> str:
    """把报告文档渲染成人类可读的 Markdown 文本（**纯函数**）。

    纯函数是「md 与 json 内容一致」这条验收项的前提：md 一定是
    `render_report(json.loads(机读 json))` 的产物，于是逐字节比对可断言，
    ⛔ 不靠肉眼。

    Args:
        document: `build_report_document()` 的产物。

    Returns:
        str: 完整的 Markdown 文本（按 `sections` 顺序逐段渲染）。

    Example:
        >>> doc = {"batch": "2026-10-12", "mode": "dry-run", "dry_run": True,
        ...        "source": "R", "counts": {KEEP: 1, ARCHIVE: 0, QUARANTINE: 0, SKIP: 0},
        ...        "seconds": 0.01,
        ...        "sections": [_section(k, "s", []) for k in REPORT_SECTION_KEYS]}
        >>> render_report(doc).startswith("# 源表整理报告 · 2026-10-12")
        True
        >>> render_report(doc).count("## [")
        5
    """
    lines = [
        f"# 源表整理报告 · {document.get('batch', '')}",
        "",
        f"- 模式：{document.get('mode', '')}（dry_run={document.get('dry_run')}）",
        f"- 数据源：{document.get('source', '')}",
        f"- 决策统计：{_render_counts(document.get('counts'))}",
        "",
    ]
    sections = document.get("sections", [])
    for i, sec in enumerate(sections, 1):
        lines.append(f"## [{i}/{len(sections)}] {sec['title']}")
        # 「关键词：摘要」——关键词由 `SECTION_KEYWORDS` 保证**段段互不相同**，
        # 于是「某一段没被渲染出来」这类破坏能被关键词断言抓住（⛔ 不是拼一整块字符串）。
        lines.append(f"「{sec['keyword']}」{sec['summary']}")
        lines.extend(sec.get("lines", []))
        lines.append("")
    lines.append(f"（整理耗时 {document.get('seconds', 0)}s）")
    return "\n".join(lines)


def write_organize_report(report: dict, out_dir: Path) -> tuple[Path, Path]:
    """把报告落成一对文件：人读 md + 机读 json，两者**内容一致**。

    md 由 `render_report(json.loads(json))` 可逐字节复现——这正是「内容一致」的
    可断言形式（见 `render_report`）。文件名用 `report-` 前缀，⛔ 不碰工单 02 的
    人工过目材料（`logs/organize/{日期}.md` / `decisions-{日期}.json`）。

    Args:
        report: `organize_files()` 的返回。
        out_dir: 报告目录；不存在时自动创建。

    Returns:
        tuple[Path, Path]: `(md 路径, json 路径)`。

    Example:
        >>> md, js = write_organize_report(rep, Path("logs/organize"))   # doctest: +SKIP
    """
    document = build_report_document(report)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"report-{document['batch']}.md"
    json_path = out_dir / f"report-{document['batch']}.json"
    md_path.write_text(render_report(document), encoding="utf-8")
    json_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    return md_path, json_path


def organize_files(root: Path | None = None, *, mode: str | None = None,
                   now: datetime | None = None, move=move_file,
                   recycle=to_recycle_bin, report_dir: Path | None = None) -> dict:
    """执行一次整理（判定 + 按模式搬文件 + 哨兵）。返回机读报告。

    行为随 `mode` 而变：
        · `off`     —— 什么都不做（连决策都不算）。
        · `dry-run` —— 只算决策与哨兵告警，**不搬文件、不写清单**（零副作用）。
        · `on`      —— 把 `archive` 决策的文件搬进 `{root}/_archive/{YYYY-MM-DD}/`、
            `quarantine` 决策的文件搬进 `{root}/_quarantine/`（都保持原文件名），
            各自写 write-ahead 清单。

    write-ahead 的顺序（崩溃可续做的关键）：
        ① 把**整批**候选写成 `planned` 落盘 → ② 逐个搬 → ③ 每搬成一个就落一次盘标 `done`；
        单个文件搬不动时跳过它、标 `failed` + 原因、**本批继续跑完**（⛔ 不抛到上层）。
        归档与隔离各自独立走一遍这套（两份清单互不干扰）。

    哨兵（本工单核心）：对被隔离文件算「独有 key」（同店同类**保留集**里找不到来源），
    命中即产出 ERROR 级告警；**没有独有 key 时不报警**。⚠️ 只报警不阻断：本函数**不**因
    告警改变任何决策，`run_once()` 照常完成入库。

    ⚠️ 幂等：紧接着再跑一次，候选（保留集不变、已归档 / 已隔离的不在原地）为空 →
    **零搬动**，且不会覆盖既有清单（候选为空时压根不写清单）。

    Args:
        root: 数据源根目录；可选，默认 `config.POP_SOURCE_DIR`。
        mode: `off` / `dry-run` / `on`；None 表示按 `JD_ORGANIZE` / 默认 dry-run 解析。
        now: 批次时间（决定归档子目录名与清单时间）；可选，默认取当前 UTC 时间。
        move: 搬移函数（默认 `move_file`）；可注入以便测试模拟失败。
        recycle: 回收站函数（默认 `to_recycle_bin`）；可注入以便测试降级路径。
        report_dir: 五段报告的落盘目录；None 表示用默认
            `{source_root 的同级}/logs/organize/`（生产里 = 项目根 `logs/organize/`）。

    Returns:
        dict: 报告。归档段含 `archive_dir` / `manifest` / `planned` / `moved` / `failed` / `moves`；
            隔离段在 `quarantined` 键下（同构：`dir` / `manifest` / `planned` / `moved` /
            `failed` / `moves`）；断档段 `gaps`（list，见 `find_gaps`）；哨兵段含
            `sentinel_alerts`（list，见 `compute_sentinel_alerts`）与 `sentinel_error`
            （bool，是否有 ERROR 级告警）；回收站清理段 `cleanup`（dict，见 `cleanup_expired`）。
            另有 `mode` / `batch` / `source` / `dry_run` / `counts` / `seconds`。
            `off` 模式下上述数据段为空、且**不产报告**。

    Side Effects:
        非 `off` 模式下会把五段报告写成人读 md + 机读 json 一对文件
        （`report-{batch}.md` / `.json`）到 `report_dir`；dry-run 也写报告（便于过目），
        但⛔ 不搬文件、不写清单、不动任何源文件。

    Raises:
        FileNotFoundError: 数据源表目录不存在（同 `derive_decisions`）。
    """
    root = Path(root) if root is not None else config.POP_SOURCE_DIR
    mode = resolve_mode(mode)
    now = now or datetime.now(timezone.utc)
    batch = now.date().isoformat()
    t0 = time.time()

    report: dict = {
        "mode": mode,
        "batch": batch,
        "source": str(root),
        "dry_run": mode != MODE_ON,
        "counts": None,
        # —— 归档段 ——
        "archive_dir": str(_archive_dir(root, batch)),
        "manifest": str(_manifest_path(root, batch)),
        "planned": 0,
        "moved": 0,
        "failed": 0,
        "moves": [],
        # —— 隔离段（与归档段同构；供工单 05 渲染「隔离」一段）——
        "quarantined": {
            "dir": str(_quarantine_dir(root)),
            "manifest": str(_quarantine_manifest_path(root)),
            "planned": 0,
            "moved": 0,
            "failed": 0,
            "moves": [],
        },
        # —— 哨兵段（供工单 05 渲染「哨兵告警」一段）——
        "sentinel_alerts": [],
        "sentinel_error": False,
        # —— 断档段（供工单 05 渲染「断档」一段；只报告、零动作）——
        "gaps": [],
        # —— 回收站清理段（供工单 05 渲染「回收站清理」一段）——
        "cleanup": {
            "dir": str(root / ARCHIVE_DIR_NAME),
            "manifest": str(root / ARCHIVE_DIR_NAME / CLEANUP_MANIFEST_NAME),
            "retention_days": RETENTION_DAYS,
            "batches": [],
            "candidates": 0,
            "recycled": 0,
            "failed": 0,
            "deferred": 0,
            "degraded": False,
            "dry_run": mode != MODE_ON,
            "entries": [],
            "risk_note": RECYCLE_BIN_RISK_NOTE,
        },
    }
    if mode == MODE_OFF:
        report["seconds"] = round(time.time() - t0, 3)
        return report

    # 一次读盘算 key，判定与哨兵复用同一份 files（避免重复读同一批文件）
    files = collect_file_keys(root)
    decisions = decide(files, load_contributing_shops(root))
    counts = {KEEP: 0, ARCHIVE: 0, QUARANTINE: 0, SKIP: 0}
    for d in decisions:
        counts[d["decision"]] += 1
    candidates = [d for d in decisions if d["decision"] == ARCHIVE]
    qcandidates = [d for d in decisions if d["decision"] == QUARANTINE]
    report["counts"] = counts

    # 哨兵：无论哪种模式都算——dry-run 下预演告警，便于人工过目。⛔ 只报警不阻断。
    report["sentinel_alerts"] = compute_sentinel_alerts(root, decisions, files)
    report["sentinel_error"] = bool(report["sentinel_alerts"])
    # 断档：只报告、零动作（保留集口径，见 find_gaps 的说明；dry-run 下也列）
    report["gaps"] = find_gaps(files, decisions)

    if mode == MODE_DRY_RUN:
        # dry-run：只算决策 / 哨兵 / 断档 / 清理候选，⛔ 不搬文件、不写清单、不动源文件
        report["planned"] = len(candidates)
        report["quarantined"]["planned"] = len(qcandidates)
        report["cleanup"] = cleanup_expired(root, now=now, recycle=recycle, dry_run=True)
        report["seconds"] = round(time.time() - t0, 3)
        _emit_report(report, report_dir)   # 报告是 dry-run 的产物之一（便于人工过目）
        return report

    # ---- mode == on：真搬（归档段 + 隔离段，两份清单各自独立）----
    # ① 归档段
    manifest = read_manifest(report["manifest"])
    if not manifest or manifest.get("corrupt"):
        # 没有既有清单（或它坏了）：新建一份，以本次批次条目为准。
        # ⚠️ 损坏的清单无法解析出条目，只能被这份新清单覆盖——所以「清单损坏」本身
        # 就是该批次不可回滚的信号，read_manifest 会把它标成 corrupt 供上层察觉。
        manifest = {"batch": batch, "batch_time": now.isoformat(),
                    "source_root": str(root), "entries": []}
    a_index = {e.get("source_rel"): e for e in manifest["entries"]}
    planned = _plan_moves(root, candidates, manifest["entries"], a_index, now, batch,
                          f"{ARCHIVE_DIR_NAME}/{batch}", "archive_rel")
    report["planned"] = len(planned)
    report["moved"], report["failed"] = _run_moves(
        Path(report["manifest"]), manifest, planned, move)
    report["moves"] = [
        {"source_rel": e["source_rel"], "archive_rel": e["archive_rel"],
         "status": e["status"], "error": e.get("error", "")}
        for e, _, _ in planned
    ]

    # ② 隔离段（不再参与 60 天清理；见模块头 / iter_archive_batches 的说明）
    q_manifest_path = Path(report["quarantined"]["manifest"])
    q_manifest = read_manifest(q_manifest_path)
    if not q_manifest or q_manifest.get("corrupt"):
        q_manifest = {"batch": batch, "batch_time": now.isoformat(),
                      "source_root": str(root), "entries": []}
    q_index = {e.get("source_rel"): e for e in q_manifest["entries"]}
    q_planned = _plan_moves(root, qcandidates, q_manifest["entries"], q_index, now, batch,
                            QUARANTINE_DIR_NAME, "quarantine_rel")
    report["quarantined"]["planned"] = len(q_planned)
    report["quarantined"]["moved"], report["quarantined"]["failed"] = _run_moves(
        q_manifest_path, q_manifest, q_planned, move)
    report["quarantined"]["moves"] = [
        {"source_rel": e["source_rel"], "quarantine_rel": e["quarantine_rel"],
         "status": e["status"], "error": e.get("error", "")}
        for e, _, _ in q_planned
    ]

    # ③ 回收站清理段：把超期归档批次里的文件逐个送进系统回收站（write-ahead + 降级）。
    #    ⛔ 只遍历 `_archive/*`（cleanup_expired 内部复用 iter_archive_batches）——
    #    隔离区结构上扫不到，永不入清理。本批次今天刚归档，不可能超期，故与归档互不干扰。
    report["cleanup"] = cleanup_expired(root, now=now, recycle=recycle, dry_run=False)

    report["seconds"] = round(time.time() - t0, 3)
    _emit_report(report, report_dir)
    return report


def _emit_report(report: dict, report_dir: Path | None) -> None:
    """把五段报告落盘并回填路径（内部用）。

    `report_dir=None` 时落到默认目录 `{source_root 的同级}/logs/organize/`。
    ⛔ 不吞异常：报告的落盘失败由上层 `run_batch._run_organize` 兜底（ADR-0002）。
    """
    out = Path(report_dir) if report_dir is not None else _default_report_dir(report["source"])
    md_path, json_path = write_organize_report(report, out)
    report["report_md"] = str(md_path)
    report["report_json"] = str(json_path)


def _manifest_sources(root: Path, target: str) -> tuple[list[dict], bool]:
    """列出回滚要遍历的**清单来源**（归档区 + 隔离区），用同一形状描述。

    为什么要有它：回滚既要覆盖归档区又要覆盖隔离区，而两份清单由**同一套**
    `_plan_moves` / `_run_moves` 产出、形状**完全同构**——唯一区别是
    ① 清单在哪（归档按批次 `_archive/{批次}/manifest.json`；隔离平铺 `_quarantine/manifest.json`）
    与 ② 目的地字段名（`archive_rel` / `quarantine_rel`）。把这两点抽成「来源描述」，
    回滚主循环便只写一遍——⛔ 不复制两份几乎相同的循环体（复制出来的两份会各自漂移）。

    归档来源按 `target` 决定「按批次」还是「跨全部批次」；隔离区**没有批次概念**，
    故其来源永远是那一份平铺清单（`target` 非空时由主循环按文件名匹配，命中不了自然跳过）。

    Args:
        root: 数据源根目录。
        target: 回滚目标（已 strip）；等于 `_archive/` 下某批次目录名时按批次取归档清单。

    Returns:
        tuple[list[dict], bool]: `(sources, by_batch)`。
            `sources` 每项为 `{"kind": "archive"|"quarantine", "paths": [清单路径…],
            "rel_field": "archive_rel"|"quarantine_rel"}`；`paths` 为空表示该来源无可读清单。
            `by_batch` 为 True 表示 `target` 命中了一个归档批次目录（此时归档来源只含该批次清单）。

    Example:
        >>> srcs, by_batch = _manifest_sources(Path("__no_such_root__"), "")
        >>> [s["kind"] for s in srcs]
        ['archive', 'quarantine']
        >>> by_batch
        False
        >>> [len(s["paths"]) for s in srcs]   # 根不存在 → 两个来源都没有清单
        [0, 0]
    """
    arch_root = root / ARCHIVE_DIR_NAME
    batch_dir = arch_root / target
    by_batch = bool(target) and batch_dir.is_dir()

    if by_batch:
        arch_paths = [batch_dir / MANIFEST_NAME]
    elif arch_root.is_dir():
        arch_paths = sorted(arch_root.glob(f"*/{MANIFEST_NAME}"))
    else:
        arch_paths = []

    q_path = _quarantine_manifest_path(root)
    sources = [
        {"kind": "archive", "paths": arch_paths, "rel_field": "archive_rel"},
        {"kind": "quarantine", "paths": [q_path] if q_path.is_file() else [],
         "rel_field": "quarantine_rel"},
    ]
    return sources, by_batch


def rollback(root: Path | None = None, batch_or_file: str = "", *,
             move=move_file) -> dict:
    """按批次或按单文件，把**归档区与隔离区**里的文件搬回原位，并把清单状态改成 `rolled_back`。

    回滚**同时**遍历两份清单：归档区 `_archive/{批次}/manifest.json`（按批次分目录）与
    隔离区 `_quarantine/manifest.json`（平铺）——两者形状同构，都按各自的 `source_rel` 搬回原位。
    目的地字段名不同：归档条目用 `archive_rel`，隔离条目用 `quarantine_rel`（见 `_manifest_sources`）。

    目标位置**已有同名文件时不覆盖**，只报冲突（⛔ 绝不悄悄盖掉别人的数据）。
    两份清单**互不干扰**：回滚隔离条目只改隔离清单，归档清单字节不变（反之亦然）。

    匹配规则：
        · `batch_or_file` 等于 `_archive/` 下的某个批次目录名 → 只回滚该批次（隔离区无批次，不参与）；
        · 否则当作文件名 / 相对路径，跨**归档全部批次 + 隔离清单**匹配 `source_rel`（或其文件名）。
        · 传空串 → 回滚归档全部批次与隔离清单里的全部未回滚条目。

    Args:
        root: 数据源根目录；可选，默认 `config.POP_SOURCE_DIR`。
        batch_or_file: 批次目录名（如 `2026-10-12`）或文件名 / 相对路径；空串表示全部。
        move: 搬移函数（默认 `move_file`）。

    Returns:
        dict: `{root, target, restored, conflicts, missing, not_found, entries, by_source}`。
            `restored` / `conflicts` / `missing` / `not_found` / `entries` 语义不变；
            `entries` 的每条新增 `source` 字段（`"archive"` / `"quarantine"`）以区分来源；
            `by_source` 为新增字段，按来源给出 `{restored, conflicts, missing}` 分项计数
            （`{"archive": {...}, "quarantine": {...}}`）。

    Example:
        >>> rollback(Path("ResourceData"), "2026-10-12")   # doctest: +SKIP
    """
    root = Path(root) if root is not None else config.POP_SOURCE_DIR
    target = (batch_or_file or "").strip()
    sources, by_batch = _manifest_sources(root, target)

    report = {"root": str(root), "target": target, "restored": 0,
              "conflicts": 0, "missing": 0, "not_found": True, "entries": [],
              "by_source": {"archive": {"restored": 0, "conflicts": 0, "missing": 0},
                            "quarantine": {"restored": 0, "conflicts": 0, "missing": 0}}}
    for source in sources:
        kind = source["kind"]
        rel_field = source["rel_field"]
        # 按批次回滚只针对归档（隔离区无批次）；其余情况 target 非空即按文件名匹配。
        apply_file_filter = bool(target) and not (kind == "archive" and by_batch)
        for mp in source["paths"]:
            manifest = read_manifest(mp)
            if not manifest or manifest.get("corrupt"):
                continue
            changed = False
            for entry in manifest.get("entries", []):
                if entry.get("status") == STATUS_ROLLED_BACK:
                    continue
                src_rel = entry.get("source_rel", "")
                if apply_file_filter:
                    # 按文件匹配：相对路径相符、或文件名（末段）相符、或目的地相对路径相符
                    if (src_rel != target and Path(src_rel).name != target
                            and entry.get(rel_field) != target):
                        continue
                report["not_found"] = False
                dest = root / entry.get(rel_field, "")
                orig = root / src_rel
                if not dest.is_file():
                    # 归档 / 隔离副本不在：本就没搬成（failed / 尚未搬的 planned），或已被清走 —— 无可回滚
                    report["missing"] += 1
                    report["by_source"][kind]["missing"] += 1
                    report["entries"].append({"source_rel": src_rel, "result": "missing",
                                              "source": kind})
                    continue
                if orig.exists():
                    # 原位与副本**同时存在**：⛔ 不覆盖原位文件，报冲突（数据两侧都在，不丢）
                    report["conflicts"] += 1
                    report["by_source"][kind]["conflicts"] += 1
                    report["entries"].append({"source_rel": src_rel, "result": "conflict",
                                              "source": kind})
                    continue
                move(dest, orig)
                entry["status"] = STATUS_ROLLED_BACK
                report["restored"] += 1
                report["by_source"][kind]["restored"] += 1
                report["entries"].append({"source_rel": src_rel, "result": "restored",
                                          "source": kind})
                changed = True
            if changed:
                _write_manifest(mp, manifest)
    return report


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。

    `--mode` 默认 `dry-run`（安全默认）：真实目录上「什么都不搬」必须是默认行为，
    只有显式 `--mode on` 才搬文件（ADR-0002 / Q13）。`--rollback` 与搬移互斥地走回滚分支。
    """
    p = argparse.ArgumentParser(
        prog="python -m backend.jobs.organize",
        description="源表整理器：按 key 覆盖产出决策清单并（在 on 模式下）搬移冗余文件。",
    )
    p.add_argument("--source", default=None,
                   help="数据源根目录（其下应有「数据源表目录」）；默认 config.POP_SOURCE_DIR")
    p.add_argument("--out", default=None,
                   help="机读结果的输出路径（dry-run 写决策清单；on 写整理报告）；不给则只打印")
    p.add_argument("--mode", choices=list(VALID_MODES), default=None,
                   help="整理模式：off | dry-run（默认）| on。on 才会真搬文件")
    p.add_argument("--rollback", default=None, metavar="批次|文件",
                   help="回滚：给批次目录名（如 2026-10-12）或文件名 / 相对路径；"
                        "同时覆盖归档区与隔离区")
    return p


def main(argv: list[str] | None = None) -> int:
    """命令行入口：按 `--mode` 执行整理 / dry-run，或按 `--rollback` 回滚；返回退出码。

    Args:
        argv: 参数列表；None 表示取 `sys.argv[1:]`。

    Returns:
        int: 0 成功；2 数据源表目录不存在（参数或数据源摆错了，不是判定失败）。
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文会乱码
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    root = Path(args.source) if args.source else config.POP_SOURCE_DIR

    if args.rollback is not None:
        try:
            report = rollback(root, args.rollback)
        except FileNotFoundError as e:
            print(f"[错误] {e}", file=sys.stderr)
            return 2
        print(f"[rollback] 目标={report['target'] or '<全部>'}：搬回 {report['restored']} 份，"
              f"冲突 {report['conflicts']}，缺失 {report['missing']}，"
              f"未匹配到条目={report['not_found']}")
        bs = report["by_source"]
        print(f"  来源拆分：归档区 搬回 {bs['archive']['restored']} 份 / "
              f"隔离区 搬回 {bs['quarantine']['restored']} 份")
        return 0

    mode = resolve_mode(args.mode)
    out = Path(args.out) if args.out else None
    try:
        if mode == MODE_OFF:
            print("[organize] 模式 off：本次不整理。")
            return 0
        if mode == MODE_DRY_RUN:
            dry_run(root, out)
            return 0
        report = organize_files(root, mode=MODE_ON)
    except FileNotFoundError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 2

    c = report["counts"]
    print(f"=== 源表整理器 mode=on（批次 {report['batch']}）===")
    print(f"决策统计：keep {c[KEEP]} / archive {c[ARCHIVE]} / "
          f"quarantine {c[QUARANTINE]} / skip {c[SKIP]}")
    print(f"归档区：{report['archive_dir']}")
    print(f"清单：{report['manifest']}")
    print(f"搬动：{report['moved']} 份成功，{report['failed']} 份失败"
          f"（共计划 {report['planned']} 份）；耗时 {report['seconds']}s")
    for m in report["moves"]:
        if m["status"] != STATUS_DONE:
            print(f"  [{m['status']}] {m['source_rel']} —— {m['error']}")

    q = report["quarantined"]
    print(f"隔离区：{q['dir']}")
    print(f"隔离：{q['moved']} 份成功，{q['failed']} 份失败（共计划 {q['planned']} 份）"
          f"；清单 {q['manifest']}")
    for m in q["moves"]:
        if m["status"] != STATUS_DONE:
            print(f"  [{m['status']}] {m['source_rel']} —— {m['error']}")

    alerts = report["sentinel_alerts"]
    if alerts:
        print(f"哨兵告警（ERROR × {len(alerts)}）：被隔离文件持有保留集里找不到来源的独有 key——"
              f"这些 (店铺, 日期, SPU) 会变成历史缺口或静默陈旧，需人工判断（只报警不阻断）：")
        for a in alerts:
            print(f"  [ERROR] {a['filename']}：独有 key {a['unique_count']} 个，"
                  f"样例 {a['unique_keys']}")
    else:
        print("哨兵：无告警（没有独有 key——被隔离文件的数据别处有来源，隔离只是整理）。")
    if report["failed"]:
        print("⚠️ 有文件未能搬动（被占用 / 权限不足）：已跳过并记录，本批照常跑完。")
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        print(f"\n整理报告已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
